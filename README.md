# neorc

A next generation orchestration system: publish a task, and a worker somewhere
else runs it.

This repository is a monorepo. The packages it publishes:

| Package                           | Description                             |
| --------------------------------- | --------------------------------------- |
| [`neorc`](python/neorc)           | Manager service, worker CLI, Postgres and HTTP adapters |
| [`neorc-core`](python/neorc-core) | The mechanism: task model, ports, manager and worker logic |

## How it works

A **manager** service owns the queue. **Workers** on other hosts long-poll it
for the next task, run it, and report back. A **publisher** is anything that
sends a task to the manager.

    publisher > queue client > http > manager > database
    worker     > queue client > http > manager > database

Claiming a task takes a *lease*: the worker heartbeats while it runs, and a task
whose lease lapses goes back to the queue, so nothing is lost when a worker
dies. Delivery is at-least-once, so handlers should be idempotent.

## Quick start

Install what each host needs — the base install pulls in nothing:

    pip install 'neorc[manager,postgres]'   # the manager host
    pip install 'neorc[http]'               # worker and publisher hosts

Start the manager:

    export NEORC_DATABASE_URL=postgresql://localhost/neorc
    neorc manager start --create-schema

Write the work, as plain functions that need not import neorc:

```python
# myapp/tasks.py
def greet(task):
    print(f"hello {task.payload['name']}")
```

Name each task in a TOML file. Modules resolve from the file's directory, then
the usual import path. Handlers can be plain or `async` functions; plain ones
run in a thread.

```toml
# tasks.toml
[tasks]
greet = "myapp.tasks:greet"
```

Start a worker, on any host that can reach the manager:

    export NEORC_MANAGER_ADDRESS=manager.internal:8420
    neorc worker start --tasks tasks.toml

Publish from anywhere:

```python
from neorc.http import HttpQueueClient

async with HttpQueueClient("manager.internal:8420") as queue:
    task_id = await queue.publish("greet", {"name": "world"})
    print(await queue.get_status(task_id))
```

## Documentation

- [docs/specs/core.md](docs/specs/core.md) — the components and what they owe
  each other
- [docs/specs/postgres-implementation.md](docs/specs/postgres-implementation.md)
  — how the reference persistence layer claims, leases and recovers
- [CONTRIBUTING.md](CONTRIBUTING.md) — repository layout and development setup

Licensed under the [Apache License 2.0](LICENSE).
