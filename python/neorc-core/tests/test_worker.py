# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The worker for flows: startup checks, running handlers, reporting."""

import asyncio
import uuid
from pathlib import Path
from textwrap import dedent

import pytest

from neorc_core import (
    HandlerError,
    Manager,
    RunStatus,
    TaskStatus,
    Worker,
)
from neorc_core._runs import task_id_for
from neorc_core._values import MAX_PAYLOAD_BYTES, MAX_VALUE_DEPTH, JsonValue
from neorc_core._worker import MAX_ERROR_LENGTH
from neorc_core.flows import Address, Outcome
from neorc_core.local import (
    DirectQueueClient,
    MemoryStore,
    MemoryTaskNotifier,
)

WHEN: JsonValue = {"$datetime": "2026-09-13T10:00:00+00:00"}


@pytest.fixture
def flows() -> Manager:
    return Manager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )


def handlers(tmp_path: Path, code: str) -> str:
    """A module of handlers in ``tmp_path`` under a name no other test uses."""
    name = f"handlers_{uuid.uuid4().hex}"
    (tmp_path / f"{name}.py").write_text(dedent(code))
    return name


def flow(task: JsonValue, **inputs: str) -> JsonValue:
    declared: JsonValue = dict(inputs)
    return {
        "name": "f",
        "version": "1.0.0",
        "inputs": declared,
        "steps": {"work": task},
    }


async def published(
    flows: Manager, content: JsonValue, inputs: dict[str, JsonValue]
) -> uuid.UUID:
    await flows.upload_flows([content])
    run = await flows.start_run("f", inputs)
    await flows.publish_task(run.id, Address("work"))
    return run.id


def worker(flows: Manager, tmp_path: Path, **kwargs: float) -> Worker:
    return Worker(
        DirectQueueClient(flows),
        code_location=tmp_path,
        poll_timeout=kwargs.get("poll_timeout", 0.1),
        lease_seconds=kwargs.get("lease_seconds", 5),
    )


async def test_a_handler_runs_with_its_inputs_and_its_result_is_recorded(
    flows: Manager, tmp_path: Path
) -> None:
    module = handlers(
        tmp_path,
        """
        from datetime import timedelta

        def work(word, when, tries, pad):
            return {"padded": word + pad * tries, "later": when + timedelta(hours=1)}
        """,
    )
    task: JsonValue = {
        "handler": f"{module}:work",
        "params": {
            "word": "inputs.word",
            "when": "inputs.when",
            "tries": "neorc.attempts",
        },
        "fixed_params": {"pad": "*"},
    }
    run_id = await published(
        flows,
        flow(task, word="string", when="datetime"),
        {"word": "red", "when": WHEN},
    )
    running = worker(flows, tmp_path)
    await running.prepare()

    delivery = await running.run_once()

    assert delivery is not None
    state = await flows.run_state(run_id)
    assert state.steps[Address("work")].value == {
        "padded": "red*",
        "later": {"$datetime": "2026-09-13T11:00:00+00:00"},
    }


async def test_an_async_handler_is_awaited(flows: Manager, tmp_path: Path) -> None:
    module = handlers(
        tmp_path,
        """
        import asyncio

        async def work():
            await asyncio.sleep(0)
            return 42
        """,
    )
    run_id = await published(flows, flow({"handler": f"{module}:work"}), {})
    running = worker(flows, tmp_path)
    await running.prepare()

    await running.run_once()

    assert (await flows.run_state(run_id)).steps[Address("work")].value == 42


async def test_a_raising_handler_fails_its_task_with_the_reason(
    flows: Manager, tmp_path: Path
) -> None:
    module = handlers(
        tmp_path,
        """
        def work():
            raise ValueError("no words")
        """,
    )
    run_id = await published(flows, flow({"handler": f"{module}:work"}), {})
    running = worker(flows, tmp_path)

    await running.run_once()

    task = await flows.get_task(task_id_for(run_id, Address("work")))
    assert task.status is TaskStatus.FAILED
    assert task.error == "ValueError: no words"


async def test_a_result_that_is_not_a_value_fails_its_task(
    flows: Manager, tmp_path: Path
) -> None:
    module = handlers(
        tmp_path,
        """
        def work():
            return {"words": {"red", "blue"}}
        """,
    )
    run_id = await published(flows, flow({"handler": f"{module}:work"}), {})
    running = worker(flows, tmp_path)

    await running.run_once()

    task = await flows.get_task(task_id_for(run_id, Address("work")))
    assert task.status is TaskStatus.FAILED
    assert task.error is not None and task.error.startswith("invalid result")


@pytest.mark.parametrize("what", ["deep", "big", "surrogate", "long"])
async def test_what_no_report_could_carry_fails_its_task_once(
    flows: Manager, tmp_path: Path, what: str
) -> None:
    """A transport would refuse the report on every lease; the worker fails it."""
    module = handlers(
        tmp_path,
        f"""
        def work():
            if {what!r} == "deep":
                value = []
                for _ in range({MAX_VALUE_DEPTH}):
                    value = [value]
                return value
            if {what!r} == "big":
                return "x" * ({MAX_PAYLOAD_BYTES} + 1)
            if {what!r} == "surrogate":
                raise FileNotFoundError("no such file: '/data/\\udcff'")
            raise ValueError("x" * ({MAX_ERROR_LENGTH} * 2))
        """,
    )
    run_id = await published(flows, flow({"handler": f"{module}:work"}), {})

    await worker(flows, tmp_path).run_once()

    task = await flows.get_task(task_id_for(run_id, Address("work")))
    assert task.status is TaskStatus.FAILED
    assert task.error is not None
    if what in ("deep", "big"):
        assert task.error.startswith("invalid result")
    elif what == "surrogate":
        assert task.error == "FileNotFoundError: no such file: '/data/\ufffd'"
    else:
        assert len(task.error) == MAX_ERROR_LENGTH
    assert await flows.pick_next_task("default", timeout=0) is None


