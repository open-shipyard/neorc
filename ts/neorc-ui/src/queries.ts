// Data fetching on TanStack Query, and the event poll that keeps it fresh.
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  type InvalidateQueryFilters,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect } from "react";

import {
  flowVersions,
  getFlow,
  getRun,
  latestSequence,
  listFlows,
  listRuns,
  PAGE_SIZE,
  runTasks,
  subRuns,
  waitForEvents,
  type ApiEvent,
  type RunFilters,
} from "./api";

export const keys = {
  flows: ["flows"] as const,
  flow: (name: string, version?: string) =>
    ["flows", name, version ?? "latest"] as const,
  versions: (name: string) => ["flows", name, "versions"] as const,
  runs: (filters: RunFilters) => ["runs", filters] as const,
  run: (id: string) => ["run", id] as const,
  tasks: (id: string) => ["run", id, "tasks"] as const,
  subRuns: (id: string) => ["run", id, "sub-runs"] as const,
};

/**
 * How long a flow query is trusted. No event says a flow was uploaded, so
 * the flow pages ask again after a while, and whenever a run starts.
 */
export const FLOWS_STALE_MS = 30_000;

export function useFlows() {
  return useQuery({
    queryKey: keys.flows,
    queryFn: listFlows,
    staleTime: FLOWS_STALE_MS,
  });
}

export function useFlow(name: string, version?: string) {
  return useQuery({
    queryKey: keys.flow(name, version),
    queryFn: () => getFlow(name, version),
    // A stored version never changes; the latest may be replaced.
    staleTime: version ? Infinity : FLOWS_STALE_MS,
  });
}

export function useFlowVersions(name: string) {
  return useQuery({
    queryKey: keys.versions(name),
    queryFn: () => flowVersions(name),
    staleTime: FLOWS_STALE_MS,
  });
}

export function useRuns(filters: RunFilters) {
  return useInfiniteQuery({
    queryKey: keys.runs(filters),
    queryFn: ({ pageParam }) => listRuns(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    // The cursor is the last run of the previous page; a short page is the end.
    getNextPageParam: (page) =>
      page.length < PAGE_SIZE ? undefined : page[page.length - 1]?.id,
  });
}

export function useRun(id: string) {
  return useQuery({ queryKey: keys.run(id), queryFn: () => getRun(id) });
}

export function useRunTasks(id: string) {
  return useQuery({ queryKey: keys.tasks(id), queryFn: () => runTasks(id) });
}

export function useSubRuns(id: string) {
  return useQuery({ queryKey: keys.subRuns(id), queryFn: () => subRuns(id) });
}

/** How long one poll waits on the manager, under its own deadline. */
export const POLL_SECONDS = 20;

/** How long to wait before polling again after a failure. */
export const RETRY_MS = 2000;

/**
 * Follow the manager's event log for the life of the page.
 *
 * The poll starts after the log's latest sequence, asked for first, and then
 * refetches everything once: what a page loaded before that answer came may
 * already be behind. From then on each event invalidates the run it concerns
 * and every runs listing, and a run starting invalidates the flows, since a
 * flow may have been uploaded with no event of its own. No event kind is
 * needed beyond that: an event only says which run to look at again.
 */
export function useLiveEvents(): void {
  const client = useQueryClient();
  useEffect(() => {
    const controller = new AbortController();
    void follow(client, controller.signal);
    return () => controller.abort();
  }, [client]);
}

export async function follow(
  client: QueryClient,
  signal: AbortSignal,
  sleep: (ms: number) => Promise<void> = pause,
): Promise<void> {
  let after: number | undefined;
  while (!signal.aborted) {
    try {
      if (after === undefined) {
        after = await latestSequence(signal);
        void refresh(client, {});
        continue;
      }
      const events = await waitForEvents(after, {
        timeout: POLL_SECONDS,
        signal,
      });
      if (events.length > 0) {
        after = events[events.length - 1]?.sequence ?? after;
        invalidate(client, events);
      }
    } catch (error) {
      if (signal.aborted) return;
      console.warn("event poll failed, retrying", error);
      // Start over from the log's end: a manager restarted on a fresh log
      // counts from 1 again, and a position past its end would read nothing
      // for ever. Everything is refetched then, which also clears queries
      // that failed during the outage.
      after = undefined;
      await sleep(RETRY_MS);
    }
  }
}

/**
 * A refetch already under way is left to finish rather than cancelled: the
 * poll returns as soon as an event exists, and cancelling the last refetch
 * on every batch would keep a busy manager's pages from ever settling, and
 * drop a page of older runs a reader asked for.
 */
const KEEP_GOING = { cancelRefetch: false } as const;

/**
 * Refetch what matches, and once more if a fetch was already in flight: that
 * one may have read the store before the event was recorded, and its answer
 * would otherwise clear the invalidation with stale data.
 */
export async function refresh(
  client: QueryClient,
  filters: InvalidateQueryFilters,
): Promise<void> {
  const inFlight = client.isFetching(filters) > 0;
  await client.invalidateQueries(filters, KEEP_GOING);
  if (inFlight) await client.invalidateQueries(filters, KEEP_GOING);
}

function invalidate(client: QueryClient, events: ApiEvent[]): void {
  void refresh(client, { queryKey: ["runs"] });
  for (const id of new Set(events.map((event) => event.run_id))) {
    void refresh(client, { queryKey: keys.run(id) });
  }
  if (events.some((event) => event.kind === "run_started")) {
    void refresh(client, { queryKey: keys.flows });
  }
}

function pause(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
