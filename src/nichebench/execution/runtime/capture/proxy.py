"""Transparent HTTP/SSE capture proxy for one pinned MiniMax route.

Why a proxy
-----------
To answer "what did we actually send and receive" the harness must see the
bytes. The cage reaches the provider through this process instead of dialing
the provider directly, so the same provider key, npm provider package, model
id, prompt, tools, permissions, and sampling parameters are preserved — only
the transport base URL changes. No SDK switch, no provider remapping.

Safety properties
-----------------
- **Fixed upstream.** ``https://api.minimax.io/v1``. The incoming path tail is
  appended to ``scheme://netloc`` — the upstream's own path is *not* reused,
  because the cage's base URL already carries the ``/v1`` prefix and appending
  both would produce ``/v1/v1/…``. The query string is forwarded byte-for-byte,
  already percent-encoded by the client. The host is never client-controlled.
- **Capability path and auth swap.** A per-run random path prefix plus a per-run
  random bearer token guard the listener. The token is what the cage presents;
  the proxy replaces it in memory with the real provider key before forwarding.
   The token is never archived; capability URLs may appear in config metadata.
- **No new default total timeout.** Connect timeout is bounded; the response
  stream is unbounded so the cage's own timeouts govern.
- **TLS verified.** No redirect following, no decompression, no cookies, no
  retries. Body and method bytes pass through untouched; SSE frames are
  forwarded raw without translation.

Capture fidelity
----------------
Events are recorded at the ``transport_http_entity`` boundary: exact request
and response body bytes, exact forwarded offsets, status, and a terminal state
of ``complete`` / ``partial`` / ``error`` / ``disconnect``. Each body is stored
as its own blob set, so a repeated request prefix is not rewritten per turn.

Stored bytes are scrubbed of the literal credentials the harness holds in
memory (see :mod:`nichebench.execution.runtime.capture.scrub`). Forwarded bytes
are never rewritten: the provider receives exactly what the cage sent. When a
stored body is scrubbed, its journal record carries ``exact: false`` and an
``exclusions`` entry. Compressed bodies are forwarded unchanged but withheld from
storage, with an explicit incomplete reason: searching compressed bytes would
not prove the absence of a credential in their decoded content.

Ownership: proxy process only. Nothing here interprets SSE payloads; decoded
application views are the agent runtime's job, not the transport's.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import secrets
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from nichebench.execution.runtime.capture.errors import CaptureError
from nichebench.execution.runtime.capture.scrub import StreamingCredentialScrubber, scrub_mapping_known_credentials
from nichebench.execution.runtime.capture.store import BOUNDARY_TRANSPORT, EventJournal

DEFAULT_UPSTREAM = "https://api.minimax.io/v1"
HOST_GATEWAY = "host.docker.internal"
BIND_HOST = "0.0.0.0"
CAPABILITY_PREFIX = "nbcap"

# The cage base URL must mirror the real provider shape (``<base>/v1``), so the
# capability URL ends with this suffix. The proxy strips the capability prefix
# and forwards ``/v1/...`` — the upstream origin path is never reused.
UPSTREAM_PATH_SUFFIX = "/v1"
HEALTH_SUFFIX = "healthz"
CONNECT_TIMEOUT_SECONDS = 30.0
STARTUP_TIMEOUT_SECONDS = 25.0
SHUTDOWN_TIMEOUT_SECONDS = 2.0
HEALTH_PROBE_TIMEOUT_SECONDS = 10
HEALTH_PROBE_URL_PLACEHOLDER = "__NB_HEALTH_URL__"

# Hop-by-hop headers are connection-scoped and must not be forwarded.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Header *names* are recorded so the header set is verifiable, but values are
# dropped for every credential header. Other header values are preserved.
CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"})


class ProxyUnavailable(RuntimeError):
    """Raised when the capture proxy cannot be started or reached."""


@dataclass
class ProxyConfig:
    """Resolved proxy bind/advertise/upstream configuration.

    ``allow_insecure_upstream`` exists only so the offline test suite can point
    the proxy at a loopback stub instead of the real provider. Production call
    sites never set it, and the production default is the pinned https origin —
    which is what prevents this from becoming a general-purpose open proxy.
    """

    upstream: str = DEFAULT_UPSTREAM
    bind_host: str = BIND_HOST
    advertise_host: str = HOST_GATEWAY
    port: int = 0
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS
    allow_insecure_upstream: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "upstream": self.upstream,
            "bind_host": self.bind_host,
            "advertise_host": self.advertise_host,
            "port": self.port,
            "connect_timeout_seconds": self.connect_timeout,
        }


@dataclass
class ProxyHandle:
    """Live proxy reference returned by :func:`start_capture_proxy`.

    ``base_url`` is what the cage uses. ``verify()`` performs an offline
    connectivity check that makes no paid API call.
    """

    base_url: str
    port: int
    capability_path: str
    config: ProxyConfig
    token: str = field(default="", repr=False)
    _loop: Any = field(default=None, repr=False)
    _thread: Any = field(default=None, repr=False)
    _runner: Any = field(default=None, repr=False)
    _stop_event: Any = field(default=None, repr=False)

    @property
    def health_url(self) -> str:
        """Absolute URL of the offline health endpoint."""
        return f"http://127.0.0.1:{self.port}{self.capability_path}/{HEALTH_SUFFIX}"

    def stop(self, timeout: float = 5.0) -> None:
        """Shut the proxy down and wait for it to finish.

        The stop event is set first so the serve coroutine runs its own cleanup
        (upstream session close, runner cleanup) and the loop can exit by
        itself. ``loop.stop()`` is only a fallback for a wedged coroutine, so a
        normal shutdown never needs to wait on the timeout.
        """
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None or not thread.is_alive():
            return
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(_set_stop, self)
        thread.join(timeout=timeout)
        if thread.is_alive():
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=timeout)
        else:
            # The serve coroutine already returned (it stops the loop itself);
            # calling run_until_complete on a stopped loop would raise.
            return


def _set_stop(handle: ProxyHandle) -> None:
    """Signal the serve coroutine to stop (runs on the proxy's loop)."""
    if handle._stop_event is not None:
        handle._stop_event.set()


def build_capability_path() -> str:
    """Return a fresh random capability path prefix."""
    return f"/{CAPABILITY_PREFIX}-{secrets.token_hex(8)}"


def build_gateway_docker_args(host_gateway: str = HOST_GATEWAY) -> List[str]:
    """Return the docker args that make the host gateway resolvable in-cage."""
    return ["--add-host", f"{host_gateway}:host-gateway"]


def resolve_free_port(host: str = BIND_HOST) -> int:
    """Ask the OS for an ephemeral port on ``host``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_until_listening(port: int, host: str = "127.0.0.1", timeout: float = 10.0) -> bool:
    """Block until ``host:port`` accepts a TCP connection."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def is_private_advertise_host(host: str) -> bool:
    """True when ``host`` is loopback/private or the Docker host gateway."""
    if host in (HOST_GATEWAY, "localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def build_health_probe_command(
    image: str,
    host_gateway: str = HOST_GATEWAY,
    timeout_seconds: int = HEALTH_PROBE_TIMEOUT_SECONDS,
) -> List[str]:
    """Build the in-container command that probes the proxy health endpoint.

    The pinned image ships ``curl`` but not ``wget``, and its ``HOME`` is ``/``
    which the non-root runtime user cannot write. The probe therefore pins
    ``HOME=/tmp``, resolves the host gateway with ``--add-host``, and checks the
    HTTP status as well as the body.

    Args:
        image: Cage image that will run the agent; probed with the same image.
        host_gateway: Hostname mapped to the host gateway inside the container.
        timeout_seconds: curl connect/total timeout.

    Returns:
        An argv list suitable for :func:`probe_from_container`. The URL is a
        placeholder that the caller replaces with the live health URL.
    """
    script = (
        "HOME=/tmp; export HOME; "
        "exec curl --silent --show-error --fail "
        f"--max-time {int(timeout_seconds)} --write-out '\\n%{{http_code}}' "
        f"{HEALTH_PROBE_URL_PLACEHOLDER}"
    )
    return [
        "docker",
        "run",
        "--rm",
        "--add-host",
        f"{host_gateway}:host-gateway",
        "--entrypoint",
        "sh",
        image,
        "-c",
        script,
    ]


def parse_health_probe_output(stdout: str) -> Tuple[bool, str]:
    """Interpret the probe stdout: body first, HTTP status last.

    Returns:
        Tuple of (healthy, detail). Health requires both a 200 status and the
        expected body, so a proxy that answers but is not ours is rejected.
    """
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        return False, "empty_probe_output"
    status = lines[-1].strip()
    body = "\n".join(lines[:-1]).strip()
    return status == "200" and body == "ok", f"status={status} body_ok={body == 'ok'}"


def probe_from_container(
    container_command: List[str],
    health_url: str,
    timeout_seconds: float = 20.0,
) -> Tuple[bool, str]:
    """Run the health endpoint through a container command.

    The production call passes the argv from :func:`build_health_probe_command`,
    so the probe exercises the same network path the cage will use — host-gateway
    resolution and container networking included — without contacting the
    provider.

    Args:
        container_command: Argv whose final element is the URL placeholder.
        health_url: Absolute URL of the proxy health endpoint.
        timeout_seconds: Hard cap for the probe.

    Returns:
        Tuple of (healthy, detail). Detail never contains the capability token.
    """
    argv = [arg.replace(HEALTH_PROBE_URL_PLACEHOLDER, health_url) for arg in container_command]
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"probe_failed: {type(exc).__name__}"
    healthy, detail = parse_health_probe_output(completed.stdout or "")
    if not healthy or completed.returncode != 0:
        return False, f"{detail} rc={completed.returncode}"
    return True, detail


def _require_aiohttp() -> Tuple[Any, Any]:
    try:
        import aiohttp
        from aiohttp import web
    except Exception as exc:  # pragma: no cover - pinned by the lockfile
        raise ProxyUnavailable(f"aiohttp is required for runtime capture: {exc}") from exc
    return aiohttp, web


def _headers_view(headers: Any) -> Dict[str, str]:
    """Build a recordable header view: names kept, credential values dropped."""
    view: Dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP:
            continue
        view[key] = "[REDACTED]" if lowered in CREDENTIAL_HEADERS else value
    return view


def create_app(
    journal: EventJournal,
    config: ProxyConfig,
    capability_path: str,
    token: str,
    upstream_auth: Optional[str] = None,
    known_credentials: Optional[List[str]] = None,
) -> Any:
    """Build the aiohttp application for one capture proxy.

    Args:
        journal: Attempt journal that receives transport events.
        config: Bind/advertise/upstream configuration.
        capability_path: Random path prefix the cage is allowed to use.
        token: Random capability token the cage must present.
        upstream_auth: Real provider credential placed on the forwarded request
            in place of the capability token. Held in memory only. When
            ``None`` the token is forwarded unchanged, which is what the offline
            test stubs expect.
        known_credentials: Literal credentials to scrub from stored bodies.

    Returns:
        An ``aiohttp.web.Application``.

    Raises:
        ProxyUnavailable: If aiohttp is unavailable or the upstream is not a
            fixed https origin.
    """
    aiohttp, web = _require_aiohttp()
    from yarl import URL

    upstream = urlsplit(config.upstream)
    if not config.allow_insecure_upstream:
        if upstream.scheme != "https" or not upstream.hostname:
            raise ProxyUnavailable(f"capture proxy upstream must be https with a host, got {config.upstream!r}")
        if f"{upstream.scheme}://{upstream.netloc}{upstream.path}".rstrip("/") != DEFAULT_UPSTREAM:
            raise ProxyUnavailable(
                f"capture proxy upstream must be the pinned origin {DEFAULT_UPSTREAM}, got {config.upstream!r}"
            )
    # Only the origin is reused. The upstream's path is deliberately dropped:
    # the cage base URL already ends in the provider prefix (e.g. ``/v1``), so
    # reusing it would duplicate the segment.
    origin = f"{upstream.scheme}://{upstream.netloc}"
    scrub_known = [token, *(known_credentials or []), *journal.known_credentials]
    if upstream_auth:
        scrub_known.append(upstream_auth)
        scrub_known.append(upstream_auth.removeprefix("Bearer "))
    journal.known_credentials.extend(c for c in scrub_known if c not in journal.known_credentials)

    @web.middleware
    async def storage_guard(request: Any, handler: Any) -> Any:
        if journal.stopped:
            return web.Response(status=507, text="runtime capture storage exhausted")
        try:
            return await handler(request)
        except CaptureError as exc:
            journal.stop_capture(f"capture_storage_failed:{type(exc).__name__}")
            return web.Response(status=507, text="runtime capture storage exhausted")

    def _upstream_auth_header() -> Optional[str]:
        """Return the Authorization value to forward, if one is configured."""
        if upstream_auth:
            return upstream_auth if " " in upstream_auth else f"Bearer {upstream_auth}"
        return None

    def _token_ok(request: Any) -> bool:
        header = request.headers.get("Authorization", "")
        presented = header[7:] if header[:7].lower() == "bearer " else ""
        return bool(presented) and secrets.compare_digest(presented, token)

    async def health(request: Any) -> Any:
        del request
        journal.append(
            {"event": "health_probe", "paid_call": False},
            boundary=BOUNDARY_TRANSPORT,
            source="transport",
        )
        return web.Response(text="ok", status=200)

    async def forward(request: Any) -> Any:
        # raw_path includes the encoded query. Neither aiohttp's decoded path
        # nor its query_string may be used to reconstruct signed/encoded URLs.
        raw_tail = request.raw_path[len(capability_path) :]
        if not _token_ok(request):
            journal.append(
                {"event": "proxy_rejected_auth", "path": request.path},
                boundary=BOUNDARY_TRANSPORT,
                source="transport",
            )
            return web.Response(status=401, text="unauthorized")

        method = request.method
        target_path, _, query = raw_tail.partition("?")
        target_path = target_path or "/"
        body = await request.read()
        url = f"{origin}{target_path}" + (f"?{query}" if query else "")

        # Replace the capability token with the real provider credential. Only
        # the forwarded copy changes; the archive keeps the header redacted.
        forwarded_auth = _upstream_auth_header()
        headers: Dict[str, str] = {}
        for key, value in request.headers.items():
            lowered = key.lower()
            if lowered in HOP_BY_HOP or lowered == "host":
                continue
            if forwarded_auth and lowered == "authorization":
                headers[key] = forwarded_auth
                continue
            headers[key] = value
        headers["Host"] = upstream.netloc

        recorded_headers, header_hits = scrub_mapping_known_credentials(
            {"incoming": _headers_view(request.headers), "forwarded_host": upstream.netloc},
            [token, *scrub_known],
        )
        start = journal.append(
            {
                "event": "http_request",
                "method": method,
                "path": request.path,
                "upstream_url": url,
                "upstream_url_path": target_path,
                "query": query,
                "headers": recorded_headers,
                "auth_header_exact": not header_hits
                and not any(key.lower() in CREDENTIAL_HEADERS for key in request.headers),
                "forwarded_auth_replaced": bool(forwarded_auth),
                "redirects_followed": False,
                "retried": False,
            },
            boundary=BOUNDARY_TRANSPORT,
            source="transport",
        )
        request_id = str(start["seq"])
        request_encoding = request.headers.get("Content-Encoding", "identity").lower()
        if request_encoding not in ("", "identity"):
            journal.mark_incomplete("compressed_request_body_withheld")
            journal.append(
                {
                    "event": "request_body_withheld",
                    "request_id": request_id,
                    "exact": False,
                    "reason": "content_encoding_not_inspectable",
                    "byte_length": len(body),
                    "content_encoding": request_encoding,
                },
                boundary=BOUNDARY_TRANSPORT,
                source="transport",
            )
        else:
            journal.store_payload(
                "request_body",
                body,
                extra={
                    "method": method,
                    "upstream_path": target_path,
                    "query": query,
                    "boundary": BOUNDARY_TRANSPORT,
                    "source": "transport",
                    "request_id": request_id,
                },
                known_credentials=scrub_known,
            )
        journal.append(
            {
                "event": "http_request_sent",
                "byte_length": len(body),
            },
            boundary=BOUNDARY_TRANSPORT,
            source="transport",
            request_id=request_id,
        )

        session = request.app["upstream_session"]
        try:
            upstream_response = await session.request(
                method,
                URL(url, encoded=True),
                headers=headers,
                data=body,
                allow_redirects=False,
                auto_decompress=False,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            journal.append(
                {
                    "event": "http_request_error",
                    "state": "error",
                    "error_type": type(exc).__name__,
                    "response_invented": False,
                },
                boundary=BOUNDARY_TRANSPORT,
                source="transport",
                request_id=request_id,
            )
            return web.Response(status=502, text="capture proxy upstream failure")

        try:
            journal.append(
                {
                    "event": "http_response_headers",
                    "status": int(upstream_response.status),
                    "headers": _headers_view(upstream_response.headers),
                },
                boundary=BOUNDARY_TRANSPORT,
                source="transport",
                request_id=request_id,
            )
        except CaptureError:
            upstream_response.release()
            raise

        response = web.StreamResponse(status=int(upstream_response.status))
        for key, value in upstream_response.headers.items():
            lowered = key.lower()
            if lowered in HOP_BY_HOP or lowered in ("content-length", "set-cookie"):
                continue
            response.headers[key] = value

        # The stored copy is scrubbed of known credentials. The forwarded copy is
        # byte-identical to what the upstream sent.
        response_scrubber = StreamingCredentialScrubber(scrub_known)
        content_encoding = upstream_response.headers.get("Content-Encoding", "identity")
        compressed = content_encoding.strip().lower() not in ("", "identity")
        if compressed:
            # Never search compressed chunks as if they were plaintext. Withhold
            # rather than buffering/decompressing an unbounded response.
            journal.mark_incomplete("compressed_response_body_withheld")
        offset = 0
        forwarded = 0
        stored_offset = 0
        state = "complete"
        phase = "downstream_prepare"
        upstream_eof = False
        cancelled = False

        def store_chunk(storable: bytes) -> None:
            nonlocal stored_offset
            if not storable:
                return
            journal.store_payload(
                "response_body",
                storable,
                extra={
                    "offset": stored_offset,
                    "received_through_offset": offset,
                    "request_id": request_id,
                    "status": int(upstream_response.status),
                    "boundary": BOUNDARY_TRANSPORT,
                    "source": "transport",
                    "exact": not response_scrubber.redacted,
                    "exclusions": ["known_credential_replaced"] if response_scrubber.redacted else [],
                },
            )
            stored_offset += len(storable)

        def record_interruption(error_type: str) -> None:
            record = {
                "event": "http_response_stream_interrupted",
                "state": state,
                "error_type": error_type,
                "phase": phase,
                "bytes_received": offset,
                "bytes_forwarded": forwarded,
                "upstream_eof_observed": upstream_eof,
                "request_id": request_id,
                "scope": "downstream" if phase.startswith("downstream") else "upstream",
            }
            if cancelled:
                journal.append_reserved(record, boundary=BOUNDARY_TRANSPORT, source="transport")
            else:
                journal.append(record, boundary=BOUNDARY_TRANSPORT, source="transport", request_id=request_id)

        try:
            await response.prepare(request)
            while True:
                phase = "upstream_read"
                chunk = await upstream_response.content.readany()
                if not chunk:
                    upstream_eof = True
                    break
                offset += len(chunk)
                phase = "capture_write"
                if not compressed:
                    store_chunk(response_scrubber.feed(chunk))
                phase = "downstream_write"
                await response.write(chunk)
                forwarded += len(chunk)
        except CaptureError as exc:
            journal.stop_capture(f"capture_write_failed_midstream:{type(exc).__name__}")
            state = "partial"
        except asyncio.CancelledError:
            cancelled = True
            state = "partial"
            record_interruption("CancelledError")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            # A client- or peer-side reset means the stream was cut mid-flight;
            # other failures (e.g. a read timeout) are partial transfers.
            state = "disconnect" if isinstance(exc, ConnectionResetError) else "partial"
            record_interruption(type(exc).__name__)
        finally:
            # A downstream write can fail after aiohttp has already consumed the
            # entire upstream entity. Report that observation separately from
            # the interrupted downstream transfer, never as a complete exchange.
            upstream_eof = upstream_eof or upstream_response.content.at_eof()
            # All observed bytes, including a credential-prefix carry, belong to
            # the stored prefix even when the peer disconnects or shutdown cancels
            # this handler. Never flush twice or discard it on the error path.
            try:
                if not journal.stopped:
                    if not compressed:
                        store_chunk(response_scrubber.flush())
                    else:
                        journal.append(
                            {
                                "event": "response_body_withheld",
                                "exact": False,
                                "reason": "content_encoding_not_inspectable",
                                "content_encoding": content_encoding,
                                "byte_length": offset,
                            },
                            boundary=BOUNDARY_TRANSPORT,
                            source="transport",
                            request_id=request_id,
                        )
            except CaptureError as exc:
                journal.stop_capture(f"capture_tail_write_failed:{type(exc).__name__}")
                state = "partial"
            if state != "complete":
                journal.mark_incomplete("transport_stream_incomplete")
            with contextlib.suppress(Exception):
                upstream_response.release()

        terminal = {
            "event": "http_response_complete",
            "state": state,
            "status": int(upstream_response.status),
            "bytes_forwarded": forwarded,
            "bytes_received": offset,
            "stored_byte_length": stored_offset,
            "upstream_eof_observed": upstream_eof,
            "interruption_phase": phase if state != "complete" else None,
            "exact": state == "complete" and not compressed and not response_scrubber.redacted,
            "exclusions": (
                ["compressed_body_withheld"]
                if compressed
                else ["known_credential_replaced"]
                if response_scrubber.redacted
                else []
            )
            + (["stream_incomplete"] if state != "complete" else []),
            "sse_translated": False,
            "decompressed": False,
            "retried": False,
            "request_id": request_id,
        }
        if journal.stopped or cancelled:
            journal.append_reserved(terminal, boundary=BOUNDARY_TRANSPORT, source="transport")
        else:
            journal.append(
                terminal,
                boundary=BOUNDARY_TRANSPORT,
                source="transport",
                request_id=request_id,
            )
        if cancelled:
            raise asyncio.CancelledError
        with contextlib.suppress(Exception):
            await response.write_eof()
        return response

    async def reject_unknown(request: Any) -> Any:
        """Deny everything outside the run-scoped capability path."""
        journal.append(
            {"event": "proxy_rejected_path", "path": request.path},
            boundary=BOUNDARY_TRANSPORT,
            source="transport",
        )
        return web.Response(status=404, text="unknown capture path")

    app = web.Application(client_max_size=0, middlewares=[storage_guard])
    app["journal"] = journal
    app["config"] = config
    app["capability_path"] = capability_path
    app.router.add_get(f"{capability_path}/{HEALTH_SUFFIX}", health)
    app.router.add_post(f"{capability_path}/{HEALTH_SUFFIX}", health)
    app.router.add_route("*", f"{capability_path}/{{tail:.*}}", forward)
    # Catch-all last: any path outside the capability prefix is refused, which
    # is what keeps the listener from acting as a general-purpose proxy.
    app.router.add_route("*", "/{tail:.*}", reject_unknown)
    return app


async def _serve(
    app: Any,
    config: ProxyConfig,
    ready: Any,
    runtime: Dict[str, Any],
    stop_event: Any,
    started: Any = None,
) -> None:
    """Run the proxy until cancelled."""
    _, web = _require_aiohttp()
    runner = web.AppRunner(app)
    # The upstream session is created before startup and stored on the app, so
    # handlers can reach it without mutating app state after it has started.
    import aiohttp

    app["upstream_session"] = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, connect=config.connect_timeout),
        auto_decompress=False,
        trust_env=False,
        cookie_jar=aiohttp.DummyCookieJar(),
    )
    try:
        await runner.setup()
        site = web.TCPSite(runner, config.bind_host, config.port, reuse_address=True)
        await site.start()
    except BaseException:
        await app["upstream_session"].close()
        await runner.cleanup()
        raise
    runtime["runner"] = runner
    ready.set_result(config.port)
    if started is not None:
        started.set()
    try:
        await stop_event.wait()
    finally:
        # Bounded, staged teardown: stop accepting, close the upstream session,
        # then clean up the runner. Every stage is capped so a lingering
        # keep-alive connection can never wedge run finalisation.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(site.stop(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(app["upstream_session"].close(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(runner.cleanup(), timeout=SHUTDOWN_TIMEOUT_SECONDS)


