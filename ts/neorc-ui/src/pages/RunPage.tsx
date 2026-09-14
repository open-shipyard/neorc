import { Failed, Loading } from "../components/Layout";
import { RunTree, published } from "../components/RunTree";
import { StatusBadge } from "../components/StatusBadge";
import { stepsOf } from "../definition";
import { formatDuration, formatTime, formatValue, shortId } from "../format";
import { useFlow, useRun, useRunTasks, useSubRuns } from "../queries";
import { href } from "../router";

/** One run: what it is, where it stands, and each step of its flow, live. */
export function RunPage({ id }: { id: string }) {
  const run = useRun(id);
  if (run.isPending) return <Loading what={`run ${shortId(id)}`} />;
  if (run.isError) return <Failed what={`run ${shortId(id)}`} error={run.error} />;
  return <Loaded run={run.data} />;
}

function Loaded({ run }: { run: ReturnType<typeof useRun>["data"] & object }) {
  const flow = useFlow(run.flow, run.version);
  const live = { active: run.status === "active" };
  const tasks = useRunTasks(run.id, live);
  const subRuns = useSubRuns(run.id, live);

  return (
    <>
      <h1>
        Run <code>{shortId(run.id)}</code> <StatusBadge status={run.status} />
      </h1>
      <dl className="facts">
        <dt>Flow</dt>
        <dd>
          <a href={href({ name: "flow", flow: run.flow, version: run.version })}>
            {run.flow} {run.version}
          </a>
        </dd>
        <dt>Id</dt>
        <dd>
          <code>{run.id}</code>
        </dd>
        {run.parent_id && (
          <>
            <dt>Parent</dt>
            <dd>
              <a href={href({ name: "run", id: run.parent_id })}>
                run <code>{shortId(run.parent_id)}</code>
              </a>
              {run.parent_address && (
                <span className="muted">
                  {" "}
                  at {run.parent_address.step}
                  {run.parent_address.scope.map(([name, n]) => ` ${name} ${n}`).join("")}
                </span>
              )}
            </dd>
          </>
        )}
        <dt>Started</dt>
        <dd>{formatTime(run.created_at)}</dd>
        <dt>Finished</dt>
        <dd>
          {formatTime(run.finished_at)}
          {run.finished_at && (
            <span className="muted"> took {formatDuration(run.created_at, run.finished_at)}</span>
          )}
        </dd>
        {run.reason && (
          <>
            <dt>Reason</dt>
            <dd>{run.reason}</dd>
          </>
        )}
      </dl>

      <h2>Steps</h2>
      {(flow.isPending || tasks.isPending || subRuns.isPending) && (
        <Loading what="the steps" />
      )}
      {flow.isError && <Failed what="the flow's definition" error={flow.error} />}
      {tasks.isError && <Failed what="the tasks" error={tasks.error} />}
      {subRuns.isError && <Failed what="the sub-runs" error={subRuns.error} />}
      {flow.isSuccess && tasks.isSuccess && subRuns.isSuccess && (
        <RunTree
          steps={stepsOf(flow.data.content)}
          scope={[]}
          done={published(tasks.data, subRuns.data)}
        />
      )}

      {subRuns.isSuccess && subRuns.data.length > 0 && (
        <>
          <h2>Sub-runs</h2>
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Flow</th>
                <th>Step</th>
                <th>Status</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {subRuns.data.map((sub) => (
                <tr key={sub.id}>
                  <td>
                    <a href={href({ name: "run", id: sub.id })}>
                      <code>{shortId(sub.id)}</code>
                    </a>
                  </td>
                  <td>
                    {sub.flow} <span className="muted">{sub.version}</span>
                  </td>
                  <td>
                    {sub.parent_address?.step}
                    {sub.parent_address?.scope.map(([name, n]) => ` ${name} ${n}`).join("")}
                  </td>
                  <td>
                    <StatusBadge status={sub.status} />
                  </td>
                  <td>{sub.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Inputs</h2>
      <pre>{formatValue(run.inputs)}</pre>
      {run.status === "succeeded" && (
        <>
          <h2>Output</h2>
          <pre>{formatValue(run.output)}</pre>
        </>
      )}
    </>
  );
}
