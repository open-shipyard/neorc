# Workers and manager

Tasks and flows definitions are data, not code.

So the manager and the scheduler do not need to have the code or be redeployed when task code or definition change.

Workers would only need to be redeployed when the code needed in their queue change.

A worker on startup should:
1. receive from the startup command: a code location, a queue name (will be "default" if not specified), the manager address
2. contact the manager and pull the tasks for their queue.
3. verify every task's handler can be imported and its named parameters match the task's inputs in the flow definition.
4. confirm the manager it is healthy
5. start pulling tasks.

A worker validates its queue's task definitions only at startup. A mechanism for
the manager to tell workers to perform actions, such as revalidating flows
after a new version is uploaded, will come later; it is not designed yet.


Tasks and flows are defined in YAML and stored as text files under version control.

Will be one file per flow.

Flow files need to have in the first fields, a name and a version following semver.

A utility is provided for reading all the files and call the manager endpoints for updating. It is meant to run in CI/CD.

Storing a new version of a flow and cancelling that flow's active run trees
happen in the same transaction. See [core.md](core.md), "Cancellation and
failure".

The `manager` exposes one method for creating or updating a flow, in the payload will receive the exact same structure as the file is in the repo, but always translated to json if needed.

The manager will keep history of the different versions of a flow.

Uploading a flow whose name and version already exist:
- with content identical to the stored definition is a no-op: nothing is stored and no run is cancelled. Identical means the same JSON structure, so formatting and comments in the file do not count.
- with different content is rejected as a bad request: changed content needs a new version.

Uploading a version lower than the flow's highest stored version is rejected as a bad request. Versions only move forward, so the latest version is always the highest one.

Workers can be of multiple languages, not only Python. In their implementations they only need to know how to fetch tasks from the manager and route that to the executable code, transmitting back the results by http.

## Handlers

Each task in a flow file names its handler as an import path, in the format of
the runtime that serves the task's queue. For Python it is `module:function`,
resolved from the worker's code location, then the usual import path:

```yaml
my_task:
  queue: python-default
  handler: myapp.tasks:my_task_handler
```

A worker in another language defines its own format for the same field. The
manager and the scheduler treat the handler as an opaque string; only the
worker resolves it.

Task definitions are local to their flow. One handler can back several tasks,
in the same flow or in different flows.

## Values

Flow inputs, task inputs and task results are JSON values plus datetimes: dicts,
lists, strings, numbers, booleans, null and datetimes. A result is at most
1 MiB, and is stored in its task row.

User code always sees a datetime as a datetime, whether it arrives as a flow
input or as an upstream task's result. neorc serializes datetimes to strings on
the wire and in storage, and deserializes them back before calling a handler;
handlers never parse them.

### Datetime encoding

A datetime is encoded as an object with a single `$datetime` key holding an
ISO 8601 string, at any depth of a value:

```python
def record_order(order_id):
    return {
        "order_id": order_id,
        "when": datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc),
    }
```

is stored and transmitted as

```json
{"order_id": 42, "when": {"$datetime": "2026-09-13T10:00:00+00:00"}}
```

and a handler reading that value receives `when` as a datetime again.

Rules, applied by every worker runtime wherever it encodes or decodes a value:

- An object whose only key is `$datetime` is a datetime. Its value must be an
  ISO 8601 string with a timezone; anything else, such as `{"$datetime": 34}`,
  is a value error.
- Datetimes must carry a timezone. Encoding a naive datetime is a value error.
- Keys starting with `$` are reserved for neorc. A user value containing one,
  in any object, is a value error. There is no escape.


