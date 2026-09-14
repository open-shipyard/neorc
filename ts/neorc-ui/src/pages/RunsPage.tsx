import { RUN_STATUSES, type RunStatus } from "../api";
import { Failed, Loading } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { formatDuration, formatTime, shortId } from "../format";
import { useFlows, useRuns } from "../queries";
import { href, navigate } from "../router";

function asStatus(value: string | undefined): RunStatus | undefined {
  return RUN_STATUSES.find((status) => status === value);
}

/** Runs newest first, a page at a time, filtered by flow and status. */
export function RunsPage({ flow, status }: { flow?: string; status?: string }) {
  const filters = { flow, status: asStatus(status) };
  const flows = useFlows();
  const runs = useRuns(filters);

  const listed = runs.data?.pages.flat() ?? [];
  return (
    <>
      <h1>Runs</h1>
      <form
        className="filters"
        onSubmit={(event) => event.preventDefault()}
        aria-label="Filters"
      >
        <label>
          Flow{" "}
          <select
            value={flow ?? ""}
            onChange={(event) =>
              navigate({
                name: "runs",
                flow: event.target.value || undefined,
                status,
              })
            }
          >
            <option value="">all</option>
            {(flows.data ?? []).map((each) => (
              <option key={each.name} value={each.name}>
                {each.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status{" "}
          <select
            value={filters.status ?? ""}
            onChange={(event) =>
              navigate({
                name: "runs",
                flow,
                status: event.target.value || undefined,
              })
            }
          >
            <option value="">all</option>
            {RUN_STATUSES.map((each) => (
              <option key={each} value={each}>
                {each}
              </option>
            ))}
          </select>
        </label>
      </form>
      {runs.isPending && <Loading what="runs" />}
      {runs.isError && <Failed what="runs" error={runs.error} />}
      {runs.isSuccess && listed.length === 0 && (
        <p className="muted">No runs yet.</p>
      )}
      {listed.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Flow</th>
              <th>Status</th>
              <th>Started</th>
              <th>Took</th>
            </tr>
          </thead>
          <tbody>
            {listed.map((run) => (
              <tr key={run.id}>
                <td>
                  <a href={href({ name: "run", id: run.id })}>
                    <code>{shortId(run.id)}</code>
                  </a>
                </td>
                <td>
                  {run.flow} <span className="muted">{run.version}</span>
                </td>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>{formatTime(run.created_at)}</td>
                <td>{formatDuration(run.created_at, run.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {runs.hasNextPage && (
        <button
          type="button"
          // Not at the cost of a refetch an event started: see queries.ts.
          onClick={() => void runs.fetchNextPage({ cancelRefetch: false })}
          disabled={runs.isFetchingNextPage}
        >
          {runs.isFetchingNextPage ? "Loading…" : "Older runs"}
        </button>
      )}
    </>
  );
}
