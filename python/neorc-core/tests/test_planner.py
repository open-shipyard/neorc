# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The planner: plain data in, actions out."""

import importlib.util
from collections.abc import Callable, Mapping
from pathlib import Path
from textwrap import dedent
from types import ModuleType
from typing import Any

import pytest

from neorc_core import _values
from neorc_core._planner import (
    Action,
    FailRun,
    PublishTask,
    StartSubRun,
    SucceedRun,
    plan,
)
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    FlowDefinition,
    Namespace,
    Outcome,
    Reference,
    RunState,
    StepResult,
    load_flow_yaml,
    load_flows,
    resolve,
)

EXAMPLES = Path(__file__).parents[3] / "examples"
WORDPLAY = {f.name: f for f in load_flows(EXAMPLES / "wordplay" / "flows")}
PICKER = WORDPLAY["word_picker"]
ROUNDS = WORDPLAY["word_picker_rounds"]


def ok(value: JsonValue = None) -> StepResult:
    return StepResult(Outcome.SUCCEEDED, value)


RUNNING = StepResult(Outcome.RUNNING)
FAILED = StepResult(Outcome.FAILED)


def at(step: str, *scope: tuple[str, int]) -> Address:
    return Address(step, tuple(scope))


def flow(steps: str) -> FlowDefinition:
    return load_flow_yaml(
        "name: f\nversion: 1.0.0\nsteps:\n"
        + "".join(f"  {line}\n" for line in dedent(steps).splitlines())
    )


def published(actions: list[Action]) -> list[str]:
    """The addresses of the tasks the actions publish, as text."""
    assert all(isinstance(action, PublishTask) for action in actions), actions
    return [
        str(action.address) for action in actions if isinstance(action, PublishTask)
    ]


def state(steps: Mapping[Address, StepResult]) -> RunState:
    return RunState({}, dict(steps))


def picker(steps: Mapping[Address, StepResult]) -> RunState:
    """word_picker's state, with its inputs."""
    inputs: dict[str, JsonValue] = {"sentence": "a: b c d", "preferred_letter": "b"}
    return RunState(inputs, dict(steps))


def load_handlers(path: Path) -> Callable[[str], Callable[..., Any]]:
    spec = importlib.util.spec_from_file_location(f"handlers_{path.parent.name}", path)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda handler: getattr(module, handler.partition(":")[2])


def run_to_the_end(
    definition: FlowDefinition,
    handlers: Callable[[str], Callable[..., Any]],
    inputs: Mapping[str, JsonValue],
) -> tuple[RunState, Action]:
    """Apply every action at once, running tasks and sub-flows, until the run ends.

    Values cross the JSON boundary the way a worker's do: handlers get them
    decoded, and their results are encoded.
    """
    current = RunState(inputs, {})
    for _ in range(1000):
        actions = plan(definition, current)
        assert actions, "a run that is not over always has something to do"
        if isinstance(actions[0], SucceedRun | FailRun):
            assert len(actions) == 1
            return current, actions[0]
        steps = dict(current.steps)
        for action in actions:
            assert isinstance(action, PublishTask | StartSubRun)
            arguments: dict[str, JsonValue] = dict(action.fixed_params)
            # What the manager and the worker fill in.
            filled_in: dict[str, JsonValue] = {
                "flow_run_id": "run-1",
                "task_id": str(action.address),
                "attempts": 1,
            }
            for name, reference in action.params.items():
                if reference.namespace is Namespace.NEORC and (
                    reference.name in filled_in
                ):
                    arguments[name] = filled_in[reference.name]
                    continue
                resolved = resolve(definition, current, action.address, reference)
                assert resolved is not None, (action.address, reference)
                arguments[name] = resolved.value
            if isinstance(action, StartSubRun):
                steps[action.address] = sub_run(action.flow, handlers, arguments)
            else:
                function = handlers(action.handler)
                result = function(**_values.decode(arguments))
                steps[action.address] = ok(_values.encode(result))
        current = RunState(inputs, steps)
    raise AssertionError("the run did not settle")


