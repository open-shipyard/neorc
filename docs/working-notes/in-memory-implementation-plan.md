# Plan: flows running in memory

Goal: `neorc_core` runs flows end to end in one process — YAML definitions, the
manager, the scheduler and workers — and `neorc run` executes `examples/hello`
and `examples/wordplay` with a single command, with no Postgres and no HTTP.

Follows [contributing/in-memory-first.md](../../contributing/in-memory-first.md)
and the specs in [docs/specs](../specs). Each step is roughly under 1000 lines
including tests, leaves every existing check green, and is one pull request.

Out of scope for this plan: the Postgres and FastAPI/HTTP adapters for flows.
They keep serving today's task API until a follow-up plan moves them onto the
new ports and runs the contract suites against them.

## Steps

| #  | Step                                        | Status |
| -- | ------------------------------------------- | ------ |
| 1  | Values and flow definitions                 | done   |
| 2  | `local` package and contract suite scaffold | done   |
| 3  | Reference resolution and run state          | done   |
| 4  | Planner: tasks, loops, fan-outs             | done   |
| 5  | Planner: sub-flows, output, failures        | done   |
| 6  | Store port and memory store                 | todo   |
| 7  | Manager: flows, runs, run trees             | todo   |
| 8  | Manager: tasks, payloads, events            | todo   |
| 9  | Client ports and direct clients             | todo   |
| 10 | Worker for flows                            | todo   |
| 11 | Scheduler and LocalCluster                  | todo   |
| 12 | `neorc run` and the examples as tests       | todo   |

### 1. Values and flow definitions

Pure code, no I/O beyond reading a file.

- `neorc_core/_values.py`: encode and decode values — JSON plus
  `{"$datetime": ...}`, reserved `$` keys, naive datetimes rejected, the 1 MiB
  limit on an encoded payload.
- `neorc_core/flows/`: the definition model (`FlowDefinition`, `TaskStep`,
  `LoopStep`, `FanOutStep`, `SubFlowStep`, `Reference`, `Version`), loading from
  YAML or from the JSON structure the manager receives, and validation:
  - unknown or missing keys, `name` and `version` first, semver versions
  - step names unique across the file, identifiers only
  - references exist and fit their context (`neorc.index` needs a fan-out,
    `neorc.item` a fan-out over a list, `neorc.loop_count` a loop)
  - no dependency cycles, checked per scope
  - a loop's exit condition is a task directly in its body
  - across a set of flows: sub-flows exist, their params match the called
    flow's inputs, and flows do not call themselves through sub-flows
- PyYAML becomes neorc-core's runtime dependency.
- Tests: values, loading both examples, one test per validation rule.

### 2. `local` package and contract suite scaffold

No behaviour change.

- Move `neorc_core/testing.py` to `neorc_core/local/`: `MemoryTaskStore`,
  `MemoryTaskNotifier`, `DirectQueueClient`.
- Create `neorc_core/testing/contracts/` and move the port-level tests of the
  store and queue client there as reusable suites; run them on the memory
  adapters in core and, unchanged, on Postgres and HTTP in `neorc`.
- Update imports in `conftest.py` and tests.

### 3. Reference resolution and run state

Pure code.

- `RunState`: a snapshot of a run — task outcomes and results addressed by step,
  loop iterations and fan-out indexes; sub-flow run outcomes.
- `neorc_core/flows/_references.py`: the "Resolving references" table of
  [flows.md](../specs/flows.md) as functions: given a consumer's address and a
  reference, whether it is available yet and the value's shape — single value,
  lists per fan-out and per loop, outermost first.
- Tests from the table, including every nesting in `examples/wordplay`.

### 4. Planner: tasks, loops, fan-outs

Pure code: `plan(definition, run_state) -> list[Action]`.

- Actions as data: `PublishTask`, `StartSubRun`, `SucceedRun`, `FailRun`.
- Readiness from the per-scope dependency graph of step 1.
- Loops: iteration 1 on entry, the next iteration when the exit task returns
  false, leaving the loop when it returns true, `FailRun` past `max_cycles`, a
  non-boolean exit result as a failure.
- Fan-outs: `range` widths, `over` widths from the upstream list, fan-in once
  every branch succeeded.
- Deterministic task addresses, so a redelivered event plans the same actions.
- Tests: plain data in, actions out, covering `word_picker`.

