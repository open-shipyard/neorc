# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What every ``TaskStore`` must do: leases, ordering, transitions."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from neorc_core._errors import TaskNotFoundError, TaskStateError
from neorc_core._task import TaskStatus
from neorc_core.ports._task_store import TaskStore


class TaskStoreContract:
    """Subclass and provide a ``store`` fixture holding no tasks."""

    @pytest.fixture
    def store(self) -> TaskStore:
        raise NotImplementedError("a TaskStoreContract subclass provides `store`")

    async def test_a_stored_task_comes_back_as_published(
        self, store: TaskStore
    ) -> None:
        added = await store.add("send_email", {"to": "a@example.com"}, priority=3)

        task = await store.get(added.id)

        assert task.id == added.id
        assert task.name == "send_email"
        assert task.payload == {"to": "a@example.com"}
        assert task.status is TaskStatus.PENDING
        assert task.priority == 3
        assert task.attempts == 0
        assert task.lease_expires_at is None

    async def test_claiming_leases_the_task(self, store: TaskStore) -> None:
        await store.add("send_email", {})

        claimed = await store.claim_next(lease_seconds=30)

        assert claimed is not None
        assert claimed.status is TaskStatus.CLAIMED
        assert claimed.attempts == 1
        assert claimed.lease_expires_at is not None
        assert claimed.lease_expires_at > datetime.now(UTC)

    async def test_an_empty_queue_claims_nothing(self, store: TaskStore) -> None:
        assert await store.claim_next() is None

    async def test_a_leased_task_is_not_claimed_twice(self, store: TaskStore) -> None:
        await store.add("send_email", {})

        assert await store.claim_next(lease_seconds=30) is not None
        assert await store.claim_next(lease_seconds=30) is None

    async def test_concurrent_claimers_never_share_a_task(
        self, store: TaskStore
    ) -> None:
        for index in range(10):
            await store.add(f"task-{index}", {})

        claimed = await asyncio.gather(
            *(store.claim_next(lease_seconds=30) for _ in range(30))
        )

        ids = [task.id for task in claimed if task is not None]
        assert len(ids) == 10
        assert len(set(ids)) == 10

    async def test_priority_then_age_decides_who_goes_first(
        self, store: TaskStore
    ) -> None:
        past = datetime.now(UTC) - timedelta(minutes=5)
        await store.add("old-low", {}, priority=0, run_after=past)
        await store.add("new-high", {}, priority=5)
        await store.add("old-high", {}, priority=5, run_after=past)

        order = []
        while (task := await store.claim_next(lease_seconds=30)) is not None:
            order.append(task.name)

        assert order == ["old-high", "new-high", "old-low"]

    async def test_a_deferred_task_waits_for_its_time(self, store: TaskStore) -> None:
        await store.add("later", {}, run_after=datetime.now(UTC) + timedelta(hours=1))

        assert await store.claim_next() is None

    async def test_a_lapsed_lease_makes_the_task_claimable_again(
        self, store: TaskStore
    ) -> None:
        added = await store.add("send_email", {})
        first = await store.claim_next(lease_seconds=0.05)
        assert first is not None

        await asyncio.sleep(0.06)
        second = await store.claim_next(lease_seconds=30)

        assert second is not None
        assert second.id == added.id
        assert second.attempts == 2

    async def test_a_lapsed_lease_on_a_running_task_is_reclaimed(
        self, store: TaskStore
    ) -> None:
        added = await store.add("send_email", {})
        claimed = await store.claim_next(lease_seconds=0.05)
        assert claimed is not None
        await store.mark_started(added.id)

        await asyncio.sleep(0.06)
        reclaimed = await store.claim_next(lease_seconds=30)

        assert reclaimed is not None
        assert reclaimed.id == added.id

    async def test_extending_a_lease_keeps_the_task(self, store: TaskStore) -> None:
        added = await store.add("send_email", {})
        await store.claim_next(lease_seconds=0.05)

        expires_at = await store.extend_lease(added.id, lease_seconds=30)
        await asyncio.sleep(0.06)

        assert expires_at > datetime.now(UTC)
        assert await store.claim_next() is None

    async def test_the_lifecycle_is_recorded(self, store: TaskStore) -> None:
        added = await store.add("send_email", {})
        await store.claim_next(lease_seconds=30)
        await store.mark_started(added.id)

        assert (await store.get(added.id)).status is TaskStatus.RUNNING

        await store.mark_finished(added.id)
        finished = await store.get(added.id)

        assert finished.status is TaskStatus.SUCCEEDED
        assert finished.error is None
        assert finished.lease_expires_at is None

    async def test_a_failure_keeps_its_reason(self, store: TaskStore) -> None:
        added = await store.add("send_email", {})
        await store.claim_next(lease_seconds=30)

        await store.mark_finished(added.id, error="ValueError: nope")
        task = await store.get(added.id)

        assert task.status is TaskStatus.FAILED
        assert task.error == "ValueError: nope"

    async def test_marking_a_start_twice_is_allowed(self, store: TaskStore) -> None:
        added = await store.add("send_email", {})
        await store.claim_next(lease_seconds=30)
        await store.mark_started(added.id)
        await store.mark_started(added.id)

        assert (await store.get(added.id)).status is TaskStatus.RUNNING

    async def test_transitions_are_enforced(self, store: TaskStore) -> None:
        added = await store.add("send_email", {})

        with pytest.raises(TaskStateError):
            await store.mark_started(added.id)

        await store.claim_next(lease_seconds=30)
        await store.mark_finished(added.id)

        with pytest.raises(TaskStateError):
            await store.mark_finished(added.id, error="too late")

        with pytest.raises(TaskStateError):
            await store.extend_lease(added.id)

    async def test_an_unknown_task_is_reported_as_missing(
        self, store: TaskStore
    ) -> None:
        missing = uuid.uuid4()

        with pytest.raises(TaskNotFoundError):
            await store.get(missing)

        with pytest.raises(TaskNotFoundError):
            await store.mark_started(missing)

        with pytest.raises(TaskNotFoundError):
            await store.mark_finished(missing)

        with pytest.raises(TaskNotFoundError):
            await store.extend_lease(missing)
