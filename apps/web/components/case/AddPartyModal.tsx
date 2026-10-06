"use client";

import { Building2, UserRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ENTITY_TYPES, US_TAX_CLASSES, type EntityType, type Party, type UsTaxClass } from "@fcc/domain";
import { Modal } from "@/components/ui";
import { uid } from "@/lib/format";
import { ENTITY_LABELS, PARTY_TITLES, RESIDENCE_COUNTRIES, US_TAX_LABELS } from "@/lib/labels";

export function AddPartyModal({ parent, siblingsTotal, onClose, onAdd, pending }: {
  parent: Party; siblingsTotal: number; onClose: () => void; onAdd: (party: Party) => void; pending: boolean;
}) {
  const [kind, setKind] = useState<Party["kind"]>("PERSON");
  const [legalName, setLegalName] = useState("");
  const [title, setTitle] = useState("");
  const [country, setCountry] = useState("Canada");
  const [usTaxClass, setUsTaxClass] = useState<UsTaxClass>("unsure");
  const [entityType, setEntityType] = useState<EntityType>("corporation");
  const [ownership, setOwnership] = useState(String(Math.max(0, 100 - siblingsTotal)));
  const [flags, setFlags] = useState({ isController: false, isSigningAuthority: false, isUsPerson: false, isPepHio: false });

  function submit(event: FormEvent) {
    event.preventDefault();
    onAdd({
      id: uid("p"), parentId: parent.id, kind, legalName: legalName.trim(), title: title.trim() || undefined, country: country.trim() || undefined,
      entityType: kind === "ENTITY" ? entityType : undefined, usTaxClass: kind === "ENTITY" ? usTaxClass : undefined, ownershipPercent: Number(ownership) || 0,
      ...flags, isPepHio: kind === "PERSON" && flags.isPepHio,
    });
  }

  const flag = (key: keyof typeof flags, label: string, hint: string) => (
    <label className="check" style={{ alignItems: "flex-start" }}>
      <input type="checkbox" checked={flags[key]} onChange={(event) => setFlags({ ...flags, [key]: event.target.checked })} style={{ marginTop: 2 }} />
      <span><strong style={{ color: "var(--text)", fontWeight: 600 }}>{label}</strong><br /><small className="muted">{hint}</small></span>
    </label>
  );

  return (
    <Modal
      title={`Add owner or controller`}
      description={`Under ${parent.legalName}. Disclosed so far: ${siblingsTotal}%.`}
      onClose={onClose}
      footer={<><button className="btn btn-secondary" onClick={onClose}>Cancel</button><button className="btn btn-primary" form="add-party" disabled={!legalName.trim() || pending}>Add to graph</button></>}
    >
      <form id="add-party" className="stack" style={{ gap: 14 }} onSubmit={submit}>
        <div className="segmented" style={{ alignSelf: "flex-start" }}>
          <button type="button" className={kind === "PERSON" ? "active" : ""} onClick={() => setKind("PERSON")}><UserRound />Natural person</button>
          <button type="button" className={kind === "ENTITY" ? "active" : ""} onClick={() => setKind("ENTITY")}><Building2 />Another entity</button>
        </div>
        <label className="field"><span>Full legal name</span><input className="input" autoFocus required value={legalName} onChange={(event) => setLegalName(event.target.value)} /></label>
        {kind === "ENTITY" && (
          <label className="field"><span>Entity type</span>
            <select className="select" value={entityType} onChange={(event) => setEntityType(event.target.value as EntityType)}>{ENTITY_TYPES.map((type) => <option key={type} value={type}>{ENTITY_LABELS[type]}</option>)}</select>
            <small>You will need to continue tracing this entity down to natural persons.</small>
          </label>
        )}
        {kind === "ENTITY" && (
          <div className="field"><span>US tax classification</span>
            <div className="choice-grid">
              {US_TAX_CLASSES.map((value) => (
                <button type="button" key={value} className={`choice${usTaxClass === value ? " selected" : ""}`} onClick={() => setUsTaxClass(value)}>
                  <span><strong>{US_TAX_LABELS[value].label}</strong><small>{US_TAX_LABELS[value].hint}</small></span>
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="grid-3">
          <label className="field"><span>Role</span><input className="input" list="party-titles" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. Director" /></label>
          <label className="field"><span>Ownership %</span><input className="input" type="number" min={0} max={100} step="0.01" value={ownership} onChange={(event) => setOwnership(event.target.value)} /></label>
          <label className="field"><span>{kind === "PERSON" ? "Country of residence" : "Country"}</span>
            <select className="select" value={country} onChange={(event) => setCountry(event.target.value)}>{RESIDENCE_COUNTRIES.map((item) => <option key={item}>{item}</option>)}</select>
          </label>
        </div>
        <datalist id="party-titles">{PARTY_TITLES.map((item) => <option key={item} value={item} />)}</datalist>
        <div className="grid-2" style={{ gap: 12 }}>
          {flag("isController", "Controller", "Can direct decisions (e.g. sole director, trustee)")}
          {flag("isSigningAuthority", "Signing authority", "Can sign on behalf of the entity")}
          {flag("isUsPerson", "US person / US entity", "Triggers W-9 requirements")}
          {kind === "PERSON" && flag("isPepHio", "PEP / HIO", "Flag only; screening comes from the approved system")}
        </div>
      </form>
    </Modal>
  );
}
