import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ACTIVE, HELLO, SUCCEEDED, WORD_PICKER } from "../test/fixtures";
import { mockApi, renderWithClient } from "../test/render";
import { Layout, SIDEBAR_KEY } from "./Layout";

function routes() {
  return {
    "/flows": { flows: [HELLO, WORD_PICKER] },
    "/runs?root_only=true&limit=6": { runs: [ACTIVE, SUCCEEDED] },
  };
}

describe("Layout", () => {
  it("has the navigation, a way to a new run, the flows, the latest runs and no account", async () => {
    mockApi(routes());

    renderWithClient(
      <Layout route={{ name: "run", id: ACTIVE.id }}>
        <p>the page</p>
      </Layout>,
    );

    const main = screen.getByRole("navigation", { name: "Main" });
    expect(within(main).getByRole("link", { name: "Runs" })).toHaveAttribute("aria-current", "page");
    expect(within(main).getByRole("link", { name: "Flows" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "New run" })).toHaveAttribute("href", "#/flows");
    expect(screen.getByRole("note")).toHaveTextContent("No authentication yet");
    expect(screen.getByText("the page")).toBeInTheDocument();

    const flows = await screen.findByRole("navigation", { name: "Flows" });
    expect(within(flows).getByRole("link", { name: "word_picker" })).toHaveAttribute(
      "href",
      "#/flows/word_picker",
    );
    const recent = await screen.findByRole("navigation", { name: "Recent runs" });
    const links = within(recent).getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", `#/runs/${ACTIVE.id}`);
    expect(links[0]).toHaveAttribute("aria-current", "page");
    expect(links[0]?.querySelector("[data-status]")).toHaveAttribute("data-status", "active");
    expect(links[1]?.querySelector("[data-status]")).toHaveAttribute("data-status", "succeeded");

    const profile = screen.getByLabelText("Profile");
    expect(profile).toHaveTextContent("No account");
    expect(within(profile).queryByRole("link")).not.toBeInTheDocument();
  });

  it("collapses to a rail and remembers it", () => {
    mockApi(routes());

    const first = renderWithClient(
      <Layout route={{ name: "flows" }}>
        <p>the page</p>
      </Layout>,
    );
    const collapse = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(collapse);

    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    // The links keep their names for a reader of the rail.
    expect(screen.getByRole("link", { name: "Flows" })).toHaveAttribute("aria-current", "page");
    expect(localStorage.getItem(SIDEBAR_KEY)).toBe("rail");
    first.unmount();

    renderWithClient(
      <Layout route={{ name: "flows" }}>
        <p>the page</p>
      </Layout>,
    );
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
  });

  it("stands without the flows or the runs", async () => {
    mockApi({});

    renderWithClient(
      <Layout route={{ name: "runs" }}>
        <p>the page</p>
      </Layout>,
    );

    expect(await screen.findByText("the page")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Flows" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Recent runs" })).not.toBeInTheDocument();
  });
});
