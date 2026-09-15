import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SESSION_KEY } from "./session";
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

// A manager restarting as the page opens is waited out for a while before
// the page says it cannot tell who is signed in.
client.setQueryDefaults(SESSION_KEY, { retry: 3 });

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
