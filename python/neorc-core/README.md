# neorc-core

The mechanism of [neorc](https://github.com/open-shipyard/neorc), a next
generation orchestration system: flow definitions, the planner, the ports, and
the manager, scheduler and worker logic that sit behind them.

This package has no dependencies, knows nothing about HTTP, and names no
database. Install [`neorc`](https://pypi.org/project/neorc/) for a working
deployment; install this one to implement your own adapters.

    pip install neorc-core

## What is in it

- `neorc_core.flows` — flow definitions, loading and validation, and reference
  resolution
- `Manager` — uploads under the version rules, runs and their trees, tasks
  leased to workers, and the events a scheduler waits for, against the `Store`
  and `TaskNotifier` ports
- `Scheduler` — the planner's I/O: waits for events and moves runs forward
  through the `ManagerClient` port
- `Worker` — checks its handlers against its queue's tasks, then claims,
  executes, heartbeats and reports through the `QueueClient` port
- `neorc_core.local` — in-memory adapters, and `LocalCluster` and `run_local`:
  the whole system in one process
- `neorc_core.testing.contracts` — test suites every adapter of a port must
  pass, for implementing your own; `neorc_core.testing.examples` — the
  repository's examples as scenarios (both need pytest and pytest-asyncio)

Licensed under the Apache License 2.0. See
[CONTRIBUTING.md](https://github.com/open-shipyard/neorc/blob/main/CONTRIBUTING.md)
to get started.