def start_capture_proxy(
    journal: EventJournal,
    config: ProxyConfig,
    upstream_auth: Optional[str] = None,
    known_credentials: Optional[List[str]] = None,
) -> ProxyHandle:
    """Start the capture proxy on a background event loop thread.

    The upstream session uses ``total=None`` so the proxy never introduces a
    default total timeout the cage did not ask for; only the connect phase is
    bounded.

    Args:
        journal: Attempt journal receiving transport events.
        config: Bind/advertise/upstream configuration. ``port=0`` picks an
            ephemeral port.
        upstream_auth: Real provider credential substituted for the capability
            token on forwarded requests. Memory only; never journalled.
        known_credentials: Literal credentials to scrub from stored bodies.

    Returns:
        A :class:`ProxyHandle` with ``base_url``, ``port``, and the capability
        path the cage must use.

    Raises:
        ProxyUnavailable: If the listener cannot be started.
    """
    if config.port == 0:
        config.port = resolve_free_port(config.bind_host)
    capability_path = build_capability_path()
    token = secrets.token_hex(24)
    app = create_app(
        journal,
        config,
        capability_path,
        token,
        upstream_auth=upstream_auth,
        known_credentials=known_credentials,
    )

    loop = asyncio.new_event_loop()
    ready: Any = loop.create_future()
    failure: Dict[str, BaseException] = {}
    runtime: Dict[str, Any] = {}
    stop_event = asyncio.Event()
    started: Any = threading.Event()

    async def _bootstrap() -> None:
        try:
            await _serve(app, config, ready, runtime, stop_event, started)
        except BaseException as exc:  # noqa: BLE001 - surfaced via the future
            failure["error"] = exc
            if not ready.done():
                ready.set_exception(exc)
            started.set()

    def _run_loop() -> None:
        try:
            loop.run_until_complete(_bootstrap())
        except RuntimeError:
            # Loop stopped during teardown before the coroutine settled.
            pass
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    thread = threading.Thread(target=_run_loop, name="nichebench-capture-proxy")
    thread.daemon = True
    thread.start()

    # Wait on an explicit signal from inside the loop rather than polling the
    # socket: the listener is bound once the serve coroutine has started.
    if not started.wait(timeout=STARTUP_TIMEOUT_SECONDS):
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        detail = f"{type(failure['error']).__name__}" if "error" in failure else "listener_not_ready"
        raise ProxyUnavailable(f"capture proxy failed to listen on {config.bind_host}:{config.port} ({detail})")
    if "error" in failure:
        if ready.done():
            ready.exception()
        thread.join(timeout=5)
        raise ProxyUnavailable(f"capture proxy startup failed: {type(failure['error']).__name__}")

    handle = ProxyHandle(
        base_url=f"http://{config.advertise_host}:{config.port}{capability_path}/v1",
        port=int(config.port),
        capability_path=capability_path,
        config=config,
        token=token,
        _loop=loop,
        _thread=thread,
        _runner=runtime.get("runner"),
        _stop_event=stop_event,
    )
    try:
        journal.append(
            {
                "event": "proxy_started",
                "upstream": config.upstream,
                "bind_host": config.bind_host,
                "advertise_host": config.advertise_host,
                "port": int(config.port),
                "capability_path_prefix": CAPABILITY_PREFIX,
                "auth_required": True,
                "upstream_auth_replaced": bool(upstream_auth),
                "cage_base_url_path_suffix": UPSTREAM_PATH_SUFFIX,
            },
            boundary=BOUNDARY_TRANSPORT,
            source="transport",
        )
    except CaptureError:
        handle.stop()
        raise
    return handle
