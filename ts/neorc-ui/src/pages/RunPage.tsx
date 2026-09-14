import { Failed, Loading } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { formatTime, formatValue, shortId } from "../format";
import { useRun } from "../queries";
import { href } from "../router";

/** One run: what it is and where it stands. Its tree and tasks come next. */
export function RunPage({ id }: { id: string }) {
  const run = useRun(id);
  if (run.isPending) return <Loading what={`run ${shortId(id)}`} />;
  if (run.isError) return <Failed what={`run ${shortId(id)}`} error={run.error} />;

  return (
    <>
      <h1>
        Run <code>{shortId(run.data.id)}</code>{" "}
        <StatusBadge status={run.data.status} />
      </h1>
      <dl className="facts">
        <dt>Flow</dt>
        <dd>
          <a href={href({ name: "flow", flow: run.data.flow, version: run.data.version })}>
            {run.data.flow} {run.data.version}
          </a>
        </dd>
        <dt>Id</dt>
        <dd>
          <code>{run.data.id}</code>
        </dd>
        <dt>Started</dt>
        <dd>{formatTime(run.data.created_at)}</dd>
        <dt>Finished</dt>
        <dd>{formatTime(run.data.finished_at)}</dd>
        {run.data.reason && (
          <>
            <dt>Reason</dt>
            <dd>{run.data.reason}</dd>
          </>
        )}
      </dl>
      <h2>Inputs</h2>
      <pre>{formatValue(run.data.inputs)}</pre>
      {run.data.status === "succeeded" && (
        <>
          <h2>Output</h2>
          <pre>{formatValue(run.data.output)}</pre>
        </>
      )}
    </>
  );
}
