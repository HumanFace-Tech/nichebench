"""Test result encapsulation.

Ownership
--------
A |TestResult| instance is owned by the **executor** that creates it (see
``nichebench.core.executor``).  The executor populates all fields during or
immediately after a task run and is the sole authority for mutating once
created.

Non-ownership
-------------
All other callers (``scoring``, ``datamodel``, CLI formatters, reporters, etc.)
must treat a |TestResult| as **immutable after construction**.  No external
component should assign to or otherwise modify its attributes.  If you need a
modified view of a result, call ``to_dict()`` and work on the returned dict.

Caller expectations
-------------------
* Fields are always populated — even on error.  On failure ``passed`` is
  ``False`` and ``error`` contains a string reason; ``mut_output`` may be
  empty.
* ``trial`` / ``trials_total`` are set to ``1/1`` when the run is not part of a
  multi-trial sweep.
* ``runtime_artifacts`` carries only *metadata keys* (paths / identifiers);
  the raw artifact payload is stored on disk by the executor and is **not**
  embedded in this object.

Serialization constraints
-------------------------
``to_dict()`` is the only public serialisation entry point.  It guarantees a
stable, flat ``dict`` suitable for JSON serialisation and for merging into
result reports.  Callers that need a different shape should not patch
``to_dict()``; instead transform the dict after the call.
"""

import math
from typing import Any, Dict, Optional

from nichebench.core.datamodel import TestCaseSpec
from nichebench.metrics.types import JUDGE_ERROR_CONFIG


