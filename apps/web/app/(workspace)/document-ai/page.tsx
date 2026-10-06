"use client";

import { FileSearch, FileText, LoaderCircle, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Badge, EmptyState, Tag } from "@/components/ui";
import { apiDocuments } from "@/lib/data/api";
import { useSession, userName } from "@/lib/data/hooks";
import { isApiMode } from "@/lib/data/source";
import { formatBytes, timeAgo } from "@/lib/format";
import { checklistFor } from "@/lib/insights";

const CAPABILITIES = [
  { title: "Formation document parsing", body: "Extracts owners, percentages, directors and signers from articles, registers, trust deeds and LP agreements into a draft ownership graph.", where: "Ownership step" },
  { title: "Entity subtype suggestion", body: "Suggests the Manual Account Opening subtype from the legal name and description, with reasons.", where: "New case" },
  { title: "Conversational data entry", body: "Turns a sentence like “Alice holds 60% and is a director” into a proposed node for confirmation.", where: "Case assistant" },
  { title: "Gap explanation", body: "Explains in business language why a step is blocked and which node to fix.", where: "Ownership inspector" },
  { title: "Checklist pre-review", body: "Flags missing pages, name mismatches and signers not on the graph before the client signs.", where: "Documents step" },
  { title: "Compliance comments to tasks", body: "Each Compliance comment becomes a task linked to the node or document it concerns.", where: "Review step" },
];

export default function DocumentAiPage() {
  const { db } = useSession()!;
  const [filter, setFilter] = useState<"ALL" | "PROCESSING" | "UNASSIGNED">("ALL");
  const [remote, setRemote] = useState<Array<{ document: { id: string; fileName: string; sizeBytes: number; extraction: string; uploadedBy: string; uploadedAt: string }; caseId: string; caseReference: string; caseLegalName: string; requirementName?: string }> | null>(null);
  const rows = useMemo(() => db.cases.flatMap((record) => {
    const names = new Map(checklistFor(record, db.ruleLibrary).map((item) => [item.id, item.name]));
    return record.documents.map((document) => ({ document, record, requirement: document.requirementId ? names.get(document.requirementId) : undefined }));
  }).sort((a, b) => new Date(b.document.uploadedAt).getTime() - new Date(a.document.uploadedAt).getTime()), [db.cases, db.ruleLibrary]);
  useEffect(() => {
    if (!isApiMode()) return;
    void apiDocuments(filter).then((page) => setRemote(page.items as typeof remote)).catch(() => setRemote([]));
  }, [filter]);
  const visible = isApiMode()
    ? (remote ?? []).map((item) => ({ document: item.document, record: { id: item.caseId, legalName: item.caseLegalName, reference: item.caseReference }, requirement: item.requirementName }))
    : rows.filter(({ document }) => filter === "ALL" || (filter === "PROCESSING" ? document.extraction === "PROCESSING" : !document.requirementId));

  return (
    <div className="page"><div className="page-narrow">
      <div className="page-header">
        <div><h1>Document AI</h1><p>Every file uploaded across cases, with extraction status. AI reads and suggests; staff confirm; the rule engine decides what is required.</p></div>
      </div>
      <div className="grid-3" style={{ marginBottom: 16 }}>
        {CAPABILITIES.map((item) => (
          <div key={item.title} className="card" style={{ padding: 16 }}>
            <div className="row" style={{ marginBottom: 6 }}><Sparkles size={15} color="var(--violet)" /><strong>{item.title}</strong></div>
            <p className="small text-2">{item.body}</p>
            <div style={{ marginTop: 10 }}><Tag tone="violet">{item.where}</Tag></div>
          </div>
        ))}
      </div>
      <section className="card">
        <div className="toolbar">
          <div className="filter-chips">
            {(["ALL", "PROCESSING", "UNASSIGNED"] as const).map((item) => (
              <button key={item} className={`filter-chip${filter === item ? " active" : ""}`} onClick={() => setFilter(item)}>
                {item === "ALL" ? "All files" : item === "PROCESSING" ? "Processing" : "Not linked to checklist"}
                <span>{isApiMode() ? (filter === item ? (remote ?? []).length : "") : rows.filter(({ document }) => item === "ALL" || (item === "PROCESSING" ? document.extraction === "PROCESSING" : !document.requirementId)).length}</span>
              </button>
            ))}
          </div>
        </div>
        {visible.length === 0 ? <EmptyState icon={<FileSearch />} title="No files" /> : (
          <table className="table">
            <thead><tr><th>File</th><th>Case</th><th>Checklist item</th><th>Extraction</th><th>Uploaded</th></tr></thead>
            <tbody>
              {visible.map(({ document, record, requirement }) => (
                <tr key={document.id}>
                  <td><div className="row"><FileText size={18} color="var(--red)" /><div><div className="cell-title">{document.fileName}</div><div className="cell-sub">{formatBytes(document.sizeBytes)}</div></div></div></td>
                  <td><Link href={`/cases/${record.id}/documents`} className="cell-title" style={{ color: "var(--primary)" }}>{record.legalName}</Link><div className="cell-sub">{record.reference}</div></td>
                  <td className="small">{requirement ?? <Tag tone="amber">Unassigned</Tag>}</td>
                  <td>{document.extraction === "PROCESSING" ? <Badge tone="violet"><LoaderCircle className="spin" />Reading</Badge> : document.extraction === "EXTRACTED" ? <Badge tone="green">Extracted</Badge> : document.extraction === "FAILED" ? <Badge tone="red">Failed</Badge> : <Badge>Not processed</Badge>}</td>
                  <td className="small">{userName(db, document.uploadedBy)}<div className="cell-sub">{timeAgo(document.uploadedAt)}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div></div>
  );
}
