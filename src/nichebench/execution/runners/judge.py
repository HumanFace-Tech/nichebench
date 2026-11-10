"""Judge runner: evaluates MUT output per task category.

Responsibilities
================
* Accept a :class:`TestCaseSpec` and the MUT output, then route to the
  appropriate scoring path (quiz, code generation, bug fixing, or runtime).
* **Quiz is scored by the LLM judge**
  (:meth:`nichebench.providers.litellm_judge.LiteLLMJudge.score_quiz`),
  with the deterministic parser (:func:`nichebench.metrics.quiz.score_quiz`)
  kept only as an audit cross-check (``parser`` on the judge output).
  Malformed quiz *task data* (missing/out-of-range choices or gold) fails
  closed with a ``validation`` verdict before any judge call.
* **Code generation and bug fixing** are scored by calling
  the LLM judge
  (:meth:`nichebench.providers.litellm_judge.LiteLLMJudge.score_code_generation`
  / :meth:`~nichebench.providers.litellm_judge.LiteLLMJudge.score_bug_fixing`)
  directly — no metric-wrapper layer in between.
* For runtime tasks, assemble a human-readable *artifact summary* from
  the raw runtime output bundle (``final.diff``, ``run.log``,
  ``checks.json``, ``phpcs.json``, ``phpstan.json``) so the judge LLM
  can reason over the full execution trace.
* Return a structured verdict (``judge_output`` dict) and a boolean
  ``passed`` flag that the scoring layer combines with deterministic
  checks.  Unknown categories fail closed — they are never auto-passed.

Key boundaries
=============
* This runner knows about category (quiz / code_generation / bug_fixing /
  runtime) but does **not** implement scoring weights — those belong to
  :mod:`nichebench.core.scoring`.
* The actual judge LLM calls are delegated to :class:`LiteLLMJudge`.
  Changing the judge provider means updating that module, not this one.
* Artifact summarisation is lossy (diff/log truncation at character
  limits); this is intentional to keep prompt size bounded for the judge.
"""

from dataclasses import asdict
from typing import Any, Dict, Optional, Tuple

from nichebench.config.nichebench_config import get_config
from nichebench.config.settings import resolve_judge_threshold
from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.quiz_text import normalize_choice
from nichebench.metrics.quiz import MAX_NUM_CHOICES, MIN_NUM_CHOICES, score_quiz
from nichebench.metrics.sanity import structural_checks
from nichebench.metrics.types import SanityFinding
from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import JudgeError, LiteLLMJudge

# Verdict returned when a quiz task has no usable choices.  Such a task can
# never be scored, so it fails closed without a judge call instead of raising.
# ``method`` is ``"validation"``: this is a data defect, not a model verdict.
_INVALID_QUIZ_OUTPUT: Dict[str, Any] = {
    "pass": False,
    "selected": None,
    "score": 0.0,
    "explanation": "Quiz task is missing choices",
    "method": "validation",
    "parse_reason": "invalid_task",
}

# Verdict returned when a quiz task offers usable choices but its
# ``correct_choice`` is missing, not a string, or out of range.  This is a
# different data defect than a missing-choice task, so it gets its own
# machine-readable ``parse_reason``.
_INVALID_QUIZ_GOLD_OUTPUT: Dict[str, Any] = {
    "pass": False,
    "selected": None,
    "score": 0.0,
    "explanation": "Quiz task is missing or invalid correct_choice",
    "method": "validation",
    "parse_reason": "invalid_gold",
}

# Verdict returned when the choice count is outside the scorable range.
_INVALID_QUIZ_CHOICE_COUNT_OUTPUT: Dict[str, Any] = {
    "pass": False,
    "selected": None,
    "score": 0.0,
    "explanation": f"Quiz task has an unsupported number of choices (expected {MIN_NUM_CHOICES}-{MAX_NUM_CHOICES})",
    "method": "validation",
    "parse_reason": "invalid_task",
}

