import { describe, expect, it } from "vitest";

import { published, stepsOf } from "./definition";
import {
  CONTAINER_HEADER,
  containerNodeId,
  referencedStep,
  runGraph,
  stepNodeId,
  type Graph,
  type GraphNode,
} from "./graph";
import type { Task } from "./api";
import { SPLIT } from "./test/fixtures";
import { RECORDED } from "./test/recorded";

function recordedGraph(): Graph {
  const { flows, tasks, sub_runs } = RECORDED;
  return runGraph(stepsOf(flows["word_picker_rounds"]?.content), published(tasks, sub_runs));
}

function node(graph: Graph, id: string): GraphNode {
  const found = graph.nodes.find((n) => n.id === id);
  if (!found) throw new Error(`no node ${id} among ${graph.nodes.map((n) => n.id).join(", ")}`);
  return found;
}

function edgeIds(graph: Graph): string[] {
  return graph.edges.map((edge) => edge.id).sort();
}

function overlap(a: GraphNode, b: GraphNode): boolean {
  return (
    a.position.x < b.position.x + b.width &&
    b.position.x < a.position.x + a.width &&
    a.position.y < b.position.y + b.height &&
    b.position.y < a.position.y + a.height
  );
}

describe("runGraph on the recorded word_picker_rounds run", () => {
  it("has one node per step instance, nested in a node per iteration or branch", () => {
    const graph = recordedGraph();

    expect(graph.nodes).toHaveLength(22);
    const runs1 = node(graph, containerNodeId("runs", [], 1));
    expect(runs1.data).toMatchObject({
      kind: "container",
      container: "loop",
      instance: "iteration 1",
      entered: true,
      about: "loop, at most 4, until ran_enough",
    });
    expect(runs1.parentId).toBeUndefined();
    const picker1 = node(graph, stepNodeId("picker", [["runs", 1]]));
    expect(picker1).toMatchObject({ type: "flow", parentId: runs1.id, extent: "parent" });
    expect(picker1.data).toMatchObject({
      kind: "flow",
      flow: "word_picker",
      status: "succeeded",
      run: { id: RECORDED.sub_runs[0]?.id },
    });
    const pad = node(graph, stepNodeId("pad", [["decorate", 2], ["padding", 1]]));
    expect(pad).toMatchObject({
      type: "task",
      parentId: containerNodeId("padding", [["decorate", 2]], 1),
    });
    expect(pad.data).toMatchObject({ kind: "task", handler: "tasks:pad", status: "succeeded" });
    expect(pad.data.kind === "task" && pad.data.task?.address).toEqual({
      step: "pad",
      scope: [["decorate", 2], ["padding", 1]],
    });
    expect(pad.data.kind === "task" && pad.data.duration).toMatch(/ms$/);
    expect(node(graph, containerNodeId("padding", [["decorate", 2]], 1)).parentId).toBe(
      containerNodeId("decorate", [], 2),
    );
    expect(node(graph, stepNodeId("report", [])).parentId).toBeUndefined();
  });

  it("lists parents before their children, as React Flow requires", () => {
    const graph = recordedGraph();
    const seen = new Set<string>();
    for (const n of graph.nodes) {
      if (n.parentId !== undefined) expect(seen.has(n.parentId)).toBe(true);
      seen.add(n.id);
    }
  });

  it("draws an edge from the step a param refers to, into the instance it resolves to", () => {
    const graph = recordedGraph();
    const id = (source: string, target: string) => `${source}->${target}`;
    const iteration = (n: number): [string, number][] => [["runs", n]];
    const padding = (branch: number, n: number): [string, number][] => [
      ["decorate", branch],
      ["padding", n],
    ];

    expect(edgeIds(graph)).toEqual(
      [
        // From inside the loop: the iterations so far, the consumer's own last.
        id(stepNodeId("picker", iteration(1)), stepNodeId("ran_enough", iteration(1))),
        id(stepNodeId("picker", iteration(1)), stepNodeId("ran_enough", iteration(2))),
        id(stepNodeId("picker", iteration(2)), stepNodeId("ran_enough", iteration(2))),
        // From after the loop: every iteration.
        id(stepNodeId("picker", iteration(1)), stepNodeId("final_words", [])),
        id(stepNodeId("picker", iteration(2)), stepNodeId("final_words", [])),
        // What the fan-out iterates over feeds each branch.
        id(stepNodeId("final_words", []), containerNodeId("decorate", [], 1)),
        id(stepNodeId("final_words", []), containerNodeId("decorate", [], 2)),
        // The same branch, the iterations so far of the loop inside it.
        ...[1, 2].flatMap((branch) =>
          [1, 2].flatMap((n) =>
            [1, 2]
              .filter((m) => m <= n)
              .map((m) =>
                id(stepNodeId("pad", padding(branch, m)), stepNodeId("long_enough", padding(branch, n))),
              ),
          ),
        ),
        // From outside the fan-out and the loop in it: every branch, every iteration.
        ...[1, 2].flatMap((branch) =>
          [1, 2].map((n) => id(stepNodeId("pad", padding(branch, n)), stepNodeId("report", []))),
        ),
      ].sort(),
    );
    const report = graph.edges.filter((e) => e.target === stepNodeId("report", []));
    expect(report.map((e) => e.params)).toEqual([["padded"], ["padded"], ["padded"], ["padded"]]);
    const decorate = graph.edges.find((e) => e.target === containerNodeId("decorate", [], 1));
    expect(decorate?.params).toEqual(["over"]);
  });

  it("positions every node inside its parent, below its title, with no overlap", () => {
    const graph = recordedGraph();

    expect(graph.width).toBeGreaterThan(0);
    expect(graph.height).toBeGreaterThan(0);
    for (const n of graph.nodes) {
      expect(n.width).toBeGreaterThan(0);
      expect(n.height).toBeGreaterThan(0);
      const bounds = n.parentId ? node(graph, n.parentId) : { width: graph.width, height: graph.height };
      expect(n.position.x).toBeGreaterThanOrEqual(0);
      expect(n.position.y).toBeGreaterThanOrEqual(n.parentId ? CONTAINER_HEADER : 0);
      expect(n.position.x + n.width).toBeLessThanOrEqual(bounds.width);
      expect(n.position.y + n.height).toBeLessThanOrEqual(bounds.height);
    }
    for (const a of graph.nodes) {
      for (const b of graph.nodes) {
        if (a.id < b.id && a.parentId === b.parentId) {
          expect(overlap(a, b), `${a.id} overlaps ${b.id}`).toBe(false);
        }
      }
    }
  });

  it("stacks a loop's iterations top to bottom and a fan-out's branches side by side", () => {
    const graph = recordedGraph();
    const runs1 = node(graph, containerNodeId("runs", [], 1));
    const runs2 = node(graph, containerNodeId("runs", [], 2));
    expect(runs2.position.y).toBeGreaterThanOrEqual(runs1.position.y + runs1.height);
    const branch1 = node(graph, containerNodeId("decorate", [], 1));
    const branch2 = node(graph, containerNodeId("decorate", [], 2));
    expect(branch1.position.y).toBe(branch2.position.y);
    expect(branch2.position.x).toBeGreaterThanOrEqual(branch1.position.x + branch1.width);
  });

  it("draws a run that has published nothing yet with each container not entered", () => {
    const { flows } = RECORDED;
    const graph = runGraph(stepsOf(flows["word_picker_rounds"]?.content), published([], []));

    expect(graph.nodes.map((n) => n.id).sort()).toEqual(
      [
        containerNodeId("runs", [], 0),
        stepNodeId("picker", [["runs", 0]]),
        stepNodeId("ran_enough", [["runs", 0]]),
        stepNodeId("final_words", []),
        containerNodeId("decorate", [], 0),
        containerNodeId("padding", [["decorate", 0]], 0),
        stepNodeId("pad", [["decorate", 0], ["padding", 0]]),
        stepNodeId("long_enough", [["decorate", 0], ["padding", 0]]),
        stepNodeId("report", []),
      ].sort(),
    );
    const runs = node(graph, containerNodeId("runs", [], 0));
    expect(runs.data).toMatchObject({ instance: "not entered yet", entered: false });
    const picker = node(graph, stepNodeId("picker", [["runs", 0]]));
    expect(picker.data).toMatchObject({ status: "none", duration: "" });
    expect(picker.data.kind === "flow" && picker.data.run).toBeUndefined();
    expect(edgeIds(graph)).toContain(
      `${stepNodeId("picker", [["runs", 0]])}->${stepNodeId("final_words", [])}`,
    );
    expect(edgeIds(graph)).toContain(
      `${stepNodeId("pad", [["decorate", 0], ["padding", 0]])}->${stepNodeId("report", [])}`,
    );
    expect(edgeIds(graph)).toContain(
      `${stepNodeId("final_words", [])}->${containerNodeId("decorate", [], 0)}`,
    );
  });
});

