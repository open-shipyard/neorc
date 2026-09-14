import type { Address, Run, Task } from "../api";
import { addressKey, instancesOf, type Scope, type StepNode } from "../definition";
import { shortId } from "../format";
import { href } from "../router";
import { StatusBadge } from "./StatusBadge";
import { TaskDetails } from "./TaskDetails";

export interface Published {
  /** The run's tasks by their address. */
  tasks: Map<string, Task>;
  /** The run's sub-flow runs by the address of the step that started them. */
  subRuns: Map<string, Run>;
  /** Every address above, for finding a container's iterations and branches. */
  addresses: Address[];
}

export function published(tasks: Task[], subRuns: Run[]): Published {
  const addresses: Address[] = [];
  const byTask = new Map<string, Task>();
  for (const task of tasks) {
    byTask.set(addressKey(task.address.step, task.address.scope), task);
    addresses.push(task.address);
  }
  const bySubRun = new Map<string, Run>();
  for (const run of subRuns) {
    if (run.parent_address) {
      bySubRun.set(addressKey(run.parent_address.step, run.parent_address.scope), run);
      addresses.push(run.parent_address);
    }
  }
  return { tasks: byTask, subRuns: bySubRun, addresses };
}

/**
 * The run's steps as its definition lays them out, each with what the run
 * has done there: tasks with their status, loops with their iterations,
 * fan-outs with their branches, and sub-flows linking to their runs.
 */
export function RunTree({
  steps,
  scope,
  done,
}: {
  steps: StepNode[];
  scope: Scope;
  done: Published;
}) {
  return (
    <ul className="tree">
      {steps.map((step) => (
        <li key={step.name} className={`step step-${step.kind}`}>
          <Step step={step} scope={scope} done={done} />
        </li>
      ))}
    </ul>
  );
}

function Step({
  step,
  scope,
  done,
}: {
  step: StepNode;
  scope: Scope;
  done: Published;
}) {
  switch (step.kind) {
    case "task": {
      const task = done.tasks.get(addressKey(step.name, scope));
      return (
        <>
          {task ? <StatusBadge status={task.status} /> : <NotYet />}{" "}
          <strong>{step.name}</strong>{" "}
          <span className="muted">
            <code>{step.handler}</code>
          </span>
          {task && <TaskDetails task={task} />}
        </>
      );
    }
    case "flow": {
      const run = done.subRuns.get(addressKey(step.name, scope));
      return (
        <>
          {run ? <StatusBadge status={run.status} /> : <NotYet />}{" "}
          <strong>{step.name}</strong>{" "}
          <span className="muted">
            sub-flow <code>{step.flow}</code>
          </span>
          {run && (
            <>
              {" "}
              <a href={href({ name: "run", id: run.id })}>
                run <code>{shortId(run.id)}</code>
              </a>
            </>
          )}
        </>
      );
    }
    case "loop":
    case "fan_out":
      return <Container step={step} scope={scope} done={done} />;
  }
}

function Container({
  step,
  scope,
  done,
}: {
  step: Extract<StepNode, { kind: "loop" | "fan_out" }>;
  scope: Scope;
  done: Published;
}) {
  const instances = instancesOf(step.name, scope, done.addresses);
  const what = step.kind === "loop" ? "iteration" : "branch";
  const about =
    step.kind === "loop"
      ? `loop, at most ${step.maxCycles}, until ${step.exitCondition}`
      : `fan-out ${step.over}`;
  return (
    <>
      <strong>{step.name}</strong> <span className="muted">{about}</span>
      {instances.length === 0 ? (
        <section className="instance not-yet">
          <h4 className="muted">not entered yet</h4>
          <RunTree steps={step.children} scope={[...scope, [step.name, 0]]} done={done} />
        </section>
      ) : (
        instances.map((n) => (
          <section key={n} className="instance" aria-label={`${step.name} ${what} ${n}`}>
            <h4>
              {what} {n}
            </h4>
            <RunTree steps={step.children} scope={[...scope, [step.name, n]]} done={done} />
          </section>
        ))
      )}
    </>
  );
}

function NotYet() {
  return (
    <span className="badge badge-none" data-status="none">
      not published
    </span>
  );
}
