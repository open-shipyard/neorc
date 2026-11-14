# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A task's form that reads the task's inputs: a list of items, and buttons.

The inputs seed the form's data under /inputs, read only. A List repeats a
template once per item, a control in it submits one value per item, and a
Button completes the task: docs/architecture/forms.md.
"""

import asyncio
import copy
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from neorc_core import (
    FlowDefinitionError,
    InvalidValueError,
    Manager,
    QueueKind,
    RunStatus,
    TaskStatus,
)
from neorc_core._values import JsonValue
from neorc_core.flows import Address, parse_flow, read_flows
from neorc_core.forms import (
    CATALOG_ID,
    VERSION,
    check_form_result,
    form_fields,
    form_problems,
)
from neorc_core.local import LocalCluster, MemoryStore, MemoryTaskNotifier
from neorc_core.testing.examples import wait_for_run

EXAMPLE = Path(__file__).parents[4] / "examples" / "review_loop"
QUEUES = {"default": QueueKind.WORKER, "editors": QueueKind.EXTERNAL}

ISSUES: list[JsonValue] = [
    {
        "n": 1,
        "severity": "blocker",
        "title": "Crash on empty input",
        "detail": "a.py:9",
    },
    {"n": 2, "severity": "major", "title": "No test", "detail": "tests/test_a.py"},
    {"n": 3, "severity": "minor", "title": "Typo", "detail": "a.py:2"},
]
REVIEW: dict[str, JsonValue] = {
    "pr_url": "https://github.com/o/r/pull/7",
    "summary": "Three issues.",
    "issues": ISSUES,
}
INPUTS: dict[str, JsonValue] = {"review": REVIEW}


def triage() -> list[Any]:
    form: list[Any] = json.loads((EXAMPLE / "flows" / "triage.form.json").read_text())
    return form


def components(form: list[Any]) -> list[dict[str, Any]]:
    listed: list[dict[str, Any]] = form[1]["updateComponents"]["components"]
    return listed


def component(form: list[Any], cid: str) -> dict[str, Any]:
    return next(c for c in components(form) if c["id"] == cid)


def changed(change: Callable[[list[Any]], object]) -> list[Any]:
    form = copy.deepcopy(triage())
    change(form)
    return form


def placed(form: list[Any], parent: str, *added: dict[str, Any]) -> None:
    """Add components to ``form``, the first under ``parent``."""
    components(form).extend(added)
    component(form, parent)["children"].append(added[0]["id"])


@pytest.fixture(autouse=True)
def own_tasks_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each example has a ``tasks`` module: import this one's, not another's."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "tasks", raising=False)
    yield
    sys.modules.pop("tasks", None)


# What a task's form may read and hold.


def test_the_examples_form_is_within_the_subset() -> None:
    assert form_problems(triage(), None, params=["review"]) == []


def test_a_control_in_a_template_submits_a_list_and_buttons_a_string() -> None:
    assert form_fields(triage()) == {"decision": "list of strings", "verdict": "string"}


def test_the_ui_is_tested_on_the_examples_form() -> None:
    """``ts/neorc-ui/src/forms`` keeps a copy of the form, for its own tests."""
    copy = Path(__file__).parents[4] / "ts/neorc-ui/src/forms/triage.form.json"

    assert copy.read_text() == (EXAMPLE / "flows/triage.form.json").read_text()


REFUSED: list[tuple[str, Callable[[list[Any]], object], str]] = [
    (
        "a list from outside the inputs",
        lambda f: component(f, "issues")["children"].update(path="/issues"),
        "form.issues: a path from the root reads the task's inputs: "
        "'/inputs/<input>', and below it",
    ),
    (
        "a list of an input the task does not have",
        lambda f: component(f, "issues")["children"].update(path="/inputs/found"),
        "form.issues: the task has no input 'found'",
    ),
    (
        "a list's path relative to nothing",
        lambda f: component(f, "issues")["children"].update(path="issues"),
        "form.issues: children must be a list of component ids, or "
        "{'componentId': ..., 'path': ...}",
    ),
    (
        "a template that is not there",
        lambda f: component(f, "issues")["children"].update(componentId="nowhere"),
        "form.issues: no component with id 'nowhere'",
    ),
    (
        "a template on a Column",
        lambda f: component(f, "issue_body").update(
            children={"componentId": "issue_head", "path": "/inputs/review/issues"}
        ),
        "form.issue_body: children must be a list of component ids",
    ),
    (
        "a list in a list's template",
        lambda f: placed(
            f,
            "issue_body",
            {
                "id": "inner",
                "component": "List",
                "children": {"componentId": "deep", "path": "/inputs/review/issues"},
            },
            {"id": "deep", "component": "Text", "text": "x"},
        ),
        "form.inner: a List repeating a template inside another List's template "
        "is left out",
    ),
    (
        "a relative text outside a template",
        lambda f: component(f, "summary").update(text={"path": "summary"}),
        "form.summary: a path without a leading slash is relative to an item",
    ),
    (
        "a text that calls a function",
        lambda f: component(f, "summary").update(
            text={"call": "formatString", "args": {"value": "${/inputs}"}}
        ),
        "form.summary: text must be a string or {'path': ...}",
    ),
    (
        "a text from no input",
        lambda f: component(f, "summary").update(text={"path": "/verdict"}),
        "form.summary: a path from the root reads the task's inputs",
    ),
    (
        "a control in a template bound from the root",
        lambda f: component(f, "decision").update(value={"path": "/decision"}),
        "form.decision: value must be {'path': '<field>'}, a field of the item",
    ),
    (
        "a control in a template bound below the item",
        lambda f: component(f, "decision").update(value={"path": "a/decision"}),
        "form.decision: value must be {'path': '<field>'}, a field of the item",
    ),
    (
        "a control that writes the inputs",
        lambda f: placed(
            f,
            "root",
            {
                "id": "note",
                "component": "TextField",
                "label": "Note",
                "value": {"path": "/inputs"},
            },
        ),
        "form.note: the task's inputs at /inputs are read only",
    ),
    (
        "a choice shown as a dial",
        lambda f: component(f, "decision").update(displayStyle="dial"),
        "form.decision: displayStyle must be one of checkbox, chips",
    ),
    (
        "a card with no child",
        lambda f: component(f, "issue").update(child="nowhere"),
        "form.issue: no component with id 'nowhere'",
    ),
    (
        "a Row's alignment",
        lambda f: component(f, "verdicts").update(justify="spaceBetween"),
        "form.verdicts: Row has no 'justify' in the subset",
    ),
    (
        "a List's direction",
        lambda f: component(f, "issues").update(direction="horizontal"),
        "form.issues: List has no 'direction' in the subset",
    ),
    (
        "a button labelled by no text",
        lambda f: component(f, "ready").update(child="issue_body"),
        "form.ready: child must be the id of a Text with a string text",
    ),
    (
        "a button that calls a function",
        lambda f: component(f, "ready").update(
            action={"functionCall": {"call": "openUrl", "args": {"url": "x"}}}
        ),
        "form.ready: action must be {'event': {'name': ..., 'context': "
        "{'<field>': '<value>'}}}, one field set to a string",
    ),
    (
        "a button that sets two fields",
        lambda f: component(f, "ready")["action"]["event"]["context"].update(
            urgent="yes"
        ),
        "form.ready: action must be {'event'",
    ),
    (
        "a button that sets a value from the data",
        lambda f: component(f, "ready")["action"]["event"]["context"].update(
            verdict={"path": "/decision"}
        ),
        "form.ready: action must be {'event'",
    ),
    (
        "buttons that set two fields",
        lambda f: component(f, "ready")["action"]["event"].update(
            context={"done": "yes"}
        ),
        "form.ready: sets 'done', and every Button sets the same field, here 'verdict'",
    ),
    (
        "two buttons that set one value",
        lambda f: component(f, "ready")["action"]["event"]["context"].update(
            verdict="rework"
        ),
        "form.ready: rework sets 'verdict' to 'rework' already",
    ),
    (
        "a button that sets a control's field",
        lambda f: placed(
            f,
            "root",
            {
                "id": "verdict",
                "component": "TextField",
                "label": "Verdict",
                "value": {"path": "/verdict"},
            },
        ),
        "field 'verdict' already has a control",
    ),
    (
        "a button in a list's template",
        lambda f: placed(
            f,
            "issue_body",
            {
                "id": "again",
                "component": "Button",
                "child": "again_label",
                "action": {"event": {"name": "submit", "context": {"verdict": "x"}}},
            },
            {"id": "again_label", "component": "Text", "text": "Again"},
        ),
        "form.again: a Button inside a List's template is left out",
    ),
    (
        "a button's label placed again",
        lambda f: component(f, "root")["children"].append("ready_label"),
        "form.ready_label: placed more than once",
    ),
    (
        "data to start from for a control in a template",
        lambda f: f.append(
            {
                "version": VERSION,
                "updateDataModel": {
                    "surfaceId": "triage",
                    "path": "/",
                    "value": {"decision": ["fix"]},
                },
            }
        ),
        "form: updateDataModel sets 'decision', which has one value per item of a list",
    ),
    (
        "data to start from for the inputs",
        lambda f: f.append(
            {
                "version": VERSION,
                "updateDataModel": {
                    "surfaceId": "triage",
                    "path": "/",
                    "value": {"inputs": {}},
                },
            }
        ),
        "form: updateDataModel sets 'inputs', which are the task's own",
    ),
]


@pytest.mark.parametrize(
    ("change", "problem"),
    [(change, problem) for _, change, problem in REFUSED],
    ids=[name for name, _, _ in REFUSED],
)
def test_a_tasks_form_outside_the_subset_is_refused_with_the_reason(
    change: Callable[[list[Any]], object], problem: str
) -> None:
    problems = form_problems(changed(change), None, params=["review"])

    assert any(problem in found for found in problems), problems


def test_every_problem_is_reported_at_once() -> None:
    def break_three(form: list[Any]) -> None:
        component(form, "decision").update(displayStyle="dial")
        component(form, "summary").update(text={"path": "/verdict"})
        component(form, "ready").update(child="nowhere")

    problems = form_problems(changed(break_three), None, params=["review"])

    assert "form.decision: displayStyle must be one of checkbox, chips" in problems
    assert "form.summary: a path from the root reads the task's inputs: " in "".join(
        problems
    )
    assert "form.ready: child must be the id of a Text with a string text" in problems


def test_buttons_alone_are_a_form() -> None:
    def ready_or_not(form: list[Any]) -> None:
        component(form, "root")["children"] = ["verdicts"]
        for cid in ("summary", "issues", "issue", "issue_body", "issue_head"):
            components(form).remove(component(form, cid))
        for cid in ("issue_severity", "issue_title", "issue_detail", "decision"):
            components(form).remove(component(form, cid))

    form = changed(ready_or_not)

    assert form_problems(form, None) == []
    assert form_fields(form) == {"verdict": "string"}
    check_form_result("triage", form, {"verdict": "ready"}, {})


# A flow's form fills the run's inputs: it reads none, and has no button.

FLOW_FORM: list[Any] = [
    {"version": VERSION, "createSurface": {"surfaceId": "s", "catalogId": CATALOG_ID}},
    {
        "version": VERSION,
        "updateComponents": {
            "surfaceId": "s",
            "components": [
                {"id": "root", "component": "Column", "children": ["box"]},
                {"id": "box", "component": "Card", "child": "line"},
                {"id": "line", "component": "Row", "children": ["name", "urgent"]},
                {
                    "id": "name",
                    "component": "TextField",
                    "label": "Name",
                    "value": {"path": "/name"},
                },
                {
                    "id": "urgent",
                    "component": "ChoicePicker",
                    "label": "Urgent",
                    "variant": "mutuallyExclusive",
                    "displayStyle": "chips",
                    "value": {"path": "/urgent"},
                    "options": [
                        {"label": "Yes", "value": "yes"},
                        {"label": "No", "value": "no"},
                    ],
                },
            ],
        },
    },
]
FLOW_INPUTS = {"name": "string", "urgent": "string"}


def test_a_flows_form_lays_out_in_rows_and_cards_and_shows_a_choice_as_chips() -> None:
    assert form_problems(FLOW_FORM, FLOW_INPUTS) == []


@pytest.mark.parametrize(
    ("added", "problem"),
    [
        (
            {"id": "said", "component": "Text", "text": {"path": "/inputs/name"}},
            "form.said: text must be a string, or in a task's form {'path': ...} "
            "into the task's inputs",
        ),
        (
            {
                "id": "said",
                "component": "List",
                "children": {"componentId": "x", "path": "/inputs/name"},
            },
            "form.said: a template is for a task's form, which reads the task's inputs",
        ),
        (
            {
                "id": "said",
                "component": "Button",
                "child": "x",
                "action": {"event": {"name": "go", "context": {"name": "x"}}},
            },
            "form.said: a Button is for a task's form, which it completes; the page "
            "starts a run",
        ),
    ],
    ids=["a text bound to data", "a template", "a button"],
)
def test_a_flows_form_reads_no_inputs_and_has_no_button(
    added: dict[str, Any], problem: str
) -> None:
    form = copy.deepcopy(FLOW_FORM)
    placed(form, "root", added, {"id": "x", "component": "Text", "text": "x"})

    assert problem in form_problems(form, FLOW_INPUTS)


# What the form asks of a result: one decision per item, and a verdict.

RESULT: dict[str, Any] = {
    "decision": ["fix", "defer", "discard"],
    "verdict": "ready",
}


@pytest.mark.parametrize("verdict", ["ready", "rework"])
def test_a_decision_per_item_and_a_buttons_value_are_let_through(verdict: str) -> None:
    check_form_result("triage", triage(), {**RESULT, "verdict": verdict}, INPUTS)


def test_a_list_with_no_items_takes_no_decision() -> None:
    check_form_result(
        "triage",
        triage(),
        {"decision": [], "verdict": "ready"},
        {"review": {**REVIEW, "issues": []}},
    )


@pytest.mark.parametrize(
    ("result", "problem"),
    [
        ({"verdict": "ready"}, "no field 'decision'"),
        ({"decision": ["fix", "fix", "fix"]}, "no field 'verdict'"),
        (
            {**RESULT, "verdict": "later"},
            "field 'verdict' is not one of rework, ready",
        ),
        (
            {**RESULT, "decision": "fix"},
            "field 'decision' must be a list of strings, one per item of "
            "/inputs/review/issues",
        ),
        (
            {**RESULT, "decision": ["fix", 2, "fix"]},
            "field 'decision' must be a list of strings",
        ),
        (
            {**RESULT, "decision": ["fix", "fix"]},
            "field 'decision' has 2 values, and /inputs/review/issues has 3 items",
        ),
        (
            {**RESULT, "decision": ["fix", "fix", "fix", "fix"]},
            "field 'decision' has 4 values, and /inputs/review/issues has 3 items",
        ),
        (
            {**RESULT, "decision": ["fix", "later", "fix"]},
            "field 'decision', item 2, is not one of fix, defer, discard",
        ),
        ({**RESULT, "extra": 1}, "the form has no field 'extra'"),
    ],
)
def test_a_result_the_form_does_not_submit_is_refused(
    result: dict[str, JsonValue], problem: str
) -> None:
    with pytest.raises(InvalidValueError, match=problem) as refused:
        check_form_result("triage", triage(), result, INPUTS)

    assert str(refused.value).startswith("triage: ")


@pytest.mark.parametrize(
    "inputs",
    [{}, {"review": "three issues"}, {"review": {**REVIEW, "issues": "none"}}],
    ids=["no input", "an input that is no mapping", "no list there"],
)
def test_a_result_is_refused_when_the_inputs_hold_no_list(
    inputs: dict[str, JsonValue],
) -> None:
    with pytest.raises(
        InvalidValueError, match="the task's inputs have no list at /inputs/review"
    ):
        check_form_result("triage", triage(), RESULT, inputs)


def test_every_broken_field_is_named_at_once() -> None:
    with pytest.raises(InvalidValueError) as refused:
        check_form_result(
            "triage", triage(), {"decision": ["no"] * 3, "verdict": "x"}, INPUTS
        )

    assert "item 1" in str(refused.value)
    assert "item 3" in str(refused.value)
    assert "field 'verdict'" in str(refused.value)


# At upload: the task's form reads inputs the task has.


def triage_flow() -> Any:
    """A flow of two tasks: a worker reviews, and a person triages."""
    return {
        "name": "triage_only",
        "version": "1.0.0",
        "output": "tasks.triage",
        "steps": {
            "review": {"queue": "default", "handler": "tasks:review"},
            "triage": {
                "queue": "editors",
                "handler": "triage-issues",
                "params": {"review": "tasks.review"},
                "form": triage(),
            },
        },
    }


def test_a_tasks_form_reads_the_tasks_own_inputs() -> None:
    content = triage_flow()
    content["steps"]["triage"]["params"] = {"found": "tasks.review"}

    with pytest.raises(FlowDefinitionError) as refused:
        parse_flow(content)

    assert refused.value.problems == [
        "steps.triage.form.summary: the task has no input 'review'",
        "steps.triage.form.issues: the task has no input 'review'",
    ]


def test_a_fixed_param_is_an_input_a_form_reads() -> None:
    content = triage_flow()
    content["steps"]["triage"]["params"] = {}
    content["steps"]["triage"]["fixed_params"] = {"review": REVIEW}

    assert parse_flow(content).name == "triage_only"


# The manager: at every completion, whoever sends it.


@pytest.fixture
def manager() -> Manager:
    return Manager(
        MemoryStore(queues=QUEUES),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )


async def waiting_for_triage(manager: Manager) -> Any:
    """A run whose triage is published, after the review; the triage's task id."""
    await manager.upload_flows([triage_flow()])
    run = await manager.start_run("triage_only", {})
    task = await manager.publish_task(run.id, Address("review"))
    await manager.receive_task("default", timeout=0)
    await manager.report_finished(task.id, result=REVIEW)
    return (await manager.publish_task(run.id, Address("triage"))).id


