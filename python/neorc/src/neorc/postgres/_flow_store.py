# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres store for flows, runs, their tasks and events.

Every operation is one transaction in ``READ COMMITTED``, and takes only the
locks it needs, always in this order, so the rules ``MemoryStore`` keeps under
one lock hold here too, and no two transactions wait on each other in a cycle:

1. The upload lock, a transaction-level advisory lock: exclusive in
   ``store_flows``, shared in ``start_run``. Two uploads cannot pass the set
   check against the same stored flows, and a run cannot start on a version an
   upload is replacing.
2. Root run rows, ``FOR UPDATE``, in id order, in every operation that adds to,
   finishes or reads the status of a run tree. A tree update runs after its root
   is locked, as a statement of its own, so it sees every child committed before
   the lock was granted; and a child cannot be added without the same lock. A
   cancellation therefore never misses a sub-run or task, and nothing is added
   to a tree after it stopped.
3. Other rows: runs, tasks.
4. The event lock, a second advisory lock, taken last, by a statement of its
   own. A later statement appends every event of the transaction, with
   sequences allocated as ``max(sequence) + 1``: its snapshot is taken after
   the lock is granted, so it sees the events of the transaction that held the
   lock before. Nothing is locked after it, so events commit in sequence order
   and a scheduler reading after a sequence misses none.

There are no foreign keys between the tables, so an insert takes no lock on
another row behind the order's back; the operations keep the references whole.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from neorc.postgres._pool import Pooled
from neorc.postgres._rows import (
    EVENT_COLUMNS,
    FLOW_COLUMNS,
    RUN_COLUMNS,
    TASK_COLUMNS,
    event_from_row,
    flow_from_row,
    flow_to_row,
    run_from_row,
    run_to_row,
    task_from_row,
    task_to_row,
)
from neorc.postgres._schema import (
    EVENTS_TABLE,
    FLOW_TASKS_TABLE,
    FLOW_VERSIONS_TABLE,
    RUNS_TABLE,
)
from neorc_core import (
    LEASED_STATUSES,
    Event,
    EventKind,
    FlowNotFoundError,
    FlowTask,
    FlowVersionError,
    Run,
    RunId,
    RunNotFoundError,
    RunStatus,
    Store,
    StoredFlow,
    TaskId,
    TaskNotFoundError,
    TaskStateError,
    TaskStatus,
    ensure_transition,
)
from neorc_core._runs import (
    check_uploads,
    ensure_active,
    run_state_of,
    storable_text,
    sub_run_id_for,
    task_id_for,
)
from neorc_core._values import JsonValue, dumps_json
from neorc_core.flows import Address, Reference, RunState, Version
from neorc_core.ports._flow_clients import DEFAULT_LEASE_SECONDS

_LOCK_SPACE = 0x6E656F72  # "neor": keeps clear of other advisory locks in the database
UPLOAD_LOCK = (_LOCK_SPACE, 1)
"""The advisory lock key uploads take exclusively and run starts share."""
EVENT_LOCK = (_LOCK_SPACE, 2)
"""The advisory lock key every transaction appending events takes, last."""

_LATEST_ORDER = "ORDER BY major DESC, minor DESC, patch DESC"

_SELECT_ALL_FLOWS = f"SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}"

_SELECT_LATEST_FLOW = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}
 WHERE name = %(name)s
 {_LATEST_ORDER} LIMIT 1
"""

_SELECT_FLOW = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}
 WHERE name = %(name)s AND major = %(major)s AND minor = %(minor)s
   AND patch = %(patch)s
"""

_SELECT_LATEST_FLOWS = f"""
SELECT DISTINCT ON (name) {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}
 ORDER BY name, major DESC, minor DESC, patch DESC
"""

_INSERT_FLOW = f"""
INSERT INTO {FLOW_VERSIONS_TABLE} ({FLOW_COLUMNS})
VALUES (%(name)s, %(major)s, %(minor)s, %(patch)s, %(content)s)
"""

_SELECT_RUN = f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE id = %(id)s"

_ACTIVE_ROOTS_OF_FLOW = f"""
SELECT DISTINCT root_id FROM {RUNS_TABLE}
 WHERE flow = %(flow)s AND status = 'active'
"""

