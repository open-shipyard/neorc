import { RUN_STATUSES, type Run, type RunStatus } from "../api";
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
      <div className="page-head">
        <div>
          <h1>Latest runs</h1>
          <p className="muted subtitle">
            {runs.isSuccess &&
              `${listed.length}${runs.hasNextPage ? "+" : ""} run${listed.length === 1 ? "" : "s"}` +
                `${flow ? ` of ${flow}` : ""}${filters.status ? `, ${filters.status}` : ""}, newest first`}
          </p>
        </div>
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
      </div>
      {runs.isSuccess && listed.length > 0 && <Tiles runs={listed} partial={runs.hasNextPage} />}
      {runs.isPending && <Loading what="runs" />}
      {runs.isError && <Failed what="runs" error={runs.error} />}
      {runs.isSuccess && listed.length === 0 && (
        <p className="muted">No runs yet.</p>
      )}
      {listed.length > 0 && (
        <table className="runs">
          <thead>
            <tr>
              <th>Status</th>
              <th>Run</th>
              <th>Version</th>
              <th>Started</th>
              <th className="num">Took</th>
            </tr>
          </thead>
          <tbody>
            {listed.map((run) => (
              <tr key={run.id}>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>
                  <a className="run-name" href={href({ name: "run", id: run.id })}>
                    <strong>{run.flow}</strong>{" "}
                    <span className="mono muted">{shortId(run.id)}</span>
                  </a>
                </td>
                <td className="muted">{run.version}</td>
                <td>{formatTime(run.created_at)}</td>
                <td className="num mono">{formatDuration(run.created_at, run.finished_at)}</td>
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

const TILES: { status: RunStatus; label: string }[] = [
  { status: "active", label: "Active" },
  { status: "succeeded", label: "Succeeded" },
  { status: "failed", label: "Failed" },
  { status: "cancelled", label: "Cancelled" },
];

/**
 * Counts by status among the runs loaded: the manager aggregates nothing,
 * so the tiles say what they count when more runs remain unloaded.
 */
function Tiles({ runs, partial }: { runs: Run[]; partial: boolean }) {
  return (
    <div className="tiles" aria-label={partial ? "Of the runs loaded" : "Of these runs"}>
      {TILES.map((tile) => (
        <div key={tile.status} className="tile">
          <span className="tile-label">
            {tile.label}
            {partial && <span className="muted"> of loaded</span>}
          </span>
          <span className="tile-figure mono">
            {runs.filter((run) => run.status === tile.status).length}
          </span>
        </div>
      ))}
    </div>
  );
}
