# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command: what it parses, and what it refuses."""

from __future__ import annotations

import argparse
import builtins
import sys
from pathlib import Path
from typing import Any

import pytest

from neorc import _cli
from neorc_core import Manager, Task, Worker
from neorc_core.testing import DirectQueueClient, MemoryTaskNotifier, MemoryTaskStore


def _worker() -> Worker:
    manager = Manager(MemoryTaskStore(), MemoryTaskNotifier())
    return Worker("test", DirectQueueClient(manager))


def test_manager_start_takes_its_settings_from_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake_run(host: str, port: int, **kwargs: Any) -> None:
        calls.append((host, port, kwargs))

    monkeypatch.setattr("neorc.manager.run", fake_run)

    exit_code = _cli.main(
        [
            "manager",
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
            "--database-url",
            "postgresql:///neorc",
            "--create-schema",
        ]
    )

    assert exit_code == 0
    assert calls == [
        (
            "127.0.0.1",
            9000,
            {"database_url": "postgresql:///neorc", "create_schema": True},
        )
    ]


def test_a_worker_needs_to_be_told_which_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_cli.MANAGER_ADDRESS_ENV, raising=False)

    with pytest.raises(SystemExit, match=_cli.MANAGER_ADDRESS_ENV):
        _cli.worker_start_command(
            argparse.Namespace(manager_address=None, handlers=None)
        )


def test_the_manager_address_can_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_cli.MANAGER_ADDRESS_ENV, "manager.internal:8420")
    served: list[str] = []

    def fake_run(coro: Any) -> None:
        coro.close()
        served.append("ran")

    monkeypatch.setattr("neorc._cli.asyncio.run", fake_run)

    exit_code = _cli.worker_start_command(
        argparse.Namespace(
            manager_address=None, handlers=None, poll_timeout=1.0, lease_seconds=2.0
        )
    )

    assert exit_code == 0
    assert served == ["ran"]


def test_handlers_are_loaded_from_a_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = tmp_path / "handlers_under_test.py"
    module.write_text(
        "async def greet(task):\n"
        "    return None\n"
        "\n"
        "def setup(worker):\n"
        "    worker.register('greet', greet)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    register = _cli._load_handlers("handlers_under_test:setup")
    worker = _worker()
    register(worker)

    with pytest.raises(ValueError, match="already registered"):
        worker.register("greet", _noop)


def test_a_worker_without_handlers_is_allowed_but_warned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    register = _cli._load_handlers(None)
    register(_worker())

    assert "no handlers given" in caplog.text


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("just_a_module", "module:function"),
        ("neorc:not_there", "no 'not_there'"),
        ("no_such_module_at_all:setup", "cannot import"),
        ("neorc:__doc__", "not callable"),
    ],
)
def test_bad_handler_targets_are_refused_with_a_reason(
    target: str, message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        _cli._load_handlers(target)


def test_the_parser_covers_both_services() -> None:
    parser = _cli.build_parser()

    manager = parser.parse_args(["manager", "start"])
    worker = parser.parse_args(["worker", "start"])

    assert manager.handler is _cli.manager_start_command
    assert manager.host == _cli.DEFAULT_HOST
    assert manager.port == _cli.DEFAULT_PORT
    assert worker.handler is _cli.worker_start_command


def test_an_unknown_command_exits_with_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        _cli.main(["orchestrate", "everything"])

    assert "usage: neorc" in capsys.readouterr().err


def test_the_console_script_is_installed() -> None:
    from importlib.metadata import entry_points

    scripts = entry_points(group="console_scripts")
    assert "neorc" in {script.name for script in scripts}
    assert sys.executable  # the script runs on this interpreter


async def _noop(task: Task) -> None:
    return None


def test_a_missing_extra_is_reported_as_the_command_that_installs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base install brings nothing, so this is the first thing a host meets."""
    real_import = builtins.__import__

    def without_httpx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("httpx"):
            raise ImportError("No module named 'httpx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_httpx)
    monkeypatch.delitem(sys.modules, "neorc.http", raising=False)
    monkeypatch.delitem(sys.modules, "neorc.http._queue_client", raising=False)

    with pytest.raises(SystemExit, match=r"pip install 'neorc\[http\]'"):
        _cli.worker_start_command(
            argparse.Namespace(manager_address="manager.test", handlers=None)
        )