describe("runGraph on the recorded word_picker sub-run", () => {
  it("joins every instance a reference resolves to", () => {
    const { flows, sub_runs, sub_run_tasks } = RECORDED;
    const sub = sub_runs[0]!;
    const graph = runGraph(
      stepsOf(flows["word_picker"]?.content),
      published(sub_run_tasks[sub.id] ?? [], []),
    );
    const into = (target: string) =>
      graph.edges.filter((e) => e.target === target).map((e) => e.source).sort();

    // In the loop, outside the fan-out nested in it: every branch, of the iterations so far.
    expect(into(stepNodeId("collect_words", [["rounds", 1]]))).toEqual(
      [1, 2, 3].map((branch) => stepNodeId("extract_word", [["rounds", 1], ["picking", branch]])),
    );
    expect(into(stepNodeId("collect_words", [["rounds", 2]]))).toEqual(
      [1, 2].flatMap((round) =>
        [1, 2, 3].map((branch) => stepNodeId("extract_word", [["rounds", round], ["picking", branch]])),
      ),
    );
    // In the loop, a sibling: its own iteration and the one before, chained.
    expect(into(stepNodeId("enough_rounds", [["rounds", 3]]))).toEqual(
      [2, 3].map((round) => stepNodeId("collect_words", [["rounds", round]])),
    );
    expect(into(stepNodeId("enough_rounds", [["rounds", 1]]))).toEqual([
      stepNodeId("collect_words", [["rounds", 1]]),
    ]);
    // After the loop: every iteration.
    expect(into(stepNodeId("latest_round", []))).toEqual(
      [1, 2, 3, 4].map((round) => stepNodeId("collect_words", [["rounds", round]])),
    );
    // A fan-out over a range is fed by nothing.
    expect(into(containerNodeId("grading", [], 2))).toEqual([]);
    // Outside a fan-out: every branch.
    expect(into(stepNodeId("keep_matching_words", []))).toEqual(
      [1, 2, 3].map((branch) => stepNodeId("score_words", [["grading", branch]])),
    );
    // From deep inside, a step at the root.
    expect(into(stepNodeId("pick_word", [["rounds", 3], ["picking", 2]]))).toEqual([
      stepNodeId("compose_payload", []),
    ]);
  });
});

