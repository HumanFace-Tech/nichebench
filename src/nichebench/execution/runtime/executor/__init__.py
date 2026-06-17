"""Runtime executor components.

Package owns the runtime-task execution mixin and its orchestration flow.
Split from the original executor.py mega-method in behavior-preserving steps.

Modules:
    mixin: RuntimeExecutionMixin public facade (all helper methods).
    flow: High-level execute_runtime_test orchestration.
    stages: Config/workspace/bootstrap/check/judge stage helpers.
    review_nudge: Second-pass review nudge logic.
    failure_shortcut: Catastrophic failure handling.
    cleanup: Final trace/artifact cleanup handling.

Public exports:
    RuntimeExecutionMixin (via this package's __init__.py).

Operational constraints:
    - Preserves RuntimeExecutionMixin.execute_runtime_test signature.
    - Preserves trace stage names exactly.
    - Preserves metadata keys exactly.
    - Preserves review nudge semantics.
"""

# Import flow.py to trigger execute_runtime_test attachment to the mixin.
# This must happen after the mixin is defined so the method can be attached.
from nichebench.execution.runtime.executor import flow  # noqa: F401
from nichebench.execution.runtime.executor.mixin import RuntimeExecutionMixin

__all__ = ["RuntimeExecutionMixin"]
