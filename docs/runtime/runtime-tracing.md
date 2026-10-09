# Runtime Tracing

Runtime capture records HTTP entity bodies on the supported MiniMax route,
agent stdout/stderr, native OpenCode events, and session-store rows. Inspect
`exact`, `exclusions`, and descriptor completeness before treating any artifact
as lossless. Known credentials are replaced in stored copies. Compressed bodies
are withheld, not claimed as inspected. The judge lane captures application
objects, not HTTP wire traffic.

It is off by default. Turn it on per run:

```yaml
evaluation:
  runtime_capture_enable: true
  runtime_capture_disk_quota_bytes: 4294967296   # 4 GiB
  runtime_artifact_retention: standard           # or "full"; never "minimal"
  runtime_opencode_api_base: https://api.minimax.io/v1
```

```bash
poetry run nichebench run drupal_runtime runtime --ids drupal_runtime_001 \
  --model minimax/MiniMax-M3 --profile minimax_m3
```

## Why it exists

A failing agentic run is hard to diagnose from the artifacts the harness keeps
by default. `run.log` holds decoded text, `trajectory.json` holds a reduced
summary of one session, and `final.diff` shows the end state. None of that tells
you whether the provider rejected a request, whether the model streamed a tool
call that never reached the API, or what the full tool input was for a large
`write`. Capture records those facts at the boundary where they happened.

## Where the archive lives

```
<results_outdir>/captures/<uuid>/
├── capture.json                     run descriptor: completion, reasons, paths
├── attempts/
│   ├── attempt_001/                 one directory per agent attempt
│   │   ├── events.jsonl             incremental journal (append-only, fsync per record)
│   │   ├── blobs/<sha256>.bin       content-addressed payload blocks
│   │   ├── stdout.bin, stderr.bin   agent byte streams, known credentials scrubbed
│   │   ├── opencode.json            effective generated config, credentials removed
│   │   ├── opencode_config_manifest.json
│   │   ├── agent_timeline.json      normalised per-attempt ordering
│   │   └── opencode-db/opencode.db  WAL-consistent, sanitised snapshot if safe
│   └── attempt_002/                 a retry or review pass — never overwritten
└── judges/
    └── attempt_001/                 application-boundary records for the judge lane
```

The archive root lives under the results directory, **not** under the task
workspace, so task cleanup never removes it. Directories are `0700` and files
are `0600`: captures are fine-tuning material and stay host-local.

The task artifact folder gets a `capture.json` pointer, and `runtime_trace.json`
plus `metadata.json` carry a reference. Those are the intended entry points —
follow the path to reach the journal.

Descriptor version 2 includes a transport summary. Finalisation refreshes the
task pointer, metadata pointer, and runtime-trace pointer from the same final
descriptor, after judge capture has closed. These are independent snapshots;
later labels cannot mutate an earlier pointer's lists.

## Journal and blob store

`events.jsonl` is append-only. Each record carries a monotonic `seq`, an
observation `timestamp`, the `source` subsystem that saw it, the `invocation_id`,
and — where applicable — `request_id` and `part_id`.

```json
{"event":"http_request","seq":12,"method":"POST","upstream_url_path":"/v1/chat/completions",
 "headers":{"incoming":{"Authorization":"[REDACTED]"},"exact_bytes":false},
 "boundary":"transport_http_entity","source":"transport","request_id":"11"}
```

Native stdout lines have individual `agent_event` payload references. Normalised
events retain part identities, tool inputs and results, and observed order.
They do not concatenate an ever-growing transcript. Each HTTP request body has
its own reference tied to `request_id`; repeated conversation history that the
SDK actually sent remains in those bodies.

Raw body, native-event, and DB-row payloads use 64 KiB content-addressed blocks.
Identical blocks are stored once. Deduplication applies to identical aligned
blocks, not every shared substring or growing JSON prefix. Payloads over one
block get a manifest listing the pieces. Normalised event fields may also be
inline in the journal.

