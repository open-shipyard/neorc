# neorc-core

The mechanism of [neorc](https://github.com/open-shipyard/neorc), a next
generation orchestration system: the task model, the ports, and the manager and
worker logic that sit behind them.

This package has no dependencies, knows nothing about HTTP, and names no
database. Install [`neorc`](https://pypi.org/project/neorc/) for a working
deployment; install this one to implement your own adapters.

    pip install neorc-core

## What is in it

- `Task`, `TaskStatus` — the unit of work and its lifecycle
- `QueueClient` — how publishers and workers reach the queue
- `TaskStore`, `TaskNotifier` — how a manager persists tasks and wakes waiters
- `Manager` — leasing, waiting and status transitions, with no framework
- `Worker` — claim, execute, heartbeat, report
- `neorc_core.local` — in-memory adapters: the whole system in one process
- `neorc_core.testing.contracts` — test suites every adapter of a port must
  pass, for implementing your own (needs pytest and pytest-asyncio)

Licensed under the Apache License 2.0. See
[CONTRIBUTING.md](https://github.com/open-shipyard/neorc/blob/main/CONTRIBUTING.md)
to get started.
