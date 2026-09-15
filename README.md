# neorc

A next generation orchestration system: flows of tasks, defined in YAML files
under version control, run by workers wherever the work is.

This repository is a monorepo. The packages it publishes:

| Package                           | Description                             |
| --------------------------------- | --------------------------------------- |
| [`neorc`](python/neorc)           | Manager service, scheduler and worker commands, Postgres and HTTP adapters |
| [`neorc-core`](python/neorc-core) | The mechanism: flow definitions, the planner, ports, and the manager, scheduler and worker logic |

## How it works

A flow file names its tasks, the queue each runs on, the function that runs it,
and where its inputs come from: the flow's inputs, or other tasks' results. A
**manager** service holds the flows and their runs. A **scheduler** moves runs
forward as tasks finish. **Workers** on other hosts long-poll the manager for
the next task on their queue, run its handler, and report back.

    scheduler > http > manager > database
    worker    > http > manager > database

Claiming a task takes a *lease*: the worker heartbeats while it runs, and a task
whose lease lapses goes back to the queue, so nothing is lost when a worker
dies. Delivery is at-least-once, so handlers should be idempotent.

## Quick start

Try a directory of flow files in one process, with nothing to deploy:

    uv run neorc run examples/wordplay --flow word_picker \
        --inputs '{"sentence": "potato tomate berry watermelon", "preferred_letter": "t"}'

A deployment installs what each host needs — the base install pulls in nothing:

    pip install 'neorc[manager,postgres]'   # the manager host
    pip install 'neorc[http]'               # scheduler and worker hosts

Start the manager, and create an API token for each process that will reach
it. Every request needs one; `neorc tokens create` prints its secret once:

    export NEORC_DATABASE_URL=postgresql://localhost/neorc
    neorc manager start --create-schema \
        --ssl-certfile manager.pem --ssl-keyfile manager-key.pem
    neorc tokens create scheduler
    neorc tokens create worker-1

A token is sent only over HTTPS, or to a loopback address: serve the manager
with a certificate as above, or behind a proxy that terminates TLS. `neorc
tokens list` and `neorc tokens revoke <name>` manage them. To try it on one
machine nobody else reaches, `neorc manager start --no-auth` asks for none.

Write the work, as plain functions that need not import neorc:

```python
# myapp/tasks.py
def greet(name):
    print(f"hello {name}")
```

Describe it in a flow file:

```yaml
# flows/hello.yaml
name: hello
version: 1.0.0
inputs: {name: string}
steps:
  greet:
    handler: myapp.tasks:greet
    params: {name: inputs.name}
```

Upload the flows, start the scheduler, and a worker on any host that can reach
the manager, with the code the handlers import from, each with its token in
`NEORC_API_TOKEN`:

    export NEORC_MANAGER_ADDRESS=https://manager.internal:8420
    export NEORC_API_TOKEN=neorc_...
    neorc flows upload flows
    neorc scheduler start
    neorc worker start --code-location .

Start a run from anywhere:

    curl -X POST https://manager.internal:8420/flows/hello/runs \
        -H "authorization: Bearer $NEORC_API_TOKEN" \
        -H 'content-type: application/json' -d '{"inputs": {"name": "world"}}'

Or from the web UI the manager serves at `https://manager.internal:8420/ui/`:
the flows, the runs and every task of each, live, as an outline or as a
zoomable graph of the run's steps, with a form to start a run and a button
to cancel one. Signing in to the UI is not there yet: until it is, the UI
works only with a manager started with `--no-auth`, where anyone who reaches
the manager can do all of that, so keep it to development and testing.

[examples/hello](examples/hello) and [examples/wordplay](examples/wordplay)
walk through both ways of running, step by step.

## Documentation

- [docs/specs/core.md](docs/specs/core.md) — the components and what they owe
  each other
- [docs/specs/flows.md](docs/specs/flows.md) — flow files: tasks, loops,
  fan-outs, sub-flows and references
- [docs/specs/postgres-implementation.md](docs/specs/postgres-implementation.md)
  — how the reference persistence layer claims, leases and recovers
- [docs/working-notes/web-ui-plan.md](docs/working-notes/web-ui-plan.md) — the
  web UI: what it shows, how it is built and shipped, and why
- [CONTRIBUTING.md](CONTRIBUTING.md) — repository layout and development setup

Licensed under the [Apache License 2.0](LICENSE).
