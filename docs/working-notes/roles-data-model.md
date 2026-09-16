# Roles: a data model sketch

A first sketch of how [roles.md](../specs/roles.md) could be stored and
checked. Not a plan yet: no steps, no tests. It builds on what
[sso-plan.md](sso-plan.md) shipped, which left `Principal`, `ApiToken` and
the manager's one access dependency as the places roles go.

## The three things

- **Permission**: one operation on one kind of resource, such as
  `tasks:claim`. The unit a route checks.
- **Role**: a fixed, named set of permissions, shipped in code. Roles are not
  editable (roles.md, "Not in scope"), so they live in `neorc_core`, not in
  the database.
- **Binding**: which role a credential holds and, for a role whose
  permissions apply to a resource, which resource. Today the only resource is
  a queue; later it may be a flow (roles.md, "Roles").

The binding is what keeps roles fixed while queues are not. Queue names are
whatever flow files say (`python-gpu`, `java-finance`), so a role per queue,
such as `worker-default`, cannot be shipped for every queue: there is one
`worker` role, and each worker token is bound to its queue.

## Permissions

Every route the API has, and the permission it needs. Anything not listed is
refused (roles.md: the permissions are exhaustive).

| Permission         | Resource | Routes                                                                  |
| ------------------ | -------- | ----------------------------------------------------------------------- |
| `flows:upload`     | —        | `POST /flows`                                                           |
| `flows:read`       | —        | `GET /flows`, `/flows/{name}`, `/flows/{name}/versions[/{version}]`     |
| `runs:start`       | —        | `POST /flows/{name}/runs`                                               |
| `runs:read`        | —        | `GET /runs`, `/runs/{id}`, `/runs/{id}/tasks`, `/runs/{id}/sub-runs`, `/runs/{id}/state`, `/tasks/{id}` |
| `runs:cancel`      | —        | `POST /runs/{id}/cancel`                                                |
| `runs:schedule`    | —        | `POST /runs/{id}/tasks`, `/runs/{id}/sub-runs`, `/runs/{id}/succeed`, `/runs/{id}/fail` |
| `events:read`      | —        | `GET /events`, `/events/latest`                                         |
| `queue:definitions`| queue    | `GET /queues/{queue}/tasks`                                             |
| `queue:receive`    | queue    | `POST /queues/{queue}/tasks/receive`                                    |
| `tasks:claim`      | queue    | `POST /tasks/{id}/claim`                                                |
| `tasks:heartbeat`  | queue    | `POST /tasks/{id}/heartbeat`                                            |
| `tasks:finish`     | queue    | `POST /tasks/{id}/finished`                                             |

Public, with no permission: `GET /health`, `/openapi.json`, `/auth/*` and the
UI's static files.

For `tasks:*` the queue is not in the path: the manager loads the task and
compares its queue with the token's. A task on another queue answers as not
found rather than forbidden, so a token cannot learn which ids exist.

## Roles

| Role               | Permissions                                                                 | Bound to |
| ------------------ | --------------------------------------------------------------------------- | -------- |
| `worker`           | `queue:definitions`, `queue:receive`, `tasks:claim`, `tasks:heartbeat`, `tasks:finish` | a queue  |
| `scheduler`        | `flows:read`, `runs:read`, `runs:schedule`, `events:read`                   | —        |
| `ci`               | `flows:upload`                                                              | —        |
| `user`             | `flows:read`, `runs:start`, `runs:read`, `runs:cancel`, `events:read`       | —        |
| `external-trigger` | `runs:start`                                                                | —        |

Where this goes beyond roles.md as written:

- `scheduler` gets `runs:read` because it calls `GET /runs/{id}` and
  `GET /runs/{id}/state` (`neorc_core/_scheduler.py`); "read task results"
  in roles.md is `run_state`.
- `user` gets `events:read`: the UI long-polls `/events` to refresh
  (`ts/neorc-ui/src/api.ts`, `waitForEvents`). roles.md does not list it.
- `external-trigger` cannot read the run it started beyond the response to
  `POST /flows/{name}/runs`. Open: add `runs:read`, or keep it write-only.

In code:

```python
class Permission(StrEnum):
    FLOWS_UPLOAD = "flows:upload"
    ...

class Role(StrEnum):
    WORKER = "worker"
    SCHEDULER = "scheduler"
    CI = "ci"
    USER = "user"
    EXTERNAL_TRIGGER = "external-trigger"

PERMISSIONS: Mapping[Role, frozenset[Permission]] = {...}
QUEUE_BOUND: frozenset[Role] = frozenset({Role.WORKER})
```

## Storage

Tokens gain their binding as two columns. One role per token: roles.md needs
no more, and a token for two jobs is two tokens.

```sql
CREATE TABLE IF NOT EXISTS neorc_api_tokens (
    id               uuid PRIMARY KEY,
    name             text NOT NULL UNIQUE,
    secret_hash      text NOT NULL UNIQUE,
    role             text NOT NULL
                     CHECK (role IN ('worker', 'scheduler', 'ci', 'user',
                                     'external-trigger')),
    queue            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz,
    CHECK ((role = 'worker') = (queue IS NOT NULL))
);
```

Sessions get no column: a signed-in person is `user`, the only role a person
can have while "multiple different users roles" is out of scope. When that
changes, the allow list in `auth.toml` is where a role would be given, and
the session would store it.

`Principal` gains `role: Role` and `queue: str | None`. `ApiToken` gains the
same, so `neorc tokens list` shows them.

When permissions on flows arrive, one token may need several bindings, such
as `user` on flows `a` and `b`. The columns then move to a table:

```sql
CREATE TABLE neorc_bindings (
    token_id  uuid REFERENCES neorc_api_tokens (id) ON DELETE CASCADE,
    role      text NOT NULL,
    resource  text,          -- 'queue:default', 'flow:word_picker', or NULL
    PRIMARY KEY (token_id, role, resource)
);
```

Not before: nothing needs it today.

## Tasks

roles.md makes the task id the credential a worker holds:

- `id` becomes a random UUID made when the task is first published, replacing
  `uuid5(run_id, "task:<address>")`.
- Publishing the same address twice must still return the first task, so
  the uniqueness moves to `UNIQUE (run_id, address)`, and the insert's
  conflict target with it. Sub-run ids can stay `uuid5`: no worker uses them.
- The payload size check (`_manager.py`) encodes an id before one exists; a
  placeholder UUID has the same length.
- `neorc.task_id` handed to a handler is read from the stored task, not
  computed.

## Checking

The manager's access dependency resolves the credential to a `Principal`, as
today, and each route declares its permission. The check is:

1. The role has the permission, or the request is refused with 403.
2. For a queue-bound permission, the resource's queue is the principal's:
   the path's `{queue}`, or the task's stored queue. A mismatch on a path
   queue is 403; on a task id, 404.

`--no-auth` keeps today's meaning: no principal, every permission.

## Open

- A worker whose lease lapsed still holds a valid task id after another
  worker receives the task. Accepted, since delivery is at-least-once, or an
  id per receive?
- Whether `external-trigger` may read runs, and whether it may start any flow
  or only named ones: the second is a flow binding, so it waits for flow
  resources.
- Worker start-up (roles.md: "link to the received queue"): a separate
  endpoint, or the first `GET /queues/{queue}/tasks` answering 403?
- `neorc tokens create --role user`: allowed, for a person's scripts, or
  sessions only?
