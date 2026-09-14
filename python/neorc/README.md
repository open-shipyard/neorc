# neorc

A next generation orchestration system: flows of tasks, defined in YAML files,
run by workers wherever the work is. This is the reference implementation on
top of [`neorc-core`](https://pypi.org/project/neorc-core/) — a manager
service, the scheduler and worker commands, and adapters for HTTP and Postgres.

Every dependency is optional, so a host installs only what its role needs:

    pip install neorc                       # nothing extra
    pip install 'neorc[http]'               # scheduler and worker hosts
    pip install 'neorc[manager,postgres]'   # the manager host

## Running it

Try a directory of flow files in one process, with nothing to deploy:

    neorc run examples/wordplay --flow word_picker \
        --inputs '{"sentence": "potato tomate", "preferred_letter": "t"}'

A deployment runs a manager, a scheduler, and a worker per queue wherever the
work is:

    export NEORC_DATABASE_URL=postgresql://localhost/neorc
    neorc manager start --create-schema

    export NEORC_MANAGER_ADDRESS=manager.internal:8420
    neorc flows upload myapp/flows
    neorc scheduler start
    neorc worker start --code-location myapp

Each task in a flow file names its handler by import path, `module:function`,
resolved from the worker's code location first. Handlers are plain or `async`
functions taking the task's inputs as keyword arguments; plain ones run in a
thread, and they need not import neorc.

## What is in it

- `neorc` — `Scheduler`, `Worker` and friends, re-exported from `neorc-core`
- `neorc.http` — `HttpManagerClient` and `HttpQueueClient`, the
  long-polling clients the scheduler and workers use
- `neorc.manager` — the FastAPI application and the service that runs it
- `neorc.postgres` — the store, the LISTEN/NOTIFY notifier, and the schema

See the [repository](https://github.com/open-shipyard/neorc) for the full
picture, and
[CONTRIBUTING.md](https://github.com/open-shipyard/neorc/blob/main/CONTRIBUTING.md)
to get started.
