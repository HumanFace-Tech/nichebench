"""Unit tests for scripts/runtime_smoke.py.

These tests do NOT require Docker, DDEV, or a real Drupal installation.
``subprocess.run`` is mocked so each scenario is reproducible.

Coverage:
- ``run_check`` returns ``passed=True`` only when exit code is 0.
- Checks with ``success_marker`` require the marker substring in stdout
  even when exit code is 0 (Drush ``config:status`` drift false-positive).
- Failures (non-zero exit, timeout, OSError) produce a structured failure
  record with a useful ``failure_reason``.
- The CLI emits valid JSON with ``--json`` and exits 0 only when all checks
  pass.
"""
