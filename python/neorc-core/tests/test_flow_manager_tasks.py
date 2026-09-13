# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager for flows: publishing tasks, delivering them, and events."""

import asyncio
import uuid

import pytest

from neorc_core import (
    EventKind,
    FlowManager,
    FlowNotFoundError,
    InvalidValueError,
    PayloadTooLargeError,
    ResolutionError,
    RunStateError,
    RunStatus,
    TaskStatus,
)
from neorc_core._runs import sub_run_id_for, task_id_for
from neorc_core._values import MAX_PAYLOAD_BYTES, JsonValue
from neorc_core.flows import Address
from neorc_core.local import MemoryStore, MemoryTaskNotifier

FLOW: JsonValue = {
    "name": "f",
    "version": "1.0.0",
    "inputs": {"word": "string"},
    "steps": {
        "first": {
            "handler": "tasks:first",
            "params": {"word": "inputs.word", "id": "neorc.task_id"},
            "fixed_params": {"pad": "*"},
        },
        "each": {
            "fan_out": {"over": "tasks.first"},
            "steps": {
                "second": {
                    "queue": "other",
                    "handler": "tasks:second",
                    "params": {
                        "item": "neorc.item",
                        "index": "neorc.index",
                        "run": "neorc.flow_run_id",
                        "tries": "neorc.attempts",
                    },
                },
            },
        },
        "call": {"flow": "g", "params": {"text": "inputs.word"}},
    },
}

CALLED: JsonValue = {
    "name": "g",
    "version": "1.0.0",
    "inputs": {"text": "string"},
    "output": "tasks.echo",
    "steps": {"echo": {"handler": "tasks:echo", "params": {"text": "inputs.text"}}},
}

FIRST = Address("first")


@pytest.fixture
def flows() -> FlowManager:
    return FlowManager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )


async def started(flows: FlowManager, word: str = "red") -> uuid.UUID:
    await flows.upload_flows([FLOW, CALLED])
    run = await flows.start_run("f", {"word": word})
    return run.id


async def finish(flows: FlowManager, queue: str, result: JsonValue) -> uuid.UUID:
    delivery = await flows.pick_next_task(queue, timeout=0)
    assert delivery is not None
    await flows.report_started(delivery.task.id)
    await flows.report_finished(delivery.task.id, result=result)
    return delivery.task.id


async def test_a_published_task_takes_its_definition_from_the_flow(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)

    task = await flows.publish_task(run_id, FIRST)

    assert task.id == task_id_for(run_id, FIRST)
    assert task.handler == "tasks:first"
    assert task.queue == "default"
    assert {name: str(ref) for name, ref in task.params.items()} == {
        "word": "inputs.word",
        "id": "neorc.task_id",
    }
    assert task.fixed_params == {"pad": "*"}
    assert task.status is TaskStatus.PENDING


async def test_a_delivery_fills_in_references_and_metadata(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)
    await flows.publish_task(run_id, FIRST)

    delivery = await flows.pick_next_task("default", timeout=0)

    assert delivery is not None
    assert delivery.inputs == {
        "word": "red",
        "id": str(task_id_for(run_id, FIRST)),
        "pad": "*",
    }


async def test_a_delivery_in_a_fan_out_carries_its_branch(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)
    await flows.publish_task(run_id, FIRST)
    await finish(flows, "default", ["red", "blue"])
    second = Address("second", (("each", 2),))

    await flows.publish_task(run_id, second)
    delivery = await flows.pick_next_task("other", timeout=0)

    assert delivery is not None
    # neorc.attempts is the worker's to fill in.
    assert delivery.inputs == {"item": "blue", "index": 2, "run": str(run_id)}


async def test_publishing_the_same_address_again_changes_nothing(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)
    first = await flows.publish_task(run_id, FIRST)
    await flows.pick_next_task("default", timeout=0)

    again = await flows.publish_task(run_id, FIRST)

    assert again.id == first.id
    assert again.status is TaskStatus.CLAIMED


async def test_a_task_whose_inputs_are_not_there_yet_is_rejected(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)

    with pytest.raises(RunStateError, match="not available"):
        await flows.publish_task(run_id, Address("second", (("each", 1),)))


@pytest.mark.parametrize(
    "address",
    [Address("nothing"), Address("call"), Address("each"), Address("second")],
    ids=str,
)
async def test_an_address_that_is_not_a_task_of_the_flow_is_rejected(
    flows: FlowManager, address: Address
) -> None:
    run_id = await started(flows)

    with pytest.raises(InvalidValueError):
        await flows.publish_task(run_id, address)


async def test_no_task_is_published_into_an_inactive_run(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)
    await flows.cancel_run(run_id)

    with pytest.raises(RunStateError):
        await flows.publish_task(run_id, FIRST)


async def test_a_payload_over_the_limit_is_rejected_at_publish(
    flows: FlowManager,
) -> None:
    run_id = await started(flows, word="x" * MAX_PAYLOAD_BYTES)

    with pytest.raises(PayloadTooLargeError):
        await flows.publish_task(run_id, FIRST)

    assert await flows.pick_next_task("default", timeout=0) is None


async def test_a_fan_out_over_something_not_a_list_cannot_publish(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)
    await flows.publish_task(run_id, FIRST)
    await finish(flows, "default", "not a list")

    with pytest.raises(ResolutionError):
        await flows.publish_task(run_id, Address("second", (("each", 1),)))


