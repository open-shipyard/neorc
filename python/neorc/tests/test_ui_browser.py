# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The built UI in a real browser, against the whole deployment.

A manager on uvicorn and Postgres serves the assets ``ts/neorc-ui`` built, a
scheduler and workers run ``examples/wordplay`` over HTTP, and Chromium, driven
by Playwright, starts a run from the form, watches it succeed, and cancels
another. Skipped without a built UI or a browser: CI builds and installs both
for one Python, and contributing/dev-environment.md says how to locally.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import EXAMPLES, deployed_example, serve_app

from neorc.manager import build_app
from neorc_core.flows import read_flows

if TYPE_CHECKING:
    from playwright.async_api import Page

pytestmark = pytest.mark.postgres

BUILT = Path(__file__).parents[3] / "ts" / "neorc-ui" / "dist"
RUN_TIMEOUT_MS = 90_000
SENTENCE = "potato tomate berry watermelon"


def _built_ui() -> Path:
    if (BUILT / "index.html").is_file():
        return BUILT
    import neorc_ui

    if (neorc_ui.STATIC / "index.html").is_file():
        return neorc_ui.STATIC
    pytest.skip("no built UI: run `npm run build` in ts/neorc-ui")


@pytest.fixture
async def ui_address(
    pg_schema: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """The manager serving the built UI, wordplay uploaded, its default queue served.

    The ``scoring`` queue, which every run of ``word_picker_rounds`` reaches
    through its sub-flow, gets a worker only where the test wants a run to
    finish: without one, a run stays active for as long as it takes to cancel.
    """
    monkeypatch.setattr("neorc_ui.STATIC", _built_ui())
    app = build_app(pg_schema, create_schema=True, long_poll_timeout=2)
    async with (
        serve_app(app) as address,
        deployed_example(address, "wordplay", ["default"]) as client,
    ):
        await client.upload_flows(read_flows(EXAMPLES / "wordplay" / "flows"))
        yield address


@pytest.fixture
async def browsing() -> AsyncIterator[tuple[Page, list[str]]]:
    """A Chromium page, and the console errors and page errors it raises."""
    playwright = pytest.importorskip("playwright.async_api")
    async with playwright.async_playwright() as driver:
        try:
            browser = await driver.chromium.launch()
        except playwright.Error as exc:  # no browser downloaded
            pytest.skip(f"no Chromium for Playwright: {str(exc).splitlines()[0]}")
        page = await browser.new_page()
        complaints: list[str] = []
        page.on(
            "console",
            lambda message: (
                complaints.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: complaints.append(str(error)))
        try:
            yield page, complaints
        finally:
            with contextlib.suppress(Exception):
                await browser.close()


async def _start_word_picker_rounds(page: Page, ui_address: str) -> None:
    await page.goto(f"{ui_address}/ui/#/flows/word_picker_rounds")
    await page.get_by_label("sentence").fill(SENTENCE)
    await page.get_by_label("preferred_letter").fill("t")
    await page.get_by_label("requested_at").fill("2026-09-13T10:00")
    await page.get_by_role("button", name="Start run").click()
    await page.wait_for_url(re.compile(r"#/runs/[0-9a-f-]{36}$"))


@pytest.mark.usefixtures("own_tasks_module")
async def test_a_run_is_started_watched_and_another_cancelled_in_the_browser(
    ui_address: str, browsing: tuple[Page, list[str]]
) -> None:
    from playwright.async_api import expect

    page, complaints = browsing
    expect.set_options(timeout=10_000)
    heading = page.get_by_role("heading", level=1)

    # The flows are listed, with the one uploaded, under the banner.
    await page.goto(f"{ui_address}/ui/")
    await expect(page.get_by_role("note")).to_contain_text("No authentication")
    await page.get_by_role("link", name="Flows").click()
    await expect(page.get_by_role("link", name="word_picker_rounds")).to_be_visible()

    # A run started from the form, with every declared input, watched to its
    # end: with a worker on the scoring queue for as long as that takes.
    async with deployed_example(ui_address, "wordplay", ["scoring"], scheduler=False):
        await _start_word_picker_rounds(page, ui_address)
        await expect(heading).to_contain_text("succeeded", timeout=RUN_TIMEOUT_MS)

    # The fan-out ran a branch per word, and the results are shown.
    await expect(page.get_by_role("region", name="decorate branch 2")).to_be_visible()
    report = page.locator("li.step-task", has_text="report")
    await report.get_by_text("details").click()
    await expect(report).to_contain_text("potato**")
    await expect(report).to_contain_text("tomate**")

    # Another run, which cannot finish with nobody serving the scoring queue,
    # cancelled from its page.
    await _start_word_picker_rounds(page, ui_address)
    await page.get_by_role("button", name="Cancel run").click()
    asking = page.get_by_role("group", name="Confirm cancelling")
    await expect(asking).to_contain_text("every active run in its tree")
    await page.get_by_role("button", name="Cancel it").click()
    await expect(heading).to_contain_text("cancelled", timeout=RUN_TIMEOUT_MS)
    await expect(page.locator("dd", has_text="cancelled by hand")).to_be_visible()

    # The runs list has both, newest first.
    await page.get_by_role("link", name="Runs").click()
    rows = page.get_by_role("row")
    await expect(rows).to_have_count(3)
    await expect(rows.nth(1)).to_contain_text("cancelled")
    await expect(rows.nth(2)).to_contain_text("succeeded")

    assert complaints == [], "the browser complained: " + "; ".join(complaints)
