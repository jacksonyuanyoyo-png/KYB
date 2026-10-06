"use client";

import { Check, CircleCheck, Circle, Plus, ShieldCheck, Sparkles, Trash2, Undo2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { AuditIcon, AUDIT_LABELS } from "@/components/case/AuditIcon";
import { useCaseContext } from "@/components/case/CaseContext";
import { Avatar, Badge, EmptyState, Modal, StatusBadge, Tag, useAction } from "@/components/ui";
import { complianceDecision, toggleTask } from "@/lib/data/actions";
import { isApiMode } from "@/lib/data/source";
import { userName } from "@/lib/data/hooks";
import { formatDateTime, timeAgo } from "@/lib/format";
import { aiDecisionPhrase } from "@/lib/labels";
import type { ReviewTask } from "@/lib/types";

type Comment = Pick<ReviewTask, "title" | "partyId" | "requirementId">;

export default function ReviewPage() {
  const { record, insight, user, db } = useCaseContext();
  const { run, pending } = useAction();
  const [decision, setDecision] = useState<"APPROVE" | "RETURN" | null>(null);
  const events = db.audit.filter((event) => event.caseId === record.id);
  const canDecide = user.role === "COMPLIANCE" && record.status === "READY_FOR_COMPLIANCE";
  const byParty = new Map(record.parties.map((party) => [party.id, party]));
  const byRequirement = new Map(insight.checklist.map((item) => [item.id, item]));
  const tasksEditable = record.status !== "APPROVED" && !(isApiMode() && record.status === "READY_FOR_COMPLIANCE" && user.role === "ADVISOR");

  const readiness = [
    { label: "Ownership traced to natural persons", done: insight.ownershipIssues.length === 0 },
    { label: "Account details complete", done: insight.detailsGaps.length === 0 },
    { label: `Checklist collected (${insight.collected}/${insight.checklist.length})`, done: insight.collected === insight.checklist.length },
    { label: "Follow-up tasks resolved", done: insight.openTasks === 0 },
  ];

  return (
    <div className="workspace">
      <div className="workspace-main"><div style={{ maxWidth: 860 }}>
        {canDecide && (
          <div className="decision-bar">
            <ShieldCheck size={20} color="var(--primary)" />
            <div style={{ flex: 1 }}><strong>Awaiting your pre-review decision</strong><div className="small text-2">Submitted by {userName(db, record.ownerId)} · {insight.identify.length} persons to identify · {insight.checklist.length} documents</div></div>
            <button className="btn btn-secondary" onClick={() => setDecision("RETURN")}><Undo2 />Return with comments</button>
            <button className="btn btn-success" onClick={() => setDecision("APPROVE")}><Check />Approve</button>
          </div>
        )}
        {record.status === "READY_FOR_COMPLIANCE" && user.role !== "COMPLIANCE" && (
          <div className="callout tone-blue" style={{ marginBottom: 16 }}><ShieldCheck /><span><strong>With Compliance</strong>The case is read-only while Compliance reviews it. Switch to the Compliance demo user to record a decision.</span></div>
        )}

        <section className="card">
          <div className="card-header"><div><h2>Follow-up tasks</h2><p>Compliance comments and AI findings, each linked to the node or document it concerns</p></div><Tag tone={insight.openTasks ? "amber" : "green"}>{insight.openTasks} open</Tag></div>
          <div className="card-body">
            {record.tasks.length === 0 && <p className="small muted">No tasks on this case.</p>}
            {record.tasks.map((task) => {
              const party = task.partyId ? byParty.get(task.partyId) : undefined;
              const requirement = task.requirementId ? byRequirement.get(task.requirementId) : undefined;
              return (
                <div key={task.id} className={`task${task.done ? " done" : ""}`}>
                  <button className="task-check" disabled={!tasksEditable || pending} onClick={() => void run(() => toggleTask(record, task.id))} aria-label={task.done ? "Reopen task" : "Complete task"}>{task.done && <Check />}</button>
                  <div style={{ flex: 1 }}>
                    <div className="task-title">{task.title}</div>
                    <div className="row small muted" style={{ marginTop: 4, flexWrap: "wrap" }}>
                      <Tag tone={task.source === "COMPLIANCE" ? "blue" : task.source === "AI" ? "violet" : "neutral"}>{task.source === "AI" ? "AI finding" : task.source.toLowerCase()}</Tag>
                      <span>{userName(db, task.createdBy)} · {timeAgo(task.createdAt)}</span>
                      {party && <Link href={`/cases/${record.id}?node=${party.id}`} style={{ color: "var(--primary)" }}>→ {party.legalName}</Link>}
                      {requirement && <Link href={`/cases/${record.id}/documents`} style={{ color: "var(--primary)" }}>→ {requirement.name}</Link>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="card" style={{ marginTop: 16 }}>
          <div className="card-header"><div><h2>Case history</h2><p>Every save increments the version and records who changed what</p></div><span className="mono muted">v{record.version}</span></div>
          <div className="card-body">
            {events.length === 0 && <EmptyState icon={<ShieldCheck />} title="No history yet" />}
            <div className="timeline">
              {events.map((event) => (
                <div key={event.id} className="tl-item">
                  <AuditIcon action={event.action} className="tl-icon" />
                  <div>
                    <div className="tl-head">
                      <Avatar name={userName(db, event.actorId)} size="sm" />
                      <strong>{userName(db, event.actorId)}</strong>
                      <Badge tone="neutral">{AUDIT_LABELS[event.action]}</Badge>
                      <span className="muted small">{formatDateTime(event.at)} · v{event.version}</span>
                    </div>
                    <div className="tl-summary">{event.summary}</div>
                    {event.ai && (
                      <div className="row small" style={{ marginTop: 6, color: "var(--violet)" }}>
                        <Sparkles size={13} />{aiDecisionPhrase(event.ai, "review")} · model <span className="mono">{event.ai.model}</span> · rules <span className="mono">{event.ai.ruleVersion}</span>
                      </div>
                    )}
                    {event.changes && event.changes.length > 0 && (
                      <div className="diff">
                        {event.changes.map((change) => (
                          <div key={change.field} className="diff-row"><div className="muted">{change.field}</div><div className="from">{change.from}</div><div className="to">{change.to}</div></div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div></div>

      <aside className="inspector">
        <div className="inspector-section">
          <h3>Readiness</h3>
          <div className="stack" style={{ gap: 8 }}>
            {readiness.map((item) => <div key={item.label} className="row" style={{ color: item.done ? "var(--text)" : "var(--text-3)" }}>{item.done ? <CircleCheck size={16} color="var(--green)" /> : <Circle size={16} />}<span>{item.label}</span></div>)}
          </div>
        </div>
        <div className="inspector-section">
          <h3>Case record</h3>
          <dl className="kv">
            <dt>Status</dt><dd><StatusBadge status={record.status} /></dd>
            <dt>Reference</dt><dd className="mono">{record.reference}</dd>
            <dt>Version</dt><dd className="mono">v{record.version}</dd>
            <dt>Rule version</dt><dd className="mono">{record.ruleVersion}</dd>
            <dt>Owner</dt><dd>{userName(db, record.ownerId)}</dd>
            <dt>Created</dt><dd>{formatDateTime(record.createdAt)}</dd>
            <dt>Last saved</dt><dd>{timeAgo(record.updatedAt)}</dd>
          </dl>
          <p className="small muted" style={{ marginTop: 10 }}>The checklist is pinned to the rule version above; publishing new rules does not rewrite this case.</p>
        </div>
        <div className="inspector-section">
          <h3>Persons identified</h3>
          {insight.identify.map((party) => (
            <Link key={party.id} href={`/cases/${record.id}?node=${party.id}`} className="person-row">
              <Avatar name={party.legalName} size="sm" /><span style={{ flex: 1 }}><strong>{party.legalName}</strong><small>{party.title}</small></span>{party.isPepHio && <Tag tone="red">PEP</Tag>}
            </Link>
          ))}
        </div>
      </aside>

      {decision && (
        <DecisionModal
          decision={decision} pending={pending}
          parties={record.parties.filter((party) => party.parentId).map((party) => ({ id: party.id, name: party.legalName }))}
          requirements={insight.checklist.map((item) => ({ id: item.id, name: item.name }))}
          onClose={() => setDecision(null)}
          onSubmit={(comments) => void run(() => complianceDecision(record, decision, comments), decision === "APPROVE" ? "Case approved" : "Returned to advisor").then((result) => { if (result) setDecision(null); })}
        />
      )}
    </div>
  );
}

function DecisionModal({ decision, parties, requirements, onClose, onSubmit, pending }: {
  decision: "APPROVE" | "RETURN"; parties: Array<{ id: string; name: string }>; requirements: Array<{ id: string; name: string }>;
  onClose: () => void; onSubmit: (comments: Comment[]) => void; pending: boolean;
}) {
  const [comments, setComments] = useState<Comment[]>(decision === "RETURN" ? [{ title: "" }] : []);
  const update = (index: number, patch: Partial<Comment>) => setComments((current) => current.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  const valid = comments.filter((item) => item.title.trim());
  return (
    <Modal
      size={decision === "RETURN" ? "lg" : undefined}
      title={decision === "APPROVE" ? "Approve this case?" : "Return to advisor"}
      description={decision === "APPROVE" ? "Received documents are marked verified and the case is closed for editing." : "Each comment becomes a follow-up task linked to the node or document it concerns."}
      onClose={onClose}
      footer={<>
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        {decision === "APPROVE"
          ? <button className="btn btn-success" disabled={pending} onClick={() => onSubmit([])}><Check />Approve case</button>
          : <button className="btn btn-primary" disabled={pending || !valid.length} onClick={() => onSubmit(valid.map((item) => ({ ...item, title: item.title.trim() })))}><Undo2 />Return with {valid.length} comment{valid.length === 1 ? "" : "s"}</button>}
      </>}
    >
      {decision === "APPROVE" ? <p className="text-2">This records your decision in the audit trail with the current rule version.</p> : (
        <div className="stack">
          {comments.map((comment, index) => (
            <div key={index} className="card" style={{ padding: 12 }}>
              <div className="row" style={{ alignItems: "flex-start" }}>
                <textarea className="textarea" style={{ minHeight: 60 }} placeholder="What needs to change?" value={comment.title} onChange={(event) => update(index, { title: event.target.value })} />
                <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setComments((current) => current.filter((_, i) => i !== index))} aria-label="Remove comment"><Trash2 /></button>
              </div>
              <div className="grid-2" style={{ marginTop: 8, gap: 8 }}>
                <select className="select input-sm" value={comment.partyId ?? ""} onChange={(event) => update(index, { partyId: event.target.value || undefined })}>
                  <option value="">Link to a party (optional)</option>{parties.map((party) => <option key={party.id} value={party.id}>{party.name}</option>)}
                </select>
                <select className="select input-sm" value={comment.requirementId ?? ""} onChange={(event) => update(index, { requirementId: event.target.value || undefined })}>
                  <option value="">Link to a document (optional)</option>{requirements.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </div>
            </div>
          ))}
          <button className="btn btn-secondary btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setComments((current) => [...current, { title: "" }])}><Plus />Add comment</button>
        </div>
      )}
    </Modal>
  );
}
