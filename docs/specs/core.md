# Neorc

## The libs
neorc-core will host all the mechanism and interactions between components, but no specific persistence layer or vendor specific products.

neorc will provide a base reference implementation with postgres, when installed as neorc[postgres]
it may allow more extras in the future for other implementations.


## Task Queues

It will be implemented as two services a "manager" and one or more "workers".

After installing neorc users should be able to start

neorc manager start

And, in a different host

export NEORC_MANAGER_ADDESS="xx.xx.xx.xx"
neorc worker start






```
from neorc import Worker


worker = Worker(manager_address, queue_cli)

worker.start()

```


The worker class should use queue_cli to pick the next task and execute it.

queue_cli will stay in long polling to the manager pick_next_task. That long polling could be replaced by Redis or SQS in the future, that's why queue_cli needs to be a port, supporting multiple implementations. Worker will receive queue_cli by its constructor.

The "neorc" library implements queue_cli.