def sub_run(
    flow_name: str,
    handlers: Callable[[str], Callable[..., Any]],
    inputs: Mapping[str, JsonValue],
) -> StepResult:
    """Run a sub-flow to its end, as the scheduler would a child run."""
    child = WORDPLAY[flow_name]
    final, ending = run_to_the_end(child, handlers, inputs)
    if isinstance(ending, FailRun):
        return FAILED
    assert isinstance(ending, SucceedRun)
    if ending.output is None:
        return ok()
    resolved = resolve(child, final, None, ending.output)
    assert resolved is not None
    return ok(resolved.value)


def test_word_picker_runs_to_the_end() -> None:
    handlers = load_handlers(EXAMPLES / "wordplay" / "tasks.py")

    final, ending = run_to_the_end(
        PICKER,
        handlers,
        {"sentence": "potato tomate berry watermelon", "preferred_letter": "t"},
    )

    assert ending == SucceedRun(Reference.parse("tasks.keep_matching_words"))
    assert final.last((), "rounds") == 4
    assert final.last((("rounds", 4),), "picking") == 3
    assert final.get(at("keep_matching_words")) == ok(["potato", "tomate"])


def test_a_run_starts_with_the_steps_that_wait_on_nothing() -> None:
    assert published(plan(PICKER, picker({}))) == ["compose_payload"]


def test_a_published_task_carries_its_definition() -> None:
    (action,) = plan(PICKER, picker({}))

    assert action == PublishTask(
        at("compose_payload"),
        queue="default",
        handler="tasks:compose_payload",
        params={"sentence": Reference.parse("inputs.sentence")},
        fixed_params={},
    )


def test_entering_a_loop_starts_its_first_iteration_and_every_branch() -> None:
    actions = plan(PICKER, picker({at("compose_payload"): ok("p")}))

    assert published(actions) == [
        "rounds[1].picking[1].pick_word",
        "rounds[1].picking[2].pick_word",
        "rounds[1].picking[3].pick_word",
    ]


def test_a_task_already_published_is_not_published_again() -> None:
    running = {
        at("compose_payload"): ok("p"),
        at("pick_word", ("rounds", 1), ("picking", 1)): RUNNING,
        at("pick_word", ("rounds", 1), ("picking", 2)): ok("x"),
    }

    actions = plan(PICKER, picker(running))

    assert published(actions) == [
        "rounds[1].picking[2].extract_word",
        "rounds[1].picking[3].pick_word",
    ]
    assert plan(PICKER, picker(running)) == actions


def one_round(number: int, done: JsonValue) -> dict[Address, StepResult]:
    """Every step of one word_picker iteration, finished."""
    loop = ("rounds", number)
    steps = {}
    for index in (1, 2, 3):
        steps[at("pick_word", loop, ("picking", index))] = ok("pick")
        steps[at("extract_word", loop, ("picking", index))] = ok(f"w{index}")
    steps[at("collect_words", loop)] = ok({"selected_words": [], "len": number})
    steps[at("enough_rounds", loop)] = ok(done)
    return steps


def test_a_fan_in_waits_for_every_branch() -> None:
    steps = {at("compose_payload"): ok("p"), **one_round(1, False)}
    del steps[at("collect_words", ("rounds", 1))]
    del steps[at("enough_rounds", ("rounds", 1))]
    steps[at("extract_word", ("rounds", 1), ("picking", 2))] = RUNNING

    assert published(plan(PICKER, picker(steps))) == []

    steps[at("extract_word", ("rounds", 1), ("picking", 2))] = ok("w2")

    assert published(plan(PICKER, picker(steps))) == ["rounds[1].collect_words"]


