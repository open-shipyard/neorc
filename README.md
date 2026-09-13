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

Write the work, in a module your workers can import:

```python
from neorc import Task, Worker


async def greet(task: Task) -> None:
    print(f"hello {task.payload['name']}")


def setup(worker: Worker) -> None:
    worker.register("greet", greet)
```

Start a worker, on any host that can reach the manager:

    export NEORC_MANAGER_ADDRESS=manager.internal:8420
    neorc worker start --handlers myapp.tasks:setup

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
