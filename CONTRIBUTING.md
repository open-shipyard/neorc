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

## Development setup

The project uses [uv](https://docs.astral.sh/uv/). Install the package and its
development tools into `.venv`:

    uv sync

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
