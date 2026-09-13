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
- `neorc-core.testing`: in-memory `MemoryTaskStore`, `MemoryTaskNotifier` and
  `DirectQueueClient`, for exercising handlers and workers without a database.
- Claims are leases: workers heartbeat to hold a task, and a task whose lease
  lapses returns to the queue. Chosen so the queue can move to SQS unchanged.
- `neorc.postgres`: the task store, claiming with `FOR UPDATE SKIP LOCKED`, and
  a LISTEN/NOTIFY notifier whose waiters hold no connection.
- `neorc.manager`: the FastAPI application, and a service that assembles it from
  `NEORC_DATABASE_URL`.
- `neorc.http`: `HttpQueueClient`, the long-polling client publishers and
  workers use.
- `neorc`: the console script — `neorc manager start` and `neorc worker start`.
  A command whose extra is missing says which one to install rather than
  raising `ModuleNotFoundError`.
- Workers stop on `SIGINT` and `SIGTERM`, cutting an idle long poll short so
  shutdown does not outlast a supervisor's grace period. A task already being
  executed is always allowed to finish.

[Unreleased]: https://github.com/open-shipyard/neorc/commits/main
