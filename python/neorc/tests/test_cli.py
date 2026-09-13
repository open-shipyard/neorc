# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command: what it parses, and what it refuses."""

from __future__ import annotations

import argparse
import asyncio
import builtins
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from neorc import _cli
from neorc_core import Task, TaskHandler, TaskStatus


def _write_tasks(tmp_path: Path, tasks: str, module: str = "") -> Path:
    """A tasks file, next to a module of handlers when one is given."""
    if module:
        (tmp_path / "handlers_under_test.py").write_text(module)
    path = tmp_path / "tasks.toml"
    path.write_text(f"[tasks]\n{tasks}")
    return path


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
        _cli.worker_start_command(argparse.Namespace(manager_address=None, tasks=None))


def test_a_worker_needs_to_be_told_which_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_cli.TASKS_FILE_ENV, raising=False)

    with pytest.raises(SystemExit, match=_cli.TASKS_FILE_ENV):
        _cli.worker_start_command(
            argparse.Namespace(manager_address="manager.test", tasks=None)
        )


def test_the_settings_can_come_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _write_tasks(tmp_path, 'noop = "neorc._cli:main"')
    monkeypatch.setenv(_cli.MANAGER_ADDRESS_ENV, "manager.internal:8420")
    monkeypatch.setenv(_cli.TASKS_FILE_ENV, str(tasks))
    served: list[str] = []

    def fake_run(coro: Any) -> None:
        coro.close()
        served.append("ran")

    monkeypatch.setattr("neorc._cli.asyncio.run", fake_run)

    exit_code = _cli.worker_start_command(
        argparse.Namespace(
            manager_address=None, tasks=None, poll_timeout=1.0, lease_seconds=2.0
        )
    )

    assert exit_code == 0
    assert served == ["ran"]


def test_tasks_are_loaded_from_a_file_next_to_their_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "handlers_under_test", raising=False)
    path = _write_tasks(
        tmp_path,
        'greet = "handlers_under_test:greet"\nwave = "handlers_under_test:wave"\n',
        module=(
            "ran = []\n"
            "async def greet(task):\n"
            "    ran.append(('greet', task))\n"
            "def wave(task):\n"
            "    ran.append(('wave', task))\n"
        ),
    )

    handlers = _cli.load_tasks(path)
    for handler in handlers.values():
        asyncio.run(_call(handler))

    module = sys.modules["handlers_under_test"]
    assert sorted(handlers) == ["greet", "wave"]
    assert module.ran == [("greet", _TASK), ("wave", _TASK)]


def test_plain_functions_run_off_the_event_loop() -> None:
    threads: list[threading.Thread] = []
    handler = _cli._as_handler(lambda task: threads.append(threading.current_thread()))

    asyncio.run(_call(handler))

    assert threads and threads[0] is not threading.main_thread()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[tasks", "not valid TOML"),
        ("[other]\nx = 1", "no \\[tasks\\] table"),
        ("[tasks]", "no \\[tasks\\] table"),
    ],
)
def test_malformed_tasks_files_are_refused(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "tasks.toml"
    path.write_text(content)

    with pytest.raises(SystemExit, match=message):
        _cli.load_tasks(path)


def test_a_missing_tasks_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="cannot read tasks file"):
        _cli.load_tasks(tmp_path / "absent.toml")


def test_every_bad_entry_is_reported_at_once(tmp_path: Path) -> None:
    path = _write_tasks(
        tmp_path,
        'a = "just_a_module"\n'
        "b = 3\n"
        'c = "neorc:not_there"\n'
        'd = "no_such_module_at_all:f"\n'
        'e = "neorc:__doc__"\n',
    )

    with pytest.raises(SystemExit) as raised:
        _cli.load_tasks(path)

    message = str(raised.value)
    for expected in (
        'a: wants "module:function"',
        'b: wants "module:function"',
        "c: 'neorc' has no function 'not_there'",
        "d: cannot import",
        "e: 'neorc' has no function '__doc__'",
    ):
        assert expected in message


_NOW = datetime.now(UTC)
_TASK = Task(
    id=uuid4(),
    name="t",
    payload={},
    status=TaskStatus.RUNNING,
    created_at=_NOW,
    run_after=_NOW,
)


async def _call(handler: TaskHandler) -> None:
    await handler(_TASK)


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
            argparse.Namespace(manager_address="manager.test", tasks=None)
        )


def test_an_install_command_names_every_extra_it_needs() -> None:
    with (
        pytest.raises(SystemExit, match=r"pip install 'neorc\[manager,postgres\]'"),
        _cli._needs("manager", "postgres"),
    ):
        raise ImportError("No module named 'fastapi'")