### 5. Planner: sub-flows, output, failures

- Sub-flow steps start a child run and complete when it succeeds; its output is
  referenced as `flows.<step>`.
- `SucceedRun` with the output reference once every step finished.
- A failed task or failed sub-run plans `FailRun`.
- Tests covering `word_picker_rounds`.

### 6. Store port and memory store

- `neorc_core/ports/_store.py`: the single store port, one method per atomic
  operation — store a flow version (with the upload rules and the run-tree
  cancellation in the same operation), start a run, publish a task by address
  (idempotent), claim, start, finish with a result, cancel and fail a run tree,
  succeed a run, append and read events, read a run state.
- Extend the task model: run id, step address, inputs as references and fixed
  values, result, the task and run statuses and transitions.
- `neorc_core/local/`: `MemoryStore`, one lock per operation, same lease rules.
- Contract suite for the store port, run on `MemoryStore`.

### 7. Manager: flows, runs, run trees

- `upload_flow` with the version rules: identical is a no-op, changed content on
  an existing version and lower versions rejected.
- `start_run(name, inputs)` on the latest version, inputs validated against the
  declared types.
- `cancel_run` and `fail_run` over the whole run tree; `succeed_run`.
- Tests on the memory adapters.

### 8. Manager: tasks, payloads, events

- `publish_task` with references; the size check on the complete encoded
  payload; rejection into inactive runs.
- Filling references in when a worker fetches a task; metadata known at publish
  time filled in by the manager.
- `report_started` rejected for inactive runs; `report_finished` with a result.
- Events ("run started", "task finished", "run finished") and a long poll for
  them, on the notifier.

### 9. Client ports and direct clients

- `QueueClient` extended for flows; a new `ManagerClient` port for the scheduler
  and flow uploads.
- `DirectQueueClient` and `DirectManagerClient` round-trip every value through
  `_values.py` and JSON.
- Contract suites for both client ports, on the direct clients.

### 10. Worker for flows

- Handler resolution by import path moves from `neorc/_cli.py` to
  `neorc_core/_handlers.py`, resolving from a code location.
- On startup: pull the queue's task definitions, check each handler imports and
  its parameters match `params` and `fixed_params`.
- Execute `handler(**inputs)`, sync handlers in a thread; fill in
  `neorc.attempts`; encode the result; drop a task whose start is rejected.

### 11. Scheduler and LocalCluster

- `neorc_core/_scheduler.py`: poll events, read the run state and the run's
  version definition, call `plan`, apply the actions; a rejection fails the run
  unless the run is already inactive.
- `neorc_core/local/_cluster.py`: `LocalCluster` and `run_local(flows_dir, flow,
  inputs)` — manager, scheduler and one worker per queue on one event loop.
- Behaviour tests on `LocalCluster`: cancellation by upload and by hand,
  failures across a run tree, a lost worker's lease.

### 12. `neorc run` and the examples as tests

- `neorc run <dir> --flow <name> --inputs <json>` in `neorc/_cli.py`, calling
  `run_local`.
- `python/neorc-core/tests/test_examples.py`: run `examples/hello` and
  `examples/wordplay`, asserting their outputs.
- Example READMEs: the single command replaces "not runnable yet".

## Choices made while planning

Open in the specs, decided here to make progress. Revisit if they are wrong.

- Flow file syntax follows `examples/wordplay`: `steps`, `loop`, `fan_out`
  with `range` or `over`, `flow` for sub-flows, `output`, `inputs` with types
  `string`, `number`, `boolean`, `datetime`.
- `queue` defaults to `default` when a task omits it.
- `range: N` gives indexes 1 to N; `neorc.loop_count` starts at 1 and counts the
  innermost enclosing loop.
- `max_cycles: N` allows at most N complete iterations.
- A loop's exit condition is a task directly in the loop body.
- YAML timestamps are not converted: a datetime in a flow file is written as
  `{"$datetime": ...}`, so a YAML file and its JSON upload are the same structure.
- Flow versions are `MAJOR.MINOR.PATCH`, with no pre-release or build suffix.
- The planner takes no event: it plans from the run's whole state, publishing
  every ready step instance not started yet. The event only tells the scheduler
  which run to look at, and a redelivered one cannot plan anything different.
- A loop starts its next iteration once every step of the current one has
  finished, not only its exit condition.
