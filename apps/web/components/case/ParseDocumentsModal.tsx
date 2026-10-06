"use client";

import { Building2, CircleCheck, Eye, FileText, LoaderCircle, Sparkles, UserRound, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { Party } from "@fcc/domain";
import { DocumentCompare, splitCitation, type ExtractRow, type StoredFile } from "@/components/case/DocumentCompare";
import { Dropzone, Modal } from "@/components/ui";
import { AI_MODELS, extractFormationParties } from "@/lib/ai/mock";
import { uploadDocuments } from "@/lib/data/actions";
import { apiExtractFormation } from "@/lib/data/api";
import { isApiMode } from "@/lib/data/source";
import { formatBytes, uid } from "@/lib/format";
import type { CaseRecord } from "@/lib/types";

const SAMPLE_FILES: StoredFile[] = [
  { name: "Articles_of_Incorporation.pdf", size: 1_840_000, type: "application/pdf" },
  { name: "Shareholder_Register_2026.pdf", size: 264_000, type: "application/pdf" },
];

const STEPS = ["Reading pages and tables", "Finding owners, directors and signers", "Matching against the current graph"];

export function ParseDocumentsModal({ record, onClose, onApply, onReject, pending }: {
  record: CaseRecord;
  onClose: () => void;
  onApply: (parties: Party[], files: StoredFile[], summary: string) => void;
  onReject: (files: StoredFile[]) => void;
  pending: boolean;
}) {
  const [files, setFiles] = useState<StoredFile[]>([]);
  const [phase, setPhase] = useState<"upload" | "processing" | "review">("upload");
  const [step, setStep] = useState(0);
  const [rows, setRows] = useState<ExtractRow[]>([]);
  const [compare, setCompare] = useState<{ fileName: string; rowId: string | null } | null>(null);
  const entities = record.parties.filter((party) => party.kind === "ENTITY");
  const [parentId, setParentId] = useState(entities.find((party) => party.parentId === null)?.id ?? "");
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);

  useEffect(() => {
    if (phase !== "processing") return;
    let cancelled = false;
    const waitMs = totalBytes >= 1_500_000 ? 1100 : 650;
    void (async () => {
      for (let index = 0; index < STEPS.length; index += 1) {
        if (cancelled) return;
        setStep(index);
        await new Promise((resolve) => setTimeout(resolve, waitMs));
      }
      if (isApiMode()) {
        try {
          const saved = await uploadDocuments(record, null, files);
          const ids = saved.documents.slice(-files.length).map((item) => item.id);
          const extracted = await apiExtractFormation(record.id, ids);
          if (cancelled) return;
          setRows(extracted.entities.map((item) => ({
            tempId: String(item.tempId),
            kind: item.kind as Party["kind"],
            legalName: String(item.legalName ?? ""),
            entityType: item.entityType as Party["entityType"],
            title: String(item.title ?? ""),
            ownershipPercent: Number(item.ownershipPercent ?? 0),
            isController: Boolean(item.isController),
            isSigningAuthority: Boolean(item.isSigningAuthority),
            country: String(item.country ?? "Canada"),
            confidence: Number(item.confidence ?? 0),
            citation: String(item.citation ?? ""),
            include: Boolean(item.includeByDefault),
          })));
          setPhase("review");
        } catch (error) {
          if (!cancelled) {
            setPhase("upload");
            setRows([]);
            window.alert(error instanceof Error ? error.message : "Document reading failed.");
          }
        }
        return;
      }
      const extracted = await extractFormationParties(record, files.map((file) => file.name));
      if (cancelled) return;
      setRows(extracted.map((item) => ({ ...item, include: item.confidence >= 0.75 })));
      setPhase("review");
    })();
    return () => { cancelled = true; };
  }, [phase]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (tempId: string, patch: Partial<ExtractRow>) => setRows((current) => current.map((row) => (row.tempId === tempId ? { ...row, ...patch } : row)));
  const included = rows.filter((row) => row.include);
  const parentName = entities.find((party) => party.id === parentId)?.legalName ?? record.legalName;
  const compareFile = files.find((file) => file.name === compare?.fileName);
  const compareRows = compareFile ? rows.filter((row) => splitCitation(row.citation, files).fileName === compareFile.name) : [];

  function openCompare(fileName: string, rowId: string | null) {
    setCompare({ fileName, rowId });
  }

  function apply() {
    const parties: Party[] = included.map((row) => ({
      id: uid("p"), parentId, kind: row.kind, legalName: row.legalName.trim(), entityType: row.entityType, title: row.title || undefined,
      country: row.country, usTaxClass: row.kind === "ENTITY" ? "unsure" : undefined, ownershipPercent: row.ownershipPercent, isController: row.isController, isSigningAuthority: row.isSigningAuthority, isUsPerson: false, isPepHio: false,
    }));
    onApply(parties, files, `Accepted ${parties.length} of ${rows.length} parties extracted from ${files.map((file) => file.name).join(", ")}`);
  }

  const footer = phase === "review" ? (
    <>
      <span className="small muted" style={{ marginRight: "auto" }}>Nothing is written until you confirm. Model {AI_MODELS.extract}.</span>
      <button className="btn btn-secondary" disabled={pending} onClick={() => onReject(files)}>Discard suggestions</button>
      <button className="btn btn-primary" disabled={!included.length || pending} onClick={apply}>Add {included.length} to graph</button>
    </>
  ) : phase === "upload" ? (
    <>
      <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary" disabled={!files.length} onClick={() => setPhase("processing")}><Sparkles />Extract ownership</button>
    </>
  ) : undefined;

  return (
    <Modal size={compare ? "xl" : "lg"} title="Parse formation documents" description="AI reads articles, shareholder registers, trust deeds or partnership agreements and drafts owners and controllers for you to confirm." onClose={onClose} footer={footer}>
      {phase === "upload" && (
        <div className="stack">
          <Dropzone onFiles={(added) => setFiles((current) => [...current, ...added.map((file) => ({ name: file.name, size: file.size, type: file.type, blob: file }))])} label="Drop formation documents here" />
          <div className="row-between">
            <span className="small muted">{files.length ? `${files.length} file${files.length === 1 ? "" : "s"} selected` : "No files yet."}</span>
            {!files.length && <button className="btn btn-ghost btn-sm" onClick={() => setFiles(SAMPLE_FILES)}><FileText />Use sample documents</button>}
          </div>
          {files.map((file) => (
            <div key={file.name} className="row-between" style={{ padding: "8px 12px", border: "1px solid var(--border)", borderRadius: 10 }}>
              <span className="row"><FileText size={16} color="var(--red)" /><span>{file.name}</span><span className="muted small">{formatBytes(file.size)}</span></span>
              <button className="btn btn-ghost btn-icon btn-sm" aria-label={`Remove ${file.name}`} onClick={() => setFiles((current) => current.filter((item) => item !== file))}><X /></button>
            </div>
          ))}
          <div className="ai-note"><Sparkles /><span>Uploaded files are also attached to the case as the formation document. AI does not decide beneficial ownership; it only drafts entries for you to confirm.</span></div>
        </div>
      )}

      {phase === "processing" && (
        <div style={{ padding: "24px 8px", display: "grid", gridTemplateColumns: "56px 1fr", gap: 18, alignItems: "start" }}>
          <div className="assistant-mark" style={{ width: 52, height: 52, borderRadius: 14 }}><Sparkles size={22} /></div>
          <div>
            <h3 style={{ fontSize: 15, marginBottom: 4 }}>Analysing {files.length} document{files.length === 1 ? "" : "s"} · {formatBytes(totalBytes)}</h3>
            {totalBytes >= 1_500_000 && <p className="small muted" style={{ marginBottom: 12 }}>Reading continues on this step until the file is done.</p>}
            <div className="extract-steps">
              {STEPS.map((label, index) => (
                <div key={label} className={`extract-step${index < step ? " done" : index === step ? " active" : ""}`}>
                  {index < step ? <CircleCheck /> : index === step ? <LoaderCircle className="spin" /> : <CircleCheck style={{ opacity: 0.3 }} />}{label}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {phase === "review" && compareFile && (
        <DocumentCompare
          file={compareFile}
          rows={compareRows}
          activeId={compare?.rowId ?? null}
          onBack={() => setCompare(null)}
          onSelect={(rowId) => setCompare({ fileName: compareFile.name, rowId })}
          onUpdate={update}
        />
      )}

      {phase === "review" && !compareFile && (
        <div className="stack">
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {files.map((file) => (
              <button key={file.name} type="button" className="file-chip" title={`Preview ${file.name}`} onClick={() => openCompare(file.name, rows.find((row) => splitCitation(row.citation, files).fileName === file.name)?.tempId ?? null)}>
                <FileText /><span>{file.name}</span>
              </button>
            ))}
          </div>
          <div className="row" style={{ gap: 10 }}>
            <span className="small text-2">Attach under</span>
            <select className="select input-sm" style={{ width: 300 }} value={parentId} onChange={(event) => setParentId(event.target.value)}>
              {entities.map((party) => <option key={party.id} value={party.id}>{party.legalName}</option>)}
            </select>
            <span className="spacer" />
            <span className="small muted">Total of selected: {included.reduce((sum, row) => sum + row.ownershipPercent, 0)}%</span>
          </div>
          <div className="card" style={{ overflow: "hidden" }}>
            <table className="table">
              <thead><tr><th style={{ width: 36 }} /><th>Party</th><th>Role</th><th style={{ width: 90 }}>Own %</th><th>Confidence</th><th>Source</th></tr></thead>
              <tbody>
                {rows.map((row) => {
                  const citation = splitCitation(row.citation, files);
                  return (
                    <tr key={row.tempId} style={{ opacity: row.include ? 1 : 0.55 }}>
                      <td><input type="checkbox" checked={row.include} onChange={(event) => update(row.tempId, { include: event.target.checked })} aria-label={`Include ${row.legalName}`} style={{ accentColor: "var(--primary)" }} /></td>
                      <td>
                        <div className="row">
                          {row.kind === "PERSON" ? <UserRound size={15} color="var(--text-3)" /> : <Building2 size={15} color="#15803d" />}
                          <input className="input input-sm" value={row.legalName} onChange={(event) => update(row.tempId, { legalName: event.target.value })} />
                        </div>
                      </td>
                      <td><input className="input input-sm" value={row.title} onChange={(event) => update(row.tempId, { title: event.target.value })} /></td>
                      <td><input className="input input-sm" type="number" min={0} max={100} value={row.ownershipPercent} onChange={(event) => update(row.tempId, { ownershipPercent: Number(event.target.value) })} /></td>
                      <td><span className={`confidence${row.confidence < 0.8 ? " low" : ""}`}><i><b style={{ width: `${row.confidence * 100}%` }} /></i>{Math.round(row.confidence * 100)}%</span></td>
                      <td>
                        <button type="button" className="source-btn" title={row.citation} onClick={() => openCompare(citation.fileName, row.tempId)}>
                          <Eye /><span>{citation.place || "Preview source"}</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="small muted">Preview a source to compare the file with the read. Low-confidence rows are unchecked by default. Parties will be added under <strong>{parentName}</strong>.</p>
        </div>
      )}
    </Modal>
  );
}
