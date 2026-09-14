# Plan: a web UI that ships in the wheel

Goal: after `pip install 'neorc[manager]'`, with no Node.js on the machine,
`neorc manager start` serves, next to its API, a React UI that shows the
flows, the runs and their trees, and every task's status, inputs, result and
error, updating live; and from which runs can be started and cancelled. Flows
keep being uploaded with `neorc flows upload`.

There is no authentication yet: anyone who reaches the UI or the API can do
everything they allow. That is accepted for development and testing.
Authentication gets its own plan, and no release is tagged before it lands.

Builds on the manager as it is since the flows moved onto FastAPI and Postgres
(CHANGELOG, and [decisions-from-past-plans.md](decisions-from-past-plans.md)
for what those plans decided), and follows
[contributing/in-memory-first.md](../../contributing/in-memory-first.md): the
queries the UI needs are added in core and tested in memory; the manager's
routes only translate. Each step is roughly under 1000 lines including tests,
leaves every existing check green, and is one pull request.

Node.js is needed where the UI is built, which is CI and the machines of people
working on the UI, and never where it runs.

## Shape

    ts/neorc-ui/          the React app: Vite, TypeScript, package-lock.json
    python/neorc-ui/      distribution "neorc-ui": the built assets, no dependencies
      src/neorc_ui/
        __init__.py       static_dir(): where the built assets are
        static/           built by CI, gitignored, included in wheel and sdist
      hatch_build.py      builds ts/neorc-ui when static/ is missing
    python/neorc/src/neorc/
      manager/_routes.py  the routes the UI reads and writes, next to the others
      manager/_ui.py      mounting the assets at /ui
      _cli.py             neorc manager start --no-ui

    pip install 'neorc[manager]'  ->  fastapi, uvicorn, neorc-ui

The assets live in their own distribution so that worker and publisher hosts,
which never serve a page, do not carry them, and so that `neorc-core` and
`neorc` keep building with a plain `uv build` and no Node.js.

The UI talks to the manager's existing API: `GET /flows/{name}`,
`GET /flows/{name}/versions/{version}`, `POST /flows/{name}/runs`,
`GET /runs/{id}`, `POST /runs/{id}/cancel`, `GET /runs/{id}/state` and the
long-polled `GET /events` are there already, on `Manager`, with bodies in the
`neorc_core._wire` forms and errors as
`{"error": "<exception class>", "detail": "...", "problems": [...]}`. What it
still lacks is everything that lists: flows, versions, runs, a run's tasks and
sub-runs.

To see the UI locally: Postgres, `neorc manager start`, `neorc scheduler
start`, one `neorc worker start` per queue, and `neorc flows upload`, as the
"Deployed" steps of [examples/hello](../../examples/hello/README.md) do. The
in-memory `neorc run` has no UI: it prints its run's output and exits.

## Steps

| #  | Step                                                | Status |
| -- | --------------------------------------------------- | ------ |
| 1  | Read queries and timestamps, in core and in memory  | todo   |
| 2  | The same on the Postgres store                      | todo   |
| 3  | The manager routes the UI needs                     | todo   |
| 4  | `neorc-ui` distribution and its build               | todo   |
| 5  | The manager serves the UI                           | todo   |
| 6  | UI: flows and runs                                  | todo   |
| 7  | UI: a run, its tree and its tasks, live             | todo   |
| 8  | UI: start and cancel runs                           | todo   |
| 9  | Browser smoke test, docs and changelog              | todo   |

### 1. Read queries and timestamps, in core and in memory

Core only. Today the `Store` port has `get_run`, `run_state`, `get_flow` and
`latest_flows`; `run_state` folds a run's tasks and sub-runs into step
outcomes and values, which is what the scheduler needs and not what a UI
shows. Nothing lists.

