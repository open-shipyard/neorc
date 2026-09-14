import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import { useMemo, useState } from "react";
// React Flow's own styles, once, bundled with ours.
import "@xyflow/react/dist/style.css";

import type { Published, StepNode } from "../definition";
import { shortId } from "../format";
import { runGraph, type NodeData } from "../graph";
import { href } from "../router";
import { TaskDetails } from "./TaskDetails";

type GraphFlowNode = Node<NodeData>;

/**
 * The run's steps as a graph: a node per step instance, iterations and
 * branches as boxes around them, edges from each step to the ones reading
 * it. Read-only: pan and zoom, click a task for its details.
 */
export function RunGraph({ steps, done }: { steps: StepNode[]; done: Published }) {
  // The poll gives new tasks and sub-runs only when something changed, so
  // the layout is redone only then.
  const graph = useMemo(() => runGraph(steps, done), [steps, done]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = graph.nodes.find((node) => node.id === selectedId)?.data;
  const nodes = useMemo(
    (): GraphFlowNode[] =>
      graph.nodes.map((node) => ({
        ...node,
        selected: node.id === selectedId,
        // Where edges attach, known from the sizes: React Flow measures the
        // handles in the browser, and draws edges from these until then.
        handles: [
          { type: "target", position: Position.Top, x: node.width / 2, y: 0, width: 1, height: 1 },
          {
            type: "source",
            position: Position.Bottom,
            x: node.width / 2,
            y: node.height,
            width: 1,
            height: 1,
          },
        ],
        // A parent's box is drawn behind its children, not over them.
        ...(node.type === "container" && { zIndex: -1 }),
      })),
    [graph, selectedId],
  );
  const edges = useMemo(
    (): Edge[] =>
      graph.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed },
        ariaLabel: `${edge.source} feeds ${edge.target}`,
      })),
    [graph],
  );

  return (
    <div className="run-graph">
      <div className="run-graph-canvas" aria-label="Run graph">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          minZoom={0.05}
          colorMode="system"
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable
          edgesFocusable={false}
          onNodeClick={(_, node) => setSelectedId(node.data.kind === "task" ? node.id : null)}
          onPaneClick={() => setSelectedId(null)}
        >
          <Background />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
      {selected?.kind === "task" && selected.task && (
        <aside className="run-graph-panel" aria-label={`${selected.name} details`}>
          <div className="run-graph-panel-head">
            <strong>{selected.name}</strong>
            <button type="button" onClick={() => setSelectedId(null)}>
              Close
            </button>
          </div>
          <TaskDetails task={selected.task} open />
        </aside>
      )}
    </div>
  );
}

const NODE_TYPES: NodeTypes = {
  task: TaskNode,
  flow: FlowNode,
  container: ContainerNode,
};

function TaskNode({ data }: NodeProps<Node<Extract<NodeData, { kind: "task" }>>>) {
  return (
    <div className={`graph-node graph-task${data.task ? "" : " graph-none"}`}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <Dot status={data.status} />
      <div className="graph-node-text">
        <strong>{data.name}</strong>
        <span className="muted">{data.handler}</span>
      </div>
      <span className="graph-node-when mono">{data.duration || data.status}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

function FlowNode({ data }: NodeProps<Node<Extract<NodeData, { kind: "flow" }>>>) {
  return (
    <div className={`graph-node graph-flow${data.run ? "" : " graph-none"}`}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <Dot status={data.status} />
      <div className="graph-node-text">
        <strong>{data.name}</strong>
        <span className="muted">sub-flow {data.flow}</span>
        {data.run && (
          <a href={href({ name: "run", id: data.run.id })}>run {shortId(data.run.id)}</a>
        )}
      </div>
      <span className="graph-node-when mono">{data.duration || data.status}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

function ContainerNode({
  data,
  width,
  height,
}: NodeProps<Node<Extract<NodeData, { kind: "container" }>>>) {
  return (
    <div
      className={`graph-container${data.entered ? "" : " graph-none"}`}
      style={{ width, height }}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="graph-container-title">
        <strong>{data.name}</strong> <span>{data.instance}</span>{" "}
        <span className="muted">{data.about}</span>
      </div>
    </div>
  );
}

function Dot({ status }: { status: string }) {
  return <span className={`dot dot-${status}`} data-status={status} aria-label={status} />;
}
