# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command.

    neorc run examples/hello --flow a
    neorc manager start
    neorc flows upload examples/hello/flows --manager-address 127.0.0.1:8420
    neorc scheduler start --manager-address 127.0.0.1:8420
    neorc worker start --manager-address 127.0.0.1:8420 --code-location examples/hello

Adapters are imported inside the handlers, so a host that installed only the
extras it needs can still run the CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from neorc_core import (
    FlowDefinitionError,
    FlowWorker,
    HandlerError,
    ManagerUnavailableError,
    NeorcError,
    RunStatus,
    Scheduler,
    _values,
)
from neorc_core.flows import DEFAULT_QUEUE, is_queue_name, read_flows
from neorc_core.local import run_local

MANAGER_ADDRESS_ENV = "NEORC_MANAGER_ADDRESS"

DEFAULT_HOST = "0.0.0.0"  # a manager serves workers on other hosts
DEFAULT_PORT = 8420

_log = logging.getLogger("neorc")


def build_parser() -> argparse.ArgumentParser:
    """Build the ``neorc`` argument parser and its subcommands."""
    parser = argparse.ArgumentParser(prog="neorc", description="Orchestrate tasks.")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("debug", "info", "warning", "error"),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    manager = commands.add_parser("manager", help="run the manager service")
    manager_commands = manager.add_subparsers(dest="subcommand", required=True)
    manager_start = manager_commands.add_parser("start", help="serve the manager")
    manager_start.add_argument("--host", default=DEFAULT_HOST)
    manager_start.add_argument("--port", type=int, default=DEFAULT_PORT)
    manager_start.add_argument(
        "--database-url",
        default=None,
        help="defaults to $NEORC_DATABASE_URL",
    )
    manager_start.add_argument(
        "--create-schema",
        action="store_true",
        help="create the tables at startup if they are not there yet",
    )
    manager_start.set_defaults(handler=manager_start_command)

    flows = commands.add_parser("flows", help="manage the flows a manager holds")
    flows_commands = flows.add_subparsers(dest="subcommand", required=True)
    flows_upload = flows_commands.add_parser(
        "upload", help="upload a directory of flow files as one set, as CI/CD would"
    )
    flows_upload.add_argument(
        "directory", type=Path, help="the flow files, *.yaml and *.yml"
    )
    _manager_address_option(flows_upload)
    flows_upload.set_defaults(handler=flows_upload_command)

    scheduler = commands.add_parser("scheduler", help="run the scheduler")
    scheduler_commands = scheduler.add_subparsers(dest="subcommand", required=True)
    scheduler_start = scheduler_commands.add_parser(
        "start", help="move runs forward as their events arrive"
    )
    _manager_address_option(scheduler_start)
    scheduler_start.add_argument("--poll-timeout", type=float, default=30.0)
    scheduler_start.set_defaults(handler=scheduler_start_command)

    worker = commands.add_parser("worker", help="run a worker")
    worker_commands = worker.add_subparsers(dest="subcommand", required=True)
    worker_start = worker_commands.add_parser("start", help="claim and run tasks")
    _manager_address_option(worker_start)
    worker_start.add_argument(
        "--code-location",
        type=Path,
        required=True,
        help="the handlers' code, imported from there first",
    )
    worker_start.add_argument(
        "--queue", default=DEFAULT_QUEUE, help="the queue to serve"
    )
    worker_start.add_argument("--poll-timeout", type=float, default=30.0)
    worker_start.add_argument("--lease-seconds", type=float, default=60.0)
    worker_start.set_defaults(handler=worker_start_command)

    run = commands.add_parser(
        "run", help="run a flow to its end in this process, with nothing to deploy"
    )
    run.add_argument(
        "directory",
        type=Path,
        help="the handlers' code, with the flow files in its flows/ directory",
    )
    run.add_argument("--flow", required=True, help="the flow to run")
    run.add_argument(
        "--inputs",
        default="{}",
        help='the run\'s inputs, a JSON object; datetimes as {"$datetime": "..."}',
    )
    run.add_argument(
        "--flows-dir",
        type=Path,
        default=None,
        help="where the flow files are; defaults to DIRECTORY/flows",
    )
    run.add_argument(
        "--timeout", type=float, default=None, help="give up after this many seconds"
    )
    run.set_defaults(handler=run_command)

    return parser


def _manager_address_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--manager-address",
        default=None,
        help=f"defaults to ${MANAGER_ADDRESS_ENV}",
    )


