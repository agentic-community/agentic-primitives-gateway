import {
  Background,
  Controls,
  type Edge,
  type Node,
  ReactFlow,
  type Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { useMemo } from "react";
import type {
  AgentLineageNode,
  AgentVersion,
  TeamLineageNode,
  TeamVersion,
  VersionStatus,
} from "../api/types";

/**
 * Shared lineage visualization used by ``/agents/:name/lineage`` and
 * ``/teams/:name/lineage``.  Takes a list of typed lineage nodes and
 * auto-lays them out with Dagre (top-down), then renders React Flow.
 *
 * Intra-identity edges (parent_version_id) are solid.  Cross-identity
 * fork edges (forked_from) are dashed and labelled "fork".  Status
 * drives the node border color.
 */

type AnyVersion = AgentVersion | TeamVersion;

type AnyNode =
  | { kind: "agent"; node: AgentLineageNode }
  | { kind: "team"; node: TeamLineageNode };

interface Props {
  nodes: AnyNode[];
  deployed: Record<string, string>;
  onSelect?: (version: AnyVersion) => void;
  selectedVersionId?: string | null;
}

const STATUS_BORDER: Record<VersionStatus, string> = {
  deployed: "border-green-500",
  draft: "border-gray-400",
  proposed: "border-blue-500",
  archived: "border-gray-300 dark:border-gray-700",
  rejected: "border-red-500",
};

const NODE_WIDTH = 200;
const NODE_HEIGHT = 60;

function versionOf(n: AnyNode): AnyVersion {
  return n.node.version;
}

function bareName(n: AnyNode): string {
  const v = versionOf(n);
  return "agent_name" in v ? v.agent_name : v.team_name;
}

export default function LineageGraph({ nodes, deployed, onSelect, selectedVersionId }: Props) {
  const { rfNodes, rfEdges } = useMemo(() => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 60 });
    g.setDefaultEdgeLabel(() => ({}));

    for (const n of nodes) {
      const v = versionOf(n);
      g.setNode(v.version_id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }

    const versionIds = new Set(nodes.map((n) => versionOf(n).version_id));

    // Parent edges (intra-identity)
    for (const n of nodes) {
      const v = versionOf(n);
      if (v.parent_version_id && versionIds.has(v.parent_version_id)) {
        g.setEdge(v.parent_version_id, v.version_id);
      }
    }
    // Fork edges (cross-identity) — point from source version to this one
    for (const n of nodes) {
      const v = versionOf(n);
      if (v.forked_from && versionIds.has(v.forked_from.version_id)) {
        g.setEdge(v.forked_from.version_id, v.version_id);
      }
    }

    dagre.layout(g);

    const deployedIds = new Set(Object.values(deployed));

    const rfNodes: Node[] = nodes.map((n) => {
      const v = versionOf(n);
      const pos = g.node(v.version_id);
      const identity = `${v.owner_id}:${bareName(n)}`;
      const isDeployed = deployedIds.has(v.version_id);
      const border = STATUS_BORDER[v.status] ?? "border-gray-400";
      const selected = selectedVersionId === v.version_id;
      return {
        id: v.version_id,
        type: "default",
        position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
        sourcePosition: "bottom" as Position,
        targetPosition: "top" as Position,
        data: {
          label: (
            <div
              className={
                "flex flex-col items-center gap-0.5 py-1 px-2 rounded border-2 bg-white dark:bg-gray-900 " +
                border +
                (selected ? " ring-2 ring-indigo-400" : "")
              }
              style={{ width: NODE_WIDTH - 4 }}
            >
              <div className="flex items-center gap-1 text-[11px] font-mono font-semibold text-gray-900 dark:text-gray-100">
                v{v.version_number}
                {isDeployed && (
                  <span className="text-[9px] uppercase tracking-wide text-green-600 dark:text-green-400">
                    ● live
                  </span>
                )}
              </div>
              <div className="text-[10px] font-mono text-gray-500 dark:text-gray-400 truncate max-w-full">
                {identity}
              </div>
              <div className="text-[9px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
                {v.status}
              </div>
            </div>
          ),
        },
        style: {
          // Hide the default node frame — we draw our own above.
          background: "transparent",
          border: "none",
          padding: 0,
          width: NODE_WIDTH,
        },
      };
    });

    const rfEdges: Edge[] = [];
    for (const n of nodes) {
      const v = versionOf(n);
      if (v.parent_version_id && versionIds.has(v.parent_version_id)) {
        rfEdges.push({
          id: `p-${v.parent_version_id}-${v.version_id}`,
          source: v.parent_version_id,
          target: v.version_id,
          style: { stroke: "#9ca3af" },
        });
      }
      if (v.forked_from && versionIds.has(v.forked_from.version_id)) {
        rfEdges.push({
          id: `f-${v.forked_from.version_id}-${v.version_id}`,
          source: v.forked_from.version_id,
          target: v.version_id,
          label: "fork",
          animated: true,
          style: { stroke: "#6366f1", strokeDasharray: "6 4" },
          labelStyle: { fontSize: 10, fill: "#6366f1" },
        });
      }
    }

    return { rfNodes, rfEdges };
  }, [nodes, deployed, selectedVersionId]);

  return (
    <div className="w-full h-[60vh] rounded border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodesDraggable={false}
        nodesConnectable={false}
        onNodeClick={
          onSelect
            ? (_e, node) => {
                const match = nodes.find((n) => versionOf(n).version_id === node.id);
                if (match) onSelect(versionOf(match));
              }
            : undefined
        }
        fitView
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
