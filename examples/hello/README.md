# hello

Run a flow in one process, with nothing to deploy, from the repository root
after `uv sync`:

    uv run neorc run examples/hello --flow a

The worker prints `a`, and the command prints the run's output, `null`: these
flows have none. `--flow b` prints `b`.

## Deployed

A manager, a scheduler, a worker with two handlers (`a` prints `a`, `b` prints
`b`), and flow runs started on demand with `curl`. Every task belongs to a
flow, so each handler gets a single-task flow in `flows/`, which names its
function in `tasks.py` by import path. `tasks.py` does not import neorc. Run
each step from the repository root in its own terminal, after `uv sync`.

1. Postgres (skip if you have one; set `NEORC_DATABASE_URL` to it instead):

       uv run python examples/hello/postgres.py

   It prints an `export NEORC_DATABASE_URL=...` line; copy it for step 2.

2. The manager:

       export NEORC_DATABASE_URL=...   # from step 1
       uv run neorc manager start --host 127.0.0.1 --create-schema

3. Upload the flows, as CI/CD would:

       uv run neorc flows upload --manager-address 127.0.0.1:8420 \
           examples/hello/flows

4. The scheduler:

       uv run neorc scheduler start --manager-address 127.0.0.1:8420

5. The worker, serving the `default` queue with the code in this directory:

       uv run neorc worker start --manager-address 127.0.0.1:8420 \
           --code-location examples/hello

6. Start runs, as often as you like. The worker prints `a` or `b`:

       curl -X POST 127.0.0.1:8420/flows/a/runs \
           -H 'content-type: application/json' -d '{"inputs": {}}'

       curl -X POST 127.0.0.1:8420/flows/b/runs \
           -H 'content-type: application/json' -d '{"inputs": {}}'

   Each response carries the run's `id`; check on it with
   `curl 127.0.0.1:8420/runs/<id>`.

[python/neorc/tests/test_end_to_end.py](../../python/neorc/tests/test_end_to_end.py)
runs these steps, on a Postgres of its own, and asserts what the worker
prints.
