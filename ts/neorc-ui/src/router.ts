// Hash routing: the page's path never changes, so the manager serves plain
// files under one prefix and needs no fallback route.
import { useSyncExternalStore } from "react";

export type Route =
  | { name: "runs"; flow?: string; status?: string }
  | { name: "flows" }
  | { name: "flow"; flow: string; version?: string }
  | { name: "run"; id: string }
  | { name: "unknown"; hash: string };

export function parseHash(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?", 2);
  const params = new URLSearchParams(query);
  const parts = path.split("/").filter((part) => part !== "");
  // The hash is the reader's to type: a bad escape is not a route, not a crash.
  if (parts.some((part) => safeDecode(part) === undefined)) {
    return { name: "unknown", hash };
  }
  if (parts.length === 0 || (parts.length === 1 && parts[0] === "runs")) {
    return {
      name: "runs",
      flow: params.get("flow") ?? undefined,
      status: params.get("status") ?? undefined,
    };
  }
  if (parts.length === 1 && parts[0] === "flows") return { name: "flows" };
  if (parts.length === 2 && parts[0] === "flows" && parts[1]) {
    return {
      name: "flow",
      flow: safeDecode(parts[1]) ?? parts[1],
      version: params.get("version") ?? undefined,
    };
  }
  if (parts.length === 2 && parts[0] === "runs" && parts[1]) {
    return { name: "run", id: safeDecode(parts[1]) ?? parts[1] };
  }
  return { name: "unknown", hash };
}

function safeDecode(part: string): string | undefined {
  try {
    return decodeURIComponent(part);
  } catch {
    return undefined;
  }
}

export function href(route: Route): string {
  switch (route.name) {
    case "runs": {
      const params = new URLSearchParams();
      if (route.flow) params.set("flow", route.flow);
      if (route.status) params.set("status", route.status);
      const query = params.toString();
      return query ? `#/runs?${query}` : "#/runs";
    }
    case "flows":
      return "#/flows";
    case "flow": {
      const base = `#/flows/${encodeURIComponent(route.flow)}`;
      return route.version
        ? `${base}?version=${encodeURIComponent(route.version)}`
        : base;
    }
    case "run":
      return `#/runs/${encodeURIComponent(route.id)}`;
    case "unknown":
      return route.hash;
  }
}

export function navigate(route: Route): void {
  location.hash = href(route);
}

function subscribe(onChange: () => void): () => void {
  addEventListener("hashchange", onChange);
  return () => removeEventListener("hashchange", onChange);
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return parseHash(hash);
}
