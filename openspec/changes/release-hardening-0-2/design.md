## Context

The runtime benchmark, diagnostics, cage isolation, and scoring implementations are present,
but the release surface predates them. The project uses Poetry, Ruff, Pytest, and Mypy locally,
while CI references a former nested repository layout and undeclared tools. Raw benchmark data,
provider configuration, credentials, and curated reports are intentionally gitignored because
they are internal research material.

## Goals / Non-Goals

**Goals:**
- Make `0.2.0` installable, verifiable, and accurately documented from a clean checkout.
- Make CI execute the same supported quality gates as local contributors.
- Preserve private benchmark artifacts while documenting a public methodology boundary.
- Keep score normalization generic to every classic task judge rather than coupling it to MiniMax.

**Non-Goals:**
- Publish raw benchmark outputs, private endpoints, credentials, or internal blog material.
- Claim that model benchmark outcomes are release validation.
- Redesign runtime scoring, DDEV isolation, or the benchmark task suite.
- Guarantee live-provider, Docker, or DDEV validation in hosted CI.

## Decisions

### Treat numeric judge values as an integration boundary

Classic summary code will normalize numeric strings and numeric Python values to finite floats,
falling back safely for invalid input. JSON producers are not required to preserve a numeric type,
so strict float-only handling is brittle. Booleans and non-finite values are rejected to avoid
accidental categorization or non-standard JSON summary output. The Rich report-view package
(`cli/rich_views/reports/`) must import the same `execution.summary.normalize_score` helper rather
than calling `float()` directly, so display and aggregation agree on the valid-score contract for
finite numeric strings, malformed/non-finite values, booleans, and out-of-range inputs.

### Align CI to local commands

CI will run `poetry install`, `poetry run pytest -q tests/unit`, `poetry run ruff check src tests`,
and `poetry run mypy src` from the checkout root. Docker/DDEV validation remains an operator-run
release rehearsal because it requires private runtime-pack data and a Docker-capable host.

### Keep benchmark evidence private by design

`results/`, `reports/`, local `nichebench.yml`, and environment files remain ignored. Public docs
will state the benchmark protocol and explain how operators can preserve their own run bundles.
Blog evidence belongs in a separately retained, immutable internal archive. A future sanitized
example may be added only if it contains no private endpoints, credentials, raw prompts, or
unreviewed claims.

### Replace historical blockers with operational guidance

Resolved delivery notes will be archived or removed from `BLOCKERS.md`. Remaining provider
credential and image-access limitations will be stated as environment-dependent operational notes,
not source release blockers.

### Keep task-pack bootstrap at its ownership boundary

The public harness does not modify or guarantee private task-pack bootstrap behavior. Runtime
smoke validates an existing, already-bootstrapped isolated workspace. Task-pack owners are
responsible for their own bootstrap scripts, tests, commits, and submodule revisions.

## Risks / Trade-offs

- [CI does not run live runtime tasks] → Document the runtime smoke procedure and use it in a
  release rehearsal on a Docker/DDEV-capable machine.
- [Private evidence cannot be independently reproduced from Git] → Preserve a dated private run
  archive with commands, sanitized config snapshot, model identifiers, and result bundles.
- [Judge fallback hides malformed provider output] → Categorize malformed output conservatively as
  zero and cover behavior with unit tests.
- [Changing CI can expose unrelated environment assumptions] → Validate the workflow-equivalent
  commands locally before release.
- [Private task-pack bootstrap may be unavailable] → Document runtime smoke as an environment
  preflight against a known bootstrapped workspace; obtain a new pack revision from its owner
  before claiming fresh-pack bootstrap validation.

## Migration Plan

1. Review and retain the score-normalization change with focused tests.
2. Retain the restored Rich report-view package and its shared
   score-normalization/report-rendering tests as a coupled unit with (1); they exist to keep
   display and aggregation aligned on the same coerce-to-finite-float contract.
3. Correct CI, package metadata, docs, and blocker/runbook content.
4. Run release quality gates and smoke checks.
5. Review the final diff for private artifacts and commit in cohesive units.
6. Create `0.2.0` only after CI is green; roll back by reverting the release-hardening commits if
   a public workflow regression is discovered.

## Open Questions

- Whether to publish a sanitized benchmark-output example later; this is explicitly not required
  for `0.2.0`.
