# MiniMax Model Metadata Backport

The pinned cage runtime (OpenCode **v1.18.3**) does not ship MiniMax model declarations. The
generated cage `opencode.json` previously registered the MiniMax MUT with no model entry beyond
whatever token limits the profile set, so capability metadata (modalities, reasoning, attachment,
cost) never reached the cage.

## What is not claimed

An earlier run on these profiles omitted explicit model metadata. The **effective defaults** the
pinned runtime applied in that situation are not recorded, so there is **no evidence that the
omission distorted results**. This backport removes the ambiguity about what the cage is told, not a
measured defect.

## What was backported

`opencode.jsonc` at the project root declares MiniMax M3 and M3.1 Flash Preview under
`provider.minimax.models`. The harness imports that single selected entry into the generated cage
`opencode.json` when a profile sets `runtime_opencode_model_config_file`.

| Field | MiniMax-M3 | MiniMax-M3.1-Flash-Preview |
|---|---|---|
| `limit.context` | 1048576 | 1000000 |
| `limit.output` (native max) | 512000 | 512000 |
| `cost` (input/output/cache_read/cache_write) | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `attachment` / `reasoning` / `temperature` / `tool_call` | true | true |
| `modalities.input` | text, image, video | text, image, video |
| `modalities.output` | text | text |
| `family` / `release_date` | minimax / 2026-06-01 | minimax / 2026-09-27 |

## Source catalogue: Token Plan, not pay-as-you-go

These records come from the models.dev **`minimax-coding-plan`** catalogue — the MiniMax Token Plan
subscription — and are aliased to the cage provider key `minimax`.

That choice is deliberate and shows up in two places:

- **Cost is all zeros.** The Token Plan is prepaid, so the catalogue declares
  `input/output/cache_read/cache_write = 0`. These are **not** free-service rates, and they are not
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
| Snapshot sha256 | `e43a7e794e4f76455431c6988875a6a1a330b42ece3b8293201e6c94f20ac32f` |
| Upstream checkout | `/tmp/opencode/upstream-20261006-071018` |
| Upstream HEAD | `f03046d9f558bc54a090363fea48441b05f522bf` |
| Pinned runtime tag | `v1.18.3` = `127bdb30784d508cc556c71a0f32b508a3061517` |
| Pinned model schema | `packages/core/src/v1/config/provider.ts` |
| Raw selected records | [minimax-model-metadata.source.json](./minimax-model-metadata.source.json) |

## What this is not

This is a **capability backport**, not a runtime upgrade. Nothing else moves:

- No OpenCode SDK or sampler upgrade. The container image pin is untouched.
- No prompt, permission or provider-route changes. The cage still emits its own
  deny-by-default permission block, prompt and provider options.
- No change to the selected interactive model.

## Deliberate exclusions

- **`reasoning_options`** — not declared by the pinned v1.18.3 provider model schema
  (`packages/core/src/v1/config/provider.ts`). Declaring it would be inventing config. Both models
  declare it upstream; it is dropped for both, not synthesised for M3.1.
- **`interleaved`** — not published upstream for these models.

On cost specifically: the pinned schema **does** support `cost.context_over_200k` (`input`,
`output`, optional `cache_read` / `cache_write`). The only unsupported cost field is **`tiers`**.
Neither is emitted here — the Token Plan catalogue carries no tiered pricing, so there is nothing to
transcribe and no reason to fabricate a pay-as-you-go rate for M3.1.

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
permissions, agents, plugins and provider options in that file cannot reach the cage. Only
`SUPPORTED_MODEL_METADATA_FIELDS` are propagated.

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
