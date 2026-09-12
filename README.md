# neorc

A next generation orchestration system.

This repository is a monorepo. The packages it publishes:

| Package                                  | Description                       |
| ---------------------------------------- | --------------------------------- |
| [`neorc`](python/neorc)                   | Top-level package                 |
| [`neorc-core`](python/neorc-core)         | Core primitives other packages build on |

Both are placeholders today: there is no public API yet.

Python packages live under `python/` and form a
[uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/); see
[CONTRIBUTING.md](CONTRIBUTING.md) for the layout and the development setup.

Licensed under the [Apache License 2.0](LICENSE).