_ACTIVE_ROOTS_OF_FLOW_AMONG = f"""
SELECT DISTINCT root_id FROM {RUNS_TABLE}
 WHERE flow = %(flow)s AND status = 'active' AND root_id = ANY(%(roots)s)
"""

_LOCK_ROOTS = f"""
SELECT id FROM {RUNS_TABLE} WHERE id = ANY(%(ids)s) ORDER BY id FOR UPDATE
"""

_INSERT_RUN = f"""
INSERT INTO {RUNS_TABLE} ({RUN_COLUMNS})
VALUES (%(id)s, %(flow)s, %(version)s, %(inputs)s, %(status)s, %(root_id)s,
        %(parent_id)s, %(parent_address)s, %(output)s, %(reason)s)
ON CONFLICT (id) DO NOTHING
RETURNING id
"""

_SUCCEED_RUN = f"""
UPDATE {RUNS_TABLE}
   SET status = 'succeeded', output = %(output)s
 WHERE id = %(id)s AND status = 'active'
RETURNING {RUN_COLUMNS}
"""

_FINISH_TREE = f"""
UPDATE {RUNS_TABLE}
   SET status = %(status)s, reason = %(reason)s
 WHERE root_id = ANY(%(root_ids)s) AND status = 'active'
RETURNING id
"""

# Sequences follow on from the highest committed, which the statement sees
# because the event lock was granted first. WITH ORDINALITY keeps the events of
# one transaction in the order they happened.
_INSERT_EVENTS = f"""
INSERT INTO {EVENTS_TABLE} ({EVENT_COLUMNS})
SELECT (SELECT COALESCE(MAX(sequence), 0) FROM {EVENTS_TABLE}) + e.ordinality,
       e.run_id, e.kind
  FROM unnest(%(run_ids)s::uuid[], %(kinds)s::text[])
       WITH ORDINALITY AS e (run_id, kind, ordinality)
"""

_SELECT_EVENTS = f"""
SELECT {EVENT_COLUMNS} FROM {EVENTS_TABLE}
 WHERE sequence > %(after)s
 ORDER BY sequence
 LIMIT %(limit)s
"""

_SELECT_TASK = f"SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE id = %(id)s"

_LOCK_TASK = f"{_SELECT_TASK} FOR UPDATE"

_INSERT_TASK = f"""
INSERT INTO {FLOW_TASKS_TABLE} ({TASK_COLUMNS})
VALUES (%(id)s, %(run_id)s, %(address)s, %(queue)s, %(handler)s, %(params)s,
        %(fixed_params)s, %(status)s, %(attempts)s, %(lease_expires_at)s,
        %(result)s, %(error)s)
ON CONFLICT (id) DO NOTHING
RETURNING id
"""

# One statement, so the claim and everything that goes with it are atomic. The
# inner SELECT takes the oldest ready task of the queue and locks it, skipping
# rows another claimer already holds; ready is pending, or leased past the
# lease's end.
_CLAIM_TASK = f"""
UPDATE {FLOW_TASKS_TABLE} AS t
   SET status = 'claimed',
       attempts = t.attempts + 1,
       lease_expires_at = now() + make_interval(secs => %(lease_seconds)s)
 WHERE t.id = (
       SELECT c.id
         FROM {FLOW_TASKS_TABLE} AS c
        WHERE c.queue = %(queue)s
          AND (c.status = 'pending'
               OR (c.status IN ('claimed', 'running')
                   AND c.lease_expires_at <= now()))
        ORDER BY c.position
          FOR UPDATE SKIP LOCKED
        LIMIT 1)
RETURNING {TASK_COLUMNS}
"""

_START_TASK = f"""
UPDATE {FLOW_TASKS_TABLE} SET status = 'running' WHERE id = %(id)s
RETURNING {TASK_COLUMNS}
"""

_EXTEND_TASK_LEASE = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET lease_expires_at = now() + make_interval(secs => %(lease_seconds)s)
 WHERE id = %(id)s
RETURNING lease_expires_at
"""

_FINISH_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = %(status)s, result = %(result)s, error = %(error)s,
       lease_expires_at = NULL
 WHERE id = %(id)s
RETURNING {TASK_COLUMNS}
"""

_TASKS_OF_RUN = f"SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE run_id = %(id)s"