# Verdict returned for a category the judge runner does not know.  The
# runner fails closed: an unknown category must never be auto-passed.
_UNKNOWN_CATEGORY_OUTPUT: Dict[str, Any] = {
    "pass": False,
    "score": 0.0,
    "overall_score": 0.0,
    "error": "unknown category",
    "error_kind": "config_error",
    "explanation": "Unknown category - failing closed",
}


def _is_valid_gold(gold: str, num_choices: int) -> bool:
    """Return ``True`` when ``gold`` is a letter offered by the quiz."""
    normalized = gold.strip().upper()
    if len(normalized) != 1 or not normalized.isalpha():
        return False
    return normalized in {chr(ord("A") + index) for index in range(num_choices)}


def _build_runtime_task_description(raw: Dict[str, Any]) -> str:
    """Pull title + structured description fields into a single readable block.

    Used for the ``runtime`` category to give the judge LLM a compact
    task overview when it cannot see the original ``TASK.md``.
    """
    parts: list[str] = []
    title = str(raw.get("title", "")).strip()
    if title:
        parts.append(f"Task: {title}")
    desc = raw.get("description_structured", {})
    if isinstance(desc, dict):
        bg = str(desc.get("background", "")).strip()
        if bg:
            parts.append(f"Background:\n{bg}")
        ac = desc.get("acceptance_criteria", [])
        if ac and isinstance(ac, list):
            parts.append("Acceptance Criteria:\n" + "\n".join(f"- {c}" for c in ac))
    return "\n\n".join(parts) if parts else "(no task description available)"


def _build_runtime_artifact_summary(
    artifacts: Dict[str, Any],
    max_diff_chars: int = 8000,
    max_log_chars: int = 4000,
) -> str:
    """Transform the raw artifact bundle into a compact text summary for the judge LLM.

    The summary includes (in order): git diff, agent run log, deterministic
    check results, PHPCS summary, and PHPStan summary.  Each section is
    truncated at the respective character limit to keep the judge prompt
    within size bounds.

    Args:
        artifacts: runtime output bundle — keys include ``final.diff``,
            ``run.log``, ``checks.json``, ``phpcs.json``, ``phpstan.json``.
        max_diff_chars: truncation threshold for the diff section.
        max_log_chars: truncation threshold for the log section (shows the
            *last* portion so recent activity is preserved).

    Returns:
        A multi-section text string, or ``"(no artifacts available)"`` if
        the bundle is empty.
    """
    parts: list[str] = []

    diff = artifacts.get("final.diff", "")
    if diff and isinstance(diff, str):
        if len(diff) > max_diff_chars:
            diff = diff[:max_diff_chars] + f"\n... [truncated at {max_diff_chars} chars]"
        parts.append(f"=== GIT DIFF (final.diff) ===\n{diff}")

    log = artifacts.get("run.log", "")
    if log and isinstance(log, str):
        if len(log) > max_log_chars:
            log = log[-max_log_chars:] + f"\n... [showing last {max_log_chars} chars]"
        parts.append(f"=== AGENT RUN LOG (run.log) ===\n{log}")

    checks = artifacts.get("checks.json", {})
    if checks and isinstance(checks, dict):
        det = checks.get("deterministic", [])
        if det and isinstance(det, list):
            lines = [
                f"  [{'PASS' if c.get('passed') else 'FAIL'}] " f"{c.get('name', '')}: {c.get('message', '')}"
                for c in det
            ]
            parts.append("=== DETERMINISTIC CHECKS ===\n" + "\n".join(lines))

    phpcs = artifacts.get("phpcs.json", {})
    if phpcs and isinstance(phpcs, dict):
        totals = phpcs.get("totals", {})
        parts.append(
            f"=== PHPCS ===\n"
            f"errors={totals.get('errors', '?')}, "
            f"warnings={totals.get('warnings', '?')}, "
            f"fixable={totals.get('fixable', '?')}"
        )

    phpstan = artifacts.get("phpstan.json", {})
    if phpstan and isinstance(phpstan, dict):
        totals = phpstan.get("totals", {})
        parts.append(
            f"=== PHPSTAN ===\n" f"error={totals.get('error', '?')}, " f"maybe_error={totals.get('maybe_error', '?')}"
        )

    return "\n\n".join(parts) if parts else "(no artifacts available)"


