# Quiz Tasks

Quiz tasks evaluate LLM knowledge of a framework through static multiple-choice questions, scored by an LLM-as-a-Judge.

## Format

```yaml
# frameworks/drupal/data/quiz/my_quiz.yaml
id: "drupal_quiz_006"
context: "You're building a custom module..."
question: "Which API should you use for custom entities in Drupal 11?"
choices:
  - "hook_entity_info()"
  - "EntityTypeInterface annotation"
  - "EntityInterface::create()"
  - "Custom entity plugins"
correct_choice: "B"
```

## Prompt Structure

**MUT prompt** (system + context + question + choices):
- MUT must respond with a single letter (A, B, C, D, or E)

**Judge prompt** (system + rubric):
- Judge receives MUT's response and evaluates correctness
- Returns structured JSON: `{"pass": true/false, "selected": "B", "score": 1, "explanation": "..."}`

## Scoring

- **Pass**: MUT selected the correct choice
- **Score**: 1 (correct) / 0 (incorrect)

3-value scoring (Pass/Partial/Fail) applies to the judge score as a percentage:
- **Pass**: score ≥ 0.66
- **Partial**: 0.33 ≤ score < 0.66
- **Fail**: score < 0.33

## Example

```bash
poetry run nichebench run drupal quiz --ids drupal_quiz_001,drupal_quiz_003
```