async def test_a_completion_is_held_to_the_items_of_the_tasks_inputs(
    manager: Manager,
) -> None:
    task_id = await waiting_for_triage(manager)

    with pytest.raises(InvalidValueError, match="has 1 values, and /inputs/review"):
        await manager.complete_task(
            task_id, result={"decision": ["fix"], "verdict": "ready"}
        )
    with pytest.raises(InvalidValueError, match="is not one of rework, ready"):
        await manager.complete_task(task_id, result={**RESULT, "verdict": "done"})

    assert (await manager.get_task(task_id)).status is TaskStatus.PENDING
    completed = await manager.complete_task(task_id, result=RESULT)
    assert (completed.status, completed.result) == (TaskStatus.SUCCEEDED, RESULT)


async def test_a_triage_fails_with_an_error_and_no_decision(manager: Manager) -> None:
    task_id = await waiting_for_triage(manager)

    failed = await manager.complete_task(task_id, error="the review is wrong")

    assert failed.status is TaskStatus.FAILED


# The example, from start to end: Re-work, then Ready.


async def test_the_example_loops_until_a_person_says_ready() -> None:
    verdicts = ["rework", "ready"]
    async with LocalCluster(code_location=EXAMPLE, poll_timeout=0.2) as cluster:
        manager = cluster.manager
        await manager.declare_queue("editors", QueueKind.EXTERNAL)
        await cluster.upload(EXAMPLE / "flows")
        await cluster.start()
        run = await cluster.client.start_run("review_loop", {"change": "Add a retry"})
        async with asyncio.timeout(10):
            while verdicts:
                for delivery in await manager.pending_tasks("editors"):
                    review = delivery.inputs["review"]
                    assert isinstance(review, dict)
                    assert isinstance(review["issues"], list)
                    assert len(review["issues"]) == 3
                    with pytest.raises(InvalidValueError, match="has 0 values"):
                        await manager.complete_task(
                            delivery.task.id,
                            result={"decision": [], "verdict": "ready"},
                        )
                    await manager.complete_task(
                        delivery.task.id,
                        result={
                            "decision": ["fix", "fix", "discard"],
                            "verdict": verdicts.pop(0),
                        },
                    )
                await asyncio.sleep(0.01)
        finished = await wait_for_run(cluster.client, run.id)

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.output == "Ready after 2 rounds: 2 to fix, 0 deferred, 1 discarded."


def test_the_example_reads_in_with_its_form() -> None:
    contents = {content["name"]: content for content in read_flows(EXAMPLE / "flows")}

    assert contents["review_round"]["steps"]["triage"]["form"] == triage()
