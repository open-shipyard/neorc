# Contributing to neorc

## Branching model

`main` is the only long-lived branch and must always be releasable. Direct
pushes to `main` are blocked.

1. Create a short-lived branch from `main` for each change, prefixed with
   `feature/` for new functionality or `fix/` for corrections:

       git switch main && git pull
       git switch -c feature/short-description

2. Open a pull request against `main`.
3. Once checks pass and review is complete, a maintainer merges it.

Releases are cut by maintainers by tagging a commit on `main` with a
[Semantic Versioning](https://semver.org/) tag such as `v0.2.0`. Fixes for a
release go through a regular pull request to `main` and ship in the next tag.

## Repository layout

This repository is a monorepo. Each distribution lives in its own directory and
is released from a single tag, so all packages share one version.

    python/neorc-core/    core primitives
    python/neorc/         top-level package, builds on neorc-core

The Python packages form a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
whose root is the `pyproject.toml` at the top of the repository: it holds the
shared ruff, mypy and pytest configuration and the single `uv.lock`. A package
depends on a sibling by naming it in its own `dependencies`; the root
`[tool.uv.sources]` resolves it to the workspace copy.

Packages in other languages get a sibling top-level directory (`rust/`, `ts/`)
with their own tooling.

Adding a Python package:

1. Create `python/<name>/` with a `pyproject.toml` modelled on an existing one,
   `src/<module>/` (including `py.typed`), `tests/` and a `README.md`.
2. Copy `LICENSE`, `NOTICE` and `AUTHORS` from the root into it. Each
   distribution ships its own copies and CI checks they stay identical.
3. Add it to `[tool.uv.sources]` and the `dev` dependency group in the root
   `pyproject.toml`, to `testpaths` if it has tests, and to the `package`
   matrices in `.github/workflows/release.yml`.
4. Name test modules so they are unique across the repository, for example
   `tests/test_<name>_package.py`; pytest and mypy collect them all together.

## Development setup

The project uses [uv](https://docs.astral.sh/uv/). Install the packages and the
development tools into `.venv`:

    uv sync

That installs every workspace member in editable mode. The checks below run
across the whole repository from the root.

Run the same checks as CI before opening a pull request:

    uv run ruff check
    uv run ruff format --check
    uv run mypy
    uv run pytest

New source files start with the license header:

    # Copyright 2026 The neorc Authors
    # SPDX-License-Identifier: Apache-2.0

## Developer Certificate of Origin

All contributions must be signed off under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/).
Add `-s` to your commit:

    git commit -s -m "component: short imperative summary"

which appends:

    Signed-off-by: Jane Doe <jane@example.com>

Use your real name and a reachable email address. Sign-off is a statement about
the provenance of your contribution, so pseudonymous sign-offs cannot be
accepted. If you are contributing on behalf of an employer, make sure you have
their authorization.