def test_an_exit_condition_returning_false_starts_the_next_iteration() -> None:
    steps = {at("compose_payload"): ok("p"), **one_round(1, False)}

    assert published(plan(PICKER, picker(steps))) == [
        "rounds[2].picking[1].pick_word",
        "rounds[2].picking[2].pick_word",
        "rounds[2].picking[3].pick_word",
    ]


def test_an_exit_condition_returning_true_leaves_the_loop() -> None:
    steps = {
        at("compose_payload"): ok("p"),
        **one_round(1, False),
        **one_round(2, True),
    }

    assert published(plan(PICKER, picker(steps))) == ["latest_round"]


def test_after_the_loop_the_run_carries_on_to_the_end() -> None:
    steps = {
        at("compose_payload"): ok("p"),
        **one_round(1, True),
        at("latest_round"): ok({"selected_words": []}),
    }

    assert published(plan(PICKER, picker(steps))) == [
        "grading[1].score_words",
        "grading[2].score_words",
        "grading[3].score_words",
    ]

    for index in (1, 2, 3):
        steps[at("score_words", ("grading", index))] = ok({})

    assert published(plan(PICKER, picker(steps))) == ["keep_matching_words"]

    steps[at("keep_matching_words")] = ok([])

    assert plan(PICKER, picker(steps)) == [
        SucceedRun(Reference.parse("tasks.keep_matching_words"))
    ]


LOOPING = flow(
    """\
    repeat:
      loop: {max_cycles: 2, exit_condition: check}
      steps:
        slow: {handler: m:slow}
        check: {handler: m:check}
    after: {handler: m:after, params: {checks: tasks.check}}
    """
)


def iteration(
    number: int, check: StepResult, slow: StepResult | None = None
) -> dict[Address, StepResult]:
    loop = ("repeat", number)
    return {at("slow", loop): slow or ok(), at("check", loop): check}


def test_the_next_iteration_waits_for_every_step_of_the_current_one() -> None:
    steps = iteration(1, ok(False), slow=RUNNING)

    assert plan(LOOPING, state(steps)) == []


def test_steps_of_one_iteration_that_wait_on_nothing_start_together() -> None:
    assert published(plan(LOOPING, state({}))) == [
        "repeat[1].slow",
        "repeat[1].check",
    ]


def test_a_loop_past_max_cycles_fails_the_run() -> None:
    steps = {**iteration(1, ok(False)), **iteration(2, ok(False))}

    assert plan(LOOPING, state(steps)) == [
        FailRun("repeat: still not done after max_cycles, 2 iterations")
    ]


def test_a_loop_may_leave_on_its_last_allowed_iteration() -> None:
    steps = {**iteration(1, ok(False)), **iteration(2, ok(True))}

    assert published(plan(LOOPING, state(steps))) == ["after"]


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {"done": True}])
def test_an_exit_condition_that_is_not_a_boolean_fails_the_run(
    value: JsonValue,
) -> None:
    (action,) = plan(LOOPING, state(iteration(1, ok(value))))

    assert isinstance(action, FailRun)
    assert "repeat[1].check" in action.reason
    assert "not true or false" in action.reason


def test_an_exit_condition_still_running_decides_nothing() -> None:
    assert plan(LOOPING, state(iteration(1, RUNNING))) == []


DECORATE = flow(
    """\
    words: {handler: m:words}
    decorate:
      fan_out: {over: tasks.words}
      steps:
        padding:
          loop: {max_cycles: 5, exit_condition: long_enough}
          steps:
            pad: {handler: m:pad, params: {word: neorc.item, count: neorc.loop_count}}
            long_enough: {handler: m:long_enough, params: {pads: tasks.pad}}
    report: {handler: m:report, params: {padded: tasks.pad}}
    """
)


def test_a_fan_out_over_a_list_has_one_branch_per_element() -> None:
    steps = {at("words"): ok(["red", "blue"])}

    assert published(plan(DECORATE, state(steps))) == [
        "decorate[1].padding[1].pad",
        "decorate[2].padding[1].pad",
    ]


