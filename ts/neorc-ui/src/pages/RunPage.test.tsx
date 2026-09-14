import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { shortId } from "../format";
import { RECORDED, recordedRoutes } from "../test/recorded";
import { mockApi, refusal, renderWithClient } from "../test/render";
import { RunPage } from "./RunPage";

describe("RunPage on a recorded word_picker_rounds run", () => {
  it("shows the run, and every step with what the run did there", async () => {
    mockApi(recordedRoutes());
    const { run, sub_runs } = RECORDED;

    renderWithClient(<RunPage id={run.id} />);

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(`Run ${shortId(run.id)}`);
    expect(within(heading).getByText("succeeded")).toBeInTheDocument();

    // The loop ran twice: each iteration holds the sub-flow and its exit task.
    const first = await screen.findByRole("region", { name: "runs iteration 1" });
    const second = screen.getByRole("region", { name: "runs iteration 2" });
    for (const [iteration, sub] of [first, second].map((r, i) => [r, sub_runs[i]] as const)) {
      expect(within(iteration).getByText("picker")).toBeInTheDocument();
      expect(within(iteration).getByText("ran_enough")).toBeInTheDocument();
      expect(within(iteration).getByRole("link", { name: `run ${shortId(sub!.id)}` })).toHaveAttribute(
        "href",
        `#/runs/${sub!.id}`,
      );
    }
    expect(screen.queryByRole("region", { name: "runs iteration 3" })).not.toBeInTheDocument();

    // The fan-out has a branch per word, each with the nested loop's iterations.
    for (const branch of [1, 2]) {
      const region = screen.getByRole("region", { name: `decorate branch ${branch}` });
      expect(within(region).getByRole("region", { name: "padding iteration 1" })).toBeInTheDocument();
      expect(within(region).getByRole("region", { name: "padding iteration 2" })).toBeInTheDocument();
    }
    expect(screen.queryByText("not published")).not.toBeInTheDocument();

    // Sub-runs are listed too, with the step that started them.
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByText("picker runs 1")).toBeInTheDocument();
  });

  it("opens a task's details: handler, attempts, times, params and result", async () => {
    mockApi(recordedRoutes());
    const { run, tasks } = RECORDED;
    const report = tasks.find((task) => task.address.step === "report");

    renderWithClient(<RunPage id={run.id} />);
    await screen.findByRole("region", { name: "runs iteration 1" });
    const step = screen.getByText("report").closest("li");
    expect(step).not.toBeNull();
    fireEvent.click(within(step!).getByText("details"));

    const details = within(step!.querySelector("details")!);
    expect(details.getByText("tasks:report")).toBeInTheDocument();
    expect(details.getByText(String(report?.attempts))).toBeInTheDocument();
    expect(details.getByText(/potato\*\*/)).toBeInTheDocument();
    expect(details.getByText(/neorc\.flow_run_id/)).toBeInTheDocument();
  });

  it("links a sub-run to its parent and the step it was started at", async () => {
    mockApi(recordedRoutes());
    const { run, sub_runs } = RECORDED;
    const sub = sub_runs[0]!;

    renderWithClient(<RunPage id={sub.id} />);

    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("link", { name: `run ${shortId(run.id)}` })).toHaveAttribute(
      "href",
      `#/runs/${run.id}`,
    );
    expect(screen.getByText(/at picker runs 1/)).toBeInTheDocument();
    // word_picker's own loop and fan-outs are drawn from its definition.
    expect(await screen.findByRole("region", { name: "rounds iteration 1" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "grading branch 3" })).toBeInTheDocument();
  });

  it("draws the steps of a run that has published nothing yet as not started", async () => {
    const { run, flows } = RECORDED;
    const fresh = { ...run, status: "active" as const, finished_at: null, output: null };
    mockApi({
      ...recordedRoutes(),
      [`/runs/${run.id}`]: fresh,
      [`/runs/${run.id}/tasks`]: { tasks: [] },
      [`/runs/${run.id}/sub-runs`]: { runs: [] },
      [`/flows/word_picker_rounds/versions/${flows["word_picker_rounds"]?.version}`]:
        flows["word_picker_rounds"],
    });

    renderWithClient(<RunPage id={run.id} />);

    expect(await screen.findAllByText("not published")).not.toHaveLength(0);
    expect(screen.getAllByText("not entered yet")).toHaveLength(3);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("reports a run the manager does not have", async () => {
    mockApi({ "/runs/nothing": refusal(404, "RunNotFoundError", "nothing") });

    renderWithClient(<RunPage id="nothing" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("nothing");
  });
});
