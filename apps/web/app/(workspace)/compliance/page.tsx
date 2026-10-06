"use client";

import { ArrowRight, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { CaseStatus } from "@fcc/domain";
import { Avatar, EmptyState, EntityIcon, StatusBadge, Tag } from "@/components/ui";
import { caseInsight, useSession, userName } from "@/lib/data/hooks";
import { daysUntil, timeAgo } from "@/lib/format";
import { apiComplianceQueue } from "@/lib/data/api";
import { isApiMode } from "@/lib/data/source";
import { ENTITY_LABELS, ROLE_LABELS } from "@/lib/labels";

const TABS: Array<{ id: CaseStatus; label: string; hint: string }> = [
  { id: "READY_FOR_COMPLIANCE", label: "Awaiting review", hint: "Submitted by advisors, all checklist items collected" },
  { id: "RETURNED", label: "Returned", hint: "Sent back with comments; waiting on the advisor" },
  { id: "APPROVED", label: "Approved", hint: "Pre-review complete" },
];

export default function CompliancePage() {
  const { db, user } = useSession()!;
  const [tab, setTab] = useState<CaseStatus>("READY_FOR_COMPLIANCE");
  const [waiting, setWaiting] = useState<Record<string, string>>({});
  const rows = useMemo(() => db.cases.map((record) => ({ record, insight: caseInsight(record, db.ruleLibrary) })), [db.cases, db.ruleLibrary]);
  const visible = rows.filter(({ record }) => record.status === tab).sort((a, b) => new Date(waiting[a.record.id] || a.record.updatedAt).getTime() - new Date(waiting[b.record.id] || b.record.updatedAt).getTime());
  const submittedAt = (caseId: string) => waiting[caseId] || db.audit.find((event) => event.caseId === caseId && event.action === "STATUS_CHANGED" && event.summary.startsWith("Submitted"))?.at;

  useEffect(() => {
    if (!isApiMode()) return;
    void apiComplianceQueue(tab).then((page) => {
      const next: Record<string, string> = {};
      for (const item of page.items) next[String(item.caseId)] = String(item.waitingSince ?? "");
      setWaiting(next);
    }).catch(() => setWaiting({}));
  }, [tab]);

  return (
    <div className="page"><div className="page-narrow">
      <div className="page-header">
        <div><h1>Compliance review</h1><p>Pre-review queue: confirm the ownership trace, persons to identify and documents before the client signs. Oldest submissions first.</p></div>
        {user.role !== "COMPLIANCE" && <Tag tone="amber">Viewing as {ROLE_LABELS[user.role]} — switch to Compliance to decide</Tag>}
      </div>
      <div className="grid-3" style={{ marginBottom: 16 }}>
        {TABS.map((item) => (
          <button key={item.id} className={`card kpi choice${tab === item.id ? " selected" : ""}`} style={{ display: "block", borderRadius: 18 }} onClick={() => setTab(item.id)}>
            <div className="kpi-label">{item.label}</div>
            <div className="kpi-value">{rows.filter(({ record }) => record.status === item.id).length}</div>
            <div className="kpi-foot">{item.hint}</div>
          </button>
        ))}
      </div>
      <section className="card">
        {visible.length === 0 ? <EmptyState icon={<ShieldCheck />} title="Queue is empty">{`No cases in “${TABS.find((item) => item.id === tab)?.label ?? "this view"}”.`}</EmptyState> : (
          <table className="table">
            <thead><tr><th>Case</th><th>Status</th><th>To identify</th><th>Documents</th><th>Open tasks</th><th>Advisor</th><th>Waiting</th><th /></tr></thead>
            <tbody>
              {visible.map(({ record, insight }) => {
                const since = submittedAt(record.id) ?? record.updatedAt;
                const pep = record.parties.some((party) => party.isPepHio);
                return (
                  <tr key={record.id}>
                    <td><div className="row" style={{ gap: 12 }}><EntityIcon type={record.entityType} /><div><div className="cell-title">{record.legalName}</div><div className="cell-sub">{record.reference} · {ENTITY_LABELS[record.entityType]}</div></div></div></td>
                    <td><StatusBadge status={record.status} /></td>
                    <td><span className="row">{insight.identify.length}{pep && <Tag tone="red">PEP</Tag>}</span></td>
                    <td>{insight.collected}/{insight.checklist.length}</td>
                    <td>{insight.openTasks || "—"}</td>
                    <td><div className="row"><Avatar name={userName(db, record.ownerId)} size="sm" /><span className="small">{userName(db, record.ownerId)}</span></div></td>
                    <td className="small">{timeAgo(since)}{daysUntil(record.dueDate) < 0 && record.status !== "APPROVED" && <div className="due overdue">past due</div>}</td>
                    <td style={{ textAlign: "right" }}><Link href={`/cases/${record.id}/review`} className={`btn btn-sm ${tab === "READY_FOR_COMPLIANCE" ? "btn-primary" : "btn-secondary"}`}>{tab === "READY_FOR_COMPLIANCE" ? "Review" : "Open"}<ArrowRight /></Link></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div></div>
  );
}