describe("runGraph on nested containers", () => {
  const task = (step: string, scope: [string, number][]): Task => ({
    ...SPLIT,
    id: `${step}@${scope.map(([c, n]) => `${c}${n}`).join("/")}`,
    address: { step, scope },
  });

  it("joins a loop in an earlier iteration of the loop around it from every iteration", () => {
    const steps = stepsOf({
      steps: {
        outer: {
          loop: { max_cycles: 5, exit_condition: "b" },
          steps: {
            inner: {
              loop: { max_cycles: 5, exit_condition: "b" },
              steps: { a: { handler: "t:a" }, b: { handler: "t:b", params: { x: "tasks.a" } } },
            },
          },
        },
      },
    });
    const tasks = [
      ...[1, 2, 3].flatMap((n) => [
        task("a", [["outer", 1], ["inner", n]]),
        task("b", [["outer", 1], ["inner", n]]),
      ]),
      task("a", [["outer", 2], ["inner", 1]]),
      task("b", [["outer", 2], ["inner", 1]]),
    ];
    const graph = runGraph(steps, published(tasks, []));

    const into = (target: string) =>
      graph.edges.filter((e) => e.target === target).map((e) => e.source).sort();
    expect(into(stepNodeId("b", [["outer", 2], ["inner", 1]]))).toEqual(
      [
        ...[1, 2, 3].map((n) => stepNodeId("a", [["outer", 1], ["inner", n]])),
        stepNodeId("a", [["outer", 2], ["inner", 1]]),
      ].sort(),
    );
    expect(into(stepNodeId("b", [["outer", 1], ["inner", 3]]))).toEqual(
      [2, 3].map((n) => stepNodeId("a", [["outer", 1], ["inner", n]])),
    );
  });

  it("leaves out a branch an earlier iteration never had", () => {
    const steps = stepsOf({
      steps: {
        rounds: {
          loop: { max_cycles: 5, exit_condition: "b" },
          steps: {
            picking: {
              fan_out: { over: "inputs.words" },
              steps: { a: { handler: "t:a" }, b: { handler: "t:b", params: { x: "tasks.a" } } },
            },
          },
        },
      },
    });
    const tasks = [
      ...[1, 2].flatMap((branch) => [
        task("a", [["rounds", 1], ["picking", branch]]),
        task("b", [["rounds", 1], ["picking", branch]]),
      ]),
      ...[1, 2, 3].flatMap((branch) => [
        task("a", [["rounds", 2], ["picking", branch]]),
        task("b", [["rounds", 2], ["picking", branch]]),
      ]),
    ];
    const graph = runGraph(steps, published(tasks, []));

    const ids = new Set(graph.nodes.map((n) => n.id));
    for (const edge of graph.edges) {
      expect(ids.has(edge.source), edge.id).toBe(true);
      expect(ids.has(edge.target), edge.id).toBe(true);
    }
    const into = (target: string) =>
      graph.edges.filter((e) => e.target === target).map((e) => e.source).sort();
    expect(into(stepNodeId("b", [["rounds", 2], ["picking", 3]]))).toEqual([
      stepNodeId("a", [["rounds", 2], ["picking", 3]]),
    ]);
    expect(into(stepNodeId("b", [["rounds", 2], ["picking", 2]]))).toEqual([
      stepNodeId("a", [["rounds", 1], ["picking", 2]]),
      stepNodeId("a", [["rounds", 2], ["picking", 2]]),
    ]);
  });
});

describe("referencedStep", () => {
  it("names the step of a tasks. or flows. reference, and nothing else", () => {
    expect(referencedStep("tasks.pick_word")).toBe("pick_word");
    expect(referencedStep("flows.picker")).toBe("picker");
    expect(referencedStep("tasks.pick_word.field")).toBe("pick_word");
    expect(referencedStep("inputs.sentence")).toBeUndefined();
    expect(referencedStep("neorc.item")).toBeUndefined();
    expect(referencedStep("tasks.")).toBeUndefined();
    expect(referencedStep("")).toBeUndefined();
  });
});