- `Store` port, one method per query:
  - `list_runs(*, flow=None, status=None, root_only=True, before=None,
    limit=50)`: newest first, `before` a cursor from the previous page.
  - `flow_versions(name)`: every stored version of a flow, newest first.
  - `run_tasks(run_id)`: a run's `Task`s, in publish order.
  - `sub_runs(run_id)`: a run's direct sub-flow runs.
- `Run` gets `created_at` and `finished_at`; `Task` gets `created_at`,
  `started_at` and `finished_at`. Neither model nor either store has a
  timestamp today, only the task lease expiry. The store sets them, as it sets
  the lease expiry: when it creates, starts and finishes the row. A UI with no
  timestamps is not much use, and `list_runs` orders by `created_at`.
- `MemoryStore` implements them; `StoreContract` gets a test per query,
  including paging and filters, and one that the timestamps are set and
  ordered.
- `PostgresStore` implements the port too, so the new methods come with the
  contract's Postgres run marking them strict `xfail` until step 2, as the
  Postgres store's own steps did while it was incomplete.
- `Manager` exposes them unchanged, next to `get_run` and `run_state`.
- The `_wire` forms of `Run` and `Task` carry the timestamps, so the HTTP
  clients and the routes see them.

### 2. The same on the Postgres store

- `neorc/postgres/_schema.py`: the timestamp columns on `neorc_runs` and
  `neorc_flow_tasks`, `timestamptz`. `create_schema` adds them with
  `ADD COLUMN IF NOT EXISTS` as well, since databases created before this
  step exist and no release is out to migrate.
- Indexes for listing runs by flow, status and time: on `neorc_runs`
  `(created_at, id)` for the page cursor, and `(flow, created_at)`; the existing
  partial index on active runs serves the status filter.
- `PostgresStore` implements the four queries; `list_runs` pages by
  `(created_at, id)`, not by offset. The `xfail` marks from step 1 go.
- Nothing here takes a lock outside the store's documented order: reads only.

### 3. The manager routes the UI needs

Each new route calls one `Manager` method, as the existing ones do; bodies
and responses in the `_wire` forms; errors through `_errors.py` as they are,
so `InvalidValueError` is 422, `FlowVersionError` and `RunStateError` 409, the
not-found errors 404.

- Reads, in `_routes.py`:
  - `GET /flows`: latest version of each flow, on `latest_flows`.
  - `GET /flows/{name}/versions`.
  - `GET /runs?flow=&status=&root_only=&before=&limit=`.
  - `GET /runs/{id}/tasks` and `GET /runs/{id}/sub-runs`. `GET /runs/{id}`
    keeps returning the run alone: `HttpManagerClient.get_run` reads it.
  - `GET /tasks/{id}`.
- No writes: `POST /flows/{name}/runs` and `POST /runs/{id}/cancel` are there,
  and the UI does not upload.
- No event stream of its own: the UI long-polls `GET /events?after=N`, the
  route the scheduler uses, with its cursor and the manager's deadline. The
  log is read, not consumed, so the two readers do not interfere.
- A route test per new endpoint and per error, over an ASGI transport on
  `MemoryStore`, next to the existing route tests.
- `scripts/export_openapi.py` writes `create_app`'s schema to
  `ts/neorc-ui/openapi.json`; a test fails when the committed file differs
  from the app's. The UI build reads the snapshot, so it needs no Python.

### 4. `neorc-ui` distribution and its build

No UI yet: a page that fetches `/flows` and lists the names, to prove the
pipeline.

- `ts/neorc-ui/`: Vite, React, TypeScript; `base: "./"` so the app works under
  any path prefix; `package-lock.json` committed; the Node.js version pinned in
  `.nvmrc` and `engines`. Types generated from `openapi.json` with
  `openapi-typescript`.
- `python/neorc-ui/`: `pyproject.toml` modelled on `neorc-core`'s, with no
  dependencies and the same `LICENSE`, `NOTICE` and `AUTHORS` copies, which
  the `lint` job's sync check picks up from `python/*/` unchanged;
  `neorc_ui.static_dir()` returns the assets directory, and raises an error
  naming the fix when it has no `index.html`, as in an editable install that
  was never built.
