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

A claim is a lease, not a handoff: it makes the task invisible to other workers
for a bounded time rather than permanently. Claiming is atomic and must never
hand the same task to two live workers:

    SELECT ... FROM tasks
     WHERE run_after <= now()
       AND (status = 'pending' OR lease_expires_at <= now())
     ORDER BY priority DESC, run_after
     FOR UPDATE SKIP LOCKED
     LIMIT 1

`SKIP LOCKED` lets concurrent claimers pass over rows another transaction is
already taking instead of queueing behind them. The same transaction sets
`lease_expires_at = now() + visibility timeout`, moves the task to `claimed`
and increments its attempt count, so a task is never observed as
claimed-but-unleased.

## Lost workers

A worker that dies mid-task is recovered by the lease expiring, not by anything
noticing the worker is gone.

While a worker executes a task it heartbeats, and each heartbeat pushes
`lease_expires_at` further out. Stop heartbeating — crash, network partition,
a host that goes away — and the lease runs out, at which point the task matches
the claim query above again and the next worker to ask takes it. Recovery is a
side effect of the normal claim path: there is no reaper process to run, and no
sweeper to fall behind.

This is chosen over an explicit reaper because it is the model SQS already has,
so the queue can move there without the semantics changing:

| neorc                | Postgres                        | SQS                       |
| -------------------- | ------------------------------- | ------------------------- |
| claim with a lease   | `SKIP LOCKED` + `lease_expires_at` | `ReceiveMessage` with `VisibilityTimeout` |
| heartbeat            | push `lease_expires_at` out     | `ChangeMessageVisibility` |
| finish               | mark terminal                   | `DeleteMessage`           |
| worker died          | lease expires, task reappears   | visibility expires, message reappears |

A reaper has no counterpart in SQS, so building on one would mean rewriting
recovery when the backend changes.

Two consequences the rest of the system inherits:

- **Delivery is at-least-once.** A worker can finish its work and die before
  reporting it, and the task will be handed to someone else. Handlers have to be
  idempotent; the attempt count travels with the task so a handler can tell a
  retry from a first run.
- **The timeout has to outlast a missed heartbeat.** Heartbeat on an interval
  well under the visibility timeout — a third of it is a reasonable default — so
  a single slow or dropped beat does not hand a live worker's task to another.
  Tasks that run longer than the timeout are fine as long as they keep beating.

## Open questions

- Task status values and the legal transitions between them.
- Retries and their backoff, and where a permanently failed task lands.
- Whether payloads live in the task row or somewhere addressed by it.
- Migrations: shipped with the package, or the user's to run.
