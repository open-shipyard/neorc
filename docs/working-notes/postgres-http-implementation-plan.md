# Plan: flows deployed on Postgres and HTTP

Goal: the flows that run in memory today run deployed — a manager service on
FastAPI and Postgres, a scheduler and workers reaching it over HTTP — and the
"Deployed" steps of [examples/hello](../../examples/hello/README.md) work as
written. Once they do, the task API goes, and the flow classes take its names.

Follows [in-memory-implementation-plan.md](in-memory-implementation-plan.md),
which built everything this plan deploys, and
[contributing/in-memory-first.md](../../contributing/in-memory-first.md): the
adapters add persistence, transport and their own concurrency, and nothing
about what a flow means. Each step is roughly under 1000 lines including tests,
leaves every existing check green, and is one pull request.

Behaviour is already specified by the contract suites in
`neorc_core.testing.contracts` and by the tests on `LocalCluster`. An adapter
is done when it passes its suite; tests in `neorc` cover only what an adapter
adds.

Out of scope: retries and archiving (open in
[postgres-implementation.md](../specs/postgres-implementation.md)),
authentication, schema migrations beyond create-if-absent, and a queue other
than Postgres.

## Steps

| #  | Step                                              | Status |
| -- | ------------------------------------------------- | ------ |
| 1  | Postgres schema for flows                         | done   |
| 2  | `PostgresStore`: flow versions, runs, events      | done   |
| 3  | `PostgresStore`: tasks, leases, run state         | done   |
| 4  | Manager routes for flows and runs                 | done   |
| 5  | Manager routes for the scheduler and workers      | done   |
| 6  | HTTP clients for flows                            | done   |
| 7  | Manager service on Postgres, two channels         | done   |
| 8  | `flows upload`, `scheduler start`, `worker start` | done   |
| 9  | The examples end to end, deployed                 | todo   |
| 10 | Remove the task API                               | todo   |
| 11 | Flow classes take the task API's names            | todo   |

### 1. Postgres schema for flows

Tables, and one rule in core so both stores accept the same values.

- `neorc_core/_values.py`: a string holding NUL (`\u0000`) is an
  `InvalidValueError`, in values and in flow definitions: Postgres `text` and
  `jsonb` cannot store it, and the in-memory store must refuse what the
  deployed one would. Task errors and run reasons, which are not values, have
  NUL replaced before they are stored, in core. Contract cases for both.

- `neorc/postgres/_schema.py`: add, next to `neorc_tasks`,
  - `neorc_flow_versions`: name, version as three integers, content as text,
    primary key (name, major, minor, patch). The content is the upload's
    parsed structure written as canonical JSON — keys sorted, no insignificant
    whitespace — never the text as it was sent.
  - `neorc_runs`: id, flow, version, inputs, status, root id, parent id and
    parent address, output, reason; indexes on root id, on
    parent id, and a partial one on active runs by flow for upload cancellation
  - `neorc_flow_tasks`: id, run id, address, queue, handler, params, fixed
    params, status, attempts, lease expiry, result, error, and
    `position`, a `bigint` identity column that orders tasks by publication; a
    partial claim index on (queue, position) for pending and leased tasks
  - `neorc_events`: sequence (`bigint`, unique), run id, kind
- Values — run inputs and outputs, task params, fixed params and results — are
  `text` holding their compact JSON from `_values.dumps_json`, read back with
  `json.loads`: `jsonb` stores numbers as `numeric` and would hand back
  `1e16` as an integer and `-0.0` as `0.0`, where the memory store keeps them.
  A contract case covers both.
- No foreign keys between the flow tables: an insert would take a `KEY SHARE`
  lock on the run it references, outside the lock order in step 2. The store's
  operations keep the references whole.
- The `CHECK` constraints mirror `TaskStatus`, `RunStatus` and `EventKind`;
  a test checks the values stay in step with the enums.
- Row mapping to and from `StoredFlow`, `Run`, `FlowTask` and `Event`, using
  `neorc_core._wire` for addresses and references, so there is one textual
  form of each.
- Tests: `create_schema` is idempotent and creates every table and index.

### 2. `PostgresStore`: flow versions, runs, events

- `neorc/postgres/_store.py`: `PostgresStore(Store)` beside
  `PostgresTaskStore`, on its own pool, in `READ COMMITTED`.
