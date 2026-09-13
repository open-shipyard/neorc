# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command.

    neorc manager start
    neorc worker start

Adapters are imported inside the handlers, so a host that installed only the
extras it needs can still run the CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import signal
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from neorc_core import Worker

MANAGER_ADDRESS_ENV = "NEORC_MANAGER_ADDRESS"
HANDLERS_ENV = "NEORC_WORKER_HANDLERS"

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
        "--handlers",
        default=None,
        help=(
            "module:function that registers this worker's handlers, called with "
            f"the worker; defaults to ${HANDLERS_ENV}"
        ),
    )
    worker_start.add_argument("--poll-timeout", type=float, default=30.0)
    worker_start.add_argument("--lease-seconds", type=float, default=60.0)
    worker_start.set_defaults(handler=worker_start_command)

    return parser


def manager_start_command(args: argparse.Namespace) -> int:
    """Run the manager service. Needs the ``manager`` extra."""
    with _needs("manager", "and postgres"):
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

    register_handlers = _load_handlers(args.handlers or os.environ.get(HANDLERS_ENV))

    async def serve() -> None:
        async with HttpQueueClient(address, poll_timeout=args.poll_timeout) as client:
            worker = Worker(
                address,
                client,
                poll_timeout=args.poll_timeout,
                lease_seconds=args.lease_seconds,
            )
            register_handlers(worker)
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
def _needs(extra: str, also: str = "") -> Iterator[None]:
    """Turn a missing optional dependency into the install command that fixes it.

    Nothing is installed by default, so this is the first thing a new deployment
    meets. It should say what to do, not print a traceback.
    """
    try:
        yield
    except ImportError as exc:
        extras = f"{extra}{',' + also.removeprefix('and ') if also else ''}"
        raise SystemExit(
            f"this command needs the {extra!r} extra, which is not installed "
            f"({exc}). Install it with: pip install 'neorc[{extras}]'"
        ) from exc


def _load_handlers(target: str | None) -> Callable[[Worker], None]:
    """Resolve ``module:function``, the hook a deployment registers its work in."""
    if not target:
        _log.warning(
            "no handlers given: every task this worker claims will fail. "
            "Pass --handlers module:function or set $%s",
            HANDLERS_ENV,
        )
        return _register_nothing

    module_name, _, attribute = target.partition(":")
    if not attribute:
        raise SystemExit(f"--handlers wants module:function, not {target!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SystemExit(f"cannot import {module_name!r}: {exc}") from exc
    try:
        register = getattr(module, attribute)
    except AttributeError as exc:
        raise SystemExit(f"{module_name!r} has no {attribute!r}") from exc
    if not callable(register):
        raise SystemExit(f"{target!r} is not callable")
    resolved: Callable[[Worker], None] = register
    return resolved


def _register_nothing(worker: Worker) -> None:
    """The default hook: a worker with no handlers, which fails what it claims."""


def _stop_on_signals(stop: Callable[[], None]) -> None:
    """Ask the worker to stop on the signals a supervisor sends to shut it down."""
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop)
        except NotImplementedError:  # a platform without signal handlers
            _log.debug("cannot install a handler for %s", signal_number)