async def test_a_task_whose_run_was_cancelled_is_dropped_unrun(
    flows: Manager, tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    module = handlers(
        tmp_path,
        f"""
        from pathlib import Path

        def work():
            Path({str(marker)!r}).write_text("ran")
        """,
    )
    run_id = await published(flows, flow({"handler": f"{module}:work"}), {})
    client = DirectQueueClient(flows)
    delivery = await client.pick_next_task("default", timeout=0)
    assert delivery is not None
    await flows.cancel_run(run_id)

    await worker(flows, tmp_path).execute(delivery)

    assert not marker.exists()
    task = await flows.get_task(delivery.task.id)
    assert task.status is TaskStatus.FAILED
    assert (await flows.get_run(run_id)).status is RunStatus.CANCELLED


async def test_startup_reports_every_handler_that_does_not_fit(
    flows: Manager, tmp_path: Path
) -> None:
    module = handlers(
        tmp_path,
        """
        def extra(word, missing):
            return word

        def fewer():
            return 1

        def fine(word, optional=1):
            return word

        def anything(**inputs):
            return inputs

        not_callable = 3
        """,
    )
    content: JsonValue = {
        "name": "f",
        "version": "1.0.0",
        "inputs": {"word": "string"},
        "steps": {
            "a": {"handler": f"{module}:extra", "params": {"word": "inputs.word"}},
            "b": {"handler": f"{module}:fewer", "params": {"word": "inputs.word"}},
            "c": {"handler": f"{module}:fine", "params": {"word": "inputs.word"}},
            "d": {"handler": f"{module}:anything", "params": {"word": "inputs.word"}},
            "e": {"handler": f"{module}:not_callable"},
            "g": {"handler": "no_such_module_anywhere:f"},
            "other_queue": {"queue": "elsewhere", "handler": "nowhere:f"},
        },
    }
    await flows.upload_flows([content])

    with pytest.raises(HandlerError) as raised:
        await worker(flows, tmp_path).prepare()

    problems = raised.value.problems
    assert problems == [
        f"a: {module}:extra parameter 'missing' is not an input of the task",
        f"b: {module}:fewer takes no parameter 'word'",
        f"e: {module!r} has no function 'not_callable'",
        problems[3],
    ]
    assert problems[3].startswith("g: cannot import 'no_such_module_anywhere'")


async def test_a_long_task_keeps_its_lease_by_heartbeating(
    flows: Manager, tmp_path: Path
) -> None:
    module = handlers(
        tmp_path,
        """
        import time

        def work():
            time.sleep(0.6)
            return "done"
        """,
    )
    run_id = await published(flows, flow({"handler": f"{module}:work"}), {})
    running = worker(flows, tmp_path, lease_seconds=0.15)
    other = DirectQueueClient(flows)
    stolen: list[object] = []

    async def try_to_steal() -> None:
        await asyncio.sleep(0.4)
        stolen.append(await other.pick_next_task("default", timeout=0))

    async with asyncio.TaskGroup() as group:
        group.create_task(try_to_steal())
        await running.run_once()

    assert stolen == [None]
    state = await flows.run_state(run_id)
    assert state.steps[Address("work")].outcome is Outcome.SUCCEEDED


async def test_stopping_an_idle_worker_cuts_its_poll_short(
    flows: Manager, tmp_path: Path
) -> None:
    running = worker(flows, tmp_path, poll_timeout=30)
    task = asyncio.create_task(running.run())
    await asyncio.sleep(0.1)

    loop = asyncio.get_running_loop()
    begun = loop.time()
    running.stop()
    await asyncio.wait_for(task, timeout=5)

    assert loop.time() - begun < 1


async def test_a_stop_before_the_worker_runs_is_kept(
    flows: Manager, tmp_path: Path
) -> None:
    running = worker(flows, tmp_path, poll_timeout=30)
    task = asyncio.create_task(running.run())
    running.stop()

    await asyncio.wait_for(task, timeout=5)


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("raise SystemExit(3)", "SystemExit: 3"),
        ("x = []\n    x.append(x)\n    return x", "invalid result: InvalidValueError"),
        ("return 10 ** 5000", "invalid result: InvalidValueError"),
    ],
    ids=["system-exit", "circular-result", "huge-int"],
)
async def test_a_handler_cannot_take_the_worker_down(
    flows: Manager, tmp_path: Path, body: str, error: str
) -> None:
    module = handlers(tmp_path, f"def work():\n    {body}\n")
    run_id = await published(flows, flow({"handler": f"{module}:work"}), {})

    await worker(flows, tmp_path).run_once()

    task = await flows.get_task(task_id_for(run_id, Address("work")))
    assert task.status is TaskStatus.FAILED
    assert task.error is not None and task.error.startswith(error)


async def test_a_module_that_fails_to_import_is_reported_with_the_rest(
    flows: Manager, tmp_path: Path
) -> None:
    broken = handlers(tmp_path, "def work(:\n")
    failing = handlers(tmp_path, "raise RuntimeError('no config')\n")
    content: JsonValue = {
        "name": "f",
        "version": "1.0.0",
        "steps": {
            "a": {"handler": f"{broken}:work"},
            "b": {"handler": f"{failing}:work"},
        },
    }
    await flows.upload_flows([content])

    with pytest.raises(HandlerError) as raised:
        await worker(flows, tmp_path).prepare()

    a, b = raised.value.problems
    assert a.startswith(f"a: cannot import {broken!r}")
    assert b.startswith(f"b: cannot import {failing!r}") and "no config" in b