async def test_a_waiting_worker_is_woken_by_a_publish(flows: FlowManager) -> None:
    run_id = await started(flows)

    async def publish_shortly() -> None:
        await asyncio.sleep(0.05)
        await flows.publish_task(run_id, FIRST)

    loop = asyncio.get_running_loop()
    begun = loop.time()
    async with asyncio.TaskGroup() as group:
        group.create_task(publish_shortly())
        delivery = await flows.pick_next_task("default", timeout=5)

    assert delivery is not None
    assert loop.time() - begun < 2


async def test_a_worker_on_another_queue_keeps_waiting(flows: FlowManager) -> None:
    run_id = await started(flows)
    await flows.publish_task(run_id, FIRST)

    assert await flows.pick_next_task("other", timeout=0.1) is None


async def test_a_task_of_an_inactive_run_is_refused_its_start(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)
    await flows.publish_task(run_id, FIRST)
    delivery = await flows.pick_next_task("default", timeout=0)
    assert delivery is not None
    await flows.cancel_run(run_id)

    with pytest.raises(RunStateError):
        await flows.report_started(delivery.task.id)


async def test_a_result_is_recorded_and_referenced_downstream(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)
    await flows.publish_task(run_id, FIRST)

    task_id = await finish(flows, "default", ["red"])

    task = await flows.get_task(task_id)
    assert task.status is TaskStatus.SUCCEEDED
    assert task.result == ["red"]
    state = await flows.run_state(run_id)
    assert state.steps[FIRST].value == ["red"]


@pytest.mark.parametrize(
    "result",
    [
        {"$secret": 1},
        {"when": {"$datetime": "yesterday"}},
        "x" * MAX_PAYLOAD_BYTES,
    ],
    ids=["reserved-key", "bad-datetime", "too-large"],
)
async def test_an_invalid_result_fails_the_task(
    flows: FlowManager, result: JsonValue
) -> None:
    run_id = await started(flows)
    await flows.publish_task(run_id, FIRST)

    task_id = await finish(flows, "default", result)

    task = await flows.get_task(task_id)
    assert task.status is TaskStatus.FAILED
    assert task.error is not None and task.error.startswith("invalid result")
    assert task.result is None


async def test_a_sub_flow_run_starts_with_its_inputs_resolved(
    flows: FlowManager,
) -> None:
    run_id = await started(flows)
    call = Address("call")

    sub_run = await flows.start_sub_run(run_id, call)
    again = await flows.start_sub_run(run_id, call)

    assert sub_run.id == sub_run_id_for(run_id, call) == again.id
    assert sub_run.flow == "g"
    assert sub_run.inputs == {"text": "red"}
    assert sub_run.root_id == run_id


async def test_a_sub_flow_run_needs_its_called_flow(flows: FlowManager) -> None:
    await flows.upload_flows([CALLED])
    caller: JsonValue = {
        "name": "caller",
        "version": "1.0.0",
        "steps": {"call": {"flow": "g", "fixed_params": {"text": "x"}}},
    }
    await flows.upload_flows([caller])
    run = await flows.start_run("caller", {})

    sub_run = await flows.start_sub_run(run.id, Address("call"))
    await flows.cancel_run(run.id)

    assert sub_run.status is RunStatus.ACTIVE
    with pytest.raises(RunStateError):
        await flows.start_sub_run(run.id, Address("call"))
    with pytest.raises(FlowNotFoundError):
        await flows.get_flow("nothing")


async def test_events_are_waited_for_and_read_in_order(flows: FlowManager) -> None:
    run_id = await started(flows)
    (started_event,) = await flows.wait_for_events(0, timeout=0)

    async def finish_shortly() -> None:
        await asyncio.sleep(0.05)
        await flows.publish_task(run_id, FIRST)
        await finish(flows, "default", ["red"])

    async with asyncio.TaskGroup() as group:
        group.create_task(finish_shortly())
        events = await flows.wait_for_events(started_event.sequence, timeout=5)

    assert started_event.kind is EventKind.RUN_STARTED
    assert [(e.run_id, e.kind) for e in events] == [(run_id, EventKind.TASK_FINISHED)]


async def test_waiting_for_events_gives_up_at_the_deadline(
    flows: FlowManager,
) -> None:
    assert await flows.wait_for_events(0, timeout=0.1) == []


async def test_a_run_succeeds_with_its_sub_flows_output(flows: FlowManager) -> None:
    run_id = await started(flows)
    sub_run = await flows.start_sub_run(run_id, Address("call"))
    await flows.publish_task(sub_run.id, Address("echo"))
    await finish(flows, "default", "red")

    output = (await flows.get_flow("g")).definition.output
    succeeded = await flows.succeed_run(sub_run.id, output)

    assert succeeded.output == "red"
    state = await flows.run_state(run_id)
    assert state.steps[Address("call")].value == "red"


async def test_size_is_counted_in_utf_8_bytes_as_the_value_travels(
    flows: FlowManager,
) -> None:
    """Two bytes a character: well under the limit, though not as escaped ASCII."""
    wide = "é" * (MAX_PAYLOAD_BYTES // 4)
    run_id = await started(flows, word=wide)

    await flows.publish_task(run_id, FIRST)
    task_id = await finish(flows, "default", wide)

    assert (await flows.get_task(task_id)).status is TaskStatus.SUCCEEDED
