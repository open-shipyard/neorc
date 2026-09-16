# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Resolving a handler: only functions of modules under the code location."""

import importlib
import uuid
from pathlib import Path

import pytest

from neorc_core._handlers import resolve_handler


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@pytest.fixture
def location(tmp_path: Path) -> Path:
    path = tmp_path / "code"
    path.mkdir()
    return path


@pytest.fixture
def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory on the import path, outside the code location."""
    path = tmp_path / "elsewhere"
    path.mkdir()
    monkeypatch.syspath_prepend(str(path))
    return path


def test_a_function_under_the_code_location_resolves(location: Path) -> None:
    module = _name("tasks")
    (location / f"{module}.py").write_text("def work():\n    return 1\n")

    assert resolve_handler(f"{module}:work", location)() == 1


def test_a_module_elsewhere_on_the_import_path_is_never_imported(
    location: Path, elsewhere: Path
) -> None:
    module = _name("outside")
    marker = elsewhere / "imported"
    (elsewhere / f"{module}.py").write_text(
        f"open({str(marker)!r}, 'w').close()\ndef work():\n    return 1\n"
    )

    with pytest.raises(ValueError, match=r"no module .* in the code location"):
        resolve_handler(f"{module}:work", location)

    assert not marker.exists()


def test_a_namespace_package_with_a_portion_elsewhere_is_never_imported(
    location: Path, elsewhere: Path
) -> None:
    namespace = _name("ns")
    (location / namespace).mkdir()
    (location / namespace / "mine.py").write_text("def work():\n    return 1\n")
    (elsewhere / namespace).mkdir()
    marker = elsewhere / "imported"
    (elsewhere / namespace / "theirs.py").write_text(
        f"open({str(marker)!r}, 'w').close()\ndef work():\n    return 1\n"
    )

    for module in ("mine", "theirs"):
        with pytest.raises(ValueError, match=r"no module .* in the code location"):
            resolve_handler(f"{namespace}.{module}:work", location)

    assert not marker.exists()


def test_a_regular_package_elsewhere_wins_over_a_directory_here_and_is_refused(
    location: Path, elsewhere: Path
) -> None:
    """The import system prefers a package anywhere to a namespace portion."""
    name = _name("tests")
    (location / name).mkdir()  # a plain directory, with no __init__.py
    (elsewhere / name).mkdir()
    marker = elsewhere / "imported"
    (elsewhere / name / "__init__.py").write_text(
        f"open({str(marker)!r}, 'w').close()\ndef work():\n    return 1\n"
    )

    with pytest.raises(ValueError, match=r"no module .* in the code location"):
        resolve_handler(f"{name}:work", location)

    assert not marker.exists()


def test_a_submodule_of_a_package_imported_from_elsewhere_is_never_imported(
    location: Path, elsewhere: Path
) -> None:
    package = _name("pkg")
    for root in (location, elsewhere):
        (root / package).mkdir()
        (root / package / "__init__.py").write_text("")
    (location / package / "sub.py").write_text("def work():\n    return 1\n")
    marker = elsewhere / "imported"
    (elsewhere / package / "sub.py").write_text(
        f"open({str(marker)!r}, 'w').close()\ndef work():\n    return 1\n"
    )
    importlib.import_module(package)  # from elsewhere, first on the path yet

    with pytest.raises(ValueError, match="imports from outside the code location"):
        resolve_handler(f"{package}.sub:work", location)

    assert not marker.exists()


@pytest.mark.parametrize("via", ["handler", "import"])
def test_a_virtual_environment_inside_the_code_location_is_not_its_code(
    location: Path, monkeypatch: pytest.MonkeyPatch, via: str
) -> None:
    site = location / ".venv" / "lib" / "python3" / "site-packages"
    package = _name("installed")
    (site / package).mkdir(parents=True)
    marker = location / "imported"
    (site / package / "__init__.py").write_text(
        "def run_process(command):\n    return command\n"
    )
    (site / package / "loud.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    monkeypatch.syspath_prepend(str(site))
    mine = _name("tasks")
    (location / f"{mine}.py").write_text(f"from {package} import run_process\n")

    if via == "handler":
        with pytest.raises(ValueError, match=r"no module .* in the code location"):
            resolve_handler(f"{package}:run_process", location)
        with pytest.raises(ValueError, match=r"no module .* in the code location"):
            resolve_handler(f"{package}.loud:run_process", location)
        assert not marker.exists()
    else:
        with pytest.raises(ValueError, match="is not defined in the code location"):
            resolve_handler(f"{mine}:run_process", location)


def _pip_installed(target: Path, package: str) -> Path:
    """A package laid out as ``pip install --target`` leaves it: with a RECORD."""
    marker = target / "imported"
    (target / package).mkdir(parents=True)
    (target / package / "__init__.py").write_text(
        f"open({str(marker)!r}, 'w').close()\nfrom .loader import unsafe_load\n"
    )
    (target / package / "loader.py").write_text(
        "def unsafe_load(stream):\n    return stream\n"
    )
    metadata = target / f"{package}-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {package}\n")
    (metadata / "RECORD").write_text(
        f"{package}/__init__.py,,\n{package}/loader.py,,\n"
        f"{metadata.name}/METADATA,,\n../../bin/tool,,\n"
    )
    return marker


@pytest.mark.parametrize("via", ["handler", "import", "subdirectory"])
def test_a_package_pip_installed_into_the_code_location_is_not_its_code(
    location: Path, via: str
) -> None:
    package = _name("installed")
    if via == "subdirectory":
        marker = _pip_installed(location / "lib", package)
        target = f"lib.{package}.loader:unsafe_load"
    else:
        marker = _pip_installed(location, package)
        target = f"{package}:unsafe_load"
    if via == "import":
        mine = _name("tasks")
        (location / f"{mine}.py").write_text(
            f"from {package}.loader import unsafe_load\n"
        )
        target = f"{mine}:unsafe_load"

    with pytest.raises(ValueError, match="installed into the code location"):
        resolve_handler(target, location)

    if via != "import":
        assert not marker.exists()


def test_a_setuptools_egg_info_beside_the_code_does_not_refuse_it(
    location: Path,
) -> None:
    """``pip install -e .`` leaves one in the project, naming its own package."""
    package = _name("myapp")
    (location / package).mkdir()
    (location / package / "__init__.py").write_text("")
    (location / package / "tasks.py").write_text("def work():\n    return 1\n")
    egg_info = location / f"{package}.egg-info"
    egg_info.mkdir()
    (egg_info / "PKG-INFO").write_text(f"Metadata-Version: 2.1\nName: {package}\n")
    (egg_info / "top_level.txt").write_text(f"{package}\n")
    (egg_info / "SOURCES.txt").write_text(f"setup.py\n{package}/tasks.py\n")

    assert resolve_handler(f"{package}.tasks:work", location)() == 1


def test_a_module_shadowed_by_one_already_imported_is_refused(location: Path) -> None:
    (location / "json.py").write_text("def dumps(obj):\n    return 'mine'\n")

    with pytest.raises(ValueError, match="imports from outside the code location"):
        resolve_handler("json:dumps", location)


def test_a_symlink_out_of_the_code_location_is_refused(
    location: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    module = _name("linked")
    (outside / f"{module}.py").write_text("def work():\n    return 1\n")
    (location / f"{module}.py").symlink_to(outside / f"{module}.py")

    with pytest.raises(ValueError, match=r"no module .* in the code location"):
        resolve_handler(f"{module}:work", location)


def test_a_symlink_that_stays_in_the_code_location_is_followed(
    location: Path,
) -> None:
    """As a mounted ConfigMap lays out its files: links into a dated directory."""
    module = _name("tasks")
    data = location / "..2026_09_15"
    data.mkdir()
    (data / f"{module}.py").write_text("def work():\n    return 1\n")
    (location / "..data").symlink_to(data.name)
    (location / f"{module}.py").symlink_to(Path("..data") / f"{module}.py")

    assert resolve_handler(f"{module}:work", location)() == 1


@pytest.mark.parametrize(
    "target",
    ["os:system", "subprocess:run", "builtins:eval", "sys:exit", "posix:system"],
)
def test_the_standard_library_is_not_a_handler(location: Path, target: str) -> None:
    with pytest.raises(ValueError, match="code location"):
        resolve_handler(target, location)


@pytest.mark.parametrize("target", [".tasks:work", "tasks..x:work", "a/b:work", "work"])
def test_what_is_not_a_module_name_is_refused(location: Path, target: str) -> None:
    with pytest.raises(ValueError, match=r"not a module name|module:function"):
        resolve_handler(target, location)