Journal records and process-spool writes are flushed and `fsync`ed. Streaming
scrubbing may hold an unfinished credential prefix in memory. A hard host kill
can lose that tail and in-flight writes; session checkpoints can lag by 15
seconds. An unfinished descriptor is not proof of a complete capture.

## What each boundary claims

Capture labels every record with the boundary it observed. This matters, because
the boundaries have very different strength:

| Boundary | What it is | What it is not |
|---|---|---|
| `transport_http_entity` | Request/response body bytes, offsets, status, terminal state; exact only where labelled | TLS packets, compressed-body contents, or provider interpretation |
| `process_stream` | Agent stdout/stderr bytes with observation times; known credentials scrubbed | Parsed meaning or a guarantee about an unflushed tail |
| `agent_timeline` | OpenCode JSON events and DB-derived ordering | A claim of global or provider-side event order |
| `db_snapshot` | Sanitised SQLite backup and typed row export across all sessions and tables | A wire capture or a guaranteed byte-identical SQLite file |
| `application_response` | LiteLLM call arguments and the provider's public response object | A wire capture of the judge call |

Explicitly **not** observable, and never invented: provider-internal hidden
reasoning, and the provider's own event ordering. When the harness cannot see
something, the archive says so rather than filling the gap.

## Transport capture

The cage reaches the provider through a proxy process so the harness sees the
bytes. Only the transport base URL changes — the provider key, npm provider
package, model id, prompt, tools, permissions, and sampling parameters are all
preserved, and no SDK switch happens.

The proxy:

- forwards to the fixed origin `https://api.minimax.io/v1`, preserving method,
  path, query, body bytes, and application headers;
- uses the upstream scheme and host plus the SDK's encoded path/query, so
  `/v1/chat/completions` does not become `/v1/v1/chat/completions`;
- strips hop-by-hop headers and rewrites `Host`;
- does **not** follow redirects, decompress bodies, keep cookies, or retry;
- verifies TLS and adds no total response timeout of its own;
- streams SSE through untranslated, recording each chunk with its byte offset
  before forwarding it;
- records `complete`, `partial`, `disconnect`, or `error` per response, and
  records a connect/DNS failure as an attempted request with no invented
  response.

Streaming scrubbing keeps a carry-over prefix so a known credential split
between SSE reads cannot enter stored blobs. Forwarded bodies are never
rewritten. Stored offsets refer to the stored copy; terminal records report
received and forwarded byte counts separately. Compressed request and response
bodies are forwarded unchanged but withheld from storage, with explicit
incomplete reasons.

A `partial` or `disconnect` terminal, cancelled handler, missing response
terminal, or unretained observed bytes makes overall capture incomplete. This
does not change benchmark scores. A retained stream prefix is not a full
response, even when it contains a model finish reason. The proxy flushes its
credential-prefix carry on interruption as well as normal EOF. Interruptions
record their phase (`upstream_read`, `downstream_write`, or prepare), received
bytes, and successfully forwarded bytes separately.

An SDK can cancel a connection on its own idle timeout. The proxy does not add
heartbeats, retry, alter provider timeouts, or invent the rest of that response.
The phase identifies which operation failed, not why the upstream paused or
which unobserved provider events occurred.

**Not an open proxy.** The listener binds `0.0.0.0` on an ephemeral port so the
cage container can reach it via `host.docker.internal` (injected with
`--add-host … :host-gateway`). Access requires both a per-run random path prefix
and a per-run random bearer token. The token is compared in memory and excluded
from the archive. Capability URLs can appear in the sanitised config and event
metadata. Any other path is refused with 404.

A `healthz` endpoint verifies connectivity through the Docker host gateway.
The image probe uses `curl --fail --max-time`, `HOME=/tmp`, and checks both the
HTTP status and response body. A failed probe stops setup before agent execution.
Image ID and the actual `opencode --version` result are recorded. These probes
make no provider call.

