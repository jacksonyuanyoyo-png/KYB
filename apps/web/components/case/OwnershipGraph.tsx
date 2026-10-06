"use client";

import {
  Background, BackgroundVariant, BaseEdge, EdgeLabelRenderer, Handle, Panel, Position, ReactFlow,
  ReactFlowProvider, getSmoothStepPath, useEdgesState, useNodesInitialized, useNodesState, useReactFlow,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import { CircleAlert, Maximize2, Plus, TriangleAlert, UserRound, ZoomIn, ZoomOut } from "lucide-react";
import { memo, useEffect, useMemo, useRef, type ReactNode } from "react";
import type { Party, ValidationIssue } from "@fcc/domain";
import { ENTITY_VISUALS } from "@/components/ui";
import { formatPercent } from "@/lib/format";
import { ENTITY_LABELS } from "@/lib/labels";

function GraphZoom() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  return (
    <Panel position="bottom-right" className="graph-zoom">
      <button type="button" aria-label="Zoom in" onClick={() => void zoomIn({ duration: 200 })}><ZoomIn /></button>
      <button type="button" aria-label="Zoom out" onClick={() => void zoomOut({ duration: 200 })}><ZoomOut /></button>
      <button type="button" aria-label="Fit view" onClick={() => void fitView({ padding: 0.2, duration: 300, maxZoom: 1 })}><Maximize2 /></button>
    </Panel>
  );
}

const NODE_W = 236;
const GAP_X = 40;
const LEVEL_H = 190;

interface PartyNodeData extends Record<string, unknown> {
  party: Party;
  isRoot: boolean;
  effective: number;
  mustIdentify: boolean;
  issue?: string;
  selected: boolean;
  editable: boolean;
  onAdd: (parentId: string) => void;
}

type PartyNode = Node<PartyNodeData, "party">;
type OwnershipEdgeData = { label: string; variant: "owns" | "control" | "both" };
type OwnershipEdge = Edge<OwnershipEdgeData, "ownership">;

function treeLayout(parties: Party[]): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const children = new Map<string, Party[]>();
  parties.forEach((party) => { if (party.parentId) children.set(party.parentId, [...(children.get(party.parentId) ?? []), party]); });
  let cursor = 0;
  const place = (party: Party, depth: number): number => {
    const kids = children.get(party.id) ?? [];
    let x: number;
    if (!kids.length) { x = cursor; cursor += NODE_W + GAP_X; }
    else { const xs = kids.map((kid) => place(kid, depth + 1)); x = ((xs[0] ?? 0) + (xs[xs.length - 1] ?? 0)) / 2; }
    positions.set(party.id, { x, y: depth * LEVEL_H });
    return x;
  };
  parties.filter((party) => party.parentId === null).forEach((root) => place(root, 0));
  return positions;
}

function edgeLabel(party: Party): OwnershipEdgeData {
  const role = party.title && party.title !== "Shareholder" ? party.title : party.isController ? "Controller" : party.isSigningAuthority ? "Signing authority" : "";
  if (party.ownershipPercent > 0 && role) return { label: `Owns ${formatPercent(party.ownershipPercent)} · ${role}`, variant: party.isController || party.isSigningAuthority ? "both" : "owns" };
  if (party.ownershipPercent > 0) return { label: `Owns ${formatPercent(party.ownershipPercent)}`, variant: "owns" };
  return { label: role || "Related", variant: "control" };
}

const EDGE_COLORS = { owns: "#9aa6bb", control: "#ec4899", both: "#8b5cf6" } as const;

const OwnershipEdgeView = memo(function OwnershipEdgeView({ sourceX, sourceY, targetX, targetY, data }: EdgeProps<OwnershipEdge>) {
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, sourcePosition: Position.Top, targetX, targetY, targetPosition: Position.Bottom, borderRadius: 12 });
  const variant = data?.variant ?? "owns";
  return (
    <>
      <BaseEdge path={path} markerEnd={`url(#arrow-${variant})`} style={{ stroke: EDGE_COLORS[variant], strokeWidth: 1.5, strokeDasharray: variant === "owns" ? undefined : "5 4" }} />
      <EdgeLabelRenderer>
        <div className={`edge-pill ${variant}`} style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>{data?.label}</div>
      </EdgeLabelRenderer>
    </>
  );
});

function Tag({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`tag tone-${tone}`}>{children}</span>;
}

const PartyNodeView = memo(function PartyNodeView({ data }: NodeProps<PartyNode>) {
  const { party, isRoot, effective, mustIdentify, issue, selected, editable, onAdd } = data;
  const visual = party.entityType ? ENTITY_VISUALS[party.entityType] : null;
  const Icon = visual?.icon ?? UserRound;
  const subtitle = isRoot
    ? `Account holder · ${party.entityType ? ENTITY_LABELS[party.entityType] : ""}`
    : party.kind === "ENTITY"
      ? `${party.entityType ? ENTITY_LABELS[party.entityType] : "Entity"}${party.country ? ` · ${party.country}` : ""}`
      : `${party.title ?? "Natural person"} · ${party.country ?? "Country unknown"}`;
  const indirect = party.kind === "PERSON" && Math.abs(effective - party.ownershipPercent) > 0.01 && effective > 0;
  return (
    <div className={`gnode${isRoot ? " root" : ""}${party.isPepHio ? " pep" : ""}${selected ? " selected" : ""}`}>
      <Handle type="target" position={Position.Bottom} isConnectable={false} />
      <Handle type="source" position={Position.Top} isConnectable={false} />
      {issue && <div className="gnode-banner issue-banner"><TriangleAlert />{issue}</div>}
      {!issue && party.isPepHio && <div className="gnode-banner pep-banner"><CircleAlert />PEP / HIO · enhanced review</div>}
      <div className="gnode-main">
        <span className="gnode-icon" style={isRoot ? undefined : { color: visual?.color ?? "#475569", background: visual?.bg ?? "#eef1f6" }}><Icon /></span>
        <div className="gnode-text"><strong title={party.legalName}>{party.legalName}</strong><small>{subtitle}</small></div>
      </div>
      {!isRoot && (
        <div className="gnode-tags">
          {mustIdentify && <Tag tone="blue">Identify</Tag>}
          {indirect && <Tag tone="neutral">Effective {formatPercent(effective)}</Tag>}
          {party.isController && <Tag tone="violet">Controller</Tag>}
          {party.isSigningAuthority && <Tag tone="violet">Signer</Tag>}
          {party.isUsPerson && <Tag tone="amber">US person</Tag>}
          {party.isPepHio && <Tag tone="red">PEP / HIO</Tag>}
          {party.kind === "ENTITY" && !issue && <Tag tone="green">Traced</Tag>}
        </div>
      )}
      {party.kind === "ENTITY" && editable && (
        <button className="gnode-add nodrag" title="Add owner or controller" aria-label={`Add owner or controller under ${party.legalName}`} onClick={(event) => { event.stopPropagation(); onAdd(party.id); }}><Plus /></button>
      )}
    </div>
  );
});

