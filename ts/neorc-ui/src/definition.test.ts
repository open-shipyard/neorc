import { describe, expect, it } from "vitest";

import { addressKey, instancesOf, stepsOf } from "./definition";
import { RECORDED } from "./test/recorded";

describe("stepsOf", () => {
  it("reads tasks, sub-flows, loops and fan-outs, containers with their own", () => {
    const steps = stepsOf(RECORDED.flows["word_picker_rounds"]?.content);

    expect(steps.map((step) => [step.kind, step.name])).toEqual([
      ["fan_out", "decorate"],
      ["task", "final_words"],
      ["task", "report"],
      ["loop", "runs"],
    ]);
    const runs = steps.find((step) => step.name === "runs");
    expect(runs).toMatchObject({ kind: "loop", maxCycles: 4, exitCondition: "ran_enough" });
    // Keys as the manager stores them, sorted: the order in a file is formatting.
    expect(runs?.kind === "loop" && runs.children.map((c) => [c.kind, c.name])).toEqual([
      ["flow", "picker"],
      ["task", "ran_enough"],
    ]);
    const decorate = steps.find((step) => step.name === "decorate");
    expect(decorate).toMatchObject({ kind: "fan_out", over: "over tasks.final_words" });
    const padding = decorate?.kind === "fan_out" ? decorate.children[0] : undefined;
    expect(padding).toMatchObject({ kind: "loop", name: "padding", maxCycles: 5 });
    const pad = padding?.kind === "loop" ? padding.children.find((c) => c.name === "pad") : undefined;
    expect(pad).toMatchObject({
      kind: "task",
      handler: "tasks:pad",
      queue: "default",
      params: { word: "neorc.item", loop_count: "neorc.loop_count" },
      fixedParams: { pad_char: "*" },
    });
  });

  it("makes something of a definition it does not understand", () => {
    expect(stepsOf(null)).toEqual([]);
    expect(stepsOf({ steps: { odd: 1 } })).toMatchObject([{ kind: "task", name: "odd" }]);
  });
});

describe("instancesOf", () => {
  const addresses = RECORDED.tasks.map((task) => task.address);

  it("finds a container's iterations and branches at its scope", () => {
    expect(instancesOf("runs", [], addresses)).toEqual([1, 2]);
    expect(instancesOf("decorate", [], addresses)).toEqual([1, 2]);
    expect(instancesOf("padding", [["decorate", 1]], addresses)).toEqual([1, 2]);
    expect(instancesOf("padding", [], addresses)).toEqual([]);
    expect(instancesOf("padding", [["decorate", 9]], addresses)).toEqual([]);
  });

  it("keys an address by its step and scope", () => {
    expect(addressKey("pad", [["decorate", 1], ["padding", 2]])).toBe(
      "pad@decorate:1/padding:2",
    );
    expect(addressKey("report", [])).toBe("report@");
  });
});
