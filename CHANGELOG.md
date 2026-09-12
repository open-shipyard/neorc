# Changelog

All notable changes to this project are documented in this file. It covers
every package in the repository; entries name the package they affect.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). All packages share
one version, cut from a single tag on `main`.

## [Unreleased]

### Added

- Initial monorepo structure: a uv workspace with the `neorc-core` and `neorc`
  Python packages under `python/`.
- `neorc-core`: the `Task` model, the `QueueClient`, `TaskStore` and
  `TaskNotifier` ports, and the `Manager` and `Worker` classes. Scaffolding —
  every method raises `NotImplementedError`.
- `neorc`: the `http`, `manager` and `postgres` subpackages behind extras of the
  same name, and the `neorc` console script. Scaffolding, as above.
- Claims are leases: workers heartbeat to hold a task, and a task whose lease
  lapses returns to the queue. Chosen so the queue can move to SQS unchanged.

[Unreleased]: https://github.com/open-shipyard/neorc/commits/main