def _manager_address(args: argparse.Namespace, what: str) -> str:
    address: str | None = args.manager_address or os.environ.get(MANAGER_ADDRESS_ENV)
    if not address:
        raise SystemExit(
            f"no manager to {what}: pass --manager-address or set "
            f"${MANAGER_ADDRESS_ENV}"
        )
    return address


def run_command(args: argparse.Namespace) -> int:
    """Run a flow on ``LocalCluster`` and print its output as JSON.

    Exits 0 when the run succeeds, and 1, saying why, when it fails, is
    cancelled, cannot start or times out; 130 on Ctrl-C.
    """
    try:
        _values.ensure_json_depth(args.inputs)
        inputs = json.loads(args.inputs)
    except ValueError as exc:
        raise SystemExit(f"--inputs is not valid JSON: {exc}") from exc
    if not isinstance(inputs, dict):
        raise SystemExit("--inputs must be a JSON object of input names to values")
    flows_dir: Path = args.flows_dir or args.directory / "flows"
    if not flows_dir.is_dir():
        raise SystemExit(f"no flows directory at {str(flows_dir)!r}")

    # A loop and thread pool of our own: however the run ends, a plain handler
    # still running in a thread must not hold the command, as asyncio.run would.
    threads = f"{_HANDLER_THREADS}-{uuid.uuid4().hex[:8]}"  # this run's only
    executor = ThreadPoolExecutor(thread_name_prefix=threads)
    loop = asyncio.new_event_loop()
    loop.set_default_executor(executor)
    try:
        run = loop.run_until_complete(
            run_local(
                flows_dir,
                args.flow,
                inputs,
                code_location=args.directory,
                timeout=args.timeout,
            )
        )
    except TimeoutError:  # before OSError, which it is a kind of
        print(
            f"{args.flow!r} did not finish within {args.timeout} seconds",
            file=sys.stderr,
        )
        return _finish(loop, executor, threads, 1)
    except (NeorcError, ValueError, OSError) as exc:
        print(f"cannot run {args.flow!r}: {exc}", file=sys.stderr)
        return _finish(loop, executor, threads, 1)
    except KeyboardInterrupt:
        return _finish(loop, executor, threads, 130)
    except Exception as exc:  # a crashed loop: still never wait on a handler
        _log.exception("running %r crashed", args.flow)
        print(f"cannot run {args.flow!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _finish(loop, executor, threads, 1)

    if run.status is RunStatus.SUCCEEDED:
        print(json.dumps(run.output))
        return _finish(loop, executor, threads, 0)
    print(f"run {run.id} {run.status.value}: {run.reason}", file=sys.stderr)
    return _finish(loop, executor, threads, 1)


_HANDLER_THREADS = "neorc-handler"


def _finish(
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
    threads: str,
    exit_code: int,
) -> int:
    """Close the loop without waiting for handlers still running in a thread.

    Python joins those threads on its way out, so if one is still busy the
    process exits here and now instead, with ``exit_code``.
    """
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
    executor.shutdown(wait=False, cancel_futures=True)
    busy = False
    for thread in threading.enumerate():
        if thread.name.startswith(threads):
            thread.join(timeout=0.1)  # idle threads leave as the pool shuts down
            busy = busy or thread.is_alive()
    if busy:
        sys.stdout.flush()
        sys.stderr.flush()
        _exit_now(exit_code)
    return exit_code


def _exit_now(exit_code: int) -> None:
    os._exit(exit_code)


def manager_start_command(args: argparse.Namespace) -> int:
    """Run the manager service. Needs the ``manager`` extra."""
    with _needs("manager", "postgres"):
        from neorc.manager import run

    run(
        args.host,
        args.port,
        database_url=args.database_url,
        create_schema=args.create_schema,
    )
    return 0


def flows_upload_command(args: argparse.Namespace) -> int:
    """Upload the flow files in a directory as one set, and say what was stored.

    The files are read and validated as ``neorc run`` reads them; the manager
    checks the set and the version rules. Exits 1 when it refuses.
    """
    with _needs("http"):
        from neorc.http import HttpManagerClient

    address = _manager_address(args, "upload to")
    if not args.directory.is_dir():
        raise SystemExit(f"no such directory: {str(args.directory)!r}")
    try:
        contents = read_flows(args.directory)
    except FlowDefinitionError as exc:
        raise SystemExit(
            "invalid flow files:\n" + "\n".join(f"  {p}" for p in exc.problems)
        ) from exc
    except OSError as exc:
        raise SystemExit(f"cannot read {str(args.directory)!r}: {exc}") from exc
    if not contents:
        raise SystemExit(f"no flow files in {str(args.directory)!r}")

    async def upload() -> list[bool]:
        async with HttpManagerClient(address) as client:
            return await client.upload_flows(contents)

    try:
        stored = asyncio.run(upload())
    except FlowDefinitionError as exc:
        print("upload refused:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    except NeorcError as exc:
        print(f"upload refused: {exc}", file=sys.stderr)
        return 1
    for content, is_new in zip(contents, stored, strict=True):
        state = "stored" if is_new else "unchanged"
        print(f"{state} {content['name']} {content['version']}")
    return 0


def scheduler_start_command(args: argparse.Namespace) -> int:
    """Run the scheduler against the manager named by ``NEORC_MANAGER_ADDRESS``."""
    with _needs("http"):
        from neorc.http import HttpManagerClient

    address = _manager_address(args, "schedule for")

    async def serve() -> None:
        async with HttpManagerClient(address, poll_timeout=args.poll_timeout) as client:
            scheduler = Scheduler(client, poll_timeout=args.poll_timeout)
            _stop_on_signals(scheduler.stop)
            await scheduler.run()

    asyncio.run(serve())
    return 0


def worker_start_command(args: argparse.Namespace) -> int:
    """Run a worker against the manager named by ``NEORC_MANAGER_ADDRESS``.

    It serves one queue of the flows the manager holds, with the handlers found
    at ``--code-location``.
    """
    return _serve_queue(args, _manager_address(args, "work for"))


def _serve_queue(args: argparse.Namespace, address: str) -> int:
    """A worker for flows: check its handlers against the queue's tasks, then run."""
    with _needs("http"):
        from neorc.http import HttpFlowQueueClient

    if not args.code_location.is_dir():
        raise SystemExit(f"no such directory: {str(args.code_location)!r}")
    if not is_queue_name(args.queue):
        raise SystemExit(
            f"{args.queue!r} is not a queue name: letters, digits, _ and -"
        )

    async def serve() -> None:
        async with HttpFlowQueueClient(
            address, poll_timeout=args.poll_timeout
        ) as client:
            worker = FlowWorker(
                client,
                queue=args.queue,
                code_location=args.code_location,
                poll_timeout=args.poll_timeout,
                lease_seconds=args.lease_seconds,
            )
            stopping = asyncio.Event()

            def stop() -> None:
                stopping.set()
                worker.stop()

            _stop_on_signals(stop)
            if await _prepared(worker, args, stopping):
                await worker.run()

    asyncio.run(serve())
    return 0


PREPARE_RETRY_SECONDS = 5.0
"""How long a starting worker waits for a manager it cannot reach yet."""


async def _prepared(
    worker: FlowWorker, args: argparse.Namespace, stopping: asyncio.Event
) -> bool:
    """Check the handlers against the queue's tasks, waiting out an unreachable manager.

    Workers outlive manager restarts, and are often started alongside one:
    an unreachable manager is retried until a stop signal. Handlers that do
    not fit their tasks will not fit on retry: that exits, listing every
    problem. Returns ``False`` when stopped before the check passed.
    """
    while not stopping.is_set():
        try:
            await worker.prepare()
            return True
        except HandlerError as exc:
            raise SystemExit(
                f"the handlers in {str(args.code_location)!r} do not fit the "
                f"tasks on queue {args.queue!r}:\n"
                + "\n".join(f"  {p}" for p in exc.problems)
            ) from exc
        except ManagerUnavailableError as exc:
            _log.warning(
                "cannot check the handlers yet (%s); retrying in %ss",
                exc,
                PREPARE_RETRY_SECONDS,
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=PREPARE_RETRY_SECONDS)
        except NeorcError as exc:  # refused, and would be again: not worth retrying
            raise SystemExit(f"cannot start the worker: {exc}") from exc
    return False


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``neorc`` console script; returns the exit status."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )
    try:
        exit_code: int = args.handler(args)
    except KeyboardInterrupt:
        return 130
    return exit_code


@contextmanager
def _needs(*extras: str) -> Iterator[None]:
    """Turn a missing optional dependency into the install command that fixes it.

    Nothing is installed by default, so this is the first thing a new deployment
    meets. It should say what to do, not print a traceback.
    """
    try:
        yield
    except ImportError as exc:
        raise SystemExit(
            f"this command needs the {' and '.join(map(repr, extras))} extra, which "
            f"is not installed ({exc}). Install it with: "
            f"pip install 'neorc[{','.join(extras)}]'"
        ) from exc


def _stop_on_signals(stop: Callable[[], None]) -> None:
    """Ask the worker to stop on the signals a supervisor sends to shut it down."""
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop)
        except NotImplementedError:  # a platform without signal handlers
            _log.debug("cannot install a handler for %s", signal_number)