const nodeTypes = { party: PartyNodeView };
const edgeTypes = { ownership: OwnershipEdgeView };

export interface OwnershipGraphProps {
  parties: Party[];
  issues: ValidationIssue[];
  effective: Map<string, number>;
  identifyIds: Set<string>;
  selectedId: string | null;
  editable: boolean;
  layoutKey: number;
  onSelect: (id: string | null) => void;
  onAdd: (parentId: string) => void;
  toolbar?: ReactNode;
}

function GraphCanvas({ parties, issues, effective, identifyIds, selectedId, editable, layoutKey, onSelect, onAdd, toolbar }: OwnershipGraphProps) {
  const { fitView, setCenter, getNode } = useReactFlow();
  const dragged = useRef(new Map<string, { x: number; y: number }>());
  const [nodes, setNodes, onNodesChange] = useNodesState<PartyNode>([]);
  const [edges, setEdges] = useEdgesState<OwnershipEdge>([]);
  const issueByParty = useMemo(() => {
    const map = new Map<string, string>();
    issues.forEach((issue) => {
      if (!issue.partyId) return;
      map.set(issue.partyId, issue.code === "OWNERSHIP_TOTAL" ? issue.message.replace(/^.* ownership totals /, "Ownership totals ") : issue.code === "MISSING_OWNER" ? "No owners or controllers" : "Not traced to natural persons");
    });
    return map;
  }, [issues]);

  useEffect(() => { dragged.current.clear(); }, [layoutKey]);

  useEffect(() => {
    const positions = treeLayout(parties);
    setNodes(parties.map((party) => ({
      id: party.id, type: "party", position: dragged.current.get(party.id) ?? positions.get(party.id) ?? { x: 0, y: 0 },
      data: { party, isRoot: party.parentId === null, effective: effective.get(party.id) ?? 0, mustIdentify: identifyIds.has(party.id), issue: issueByParty.get(party.id), selected: party.id === selectedId, editable, onAdd },
    })));
    setEdges(parties.filter((party) => party.parentId).map((party) => ({ id: `e-${party.id}`, type: "ownership", source: party.id, target: party.parentId!, data: edgeLabel(party) })));
  }, [parties, effective, identifyIds, issueByParty, selectedId, editable, onAdd, setNodes, setEdges, layoutKey]);

  const nodeCount = parties.length;
  const initialized = useNodesInitialized();
  useEffect(() => {
    if (!initialized) return;
    const timer = setTimeout(() => void fitView({ padding: 0.2, duration: 300, maxZoom: 1 }), 40);
    return () => clearTimeout(timer);
  }, [initialized, nodeCount, layoutKey, fitView]);

  useEffect(() => {
    if (!selectedId) return;
    const node = getNode(selectedId);
    if (node && !node.dragging) {
      const zoomTimer = setTimeout(() => void setCenter(node.position.x + NODE_W / 2, node.position.y + 60, { zoom: 1, duration: 300 }), 0);
      return () => clearTimeout(zoomTimer);
    }
  }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="graph-wrap">
      <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden>
        <defs>
          {(Object.keys(EDGE_COLORS) as Array<keyof typeof EDGE_COLORS>).map((key) => (
            <marker key={key} id={`arrow-${key}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill={EDGE_COLORS[key]} />
            </marker>
          ))}
        </defs>
      </svg>
      {toolbar && <div className="graph-toolbar">{toolbar}</div>}
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        onNodesChange={(changes) => {
          onNodesChange(changes);
          changes.forEach((change) => { if (change.type === "position" && change.position) dragged.current.set(change.id, change.position); });
        }}
        onNodeClick={(_, node) => onSelect(node.id)}
        onPaneClick={() => onSelect(null)}
        nodesConnectable={false} edgesFocusable={false} minZoom={0.25} maxZoom={1.6}
        attributionPosition="bottom-center"
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1.3} color="#d3d9e4" />
        <GraphZoom />
      </ReactFlow>
      <div className="graph-legend">
        <span><i className="legend-line" />Ownership</span>
        <span><i className="legend-line dashed" />Control / signing</span>
        <span><i className="legend-ring" />PEP / HIO</span>
        <span><span className="tag tone-blue" style={{ height: 16, fontSize: 9 }}>Identify</span>≥25% or control</span>
      </div>
    </div>
  );
}

export function OwnershipGraph(props: OwnershipGraphProps) {
  return <ReactFlowProvider><GraphCanvas {...props} /></ReactFlowProvider>;
}
