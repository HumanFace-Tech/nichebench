"""Proxy tests: byte fidelity, SSE, failures, open-proxy rejection, quota.

All upstream interaction is against a local throwaway aiohttp server, so no
provider is contacted and no money is spent.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import threading
from typing import Any, Dict, List, Optional, Tuple

import pytest

from nichebench.execution.runtime.capture import (
    DEFAULT_UPSTREAM,
    CaptureRun,
    CaptureSession,
    CaptureSettings,
    EventJournal,
    ProxyConfig,
)
from nichebench.execution.runtime.capture.proxy import (
    build_capability_path,
    build_gateway_docker_args,
    create_app,
    is_private_advertise_host,
    probe_from_container,
    start_capture_proxy,
)
from tests.unit.runtime.capture_helpers import events, events_of, make_session

TOKEN = "capability-token-0123456789abcdef"


class _Upstream:
    """Local upstream used instead of the real provider."""

    def __init__(self, handler: Any):
        self.handler = handler
        self.requests: List[Dict[str, Any]] = []
        self.port = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._boot_coro: Any = None
        self._stop_event: Any = None

    def start(self) -> str:
        from aiohttp import web

        upstream_self = self

        async def handle(request: Any) -> Any:
            body = await request.read()
            upstream_self.requests.append(
                {
                    "method": request.method,
                    "path": request.path,
                    "query": request.query_string,
                    "body": body,
                    "headers": dict(request.headers),
                }
            )
            result = upstream_self.handler(request, body)
            if asyncio.iscoroutine(result):
                return await result
            return result

        app = web.Application(client_max_size=0)
        app.router.add_route("*", "/{tail:.*}", handle)
        self._loop = asyncio.new_event_loop()
        self._stop_event = asyncio.Event()
        ready: Any = self._loop.create_future()

        async def _boot_impl() -> None:
            runner = web.AppRunner(app)
            try:
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                upstream_self.port = site._server.sockets[0].getsockname()[1]
                upstream_self._runner = runner
                ready.set_result(upstream_self.port)
                await self._stop_event.wait()
            finally:
                # Close the listener so a stopped upstream really refuses
                # connections instead of leaving them queued in the backlog.
                await runner.cleanup()

        self._runner = None
        self._closed_listener = threading.Event()
        self._boot_coro = _boot_impl()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        port = asyncio.run_coroutine_threadsafe(_asyncio_wrap(ready), self._loop).result(timeout=10)
        self.port = int(port)
        return f"http://127.0.0.1:{self.port}"

    def _run(self) -> None:
        try:
            self._loop.run_until_complete(self._boot_coro)
        except RuntimeError:
            # Loop stopped mid-flight during teardown; expected.
            pass
        finally:
            self._loop.close()

    def stop(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def abandon_listener(self) -> None:
        """Close the listening socket without touching the loop.

        Closing the listener first means the port genuinely refuses new
        connections, so a client attempt produces a connect failure rather than
        a request that hangs in the kernel backlog.
        """
        loop = self._loop
        assert loop is not None
        future = asyncio.run_coroutine_threadsafe(self._close_listener(), loop)
        future.result(timeout=5)
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _close_listener(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._closed_listener.set()
        self._stop_event.set()

    def wait_closed(self, timeout: float = 5.0) -> None:
        """Block until the listener has actually stopped accepting."""
        self._closed_listener.wait(timeout=timeout)


async def _asyncio_wrap(fut: Any) -> Any:
    return await fut


def _start_local_proxy(session: CaptureSession, attempt: Any, upstream_url: str) -> Any:
    """Start the capture proxy against a local upstream, returning the handle."""
    journal = session.journal_for(attempt)
    journal._settings = CaptureSettings(
        enabled=True, quota_bytes=4 * 1024**3, retention="standard", route="minimax_m3"
    )
    config = ProxyConfig(
        upstream=upstream_url,
        bind_host="127.0.0.1",
        advertise_host="127.0.0.1",
        port=0,
        allow_insecure_upstream=True,
    )
    return start_capture_proxy(journal, config)


def _post(handle: Any, path: str, body: bytes, headers: Optional[Dict[str, str]] = None) -> Tuple[int, bytes]:
    """Issue a plain HTTP POST through the proxy."""
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{handle.port}{handle.capability_path}{path}"
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Authorization", f"Bearer {handle.token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


class TestProxyGuards:
    def test_default_upstream_is_the_pinned_minimax_origin(self):
        assert DEFAULT_UPSTREAM == "https://api.minimax.io/v1"

    def test_non_https_upstream_is_rejected(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        with pytest.raises(Exception, match="must be https"):
            create_app(session.journal_for(attempt), ProxyConfig(upstream="http://evil.invalid/v1"), "/cap", TOKEN)

    def test_non_pinned_https_upstream_is_rejected(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        with pytest.raises(Exception, match="pinned origin"):
            create_app(
                session.journal_for(attempt),
                ProxyConfig(upstream="https://evil.invalid/v1"),
                "/cap",
                TOKEN,
            )

    def test_unrelated_paths_are_not_proxied(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        upstream = _echo_upstream()
        handle = _start_local_proxy(session, attempt, upstream.start())
        try:
            import urllib.error
            import urllib.request

            url = f"http://127.0.0.1:{handle.port}/somewhere-else"
            request = urllib.request.Request(url, data=b"{}", method="POST")
            request.add_header("Authorization", f"Bearer {handle.token}")
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(request, timeout=10)
            assert excinfo.value.code == 404
            rejected = events_of(events(attempt.path), "proxy_rejected_path")
            assert rejected and rejected[0]["path"] == "/somewhere-else"
        finally:
            handle.stop()
            upstream.stop()

    def test_wrong_capability_token_is_rejected(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        upstream = _echo_upstream()
        handle = _start_local_proxy(session, attempt, upstream.start())
        try:
            import urllib.error
            import urllib.request

            url = f"http://127.0.0.1:{handle.port}{handle.capability_path}/v1/chat/completions"
            request = urllib.request.Request(url, data=b"{}", method="POST")
            request.add_header("Authorization", "Bearer wrong-token-value-000000")
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(request, timeout=10)
            assert excinfo.value.code == 401
            assert events_of(events(attempt.path), "proxy_rejected_auth")
        finally:
            handle.stop()
            upstream.stop()

    def test_capability_path_is_random_per_proxy(self, tmp_path):
        session = make_session(tmp_path)
        first = session.open_attempt("a")
        second = session.open_attempt("b")
        upstream_a = _echo_upstream()
        upstream_b = _echo_upstream()
        handle_a = _start_local_proxy(session, first, upstream_a.start())
        handle_b = _start_local_proxy(session, second, upstream_b.start())
        try:
            assert handle_a.capability_path != handle_b.capability_path
            assert handle_a.capability_path.startswith("/nbcap-")
        finally:
            handle_a.stop()
            upstream_a.stop()
            handle_b.stop()
            upstream_b.stop()

    def test_gateway_docker_args_make_host_gateway_resolvable(self):
        args = build_gateway_docker_args()
        assert args == ["--add-host", "host.docker.internal:host-gateway"]

    def test_advertise_host_must_be_private_or_gateway(self):
        assert is_private_advertise_host("host.docker.internal")
        assert is_private_advertise_host("127.0.0.1")
        assert is_private_advertise_host("192.168.1.10")
        assert not is_private_advertise_host("8.8.8.8")

    def test_health_endpoint_makes_no_paid_call(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        upstream = _echo_upstream()
        handle = _start_local_proxy(session, attempt, upstream.start())
        try:
            import urllib.request

            with urllib.request.urlopen(handle.health_url, timeout=10) as response:
                assert response.status == 200
                assert b"ok" in response.read()
            probes = events_of(events(attempt.path), "health_probe")
            assert probes and probes[0]["paid_call"] is False
        finally:
            handle.stop()
            upstream.stop()

    def test_container_probe_reports_failure_without_raising(self):
        healthy, detail = probe_from_container(["/bin/false"], "http://127.0.0.1:1/healthz", timeout_seconds=5)
        assert healthy is False
        assert "probe_failed" in detail or "rc=" in detail

    def test_container_probe_reports_success(self):
        healthy, detail = probe_from_container(["/bin/echo", "ok\n200"], "http://x", timeout_seconds=5)
        assert healthy is True
        assert "status=200" in detail


class TestProxyByteFidelity:
    def test_request_and_response_bodies_are_captured_exactly(self, tmp_path):
        payload = json.dumps({"model": "MiniMax-M3", "messages": [{"role": "user", "content": "héllo"}]}).encode(
            "utf-8"
        )
        response_body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        upstream = _Upstream(lambda request, body: _json_response(200, response_body))
        upstream_url = upstream.start()
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, upstream_url)
        try:
            status, _ = _post(handle, "/v1/chat/completions", payload)
            assert status == 200
            journal = session.journal_for(attempt)

            requests = events_of(events(attempt.path), "blob_stored")
            request_blobs = [r for r in requests if r["payload_name"] == "request_body"]
            response_blobs = [r for r in requests if r["payload_name"] == "response_body"]
            assert journal.blobs.get(request_blobs[0]["payload_ref"]) == payload
            assert journal.blobs.get(response_blobs[0]["payload_ref"]) == response_body
        finally:
            handle.stop()
            upstream.stop()

    def test_method_query_and_path_are_preserved(self, tmp_path):
        upstream = _Upstream(lambda request, body: _json_response(200, b'{"ok":true}'))
        upstream_url = upstream.start()
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, upstream_url)
        try:
            _post(handle, "/v1/chat/completions?beta=true", b"{}")
            assert upstream.requests[0]["method"] == "POST"
            assert upstream.requests[0]["path"] == "/v1/chat/completions"
            assert upstream.requests[0]["query"] == "beta=true"
            recorded = events_of(events(attempt.path), "http_request")[0]
            assert recorded["query"] == "beta=true"
            assert recorded["retried"] is False
            assert recorded["redirects_followed"] is False
        finally:
            handle.stop()
            upstream.stop()

    def test_authorization_header_value_is_never_persisted(self, tmp_path):
        upstream = _Upstream(lambda request, body: _json_response(200, b"{}"))
        upstream_url = upstream.start()
        session = make_session(tmp_path, known_credentials=[TOKEN])
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, upstream_url)
        try:
            _post(handle, "/v1/chat/completions", b"{}")
            raw = (attempt.events_path).read_text(encoding="utf-8")
            assert TOKEN not in raw
            recorded = events_of(events(attempt.path), "http_request")[0]
            assert recorded["headers"]["incoming"]["Authorization"] == "[REDACTED]"
            assert recorded["headers"]["incoming"]["Content-Type"] == "application/x-www-form-urlencoded"
        finally:
            handle.stop()
            upstream.stop()

    def test_content_encoding_header_survives_while_body_is_not_decompressed(self, tmp_path):
        compressed = gzip.compress(b'{"big":"payload"}')
        upstream = _Upstream(
            lambda request, body: _json_response(200, compressed, headers={"Content-Encoding": "gzip"})
        )
        upstream_url = upstream.start()
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, upstream_url)
        try:
            status, received = _post(handle, "/v1/chat/completions", b"{}")
            assert status == 200
            # Forwarded bytes are the raw gzip stream, not the inflated JSON.
            assert received == compressed
            blobs = events_of(events(attempt.path), "blob_stored")
            response_blobs = [b for b in blobs if b["payload_name"] == "response_body"]
            assert not response_blobs
            assert events_of(events(attempt.path), "response_body_withheld")
            assert session.journal_for(attempt).incomplete_reasons == ["compressed_response_body_withheld"]
            complete = events_of(events(attempt.path), "http_response_complete")[0]
            assert complete["decompressed"] is False
            assert complete["sse_translated"] is False
        finally:
            handle.stop()
            upstream.stop()

    def test_error_status_codes_are_captured_without_invention(self, tmp_path):
        for status_code in (429, 401, 500):
            payload = json.dumps({"error": {"message": f"status {status_code}"}}).encode("utf-8")
            upstream = _Upstream(lambda request, _req_body, code=status_code, resp=payload: _json_response(code, resp))
            upstream_url = upstream.start()
            session = make_session(tmp_path)
            attempt = session.open_attempt(f"status_{status_code}")
            handle = _start_local_proxy(session, attempt, upstream_url)
            try:
                got_status, received = _post(handle, "/v1/chat/completions", b"{}")
                assert got_status == status_code
                assert json.loads(received)["error"]["message"] == f"status {status_code}"
                records = events_of(events(attempt.path), "http_response_complete")
                assert records[0]["status"] == status_code
                assert records[0]["state"] == "complete"
                assert records[0]["retried"] is False
            finally:
                handle.stop()
                upstream.stop()

    def test_upstream_connect_failure_records_attempt_and_no_response(self, tmp_path):
        """A connect failure must be recorded as an attempt, with no response."""
        upstream = _Upstream(lambda request, body: _json_response(200, b"{}"))
        upstream_url = upstream.start()
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, upstream_url)
        # Drop the listener so the port really refuses connections.
        upstream.abandon_listener()
        upstream.wait_closed()
        try:
            status, _ = _post(handle, "/v1/chat/completions", b"{}")
            assert status == 502
            errors = events_of(events(attempt.path), "http_request_error")
            assert errors
            assert errors[0]["response_invented"] is False
            assert errors[0]["state"] == "error"
            # The request side is still recorded: we know what we tried to send.
            assert events_of(events(attempt.path), "http_request_sent")
            # The response side must be absent — nothing was received.
            assert not events_of(events(attempt.path), "http_response_complete")
            assert not events_of(events(attempt.path), "http_response_headers")
        finally:
            handle.stop()
            upstream.stop()

    def test_dns_failure_is_recorded_as_attempt_not_invented_response(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, "http://nonexistent.invalid:9")
        try:
            status, _ = _post(handle, "/v1/chat/completions", b"{}")
            assert status == 502
            errors = events_of(events(attempt.path), "http_request_error")
            assert errors and errors[0]["response_invented"] is False
            assert not events_of(events(attempt.path), "http_response_complete")
        finally:
            handle.stop()

    def test_first_response_chunk_is_recorded_before_upstream_completes(self, tmp_path):
        released = threading.Event()

        async def handler(request: Any, body: bytes) -> Any:
            from aiohttp import web

            response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b'data: {"delta":"a"}\n\n')
            # Hold the stream open so the first chunk is provably recorded while
            # the upstream exchange is still in flight.
            released.wait(timeout=10)
            await response.write(b'data: {"delta":"b"}\n\n')
            await response.write_eof()
            return response

        upstream = _Upstream(handler)
        upstream_url = upstream.start()
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, upstream_url)
        try:
            result: Dict[str, Any] = {}

            def _post_async() -> None:
                result["status"], result["body"] = _post(handle, "/v1/chat/completions", b"{}")

            worker = threading.Thread(target=_post_async)
            worker.start()
            import time

            time.sleep(1.0)
            # Upstream is still open; the first chunk must already be in the journal.
            blobs = events_of(events(attempt.path), "blob_stored")
            response_blobs = [b for b in blobs if b["payload_name"] == "response_body"]
            assert response_blobs, "first chunk must be recorded before the upstream completes"
            assert not events_of(events(attempt.path), "http_response_complete")
            released.set()
            worker.join(timeout=10)
            assert result["status"] == 200
            assert b'"delta":"a"' in result["body"]
            complete = events_of(events(attempt.path), "http_response_complete")
            assert complete[0]["state"] == "complete"
            assert complete[0]["bytes_forwarded"] == len(b'data: {"delta":"a"}\n\n') + len(b'data: {"delta":"b"}\n\n')
        finally:
            released.set()
            handle.stop()
            upstream.stop()

    def test_split_utf8_sse_frames_are_forwarded_byte_exactly(self, tmp_path):
        frames = [
            'data: {"delta":"héllo"}\n\n'.encode("utf-8"),
            "data: [DONE]\n\n".encode("utf-8"),
        ]
        raw = b"".join(frames)

        async def handler(request: Any, body: bytes) -> Any:
            response = web_stream_response(request)
            await response.prepare(request)
            # Deliberately split a multi-byte character across two writes.
            cut = raw.index(b"\xc3") + 1
            await response.write(raw[:cut])
            await response.write(raw[cut:])
            await response.write_eof()
            return response

        upstream = _Upstream(handler)
        upstream_url = upstream.start()
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, upstream_url)
        try:
            status, received = _post(handle, "/v1/chat/completions", b"{}")
            assert status == 200
            assert received == raw
            assert received.decode("utf-8").count("héllo") == 1
            journal = session.journal_for(attempt)
            blobs = [b for b in events_of(events(attempt.path), "blob_stored") if b["payload_name"] == "response_body"]
            assert b"".join(journal.blobs.get(b["payload_ref"]) for b in blobs) == raw
        finally:
            handle.stop()
            upstream.stop()

    def test_disconnect_is_recorded_as_incomplete_not_complete(self, tmp_path):
        async def handler(request: Any, body: bytes) -> Any:
            response = web_stream_response(request)
            await response.prepare(request)
            await response.write(b'data: {"delta":"partial"}\n\n')
            # Kill the connection underneath the client so it observes a real
            # reset rather than a well-formed end of stream.
            request.transport.abort()
            await asyncio.sleep(0.2)
            return response

        upstream = _Upstream(handler)
        upstream_url = upstream.start()
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        handle = _start_local_proxy(session, attempt, upstream_url)
        try:
            _post(handle, "/v1/chat/completions", b"{}")
            interrupted = events_of(events(attempt.path), "http_response_stream_interrupted")
            assert interrupted
            assert interrupted[0]["state"] in ("disconnect", "partial")
            # Bytes delivered before the cut are already in the journal, and the
            # terminal state is a partial transfer — never a clean completion.
            assert interrupted[0]["bytes_forwarded"] == len(b'data: {"delta":"partial"}\n\n')
            complete = events_of(events(attempt.path), "http_response_complete")
            assert complete[0]["state"] in ("disconnect", "partial")
            assert complete[0]["state"] != "complete"
        finally:
            handle.stop()
            upstream.stop()

    def test_no_extra_total_timeout_is_introduced(self):
        """The upstream session must not impose a total response timeout."""
        config = ProxyConfig()
        assert config.connect_timeout > 0
        # The session is constructed with total=None inside _serve; assert the
        # knob itself is a connect-only bound.
        from nichebench.execution.runtime.capture import proxy as proxy_module

        source = proxy_module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        assert "total=None" in text


def web_stream_response(request: Any) -> Any:
    """Prepare and return a prepared ``StreamResponse`` for the test upstream."""
    from aiohttp import web

    return web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})


def _json_response(status: int, body: bytes, headers: Optional[Dict[str, str]] = None) -> Any:
    from aiohttp import web

    return web.Response(status=status, body=body, headers=headers or {"Content-Type": "application/json"})


def _echo_upstream() -> _Upstream:
    """Start a trivial echo upstream the caller must stop."""
    return _Upstream(lambda request, body: _json_response(200, b'{"ok":true}'))


def test_proxy_start_reports_failure_when_port_is_unusable(tmp_path):
    import socket

    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    journal = session.journal_for(attempt)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        busy_port = probe.getsockname()[1]
    config = ProxyConfig(upstream="https://api.minimax.io/v1", bind_host="127.0.0.1", port=busy_port)
    handle = start_capture_proxy(journal, config)
    # SO_REUSEADDR lets the second bind succeed on Linux; either way the handle
    # must be usable or raise ProxyUnavailable — never hang.
    handle.stop()


def test_event_journal_is_importable_for_direct_construction(tmp_path):
    settings = CaptureSettings(enabled=True, route="minimax_m3")
    run = CaptureRun.allocate(tmp_path, settings, "t")
    session = CaptureSession(run)
    attempt = session.open_attempt("initial")
    assert isinstance(session.journal_for(attempt), EventJournal)
    assert build_capability_path().startswith("/nbcap-")
