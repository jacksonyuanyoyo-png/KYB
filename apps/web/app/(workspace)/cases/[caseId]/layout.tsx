"use client";

import { Calendar, Check, ChevronRight, FolderSearch, Hash, Lock, MapPin, Send, ShieldCheck, Sparkles, UserRound } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useMemo, useState, type ReactNode } from "react";
import { AssistantDrawer } from "@/components/case/AssistantDrawer";
import { CaseContext } from "@/components/case/CaseContext";
import { EmptyState, StatusBadge, useAction } from "@/components/ui";
import { changeStatus } from "@/lib/data/actions";
import { useCase, useSession, userName } from "@/lib/data/hooks";
import { formatDate } from "@/lib/format";
import { STAGES, stageIndex } from "@/lib/insights";
import { ENTITY_LABELS } from "@/lib/labels";

export default function CaseLayout({ children }: { children: ReactNode }) {
  const { caseId } = useParams<{ caseId: string }>();
  const pathname = usePathname();
  const router = useRouter();
  const session = useSession()!;
  const { record, insight } = useCase(caseId);
  const { run, pending } = useAction();
  const [assistant, setAssistant] = useState<{ open: boolean; prompt?: string }>({ open: false });

  const context = useMemo(() => (record && insight ? {
    db: session.db, user: session.user, record, insight,
    openAssistant: (prompt?: string) => setAssistant({ open: true, prompt }),
  } : null), [session, record, insight]);

  if (!record || !insight || !context) {
    return <div className="page"><EmptyState icon={<FolderSearch />} title="Case not found" action={<Link className="btn btn-secondary" href="/cases">Back to cases</Link>}>It may have been removed, or the link is out of date.</EmptyState></div>;
  }

  const base = `/cases/${record.id}`;
  const current = stageIndex(insight.stage);
  const structureReady = !insight.ownershipIssues.length && !insight.detailsGaps.length;
  const allCollected = insight.collected === insight.checklist.length;
  const editable = record.status !== "APPROVED" && record.status !== "READY_FOR_COMPLIANCE";

  const stepState = (index: number) => {
    const id = STAGES[index]!.id;
    if (id === "OWNERSHIP") return insight.ownershipIssues.length ? "blocked" : "done";
    if (id === "DETAILS") return insight.ownershipIssues.length ? "locked" : insight.detailsGaps.length ? "blocked" : "done";
    if (id === "DOCUMENTS") return !structureReady ? "locked" : allCollected ? "done" : "blocked";
    return record.status === "APPROVED" ? "done" : current >= index ? "active" : "locked";
  };

  return (
    <CaseContext.Provider value={context}>
      <div className="case-page">
        <div className="case-header">
          <div className="breadcrumb"><Link href="/cases">Cases</Link><ChevronRight /><span>{record.reference}</span></div>
          <div className="case-title-row">
            <h1>{record.legalName}</h1>
            <StatusBadge status={record.status} />
            <div className="spacer" />
            <button className="btn btn-ai" onClick={() => setAssistant({ open: true })}><Sparkles />Ask AI</button>
            {editable && record.status !== "DOCS_REQUESTED" && structureReady && !allCollected && (
              <button className="btn btn-secondary" disabled={pending} onClick={() => void run(() => changeStatus(record, "DOCS_REQUESTED", "Requested outstanding documents from client"), "Marked as docs requested")}><Send />Mark docs requested</button>
            )}
            {editable && (
              <button
                className="btn btn-primary"
                disabled={pending || !structureReady || !allCollected || insight.openTasks > 0}
                title={!structureReady ? "Complete ownership and account details first" : !allCollected ? "Collect every checklist item first" : insight.openTasks ? "Resolve open tasks first" : undefined}
                onClick={() => void run(() => changeStatus(record, "READY_FOR_COMPLIANCE", "Submitted for compliance review"), "Submitted to Compliance").then((result) => { if (result) router.push(`${base}/review`); })}
              ><ShieldCheck />Submit for compliance</button>
            )}
          </div>
          <div className="case-meta">
            <span>{ENTITY_LABELS[record.entityType]}</span>
            <span><MapPin />{record.jurisdiction || "Jurisdiction not set"}</span>
            <span><Hash />{record.registrationNumber || "No registration number"}</span>
            <span><UserRound />{userName(session.db, record.ownerId)}</span>
            <span><Calendar />Due {formatDate(record.dueDate)}</span>
            <span className="mono">v{record.version} · rules {record.ruleVersion}</span>
          </div>
          <nav className="stepper" aria-label="Case steps">
            {STAGES.map((stage, index) => {
              const href = stage.segment ? `${base}/${stage.segment}` : base;
              const active = stage.segment ? pathname.startsWith(href) : pathname === base;
              const state = stepState(index);
              return (
                <Link key={stage.id} href={href} className={`step step-${state}${active ? " active" : ""}`}>
                  <span className="step-num">{state === "done" ? <Check /> : index + 1}</span>
                  {stage.label}
                  {state === "locked" && <Lock className="step-lock" />}
                </Link>
              );
            })}
          </nav>
        </div>
        {children}
      </div>
      {assistant.open && <AssistantDrawer initialPrompt={assistant.prompt} onClose={() => setAssistant({ open: false })} />}
    </CaseContext.Provider>
  );
}
