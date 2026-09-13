# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What every ``QueueClient`` must do, seen from a publisher and a worker."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from neorc_core._errors import TaskNotFoundError, TaskStateError
from neorc_core._task import TaskStatus
from neorc_core.ports._queue_client import QueueClient


class QueueClientContract:
    """Subclass and provide a ``queue_client`` fixture on an empty queue."""

    @pytest.fixture
    def queue_client(self) -> QueueClient:
        raise NotImplementedError(
            "a QueueClientContract subclass provides `queue_client`"
        )

    async def test_a_published_task_starts_pending(
        self, queue_client: QueueClient
    ) -> None:
        task_id = await queue_client.publish("send_email", {"to": "a@example.com"})

        assert await queue_client.get_status(task_id) is TaskStatus.PENDING

    async def test_a_worker_picks_up_what_a_publisher_sent(
        self, queue_client: QueueClient
    ) -> None:
        task_id = await queue_client.publish(
            "send_email", {"to": "a@example.com"}, priority=2
        )

        picked = await queue_client.pick_next_task(timeout=1, lease_seconds=30)

        assert picked is not None
        assert picked.id == task_id
        assert picked.name == "send_email"
        assert picked.payload == {"to": "a@example.com"}
        assert picked.priority == 2
        assert picked.status is TaskStatus.CLAIMED
        assert picked.attempts == 1
        assert picked.lease_expires_at is not None
        assert picked.lease_expires_at > datetime.now(UTC)

    async def test_an_empty_queue_answers_nothing(
        self, queue_client: QueueClient
    ) -> None:
        assert await queue_client.pick_next_task(timeout=0) is None

    async def test_a_leased_task_is_not_handed_to_a_second_worker(
        self, queue_client: QueueClient
    ) -> None:
        await queue_client.publish("send_email", {})

        assert await queue_client.pick_next_task(timeout=0) is not None
        assert await queue_client.pick_next_task(timeout=0) is None

    async def test_concurrent_picks_never_share_a_task(
        self, queue_client: QueueClient
    ) -> None:
        for _ in range(5):
            await queue_client.publish("send_email", {})

        picked = await asyncio.gather(
            *(queue_client.pick_next_task(timeout=0) for _ in range(20))
        )

        ids = [task.id for task in picked if task is not None]
        assert len(ids) == 5
        assert len(set(ids)) == 5

    async def test_higher_priority_goes_first(self, queue_client: QueueClient) -> None:
        await queue_client.publish("low", {}, priority=0)
        await queue_client.publish("high", {}, priority=10)

        first = await queue_client.pick_next_task(timeout=0)

        assert first is not None
        assert first.name == "high"

    async def test_a_deferred_task_is_not_picked_before_its_time(
        self, queue_client: QueueClient
    ) -> None:
        run_after = datetime.now(UTC) + timedelta(hours=2)

        task_id = await queue_client.publish("later", {}, run_after=run_after)

        assert await queue_client.pick_next_task(timeout=0) is None
        assert await queue_client.get_status(task_id) is TaskStatus.PENDING

    async def test_a_lapsed_lease_returns_the_task_to_the_queue(
        self, queue_client: QueueClient
    ) -> None:
        task_id = await queue_client.publish("send_email", {})
        first = await queue_client.pick_next_task(timeout=0, lease_seconds=0.05)
        assert first is not None

        await asyncio.sleep(0.1)
        second = await queue_client.pick_next_task(timeout=0)

        assert second is not None
        assert second.id == task_id
        assert second.attempts == 2

    async def test_a_heartbeat_holds_the_task(self, queue_client: QueueClient) -> None:
        task_id = await queue_client.publish("send_email", {})
        await queue_client.pick_next_task(timeout=0, lease_seconds=0.2)

        expires_at = await queue_client.extend_lease(task_id, lease_seconds=30)
        await asyncio.sleep(0.3)

        assert expires_at > datetime.now(UTC)
        assert await queue_client.pick_next_task(timeout=0) is None

    async def test_the_whole_lifecycle(self, queue_client: QueueClient) -> None:
        task_id = await queue_client.publish("send_email", {})
        await queue_client.pick_next_task(timeout=1)

        await queue_client.report_started(task_id)
        assert await queue_client.get_status(task_id) is TaskStatus.RUNNING

        await queue_client.report_started(task_id)
        assert await queue_client.get_status(task_id) is TaskStatus.RUNNING

        await queue_client.report_finished(task_id)
        assert await queue_client.get_status(task_id) is TaskStatus.SUCCEEDED

    async def test_a_reported_failure_fails_the_task(
        self, queue_client: QueueClient
    ) -> None:
        task_id = await queue_client.publish("send_email", {})
        await queue_client.pick_next_task(timeout=1)

        await queue_client.report_finished(task_id, error="ValueError: nope")

        assert await queue_client.get_status(task_id) is TaskStatus.FAILED

    async def test_a_refused_transition_reads_as_a_state_error(
        self, queue_client: QueueClient
    ) -> None:
        task_id = await queue_client.publish("send_email", {})

        with pytest.raises(TaskStateError):
            await queue_client.report_started(task_id)

        await queue_client.pick_next_task(timeout=0)
        await queue_client.report_finished(task_id)

        with pytest.raises(TaskStateError):
            await queue_client.report_finished(task_id, error="too late")

        with pytest.raises(TaskStateError):
            await queue_client.extend_lease(task_id)

    async def test_a_missing_task_reads_as_missing(
        self, queue_client: QueueClient
    ) -> None:
        missing = uuid.uuid4()

        with pytest.raises(TaskNotFoundError):
            await queue_client.get_status(missing)

        with pytest.raises(TaskNotFoundError):
            await queue_client.report_started(missing)

        with pytest.raises(TaskNotFoundError):
            await queue_client.extend_lease(missing)

    async def test_a_waiting_pick_returns_as_soon_as_a_task_is_published(
        self, queue_client: QueueClient
    ) -> None:
        async def publish_shortly() -> None:
            await asyncio.sleep(0.05)
            await queue_client.publish("send_email", {})

        loop = asyncio.get_running_loop()
        started = loop.time()
        async with asyncio.TaskGroup() as group:
            group.create_task(publish_shortly())
            picked = await queue_client.pick_next_task(timeout=5)

        assert picked is not None
        assert loop.time() - started < 2