_SUB_RUNS_OF_RUN = f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE parent_id = %(id)s"

Connection = AsyncConnection[DictRow]
Events = list[tuple[RunId, EventKind]]


class PostgresStore(Pooled, Store):
    """Flows, runs, tasks and events in Postgres, over a connection pool."""

    async def store_flows(self, uploads: Sequence[StoredFlow]) -> list[bool]:
        async with self.pool.connection() as conn, conn.transaction():
            await _lock(conn, UPLOAD_LOCK, shared=False)
            stored: dict[str, list[StoredFlow]] = {}
            for row in await (await conn.execute(_SELECT_ALL_FLOWS)).fetchall():
                flow = flow_from_row(row)
                stored.setdefault(flow.name, []).append(flow)
            results = check_uploads(stored, uploads)
            new = [u for u, is_new in zip(uploads, results, strict=True) if is_new]
            if not new:
                return results
            await conn.cursor().executemany(_INSERT_FLOW, [flow_to_row(u) for u in new])

            # The trees to cancel are read before their roots are locked. No
            # run of the flow can start meanwhile, because a start takes the
            # upload lock too; but one can finish, under its root lock alone,
            # so once the roots are held the trees are read again among them:
            # a tree with no run of the flow left active is not cancelled.
            roots: dict[str, list[RunId]] = {}
            for upload in new:
                cursor = await conn.execute(
                    _ACTIVE_ROOTS_OF_FLOW, {"flow": upload.name}
                )
                roots[upload.name] = [row["root_id"] for row in await cursor.fetchall()]
            await _lock_roots(conn, [r for ids in roots.values() for r in ids])
            events: Events = []
            for upload in new:
                if not roots[upload.name]:
                    continue
                cursor = await conn.execute(
                    _ACTIVE_ROOTS_OF_FLOW_AMONG,
                    {"flow": upload.name, "roots": roots[upload.name]},
                )
                still = [row["root_id"] for row in await cursor.fetchall()]
                if still:
                    reason = f"{upload.name} {upload.version} was uploaded"
                    events += await _finish_trees(
                        conn, still, RunStatus.CANCELLED, reason
                    )
            await _append_events(conn, events)
            return results

    async def get_flow(self, name: str, version: Version | None = None) -> StoredFlow:
        async with self.pool.connection() as conn:
            return await _flow(conn, name, version)

    async def latest_flows(self) -> list[StoredFlow]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_SELECT_LATEST_FLOWS)
            return [flow_from_row(row) for row in await cursor.fetchall()]

    async def start_run(
        self,
        flow: str,
        version: Version,
        inputs: Mapping[str, JsonValue],
        *,
        parent_id: RunId | None = None,
        parent_address: Address | None = None,
    ) -> Run:
        if (parent_id is None) != (parent_address is None):
            raise ValueError("a sub-flow run names both its parent and its address")
        async with self.pool.connection() as conn, conn.transaction():
            await _lock(conn, UPLOAD_LOCK, shared=True)
            latest = (await _flow(conn, flow, None)).version
            if version != latest:
                raise FlowVersionError(
                    f"{flow} {version} is not the latest version, {latest}"
                )
            if parent_id is None:
                run_id = uuid.uuid4()
                root_id = run_id
            else:
                assert parent_address is not None
                parent = await _run(conn, parent_id)
                await _lock_roots(conn, [parent.root_id])
                # Under the root lock, the parent's status is settled.
                ensure_active(await _run(conn, parent_id))
                run_id = sub_run_id_for(parent_id, parent_address)
                root_id = parent.root_id
            run = Run(
                id=run_id,
                flow=flow,
                version=version,
                inputs=dict(inputs),
                status=RunStatus.ACTIVE,
                root_id=root_id,
                parent_id=parent_id,
                parent_address=parent_address,
            )
            cursor = await conn.execute(_INSERT_RUN, run_to_row(run))
            if await cursor.fetchone() is None:  # the same instance, started before
                return await _run(conn, run_id)
            await _append_events(conn, [(run_id, EventKind.RUN_STARTED)])
            return run

    async def get_run(self, run_id: RunId) -> Run:
        async with self.pool.connection() as conn:
            return await _run(conn, run_id)

    async def run_state(self, run_id: RunId) -> RunState:
        async with self.pool.connection() as conn, conn.transaction():
            # The run, its tasks and its sub-runs from one snapshot, so the
            # state is one a serial history could have reached.
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            run = await _run(conn, run_id)
            tasks = await conn.execute(_TASKS_OF_RUN, {"id": run_id})
            sub_runs = await conn.execute(_SUB_RUNS_OF_RUN, {"id": run_id})
            return run_state_of(
                run,
                [task_from_row(row) for row in await tasks.fetchall()],
                [run_from_row(row) for row in await sub_runs.fetchall()],
            )

    async def succeed_run(self, run_id: RunId, output: JsonValue) -> Run:
        async with self.pool.connection() as conn, conn.transaction():
            run = await _run(conn, run_id)
            await _lock_roots(conn, [run.root_id])
            cursor = await conn.execute(
                _SUCCEED_RUN, {"id": run_id, "output": dumps_json(output)}
            )
            row = await cursor.fetchone()
            if row is None:  # not active any more: say what it is instead
                ensure_active(await _run(conn, run_id))
                raise AssertionError("unreachable: the run was active and locked")
            await _append_events(conn, [(run_id, EventKind.RUN_FINISHED)])
            return run_from_row(row)

    async def fail_run_tree(self, run_id: RunId, reason: str) -> None:
        await self._finish_tree(run_id, RunStatus.FAILED, reason)

    async def cancel_run_tree(self, run_id: RunId, reason: str) -> None:
        await self._finish_tree(run_id, RunStatus.CANCELLED, reason)

    async def publish_task(
        self,
        run_id: RunId,
        address: Address,
        *,
        queue: str,
        handler: str,
        params: Mapping[str, Reference],
        fixed_params: Mapping[str, JsonValue],
    ) -> FlowTask:
        async with self.pool.connection() as conn, conn.transaction():
            run = await _run(conn, run_id)
            await _lock_roots(conn, [run.root_id])
            # Under the root lock, the run's status is settled.
            ensure_active(await _run(conn, run_id))
            task = FlowTask(
                id=task_id_for(run_id, address),
                run_id=run_id,
                address=address,
                queue=queue,
                handler=handler,
                params=dict(params),
                fixed_params=dict(fixed_params),
            )
            cursor = await conn.execute(_INSERT_TASK, task_to_row(task))
            if await cursor.fetchone() is None:  # published before: unchanged
                return await _task(conn, task.id)
            return task

    async def claim_task(
        self, queue: str, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> FlowTask | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                _CLAIM_TASK, {"queue": queue, "lease_seconds": lease_seconds}
            )
            row = await cursor.fetchone()
        return None if row is None else task_from_row(row)

    async def start_task(self, task_id: TaskId) -> FlowTask:
        inactive: Run | None = None
        row = None
        async with self.pool.connection() as conn, conn.transaction():
            run = await _run(conn, (await _task(conn, task_id)).run_id)
            await _lock_roots(conn, [run.root_id])
            task = await _task(conn, task_id, lock=True)
            ensure_transition(task.status, TaskStatus.RUNNING)
            run = await _run(conn, run.id)
            if run.status is not RunStatus.ACTIVE:
                # Failed and committed before the refusal is raised, so the
                # lease never hands the task out again.
                await conn.execute(
                    _FINISH_TASK,
                    {
                        "id": task_id,
                        "status": TaskStatus.FAILED.value,
                        "result": dumps_json(None),
                        "error": f"run {run.id} is {run.status.value}",
                    },
                )
                inactive = run
            else:
                cursor = await conn.execute(_START_TASK, {"id": task_id})
                row = await cursor.fetchone()
        if inactive is not None:
            ensure_active(inactive)
        if row is None:  # unreachable: the row was locked just above
            raise TaskNotFoundError(str(task_id))
        return task_from_row(row)

    async def extend_task_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        async with self.pool.connection() as conn, conn.transaction():
            task = await _task(conn, task_id, lock=True)
            if task.status not in LEASED_STATUSES:
                raise TaskStateError(
                    f"a {task.status.value} task holds no lease to extend"
                )
            cursor = await conn.execute(
                _EXTEND_TASK_LEASE, {"id": task_id, "lease_seconds": lease_seconds}
            )
            row = await cursor.fetchone()
        if row is None:  # unreachable: the row was locked just above
            raise TaskNotFoundError(str(task_id))
        expires_at: datetime = row["lease_expires_at"]
        return expires_at

    async def finish_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> FlowTask:
        status = TaskStatus.FAILED if error is not None else TaskStatus.SUCCEEDED
        async with self.pool.connection() as conn, conn.transaction():
            task = await _task(conn, task_id, lock=True)
            ensure_transition(task.status, status)
            cursor = await conn.execute(
                _FINISH_TASK,
                {
                    "id": task_id,
                    "status": status.value,
                    "result": dumps_json(result if error is None else None),
                    "error": storable_text(error) if error is not None else None,
                },
            )
            row = await cursor.fetchone()
            if row is None:  # unreachable: the row was locked just above
                raise TaskNotFoundError(str(task_id))
            await _append_events(conn, [(task.run_id, EventKind.TASK_FINISHED)])
            return task_from_row(row)

    async def get_task(self, task_id: TaskId) -> FlowTask:
        async with self.pool.connection() as conn:
            return await _task(conn, task_id)

    async def events_after(self, sequence: int, *, limit: int = 100) -> list[Event]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                _SELECT_EVENTS, {"after": sequence, "limit": limit}
            )
            return [event_from_row(row) for row in await cursor.fetchall()]

    async def _finish_tree(self, run_id: RunId, status: RunStatus, reason: str) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            root_id = (await _run(conn, run_id)).root_id
            await _lock_roots(conn, [root_id])
            events = await _finish_trees(conn, [root_id], status, reason)
            await _append_events(conn, events)


