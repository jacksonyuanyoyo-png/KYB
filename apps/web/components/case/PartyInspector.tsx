"use client";

import { ArrowRight, CircleCheck, Plus, Sparkles, Trash2, TriangleAlert, UserRound, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ENTITY_TYPES, US_TAX_CLASSES, type EntityType, type Party, type UsTaxClass } from "@fcc/domain";
import { useCaseContext } from "@/components/case/CaseContext";
import { Avatar, ENTITY_VISUALS, Switch, Tag } from "@/components/ui";
import { explainGaps } from "@/lib/ai/mock";
import { isApiMode } from "@/lib/data/source";
import { formatPercent } from "@/lib/format";
import { ENTITY_GUIDANCE, ENTITY_LABELS, NFFE_GUIDANCE, PARTY_TITLES, RESIDENCE_COUNTRIES, US_TAX_LABELS } from "@/lib/labels";

export function StructureOverview({ onSelect }: { onSelect: (id: string) => void }) {
  const { record, insight, openAssistant } = useCaseContext();
  const gaps = (isApiMode()
    ? [...insight.ownershipIssues.map((issue) => ({ partyId: issue.partyId, title: issue.message, detail: issue.message, action: "Select this node and correct the structure." })), ...insight.detailsGaps.map((gap) => ({ partyId: undefined, title: gap.message, detail: gap.message, action: "Open account details." }))]
    : explainGaps(record, insight)
  ).filter((gap) => gap.partyId);
  const people = record.parties.filter((party) => party.kind === "PERSON");
  const entities = record.parties.filter((party) => party.kind === "ENTITY" && party.parentId);
  const peps = record.parties.filter((party) => party.isPepHio);

  return (
    <>
      <div className="inspector-section">
        <h3>Structure check</h3>
        {gaps.length === 0 ? (
          <>
            <div className="ok-state"><CircleCheck /><span>Every entity is traced to natural persons and disclosed interests total 100%.</span></div>
            <Link href={`/cases/${record.id}/details`} className="btn btn-primary" style={{ width: "100%", marginTop: 12 }}>Continue to account details<ArrowRight /></Link>
          </>
        ) : (
          <>
            {gaps.map((gap) => (
              <button key={`${gap.partyId}-${gap.title}`} className="issue" onClick={() => gap.partyId && onSelect(gap.partyId)}>
                <TriangleAlert />
                <span><strong>{gap.title}</strong><small>{gap.detail}</small><em>{gap.action}</em></span>
              </button>
            ))}
            <button className="btn btn-ai btn-sm" style={{ width: "100%", marginTop: 10 }} onClick={() => openAssistant("Why can't I continue?")}><Sparkles />Explain what's missing</button>
          </>
        )}
      </div>
      <div className="inspector-section">
        <h3>Persons to identify <Tag tone="blue">{insight.identify.length}</Tag></h3>
        <p className="small muted" style={{ marginBottom: 8 }}>FINTRAC: anyone with ≥25% effective ownership, plus anyone who controls or signs, regardless of percentage.</p>
        {insight.identify.length === 0 && <p className="small muted">No natural persons meet the threshold yet.</p>}
        {insight.identify.map((party) => (
          <button key={party.id} className="person-row" onClick={() => onSelect(party.id)}>
            <Avatar name={party.legalName} size="sm" />
            <span style={{ flex: 1, minWidth: 0 }}>
              <strong>{party.legalName}</strong>
              <small>{[(insight.effective.get(party.id) ?? 0) >= 25 ? `${formatPercent(insight.effective.get(party.id) ?? 0)} effective` : null, party.isController ? "controller" : null, party.isSigningAuthority ? "signer" : null].filter(Boolean).join(" · ")}</small>
            </span>
            {party.isPepHio && <Tag tone="red">PEP</Tag>}
          </button>
        ))}
      </div>
      <div className="inspector-section">
        <h3>What to add · {ENTITY_LABELS[record.entityType]}</h3>
        <p className="small text-2">{ENTITY_GUIDANCE[record.entityType]}</p>
        <p className="small muted" style={{ marginTop: 8 }}>{NFFE_GUIDANCE}</p>
      </div>
      <div className="inspector-section">
        <h3>Summary</h3>
        <dl className="kv">
          <dt>Natural persons</dt><dd>{people.length}</dd>
          <dt>Intermediate entities</dt><dd>{entities.length}</dd>
          <dt>PEP / HIO flags</dt><dd>{peps.length ? <span style={{ color: "var(--red)" }}>{peps.map((party) => party.legalName).join(", ")}</span> : "None"}</dd>
          <dt>US persons</dt><dd>{record.parties.filter((party) => party.isUsPerson).map((party) => party.legalName).join(", ") || "None"}</dd>
        </dl>
      </div>
    </>
  );
}

