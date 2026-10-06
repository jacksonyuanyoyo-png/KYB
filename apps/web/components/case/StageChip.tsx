import { CircleCheck, ClipboardList, FileText, Network, ShieldCheck } from "lucide-react";
import type { Stage } from "@/lib/insights";

const STAGE_META: Record<Stage, { label: string; icon: typeof Network; color: string }> = {
  OWNERSHIP: { label: "Ownership", icon: Network, color: "#b45309" },
  DETAILS: { label: "Account details", icon: ClipboardList, color: "#7c3aed" },
  DOCUMENTS: { label: "Documents", icon: FileText, color: "#ea580c" },
  COMPLIANCE: { label: "Compliance", icon: ShieldCheck, color: "#0891b2" },
  DONE: { label: "Approved", icon: CircleCheck, color: "#15803d" },
};

export function StageChip({ stage }: { stage: Stage }) {
  const meta = STAGE_META[stage];
  const Icon = meta.icon;
  return <span className="stage-chip"><Icon color={meta.color} />{meta.label}</span>;
}

export const STAGE_COLORS: Record<Stage, string> = Object.fromEntries(Object.entries(STAGE_META).map(([key, value]) => [key, value.color])) as Record<Stage, string>;
export const STAGE_NAMES: Record<Stage, string> = Object.fromEntries(Object.entries(STAGE_META).map(([key, value]) => [key, value.label])) as Record<Stage, string>;
