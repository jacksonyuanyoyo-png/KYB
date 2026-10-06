"use client";

import { ArrowLeft, FileText } from "lucide-react";
import { useEffect, useState } from "react";
import type { ExtractedParty } from "@/lib/ai/mock";
import { formatBytes } from "@/lib/format";

export interface StoredFile {
  name: string;
  size: number;
  type: string;
  blob?: File;
}

export type ExtractRow = ExtractedParty & { include: boolean };

export function splitCitation(citation: string, files: Array<{ name: string }>) {
  const ordered = [...files].sort((a, b) => b.name.length - a.name.length);
  const match = ordered.find((file) => citation.startsWith(file.name));
  const fileName = match?.name ?? citation.split(" · ")[0] ?? citation;
  const place = match
    ? citation.slice(fileName.length).replace(/^\s*·\s*/, "")
    : citation.split(" · ").slice(1).join(" · ");
  const pageMatch = place.match(/\bp\.(\d+)/);
  return { fileName, place, page: pageMatch ? Number(pageMatch[1]) : undefined };
}

function useBlobUrl(blob?: File) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) {
      setUrl(null);
      return;
    }
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [blob]);
  return url;
}

function isImage(file: StoredFile) {
  return /^image\//.test(file.type) || /\.(png|jpe?g|gif|webp)$/i.test(file.name);
}

function isPdf(file: StoredFile) {
  return file.type === "application/pdf" || /\.pdf$/i.test(file.name);
}

export function DocumentCompare({ file, rows, activeId, onBack, onSelect, onUpdate }: {
  file: StoredFile;
  rows: ExtractRow[];
  activeId: string | null;
  onBack: () => void;
  onSelect: (tempId: string) => void;
  onUpdate: (tempId: string, patch: Partial<ExtractRow>) => void;
}) {
  const active = rows.find((row) => row.tempId === activeId) ?? rows[0];
  const place = active ? splitCitation(active.citation, [file]).place : "";
  const page = active ? splitCitation(active.citation, [file]).page : undefined;
  const url = useBlobUrl(file.blob);
  const others = rows.filter((row) => row.tempId !== active?.tempId);

  return (
    <div className="stack">
      <div className="row-between">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onBack}><ArrowLeft />Back to extracted parties</button>
        <span className="small muted">{file.name} · {formatBytes(file.size)}</span>
      </div>
      <div className="doc-compare">
        <div className="doc-stage">
          {url && isPdf(file) && <iframe title={file.name} src={`${url}#page=${page ?? 1}`} />}
          {url && isImage(file) && <img src={url} alt={file.name} />}
          {(!url || (!isPdf(file) && !isImage(file))) && (
            <div className="doc-sheet-wrap">
              <article className="doc-sheet">
                <div className="small muted" style={{ letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>Source preview</div>
                <h3>{file.name}</h3>
                <p className="small muted" style={{ marginBottom: 16 }}>{url ? "This file type is shown as the text that was read." : "These lines are the text the model read from the file."}</p>
                {rows.map((row) => {
                  const citation = splitCitation(row.citation, [file]);
                  return (
                    <button key={row.tempId} type="button" className={`doc-hit${row.tempId === active?.tempId ? " active" : ""}`} onClick={() => onSelect(row.tempId)}>
                      <span>{citation.place || "Cited line"}</span>
                      <strong>{row.legalName}</strong>
                      <span>{row.ownershipPercent ? `${row.ownershipPercent}%` : row.title}</span>
                    </button>
                  );
                })}
                {rows.length === 0 && <p className="small muted">Nothing was read from this file.</p>}
              </article>
            </div>
          )}
        </div>
        <div className="doc-read">
          {active ? (
            <div className="stack">
              <div>
                <div className="small muted" style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>OCR read · {place || "cited passage"}</div>
                <p className="ocr-quote">{active.legalName} — {active.title || "Role not stated"}{active.ownershipPercent ? ` — ${active.ownershipPercent}%` : ""}</p>
              </div>
              <label className="row" style={{ gap: 8 }}>
                <input type="checkbox" checked={active.include} onChange={(event) => onUpdate(active.tempId, { include: event.target.checked })} aria-label={`Include ${active.legalName}`} style={{ accentColor: "var(--primary)" }} />
                <span className="small">Include in the graph</span>
              </label>
              <label className="stack" style={{ gap: 4 }}>
                <span className="small muted">Party</span>
                <input className="input input-sm" value={active.legalName} onChange={(event) => onUpdate(active.tempId, { legalName: event.target.value })} />
              </label>
              <div className="row">
                <label className="stack" style={{ gap: 4, flex: 1 }}>
                  <span className="small muted">Role</span>
                  <input className="input input-sm" value={active.title} onChange={(event) => onUpdate(active.tempId, { title: event.target.value })} />
                </label>
                <label className="stack" style={{ gap: 4, width: 88 }}>
                  <span className="small muted">Own %</span>
                  <input className="input input-sm" type="number" min={0} max={100} value={active.ownershipPercent} onChange={(event) => onUpdate(active.tempId, { ownershipPercent: Number(event.target.value) })} />
                </label>
              </div>
              <div className="small muted">Confidence {Math.round(active.confidence * 100)}%. Correct the fields if the page says something else.</div>
              {others.length > 0 && (
                <div>
                  <div className="small muted" style={{ marginBottom: 4 }}>Other reads in this file</div>
                  {others.map((row) => (
                    <button key={row.tempId} type="button" className="doc-switch" onClick={() => onSelect(row.tempId)}>
                      <span className="row"><FileText size={14} color="var(--red)" />{row.legalName}</span>
                      <small>{splitCitation(row.citation, [file]).place}</small>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : <p className="small muted">Nothing was read from this file.</p>}
        </div>
      </div>
    </div>
  );
}