- `hatch_build.py`: when `static/index.html` is missing, runs `npm ci` and
  `npm run build` in `ts/neorc-ui` and copies `dist/` to `static/`; without
  Node.js it fails naming the version in `.nvmrc`. `static/` is in `artifacts`
  for the wheel and for the sdist, so building a wheel from the sdist needs no
  Node.js.
- Third-party licenses of the bundled JavaScript collected at build time
  (`rollup-plugin-license`) into `static/THIRD_PARTY_LICENSES.txt`, and listed
  in the distribution's `license-files`.
- A bundle size budget, 500 KB gzipped for all assets, checked by the build.
- `neorc`: the `manager` extra becomes
  `["fastapi>=0.120", "uvicorn>=0.38", "neorc-ui"]`, so every manager install
  brings the UI.
- Root `pyproject.toml`: `neorc-ui = { workspace = true }` in
  `[tool.uv.sources]`; the `dev` group already installs `neorc[manager]`.
  CONTRIBUTING: the new layout, and working on the UI with `npm run dev`
  proxying the API to a local `neorc manager start`; installing Node.js is
  already in `contributing/dev-environment.md`.
- CI: a `ui` job with `actions/setup-node` running lint, type check and build;
  the `build` job gets Node.js too, since `uv build --all-packages` now builds
  `neorc-ui`. A test builds the `neorc-ui` wheel and asserts `index.html` and
  the license file are in it.
- `release.yml`: `neorc-ui` in the `package` matrix of both the `build` and
  `publish-pypi` jobs, published between `neorc-core` and `neorc`, ready for
  the first release after authentication.
- Python tests never need built assets: they point the mount at a temporary
  directory.

### 5. The manager serves the UI

- `neorc/manager/_ui.py`: `mount_ui(app, path="/ui")` mounts `StaticFiles` on
  `neorc_ui.static_dir()`, and redirects `/` to `/ui/`. Hashed assets get
  `Cache-Control: immutable`, `index.html` `no-cache`. The UI uses hash routing
  (`/ui/#/runs/...`), so no fallback route is needed.
- `build_app` mounts the UI after the routes; `neorc manager start --no-ui`
  leaves it out and keeps the API, and `/` then answers 404 as today.
- Tests: with a fake assets directory, the app serves `index.html`, redirects
  `/`, and sets the cache headers; with `--no-ui` it does not; the missing
  assets error names the fix. The end-to-end test on uvicorn and Postgres also
  fetches `/ui/` and the listing routes.

### 6. UI: flows and runs

- Layout, navigation and a status badge shared by runs and tasks.
- A permanent banner: no authentication, for development and testing only.
- Flows: latest version of each, its versions, the definition as read-only text.
- Runs: a paged list filtered by flow and status, newest first; new runs appear
  from the event stream without a reload.
- Data fetching with TanStack Query; a long poll on `/events` runs for the
  life of the page and invalidates the queries each event concerns.
- Component tests with Vitest and Testing Library against recorded API
  responses.

### 7. UI: a run, its tree and its tasks, live

- The run's steps drawn from its version's definition: tasks, loops with their
  iterations, fan-outs with their branches, sub-flows linking to their runs.
  React Flow with an automatic layout (elkjs or dagre), within the size budget,
  or plain nested boxes if the budget does not allow it.
- Each task instance: status, queue, handler, attempts, lease expiry, params,
  result and error, and its times; values pretty-printed, `$datetime` shown as
  a date.
- The run tree: parent and sub-runs, with the cancellation or failure reason.
- Live: refetch on events for the run's tree, and every 2 s while any of its
  tasks is claimed or running.
- Component tests on a recorded `word_picker_rounds` run.

### 8. UI: start and cancel runs

