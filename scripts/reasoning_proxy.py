#!/usr/bin/env python3
"""output_index injection proxy for llama-swap → OpenCode.

llama-swap's Responses API never sends ``output_index`` on
``response.output_item.added``, ``response.output_item.done``, or
``response.function_call_arguments.delta`` events.  OpenCode's @ai-sdk/openai
Zod schema requires it — without it every delta raises "missing output at
index" and the agent loop dies after one call.

This proxy adds ``output_index`` (the sequential position of each output item)
without touching any model content (reasoning, messages, function calls).

Topology:
    OpenCode (cage) → proxy:9393 → llama-swap:9292
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Generator, Iterator, Optional, Tuple

import httpx

SseEvent = Tuple[Optional[str], str]


def _iter_sse_events(resp: httpx.Response) -> Iterator[SseEvent]:
    event_type: Optional[str] = None
    data_parts: list[str] = []
    buf = ""
    for text in resp.iter_text():
        buf += text
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.rstrip("\r")
            if line.startswith("event: "):
                event_type = line[7:].strip()
            elif line.startswith("data: "):
                data_parts.append(line[6:])
            elif line == "":
                if event_type is not None or data_parts:
                    yield (event_type, "\n".join(data_parts))
                event_type = None
                data_parts = []
    if event_type is not None or data_parts:
        yield (event_type, "\n".join(data_parts))


def _format_sse(event_type: Optional[str], data: str) -> bytes:
    lines = []
    if event_type:
        lines.append(f"event: {event_type}")
    lines.append(f"data: {data}")
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _inject_output_index(data: str, idx: int) -> str:
    try:
        obj = json.loads(data)
        if "output_index" not in obj:
            obj["output_index"] = idx
        return json.dumps(obj)
    except Exception:
        return data


def _filter_responses_events(events: Iterator[SseEvent]) -> Generator[SseEvent, None, None]:
    """Add output_index to output_item and function_call_arguments events."""
    next_index = [0]
    event_count = [0]
    yield_count = [0]

    for event_type, data in events:
        event_count[0] += 1
        if event_type in ("response.output_item.added", "response.output_item.done"):
            idx = next_index[0]
            next_index[0] += 1
            data = _inject_output_index(data, idx)
        elif event_type == "response.function_call_arguments.delta":
            idx = next_index[0]
            data = _inject_output_index(data, idx)
        yield_count[0] += 1
        yield (event_type, data)


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream: str = "http://localhost:9292"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[proxy] {fmt % args}", file=sys.stderr, flush=True)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        path = self.path
        upstream_url = self.upstream.rstrip("/") + path
        is_responses = path.rstrip("/").endswith("/responses")

        _skip_req = {"host", "content-length", "transfer-encoding", "connection"}
        fwd_headers = {k: v for k, v in self.headers.items() if k.lower() not in _skip_req}
        fwd_headers["Content-Length"] = str(len(body))

        try:
            with httpx.stream("POST", upstream_url, headers=fwd_headers, content=body, timeout=600) as resp:
                if resp.status_code >= 400:
                    body_preview = body[:300].decode(errors="replace") if isinstance(body, bytes) else body[:300]
                    print(
                        f"[proxy] UPSTREAM {resp.status_code} {path} | body={body_preview!r}",
                        file=sys.stderr,
                        flush=True,
                    )
                ct = resp.headers.get("content-type", "")
                is_sse = "text/event-stream" in ct
                self.send_response(resp.status_code)
                _skip_resp = {"transfer-encoding", "connection", "content-length", "content-encoding"}
                for k, v in resp.headers.items():
                    if k.lower() not in _skip_resp:
                        self.send_header(k, v)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    if is_sse and is_responses:
                        self._stream_events(_filter_responses_events(_iter_sse_events(resp)))
                    else:
                        for chunk in resp.iter_bytes():
                            if chunk:
                                self._write_chunk(chunk)
                        self._end_chunks()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        except httpx.RequestError as exc:
            self.log_message("upstream error: %s", exc)
            try:
                self.send_error(502, f"Upstream error: {exc}")
            except Exception:
                pass

    def do_GET(self) -> None:
        upstream_url = self.upstream.rstrip("/") + self.path
        try:
            r = httpx.get(upstream_url, timeout=30)
        except httpx.RequestError as exc:
            self.send_error(502, str(exc))
            return
        body = r.content
        self.send_response(r.status_code)
        _skip = {"transfer-encoding", "connection", "content-encoding", "content-length"}
        for k, v in r.headers.items():
            if k.lower() not in _skip:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _end_chunks(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _stream_events(self, events: Generator[SseEvent, None, None]) -> None:
        for event_type, data in events:
            self._write_chunk(_format_sse(event_type, data))
        self._end_chunks()


def main() -> None:
    ap = argparse.ArgumentParser(description="output_index injection proxy for llama-swap → OpenCode")
    ap.add_argument("--port", type=int, default=9393)
    ap.add_argument("--upstream", default="http://192.168.1.111:9292")
    args = ap.parse_args()
    _ProxyHandler.upstream = args.upstream
    server = ThreadingHTTPServer(("0.0.0.0", args.port), _ProxyHandler)
    print(f"proxy: 0.0.0.0:{args.port} → {args.upstream}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
