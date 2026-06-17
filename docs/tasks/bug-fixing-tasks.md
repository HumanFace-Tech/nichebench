# Bug Fixing Tasks

Bug fixing tasks evaluate an LLM's ability to diagnose and fix issues in existing code through a multi-turn conversation.

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
- Multi-turn conversation (up to `max_turns`)

**Judge prompt**:
- Evaluates whether the MUT correctly diagnosed the root cause
- Scores the fix quality and completeness
- Checks that no regressions were introduced

## Scoring

- **Judge**: rubric scoring against `judge_checklist`
- **Hybrid**: weighted blend with deterministic checks if defined

## Use Case

Bug fixing tasks test:
- Diagnostic ability (reading logs, tracing execution)
- Framework API knowledge
- Surgical code changes vs. broad rewrites
- Verification behavior (testing the fix)

## Example

```bash
poetry run nichebench run drupal bug_fixing --ids drupal_bug_001
```
