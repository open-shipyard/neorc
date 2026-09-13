# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The examples under examples/, run in memory, with their outputs asserted."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from neorc_core import RunStatus
from neorc_core._values import JsonValue
from neorc_core.flows import Address
from neorc_core.local import LocalCluster, run_local

EXAMPLES = Path(__file__).parents[3] / "examples"
TIMEOUT = 30


@pytest.fixture(autouse=True)
def own_tasks_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each example has a ``tasks`` module: import this one's, not another's."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "tasks", raising=False)
    yield
    sys.modules.pop("tasks", None)


@pytest.mark.parametrize("flow", ["a", "b"])
async def test_hello_runs_each_single_task_flow(
    flow: str, capfd: pytest.CaptureFixture[str]
) -> None:
    run = await run_local(EXAMPLES / "hello" / "flows", flow, {}, timeout=TIMEOUT)

    assert run.status is RunStatus.SUCCEEDED
    assert run.output is None
    assert capfd.readouterr().out == f"{flow}\n"


WORDPLAY_INPUTS: dict[str, JsonValue] = {
    "sentence": "potato tomate berry watermelon",
    "preferred_letter": "t",
}


async def test_word_picker_keeps_the_words_with_the_preferred_letter() -> None:
    run = await run_local(
        EXAMPLES / "wordplay" / "flows", "word_picker", WORDPLAY_INPUTS, timeout=TIMEOUT
    )

    assert run.status is RunStatus.SUCCEEDED
    assert run.output == ["potato", "tomate"]


async def test_word_picker_rounds_pads_each_word_to_eight_characters() -> None:
    inputs: dict[str, JsonValue] = {
        **WORDPLAY_INPUTS,
        "requested_at": {"$datetime": "2026-09-13T10:00:00+00:00"},
    }

    async with LocalCluster(code_location=EXAMPLES / "wordplay") as cluster:
        await cluster.upload(EXAMPLES / "wordplay" / "flows")
        await cluster.start()
        run = await cluster.run("word_picker_rounds", inputs, timeout=TIMEOUT)
        state = await cluster.client.run_state(run.id)

    assert run.status is RunStatus.SUCCEEDED
    assert run.output is None
    assert state.last((), "runs") == 2
    report = state.steps[Address("report")].value
    assert isinstance(report, dict)
    assert report["words"] == ["potato**", "tomate**"]
    assert report["requested_at"] == inputs["requested_at"]
    assert report["flow_run_id"] == str(run.id)
    assert report["attempts"] == 1