class JudgeRunner:
    """Coordinates evaluation for a single test case.

    Each instance owns a :class:`LiteLLMClient` (shared with the MUT
    runner at the executor level) and a :class:`LiteLLMJudge` adapter.

    Main entry point is :meth:`evaluate_test`, which routes by
    ``category``:
      * ``quiz`` — LLM judge over the question/choices/gold/answer, with
        the deterministic parser kept as an audit cross-check; malformed
        task data fails closed without a judge call.
      * ``code_generation`` / ``bug_fixing`` — direct LLM
        judge calls through :class:`LiteLLMJudge`.
      * ``runtime`` — LLM judge over a summarised artifact bundle.
      * anything else — a fail-closed verdict (never an
        auto-pass).  The removed ``code_agent`` category lands here and is
        never scored as ``code_generation``.

    For the ``runtime`` category, the runner builds an *artifact summary*
    (see :func:`_build_runtime_artifact_summary`) instead of passing
    raw MUT output directly, because the judge must evaluate a full
    agent run rather than a single-turn completion.
    """

    def __init__(
        self,
        model_str: str,
        model_config: Dict[str, Any],
        timeout: int,
        retry_attempts: int,
        retry_delay: int,
        evaluation_config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the judge runner.

        Args:
            model_str: Judge model string (``provider/model``).
            model_config: Judge model configuration dict.
            timeout: Network timeout in seconds.
            retry_attempts: Number of retries for transient failures.
            retry_delay: Delay between retries in seconds.
            evaluation_config: Effective evaluation block for this run (the
                top-level ``evaluation`` config merged with the active
                profile).  Read lazily at scoring time, so a config applied
                after construction — ``run.py`` updates the executor's copy
                with the profile values — is still honoured.
        """
        self.model_str = model_str
        self.model_config = model_config
        self.evaluation_config = evaluation_config
        self.client = LiteLLMClient(timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay)
        self.judge = LiteLLMJudge(client=self.client)

    def _judge_threshold(self) -> float:
        """Effective pass threshold for ``overall_score``.

        Comes from ``evaluation.judge_threshold`` on the effective evaluation
        config when the run carries one, otherwise from the environment-backed
        default (``NICHEBENCH_JUDGE_THRESHOLD``, ``0.7``).  The attribute read
        is defensive: test doubles build the runner without ``__init__``.
        """
        return resolve_judge_threshold(getattr(self, "evaluation_config", None))

    def _code_sanity_enabled(self) -> bool:
        """Read ``evaluation.code_sanity_checks`` (default ``True``).

        The runner's *effective* evaluation block wins — the top-level
        ``evaluation`` config merged with the active profile, the very dict
        ``run.py`` updates with the profile values before any test executes —
        so a profile can switch the sanity layer off.  Only when the runner
        carries no block (test doubles build it without ``__init__``) does
        the read fall back to the global config, unprofiled.  Read lazily at
        scoring time so a config applied after construction is still honoured.
        When disabled, the whole structural sanity layer is skipped for
        code/bug outputs.
        """
        evaluation_config = getattr(self, "evaluation_config", None)
        if not isinstance(evaluation_config, dict):
            evaluation_config = get_config().get_evaluation_config()
        return bool(evaluation_config.get("code_sanity_checks", True))

    def evaluate_test(
        self,
        test_case: TestCaseSpec,
        category: str,
        user_input: str,
        mut_output: str,
        judge_system_prompt: Optional[str],
        runtime_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Evaluate MUT output.

        Args:
            test_case: Test case specification
            category: Test category (quiz, code_generation, bug_fixing, runtime)
            user_input: User input/prompt
            mut_output: Model output to evaluate
            judge_system_prompt: Optional system prompt for judge
            runtime_artifacts: Optional dict of runtime artifacts for category='runtime'

        Returns:
            Tuple of (judge_output, passed)
        """
        if category == "quiz":
            return self._evaluate_quiz(test_case, user_input, mut_output, judge_system_prompt)

        if category in ("code_generation", "bug_fixing"):
            return self._evaluate_with_judge(test_case, category, user_input, mut_output, judge_system_prompt)

        if category == "runtime":
            return self._evaluate_runtime(test_case, user_input, mut_output, runtime_artifacts)

        # Unknown category - fail closed.  Auto-passing an unrecognised
        # category would silently turn a configuration error into a score.
        judge_output: Dict[str, Any] = dict(_UNKNOWN_CATEGORY_OUTPUT)
        return judge_output, False

    # -- quiz ----------------------------------------------------------------

    def _evaluate_quiz(
        self,
        test_case: TestCaseSpec,
        user_input: str,
        mut_output: str,
        judge_system_prompt: Optional[str],
    ) -> Tuple[Dict[str, Any], bool]:
        """Score a quiz answer with the LLM judge, cross-checked by the parser.

        Task data is validated first: missing/blank gold, missing choices,
        an out-of-range gold letter and unsupported choice counts are data
        defects, so they fail closed with a ``validation`` verdict and
        **no** judge call.

        On the happy path the judge's ``selected`` letter is authoritative
        (see :meth:`LiteLLMJudge.score_quiz`): ``pass`` / ``score`` are
        recomputed from ``selected == gold``, the judge's own boolean is
        kept as ``judge_pass`` (``judge_pass_mismatch`` flags a
        disagreement), and the deterministic parser runs on the same answer
        purely as an audit cross-check under ``parser`` — it never decides
        the score.

        Returns ``(judge_output, passed)``.  When the judge fails after its
        retries, ``judge_output`` carries the machine-readable
        ``error_kind`` / ``attempts`` and null scores — never a fabricated
        score — and ``passed`` is ``False``.
        """
        choices = [normalize_choice(choice) for choice in (test_case.choices or [])]
        gold = test_case.correct_choice

        if len(choices) < MIN_NUM_CHOICES:
            return dict(_INVALID_QUIZ_OUTPUT), False

        if len(choices) > MAX_NUM_CHOICES:
            return dict(_INVALID_QUIZ_CHOICE_COUNT_OUTPUT), False

        if not isinstance(gold, str) or not gold.strip() or not _is_valid_gold(gold, len(choices)):
            return dict(_INVALID_QUIZ_GOLD_OUTPUT), False

        gold_letter = gold.strip().upper()

        try:
            verdict = self.judge.score_quiz(
                question=user_input,
                choices=choices,
                gold=gold_letter,
                candidate=mut_output,
                model=self.model_str,
                model_params=self.model_config.get("parameters", {}),
                system_prompt=judge_system_prompt,
                judge_notes=test_case.raw.get("judge_notes"),
            )
        except JudgeError as exc:
            return (
                {
                    "error_kind": exc.kind,
                    "error": str(exc),
                    "attempts": exc.attempts,
                    "score": None,
                    "overall_score": None,
                },
                False,
            )

        # Audit-only cross-check: the deterministic parser runs on the same
        # answer and reports whether it agrees with the judge's letter.
        parsed = score_quiz(mut_output, gold_letter, len(choices))
        parser_selected = parsed.selected

        judge_output: Dict[str, Any] = dict(verdict)
        judge_output["method"] = "llm"
        judge_output["parse_reason"] = parsed.parse_reason
        judge_output["parser"] = {
            "selected": parser_selected,
            "parse_reason": parsed.parse_reason,
            "agrees": (parser_selected == verdict["selected"]) if parser_selected is not None else None,
        }
        return judge_output, bool(verdict["pass"])

    # -- code generation / bug fixing ---------------------------------------

    def _evaluate_with_judge(
        self,
        test_case: TestCaseSpec,
        category: str,
        user_input: str,
        mut_output: str,
        judge_system_prompt: Optional[str],
    ) -> Tuple[Dict[str, Any], bool]:
        """Score code/bug output with a direct :class:`LiteLLMJudge` call.

        A lean structural sanity pass runs first (unless
        ``evaluation.code_sanity_checks`` is ``False``): a hard finding
        (no code at all / obviously truncated) returns a zero-score verdict
        without calling the judge, and every finding is attached to the
        successful judge output as ``sanity_findings``.

        Returns ``(judge_output, passed)``.  ``passed`` compares
        ``overall_score`` against the effective ``evaluation.judge_threshold``
        (see :meth:`_judge_threshold`).  When the judge fails after
        its retries, ``judge_output`` carries the machine-readable
        ``error_kind`` / ``attempts`` plus ``judge_score``/``final_score``
        set to ``None`` — never a fabricated score — and ``passed`` is
        ``False``.
        """
        model_params: Dict[str, Any] = self.model_config.get("parameters", {})
        checklist = list(test_case.checklist or [])
        judge_notes = test_case.raw.get("judge_notes")

        sanity_enabled = self._code_sanity_enabled()
        sanity_findings: list[SanityFinding] = []
        if sanity_enabled:
            sanity_findings = structural_checks(mut_output)
            hard = next((finding for finding in sanity_findings if finding.severity == "hard"), None)
            if hard is not None:
                # No code / truncated response: fail closed without paying
                # for a judge call.  The model output itself stays on the
                # result for forensics.
                return (
                    {
                        "error_kind": hard.check,
                        "sanity_findings": [asdict(finding) for finding in sanity_findings],
                        "overall_score": 0.0,
                        "judge_score": 0.0,
                        "final_score": 0.0,
                        "summary": hard.message,
                    },
                    False,
                )

        judge_output: Dict[str, Any]
        try:
            if category == "bug_fixing":
                judge_output = self.judge.score_bug_fixing(
                    bug_description=user_input,
                    proposed_fix=mut_output,
                    checklist=checklist,
                    model=self.model_str,
                    model_params=model_params,
                    system_prompt=judge_system_prompt,
                    judge_notes=judge_notes,
                )
            else:
                judge_output = self.judge.score_code_generation(
                    prompt=user_input,
                    generated_code=mut_output,
                    checklist=checklist,
                    model=self.model_str,
                    model_params=model_params,
                    system_prompt=judge_system_prompt,
                    judge_notes=judge_notes,
                )
        except JudgeError as exc:
            return (
                {
                    "error_kind": exc.kind,
                    "error": str(exc),
                    "attempts": exc.attempts,
                    "judge_score": None,
                    "final_score": None,
                },
                False,
            )

        try:
            overall_score = float(judge_output.get("overall_score", 0.0))
        except (TypeError, ValueError):
            overall_score = 0.0
        passed = overall_score >= self._judge_threshold()

        if sanity_enabled:
            judge_output["sanity_findings"] = [asdict(finding) for finding in sanity_findings]

        return judge_output, passed

    # -- runtime -------------------------------------------------------------

    def _evaluate_runtime(
        self,
        test_case: TestCaseSpec,
        user_input: str,
        mut_output: str,
        runtime_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Score a runtime task with the LLM judge over the artifact bundle."""
        llm_judge_config = test_case.raw.get("llm_judge", {})
        checklist = llm_judge_config.get("checklist", [])

        if not checklist:
            # No checklist means perfect score by default
            judge_output: Dict[str, Any] = {
                "overall_score": 1.0,
                "criteria": [],
                "summary": "No checklist - passing by default",
            }
            return judge_output, True

        # The judge sees the task description and the artifact summary
        # instead of the raw MUT prompt / output.
        task_description = user_input
        artifact_summary = mut_output
        if runtime_artifacts:
            task_description = _build_runtime_task_description(test_case.raw)
            artifact_summary = _build_runtime_artifact_summary(runtime_artifacts)

        judge_output = self.judge.score_runtime(
            task_description=str(task_description),
            artifact_summary=str(artifact_summary),
            checklist_items=checklist,
            model=self.model_str,
            model_params=self.model_config.get("parameters", {}),
            system_prompt=llm_judge_config.get("model_role", "Be a fair evaluator."),
        )

        overall_score = float(judge_output.get("overall_score", 0.0))
        passed = overall_score >= 0.5  # Default threshold
        return judge_output, passed
