# hello

A manager, a worker with two handlers (`a` prints `a`, `b` prints `b`), and
tasks submitted on demand with `curl`. `tasks.toml` maps each task name to its
function in `tasks.py`, which does not import neorc. Run each step from the repository root
in its own terminal, after `uv sync`.

1. Postgres (skip if you have one; set `NEORC_DATABASE_URL` to it instead):

       uv run python examples/hello/postgres.py

   It prints an `export NEORC_DATABASE_URL=...` line; copy it for step 2.

2. The manager:

       export NEORC_DATABASE_URL=...   # from step 1
       uv run neorc manager start --host 127.0.0.1 --create-schema

3. The worker:

       uv run neorc worker start --manager-address 127.0.0.1:8420 \
           --tasks examples/hello/tasks.toml

4. Submit tasks, as often as you like. The worker prints `a` or `b`:

       curl -X POST 127.0.0.1:8420/tasks -H 'content-type: application/json' \
           -d '{"name": "a", "payload": {}}'

       curl -X POST 127.0.0.1:8420/tasks -H 'content-type: application/json' \
           -d '{"name": "b", "payload": {}}'

   Each response carries the task's `id`; check on it with
   `curl 127.0.0.1:8420/tasks/<id>`.
