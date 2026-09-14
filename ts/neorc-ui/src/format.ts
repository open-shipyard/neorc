// Values and times as the page shows them.

const time = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});

/** An ISO timestamp from the manager as local date and time; "" for none. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const moment = new Date(iso);
  return Number.isNaN(moment.getTime()) ? iso : time.format(moment);
}

/** How long a run or task took, from its times; "" while it has not finished. */
export function formatDuration(
  from: string | null | undefined,
  to: string | null | undefined,
): string {
  if (!from || !to) return "";
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (Number.isNaN(ms) || ms < 0) return "";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes} min ${seconds} s`;
}

/**
 * A JSON value pretty-printed, with a `$datetime` tag shown as its date: the
 * manager sends values in their JSON form, tags included.
 */
export function formatValue(value: unknown): string {
  return JSON.stringify(untag(value), null, 2) ?? "undefined";
}

function untag(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(untag);
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record);
    if (keys.length === 1 && keys[0] === "$datetime") {
      return `${formatTime(String(record["$datetime"]))} (${String(record["$datetime"])})`;
    }
    return Object.fromEntries(keys.map((key) => [key, untag(record[key])]));
  }
  return value;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

/**
 * A `datetime-local` field's value as the manager takes a datetime: ISO 8601
 * with the browser's own offset, so the moment typed is the moment meant.
 * Undefined for an empty or unreadable value.
 */
export function localToIso(value: string): string | undefined {
  if (!value) return undefined;
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return undefined;
  const pad = (n: number) => String(n).padStart(2, "0");
  const offset = -moment.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const hours = pad(Math.floor(Math.abs(offset) / 60));
  const minutes = pad(Math.abs(offset) % 60);
  return (
    `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}` +
    `T${pad(moment.getHours())}:${pad(moment.getMinutes())}:${pad(moment.getSeconds())}` +
    `${sign}${hours}:${minutes}`
  );
}
