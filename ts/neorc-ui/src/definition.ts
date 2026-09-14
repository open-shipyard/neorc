// A flow definition as uploaded, read into the steps the run page draws, and
// the addresses of a run's tasks and sub-runs read into loop iterations and
// fan-out branches.
import type { Address } from "./api";

export type StepNode =
  | {
      kind: "task";
      name: string;
      handler: string;
      queue: string;
      params: Record<string, string>;
      fixedParams: Record<string, unknown>;
    }
  | { kind: "flow"; name: string; flow: string; params: Record<string, string> }
  | {
      kind: "loop";
      name: string;
      maxCycles: number;
      exitCondition: string;
      children: StepNode[];
    }
  | { kind: "fan_out"; name: string; over: string; children: StepNode[] };

type Json = Record<string, unknown>;

function record(value: unknown): Json {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Json)
    : {};
}

function strings(value: unknown): Record<string, string> {
  return Object.fromEntries(
    Object.entries(record(value)).map(([key, item]) => [key, String(item)]),
  );
}

/** The steps of a definition's `steps` mapping, containers with their own. */
export function stepsOf(content: unknown): StepNode[] {
  return Object.entries(record(record(content)["steps"])).map(([name, raw]) =>
    stepOf(name, record(raw)),
  );
}

function stepOf(name: string, step: Json): StepNode {
  if ("loop" in step) {
    const loop = record(step["loop"]);
    return {
      kind: "loop",
      name,
      maxCycles: Number(loop["max_cycles"] ?? 0),
      exitCondition: String(loop["exit_condition"] ?? ""),
      children: stepsOf(step),
    };
  }
  if ("fan_out" in step) {
    const fanOut = record(step["fan_out"]);
    const over =
      "range" in fanOut
        ? `range ${String(fanOut["range"])}`
        : `over ${String(fanOut["over"] ?? "")}`;
    return { kind: "fan_out", name, over, children: stepsOf(step) };
  }
  if ("flow" in step) {
    return {
      kind: "flow",
      name,
      flow: String(step["flow"]),
      params: strings(step["params"]),
    };
  }
  return {
    kind: "task",
    name,
    handler: String(step["handler"] ?? ""),
    queue: String(step["queue"] ?? "default"),
    params: strings(step["params"]),
    fixedParams: record(step["fixed_params"]),
  };
}

export type Scope = Address["scope"];

/** One text per address, to find a run's task or sub-run for a step instance. */
export function addressKey(step: string, scope: Scope): string {
  return `${step}@${scope.map(([name, n]) => `${name}:${n}`).join("/")}`;
}

/**
 * The numbers a container at `scope` has been entered with: its iterations
 * or branches so far, from the addresses of what the run published.
 */
export function instancesOf(
  container: string,
  scope: Scope,
  addresses: Address[],
): number[] {
  const numbers = new Set<number>();
  for (const address of addresses) {
    const level = address.scope[scope.length];
    if (
      level !== undefined &&
      level[0] === container &&
      scope.every(([name, n], i) => address.scope[i]?.[0] === name && address.scope[i]?.[1] === n)
    ) {
      numbers.add(level[1]);
    }
  }
  return [...numbers].sort((a, b) => a - b);
}
