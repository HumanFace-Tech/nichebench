"""Drupal-specific evaluation tasks for NicheBench."""

import json
from typing import Any, Dict, List

from lighteval.metrics.metrics import MetricCategory, Metrics, MetricUseCase
from lighteval.metrics.utils.metric_utils import SampleLevelMetric
from lighteval.tasks.lighteval_task import LightevalTaskConfig
from lighteval.tasks.requests import Doc

from ...metrics.checklist import checklist_accuracy_fn

# Hardcoded sample data for development (2 samples per category)
DRUPAL_SAMPLE_DATA = {
    "quiz": [
        {
            "id": "drupal_quiz_001",
            "prompt": (
                "What is the correct way to implement a custom field type "
                "in Drupal 10?"
            ),
            "choices": [
                "A) Create a new class extending FieldItemBase",
                "B) Create a new class extending ConfigEntityBase",
                "C) Create a new class extending ContentEntityBase",
                "D) Create a new class extending EntityBase",
            ],
            "gold_index": 0,
            "reference": "A",
            "context": "Drupal 10 field system",
            "checklist": [
                "Must mention FieldItemBase class",
                "Should reference proper namespace",
                "Must explain field schema definition",
            ],
        },
        {
            "id": "drupal_quiz_002",
            "prompt": "Which hook is called when a Drupal node is saved?",
            "choices": [
                "A) hook_node_insert()",
                "B) hook_node_presave()",
                "C) hook_entity_presave()",
                "D) All of the above",
            ],
            "gold_index": 3,
            "reference": "D",
            "context": "Drupal hooks and entity lifecycle",
            "checklist": [
                "Must identify all relevant hooks",
                "Should explain hook execution order",
                "Must understand entity vs node hooks",
            ],
        },
    ],
    "code_generation": [
        {
            "id": "drupal_code_001",
            "prompt": (
                "Create a Drupal 10 module that adds a custom block with "
                "configurable text."
            ),
            "reference": (
                "<?php\n\n"
                "namespace Drupal\\mymodule\\Plugin\\Block;\n\n"
                "use Drupal\\Core\\Block\\BlockBase;\n"
                "use Drupal\\Core\\Form\\FormStateInterface;\n\n"
                "/**\n"
                " * Provides a custom text block.\n"
                " *\n"
                " * @Block(\n"
                ' *   id = "custom_text_block",\n'
                ' *   admin_label = @Translation("Custom Text Block")\n'
                " * )\n"
                " */\n"
                "class CustomTextBlock extends BlockBase {\n\n"
                "  public function defaultConfiguration() {\n"
                "    return [\n"
                "      'custom_text' => '',\n"
                "    ] + parent::defaultConfiguration();\n"
                "  }\n\n"
                "  public function blockForm($form, FormStateInterface $form_state) {\n"
                "    $form['custom_text'] = [\n"
                "      '#type' => 'textarea',\n"
                "      '#title' => $this->t('Custom Text'),\n"
                "      '#default_value' => $this->configuration['custom_text'],\n"
                "    ];\n"
                "    return $form;\n"
                "  }\n\n"
                "  public function blockSubmit($form, "
                "FormStateInterface $form_state) {\n"
                "    $this->configuration['custom_text'] = "
                "$form_state->getValue('custom_text');\n"
                "  }\n\n"
                "  public function build() {\n"
                "    return [\n"
                "      '#markup' => $this->configuration['custom_text'],\n"
                "    ];\n"
                "  }\n"
                "}"
            ),
            "context": "Drupal 10 block plugin system",
            "checklist": [
                "Must extend BlockBase class",
                "Must implement proper @Block annotation",
                "Must implement defaultConfiguration method",
                "Must implement blockForm method for configuration",
                "Must implement blockSubmit method",
                "Must implement build method",
                "Should use proper namespace",
                "Should include docblock comments",
            ],
        },
        {
            "id": "drupal_code_002",
            "prompt": (
                "Create a Drupal 10 custom entity with title and " "description fields."
            ),
            "reference": (
                "<?php\n\n"
                "namespace Drupal\\mymodule\\Entity;\n\n"
                "use Drupal\\Core\\Entity\\ContentEntityBase;\n"
                "use Drupal\\Core\\Entity\\EntityTypeInterface;\n"
                "use Drupal\\Core\\Field\\BaseFieldDefinition;\n\n"
                "/**\n"
                " * Defines the Custom entity.\n"
                " *\n"
                " * @ContentEntityType(\n"
                ' *   id = "custom_entity",\n'
                ' *   label = @Translation("Custom Entity"),\n'
                ' *   base_table = "custom_entity",\n'
                " *   entity_keys = {\n"
                ' *     "id" = "id",\n'
                ' *     "label" = "title",\n'
                " *   },\n"
                " * )\n"
                " */\n"
                "class CustomEntity extends ContentEntityBase {\n\n"
                "  public static function baseFieldDefinitions("
                "EntityTypeInterface $entity_type) {\n"
                "    $fields = parent::baseFieldDefinitions($entity_type);\n\n"
                "    $fields['title'] = BaseFieldDefinition::create('string')\n"
                "      ->setLabel(t('Title'))\n"
                "      ->setRequired(TRUE)\n"
                "      ->setSettings([\n"
                "        'max_length' => 255,\n"
                "      ]);\n\n"
                "    $fields['description'] = "
                "BaseFieldDefinition::create('text_long')\n"
                "      ->setLabel(t('Description'))\n"
                "      ->setRequired(FALSE);\n\n"
                "    return $fields;\n"
                "  }\n"
                "}"
            ),
            "context": "Drupal 10 content entity system",
            "checklist": [
                "Must extend ContentEntityBase",
                "Must implement proper @ContentEntityType annotation",
                "Must define base_table",
                "Must define entity_keys",
                "Must implement baseFieldDefinitions method",
                "Must define title field as string",
                "Must define description field as text_long",
                "Should use proper field settings",
            ],
        },
    ],
    "bug_fixing": [
        {
            "id": "drupal_bug_001",
            "prompt": (
                "Fix this Drupal hook that's not working:\n\n"
                "```php\n"
                "function mymodule_node_insert($node) {\n"
                "  if ($node->getType() == 'article') {\n"
                "    drupal_set_message('Article created!');\n"
                "  }\n"
                "}\n"
                "```"
            ),
            "reference": (
                "<?php\n\n"
                "function mymodule_node_insert($node) {\n"
                "  if ($node->getType() == 'article') {\n"
                "    \\Drupal::messenger()->addMessage('Article created!');\n"
                "  }\n"
                "}"
            ),
            "context": "Drupal 10 deprecated functions",
            "checklist": [
                "Must replace drupal_set_message() with messenger service",
                "Must use proper Drupal service syntax",
                "Should maintain same functionality",
                "Must work in Drupal 10",
            ],
        },
        {
            "id": "drupal_bug_002",
            "prompt": (
                "Fix this broken Drupal form validation:\n\n"
                "```php\n"
                "function mymodule_form_validate($form, &$form_state) {\n"
                "  $email = $form_state['values']['email'];\n"
                "  if (!valid_email_address($email)) {\n"
                "    form_set_error('email', 'Invalid email');\n"
                "  }\n"
                "}\n"
                "```"
            ),
            "reference": (
                "<?php\n\n"
                "function mymodule_form_validate($form, "
                "FormStateInterface $form_state) {\n"
                "  $email = $form_state->getValue('email');\n"
                "  if (!\\Drupal::service('email.validator')->isValid($email)) {\n"
                "    $form_state->setErrorByName('email', 'Invalid email');\n"
                "  }\n"
                "}"
            ),
            "context": "Drupal 10 form API changes",
            "checklist": [
                "Must use FormStateInterface type hint",
                "Must use getValue() method instead of array access",
                "Must replace valid_email_address() with email validator service",
                "Must use setErrorByName() instead of form_set_error()",
                "Should maintain same validation logic",
            ],
        },
    ],
}


