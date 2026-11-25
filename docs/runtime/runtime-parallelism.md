# Runtime parallelism

Runtime tasks use one isolated workspace and DDEV project per test. DDEV can
run these projects concurrently on the same host, but the harness keeps
concurrency at one by default. Raise the host slot limit and both worker
settings to run multiple runtime tasks at once.

## Isolation per test

Each task gets a workspace named `run-{task_id}-{uuid8}` and a DDEV project
named `nb-{task_id}-{uuid8}`. DDEV derives the project URL from that name, for
example `https://nb-drupal-runtime-001-a1b2c3d4.ddev.site`. Each project has its
own database, containers, and volumes. Do not reuse project names or workspace
directories.

## Configure concurrency

The default is one runtime project per host. To run two runtime tasks in one
CLI invocation, set `parallelism` and `runtime_max_workers` to 2, and allow two
host slots:

```yaml
evaluation:
  parallelism: 1
  runtime_max_workers: 1
  runtime_max_concurrent_projects: 1
  runtime_slot_timeout_seconds: 3600
```

For two workers:

```yaml
evaluation:
  parallelism: 2
  runtime_max_workers: 2
  runtime_max_concurrent_projects: 2
  runtime_slot_timeout_seconds: 3600
```

`parallelism` controls execution workers and `runtime_max_workers` caps the
runtime workers used by the CLI. `runtime_max_concurrent_projects` controls
runtime tasks across separate CLI processes for the current user on that host;
the locks live in the user cache directory. A task
waits up to `runtime_slot_timeout_seconds` for a host slot. The default timeout
is 3600 seconds; if no slot opens before the timeout, the run is recorded as a
configuration error.

Host slots apply to all runtime models for the current user on that host, not
separately to each model. The cross-process semaphore uses `flock`. A derived cage image is shared
by tag; its build lock serializes competing builds, then re-checks the image so
only one build is needed for that tag.

## Cleanup and shared resources

Runtime teardown deletes only the task's DDEV project with `ddev delete`. Startup
hygiene removes only stopped, dead, or created `ddev-nb-*` containers and
networks that have no attached containers. It leaves active, paused, and
restarting projects alone, including the current project.

Concurrency does not isolate host capacity. Local model calls on one GPU still
serialize, remote providers share quota and rate limits, and tasks compete for
CPU, RAM, and Docker resources. Increase concurrency only when the host and
provider can support it.

## Verify concurrent projects

For a direct DDEV check, start two isolated projects and confirm that each
serves HTTP. Then inspect the host:

```bash
ddev list
docker ps --format '{{.Names}}'
```

Confirm the project names are distinct and that each project has its own
containers and database. Do not start a DDEV project in the NicheBench source
tree. After the test, let each project owner run teardown; do not manually
remove resources attached to active projects.

## Current status

DDEV-level concurrency has been verified with two projects running
simultaneously under distinct subdomains, databases, and containers, with both
serving HTTP. A concurrent benchmark run also verified task isolation and
teardown. This validates the runtime lifecycle, not the benchmark pass rate.
