// The word_picker_rounds run recorded by scripts/record_ui_fixture.py.
import type { Flow, Run, Task } from "../api";
import recorded from "./word_picker_rounds.json";

export const RECORDED = recorded as unknown as {
  flows: Record<string, Flow>;
  run: Run;
  tasks: Task[];
  sub_runs: Run[];
  sub_run_tasks: Record<string, Task[]>;
};

/** The routes a run page asks for, as the manager would answer for the run. */
export function recordedRoutes(): Record<string, unknown> {
  const { flows, run, tasks, sub_runs, sub_run_tasks } = RECORDED;
  const routes: Record<string, unknown> = {
    "/flows": { flows: Object.values(flows) },
    [`/runs/${run.id}`]: run,
    [`/runs/${run.id}/tasks`]: { tasks },
    [`/runs/${run.id}/sub-runs`]: { runs: sub_runs },
  };
  for (const flow of Object.values(flows)) {
    routes[`/flows/${flow.name}`] = flow;
    routes[`/flows/${flow.name}/versions/${flow.version}`] = flow;
  }
  for (const sub of sub_runs) {
    routes[`/runs/${sub.id}`] = sub;
    routes[`/runs/${sub.id}/tasks`] = { tasks: sub_run_tasks[sub.id] ?? [] };
    routes[`/runs/${sub.id}/sub-runs`] = { runs: [] };
  }
  return routes;
}
