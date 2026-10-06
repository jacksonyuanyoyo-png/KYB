import { BookOpen, ClipboardList, FilePlus2, FileUp, FolderPlus, ListChecks, Network, ShieldCheck, Sparkles, SquareCheck, Workflow } from "lucide-react";
import type { AuditAction } from "@/lib/types";

const META: Record<AuditAction, { icon: typeof Network; color: string; bg: string; label: string }> = {
  CASE_CREATED: { icon: FolderPlus, color: "#ea580c", bg: "#fff7ed", label: "Case created" },
  OWNERSHIP_UPDATED: { icon: Network, color: "#b45309", bg: "#fff4e0", label: "Ownership" },
  PROFILE_UPDATED: { icon: ClipboardList, color: "#7c3aed", bg: "#f4f0ff", label: "Account details" },
  DOCUMENT_UPLOADED: { icon: FileUp, color: "#0891b2", bg: "#e6f7fb", label: "Document" },
  CHECKLIST_UPDATED: { icon: ListChecks, color: "#ea580c", bg: "#fff7ed", label: "Checklist" },
  REQUIREMENT_ADDED: { icon: FilePlus2, color: "#475569", bg: "#f1f4f8", label: "Requirement" },
  STATUS_CHANGED: { icon: Workflow, color: "#0f172a", bg: "#f1f4f8", label: "Status" },
  COMPLIANCE_DECISION: { icon: ShieldCheck, color: "#15803d", bg: "#ecfdf3", label: "Compliance" },
  AI_SUGGESTION: { icon: Sparkles, color: "#7c3aed", bg: "#f4f0ff", label: "AI suggestion" },
  TASK_UPDATED: { icon: SquareCheck, color: "#475569", bg: "#f1f4f8", label: "Task" },
  RULE_LIBRARY: { icon: BookOpen, color: "#ea580c", bg: "#fff7ed", label: "Rule library" },
};

export function AuditIcon({ action, className = "activity-icon" }: { action: AuditAction; className?: string }) {
  const meta = META[action];
  const Icon = meta.icon;
  return <span className={className} style={{ color: meta.color, background: meta.bg, borderColor: "transparent" }}><Icon /></span>;
}

export const AUDIT_LABELS: Record<AuditAction, string> = Object.fromEntries(Object.entries(META).map(([key, value]) => [key, value.label])) as Record<AuditAction, string>;
