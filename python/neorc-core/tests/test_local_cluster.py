# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The whole system in one process: runs from start to end, and how they stop."""

import asyncio
import uuid
from pathlib import Path
from textwrap import dedent

import pytest

from neorc_core import HandlerError, RunStatus, TaskStatus
from neorc_core._runs import RunId, sub_run_id_for, task_id_for
from neorc_core._values import MAX_PAYLOAD_BYTES
from neorc_core.flows import Address
from neorc_core.local import DirectQueueClient, LocalCluster, run_local

TIMEOUT = 10


def project(tmp_path: Path, handlers: str, **flows: str) -> tuple[Path, str]:
    """Flow files and a handlers module under a name no other test uses."""
    module = f"handlers_{uuid.uuid4().hex}"
    (tmp_path / f"{module}.py").write_text(dedent(handlers))
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    for name, text in flows.items():
        (flows_dir / f"{name}.yaml").write_text(dedent(text).replace("MODULE", module))
    return flows_dir, module


async def until(condition: object, timeout: float = TIMEOUT) -> None:
    async with asyncio.timeout(timeout):
        while not await condition():  # type: ignore[operator]
            await asyncio.sleep(0.01)


CHAIN = """\
    name: chain
    version: VERSION
    inputs:
      word: string
    output: tasks.shout
    steps:
      slow:
        handler: MODULE:slow
        params: {word: inputs.word}
      shout:
        handler: MODULE:shout
        params: {word: tasks.slow}
    """

HANDLERS = """\
    import time

    def slow(word):
        time.sleep(0.3)
        return word

    def shout(word):
        return word.upper() + "!"
    """


async def test_a_run_goes_from_start_to_its_output(tmp_path: Path) -> None:
    flows_dir, _ = project(tmp_path, HANDLERS, chain=CHAIN.replace("VERSION", "1.0.0"))

    run = await run_local(flows_dir, "chain", {"word": "red"}, timeout=TIMEOUT)

    assert run.status is RunStatus.SUCCEEDED
    assert run.output == "RED!"


async def started_chain(cluster: LocalCluster, flows_dir: Path) -> RunId:
    await cluster.upload(flows_dir)
    await cluster.start()
    run = await cluster.client.start_run("chain", {"word": "red"})
    slow = task_id_for(run.id, Address("slow"))

    async def slow_is_running() -> bool:
        try:
            task = await cluster.manager.get_task(slow)
        except Exception:
            return False
        return task.status is TaskStatus.RUNNING

    await until(slow_is_running)
    return run.id


async def test_uploading_a_new_version_cancels_the_run_and_lets_the_task_finish(
    tmp_path: Path,
) -> None:
    flows_dir, _ = project(tmp_path, HANDLERS, chain=CHAIN.replace("VERSION", "1.0.0"))
    async with LocalCluster(code_location=tmp_path) as cluster:
        run_id = await started_chain(cluster, flows_dir)

        newer = (flows_dir / "chain.yaml").read_text().replace("1.0.0", "1.1.0")
        (flows_dir / "chain.yaml").write_text(newer)
        assert await cluster.upload(flows_dir) == [True]
        run = await cluster.wait(run_id, timeout=TIMEOUT)

        slow = await cluster.manager.get_task(task_id_for(run_id, Address("slow")))

        async def slow_finished() -> bool:
            task = await cluster.manager.get_task(slow.id)
            return task.status is TaskStatus.SUCCEEDED

        await until(slow_finished)
        await asyncio.sleep(0.1)  # a scheduler that would publish has had its turn
        state = await cluster.manager.run_state(run_id)

    assert run.status is RunStatus.CANCELLED
    assert "chain 1.1.0 was uploaded" in (run.reason or "")
    assert Address("shout") not in state.steps


async def test_cancelling_by_hand_stops_a_run(tmp_path: Path) -> None:
    flows_dir, _ = project(tmp_path, HANDLERS, chain=CHAIN.replace("VERSION", "1.0.0"))
    async with LocalCluster(code_location=tmp_path) as cluster:
        run_id = await started_chain(cluster, flows_dir)

        await cluster.client.cancel_run(run_id)
        run = await cluster.wait(run_id, timeout=TIMEOUT)
        await asyncio.sleep(0.5)
        state = await cluster.manager.run_state(run_id)

    assert run.status is RunStatus.CANCELLED
    assert Address("shout") not in state.steps


async def test_a_failure_in_a_sub_flow_fails_the_whole_tree(tmp_path: Path) -> None:
    flows_dir, _ = project(
        tmp_path,
        """\
        def boom(word):
            raise RuntimeError("the widget jammed: " + word)
        """,
        parent="""\
        name: parent
        version: 1.0.0
        steps:
          call:
            flow: child
            fixed_params: {word: red}
        """,
        child="""\
        name: child
        version: 1.0.0
        inputs:
          word: string
        steps:
          boom:
            handler: MODULE:boom
            params: {word: inputs.word}
        """,
    )

    async with LocalCluster(code_location=tmp_path) as cluster:
        await cluster.upload(flows_dir)
        await cluster.start()
        run = await cluster.run("parent", {}, timeout=TIMEOUT)
        child = await cluster.client.get_run(sub_run_id_for(run.id, Address("call")))

    assert child.status is RunStatus.FAILED
    assert child.reason == "boom failed"
    assert run.status is RunStatus.FAILED
    assert run.reason == "boom failed"


