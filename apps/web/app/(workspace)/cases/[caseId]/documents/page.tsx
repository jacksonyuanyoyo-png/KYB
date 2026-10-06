"use client";

import { Check, ChevronDown, ChevronUp, FileText, ListPlus, LoaderCircle, Lock, Paperclip, Printer, Sparkles, Upload, X } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { useCaseContext } from "@/components/case/CaseContext";
import { Avatar, Badge, Dropzone, Progress, Tag, useAction } from "@/components/ui";
import { preReview, type ReviewFinding } from "@/lib/ai/mock";
import { apiContentUrl, apiPreReview } from "@/lib/data/api";
import { isApiMode } from "@/lib/data/source";
import { addCustomRequirement, addTasks, assignDocument, setChecklistStatus, uploadDocuments } from "@/lib/data/actions";
import { userName } from "@/lib/data/hooks";
import { formatBytes, timeAgo } from "@/lib/format";
import { isCollected, type ChecklistRow } from "@/lib/insights";
import { DOC_STATUS_LABELS, DOC_STATUS_TONES } from "@/lib/labels";
import type { DocStatus } from "@/lib/types";

export default function DocumentsPage() {
  const { record, insight, user, db } = useCaseContext();
  const { run, pending } = useAction();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [newRequirement, setNewRequirement] = useState("");
  const [addingRequirement, setAddingRequirement] = useState(false);
  const [findings, setFindings] = useState<ReviewFinding[] | null>(null);
  const [reviewing, setReviewing] = useState(false);

  const editable = record.status !== "APPROVED" && (record.status !== "READY_FOR_COMPLIANCE" || user.role === "COMPLIANCE");

  if (insight.ownershipIssues.length || insight.detailsGaps.length) {
    const toOwnership = insight.ownershipIssues.length > 0;
    return (
      <div className="workspace-main"><div className="card locked" style={{ maxWidth: 640, margin: "40px auto" }}>
        <span className="locked-icon"><Lock /></span>
        <h3 style={{ fontSize: 16, color: "var(--text)" }}>Checklist not generated yet</h3>
        <p className="muted" style={{ maxWidth: 440 }}>The rule engine needs a complete ownership structure, province, tax residency and Trusted Contact decision. Next: {(toOwnership ? insight.ownershipIssues[0]?.message : insight.detailsGaps[0]?.message) ?? ""}</p>
        <Link href={`/cases/${record.id}${toOwnership ? "" : "/details"}`} className="btn btn-primary">{toOwnership ? "Back to ownership" : "Complete account details"}</Link>
      </div></div>
    );
  }

  const total = insight.checklist.length;
  const required = insight.checklist.filter((item) => !item.conditional).length;
  const sections = [...new Set(insight.checklist.map((item) => item.section))];
  const unassigned = record.documents.filter((item) => !item.requirementId);
  const byId = new Map(record.parties.map((party) => [party.id, party]));
  const statusOptions: DocStatus[] = user.role === "COMPLIANCE" || user.role === "OPERATIONS" || user.role === "ADMIN" ? ["MISSING", "REQUESTED", "RECEIVED", "VERIFIED", "REJECTED"] : ["MISSING", "REQUESTED", "RECEIVED"];

  async function runPreReview() {
    setReviewing(true);
    if (isApiMode()) {
      try {
        const page = await apiPreReview(record.id);
        setFindings(page.findings.map((item, index) => ({ ...item, id: item.id || `f-${index}` })));
      } catch (error) {
        setFindings([{ id: "error", severity: "high", title: "Pre-review failed", detail: error instanceof Error ? error.message : "The assistant is unavailable." }]);
      }
      setReviewing(false);
      return;
    }
    setFindings(await preReview(record, insight));
    setReviewing(false);
  }

  return (
    <div className="workspace">
      <div className="workspace-main">
        <div className="card" style={{ padding: 18 }}>
          <div className="row-between" style={{ alignItems: "flex-start" }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Document checklist · rules {record.ruleVersion}</div>
              <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em" }}>{insight.collected} of {total} collected</div>
              <div className="small muted">{required} required · {total - required} if applicable · {record.documents.length} files on case</div>
            </div>
            <div className="row no-print">
              <button className="btn btn-secondary btn-sm" onClick={() => window.print()}><Printer />Print</button>
              {editable && <button className="btn btn-secondary btn-sm" onClick={() => setAddingRequirement(true)}><ListPlus />Add requirement</button>}
            </div>
          </div>
          <div style={{ marginTop: 14 }}><Progress value={total ? (insight.collected / total) * 100 : 0} tone={insight.collected === total ? "green" : undefined} /></div>
          {addingRequirement && (
            <form className="row" style={{ marginTop: 14 }} onSubmit={(event) => {
              event.preventDefault();
              if (!newRequirement.trim()) return;
              void run(() => addCustomRequirement(record, newRequirement.trim()), "Requirement added").then(() => { setNewRequirement(""); setAddingRequirement(false); });
            }}>
              <input className="input" autoFocus placeholder="e.g. Certified translation of trust deed" value={newRequirement} onChange={(event) => setNewRequirement(event.target.value)} />
              <button className="btn btn-primary" disabled={!newRequirement.trim() || pending}>Add</button>
              <button type="button" className="btn btn-ghost btn-icon" onClick={() => setAddingRequirement(false)} aria-label="Cancel"><X /></button>
            </form>
          )}
        </div>

        {sections.map((section) => {
          const rows = insight.checklist.filter((item) => item.section === section);
          return (
            <section key={section} className="card check-section">
              <div className="check-section-head"><h3>{section}</h3><span className="small muted">{rows.filter((item) => isCollected(record, item.id)).length}/{rows.length}</span></div>
              {rows.map((item) => (
                <RequirementRow
                  key={item.id} item={item} open={expanded === item.id} onToggle={() => setExpanded(expanded === item.id ? null : item.id)}
                  editable={editable} statusOptions={statusOptions} pending={pending}
                  persons={item.partyIds.map((id) => byId.get(id)).filter((party) => party !== undefined)}
                  onStatus={(status) => void run(() => setChecklistStatus(record, item.id, item.name, status))}
                  onUpload={(files) => void run(() => uploadDocuments(record, item.id, files), `${files.length} file${files.length === 1 ? "" : "s"} uploaded`)}
                />
              ))}
            </section>
          );
        })}
      </div>

      <aside className="inspector no-print">
        <div className="inspector-section">
          <h3>AI pre-review</h3>
          <p className="small muted" style={{ marginBottom: 10 }}>Compares uploaded files with the checklist and the ownership graph before the client signs — the same check Compliance does in pre-review.</p>
          <button className="btn btn-ai" style={{ width: "100%" }} disabled={reviewing || !record.documents.length} onClick={() => void runPreReview()}>{reviewing ? <LoaderCircle className="spin" /> : <Sparkles />}{findings ? "Run again" : "Run pre-review"}</button>
          {findings && (
            <div style={{ marginTop: 12 }}>
              {findings.length === 0 && <div className="ok-state"><Check />No discrepancies found.</div>}
              {findings.map((finding) => (
                <div key={finding.id} className="finding">
                  <span className={`sev ${finding.severity}`} />
                  <div><div style={{ fontWeight: 600, fontSize: 12.5 }}>{finding.title}</div><div className="small text-2">{finding.detail}</div></div>
                </div>
              ))}
              {findings.length > 0 && editable && (
                <button className="btn btn-secondary btn-sm" style={{ width: "100%", marginTop: 8 }} disabled={pending} onClick={() => void run(() => addTasks(record, findings.map((finding) => ({ title: finding.title, requirementId: finding.requirementId, partyId: finding.partyId })), "AI"), "Tasks created").then(() => setFindings(null))}>Create {findings.length} follow-up tasks</button>
              )}
            </div>
          )}
        </div>
        <div className="inspector-section">
          <h3>Unassigned files <Tag tone="neutral">{unassigned.length}</Tag></h3>
          {unassigned.length === 0 && <p className="small muted" style={{ marginBottom: 10 }}>Every file is linked to a checklist item.</p>}
          {unassigned.map((document) => (
            <div key={document.id} style={{ marginBottom: 10 }}>
              <div className="file-chip" style={{ marginBottom: 6 }}><FileText /><span>{document.fileName}</span></div>
              {editable && (
                <select className="select input-sm" defaultValue="" onChange={(event) => {
                  const target = insight.checklist.find((item) => item.id === event.target.value);
                  if (target) void run(() => assignDocument(record, document.id, target.id, target.name), "File linked");
                }}>
                  <option value="" disabled>Link to checklist item…</option>
                  {insight.checklist.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              )}
            </div>
          ))}
          {editable && <Dropzone compact label="Upload unassigned files" onFiles={(files) => void run(() => uploadDocuments(record, null, files), "Uploaded")} />}
        </div>
        <div className="inspector-section">
          <h3>Recent uploads</h3>
          {[...record.documents].reverse().slice(0, 5).map((document) => (
            <div key={document.id} className="person-row" style={{ cursor: "default" }}>
              <FileText size={16} color="var(--red)" />
              <span style={{ flex: 1, minWidth: 0 }}><strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{document.fileName}</strong><small>{userName(db, document.uploadedBy)} · {timeAgo(document.uploadedAt)} · {formatBytes(document.sizeBytes)}</small></span>
              {document.extraction === "PROCESSING" ? <Badge tone="violet"><LoaderCircle className="spin" />Reading</Badge> : null}
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}

function RequirementRow({ item, open, onToggle, editable, statusOptions, persons, onStatus, onUpload, pending }: {
  item: ChecklistRow; open: boolean; onToggle: () => void; editable: boolean; statusOptions: DocStatus[]; pending: boolean;
  persons: Array<{ id: string; legalName: string }>; onStatus: (status: DocStatus) => void; onUpload: (files: File[]) => void;
}) {
  const { record } = useCaseContext();
  const [fileNote, setFileNote] = useState<string | null>(null);
  async function openOriginal(documentId: string) {
    setFileNote(null);
    try {
      window.open(await apiContentUrl(record.id, documentId), "_blank", "noopener");
    } catch (error) {
      setFileNote(error instanceof Error ? error.message : "This file is not available.");
    }
  }
  const input = useRef<HTMLInputElement>(null);
  const state = record.checklist[item.id];
  const status = state?.status ?? "MISSING";
  const files = record.documents.filter((document) => state?.documentIds.includes(document.id));
  const stateClass = status === "RECEIVED" || status === "VERIFIED" ? "done" : status === "REQUESTED" ? "requested" : status === "REJECTED" ? "rejected" : "";

  return (
    <div className={`req${open ? " open" : ""}`}>
      <button
        className={`req-state ${stateClass}`} disabled={!editable || pending}
        title={status === "RECEIVED" || status === "VERIFIED" ? "Mark as missing" : "Mark as received"}
        onClick={() => onStatus(status === "RECEIVED" || status === "VERIFIED" ? "MISSING" : "RECEIVED")}
      >
        {(status === "RECEIVED" || status === "VERIFIED") && <Check />}
        {status === "REJECTED" && <X />}
      </button>
      <div style={{ minWidth: 0 }}>
        <div className="req-name">
          {item.conditional && <Tag tone="amber">If applicable</Tag>}
          {item.custom && <Tag tone="neutral">Additional</Tag>}
          {item.name}
        </div>
        <div className="req-reason">{item.reason}</div>
        {persons.length > 0 && (
          <div className="row" style={{ marginTop: 8, flexWrap: "wrap", gap: 6 }}>
            {persons.map((party) => (
              <Link key={party.id} href={`/cases/${record.id}?node=${party.id}`} className="file-chip" style={{ color: "var(--text)" }}><Avatar name={party.legalName} size="sm" /><span>{party.legalName}</span></Link>
            ))}
          </div>
        )}
        {files.length > 0 && <div className="req-files">{files.map((document) => {
          const stored = isApiMode() && document.storageState !== "METADATA_ONLY";
          if (!stored) return <span key={document.id} className="file-chip" title={isApiMode() ? "The original file is not stored for this document." : document.fileName}><Paperclip /><span>{document.fileName}</span></span>;
          return <button key={document.id} type="button" className="file-chip" title={document.fileName} onClick={() => void openOriginal(document.id)}><Paperclip /><span>{document.fileName}</span></button>;
        })}</div>}
        {fileNote && <p className="small" style={{ color: "var(--red)", marginTop: 6 }}>{fileNote}</p>}
        {open && (
          <dl className="req-why">
            <dt>Rule</dt><dd className="mono">{item.id}</dd>
            <dt>Source</dt><dd>{item.source}</dd>
            <dt>Why</dt><dd>{item.reason}</dd>
            <dt>Rule version</dt><dd className="mono">{record.ruleVersion}</dd>
            {state?.updatedAt && <><dt>Last change</dt><dd>{DOC_STATUS_LABELS[status]} · {timeAgo(state.updatedAt)}</dd></>}
          </dl>
        )}
        <button className="btn btn-ghost btn-sm no-print" style={{ marginTop: 6, marginLeft: -10 }} onClick={onToggle}>{open ? <ChevronUp /> : <ChevronDown />}Why is this required?</button>
      </div>
      <div className="req-actions no-print">
        {!editable && <Badge tone={DOC_STATUS_TONES[status]}>{DOC_STATUS_LABELS[status]}</Badge>}
        {editable && (
          <>
            <select className={`select input-sm status-select tone-${DOC_STATUS_TONES[status]}`} style={{ width: 112 }} value={status} disabled={pending} onChange={(event) => onStatus(event.target.value as DocStatus)} aria-label={`Status for ${item.name}`}>
              {statusOptions.map((option) => <option key={option} value={option}>{DOC_STATUS_LABELS[option]}</option>)}
              {!statusOptions.includes(status) && <option value={status}>{DOC_STATUS_LABELS[status]}</option>}
            </select>
            <button className="btn btn-secondary btn-sm" disabled={pending} onClick={() => input.current?.click()}><Upload />Upload</button>
            <input ref={input} type="file" hidden multiple accept=".pdf,.jpg,.jpeg,.png" onChange={(event) => { if (event.target.files?.length) onUpload([...event.target.files]); event.target.value = ""; }} />
          </>
        )}
      </div>
    </div>
  );
}
