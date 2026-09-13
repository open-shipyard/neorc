# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Handlers for the wordplay flows. See README.md.

Plain functions that do not import neorc. Each parameter name matches an input
the flow file declares for the task, under ``params`` or ``fixed_params``.
Values are JSON values plus datetimes; there are no sets, so collections are
lists.

The ``while`` and ``for`` loops of docs/specs/flow_example.py are gone: the flow
files describe them, and the engine runs them.
"""

from datetime import UTC, datetime

# word_picker.yaml


def compose_payload(sentence):
    return f"dummy composed payload from user input: {sentence}"


def pick_word(payload, index):
    words = payload.split(":")[1].split()
    return f"my preferred word is {words[index - 1]}"


def extract_word(picks):
    # One entry per loop iteration so far, for this fan-out branch only.
    return picks[-1].replace("my preferred word is ", "")


def collect_words(all_rounds):
    # A list of iterations so far, each a list of branch results.
    selected = sorted({word for round_words in all_rounds for word in round_words})
    return {"selected_words": selected, "len": len(all_rounds)}


def enough_rounds(collections):
    return collections[-1]["len"] > 3


def pick_last(values):
    return values[-1]


def score_words(preferred_letter, latest_round, index):
    words = latest_round["selected_words"]
    return {word: word.count(preferred_letter) for word in words}


def keep_matching_words(all_scores):
    final = {
        word for graded in all_scores for word, score in graded.items() if score >= 1
    }
    return sorted(final)


# word_picker_rounds.yaml


def ran_enough(loop_count, picker_results):
    # Pretend word_picker needed to run twice.
    return loop_count >= 2


def pad(word, loop_count, pad_char):
    return word + pad_char * loop_count


def long_enough(pads, min_length):
    return len(pads[-1]) >= min_length


def report(padded, requested_at, flow_run_id, task_id, attempts):
    # Delivery is at-least-once, so a report must be safe to produce twice.
    # task_id is stable across deliveries: anything this wrote elsewhere would
    # be keyed by it.
    generated_at = datetime.now(UTC)
    return {
        "report_id": task_id,
        "flow_run_id": flow_run_id,
        "attempts": attempts,
        # A list of branches, each a list of loop iterations: keep the last.
        "words": [iterations[-1] for iterations in padded],
        "requested_at": requested_at,
        "generated_at": generated_at,
        "waited_seconds": (generated_at - requested_at).total_seconds(),
    }
