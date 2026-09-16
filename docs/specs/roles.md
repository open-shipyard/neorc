# Roles and access control

## Roles

### Workers

Workers receive a token that can only access one specific queue.


Scope of the tokens must allow fetching task definitions, receiving, claiming, heartbeat and reporting status for tasks for a single, specific queue.

When a worker is started, it will try to link to the received queue parameter at the start command, and will send the available token. If the manager refuse the token for that queue the worker will fail to start.

Claiming a task should be done by a hash, not by a sequential task ID that workers could guess to claim task they were not assigned.

The hash is a randomly created UUID by the manager when the scheduler request a new task execution. It is provided to the worker on "receive" and should be the primary Id the worker uses through task lifecycle.


### Scheduler

Scheduler will have a dedicated default role with all needed to carry its responsibilities.

It should not be able to create, modify or delete flows.

### CI

The CI should be able to post new versions of a flow.
The manager may cancel ongoing runs, as result of an upload, but nothing CI will do directly.

### User

Anyone using the UI, will be able to execute new runs, query the status, and cancel them. Also list available flows.


### External trigger

Should be able to trigger new runs.


## Token creation

They are done with the CLI connecting to the DB.


## Not in scope

At this version the following are not needed, and may be planned in future work.

- Role editing. Only the default, fixed roles are shiped.
- Multiple different users roles.
- UI for querying roles or users.