export function PartyEditor({ party, editable, pending, onClose, onSave, onRemove, onAdd, onSelect }: {
  party: Party; editable: boolean; pending: boolean;
  onClose: () => void; onSave: (next: Party) => void; onRemove: () => void; onAdd: () => void; onSelect: (id: string) => void;
}) {
  const { record, insight } = useCaseContext();
  const [draft, setDraft] = useState(party);
  const [confirmRemove, setConfirmRemove] = useState(false);
  useEffect(() => { setDraft(party); setConfirmRemove(false); }, [party]);

  const isRoot = party.parentId === null;
  const parent = record.parties.find((item) => item.id === party.parentId);
  const children = record.parties.filter((item) => item.parentId === party.id);
  const childTotal = children.reduce((sum, item) => sum + item.ownershipPercent, 0);
  const issues = insight.ownershipIssues.filter((issue) => issue.partyId === party.id);
  const effective = insight.effective.get(party.id) ?? 0;
  const mustIdentify = insight.identify.some((item) => item.id === party.id);
  const dirty = JSON.stringify(draft) !== JSON.stringify(party);
  const visual = party.entityType ? ENTITY_VISUALS[party.entityType] : null;
  const Icon = visual?.icon ?? UserRound;
  const set = <K extends keyof Party>(key: K, value: Party[K]) => setDraft((current) => ({ ...current, [key]: value }));

  const flag = (key: "isController" | "isSigningAuthority" | "isUsPerson" | "isPepHio", label: string, hint: string) => (
    <div className="row-between" style={{ padding: "6px 0" }}>
      <div><div style={{ fontWeight: 600, fontSize: 12.5 }}>{label}</div><div className="small muted">{hint}</div></div>
      <Switch label={label} checked={draft[key]} onChange={(value) => editable && set(key, value)} />
    </div>
  );

  return (
    <>
      <div className="inspector-section">
        <div className="inspector-head">
          <span className="gnode-icon" style={{ width: 38, height: 38, color: visual?.color ?? "#475569", background: visual?.bg ?? "#eef1f6", borderRadius: 10, display: "grid", placeItems: "center" }}><Icon size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2>{party.legalName}</h2>
            <div className="small muted">{isRoot ? "Account holder (root entity)" : party.kind === "PERSON" ? "Natural person" : "Intermediate entity"}{parent ? ` · under ${parent.legalName}` : ""}</div>
          </div>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={onClose} aria-label="Close"><X /></button>
        </div>
        <div className="flag-row" style={{ marginTop: 12 }}>
          {mustIdentify && <Tag tone="blue">Must identify</Tag>}
          {!isRoot && <Tag tone="neutral">Direct {formatPercent(party.ownershipPercent)}</Tag>}
          {!isRoot && <Tag tone="neutral">Effective {formatPercent(effective)}</Tag>}
          {party.isPepHio && <Tag tone="red">PEP / HIO</Tag>}
        </div>
        {issues.map((issue) => <div key={issue.code} className="issue" style={{ marginTop: 12 }}><TriangleAlert /><span><strong>{issue.message}</strong></span></div>)}
      </div>

      <div className="inspector-section stack" style={{ gap: 12 }}>
        <h3 style={{ marginBottom: 0 }}>Details</h3>
        <label className="field"><span>Legal name</span><input className="input" disabled={!editable || isRoot} value={draft.legalName} onChange={(event) => set("legalName", event.target.value)} /></label>
        {party.kind === "ENTITY" && (
          <label className="field"><span>Entity type</span>
            <select className="select" disabled={!editable || isRoot} value={draft.entityType ?? ""} onChange={(event) => set("entityType", (event.target.value || undefined) as EntityType | undefined)}>
              <option value="">Select…</option>
              {ENTITY_TYPES.map((type) => <option key={type} value={type}>{ENTITY_LABELS[type]}</option>)}
            </select>
          </label>
        )}
        {!isRoot && (
          <div className="grid-2" style={{ gap: 10 }}>
            <label className="field"><span>Ownership %</span><input className="input" type="number" min={0} max={100} step="0.01" disabled={!editable} value={draft.ownershipPercent} onChange={(event) => set("ownershipPercent", Number(event.target.value))} /></label>
            <label className="field"><span>Role</span><input className="input" list="inspector-titles" disabled={!editable} value={draft.title ?? ""} onChange={(event) => set("title", event.target.value || undefined)} /></label>
          </div>
        )}
        <datalist id="inspector-titles">{PARTY_TITLES.map((item) => <option key={item} value={item} />)}</datalist>
        {!isRoot && (
          <label className="field"><span>{party.kind === "PERSON" ? "Country of residence" : "Country"}</span>
            <select className="select" disabled={!editable} value={draft.country ?? ""} onChange={(event) => set("country", event.target.value)}>
              <option value="">Select…</option>
              {draft.country && !RESIDENCE_COUNTRIES.includes(draft.country as (typeof RESIDENCE_COUNTRIES)[number]) && <option>{draft.country}</option>}
              {RESIDENCE_COUNTRIES.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
        )}
        {!isRoot && party.kind === "ENTITY" && (
          <label className="field"><span>US tax classification</span>
            <select className="select" disabled={!editable} value={draft.usTaxClass ?? "unsure"} onChange={(event) => set("usTaxClass", event.target.value as UsTaxClass)}>
              {US_TAX_CLASSES.map((value) => <option key={value} value={value}>{US_TAX_LABELS[value].label}</option>)}
            </select>
            <small>{US_TAX_LABELS[draft.usTaxClass ?? "unsure"].hint}</small>
          </label>
        )}
        {!isRoot && (
          <div>
            {flag("isController", "Controller", "Directs decisions")}
            {flag("isSigningAuthority", "Signing authority", "Signs for the entity")}
            {flag("isUsPerson", party.kind === "PERSON" ? "US person" : "US entity", "Triggers W-9")}
            {party.kind === "PERSON" && flag("isPepHio", "PEP / HIO", "Flag only — screening result required")}
          </div>
        )}
        {isRoot && <p className="small text-2">{ENTITY_GUIDANCE[record.entityType]}</p>}
        {editable && !isRoot && (
          <div className="row">
            <button className="btn btn-primary" disabled={!dirty || pending || !draft.legalName.trim()} onClick={() => onSave(draft)}>Save changes</button>
            {dirty && <button className="btn btn-ghost" onClick={() => setDraft(party)}>Discard</button>}
          </div>
        )}
      </div>

      {party.kind === "ENTITY" && (
        <div className="inspector-section">
          <h3>Owners &amp; controllers <span className={`badge ${children.length && Math.abs(childTotal - 100) > 0.01 && childTotal > 0 ? "tone-amber" : "tone-neutral"}`}>{formatPercent(childTotal)} disclosed</span></h3>
          {children.length === 0 && <p className="small muted" style={{ marginBottom: 10 }}>Nobody attached yet. Add the shareholders, partners, trustees or directors.</p>}
          {children.map((child) => (
            <button key={child.id} className="person-row" onClick={() => onSelect(child.id)}>
              {child.kind === "PERSON" ? <Avatar name={child.legalName} size="sm" /> : <span className="avatar avatar-sm" style={{ background: child.entityType ? ENTITY_VISUALS[child.entityType].color : "#64748b" }}>E</span>}
              <span style={{ flex: 1, minWidth: 0 }}><strong>{child.legalName}</strong><small>{child.title ?? (child.kind === "ENTITY" ? "Entity" : "Person")}</small></span>
              <span className="small" style={{ fontWeight: 600 }}>{formatPercent(child.ownershipPercent)}</span>
            </button>
          ))}
          {editable && <button className="btn btn-secondary btn-sm" style={{ width: "100%", marginTop: 10 }} onClick={onAdd}><Plus />Add owner or controller</button>}
        </div>
      )}

      {editable && !isRoot && (
        <div className="inspector-section">
          {!confirmRemove ? (
            <button className="btn btn-danger btn-sm" onClick={() => setConfirmRemove(true)}><Trash2 />Remove from structure</button>
          ) : (
            <div className="callout tone-red" style={{ flexDirection: "column" }}>
              <strong>Remove {party.legalName}{children.length ? ` and ${children.length} owner${children.length === 1 ? "" : "s"} below it` : ""}?</strong>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn btn-danger btn-sm" disabled={pending} onClick={onRemove}>Remove</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setConfirmRemove(false)}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