## Agent timeline

With capture enabled the cage runs `opencode run --pure --format json --thinking`
(flags appended only in this mode; the non-capture command is unchanged). The
journal then records, per part:

- assistant text — full, uncapped;
- reasoning parts — kept separate from text;
- tool calls with **full** inputs, including large `write`/`edit` payloads;
- tool results, errors, and step markers;
- `assistant_completed_text` — the text the scoring path derives, emitted once.

Malformed lines and bare JSON arrays are retained through native payload refs
and the process spool, counted in the stream summary, and mark interpretation
incomplete. Unknown object-shaped events remain available as native events.

Scoring is unaffected. The run's `mut_output` remains the agent's final assistant
text; reasoning and tool JSON are never mixed into it, and the derived text is
asserted equal to the plain stdout text in tests.

## Session store archive

`opencode.db` is backed up with SQLite's backup API, so rows committed to the
WAL but not yet checkpointed are included — a plain file copy would lose them.
Every table from every session is exported with OpenCode's IDs preserved.
Cells use tagged types; SQLite BLOBs use base64 rather than lossy UTF-8 decoding.
`decode_column` reverses that encoding. JSON stored inside a text cell remains
its original string, including full running or completed tool state. There is
no production 100-row cap. The existing trajectory reconstruction keeps one
session and summarises parts; capture does not.

Backups are built in private temporary storage outside the archive. Known
credentials are removed from text and BLOB cells, free pages are rebuilt with
`VACUUM`, and the file is checked before publication. If sanitisation cannot be
proved, the raw copy is withheld and capture is incomplete. Row exports can
still be produced from the live read-only DB with known credentials removed.

The snapshot is refreshed in place while the run is alive (rate-limited, staged
so a reader never sees a half-written file), so a host kill mid-run still leaves
current session state on disk. The end-of-attempt archive is the authoritative
copy.

`agent_timeline.json` is a chronological view derived from
`message.time_created`, `message.id`, `part.time_created`, `part.id`. That basis
is recorded on every record. It is a per-attempt ordering, not a global one.

Tool output the agent wrote but never transmitted upstream stays in the session
store and in file payloads. It is present in the archive and absent from the
captured HTTP request bodies. That distinction is recorded explicitly.

## Failure paths

The capture is finalised **before** any state is deleted, on every exit path:
normal exit, non-zero exit, timeout, watchdog kill, poll error, and
`KeyboardInterrupt`. Readers drain, in-flight proxy exchanges are cancelled, the
session store is snapshotted and exported, and only then is the descriptor
written. A snapshot failure is recorded in `reasons`; cleanup still runs.

Non-zero exits still attempt the legacy session dump. Capture's typed row export
handles BLOBs independently of that legacy JSON dump. Setup failures also stop
the proxy, close journals, write an incomplete descriptor, and remove allocated
cage state.

## Retry, review, and parallel runs

Every attempt owns an immutable numbered directory — `initial`,
`retry:<reason>`, `review_nudge`. Nothing is ever written over a previous
attempt. The `CaptureRun` object is allocated from the executor's results
directory per task run and passed explicitly down the call chain; there is no
mutable executor-global recorder. Application capture uses `ContextVar` scopes,
with token reset in `finally`; nested scopes restore the previous session and
lane. This isolation does not lift the host's sequential DDEV constraint.

## Privacy

Auth header values and known provider keys are excluded from the capture archive.
Header names remain verifiable; credential header values become `[REDACTED]`.
The real upstream key is held by the proxy, not serialized into captured configs
or command records. The cage receives a disposable proxy token instead.

Scrubbing has two layers, and the distinction matters:

- **Credential keys** — a value stored under a key whose name means "credential"
  is replaced, including a string-valued exact `token` key. Numeric usage counters and ordinary
  `token_count`/`input_tokens` fields are untouched.