def _clamp_overall_score(value: Any) -> Optional[float]:
    """Return ``value`` clamped to ``[0, 1]``, or ``None`` when unusable.

    Booleans and non-finite numbers are rejected; out-of-range numbers are
    clamped (temporary behaviour until the v2 plan recomputes the score
    from the judge criteria).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score):
        return None
    return max(0.0, min(1.0, score))


class TestResult:
    """Encapsulates a single test result.

    Attributes
    ----------
    framework : str
        Canonical framework name (e.g. ``"drupal_runtime"``).
    category : str
        Task category within the framework (e.g. ``"runtime"``).
    test_case : TestCaseSpec
        The test-case specification that generated this result.  Held by
        reference; the |TestResult| does not own or copy it.
    mut_model : str
        Model-under-test identifier in ``provider/model`` notation.
    judge_model : str
        Judge model identifier in ``provider/model`` notation.  Every
        category — ``quiz`` included — is judged by an LLM, so this is the
        configured judge model.
    judge_method : str
        How the result was scored — ``"llm"`` for every category the
        harness scores.
    user_input : str
        Raw prompt or input string presented to the MUT.
    mut_output : str
        Raw response string returned by the MUT.
    judge_output : Dict[str, Any]
        Structured judge response.  Keys vary by judge adapter.  For the
        ``quiz`` category the shape is
        ``{pass, selected, score, explanation, method, parse_reason,
        parser, ...}`` with a binary ``score`` recomputed from the judge's
        selected letter (plus ``error_kind``/``attempts`` and null scores
        when the judge failed).
    passed : bool
        Overall pass/fail verdict.  ``False`` if any critical check failed or
        the judge returned a score below the configured threshold.
    error : Optional[str]
        Reason string when ``passed`` is ``False`` due to an exception or
        infrastructure failure.  ``None`` when the run completed normally.
    runtime_artifacts : Dict[str, Any]
        Metadata map of artifact keys to on-disk paths produced by a runtime
        task.  Only keys are stored here; raw payloads live on disk.
    effective_profile : Optional[str]
        Name of the resolved evaluation profile (from
        ``nichebench.core.profiles``) or ``None`` if no profile was applied.
    resolved_flags : Dict[str, bool]
        Final boolean flag values after environment / profile resolution.
    trial : int
        Current trial number (1-indexed) within a multi-trial run.
    trials_total : int
        Total number of trials in the sweep.  Always ``1`` for single runs.
    """

    def __init__(
        self,
        framework: str,
        category: str,
        test_case: TestCaseSpec,
        mut_model: str,
        judge_model: str,
        judge_method: str = "llm",
    ):
        self.framework = framework
        self.category = category
        self.test_case = test_case
        self.mut_model = mut_model
        self.judge_model = judge_model
        self.judge_method = judge_method
        self.user_input = ""
        self.mut_output = ""
        self.judge_output: Dict[str, Any] = {}
        self.passed = False
        self.error: Optional[str] = None
        self.error_kind: Optional[str] = None
        self.judge_attempts: int = 1
        self.sanity_findings: list[dict] = []
        self.runtime_artifacts: Dict[str, Any] = {}
        self.effective_profile: Optional[str] = None
        self.resolved_flags: Dict[str, bool] = {}
        self.trial: int = 1
        self.trials_total: int = 1

    def _is_scoring_error(self) -> bool:
        """Return ``True`` when the row carries a judge or config error kind.

        Such rows say nothing about the model: the judge was unreachable or
        produced an unusable verdict, or the harness itself failed.  Their
        scores are serialized as ``None`` — never as a fabricated zero.
        """
        kind = self.error_kind
        if not isinstance(kind, str) or not kind:
            return False
        return kind.startswith("judge_") or kind == JUDGE_ERROR_CONFIG

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization.

        Contract (stable keys)
        ----------------------
        The following keys are always present in the returned dict:

        ``framework``, ``category``, ``test_id``, ``summary``, ``mut_model``,
        ``judge_model``, ``judge_method``, ``input``, ``output``, ``gold``,
        ``judge_output``, ``pass``, ``trial``, ``trials_total``,
        ``judge_attempts``

        The following keys are present only when their source data is set:

        ``deterministic_score``, ``judge_score``, ``final_score``
            Extracted from ``judge_output`` when it is a ``dict``.  For the
            ``quiz`` category the binary verdict *is* the score:
            ``judge_score`` and ``final_score`` carry the binary pass/fail
            value derived from the ``passed`` flag and
            ``deterministic_score`` is ``None``.  For ``code_generation``,
            ``bug_fixing`` and ``code_agent`` a numeric judge
            ``overall_score`` (clamped to ``[0, 1]``) becomes both
            ``judge_score`` and ``final_score``, with
            ``deterministic_score`` left ``None``.  A ``quiz`` row whose
            ``error_kind`` is a judge error (``judge_*``) or
            ``config_error`` carries ``None`` for all three score keys —
            such a row says nothing about the model, so it never gets a
            fabricated zero.
        ``parse_reason``
            Parse-failure reason from ``judge_output``.  Always present for
            ``quiz`` (``None`` when the judge output carries none); for the
            other categories it is present only when the judge output
            carries the key.
        ``error_kind``
            Present only when the judge failed with a machine-readable
            error kind (see ``nichebench.metrics.types.JUDGE_ERROR_*``).
        ``sanity_findings``
            Present only when structural sanity checks produced findings.
        ``base_branch``, ``resolved_sha``
            Present when the |TestCaseSpec| carries those attributes.
        ``dataset_version``
            Present when the |TestCaseSpec| carries a dataset version.
        ``artifact_keys``
            List of keys from ``runtime_artifacts``; raw payload is on disk.
        ``effective_profile``
            Present when not ``None``.
        ``resolved_flags``
            Present when not empty.

        Stability expectation
        ---------------------
        The key set above is part of the public contract and will not be
        removed or renamed in a minor release.  Additional keys may be added
        in future versions; callers must ignore unknown keys they do not
        recognise.

        ``judge_output`` is stored as a nested ``dict``; callers that need a
        fully flattened representation must transform the dict after the call.
        """
        d = {
            "framework": self.framework,
            "category": self.category,
            "test_id": self.test_case.id,
            "summary": getattr(self.test_case, "summary", "") or self.test_case.raw.get("summary", ""),
            "mut_model": self.mut_model,
            "judge_model": self.judge_model,
            "judge_method": self.judge_method,
            "input": self.user_input,
            "output": self.mut_output,
            "gold": self.test_case.correct_choice or getattr(self.test_case, "checklist", []),
            "judge_output": self.judge_output,
            "pass": self.passed,
        }

        d["trial"] = self.trial
        d["trials_total"] = self.trials_total

        if isinstance(self.judge_output, dict):
            if self.category == "quiz":
                # Quiz is LLM-judged: the judge's selected letter decides the
                # binary verdict.  ``passed`` is kept in sync with that
                # verdict by the judge runner, so a stray non-binary
                # ``score`` in the output must not desync the row from
                # ``avg_score`` (which is the pass rate).
                if self._is_scoring_error():
                    # A judge/config failure is not a model result: the row
                    # keeps its error metadata and carries null scores
                    # instead of a fabricated zero.
                    d["pass"] = False
                    d["deterministic_score"] = None
                    d["judge_score"] = None
                    d["final_score"] = None
                else:
                    score = 1.0 if self.passed else 0.0
                    d["deterministic_score"] = None
                    d["judge_score"] = score
                    d["final_score"] = score
                d["parse_reason"] = self.judge_output.get("parse_reason")
            else:
                d["deterministic_score"] = self.judge_output.get("deterministic_score")
                d["judge_score"] = self.judge_output.get("judge_score")
                d["final_score"] = self.judge_output.get("hybrid_score")
                if "parse_reason" in self.judge_output:
                    d["parse_reason"] = self.judge_output["parse_reason"]
                if self.category in ("code_generation", "bug_fixing", "code_agent"):
                    # Classic LLM-judged rows carry only the judge's
                    # ``overall_score``; it is both the judge score and the
                    # final score (there is no deterministic component).
                    overall = _clamp_overall_score(self.judge_output.get("overall_score"))
                    if overall is not None:
                        d["deterministic_score"] = None
                        d["judge_score"] = overall
                        d["final_score"] = overall
        elif self.category == "quiz":
            d["deterministic_score"] = None
            if self._is_scoring_error():
                d["pass"] = False
                d["judge_score"] = None
                d["final_score"] = None
            else:
                d["judge_score"] = 1.0 if self.passed else 0.0
                d["final_score"] = 1.0 if self.passed else 0.0
            d["parse_reason"] = None

        d["judge_attempts"] = self.judge_attempts
        if self.error_kind is not None:
            d["error_kind"] = self.error_kind
        if self.sanity_findings:
            d["sanity_findings"] = list(self.sanity_findings)

        # Task 2.2: Persist base_branch and resolved_sha
        if hasattr(self.test_case, "base_branch") and self.test_case.base_branch:
            d["base_branch"] = self.test_case.base_branch
        if hasattr(self.test_case, "resolved_sha") and self.test_case.resolved_sha:
            d["resolved_sha"] = self.test_case.resolved_sha

        dataset_version = getattr(self.test_case, "dataset_version", None)
        if dataset_version:
            d["dataset_version"] = dataset_version

        # Task 3.4 & 5.3: Runtime artifacts metadata (keys only — raw payload lives on disk)
        if hasattr(self, "runtime_artifacts") and self.runtime_artifacts:
            d["artifact_keys"] = list(self.runtime_artifacts.keys())
        if hasattr(self, "effective_profile"):
            d["effective_profile"] = getattr(self, "effective_profile")
        if hasattr(self, "resolved_flags"):
            d["resolved_flags"] = getattr(self, "resolved_flags")

        return d
