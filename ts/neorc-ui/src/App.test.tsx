import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { ACTIVE, HELLO } from "./test/fixtures";
import { mockApi, neverAnswers, renderWithClient } from "./test/render";

const OTHER = { ...ACTIVE, id: "0ddddddd-0000-4000-8000-000000000002", root_id: "0ddddddd-0000-4000-8000-000000000002" };

function routes() {
  const shared: Record<string, unknown> = {
    "/events/latest": { sequence: 0 },
    "/events": () => neverAnswers(),
    "/flows": { flows: [HELLO] },
    "/flows/hello": HELLO,
    "/flows/hello/versions/0.1.0": HELLO,
  };
  for (const run of [ACTIVE, OTHER]) {
    shared[`/runs/${run.id}`] = run;
    shared[`/runs/${run.id}/tasks`] = { tasks: [] };
    shared[`/runs/${run.id}/sub-runs`] = { runs: [] };
  }
  return shared;
}

describe("App", () => {
  it("leaves a half-confirmed cancellation behind when moving to another run", async () => {
    mockApi(routes());
    location.hash = `#/runs/${ACTIVE.id}`;

    renderWithClient(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    expect(screen.getByRole("button", { name: "Cancel it" })).toBeInTheDocument();

    location.hash = `#/runs/${OTHER.id}`;
    dispatchEvent(new HashChangeEvent("hashchange"));

    expect(await screen.findByRole("button", { name: "Cancel run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel it" })).not.toBeInTheDocument();
  });

  it("shows the banner and says where an unknown address leads", async () => {
    mockApi(routes());
    location.hash = "#/nowhere";

    renderWithClient(<App />);

    expect(screen.getByRole("note")).toHaveTextContent("No authentication yet");
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing at #/nowhere");
  });
});
