# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command.

    neorc run examples/hello --flow a
    neorc manager start
    neorc worker start --tasks tasks.toml

Adapters are imported inside the handlers, so a host that installed only the
extras it needs can still run the CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import signal
import sys
import threading
import tomllib
import uuid
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from neorc_core import NeorcError, RunStatus, Task, TaskHandler, Worker, _values
from neorc_core._handlers import resolve_handler
from neorc_core.local import run_local

MANAGER_ADDRESS_ENV = "NEORC_MANAGER_ADDRESS"
TASKS_FILE_ENV = "NEORC_WORKER_TASKS"

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

    worker = commands.add_parser("worker", help="run a worker")
    worker_commands = worker.add_subparsers(dest="subcommand", required=True)
    worker_start = worker_commands.add_parser("start", help="claim and run tasks")
    worker_start.add_argument(
        "--manager-address",
        default=None,
        help=f"defaults to ${MANAGER_ADDRESS_ENV}",
    )
    worker_start.add_argument(
        "--tasks",
        default=None,
        help=(
            "TOML file mapping task names to module:function handlers; "
            f"defaults to ${TASKS_FILE_ENV}"
        ),
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


def worker_start_command(args: argparse.Namespace) -> int:
    """Run a worker against the manager named by ``NEORC_MANAGER_ADDRESS``."""
    with _needs("http"):
        from neorc.http import HttpQueueClient

    address = args.manager_address or os.environ.get(MANAGER_ADDRESS_ENV)
    if not address:
        raise SystemExit(
            f"no manager to work for: pass --manager-address or set "
            f"${MANAGER_ADDRESS_ENV}"
        )

    tasks_file = args.tasks or os.environ.get(TASKS_FILE_ENV)
    if not tasks_file:
        raise SystemExit(f"no tasks to run: pass --tasks or set ${TASKS_FILE_ENV}")
    handlers = load_tasks(Path(tasks_file))

    async def serve() -> None:
        async with HttpQueueClient(address, poll_timeout=args.poll_timeout) as client:
            worker = Worker(
                address,
                client,
                poll_timeout=args.poll_timeout,
                lease_seconds=args.lease_seconds,
            )
            for name, handler in handlers.items():
                worker.register(name, handler)
            _stop_on_signals(worker.stop)
            await worker.run()

    asyncio.run(serve())
    return 0


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


def load_tasks(path: Path) -> dict[str, TaskHandler]:
    """Read a tasks file: a ``[tasks]`` table of name = "module:function".

    Modules resolve relative to the file's directory first, so a file can sit
    next to the code it names. Every entry is imported up front and every
    problem reported at once, so a bad file stops the worker at startup rather
    than failing tasks later.
    """
    try:
        config = tomllib.loads(path.read_text())
    except OSError as exc:
        raise SystemExit(f"cannot read tasks file {str(path)!r}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{str(path)!r} is not valid TOML: {exc}") from exc

    tasks = config.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        raise SystemExit(f"{str(path)!r} has no [tasks] table")

    code_location = path.resolve().parent
    handlers: dict[str, TaskHandler] = {}
    problems: list[str] = []
    for name, target in tasks.items():
        try:
            handlers[name] = _as_handler(resolve_handler(target, code_location))
        except ValueError as exc:
            problems.append(f"  {name}: {exc}")
    if problems:
        raise SystemExit(f"bad tasks in {str(path)!r}:\n" + "\n".join(problems))
    return handlers


def _as_handler(function: Callable[..., Any]) -> TaskHandler:
    """Run plain functions in a thread, so a handler need not be ``async``."""
    if inspect.iscoroutinefunction(function):
        handler: TaskHandler = function
        return handler

    async def in_thread(task: Task) -> None:
        await asyncio.to_thread(function, task)

    return in_thread


def _stop_on_signals(stop: Callable[[], None]) -> None:
    """Ask the worker to stop on the signals a supervisor sends to shut it down."""
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop)
        except NotImplementedError:  # a platform without signal handlers
            _log.debug("cannot install a handler for %s", signal_number)
