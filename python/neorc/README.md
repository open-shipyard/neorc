# neorc

A next generation orchestration system: publish a task, and a worker somewhere
else runs it. This is the reference implementation on top of
[`neorc-core`](https://pypi.org/project/neorc-core/) — a manager service, a
worker command, and adapters for HTTP and Postgres.

Every dependency is optional, so a host installs only what its role needs:

    pip install neorc                       # nothing extra
    pip install 'neorc[http]'               # workers and publishers
    pip install 'neorc[manager,postgres]'   # the manager host

## Running it

    export NEORC_DATABASE_URL=postgresql://localhost/neorc
    neorc manager start --create-schema

    export NEORC_MANAGER_ADDRESS=manager.internal:8420
    neorc worker start --tasks tasks.toml

Where `tasks.toml` maps each task name to the function that runs it:

```toml
# tasks.toml
[tasks]
greet = "myapp.tasks:greet"
```

```python
# myapp/tasks.py
def greet(task):
    print(f"hello {task.payload['name']}")
```

Modules resolve from the file's directory, then the usual import path.
Handlers can be plain or `async` functions; plain ones run in a thread.

## What is in it

- `neorc` — `Worker`, `Task` and friends, re-exported from `neorc-core`
- `neorc.http` — `HttpQueueClient`, the long-polling queue client
- `neorc.manager` — the FastAPI application and the service that runs it
- `neorc.postgres` — the task store, the LISTEN/NOTIFY notifier, and the schema

See the [repository](https://github.com/open-shipyard/neorc) for the full
picture, and
[CONTRIBUTING.md](https://github.com/open-shipyard/neorc/blob/main/CONTRIBUTING.md)
to get started.
