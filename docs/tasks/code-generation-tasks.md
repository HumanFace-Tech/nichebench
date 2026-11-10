# Code Generation Tasks

Code generation tasks evaluate an LLM's ability to produce production-ready code for a given framework in a single turn.

## Format

```yaml
# frameworks/drupal/data/code_generation/my_code_task.yaml
id: "drupal_code_013"
context: |
  Project: "Summit"
  Stack:
    - Drupal 11.1 (standard install)
    - PHP 8.2
  Scenario:
    - Marketing wants a short-lived promo message on the homepage.
    - Editors need to toggle the message without deploying code.
summary: Provide a configurable promo block that only renders when enabled.
prompt: |
  Create a `summit_promo` module exposing a `SummitPromoBlock` block plugin.
  The block should render a promo message pulled from `summit_promo.settings` and only
  appear when the feature is enabled. Add a ConfigFormBase settings page so editors can
  toggle the feature and update the message. Ensure the block bubbles cache tags/contexts
  for the config and respects max-age. Include an automated test that flips the config
  and asserts the block output.
judge_checklist:
  - "Block plugin class annotated with `@Block`, uses dependency injection, and returns a render array with the configured message when enabled."
  - "Settings form extends ConfigFormBase, writes to `summit_promo.settings`, and prevents saving an empty message while enabled."
  - "Typed config schema present; default config installed via `config/install` with sensible initial values."
  - "Block attaches cache metadata (config tag + user permissions) using CacheableMetadata to avoid stale output."
  - "Functional test (BrowserTestBase or Kernel plus Block plugin build) toggles the config and asserts the block text visibility."
judge_notes: |
  • Block plugins live under `Drupal\summit_promo\Plugin\Block` and extend BlockBase.
  • Inject config with `ConfigFactoryInterface` or `ImmutableConfig`, not `\Drupal::config()`.
  • Settings form should clear caches after save so block output updates immediately.
  • Tests may use `drupalPlaceBlock()` or build the block plugin manually; ensure assertions cover both enabled and disabled states.
```

## Prompt Structure

**MUT prompt** includes:
- Full context (project, stack, scenario)
- Summary of expected deliverable
- Detailed implementation prompt
- `judge_notes` with framework-specific guidance

**Judge prompt** (system + rubric):
- `LiteLLMJudge.score_code_generation` evaluates the response against `judge_checklist` directly. DeepEval is not used.
- NicheBench recomputes the score from criteria: true is 1, partial is 0.5, and false or missing is 0. The result is clamped to the valid score range.
- The judge gets up to 3 attempts after transient or parse failures. `final_score` is populated from the recomputed score. Judge errors are recorded and excluded from averages.

## Scoring

- **Judge:** direct rubric evaluation against `judge_checklist`; the final score is recomputed from criterion results and clamped. This classic task does not use a deterministic/judge hybrid score.
- Judge calls receive up to 3 attempts after transient or parse failures. Judge errors are excluded from averages.

## Structural Sanity Checks

Before the judge is called, code and bug outputs get a lean **structural**
sanity pass (`src/nichebench/metrics/sanity.py`). It is structural only: no
class-name, namespace, architecture, or Drupal-semantic inspection.

**Hard findings** fail the task without calling the judge (`final_score = 0.0`,
`passed = false`, `error_kind` set to the check name):

| Check | Trigger |
|---|---|
| `sanity_no_code` | no fenced block **and** no `FILENAME:` / `PATCH:` / `REPLACE:` marker |
| `sanity_truncated` | unbalanced ``` fences, a fence still open at the end of the response, or a response ending in `... [truncated]` |

**Advisory findings** never change the score. They are attached to the judge
output as `sanity_findings` (also written to `TestResult.sanity_findings`)
and rendered as the `ok` / `advisory` / `hard` indicator in the code detail
table:

- `sanity_no_declared_files` — code present but no declared path.
- `sanity_duplicate_path` — the same declared path appears more than once.
- `sanity_yaml_parse` / `sanity_json_parse` — a full-file YAML/JSON block does not parse.
- `sanity_php_syntax` — `php -l` reported a syntax error (advisory).
- `sanity_php_skipped` — no `php` binary on the host, so the lint is skipped; never a failure.

**Expected markers:** `FILENAME: path`, `PATCH: path`, `REPLACE: path`, each
followed by a fenced code block; generic fenced blocks are accepted too, and
narration before/between blocks is tolerated.

**Switches** (under `evaluation` in `nichebench.yml`):

- `code_sanity_checks: true` (default) — set `false` to skip the entire layer.
- `code_sanity_blend: false` — reserved for a future judge/sanity score blend; **not implemented**.

## Example

```bash
poetry run nichebench run drupal code_generation --ids drupal_code_001
poetry run nichebench run drupal code_generation --profile fast
```
