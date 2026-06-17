"""Cage container execution package.

**Ownership model**: ``CageExecutionMixin`` (in ``mixin``) is the only public
interface. All other modules are implementation details owned by the mixin.

**Module responsibilities**:
- ``mixin``        — ``CageExecutionMixin`` class + retry orchestration
- ``islands``      — island path setup and prompt file writing
- ``docker_args``  — docker command construction and socket group handling
- ``process_io``  — subprocess launch, stream readers, log capture
- ``watchdog``     — inactivity/stop-idle watchdog logic
- ``retry``        — auto-retry logic for rejected tool attempts
- ``artifacts``    — cage run artifact path discovery

**Container safety constraints**:
- The docker socket is mounted read-only where possible.
- All state (home, xdg, bin) uses run-scoped temp directories.
- Unsafe git operations are blocked by the cage git wrapper.
- No secrets are hardcoded; API keys are injected from host env at runtime.
"""

from __future__ import annotations

# Re-export ``CageExecutionMixin`` at the package level so that
# ``from nichebench.execution.runtime.cage import CageExecutionMixin``
# continues to work after the split.
from nichebench.execution.runtime.cage.mixin import CageExecutionMixin

__all__ = ["CageExecutionMixin"]
