# Quiz Tasks

Quiz tasks evaluate framework knowledge with static multiple-choice questions. `LiteLLMJudge.score_quiz` reads the candidate answer, returns a selected letter, and NicheBench compares that letter with the gold letter. The deterministic parser runs on the same answer for audit only; it never determines the score.

## Format

```yaml
# frameworks/drupal/data/quiz/my_quiz.yaml
id: "drupal_quiz_006"
dataset_version: quiz-v1
context: "You're building a custom module..."
question: "Which API should you use for custom entities in Drupal 11?"
choices:
  - "hook_entity_info()"
  - "EntityTypeInterface annotation"
  - "EntityInterface::create()"
  - "Custom entity plugins"
  - "Configuration entity handlers"
  - "Typed config schema classes"
correct_choice: "B"
```

Choices are written as plain option text, with no letter prefixes. The harness assigns letters dynamically from `A` through the letter for the last choice (for example, `A`–`F` for six choices) when it builds the prompt.

`dataset_version` marks the dataset revision. Rows without an explicit value default to `quiz-v1`, and results carry that version. The private dataset was migrated to `quiz-v2` in submodule commit `1225a56`. Its gold-answer distribution is A=25 and B–F=24 each.

## Prompt Structure

**MUT prompt** (system + context + question + choices):
- Choices are rendered once each with a dynamic letter, from `A` for the first choice through `chr(64 + n)` for the last (`A`–`F` for six choices, `A`–`D` for four).
- MUT must respond with only the letter of its choice — no explanations, no narrative.

## Scoring

The configured LLM judge determines the answer letter used for scoring. The judge model is recorded in the result, and `judge_method` is `"llm"`. The quiz `--judge` option overrides the configured judge model.

- **Judge:** the judge extracts a selected letter from the full candidate answer. NicheBench compares it with `correct_choice`.
- **Score:** binary — `1.0` when the judge-selected letter matches, `0.0` otherwise. There is no partial credit or 3-value Pass/Partial/Fail band. Results serialize `judge_score` and `final_score` as 1.0 or 0.0, with `deterministic_score: null`.
- **Parser audit:** the deterministic parser records `selected`, `parse_reason`, and `agrees` alongside the judge result. For example, with gold `B`, the response `"A is deprecated, so B"` can be scored as `B` by the judge while the parser reports `agrees: false`. The parser disagreement does not change the score.
- **Retries and errors:** judge calls retry up to 3 times after transient or parse failures. Exhausted failures raise `JudgeError` with `judge_unavailable`, `judge_invalid_json`, `judge_missing_selected`, or `judge_out_of_range`. These rows count as `judge_errors` and are excluded from quiz averages.
- **Validation:** invalid task data (missing or blank gold, missing choices, unsupported choice count, or out-of-range gold) fails closed with `parse_reason` `invalid_task` or `invalid_gold` and `method: "validation"`; the judge is not called.

The parser audit fields expose disagreements between lexical parsing and judge interpretation without affecting accuracy.

## Example

```bash
poetry run nichebench run drupal quiz --ids drupal_quiz_001,drupal_quiz_003
```
