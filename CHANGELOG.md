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
- `neorc-core`: a task's status transitions, the same in every store, and the
  `TaskNotifier` port.
- `neorc-core.local`: `MemoryTaskNotifier`, for exercising the system without
  a database.
- `neorc-core.testing.contracts`: test suites written against the ports, which
  the in-memory adapters, the Postgres adapters and the HTTP clients all run.
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
- `neorc-core`: `Manager`, uploading flows deployed together under the
  version rules, starting runs on the latest version with their inputs checked
  against the declared types, and cancelling, failing and succeeding run trees.
- `neorc-core`: `Manager` publishes tasks and starts sub-flow runs for the
  scheduler, checking the size of the complete payload; hands workers their
  tasks with references filled in; records starts and results; and long-polls
  events for the scheduler.
- `neorc-core`: the `QueueClient` and `ManagerClient` ports, the direct
  clients that reach a manager in the same process through JSON, as HTTP would,
  and `QueueClientContract` and `ManagerClientContract`.
- `neorc-core`: `Worker`, which checks at startup that every handler on its
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
- `neorc.postgres`: a LISTEN/NOTIFY notifier whose waiters hold no connection.
- `neorc.manager`: the FastAPI application, and a service that assembles it from
  `NEORC_DATABASE_URL`.
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
- `neorc.manager`: the flow routes: `POST /flows`, `GET /flows/{name}`
  and `/flows/{name}/versions/{version}`, `POST /flows/{name}/runs`,
  `GET /runs/{id}`, `POST /runs/{id}/cancel` and `GET /runs/{id}/state`.
  Bodies are read as bytes, capped at 16 MiB and checked for nesting depth
  before anything parses them. Every core error crosses as
  `{"error": "<class>", "detail": ...}` with a status per class, and a
  `FlowDefinitionError` carries its `problems`.
- `neorc.manager`: the routes the scheduler and workers use. For the
  scheduler, `GET /events` long-polled up to the manager's deadline,
  `POST /runs/{id}/tasks` and `/sub-runs` by address, `/succeed` with the
  output reference and `/fail`. For workers, `GET /queues/{queue}/tasks` for
  the task definitions, `POST /queues/{queue}/tasks/next` long-polled, and
  `POST /tasks/{id}/started`, `/heartbeat` and `/finished`. In core, a
  lease is at most a day and a run
  is succeeded only with a reference to one of its flow's tasks or
  sub-flows, so every client is refused the same requests.
- `neorc.http`: `HttpManagerClient` and `HttpQueueClient`, the flow
  clients over HTTP, held to the client contracts against the real
  application. An error body raises the core exception it names, with a
  `FlowDefinitionError`'s problems intact; a transport failure, or an answer
  that is not the manager's, is `ManagerUnavailableError`. `Worker` fails
  a result over the payload limit itself, once, rather than have the report
  refused on every lease, and reports an error message with what no store or
  transport could carry replaced. In core, a value nests at most 100 levels,
  half of what JSON text may, so it fits in whatever it travels in; and a
  queue name is letters, digits, `_` and `-`, as it travels in a URL path.
- `neorc.manager`: `build_app` assembles `Manager` on `PostgresStore`
  with two notification channels, `neorc_task_ready` for workers and
  `neorc_event_ready` for the scheduler, so a task or event recorded through
  one manager process wakes a waiter on another. `--create-schema` creates
  the flow tables too. An announcement is a hint, so a request never waits
  for one: `PostgresTaskNotifier` sends them from a background task, one per
  batch, on a connection it reopens when it breaks; the listening connection
  is reopened too, and both are kept alive by the kernel so a link dropped
  without a word is found dead within a bound.
- `neorc flows upload <dir> --manager-address`: read and validate the flow
  files as `neorc run` does, send them as one set, and print what was
  stored. `neorc scheduler start --manager-address` runs the scheduler on the
  HTTP client, stopping on `SIGINT` and `SIGTERM`. `neorc worker start
  --manager-address --code-location [--queue]` runs a worker for flows on the
  HTTP client; handlers that do not fit the queue's tasks stop it at startup
  with every problem listed.
- `neorc-core.testing.examples`: the example scenarios, `hello`,
  `word_picker` and `word_picker_rounds`, run through any `ManagerClient`
  with their outputs asserted. Core runs them on `LocalCluster`; `neorc` runs
  them deployed, on uvicorn, Postgres and the HTTP clients, with a scheduler
  and one worker per queue. The examples' deployed steps are runnable as
  their READMEs give them.
- `neorc-core`: a string holding NUL, in a value or anywhere in a flow
  definition, is refused in core before any store sees it, because Postgres
  `text` cannot hold it and the in-memory store must refuse what the deployed
  one would. Task errors and run reasons, which are messages, are stored with
  NUL replaced instead.

- `neorc-core`: the `Store` port lists, for a status page: `list_runs`,
  newest first with filters and a page cursor, `flow_versions`, `run_tasks`
  and `sub_runs`; `Manager` exposes them. Runs and tasks carry the times the
  store created, started and finished them, on the wire too. Both stores
  implement it; on Postgres the times are the server's `now()`, and runs get
  a `position` allocated under the event lock, so they list in the order
  they committed and a page never skips one. Existing tables get the new
  columns on `create_schema`.
- `neorc.manager`: the listings a status page reads, `GET /flows`,
  `GET /flows/{name}/versions`, `GET /runs` with filters and a page cursor,
  `GET /runs/{id}/tasks`, `GET /runs/{id}/sub-runs` and `GET /tasks/{id}`.
  The application's OpenAPI schema is committed as `ts/neorc-ui/openapi.json`,
  written by `scripts/export_openapi.py` and checked by a test, for the UI
  build to generate its types from without running Python.

### Changed

- The flow classes take the task API's names: `FlowManager` is `Manager`,
  `FlowWorker` is `Worker`, `FlowQueueClient` is `QueueClient`, `FlowTask` is
  `Task`, `HttpFlowQueueClient` is `HttpQueueClient`, `DirectFlowQueueClient`
  is `DirectQueueClient`, `FlowQueueClientContract` is `QueueClientContract`,
  and the worker routes move from `/flow-tasks` to `/tasks`. The table keeps
  its name, `neorc_flow_tasks`, since a database from before holds the old
  `neorc_tasks`, which create-if-absent would leave in place.

### Removed

- The task API, which let a task be published on its own: `TaskStore`,
  `Manager`, `QueueClient`, `Worker`, `Task`, `Payload`, `MemoryTaskStore`,
  `DirectQueueClient`, `TaskStoreContract`, `QueueClientContract`,
  `PostgresTaskStore`, `HttpQueueClient`, the `/tasks` routes, the
  `neorc_tasks` table, and `neorc worker start --tasks`. Every task belongs to
  a flow; a basic queue is a flow with a single task, as `examples/hello`
  shows. `create_schema` leaves a `neorc_tasks` table from before in place.

[Unreleased]: https://github.com/open-shipyard/neorc/commits/main