- Every operation follows the lock order below, so the rules `MemoryStore`
  keeps under one lock hold here too, and no two transactions wait on each
  other in a cycle.
- `store_flows`: take the upload lock exclusively, read every stored version,
  call `check_uploads`, insert the new versions, lock the roots of the active
  run trees of their flows, cancel those trees, append the events.
- Whether an upload is identical is decided on structure, by `check_uploads`,
  as in memory: spacing, indentation, line breaks, comments and key order
  outside strings are not a change; a change inside a string, or a number or
  boolean of another JSON type, is. A contract case uploads a flow, then the
  same flow reformatted, and expects nothing stored and no run cancelled.
- `start_run`: take the upload lock shared, then read the flow's latest
  version, so an upload cannot replace it until this run is committed and
  visible to that upload's cancellation. A sub-flow run also locks its root and
  checks the parent is active; its id from `sub_run_id_for` makes a repeat an
  `ON CONFLICT DO NOTHING`.
- `succeed_run`: lock the run's root, then `UPDATE ... WHERE id = $1 AND
  status = 'active' RETURNING ...`; no row back is `RunStateError`, or
  `RunNotFoundError` when the run does not exist; one event.
- `fail_run_tree`, `cancel_run_tree`: take any run of the tree, read its
  `root_id`, lock the root, then `UPDATE ... WHERE root_id = $root AND status =
  'active' RETURNING id` as a statement of its own, and an event per returned
  run.
- `get_flow`, `latest_flows`, `get_run`, `events_after`.
- Run `StoreContract` on Postgres, overriding its task tests with strict
  `xfail` until step 3.

#### Lock order

Each transaction takes only the locks it needs, always in this order:

1. The upload lock, a transaction-level advisory lock: exclusive in
   `store_flows`, shared in `start_run`.
2. Root run rows, `FOR UPDATE`, in id order: in every operation that adds to,
   finishes or reads the status of a run tree — starting a sub-flow run,
   `publish_task`, `start_task`, `succeed_run`, failing, cancelling, and the
   cancellation in `store_flows`.
3. Other rows: runs, tasks.
4. The event lock, a second transaction-level advisory lock, taken last, by a
   statement of its own. A later statement appends every event of the
   transaction, with sequences allocated as `max(sequence) + 1`: in `READ
   COMMITTED` that statement's snapshot is taken after the lock is granted, so
   it sees the events of the transaction that held the lock before. Nothing is
   locked after it.

Why this holds:

- A tree update runs after its root is locked, as a statement of its own, so in
  `READ COMMITTED` it sees every child committed before the lock was granted;
  and a child cannot be added without the same root lock. A cancellation
  therefore never misses a sub-run or task, and nothing is added to a tree
  after it stopped.
- Events commit in sequence order, because appending them is serialised and
  comes last; a scheduler reading after a sequence misses none.
- Locks always taken in the same order leave no cycle to deadlock on. With no
  foreign keys, an insert takes no lock on another row behind the order's back.

### 3. `PostgresStore`: tasks, leases, run state

- `publish_task`: lock the run's root, check the run is active, insert with
  `task_id_for`, `ON CONFLICT DO NOTHING`.
- `claim_task(queue)`: the existing claim, per queue, oldest first, `FOR UPDATE
  SKIP LOCKED`.
- `start_task`: lock the run's root, then the task; an inactive run fails the
  task and commits before `RunStateError` is raised, so the lease never hands
  it out again.
- `extend_task_lease`, `finish_task` with its event, `get_task`.
- `run_state`: the run, its tasks and its sub-runs in one snapshot
  (`REPEATABLE READ`), handed to `run_state_of`.
- The `xfail` marks from step 2 go; `StoreContract` passes in full.
- Tests beyond the suite, each two transactions forced to interleave:
  concurrent claims across connections; a task start racing a cancellation; a
  sub-flow run start racing a cancellation of its tree; a run start racing an
  upload of its flow; and two transactions appending events where the second
  waits on the event lock while the first commits, read back with distinct
  sequences and no gap; and `finish_task` recording its event while its run's
  tree is being cancelled.

### 4. Manager routes for flows and runs

- `neorc/manager/_flow_routes.py`, on `FlowManager`, bodies and responses in
  the `neorc_core._wire` forms:
  - `POST /flows` — upload a set; `GET /flows/{name}` and
    `GET /flows/{name}/versions/{version}`
  - `POST /flows/{name}/runs` — start a run; `GET /runs/{id}`;
    `POST /runs/{id}/cancel`; `GET /runs/{id}/state`
- Request bodies are read as bytes, checked with `ensure_json_depth` and only
  then parsed, before any model sees them: FastAPI's own parsing would hand a
  deeply nested body to the C parser first. Too deep is a 422 carrying
  `InvalidValueError`; a test sends one.
- Errors, shared with step 5: every `NeorcError` the manager raises crosses as
  `{"error": "<exception class>", "detail": "...", "problems": [...]}`,
  `problems` for `FlowDefinitionError` only. Clients raise the exception the
  `error` field names, since several share a status: `FlowDefinitionError`,
  `InvalidValueError` and `ResolutionError` 422, `PayloadTooLargeError` 413,
  `FlowVersionError`, `RunStateError` and `TaskStateError` 409, the not-found
  errors 404. A test lists every `NeorcError` subclass in core and checks each
  has a status and round-trips through a client.
- A request body limit well above the payload limit, 16 MiB, only to protect
  the process: the size rules stay in core, where `publish_task` and
  `report_finished` apply them, so a request the in-memory manager accepts is
  not refused by HTTP first.
- Tests over an ASGI transport, on the memory store: routing, status codes and
  request validation only.

### 5. Manager routes for the scheduler and workers

- Scheduler: `GET /events?after=&limit=` long-polled up to the manager's
  deadline; `POST /runs/{id}/tasks` and `POST /runs/{id}/sub-runs` by address;
  `POST /runs/{id}/succeed` with the output reference; `POST /runs/{id}/fail`.
- Workers: `GET /queues/{queue}/tasks` for the task definitions;
  `POST /queues/{queue}/tasks/next` long-polled; `POST /flow-tasks/{id}/started`,
  `/heartbeat` and `/finished`.
- The `/tasks` routes of the task API stay as they are until step 10; the flow
  task routes live under `/flow-tasks` meanwhile.
- Tests over an ASGI transport: the long-poll deadline caps a longer request,
  and a start refused for an inactive run is a 409.

### 6. HTTP clients for flows

- `neorc/http/_flow_clients.py`: `HttpManagerClient(ManagerClient)` and
  `HttpFlowQueueClient(FlowQueueClient)` on httpx, request and response bodies
  through `neorc_core._wire`. An error body raises the core exception its
  `error` field names, with a `FlowDefinitionError`'s problems intact; a body
  that names no known exception, and transport failures, raise
  `ManagerUnavailableError`.
- Values go through `transmit`'s checks before they are sent, so a value fails
  on the client as it does on a direct client.
- `FlowWorker`, in core, checks its result against the payload limit before
  reporting it, and reports an over-limit result as the task's error: a result
  over the HTTP body limit would otherwise be refused before the manager could
  fail the task, and the task would run again on every lease.
- Contract cases added for both client kinds: `publish_task` refused with
  `ResolutionError` for a fan-out over something not a list, and a worker's
  over-limit result failing its task once.
- `ManagerClientContract` and `FlowQueueClientContract` run on the HTTP clients
  against the real application over an ASGI transport.
- Tests beyond the suites: an unreachable manager, and a long poll cut at the
  manager's deadline rather than the client's.

### 7. Manager service on Postgres, two channels

- `build_app` assembles `FlowManager` on `PostgresStore` with two
  `PostgresTaskNotifier`s, on channels `neorc_task_ready` and
  `neorc_event_ready`, next to the task API's manager, and serves both route
  sets.
- `create_schema` creates the flow tables at startup with `--create-schema`.
- Notifications are sent after the transaction commits, as the manager does
  now: a wakeup is a hint, and a waiter re-reads the store.
- Tests on Postgres: a task published through one manager process wakes a
  worker waiting on another; an event does the same for a scheduler.

### 8. `flows upload`, `scheduler start`, `worker start`

- `neorc flows upload <dir> --manager-address`: read and validate the files as
  `LocalCluster.upload` does, send them as one set, print what was stored.
- `neorc scheduler start --manager-address`: `Scheduler` on
  `HttpManagerClient`, stopping on `SIGINT` and `SIGTERM`.
- `neorc worker start --manager-address --code-location [--queue]`:
  `FlowWorker` on `HttpFlowQueueClient`; `prepare` failing exits with every
  problem listed.
- `--tasks tasks.toml` stays for the task API's worker until step 10.
- Tests: argument parsing and wiring only, with the core classes faked, as
  `test_cli.py` does today.

### 9. The examples end to end, deployed

- The example scenarios move to `neorc_core.testing.examples`: functions that
  run `examples/hello` and `examples/wordplay` through a `ManagerClient` and
  assert their outputs. `tests/test_examples.py` in core runs them on
  `LocalCluster`.
- `neorc/tests/test_end_to_end.py` runs the same functions on uvicorn, Postgres
  and the HTTP clients, with a scheduler and one worker per queue.
- `examples/hello/README.md`: the deployed steps are runnable; the
  "not runnable yet" note goes. `examples/wordplay/README.md` gains the same
  deployed commands.

### 10. Remove the task API

- Delete `TaskStore`, `Manager`, `QueueClient`, `Worker`, `Task`, `Payload`,
  `MemoryTaskStore`, `DirectQueueClient`, `TaskStoreContract`,
  `QueueClientContract`, `PostgresTaskStore`, `HttpQueueClient`, the `/tasks`
  routes, `neorc_tasks`, and `worker start --tasks`.
- `TaskNotifier` stays: both notifiers use it.
- Specs and READMEs stop describing tasks published on their own; the
  CHANGELOG records the removal.

### 11. Flow classes take the task API's names

Mechanical, and may run over the size guideline for that reason alone.

- `FlowManager` → `Manager`, `FlowWorker` → `Worker`, `FlowQueueClient` →
  `QueueClient`, `FlowTask` → `Task`, `HttpFlowQueueClient` →
  `HttpQueueClient`, `DirectFlowQueueClient` → `DirectQueueClient`,
  `FlowQueueClientContract` → `QueueClientContract`, `/flow-tasks` → `/tasks`.
- The table keeps its name, `neorc_flow_tasks`: databases created before step
  10 still hold the task API's `neorc_tasks`, and create-if-absent would leave
  that table in place under the new code.

## Choices made while planning

Revisit if they are wrong.

- `PostgresStore` and the flow routes arrive next to the task API and replace it
  only at the end, so every step ships with the deployed task API still working.
- Flow content is stored as canonical JSON text of the parsed upload, not
  `jsonb` and not the text as sent. Formatting outside strings never makes a
  new version, as the specs say; everything inside the structure does, which
  `jsonb`'s number normalisation would blur.
- Values are stored as JSON `text` too, not `jsonb`: `jsonb` would change some
  numbers on the way back, and the deployed store must hand back what the
  memory store does. Querying inside values waits until something needs it.
- Uploads take an advisory lock exclusively and run starts take it shared, so
  two uploads cannot pass the set check against the same stored flows, and a
  run cannot start on a version an upload is replacing.
- A run tree's root row is its lock: anything that adds to a tree or ends it
  locks the root first, so a cancellation and a new child cannot miss each
  other.
- Event appends take a transaction-level advisory lock, last in every
  transaction, so sequences commit in order and a scheduler reading after a
  sequence misses nothing. That lock serialises every write that records an
  event; revisit when it shows up in a profile.
- An error crosses HTTP as the name of its core exception, not only a status,
  because callers branch on the type: a worker drops a task only on
  `RunStateError`.
- Tasks are claimed in publication order, by an identity column: their ids come
  from their address and carry no order.
- The 1 MiB limit stays where the specs put it, on task payloads and results,
  enforced in core. HTTP adds only a much larger limit that protects the
  process, so the deployed and in-memory managers refuse the same requests.
- NUL in strings is refused in core rather than escaped by the Postgres adapter:
  it has no use in these values, and refusing keeps one rule for every store.
- HTTP bodies are the `neorc_core._wire` forms, so the direct and HTTP clients
  send the same JSON and the manager's routes stay a translation.
- A deployment runs one scheduler. Several at once would plan the same run
  together; publishing is idempotent, but succeeding and failing a run are not
  designed for that yet, and would need a lock per run or a leader.