async def test_a_loop_past_max_cycles_fails_the_run(tmp_path: Path) -> None:
    flows_dir, _ = project(
        tmp_path,
        """\
        def never():
            return False
        """,
        looping="""\
        name: looping
        version: 1.0.0
        steps:
          repeat:
            loop: {max_cycles: 3, exit_condition: check}
            steps:
              check: {handler: MODULE:never}
        """,
    )

    run = await run_local(flows_dir, "looping", {}, timeout=TIMEOUT)

    assert run.status is RunStatus.FAILED
    assert run.reason == "repeat: still not done after max_cycles, 3 iterations"


async def test_a_request_the_manager_rejects_fails_the_run(tmp_path: Path) -> None:
    flows_dir, _ = project(tmp_path, HANDLERS, chain=CHAIN.replace("VERSION", "1.0.0"))

    run = await run_local(
        flows_dir, "chain", {"word": "x" * MAX_PAYLOAD_BYTES}, timeout=TIMEOUT
    )

    assert run.status is RunStatus.FAILED
    assert run.reason is not None and "PayloadTooLargeError" in run.reason


async def test_a_lost_workers_task_is_handed_on_when_its_lease_lapses(
    tmp_path: Path,
) -> None:
    flows_dir, _ = project(tmp_path, HANDLERS, chain=CHAIN.replace("VERSION", "1.0.0"))
    async with LocalCluster(code_location=tmp_path, poll_timeout=0.2) as cluster:
        await cluster.upload(flows_dir)
        await cluster.start(workers=False)
        run = await cluster.client.start_run("chain", {"word": "red"})

        lost = DirectQueueClient(cluster.manager)
        taken = None
        async with asyncio.timeout(TIMEOUT):
            while taken is None:
                taken = await lost.pick_next_task(
                    "default", timeout=1, lease_seconds=0.2
                )
        await lost.report_started(taken.task.id)
        # ...and the worker holding it is never heard from again.

        await cluster.start_workers()
        finished = await cluster.wait(run.id, timeout=TIMEOUT)
        slow = await cluster.manager.get_task(taken.task.id)

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.output == "RED!"
    assert slow.attempts == 2


async def test_handlers_that_do_not_fit_stop_the_cluster_from_starting(
    tmp_path: Path,
) -> None:
    flows_dir, _ = project(
        tmp_path,
        "def slow():\n    return 1\n\ndef shout(word):\n    return word\n",
        chain=CHAIN.replace("VERSION", "1.0.0"),
    )

    async with LocalCluster(code_location=tmp_path) as cluster:
        await cluster.upload(flows_dir)
        with pytest.raises(HandlerError, match="takes no parameter 'word'"):
            await cluster.start()


async def test_a_crashed_scheduler_is_raised_rather_than_waited_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flows_dir, _ = project(tmp_path, HANDLERS, chain=CHAIN.replace("VERSION", "1.0.0"))

    async def broken(run_id: RunId) -> object:
        raise RuntimeError("the planner's input went missing")

    async with LocalCluster(code_location=tmp_path, poll_timeout=30) as cluster:
        monkeypatch.setattr(cluster.client, "run_state", broken)
        await cluster.upload(flows_dir)
        await cluster.start()
        first = await cluster.client.start_run("chain", {"word": "red"})
        second = await cluster.client.start_run("chain", {"word": "blue"})

        waits = await asyncio.gather(
            cluster.wait(first.id, timeout=TIMEOUT),
            cluster.wait(second.id, timeout=TIMEOUT),
            return_exceptions=True,
        )
        with pytest.raises(RuntimeError, match="went missing"):
            await cluster.wait(first.id, timeout=TIMEOUT)

    assert all(isinstance(result, RuntimeError) for result in waits)


async def test_a_run_that_times_out_is_cancelled_and_its_handler_not_waited_for(
    tmp_path: Path,
) -> None:
    flows_dir, _ = project(
        tmp_path,
        """\
        import asyncio

        async def slow(word):
            await asyncio.sleep(30)

        def shout(word):
            return word
        """,
        chain=CHAIN.replace("VERSION", "1.0.0"),
    )
    cluster = LocalCluster(code_location=tmp_path)
    started: list[RunId] = []
    start_run = cluster.client.start_run

    async def recording(flow: str, inputs: dict[str, str]) -> object:
        run = await start_run(flow, inputs)
        started.append(run.id)
        return run

    cluster.client.start_run = recording  # type: ignore[method-assign,assignment]
    loop = asyncio.get_running_loop()
    begun = loop.time()

    with pytest.raises(TimeoutError):
        async with cluster:
            await cluster.upload(flows_dir)
            await cluster.start()
            await cluster.run("chain", {"word": "red"}, timeout=0.3)

    assert loop.time() - begun < 5
    (run_id,) = started
    assert (await cluster.client.get_run(run_id)).status is RunStatus.CANCELLED
