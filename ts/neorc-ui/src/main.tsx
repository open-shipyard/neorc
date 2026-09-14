import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactFlowProvider } from "@xyflow/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
// The graph view's libraries are bundled from this change on, so the
// license, size and bundled-package checks judge them here, as a dependency
// change of their own, and not as a side effect of the view that uses them.
// The run graph, which comes next, is what imports dagre for real.
import "@dagrejs/dagre";
import "@xyflow/react/dist/style.css";
import "./index.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("index.html has no #root");
}

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // The event poll says when a run's data is to be refetched; the flow
      // queries, which no event covers, set a stale time of their own.
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={client}>
        <ReactFlowProvider>
          <App />
        </ReactFlowProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
