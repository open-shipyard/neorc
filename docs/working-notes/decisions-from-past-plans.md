# Decisions kept from finished plans

The plans for flows in memory and for flows on Postgres and HTTP are done and
deleted; the CHANGELOG records what each step shipped, and the code is the
authority on shape. These are the decisions those plans made that are written
nowhere else: not in the specs, not in the code, not in the CHANGELOG. Each was
decided to make progress. Revisit if it is wrong; move it into a spec when it
settles.

## Flows

- `loop_count` starts at 1 and counts the innermost enclosing loop. `range: N`
  gives indexes 1 to N.
- `max_cycles: N` allows at most N complete iterations of a loop. This is the
  answer to the "canonical definition" question in
  [flows-open-questions.md](../specs/flows-open-questions.md).
- A loop's exit condition is a task directly in the loop body, not a nested
  step or a sub-flow.
- A loop starts its next iteration once every step of the current one has
  finished, not only its exit condition.
- YAML timestamps are not converted when a flow file is read: a datetime in a
  flow file is written as `{"$datetime": ...}`, so a YAML file and its JSON
  upload are the same structure and there is one YAML reading.

## Scheduler and manager

- The planner takes no event. It plans from the run's whole state, publishing
  every ready step instance not started yet. The event only tells the
  scheduler which run to look at, so a redelivered event cannot plan anything
  different.
- Every run that finishes, in any status, records a "run finished" event with
  its own id; the scheduler finds the parent from there. There is no event
  addressed to the parent.
- The scheduler publishes a task, or starts a sub-flow run, by run and address
  only; the manager takes the queue, handler and inputs from the run's version
  of the flow, so a request cannot disagree with the definition.
- A deployment runs one scheduler. Several at once would plan the same run
  together; publishing is idempotent, but succeeding and failing a run are not
  designed for that, and would need a lock per run or a leader. Listed as open
  in [flows-open-questions.md](../specs/flows-open-questions.md).

## Postgres

- Event appends take a transaction-level advisory lock, last in every
  transaction, so sequences commit in order and a scheduler reading after a
  sequence misses nothing. That lock serialises every write that records an
  event. Revisit when it shows up in a profile.
