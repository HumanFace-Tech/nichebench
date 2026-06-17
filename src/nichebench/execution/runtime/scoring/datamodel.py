"""Runtime scoring data model.

Owner: scoring package.
Boundary: defines the public result types produced by check runners and
the scorer. No business logic lives here.

Public types
------------
CheckResult  — outcome of a single deterministic check.
HybridScore  — blended deterministic + judge score with pass/fail decision.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CheckResult:
    """Represents the result of a single deterministic check."""

    name: str
    type: str
    passed: bool
    message: str
    is_critical: bool = True
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HybridScore:
    """Represents a composite score from deterministic and judge components."""

    deterministic_score: float
    judge_score: Optional[float] = None
    final_score: float = 0.0
    check_results: List[CheckResult] = field(default_factory=list)
    passed: bool = False
