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
- `neorc-core`: the `Store` port for flow versions, runs, their tasks and events,
  one method per atomic operation; `MemoryStore`; and `StoreContract`, the
  test suite every store must pass.
- `neorc-core`: `FlowManager`, uploading flows deployed together under the
  version rules, starting runs on the latest version with their inputs checked
  against the declared types, and cancelling, failing and succeeding run trees.
- `neorc-core`: `FlowManager` publishes tasks and starts sub-flow runs for the
  scheduler, checking the size of the complete payload; hands workers their
  tasks with references filled in; records starts and results; and long-polls
  events for the scheduler.
- `neorc-core`: the `FlowQueueClient` and `ManagerClient` ports, the direct
  clients that reach a manager in the same process through JSON, as HTTP would,
  and `FlowQueueClientContract` and `ManagerClientContract`.
- `neorc-core`: `FlowWorker`, which checks at startup that every handler on its
  queue imports and takes exactly its task's inputs, then runs handlers with
  their inputs as keyword arguments, plain functions in a thread. Handler
  resolution by import path moves from the `neorc` command to `neorc-core`.
- `neorc-core`: the `Scheduler`, which waits for events, plans each run they
  concern and applies the actions, failing a run whose request is rejected; and
  `LocalCluster` and `run_local`, a manager, scheduler and one worker per queue
  in one process.
- `neorc run <dir> --flow <name> --inputs <json>`: run a flow to its end in
  one process, with nothing to deploy. `examples/hello` and `examples/wordplay`
  run with it, and their outputs are asserted in the tests.
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
- `neorc.postgres`: the tables for flows — `neorc_flow_versions`,
  `neorc_runs`, `neorc_flow_tasks` and `neorc_events` — created by
  `create_schema` next to `neorc_tasks`, with the row mapping to and from the
  core's dataclasses. Values are stored as their compact JSON text, so numbers
  read back exactly as written; a flow version's content is canonical JSON.
- `neorc.postgres`: `PostgresStore`, the `Store` for flows on Postgres:
  uploads under an advisory lock, runs and sub-flow runs, succeeding, failing
  and cancelling run trees under their root's row lock, and events appended
  last under a second advisory lock so their sequences commit in order; tasks
  published and started under the same root lock, claimed oldest first with
  `FOR UPDATE SKIP LOCKED`, and a run's state read in one snapshot. It passes
  the store contract in full. A version part is at most 2³¹ − 1, what the
  store's `integer` columns hold, in every store.
- `neorc-core`: a string holding NUL, in a value or anywhere in a flow
  definition, is refused in core before any store sees it, because Postgres
  `text` cannot hold it and the in-memory store must refuse what the deployed
  one would. Task errors and run reasons, which are messages, are stored with
  NUL replaced instead.

[Unreleased]: https://github.com/open-shipyard/neorc/commits/main