def quiz_accuracy_fn(doc: Doc, model_response: Any) -> float:
    """Calculate accuracy for quiz tasks."""
    if not model_response.text:
        return 0.0

    prediction = model_response.text[0].strip()
    gold_answer = doc.get_golds()[0]

    # Simple exact match for now
    return 1.0 if prediction == gold_answer else 0.0


def code_quality_fn(doc: Doc, model_response: Any) -> float:
    """Evaluate code quality using dynamic checklist from dataset."""
    if not model_response.text or not doc.specific:
        return 0.0

    # Use the dynamic checklist from the specific field
    return checklist_accuracy_fn(doc, model_response)


def bug_fixing_fn(doc: Doc, model_response: Any) -> float:
    """Evaluate bug fix quality using dynamic checklist from dataset."""
    if not model_response.text or not doc.specific:
        return 0.0

    # Use the dynamic checklist from the specific field
    return checklist_accuracy_fn(doc, model_response)


# Define metrics for Drupal tasks
drupal_quiz_metric = SampleLevelMetric(
    metric_name="drupal_quiz_accuracy",
    sample_level_fn=quiz_accuracy_fn,
    category=MetricCategory.GENERATIVE,
    use_case=MetricUseCase.ACCURACY,
    corpus_level_fn="mean",
    higher_is_better=True,
)

