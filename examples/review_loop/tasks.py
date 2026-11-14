# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The handlers of the review loop. See README.md.

Plain functions that do not import neorc. The triage between them has no
function: a person answers it on the queue's page of the web UI.
"""

ISSUES = [
    {
        "n": 1,
        "severity": "blocker",
        "title": "Crash on empty input",
        "detail": "a.py:9",
    },
    {
        "n": 2,
        "severity": "major",
        "title": "No test for the retry",
        "detail": "tests/test_a.py",
    },
    {"n": 3, "severity": "minor", "title": "Typo in a docstring", "detail": "a.py:2"},
]


def review(change):
    # A stand-in for an AI reviewer, which finds the same three issues each
    # round.
    return {
        "pr_url": "https://github.com/o/r/pull/7",
        "summary": f"The review of {change!r} found {len(ISSUES)} issues.",
        "issues": ISSUES,
    }


def ready(triages):
    return triages[-1]["verdict"] == "ready"


def report(triages):
    last = triages[-1]
    counts = {
        decision: last["decision"].count(decision)
        for decision in ("fix", "defer", "discard")
    }
    return (
        f"Ready after {len(triages)} rounds: {counts['fix']} to fix, "
        f"{counts['defer']} deferred, {counts['discard']} discarded."
    )
