import { useEffect, useState } from "react";

import { listFlows, type FlowList } from "./api";

type Loaded =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; flows: FlowList["flows"] };

/** The pipeline's proof: the flows the manager holds, by name. */
export function App() {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;
    listFlows().then(
      (list) => {
        if (!cancelled) setLoaded({ state: "ready", flows: list.flows });
      },
      (error: unknown) => {
        if (!cancelled) setLoaded({ state: "failed", message: String(error) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <h1>neorc</h1>
      <p role="note">
        No authentication yet: for development and testing only.
      </p>
      {loaded.state === "loading" && <p>Loading flows…</p>}
      {loaded.state === "failed" && (
        <p role="alert">Could not load the flows: {loaded.message}</p>
      )}
      {loaded.state === "ready" && loaded.flows.length === 0 && (
        <p>No flows uploaded yet.</p>
      )}
      {loaded.state === "ready" && loaded.flows.length > 0 && (
        <ul>
          {loaded.flows.map((flow) => (
            <li key={flow.name}>
              {flow.name} {flow.version}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
