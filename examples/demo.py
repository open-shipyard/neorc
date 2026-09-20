# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A whole neorc deployment in one command, for a look around: no runs started.

    uv run python examples/demo.py                  # the wordplay example
    uv run python examples/demo.py --example hello

It starts a throwaway Postgres with ``pgserver``, as
[hello/postgres.py](hello/postgres.py) does, serves the manager with its web
UI, uploads the example's flows, and runs a scheduler and a worker per queue
the example uses. Nothing runs until you start a run, from the UI or with
``curl``; the output says how.

The manager asks for no token, as ``neorc manager start --no-auth`` does:
anyone who reaches it may do everything, which is why it listens on the
loopback address alone. A deployment gives each process a token of its role
instead; the example READMEs walk through that.

One example at a time: both keep their handlers in a module called ``tasks``,
and a worker validates every flow on its queue against its own code location,
so a worker could not serve the two at once.

Ctrl+C stops every part and throws the database away.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neorc_core import Worker

EXAMPLES = Path(__file__).resolve().parent

QUEUES = {"hello": ["default"], "wordplay": ["default", "scoring"]}
"""The queues each example's flows name; a worker serves each."""

START_A_RUN = {
    "hello": ("a", '{"inputs": {}}'),
    "wordplay": (
        "word_picker",
        '{"inputs": {"sentence": "potato tomate berry watermelon",'
        ' "preferred_letter": "t"}}',
    ),
}
"""A flow to start, and its inputs, for the curl line the demo prints."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--example",
        default="wordplay",
        choices=sorted(QUEUES),
        help="which example's flows to upload and serve (default: wordplay)",
    )
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="loopback by default: the manager asks for no token",
    )
    return parser.parse_args()


def built_ui() -> bool:
    """Whether the web UI is built and where the manager looks for it."""
    try:
        import neorc_ui
    except ImportError:
        return False
    return (neorc_ui.STATIC / "index.html").is_file()


def announce(
    args: argparse.Namespace,
    address: str,
    flows: list[object],
    *,
    with_ui: bool,
) -> None:
    """What is running, and how to start a run on it."""
    names = ", ".join(sorted(str(flow["name"]) for flow in flows))  # type: ignore[index]
    queues = ", ".join(QUEUES[args.example])
    flow, inputs = START_A_RUN[args.example]
    print(f"\nthe {args.example} example is up, with no run started")
    print(f"  flows uploaded: {names}")
    print(f"  a scheduler, and a worker on each queue: {queues}")
    print(f"  API: {address}, asking for no token")
    if with_ui:
        print(f"  UI:  {address}/ui/  — start a run there, and watch it")
    else:
        print(
            "  UI:  not built in this checkout; build it with `npm ci && npm run "
            "build` in ts/neorc-ui and copy dist/ to "
            "python/neorc-ui/src/neorc_ui/static/"
        )
    print("\nor start a run from here:")
    print(
        f"  curl -X POST {address}/flows/{flow}/runs \\\n"
        f"      -H 'content-type: application/json' -d '{inputs}'"
    )
    print("\nCtrl+C stops everything.", flush=True)


async def serve(args: argparse.Namespace, database_url: str) -> None:
    """The manager, a scheduler and the workers, until Ctrl+C."""
    import uvicorn

    from neorc.http import HttpManagerClient, HttpQueueClient
    from neorc.manager import build_app
    from neorc_core import Scheduler, Worker
    from neorc_core.flows import read_flows

    code_location = EXAMPLES / args.example
    with_ui = built_ui()
    app = build_app(database_url, auth=False, create_schema=True, ui=with_ui)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host=args.host, port=args.port, log_level="warning", lifespan="on"
        )
    )
    address = f"http://{args.host}:{args.port}"
    serving = asyncio.create_task(server.serve())
    stopping = asyncio.Event()

    async with contextlib.AsyncExitStack() as stack:
        async with asyncio.timeout(30):
            while not server.started:
                await asyncio.sleep(0.05)
        # After uvicorn's own handlers, which it installs as it starts: this
        # one stops the scheduler and the workers before the manager goes, so
        # nothing is left calling a service that is shutting down.
        loop = asyncio.get_running_loop()
        for caught in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(caught, stopping.set)
        uploads = await stack.enter_async_context(
            HttpManagerClient(address, poll_timeout=2)
        )
        flows = read_flows(code_location / "flows")
        await uploads.upload_flows(flows)
        scheduler = Scheduler(
            await stack.enter_async_context(HttpManagerClient(address, poll_timeout=2)),
            poll_timeout=2,
        )
        workers: list[Worker] = []
        for queue in QUEUES[args.example]:
            worker = Worker(
                await stack.enter_async_context(
                    HttpQueueClient(address, poll_timeout=2)
                ),
                queue=queue,
                code_location=code_location,
                poll_timeout=2,
                lease_seconds=30,
            )
            await worker.prepare()
            workers.append(worker)
        running = [asyncio.create_task(scheduler.run())]
        running += [asyncio.create_task(worker.run()) for worker in workers]
        try:
            announce(args, address, flows, with_ui=with_ui)
            await stopping.wait()
        finally:
            scheduler.stop()
            for worker in workers:
                worker.stop()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(20):
                    await asyncio.gather(*running, return_exceptions=True)
    server.should_exit = True
    await serving


def main() -> int:
    args = parse_args()
    try:
        import pgserver
    except ImportError:
        print(
            "this demo needs pgserver, one of the development dependencies:\n"
            "  uv sync\n"
            "It has no wheels for Windows or Python 3.13 and newer; there, run "
            "a Postgres of your own and follow examples/hello/README.md.",
            file=sys.stderr,
        )
        return 1
    with tempfile.TemporaryDirectory(prefix="neorc-demo-") as data_dir:
        print("starting Postgres, the first time takes a moment...", flush=True)
        database = pgserver.get_server(data_dir, cleanup_mode="stop")
        try:
            asyncio.run(serve(args, database.get_uri()))
        finally:
            print("\nstopping; the demo's database goes with it")
            database.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
