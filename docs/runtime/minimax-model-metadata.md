# MiniMax Model Metadata Backport

The pinned cage runtime (OpenCode **v2.2.5**) does not ship MiniMax model declarations. The
generated cage `opencode.json` previously registered the MiniMax MUT with no model entry beyond
whatever token limits the profile set, so capability metadata (input/output modalities, reasoning,
tools, cost) never reached the cage.

## What is not claimed

An earlier run on these profiles omitted explicit model metadata. The **effective defaults** the
pinned runtime applied in that situation are not recorded, so there is **no evidence that the
omission distorted results**. This backport removes the ambiguity about what the cage is told, not a
measured defect.

## What was backported

`opencode.jsonc` at the project root declares MiniMax M3 and M3.1 Flash Preview under
`providers.minimax.models`. The harness imports that single selected entry into the generated cage
`opencode.json` when a profile sets `runtime_opencode_model_config_file`.

| Field | MiniMax-M3 | MiniMax-M3.1-Flash-Preview |
|---|---|---|
| `limit.context` | 1048576 | 1000000 |
| `limit.output` (native max) | 512000 | 512000 |
| `cost` (input/output/cache.read/cache.write) | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `capabilities.tools` | true | true |
| `capabilities.temperature` | true | true |
| `capabilities.reasoning.supported` | true | true |
| `capabilities.input` | text, image, video | text, image, video |
| `capabilities.output` | text | text |
| `family` / `releaseDate` | minimax / 2026-06-01 | minimax / 2026-09-27 |

## Source catalogue: Token Plan, not pay-as-you-go

These records come from the models.dev **`minimax-coding-plan`** catalogue — the MiniMax Token Plan
subscription — and are aliased to the cage provider key `minimax`.

That choice is deliberate and shows up in two places:

- **Cost is all zeros.** The Token Plan is prepaid, so the catalogue declares
  `input/output/cache.read/cache.write = 0`. These are **not** free-service rates, and they are not
  the pay-as-you-go `minimax` rates.
- **M3 context is 1048576.** The pay-as-you-go `minimax` record for `MiniMax-M3` declares
  `context: 1000000`. The coding-plan record declares `1048576`, and that is what the backport uses.

`MiniMax-M3.1-Flash-Preview` is published **only** under `minimax-coding-plan` and
`minimax-cn-coding-plan`. It does not exist in the pay-as-you-go `minimax` catalogue at all, so the
coding-plan record is the only valid source for it.

## Provenance

| Item | Value |
|---|---|
| Catalogue source | `https://models.dev/api.json`, provider `minimax-coding-plan` |
| Snapshot sha256 | `745e35ac78aae05f06eb9c3c873072e5ae1e1c658815d18fffb62143f8f5891b` |
| Upstream checkout | `/tmp/opencode/upstream-20261006-071018` |
| Upstream HEAD | `f03046d9f558bc54a090363fea48441b05f522bf` |
| Pinned runtime tag | `v2.2.5` (V2 line; the V1 pin `1.18.3` = `127bdb30784d508cc556c71a0f32b508a3061517` is historical) |
| Config schema | `https://opencode.ai/config.json` (OpenCode V2) |
| Raw selected records | [minimax-model-metadata.source.json](./minimax-model-metadata.source.json) |

The snapshot hash is recomputed from the canonical JSON (sorted keys, compact separators) of the two
model records. The unit test recomputes it from the file on disk, so a stale hash fails the build
instead of silently drifting.

## What this is not

This is a **capability backport**, not a runtime upgrade. Nothing else moves:

- No OpenCode SDK or sampler upgrade. The container image pin moves with the V2 line.
- No prompt, permission or provider-route changes. The cage still emits its own
  deny-by-default permission array, prompt and provider settings.
- No change to the selected interactive model.

## Deliberate exclusions

- **V1-only model fields** — `id`, `release_date`, `attachment`, `reasoning_options`, `tool_call`,
  `modalities`, top-level `reasoning` and `open_weights` are not V2 model fields. They are dropped
  rather than translated, so the cage config contains no schema-invalid keys.
- **`reasoning_options`** — the upstream effort-scale options are not part of the V2 capability
  shape. Capability is carried as `capabilities.reasoning.supported` instead.
- **`interleaved`** — not published upstream for these models.

On cost specifically: the V2 schema carries `cost` as an object or an array of
`{input, output, cache: {read, write}}` records with an optional `tier`. The Token Plan catalogue
carries no tiered pricing, so a single untiered record is emitted and nothing is fabricated.

## Native max vs benchmark output

The native output maximum is 512,000 tokens for both models. Benchmark runs deliberately cap output
at **32,768** via `runtime_opencode_model_limits`, so the output policy stays stable across models
regardless of the native ceiling. Runtime limits are applied on top of the imported entry, and any
other `limit` field carried by the declaration is preserved.

| Profile | Model | Benchmark context | Benchmark output |
|---|---|---|---|
| `minimax_m3` | MiniMax-M3 | 1048576 | 32768 |
| `minimax_m31` | MiniMax-M3.1-Flash-Preview | 1000000 | 32768 |

## Opt-in, not auto-mounted

`opencode.jsonc` at the project root is **not** mounted into the cage. The harness reads it from the
configured host path at config-generation time and copies only the selected model entry, so
permissions, agents, plugins and provider settings in that file cannot reach the cage. Only
`SUPPORTED_MODEL_METADATA_FIELDS` are propagated — and that whitelist deliberately excludes
model-level `settings`, `headers`, `body`, `package` and `variants`, which could carry credentials
or per-request overrides.

Because the file is read at config-generation time, **existing cage processes keep the metadata
they started with. New launches pick up edits.**

## Strict JSON despite the `.jsonc` name

The importer parses the configured file with `json.loads`, which accepts only the strict JSON subset.
The file is named `opencode.jsonc` because that is OpenCode's own convention for a project-root
config. It declares `"$schema": "https://opencode.ai/config.json"` as an editor hint so tooling
validates it against the pinned schema. **No JSONC parsing library is used and none is needed** —
the declaration is written as strict JSON, so comments, trailing commas and other JSONC extensions
are not supported. Keep the file strict-JSON clean.

## Using it

In a profile's `evaluation` block:

```yaml
evaluation:
  runtime_opencode_api_base: "https://api.minimax.io/v1"
  runtime_opencode_model_config_file: "/workspaces/nichebench/opencode.jsonc"
  runtime_opencode_model_limits:
    MiniMax-M3:
      context: 1048576
      output: 32768
```

If the configured path is missing or malformed, generation raises a `ValueError` naming the file,
the provider key and the model id rather than silently dropping the declaration.