def test_loops_in_different_branches_move_on_their_own() -> None:
    first = (("decorate", 1), ("padding", 1))
    second = (("decorate", 2), ("padding", 1))
    steps = {
        at("words"): ok(["red", "blue"]),
        Address("pad", first): ok("red*"),
        Address("long_enough", first): ok(False),
        Address("pad", second): ok("blue*"),
        Address("long_enough", second): ok(True),
    }

    assert published(plan(DECORATE, state(steps))) == ["decorate[1].padding[2].pad"]

    steps[at("pad", ("decorate", 1), ("padding", 2))] = ok("red**")
    steps[at("long_enough", ("decorate", 1), ("padding", 2))] = ok(True)

    assert published(plan(DECORATE, state(steps))) == ["report"]


def test_a_fan_out_over_an_empty_list_is_finished_at_once() -> None:
    assert published(plan(DECORATE, state({at("words"): ok([])}))) == ["report"]


@pytest.mark.parametrize("words", ["red", {"red": 1}, None])
def test_a_fan_out_over_something_not_a_list_fails_the_run(words: JsonValue) -> None:
    (action,) = plan(DECORATE, state({at("words"): ok(words)}))

    assert isinstance(action, FailRun)
    assert "not a list" in action.reason


def test_a_fan_out_waits_for_its_list() -> None:
    assert plan(DECORATE, state({at("words"): RUNNING})) == []


def test_a_range_fan_out_needs_nothing_to_start() -> None:
    fan = flow(
        """\
        each:
          fan_out: {range: 2}
          steps:
            one: {handler: m:one, params: {i: neorc.index}}
        """
    )

    assert published(plan(fan, state({}))) == ["each[1].one", "each[2].one"]


def test_file_order_does_not_decide_what_waits() -> None:
    backwards = flow(
        """\
        last: {handler: m:f, params: {x: tasks.first}}
        first: {handler: m:f}
        """
    )

    assert published(plan(backwards, state({}))) == ["first"]
    assert published(plan(backwards, state({at("first"): ok(1)}))) == ["last"]


def test_a_step_waits_for_a_whole_container_it_refers_into() -> None:
    steps = {
        at("words"): ok(["red"]),
        at("pad", ("decorate", 1), ("padding", 1)): ok("red*"),
        at("long_enough", ("decorate", 1), ("padding", 1)): RUNNING,
    }

    assert plan(DECORATE, state(steps)) == []


def test_metadata_filled_in_later_does_not_hold_a_task_back() -> None:
    meta = flow(
        """\
        t:
          handler: m:f
          params: {id: neorc.task_id, run: neorc.flow_run_id, tries: neorc.attempts}
        """
    )

    assert published(plan(meta, state({}))) == ["t"]


def test_a_branch_missing_from_an_earlier_iteration_fails_the_run() -> None:
    growing = flow(
        """\
        rounds:
          loop: {max_cycles: 3, exit_condition: done}
          steps:
            make: {handler: m:f}
            picking:
              fan_out: {over: tasks.make}
              steps:
                pick: {handler: m:f}
                use: {handler: m:f, params: {picks: tasks.pick}}
            done: {handler: m:f, params: {x: tasks.use}}
        """
    )
    steps = {
        at("make", ("rounds", 1)): ok(1),
        at("pick", ("rounds", 1), ("picking", 1)): ok(1),
        at("use", ("rounds", 1), ("picking", 1)): ok(1),
        at("done", ("rounds", 1)): ok(False),
        at("make", ("rounds", 2)): ok(2),
        at("pick", ("rounds", 2), ("picking", 1)): ok(2),
        at("pick", ("rounds", 2), ("picking", 2)): ok(2),
    }

    actions = plan(growing, state(steps))

    assert len(actions) == 1 and isinstance(actions[0], FailRun)
    assert "rounds[1].picking[2].pick does not exist" in actions[0].reason


