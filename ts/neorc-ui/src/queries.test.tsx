import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { ApiEvent } from "./api";
import { follow, keys, refresh } from "./queries";
import { ACTIVE, SUCCEEDED, event } from "./test/fixtures";
import { mockApi, neverAnswers } from "./test/render";

function pages(...pages: ApiEvent[][]) {
  let served = 0;
  return () => {
    const page = pages[served];
    served += 1;
    return page === undefined ? neverAnswers() : { events: page };
  };
}

function asked(api: ReturnType<typeof mockApi>) {
  return api.calls.map((call) => ({
    path: call.url.pathname,
    ...Object.fromEntries(call.url.searchParams),
  }));
}

describe("follow", () => {
  it("starts after the latest event, refetching everything once", async () => {
    const api = mockApi({
      "/events/latest": { sequence: 41 },
      "/events": pages([], [event(42, ACTIVE), event(43, SUCCEEDED)]),
    });
    const client = new QueryClient();
    const invalidated = vi.spyOn(client, "invalidateQueries");
    const controller = new AbortController();

    void follow(client, controller.signal);
    await vi.waitFor(() => expect(api.calls).toHaveLength(4));

    expect(asked(api)[0]).toEqual({ path: "/events/latest" });
    expect(asked(api)[1]).toMatchObject({ path: "/events", after: "41", timeout: "20" });
    expect(asked(api)[2]).toMatchObject({ after: "41" });
    expect(asked(api)[3]).toMatchObject({ after: "43" });
    // Once for everything at the start, then the runs and each run concerned.
    const keep = { cancelRefetch: false };
    expect(invalidated.mock.calls[0]).toEqual([{}, keep]);
    expect(invalidated).toHaveBeenCalledWith({ queryKey: ["runs"] }, keep);
    expect(invalidated).toHaveBeenCalledWith({ queryKey: keys.run(ACTIVE.id) }, keep);
    expect(invalidated).toHaveBeenCalledWith({ queryKey: keys.run(SUCCEEDED.id) }, keep);
    expect(invalidated).not.toHaveBeenCalledWith({ queryKey: keys.flows }, keep);
    controller.abort();
  });

  it("asks for the flows again when a run starts", async () => {
    const started: ApiEvent = { sequence: 2, run_id: ACTIVE.id, kind: "run_started" };
    const api = mockApi({
      "/events/latest": { sequence: 1 },
      "/events": pages([started]),
    });
    const client = new QueryClient();
    const invalidated = vi.spyOn(client, "invalidateQueries");
    const controller = new AbortController();

    void follow(client, controller.signal);
    await vi.waitFor(() => expect(api.calls).toHaveLength(3));

    expect(invalidated).toHaveBeenCalledWith(
      { queryKey: keys.flows },
      { cancelRefetch: false },
    );
    controller.abort();
  });

  it("starts over from the log's end after a failure, and stops when told", async () => {
    let polls = 0;
    const api = mockApi({
      "/events/latest": { sequence: 200 },
      "/events": () => {
        polls += 1;
        if (polls === 1) throw new Error("connection reset");
        return neverAnswers();
      },
    });
    const controller = new AbortController();
    const slept: number[] = [];
    vi.spyOn(console, "warn").mockImplementation(() => {});

    const done = follow(new QueryClient(), controller.signal, async (ms) => {
      slept.push(ms);
    });
    await vi.waitFor(() => expect(api.calls).toHaveLength(4));
    controller.abort();

    expect(slept).toEqual([2000]);
    expect(asked(api).map((call) => call.path)).toEqual([
      "/events/latest",
      "/events",
      "/events/latest",
      "/events",
    ]);
    await expect(Promise.race([done, Promise.resolve("still waiting")])).resolves.toBe(
      "still waiting",
    );
  });
});

describe("refresh", () => {
  it("fetches once more when a fetch was already in flight", async () => {
    const client = new QueryClient();
    let resolveFirst: (value: string) => void = () => {};
    const first = new Promise<string>((resolve) => {
      resolveFirst = resolve;
    });
    const answers: Promise<string>[] = [first, Promise.resolve("finished")];
    let calls = 0;
    const observer = new QueryObserver(client, {
      queryKey: keys.run(ACTIVE.id),
      queryFn: () => answers[calls++] ?? Promise.resolve("again"),
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.waitFor(() => expect(calls).toBe(1));

    // The event arrives while the first read, which predates it, is in flight.
    const refreshed = refresh(client, { queryKey: keys.run(ACTIVE.id) });
    resolveFirst("active");
    await refreshed;

    expect(calls).toBe(2);
    expect(observer.getCurrentResult().data).toBe("finished");
    unsubscribe();
  });

  it("fetches just once when nothing was in flight", async () => {
    const client = new QueryClient();
    let calls = 0;
    const observer = new QueryObserver(client, {
      queryKey: keys.run(ACTIVE.id),
      queryFn: () => Promise.resolve(`answer ${++calls}`),
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toBe("answer 1"));

    await refresh(client, { queryKey: keys.run(ACTIVE.id) });

    expect(calls).toBe(2);
    unsubscribe();
  });
});
