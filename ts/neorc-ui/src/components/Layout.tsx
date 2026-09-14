import type { ReactNode } from "react";

import { href, type Route } from "../router";

const NAV: { label: string; route: Route }[] = [
  { label: "Runs", route: { name: "runs" } },
  { label: "Flows", route: { name: "flows" } },
];

export function Layout({
  route,
  children,
}: {
  route: Route;
  children: ReactNode;
}) {
  return (
    <>
      <header className="top">
        <span className="brand">neorc</span>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <a
              key={item.label}
              href={href(item.route)}
              aria-current={current(route, item.route) ? "page" : undefined}
            >
              {item.label}
            </a>
          ))}
        </nav>
      </header>
      <p className="banner" role="note">
        No authentication yet: anyone who reaches this page can start and
        cancel runs. For development and testing only.
      </p>
      <main>{children}</main>
    </>
  );
}

function current(route: Route, item: Route): boolean {
  if (item.name === "runs") return route.name === "runs" || route.name === "run";
  if (item.name === "flows") return route.name === "flows" || route.name === "flow";
  return false;
}

export function Loading({ what }: { what: string }) {
  return <p className="muted">Loading {what}…</p>;
}

export function Failed({ what, error }: { what: string; error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <p role="alert">
      Could not load {what}: {message}
    </p>
  );
}
