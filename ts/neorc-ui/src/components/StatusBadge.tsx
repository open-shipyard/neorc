import type { RunStatus, TaskStatus } from "../api";

/** One badge for runs and tasks: the status word, coloured by what it means. */
export function StatusBadge({ status }: { status: RunStatus | TaskStatus }) {
  return (
    <span className={`badge badge-${status}`} data-status={status}>
      {status}
    </span>
  );
}