- **Exact credentials** — only byte sequences the harness holds in memory are
  replaced, and each hit is recorded so the affected artifact is marked redacted
  rather than presented as an exact copy.

Shared report redaction also recognises credential formats such as
`MINIMAX_API_KEY=...`, JSON credentials, and Bearer values. Raw payload storage
uses exact known-value replacement, not broad token-word heuristics. Unknown
credentials in arbitrary bodies, tool output, source code, or encodings cannot
be guaranteed absent. Review captures before sharing or training on them.

## Disk quota

The quota is enforced per attempt across journal records, blobs, process spools,
and tracked auxiliary files. A 256 KiB physical emergency reserve is kept outside
the payload quota for completion metadata. Quota and write failures notify the
owning session; the cage terminates its child, the proxy refuses subsequent
requests with 507, and capture retries are disabled. Mid-stream exchanges keep
an explicit partial terminal record. Teardown still runs.

The reserve improves failure reporting but cannot guarantee a descriptor after
host failure, filesystem corruption, or other writers consuming all freed disk
space. A missing or unfinished descriptor must not be treated as complete.

## Unsupported routes

Only the pinned `minimax` provider against `https://api.minimax.io/v1` is
supported. Enabling capture for any other endpoint fails loudly at allocation
time rather than producing a partial capture with an implied completeness claim.
`runtime_artifact_retention: minimal` is rejected for the same reason — it cannot
guarantee a complete journal.

Other models work fine *without* capture. Nothing about the non-capture path
changes: no proxy, no `--format json`, no extra flags.

## Reading a capture

Start from the descriptor:

```bash
jq . results/.../runtime/drupal_runtime_001/capture.json
```

Then fold the journal:

```bash
jq -r 'select(.event=="agent_text") | .text' \
  results/.../captures/<uuid>/attempts/attempt_001/events.jsonl
```

Payload references resolve against the sibling `blobs/` directory using
`(sha256, offset, length)` from the record.

Use the read-only inspection helpers to verify references and join response
chunks before interpreting SSE. A blob can end in the middle of UTF-8, JSON, or
an SSE frame; parsing each blob as JSON produces false error counts.

```python
from nichebench.execution.runtime.capture.inspection import (
    reconstruct_response,
    summarize_sse,
    summarize_transport,
)
from nichebench.execution.runtime.capture.store import read_events

records = read_events(attempt_path)
response = reconstruct_response(attempt_path, records, request_id)
print(response.state, response.exact, response.problems)  # No payload output.
print(summarize_sse(response.body))
print(summarize_transport(records))
```

Reconstruction verifies hashes and stored offsets, and refuses missing or
overlapping ranges. SSE inspection counts complete frames separately from an
unfinished tail. `[DONE]` is not required when the observed HTTP entity ends
normally; absence of `[DONE]` alone does not prove a truncated response. HTTP
attempts include SDK retries and auxiliary title requests, not just logical
agent turns.

For older captures, reconcile only derived descriptors and pointers. Keep raw
journals, blobs, streams, and DB copies unchanged. A separate reconciliation
audit should record the old/new completeness, evidence hashes, final judge
attempts, and known omissions. It must not repair missing bytes by invention or
change benchmark scores or recorded stage outcomes.

## Tests

Capture tests are offline: local aiohttp stubs stand in for the provider, a fake
`docker` executable drives the process lifecycle, and a synthetic SQLite store
stands in for OpenCode's session DB. No network, no Docker, no paid calls.

An additional offline validation used the installed
`nichebench/opencode-ddev:1.18.3` image, its bundled npm SDK, and a local model
stub. It handled one title request and two agent requests, executed the incoming
`bash` tool schema to write a marker, and confirmed the tool result in the next
request. The returned `mut_output` contained only final assistant text. This is
not a paid MiniMax or DDEV benchmark validation.

```bash
poetry run pytest -q tests/unit/runtime
```