drupal_code_metric = SampleLevelMetric(
    metric_name="drupal_code_quality",
    sample_level_fn=code_quality_fn,
    category=MetricCategory.GENERATIVE,
    use_case=MetricUseCase.CODE,
    corpus_level_fn="mean",
    higher_is_better=True,
)

drupal_bug_fixing_metric = SampleLevelMetric(
    metric_name="drupal_bug_fixing",
    sample_level_fn=bug_fixing_fn,
    category=MetricCategory.GENERATIVE,
    use_case=MetricUseCase.CODE,
    corpus_level_fn="mean",
    higher_is_better=True,
)


# Create hardcoded prompt functions that use our sample data
def drupal_quiz_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for hardcoded quiz data."""
    from .prompt_functions import drupal_quiz_prompt

    return drupal_quiz_prompt(line, task_name)


def drupal_code_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for hardcoded code generation data."""
    from .prompt_functions import drupal_code_generation_prompt

    return drupal_code_generation_prompt(line, task_name)


def drupal_bug_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for hardcoded bug fixing data."""
    from .prompt_functions import drupal_bug_fixing_prompt

    return drupal_bug_fixing_prompt(line, task_name)


# Task configurations using hardcoded data
drupal_quiz_task = LightevalTaskConfig(
    name="nichebench_drupal_quiz",
    prompt_function=drupal_quiz_hardcoded_prompt,
    suite=["community"],
    hf_repo="local",  # Use local hardcoded data
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[drupal_quiz_metric],
    generation_size=50,
    stop_sequence=None,
    trust_dataset=True,
)

drupal_code_task = LightevalTaskConfig(
    name="nichebench_drupal_code_generation",
    prompt_function=drupal_code_hardcoded_prompt,
    suite=["community"],
    hf_repo="local",  # Use local hardcoded data
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[drupal_code_metric],
    generation_size=512,
    stop_sequence=["```", "\n\n"],
    trust_dataset=True,
)

drupal_bug_task = LightevalTaskConfig(
    name="nichebench_drupal_bug_fixing",
    prompt_function=drupal_bug_hardcoded_prompt,
    suite=["community"],
    hf_repo="local",  # Use local hardcoded data
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[drupal_bug_fixing_metric],
    generation_size=512,
    stop_sequence=["```", "\n\n"],
    trust_dataset=True,
)

# Export tasks for LightEval discovery
TASKS_TABLE = [
    drupal_quiz_task,
    drupal_code_task,
    drupal_bug_task,
]
