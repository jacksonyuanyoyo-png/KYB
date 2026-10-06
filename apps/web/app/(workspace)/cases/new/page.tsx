"use client";

import { ArrowLeft, Check, ChevronRight, FileSearch, Keyboard, LoaderCircle, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ENTITY_TYPES, type EntityType } from "@fcc/domain";
import { EntityIcon, useAction } from "@/components/ui";
import { apiClassify } from "@/lib/data/api";
import { isApiMode } from "@/lib/data/source";
import { suggestEntityType, type EntityTypeSuggestion } from "@/lib/ai/mock";
import { createCase } from "@/lib/data/actions";
import { useSession } from "@/lib/data/hooks";
import { ENTITY_DESCRIPTIONS, ENTITY_LABELS, ROLE_LABELS } from "@/lib/labels";

export default function NewCasePage() {
  const { db, user } = useSession()!;
  const router = useRouter();
  const { run, pending } = useAction();
  const [start, setStart] = useState<"manual" | "documents">("documents");
  const [legalName, setLegalName] = useState("");
  const [notes, setNotes] = useState("");
  const [entityType, setEntityType] = useState<EntityType | null>(null);
  const [jurisdiction, setJurisdiction] = useState("");
  const [registrationNumber, setRegistrationNumber] = useState("");
  const [ownerId, setOwnerId] = useState(user.role === "ADVISOR" ? user.id : "u-advisor");
  const [suggestion, setSuggestion] = useState<EntityTypeSuggestion | null | "none">(null);
  const [suggesting, setSuggesting] = useState(false);
  const advisors = db.users.filter((item) => item.role === "ADVISOR" || item.role === "OPERATIONS");

  async function askAi() {
    setSuggesting(true);
    if (isApiMode()) {
      try {
        const reply = await apiClassify(legalName, notes);
        setSuggestion(reply.type ? { type: reply.type, confidence: reply.confidence ?? Number.NaN, reasons: reply.reasons } : "none");
      } catch {
        setSuggestion("none");
      }
      setSuggesting(false);
      return;
    }
    setSuggestion((await suggestEntityType(legalName, notes)) ?? "none");
    setSuggesting(false);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!entityType) return;
    const aiEntityType = suggestion && suggestion !== "none" ? { suggested: suggestion.type, accepted: suggestion.type === entityType } : undefined;
    const created = await run(() => createCase({ legalName, entityType, jurisdiction, registrationNumber, ownerId, aiEntityType }), "Case created");
    if (created) router.push(`/cases/${created.id}${start === "documents" ? "?parse=1" : ""}`);
  }

  const canSubmit = legalName.trim().length > 1 && entityType && !pending;

  return (
    <div className="page"><form className="page-narrow" style={{ maxWidth: 1040 }} onSubmit={submit}>
      <div className="breadcrumb"><Link href="/cases">Cases</Link><ChevronRight /><span>New case</span></div>
      <div className="page-header">
        <div><h1>Open a complex entity case</h1><p>Identify the entity that is opening the account. You will map who owns and controls it in the next step.</p></div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 320px", gap: 16, alignItems: "start" }}>
        <div className="stack" style={{ gap: 16 }}>
          <section className="card">
            <div className="card-header"><div className="section-title"><span className="section-num">1</span><h3>How do you want to start?</h3></div></div>
            <div className="card-body choice-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <button type="button" className={`choice${start === "documents" ? " selected" : ""}`} onClick={() => setStart("documents")}>
                <span className="choice-icon"><FileSearch /></span>
                <span><strong>From formation documents</strong><small>Upload articles, shareholder register or trust deed. AI drafts the ownership graph for you to confirm.</small></span>
                {start === "documents" && <span className="choice-check"><Check /></span>}
              </button>
              <button type="button" className={`choice${start === "manual" ? " selected" : ""}`} onClick={() => setStart("manual")}>
                <span className="choice-icon"><Keyboard /></span>
                <span><strong>Enter manually</strong><small>Build the ownership graph yourself, node by node.</small></span>
                {start === "manual" && <span className="choice-check"><Check /></span>}
              </button>
            </div>
          </section>

          <section className="card">
            <div className="card-header"><div className="section-title"><span className="section-num">2</span><h3>Entity</h3></div></div>
            <div className="card-body stack" style={{ gap: 14 }}>
              <label className="field"><span>Full legal name</span><input className="input" required autoFocus value={legalName} onChange={(event) => setLegalName(event.target.value)} placeholder="As it appears on the formation document" /></label>
              <label className="field"><span>What does the client tell us about the entity? <span className="muted" style={{ fontWeight: 400 }}>(optional, helps AI)</span></span><textarea className="textarea" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="e.g. Family holding company that owns rental property, two directors…" /></label>
              <div className="grid-2">
                <label className="field"><span>Jurisdiction of formation</span><input className="input" value={jurisdiction} onChange={(event) => setJurisdiction(event.target.value)} placeholder="e.g. Ontario (OBCA)" /></label>
                <label className="field"><span>Registration / business number</span><input className="input" value={registrationNumber} onChange={(event) => setRegistrationNumber(event.target.value)} placeholder="e.g. BN 81920 4471" /></label>
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <div className="section-title"><span className="section-num">3</span><div><h3>Manual Account Opening subtype</h3><p className="muted small">Drives which documents the rule engine requires.</p></div></div>
              <button type="button" className="btn btn-ai btn-sm" disabled={!legalName.trim() || suggesting} onClick={() => void askAi()}>{suggesting ? <LoaderCircle className="spin" /> : <Sparkles />}Suggest with AI</button>
            </div>
            <div className="card-body">
              {suggestion === "none" && <div className="callout tone-neutral" style={{ marginBottom: 12 }}><Sparkles /><span>Not enough signal in the name. Add a short description of the entity, or choose a subtype below.</span></div>}
              {suggestion && suggestion !== "none" && (
                <div className="ai-note" style={{ marginBottom: 12, alignItems: "center" }}>
                  <Sparkles />
                  <div style={{ flex: 1 }}>
                    <strong>Suggested: {ENTITY_LABELS[suggestion.type]}</strong>{Number.isFinite(suggestion.confidence) ? <span className="muted"> · {Math.round(suggestion.confidence * 100)}% confidence</span> : null}
                    <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>{suggestion.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                  </div>
                  {entityType !== suggestion.type ? <button type="button" className="btn btn-sm btn-secondary" onClick={() => setEntityType(suggestion.type)}>Use this</button> : <span className="badge tone-green"><Check />Selected</span>}
                </div>
              )}
              <div className="choice-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                {ENTITY_TYPES.map((type) => (
                  <button type="button" key={type} className={`choice${entityType === type ? " selected" : ""}`} onClick={() => setEntityType(type)}>
                    <EntityIcon type={type} className="choice-icon" />
                    <span><strong>{ENTITY_LABELS[type]}</strong><small>{ENTITY_DESCRIPTIONS[type]}</small></span>
                    {entityType === type && <span className="choice-check"><Check /></span>}
                  </button>
                ))}
              </div>
            </div>
          </section>
        </div>

        <aside className="stack" style={{ position: "sticky", top: 0, gap: 16 }}>
          <section className="card">
            <div className="card-header"><h3>Assignment</h3></div>
            <div className="card-body stack">
              <label className="field"><span>Case owner</span>
                <select className="select" value={ownerId} onChange={(event) => setOwnerId(event.target.value)}>
                  {advisors.map((item) => <option key={item.id} value={item.id}>{item.name} · {ROLE_LABELS[item.role]}</option>)}
                </select>
              </label>
              <div className="kv">
                <dt>Initial status</dt><dd>Building</dd>
                <dt>Target</dt><dd>Ready for compliance in 10 days</dd>
              </div>
            </div>
            <div className="card-footer" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <button className="btn btn-primary" disabled={!canSubmit}>{pending ? <LoaderCircle className="spin" /> : null}Create case &amp; continue</button>
              <Link href="/cases" className="btn btn-ghost"><ArrowLeft />Cancel</Link>
            </div>
          </section>
          <div className="ai-note"><Sparkles /><span>AI suggestions are recorded in the audit trail with the model version. You make the final choice; the rule engine decides which documents are required.</span></div>
        </aside>
      </div>
    </form></div>
  );
}
