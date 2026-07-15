## 1. Score normalization

- [x] 1.1 Review the existing score-normalization diff against classic summary call paths and retain only generic behavior.
- [x] 1.2 Ensure focused tests cover finite numeric strings, malformed values, booleans, and non-finite values.
- [x] 1.3 Add a dedicated shared unit-test module (`tests/unit/core/test_summary.py`) for `execution.summary.normalize_score`,
      covering finite numeric strings, malformed/non-finite values, booleans, ``None``, out-of-range numerics,
      custom defaults, and numeric-string variants of ``nan``/``inf``/``-inf``.
- [x] 1.4 Restore the Rich report-view package (`src/nichebench/cli/rich_views/reports/`) and the shared
      report-rendering tests (`tests/unit/cli/test_report_rendering.py`); bind every report module to
      `execution.summary.normalize_score` so display and summary aggregation share the same contract,
      and assert the binding with focused tests.

## 2. Continuous integration

- [x] 2.1 Replace stale nested-directory CI commands with root-level Poetry installation and supported quality gates.
- [x] 2.2 Align the CI Python matrix, cache paths, Docker smoke behavior, and coverage policy with declared dependencies.
- [x] 2.3 Validate the workflow-equivalent commands locally.

## 3. Release metadata and documentation

- [x] 3.1 Set package metadata for version `0.2.0` and add an accurate package description.
- [x] 3.2 Add public `0.2.0` release notes/changelog describing runtime capabilities and environment limitations.
- [x] 3.3 Synchronize README, architecture, runtime, and operations docs with current LiteLLM constraints, quality commands, and runtime behavior.
- [x] 3.4 Document the public/private benchmark artifact boundary without adding internal results or local configuration to Git.

## 4. Operational-history cleanup

- [x] 4.1 Replace obsolete `BLOCKERS.md` delivery history with concise current operational notes, retaining only live environment limitations.
- [x] 4.2 Verify OpenSpec/archive references remain accurate after blocker cleanup.

## 5. Runtime smoke boundary

- [x] 5.1 Document runtime smoke as a public harness preflight against an existing bootstrapped task workspace.
- [x] 5.2 Preserve task-pack bootstrap as an external owner responsibility rather than a parent-repository change.

## 6. Release rehearsal

- [x] 6.1 Run unit tests, Ruff, and Mypy on the final tree.
- [x] 6.2 Run CLI and runtime smoke checks where Docker/DDEV prerequisites are available; document any external prerequisite rather than masking it.
- [x] 6.3 Inspect the final diff and ignored-artifact boundary for private endpoints, credentials, raw results, and unrelated changes.
- [x] 6.4 Prepare a selective conventional-commit and `0.2.0` tag plan; do not commit or tag without explicit approval.

## 7. Commit and release sequencing

- [x] 7.1 Update the parent runtime-pack gitlink to its latest owner-provided revision.
- [ ] 7.2 With explicit approval, commit the parent release-hardening changes selectively.
- [ ] 7.3 With explicit approval after CI passes, create and push the `0.2.0` release tag.