async def _lock(conn: Connection, key: tuple[int, int], *, shared: bool) -> None:
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await conn.execute(f"SELECT {function}(%s, %s)", key)


async def _lock_roots(conn: Connection, root_ids: Sequence[RunId]) -> None:
    """Lock root run rows, in id order, before anything in their trees changes."""
    if root_ids:
        await conn.execute(_LOCK_ROOTS, {"ids": sorted(set(root_ids))})


async def _flow(conn: Connection, name: str, version: Version | None) -> StoredFlow:
    if version is None:
        cursor = await conn.execute(_SELECT_LATEST_FLOW, {"name": name})
    else:
        cursor = await conn.execute(
            _SELECT_FLOW,
            {
                "name": name,
                "major": version.major,
                "minor": version.minor,
                "patch": version.patch,
            },
        )
    row = await cursor.fetchone()
    if row is None:
        if version is None:
            raise FlowNotFoundError(f"no flow {name!r}")
        raise FlowNotFoundError(f"no version {version} of flow {name!r}")
    return flow_from_row(row)


async def _run(conn: Connection, run_id: RunId) -> Run:
    cursor = await conn.execute(_SELECT_RUN, {"id": run_id})
    row = await cursor.fetchone()
    if row is None:
        raise RunNotFoundError(str(run_id))
    return run_from_row(row)


async def _task(conn: Connection, task_id: TaskId, *, lock: bool = False) -> FlowTask:
    """A task; with ``lock``, its row locked so the caller can decide and write."""
    cursor = await conn.execute(_LOCK_TASK if lock else _SELECT_TASK, {"id": task_id})
    row = await cursor.fetchone()
    if row is None:
        raise TaskNotFoundError(str(task_id))
    return task_from_row(row)


async def _finish_trees(
    conn: Connection, root_ids: Sequence[RunId], status: RunStatus, reason: str
) -> Events:
    """End every active run of the trees, their roots already locked; the events."""
    cursor = await conn.execute(
        _FINISH_TREE,
        {
            "root_ids": list(root_ids),
            "status": status.value,
            "reason": storable_text(reason),
        },
    )
    return [(row["id"], EventKind.RUN_FINISHED) for row in await cursor.fetchall()]


async def _append_events(conn: Connection, events: Events) -> None:
    """Record the transaction's events, last thing, under the event lock."""
    if not events:
        return
    await _lock(conn, EVENT_LOCK, shared=False)
    await conn.execute(
        _INSERT_EVENTS,
        {
            "run_ids": [run_id for run_id, _ in events],
            "kinds": [kind.value for _, kind in events],
        },
    )
