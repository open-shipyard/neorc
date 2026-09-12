# Neorc

## The libs
neorc-core will host all the mechanism and interactions between components, but no specific persistence layer or vendor specific products.

neorc will provide a base reference implementation with postgres, when installed as neorc[postgres]
it may allow more extras in the future for other implementations.


## Installing

Every dependency is optional. `pip install neorc` brings no runtime dependency
beyond neorc-core: it is up to the user to install the extras their hosts need.

    pip install neorc                       # nothing extra
    pip install neorc[manager]              # FastAPI, to run the manager service
    pip install neorc[postgres]             # the Postgres driver
    pip install neorc[manager,postgres]     # the reference deployment

A worker host installs no extra unless its queue client needs one. Future
backends and persistence layers arrive as further extras.


## Task Queues

It will be implemented as two services a "manager" and one or more "workers".

After installing neorc users should be able to start

neorc manager start

And, in a different host

export NEORC_MANAGER_ADDRESS="xx.xx.xx.xx"
neorc worker start






```
from neorc import Worker


worker = Worker(manager_address, queue_cli)

worker.start()

```


The worker class should use queue_cli to pick the next task and execute it.

queue_cli will stay in long polling to the manager pick_next_task. That long polling could be replaced by Redis or SQS in the future, that's why queue_cli needs to be a port, supporting multiple implementations. Worker will receive queue_cli by its constructor.

The "neorc" library implements queue_cli.

queue_cli should contain methods for publishing a new task for producers, and other for atomically picking the next task for workers.

publishers > queue_cli > http > manager service > db

Then for next task retrieval.

worker > queue_cli > http > manager service > db



## APIs

The manager will use HTTP/JSON with FastAPI, async mechanism.

The "pick next task" endpoint should comfortably keep several workers waiting
without holding one database connection per waiting worker. See
[postgres-implementation.md](postgres-implementation.md).

The API should expose endpoints for publishing tasks, query task status, fetch a task to work, inform task start processing.

Fetching of a tasks will be in a separate endpoint from informing task start.
So the task fetching can be done with SQS in the future, while the inform start stays in Postgres. No references to Postgres or SQS will be in neorc-core.