- Start a run from a flow's page: a form built from the flow's declared inputs,
  one field per type (`string`, `number`, `boolean`, `datetime`); a datetime is
  sent as `{"$datetime": ...}` with the browser's timezone; a raw JSON editor
  for anything the form cannot express. The `detail` of the 422 is shown next
  to the form. On success, go to the new run.
- Cancel an active run from its page, after a confirmation naming how many runs
  in its tree are active.
- Component tests for both actions, including the error paths.

### 9. Browser smoke test, docs and changelog

- A Playwright test in CI on the end-to-end fixture: Postgres, the manager on
  uvicorn, a scheduler and one worker per queue, `examples/wordplay` uploaded
  with `neorc flows upload`. Start `word_picker_rounds` from the form, wait
  for it to succeed, check a fan-out's branch results are shown; start another
  and cancel it.
- README quick start and the example READMEs: the UI at `/ui/` after
  `neorc manager start`, and that there is no authentication yet.
- The root CHANGELOG, the one file both packages point at: the new queries
  and timestamps in `neorc-core`, the routes and the UI in `neorc`, and the
  new `neorc-ui` distribution.

## Choices made while planning

Decided here to make progress. Revisit if they are wrong.

- No authentication yet, and write operations allowed: starting and cancelling
  runs, from the API and the UI. For development and testing only.
  Authentication is planned separately and lands before the first release;
  until then the UI and the READMEs say so.
- One way to serve the UI: the manager. An earlier draft added `neorc run
  --ui`, an in-memory cluster with a server on the same loop, for a laptop
  with no Postgres; it doubled the CLI's wiring, made `--flow` optional and
  the exit status conditional, and needed a hook on the app to start workers
  after an upload. Seeing a run locally now takes Postgres and the three
  deployed processes, which the tests already start.
- Flows are uploaded with `neorc flows upload`, never from the browser. An
  upload belongs with the code the workers load, and the CLI already reads and
  validates the files once, the same way for every caller; the UI would have
  reproduced that reading and its warning about cancelled runs for one more
  path.
- The manager keeps binding `0.0.0.0`, as it does today. With the UI, a
  manager started on a laptop puts start and cancel buttons on the LAN; the
  banner says so, and authentication is the fix.
- The UI uses the manager's API as it is, at its paths, and the routes it
  lacks are added beside the others. An earlier draft put a second set under
  `/api/v1` to leave the task API's paths alone; that API is gone, and two
  route sets on one `Manager` would be the same translation twice. No version
  prefix: `neorc-ui` ships in the same version as `neorc`.
- No server-sent events: the long-polled `GET /events?after=N` already gives
  the UI a cursor to resume from, and the manager's deadline keeps proxies from
  timing out. One route for both readers, the scheduler and the UI.
- Responses are the `_wire` forms, not Pydantic response models: the HTTP
  clients read those forms already, and a response the UI shows is the same
  JSON a client would get.
- Timestamps are set by the store, not by the manager, so they are consistent
  with the row they describe and `list_runs` can order on them in SQL.
- Hash routing in the UI, so serving is plain static files at any prefix, from
  FastAPI or any other server.
- No CDN: every asset is in the wheel, so the UI works on air-gapped hosts.
- The OpenAPI schema is a committed snapshot checked by a Python test, so the
  UI build needs no Python and the Python build needs no Node.js.
- `neorc-ui` shares the single version and tag. `neorc[manager]` does not pin
  it; the UI says so when a route it needs answers 404 without the manager's
  error body.
- No new event kinds for "task published" or "task started". The UI polls a
  run with work in flight instead, so the scheduler's event log is unchanged.
- The event log is read, not consumed (`events_after` is a slice in memory and
  a `SELECT` on Postgres), so the UI's poll and the scheduler read it side by
  side.
- Contributors who work only on Python never need Node.js: tests use a fake
  assets directory, and only the `neorc-ui` build runs npm.
- Contributors who work on the UI install Node.js themselves, at the version in
  `ts/neorc-ui/.nvmrc`; the repository does not provide it.
