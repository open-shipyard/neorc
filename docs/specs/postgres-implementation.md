# Postgres implementation

The reference persistence layer, shipped in `neorc` behind the `postgres`
extra. Nothing here is visible from `neorc-core`: the manager service depends
on a port, and this is one adapter behind it. See
[core.md](core.md) for the component layout.

## Scope

Postgres backs two distinct concerns, and they are kept apart on purpose:

- **The task queue** — handing the next task to a waiting worker. Replaceable
  by Redis or SQS later, which is why "fetch a task" is its own endpoint.
- **Task state** — the record of every task and its status. Stays in Postgres
  even if the queue moves elsewhere, which is why "inform task start" is a
  separate endpoint.

## Waiting workers

Workers long-poll `pick next task`. A deployment has many more workers than it
has database connections to spare, so a request that is waiting must not hold a
connection open.

A waiting request holds no connection. It waits on an in-process asyncio event,
and the manager runs one shared `LISTEN` connection for the whole service: when
a task is published, the publishing transaction issues `NOTIFY`, the listener
wakes the waiters, and only then does a waiter take a connection from the pool
to claim a task. Idle workers therefore cost one connection per manager
process, not one per worker.

Long polls need a deadline shorter than any proxy timeout in front of the
manager; a worker that reaches it re-polls. A wakeup is a hint, not a promise —
a woken waiter that finds nothing to claim goes back to waiting rather than
returning empty.

## Claiming a task

Claiming is atomic and must never hand the same task to two workers:

    SELECT ... FROM tasks
     WHERE status = 'pending' AND run_after <= now()
     ORDER BY priority DESC, run_after
     FOR UPDATE SKIP LOCKED
     LIMIT 1

`SKIP LOCKED` lets concurrent claimers pass over rows another transaction is
already taking instead of queueing behind them. The claim and the status
transition happen in the same transaction, so a task is never observed as
claimed-but-unassigned.

## Open questions

- Task status values and the legal transitions between them.
- What happens to a task whose worker dies after claiming it: a visibility
  timeout with a heartbeat, or an explicit reaper.
- Retries and their backoff, and where a permanently failed task lands.
- Whether payloads live in the task row or somewhere addressed by it.
- Migrations: shipped with the package, or the user's to run.
