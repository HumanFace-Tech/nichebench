# Bug Fixing Tasks

Bug fixing tasks evaluate an LLM's ability to fix an issue in existing code. The MUT makes a single-shot fix, followed by a finalization nudge rather than a diagnostic dialogue.

## Format

```yaml
# frameworks/drupal/data/bug_fixing/my_bug_task.yaml
id: "drupal_bug_003"
context: |
  Project: "LegacyDrupalSite"
  Stack: Drupal 10.2, PHP 8.1
  Issue: Users cannot upload files larger than 1MB despite PHP memory limits being adequate.
summary: Diagnose and fix the file upload size limit issue.
prompt: |
  The file upload feature broke after a module update. Users see "File too large"
  errors for files well under the PHP limit. Investigate and fix the issue.
max_turns: 5
judge_checklist:
  - "Correctly identified the cause: formatters or entity field validator settings"
  - "Applied a fix that allows files up to the intended limit"
  - "Did not break other file upload functionality"
  - "Verified the fix works for the reported scenario"
```

## Prompt Structure

**MUT prompt** includes:
- Project context and stack
- Bug description
- The requested fix
- A finalization nudge to complete the proposed change

The runner reads `max_turns` from the task manifest and clamps it to 1–8.

**Judge prompt**:
- `LiteLLMJudge.score_bug_fixing` evaluates the response against the rubric directly. DeepEval is not used.
- NicheBench recomputes the score from criteria: true is 1, partial is 0.5, and false or missing is 0. The result is clamped to the valid score range.
- Judge calls get up to 3 attempts after transient or parse failures. `final_score` is populated from the recomputed score. Judge errors are recorded and excluded from averages.

## Scoring

- **Judge:** direct rubric scoring against `judge_checklist`. Classic bug-fixing scores are not a deterministic/judge hybrid.

## Use Case

Bug fixing tasks test:
- Ability to produce a focused fix from the supplied context
- Framework API knowledge
- Surgical code changes vs. broad rewrites
- Verification behavior (testing the fix)

## Example

```bash
poetry run nichebench run drupal bug_fixing --ids drupal_bug_001
```
