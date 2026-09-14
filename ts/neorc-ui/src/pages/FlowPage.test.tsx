import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WORD_PICKER, WORD_PICKER_1_0 } from "../test/fixtures";
import { mockApi, refusal, renderWithClient } from "../test/render";
import { FlowPage } from "./FlowPage";

describe("FlowPage", () => {
  it("shows the latest definition and every version", async () => {
    mockApi({
      "/flows/word_picker": WORD_PICKER,
      "/flows/word_picker/versions": {
        versions: [WORD_PICKER, WORD_PICKER_1_0],
      },
    });

    renderWithClient(<FlowPage flow="word_picker" />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "word_picker 1.2.0",
    );
    const options = screen.getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "1.2.0 (latest)",
      "1.0.0",
    ]);
    expect(screen.getByLabelText("Definition")).toHaveTextContent(
      '"handler": "tasks:split_words"',
    );
    expect(screen.getByRole("link", { name: "Runs of this flow" })).toHaveAttribute(
      "href",
      "#/runs?flow=word_picker",
    );
  });

  it("navigates to a chosen version", async () => {
    mockApi({
      "/flows/word_picker": WORD_PICKER,
      "/flows/word_picker/versions/1.0.0": WORD_PICKER_1_0,
      "/flows/word_picker/versions": {
        versions: [WORD_PICKER, WORD_PICKER_1_0],
      },
    });

    renderWithClient(<FlowPage flow="word_picker" />);
    await screen.findByRole("heading", { level: 1 });
    fireEvent.change(screen.getByLabelText(/Version/), {
      target: { value: "1.0.0" },
    });

    expect(location.hash).toBe("#/flows/word_picker?version=1.0.0");
  });

  it("reports a flow the manager does not have", async () => {
    mockApi({
      "/flows/nothing": refusal(404, "FlowNotFoundError", "no flow 'nothing'"),
      "/flows/nothing/versions": refusal(
        404,
        "FlowNotFoundError",
        "no flow 'nothing'",
      ),
    });

    renderWithClient(<FlowPage flow="nothing" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("no flow 'nothing'");
  });
});
