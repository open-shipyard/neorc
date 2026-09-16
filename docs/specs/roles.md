
## Roles

### Workers

Workers receive a token that can only access one specific queue.


Scope of the tokens must allow fetch task definitions, receiving, claiming, heartbeat and reporting status for tasks for a single, specific queue.


Claiming a task should be done by a hash, not by a sequential task ID that workers could guess to claim task they were not assigned.



### Scheduler

Scheduler will have a dedicated default role with all needed to carry its responsibilities.

It should not be able to create, modify or delete flows.

### CI

The CI should be able to post new versions of a flow.
The manager may cancel ongoing runs, as result of an upload, but nothing CI will do directly.

### User

Anyone using the UI, will be able to execute new runs, query the status, and cancel them.

### External trigger

Should be able to trigger new runs.


## Not in scope

At this version the following are not needed, and may be planned in future work.

- Role edition. Only the default, fixed roles are shiped.
- Multiple different users roles.
- UI for querying roles or users.