# Sub-flows, the run's output and failures.

ROUNDS_INPUTS: dict[str, JsonValue] = {
    "sentence": "potato tomate berry watermelon",
    "preferred_letter": "t",
    "requested_at": {"$datetime": "2026-09-13T10:00:00+00:00"},
}


def rounds(steps: Mapping[Address, StepResult]) -> RunState:
    return RunState(ROUNDS_INPUTS, dict(steps))


def test_word_picker_rounds_runs_to_the_end_with_its_sub_flow() -> None:
    handlers = load_handlers(EXAMPLES / "wordplay" / "tasks.py")

    final, ending = run_to_the_end(ROUNDS, handlers, ROUNDS_INPUTS)

    assert ending == SucceedRun(None)
    assert final.last((), "runs") == 2
    assert final.get(at("final_words")) == ok(["potato", "tomate"])
    report = final.get(at("report"))
    assert report is not None and isinstance(report.value, dict)
    assert report.value["words"] == ["potato**", "tomate**"]
    assert report.value["requested_at"] == ROUNDS_INPUTS["requested_at"]


def test_a_sub_flow_step_starts_a_child_run() -> None:
    (action,) = plan(ROUNDS, rounds({}))

    assert action == StartSubRun(
        at("picker", ("runs", 1)),
        flow="word_picker",
        params={
            "sentence": Reference.parse("inputs.sentence"),
            "preferred_letter": Reference.parse("inputs.preferred_letter"),
        },
        fixed_params={},
    )


def test_a_sub_flow_completes_when_its_run_succeeds() -> None:
    picker_run = at("picker", ("runs", 1))

    assert plan(ROUNDS, rounds({picker_run: RUNNING})) == []
    assert published(plan(ROUNDS, rounds({picker_run: ok(["potato"])}))) == [
        "runs[1].ran_enough"
    ]


def test_a_run_with_no_output_succeeds_once_every_step_finished() -> None:
    steps = {
        at("picker", ("runs", 1)): ok(["red"]),
        at("ran_enough", ("runs", 1)): ok(True),
        at("final_words"): ok(["red"]),
        at("pad", ("decorate", 1), ("padding", 1)): ok("red*"),
        at("long_enough", ("decorate", 1), ("padding", 1)): ok(True),
    }

    assert published(plan(ROUNDS, rounds(steps))) == ["report"]

    steps[at("report")] = ok({})

    assert plan(ROUNDS, rounds(steps)) == [SucceedRun(None)]


def test_a_run_does_not_succeed_while_a_step_is_left() -> None:
    running = flow(
        """        a: {handler: m:f}
        b: {handler: m:f}
        """
    )

    assert plan(running, state({at("a"): ok(1), at("b"): RUNNING})) == []
    assert plan(running, state({at("a"): ok(1), at("b"): ok(2)})) == [SucceedRun(None)]


def test_a_failed_task_fails_the_run() -> None:
    steps = {
        at("compose_payload"): ok("p"),
        at("pick_word", ("rounds", 1), ("picking", 1)): ok("x"),
        at("pick_word", ("rounds", 1), ("picking", 2)): FAILED,
    }

    assert plan(PICKER, picker(steps)) == [
        FailRun("rounds[1].picking[2].pick_word failed")
    ]


def test_a_failed_sub_flow_run_fails_the_run() -> None:
    steps = {at("picker", ("runs", 1)): FAILED}

    assert plan(ROUNDS, rounds(steps)) == [FailRun("runs[1].picker failed")]


def test_a_failure_is_reported_the_same_whichever_order_the_state_lists_it() -> None:
    first = {
        at("a"): FAILED,
        at("b"): FAILED,
    }
    second = {at("b"): FAILED, at("a"): FAILED}
    two = flow(
        """        a: {handler: m:f}
        b: {handler: m:f}
        """
    )

    assert plan(two, state(first)) == plan(two, state(second)) == [FailRun("a failed")]
