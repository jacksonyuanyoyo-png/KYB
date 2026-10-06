"use client";

import { History, Sparkles } from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { AuditIcon, AUDIT_LABELS } from "@/components/case/AuditIcon";
import { Avatar, EmptyState } from "@/components/ui";
import { apiAudit } from "@/lib/data/api";
import { useSession, userName } from "@/lib/data/hooks";
import { isApiMode } from "@/lib/data/source";
import { formatDateTime } from "@/lib/format";
import { aiDecisionPhrase } from "@/lib/labels";
import type { AuditAction } from "@/lib/types";

export default function AuditPage() {
  const { db } = useSession()!;
  const [action, setAction] = useState<AuditAction | "">("");
  const [caseId, setCaseId] = useState("");
  const [actorId, setActorId] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [remote, setRemote] = useState<typeof db.audit | null>(null);
  useEffect(() => {
    if (!isApiMode()) return;
    void apiAudit({ action, caseId, actorId }).then((page) => setRemote(page.items)).catch(() => setRemote([]));
  }, [action, caseId, actorId]);
  const events = isApiMode() ? (remote ?? []) : db.audit.filter((event) => (!action || event.action === action) && (!caseId || event.caseId === caseId) && (!actorId || event.actorId === actorId));
  const caseName = (id: string) => db.cases.find((item) => item.id === id);

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Audit log</h1><p>Append-only record of every change: who, when, the case version it produced, field-level before and after, and for AI suggestions the model, decision and rule version.</p></div>
      </div>
      <section className="card">
        <div className="toolbar">
          <select className="select input-sm" style={{ width: 200 }} value={action} onChange={(event) => setAction(event.target.value as AuditAction | "")}><option value="">All actions</option>{(Object.keys(AUDIT_LABELS) as AuditAction[]).map((key) => <option key={key} value={key}>{AUDIT_LABELS[key]}</option>)}</select>
          <select className="select input-sm" style={{ width: 280 }} value={caseId} onChange={(event) => setCaseId(event.target.value)}><option value="">All cases</option>{db.cases.map((item) => <option key={item.id} value={item.id}>{item.reference} · {item.legalName}</option>)}</select>
          <select className="select input-sm" style={{ width: 200 }} value={actorId} onChange={(event) => setActorId(event.target.value)}><option value="">All users</option>{db.users.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}</select>
          <span className="spacer" />
          <span className="small muted">{events.length} events</span>
        </div>
        {events.length === 0 ? <EmptyState icon={<History />} title="No events match" /> : (
          <table className="table">
            <thead><tr><th>When</th><th>Action</th><th>Summary</th><th>Case</th><th>User</th><th>Version</th></tr></thead>
            <tbody>
              {events.map((event) => {
                const record = caseName(event.caseId);
                const hasDetail = Boolean(event.changes?.length || event.ai);
                return (
                  <Fragment key={event.id}>
                    <tr className={hasDetail ? "clickable" : undefined} onClick={() => hasDetail && setExpanded(expanded === event.id ? null : event.id)}>
                      <td className="small" style={{ whiteSpace: "nowrap" }}>{formatDateTime(event.at)}</td>
                      <td><div className="row"><AuditIcon action={event.action} /><span className="small" style={{ fontWeight: 600 }}>{AUDIT_LABELS[event.action]}</span></div></td>
                      <td style={{ maxWidth: 380 }}>{event.summary}{event.ai && <div className="small" style={{ color: "var(--violet)" }}><Sparkles size={11} /> {aiDecisionPhrase(event.ai, "audit")} · {event.ai.model}</div>}</td>
                      <td>{record ? <Link href={`/cases/${record.id}/review`} onClick={(clickEvent) => clickEvent.stopPropagation()} className="small" style={{ color: "var(--primary)", fontWeight: 600 }}>{record.reference}</Link> : "—"}</td>
                      <td><div className="row"><Avatar name={userName(db, event.actorId)} size="sm" /><span className="small">{userName(db, event.actorId)}</span></div></td>
                      <td className="mono">v{event.version}</td>
                    </tr>
                    {expanded === event.id && (
                      <tr><td colSpan={6} style={{ background: "var(--surface-2)" }}>
                        {event.ai && <div className="small" style={{ marginBottom: 8 }}>Model <span className="mono">{event.ai.model}</span> · decision <strong>{aiDecisionPhrase(event.ai, "audit")}</strong> · rule version <span className="mono">{event.ai.ruleVersion}</span></div>}
                        {event.changes && <div className="diff" style={{ background: "#fff" }}>{event.changes.map((change) => <div key={change.field} className="diff-row"><div className="muted">{change.field}</div><div className="from">{change.from}</div><div className="to">{change.to}</div></div>)}</div>}
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
