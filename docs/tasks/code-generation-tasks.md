# Code Generation Tasks

Code generation tasks evaluate an LLM's ability to produce production-ready code for a given framework. These can be single-turn or multi-turn (agentic conversation with up to 5 turns).

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
- Judge receives MUT's final code artifact and evaluates against `judge_checklist`
- Each checklist item is scored; weighted blend produces the judge score

## Scoring

- **Deterministic** (if checks defined): file existence, grep patterns, drush commands
- **Judge**: rubric scoring against `judge_checklist`, each item scored 0-1
- **Hybrid**: weighted blend (default 50/50)

## Multi-Turn Mode

Set `max_turns: N` in the manifest to enable multi-turn agentic conversation. The MUT receives the full conversation history and can request clarification or iterate on implementation.

## Example

```bash
poetry run nichebench run drupal code_generation --ids drupal_code_001
poetry run nichebench run drupal code_generation --profile fast
```
