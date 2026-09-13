# Changelog

All notable changes to this project are documented in this file. It covers
every package in the repository; entries name the package they affect.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). All packages share
one version, cut from a single tag on `main`.

## [Unreleased]

### Added

- Initial monorepo structure: a uv workspace with the `neorc-core` and `neorc`
  Python packages under `python/`.
- `neorc-core`: the `Task` model and its status transitions, the `QueueClient`,
  `TaskStore` and `TaskNotifier` ports, the `Manager` (leasing, long-poll
  waiting, transitions) and the `Worker` loop (claim, execute, heartbeat,
  report).
- `neorc-core.local`: in-memory `MemoryTaskStore`, `MemoryTaskNotifier` and
  `DirectQueueClient`, for exercising handlers and workers without a database.
- `neorc-core.testing.contracts`: `TaskStoreContract` and `QueueClientContract`,
  test suites written against the ports. The in-memory adapters, the Postgres
  store and the HTTP queue client all run them.
- `neorc-core.flows`: `RunState`, a run's step results addressed by loop
  iteration and fan-out index, and `resolve`, which gives a reference's value
  for a consumer as the "Resolving references" table of the flows spec does.
- `neorc-core`: the planner, `plan(definition, run_state)`, a pure function
  that publishes every ready task instance and moves loops and fan-outs on,
  starts sub-flow runs, succeeds a run with its output once every step
  finished, and fails it when a task or sub-flow run fails.
- Claims are leases: workers heartbeat to hold a task, and a task whose lease
  lapses returns to the queue. Chosen so the queue can move to SQS unchanged.
- `neorc.postgres`: the task store, claiming with `FOR UPDATE SKIP LOCKED`, and
  a LISTEN/NOTIFY notifier whose waiters hold no connection.
- `neorc.manager`: the FastAPI application, and a service that assembles it from
  `NEORC_DATABASE_URL`.
- `neorc.http`: `HttpQueueClient`, the long-polling client publishers and
  workers use.
- `neorc worker start --tasks tasks.toml` (or `$NEORC_WORKER_TASKS`): a
  `[tasks]` table maps task names to `module:function` handlers, which need not
  import neorc and may be plain functions. Every entry is checked at startup.
- `neorc`: the console script — `neorc manager start` and `neorc worker start`.
  A command whose extra is missing says which one to install rather than
  raising `ModuleNotFoundError`.
- Workers stop on `SIGINT` and `SIGTERM`, cutting an idle long poll short so
  shutdown does not outlast a supervisor's grace period. A task already being
  executed is always allowed to finish.

[Unreleased]: https://github.com/open-shipyard/neorc/commits/main
