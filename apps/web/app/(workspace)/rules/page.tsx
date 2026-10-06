"use client";

import { FlaskConical, Pencil, Plus, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ACCOUNT_FEATURES, ENTITY_TYPES, RULE_VERSION, generateRequirements, type AccountFeature, type EntityType, type TaxResidency } from "@fcc/domain";
import { Badge, Modal, Tag, useAction } from "@/components/ui";
import { discardRuleDraft, publishRuleDraft, removeLibraryRule, saveBuiltinOverride, saveLibraryRule, setBuiltinRetired, startRuleDraft } from "@/lib/data/actions";
import { apiEvaluateRules } from "@/lib/data/api";
import { isApiMode } from "@/lib/data/source";
import { useSession } from "@/lib/data/hooks";
import { uid } from "@/lib/format";
import { ENTITY_LABELS, FEATURE_LABELS, TAX_LABELS } from "@/lib/labels";
import { libraryRequirements, triggerLabel } from "@/lib/rules/library";
import type { LibraryRule, RuleTriggerKind } from "@/lib/types";

const SECTIONS = ["Entity Formation & Authorization", "Persons to Identify", "Account Features", "IRS / Withholding Tax", "FATCA / CRS"];
const TRIGGERS: Array<{ id: RuleTriggerKind; label: string }> = [
  { id: "ALWAYS", label: "Always" },
  { id: "ENTITY", label: "Entity type" },
  { id: "TAX", label: "Tax residency" },
  { id: "FEATURE", label: "Account feature" },
  { id: "PERSON", label: "Any natural person" },
  { id: "PEP", label: "PEP / HIO flagged" },
  { id: "US_PERSON", label: "US person or US residency" },
  { id: "TRUSTED_CONTACT", label: "Trusted Contact designated" },
];

const BUILTIN: Array<{ id: string; name: string; section: string; trigger: string; source: string; conditional: boolean }> = [
  { id: "naaf", name: "New Account Application Form (NAAF)", section: "Entity Formation & Authorization", trigger: "Always", source: "FCC guide §5.2", conditional: false },
  { id: "formation", name: "Articles / formation or governing document", section: "Entity Formation & Authorization", trigger: "Always", source: "FCC guide §3", conditional: false },
  { id: "resolution", name: "Corporate resolution / signing authority evidence", section: "Entity Formation & Authorization", trigger: "Corporation, Condo, Charity, Association, First Nation", source: "FCC guide §5.2", conditional: false },
  { id: "beneficial-owner", name: "Beneficial Owner Identification", section: "Entity Formation & Authorization", trigger: "Corporation, Partnership, Pooled Fund, Trust, IPP/RCA", source: "CIRO 3203–3204", conditional: false },
  { id: "directors", name: "Director listing", section: "Entity Formation & Authorization", trigger: "Corporation, Charity, Condo", source: "FCC guide", conditional: false },
  { id: "tcp", name: "Trusted Contact Person form", section: "Entity Formation & Authorization", trigger: "Trusted Contact designated", source: "Compliance approval required", conditional: true },
  { id: "identity", name: "Identity verification for signers and controllers", section: "Persons to Identify", trigger: "Any natural person", source: "FINTRAC", conditional: false },
  { id: "pep", name: "PEP / HIO enhanced review", section: "Persons to Identify", trigger: "Any person flagged PEP / HIO", source: "FINTRAC", conditional: true },
  { id: "margin", name: "Margin agreement", section: "Account Features", trigger: "Margin selected", source: "FCC guide §5.2", conditional: true },
  { id: "options", name: "Options agreement and risk disclosure", section: "Account Features", trigger: "Options selected", source: "FCC guide §5.2", conditional: true },
  { id: "cod-dvp", name: "COD / DVP settlement instructions", section: "Account Features", trigger: "COD / DVP selected", source: "FCC guide §5.2", conditional: true },
  { id: "fpl", name: "Fully Paid Lending agreement and risk disclosure", section: "Account Features", trigger: "Fully Paid Lending selected", source: "FCC guide §5.2", conditional: true },
  { id: "w9", name: "W-9", section: "IRS / Withholding Tax", trigger: "US residency or a US person", source: "FCC guide §5.2", conditional: true },
  { id: "w8", name: "W-8BEN-E or applicable treaty statement", section: "IRS / Withholding Tax", trigger: "International or mixed residency", source: "FCC guide §5.2", conditional: true },
  { id: "rc519", name: "RC519 Declaration of Tax Residence", section: "FATCA / CRS", trigger: "US, international, or mixed residency", source: "FCC guide §5.2", conditional: true },
  { id: "nffe", name: "Passive NFFE controlling-person certification", section: "FATCA / CRS", trigger: "International residency", source: "FCC guide", conditional: true },
];

export default function RulesPage() {
  const { db, user } = useSession()!;
  const library = db.ruleLibrary;
  const { run, pending } = useAction();
  const [editing, setEditing] = useState<LibraryRule | "new" | null>(null);
  const [builtinEdit, setBuiltinEdit] = useState<{ id: string; triggerText: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<{ id: string; name: string; builtin: boolean } | null>(null);
  const [entityType, setEntityType] = useState<EntityType>("corporation");
  const [tax, setTax] = useState<TaxResidency>("CANADA");
  const [features, setFeatures] = useState<AccountFeature[]>([]);
  const [trusted, setTrusted] = useState(false);
  const [usPerson, setUsPerson] = useState(false);
  const [pep, setPep] = useState(false);
  const isAdmin = user.role === "ADMIN";
  const isCompliance = user.role === "COMPLIANCE";
  const draft = library.draft;
  const retired = new Set(draft ? draft.disabled : library.publishedDisabled);
  const extras = draft ? draft.extras : library.publishedExtras;
  const overrides = draft?.overrides ?? library.publishedOverrides ?? {};
  const pinned = db.cases.filter((record) => record.ruleVersion === library.publishedVersion).length;

  async function ensureDraft() {
    if (library.draft) return true;
    return Boolean(await run(() => startRuleDraft(), "Draft started"));
  }

  async function editBuiltin(rule: (typeof BUILTIN)[number]) {
    if (!(await ensureDraft())) return;
    setBuiltinEdit({ id: rule.id, triggerText: rule.trigger });
    setEditing({
      id: rule.id,
      name: overrides[rule.id]?.name ?? rule.name,
      section: overrides[rule.id]?.section ?? rule.section,
      conditional: overrides[rule.id]?.conditional ?? rule.conditional,
      source: overrides[rule.id]?.source ?? rule.source,
      reason: overrides[rule.id]?.reason ?? rule.name,
      enabled: true,
      trigger: { kind: "ALWAYS" },
    });
  }

  async function removeConfirmed() {
    if (!confirmDelete) return;
    if (!(await ensureDraft())) return;
    if (confirmDelete.builtin) await run(() => setBuiltinRetired(confirmDelete.id, true), "Rule removed from the draft");
    else await run(() => removeLibraryRule(confirmDelete.id), "Rule deleted");
    setConfirmDelete(null);
  }

  const result = useMemo(() => {
    const account = {
      id: "test", version: 1, legalName: "Test", entityType, status: "BUILDING" as const, ruleVersion: library.publishedVersion, createdAt: "", updatedAt: "",
      parties: [
        { id: "r", parentId: null, kind: "ENTITY" as const, legalName: "Test", entityType, ownershipPercent: 100, isController: false, isSigningAuthority: false, isUsPerson: false, isPepHio: false },
        { id: "p", parentId: "r", kind: "PERSON" as const, legalName: "Person", ownershipPercent: 100, isController: true, isSigningAuthority: true, isUsPerson: usPerson, isPepHio: pep },
      ],
      profile: { province: "ON", taxResidency: tax, features, trustedContact: trusted },
    };
    const disabled = draft && isAdmin ? draft.disabled : library.publishedVersion === RULE_VERSION ? [] : library.publishedDisabled;
    const extraRules = draft && isAdmin ? draft.extras : library.publishedVersion === RULE_VERSION ? [] : library.publishedExtras;
    const overlay = draft && isAdmin ? draft.overrides ?? {} : library.publishedVersion === RULE_VERSION ? {} : library.publishedOverrides ?? {};
    return [
      ...generateRequirements(account).filter((item) => !disabled.includes(item.id)).map((item) => ({ ...item, ...overlay[item.id] })),
      ...libraryRequirements(extraRules, account),
    ];
  }, [entityType, tax, features, trusted, usPerson, pep, draft, isAdmin, library]);
  const [remoteResult, setRemoteResult] = useState<typeof result | null>(null);
  useEffect(() => {
    if (!isApiMode()) return;
    void apiEvaluateRules({
      entityType, taxResidency: tax, features, trustedContact: trusted, usPerson, pep,
      target: draft && isAdmin ? "DRAFT" : "PUBLISHED",
    }).then((page) => setRemoteResult(page.requirements.map((item) => ({ ...item, partyIds: item.partyIds ?? [] })))).catch(() => setRemoteResult([]));
  }, [entityType, tax, features, trusted, usPerson, pep, draft, isAdmin]);
  const shown = isApiMode() && remoteResult ? remoteResult : result;

  return (
    <div className="page"><div className="page-narrow">
      <div className="page-header">
        <div>
          <h1>Rule library</h1>
          <p>Admin drafts a new version. Compliance publishes it. New cases use the published version. Cases already open keep the version they were created with.</p>
        </div>
        <div className="row">
          {isAdmin && !draft && <button className="btn btn-primary" disabled={pending} onClick={() => void run(() => startRuleDraft(), "Draft started")}>Start a draft</button>}
          {isAdmin && draft && <button className="btn btn-secondary" disabled={pending} onClick={() => void run(() => discardRuleDraft(), "Draft discarded")}>Discard draft</button>}
          {isAdmin && draft && <button className="btn btn-primary" onClick={() => setEditing("new")}><Plus />Add rule</button>}
          {(isApiMode() ? isAdmin : isCompliance) && draft && <button className="btn btn-success" disabled={pending} onClick={() => void run(() => publishRuleDraft(), "Rule version published")}>Publish draft</button>}
        </div>
      </div>

      {!isAdmin && !isCompliance && <div className="callout tone-neutral" style={{ marginBottom: 16 }}><span>You can read the library. Drafting a new version is an Admin action. Publishing it is a Compliance action.</span></div>}
      {isCompliance && !draft && <div className="callout tone-neutral" style={{ marginBottom: 16 }}><span>Nothing is waiting for your signature. Ask an admin to start a draft when a rule needs to change.</span></div>}

      <section className="card" style={{ padding: 18, marginBottom: 16 }}>
        <div className="row" style={{ gap: 14, alignItems: "flex-start" }}>
          <div style={{ flex: 1 }}>
            <div className="row"><span className="mono" style={{ fontSize: 15, fontWeight: 700 }}>{library.publishedVersion}</span><Badge tone="green" dot>Published</Badge>{draft && <Badge tone="violet" dot>Draft {draft.version}</Badge>}</div>
            <div className="small muted" style={{ marginTop: 4 }}>{BUILTIN.length} builtin · {extras.length} added · {pinned} case{pinned === 1 ? "" : "s"} on the published version</div>
          </div>
          <div className="callout tone-amber" style={{ maxWidth: 420 }}><TriangleAlert /><span>The builtin demonstrator is still awaiting written FCC Compliance sign-off. Publishing a draft does not replace that review.</span></div>
        </div>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 340px", gap: 16, alignItems: "start" }}>
        <section className="card">
          <table className="table">
            <thead><tr><th>Rule</th><th>Triggered when</th><th>Source</th><th>Status</th>{isAdmin && <th>Manage</th>}</tr></thead>
            <tbody>
              {BUILTIN.map((rule) => {
                const off = retired.has(rule.id);
                const shown = overrides[rule.id];
                return (
                  <tr key={rule.id} style={{ opacity: off ? 0.45 : 1 }}>
                    <td><div className="cell-title">{shown?.name ?? rule.name}</div><div className="cell-sub"><span className="mono">{rule.id}</span> · {shown?.section ?? rule.section}{(shown?.conditional ?? rule.conditional) && " · if applicable"}</div></td>
                    <td className="small text-2" style={{ maxWidth: 220 }}>{rule.trigger}</td>
                    <td className="small">{shown?.source ?? rule.source}</td>
                    <td>{off ? <Tag tone="red">Deleted in draft</Tag> : <Tag tone="amber">Builtin</Tag>}</td>
                    {isAdmin && <td><RuleActions pending={pending} deleted={off} onEdit={() => void editBuiltin(rule)} onDelete={() => off ? void run(() => setBuiltinRetired(rule.id, false), "Rule restored") : setConfirmDelete({ id: rule.id, name: shown?.name ?? rule.name, builtin: true })} /></td>}
                  </tr>
                );
              })}
              {extras.map((rule) => (
                <tr key={rule.id}>
                  <td><div className="cell-title">{rule.name}</div><div className="cell-sub"><span className="mono">{rule.id}</span> · {rule.section}{rule.conditional && " · if applicable"}</div></td>
                  <td className="small text-2" style={{ maxWidth: 220 }}>{triggerLabel(rule.trigger)}</td>
                  <td className="small">{rule.source}</td>
                  <td>{draft ? <Tag tone="violet">Draft</Tag> : <Tag tone="green">Published</Tag>}{!rule.enabled && <Tag tone="neutral">Off</Tag>}</td>
                  {isAdmin && <td><RuleActions pending={pending} onEdit={() => { setBuiltinEdit(null); setEditing(rule); }} onDelete={() => setConfirmDelete({ id: rule.id, name: rule.name, builtin: false })} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="card" style={{ position: "sticky", top: 0 }}>
          <div className="card-header"><div className="row"><FlaskConical size={16} color="var(--primary)" /><h3>Rule tester</h3></div></div>
          <div className="card-body stack">
            <p className="small muted">{draft && isAdmin ? "Previewing the unpublished draft. Cases do not use it until Compliance publishes." : "Previewing the published version used by new cases."}</p>
            <label className="field"><span>Entity type</span><select className="select input-sm" value={entityType} onChange={(event) => setEntityType(event.target.value as EntityType)}>{ENTITY_TYPES.map((type) => <option key={type} value={type}>{ENTITY_LABELS[type]}</option>)}</select></label>
            <label className="field"><span>Tax residency</span><select className="select input-sm" value={tax} onChange={(event) => setTax(event.target.value as TaxResidency)}>{(Object.keys(TAX_LABELS) as TaxResidency[]).map((value) => <option key={value} value={value}>{TAX_LABELS[value].label}</option>)}</select></label>
            <div className="field"><span>Features</span>
              <div className="grid-2" style={{ gap: 6 }}>{ACCOUNT_FEATURES.map((feature) => <label key={feature} className="check"><input type="checkbox" checked={features.includes(feature)} onChange={(event) => setFeatures(event.target.checked ? [...features, feature] : features.filter((item) => item !== feature))} />{FEATURE_LABELS[feature].label}</label>)}</div>
            </div>
            <label className="check"><input type="checkbox" checked={trusted} onChange={(event) => setTrusted(event.target.checked)} />Trusted Contact designated</label>
            <label className="check"><input type="checkbox" checked={usPerson} onChange={(event) => setUsPerson(event.target.checked)} />Structure includes a US person</label>
            <label className="check"><input type="checkbox" checked={pep} onChange={(event) => setPep(event.target.checked)} />Structure includes a PEP / HIO</label>
            <div className="divider" style={{ margin: "4px 0" }} />
            <div className="small" style={{ fontWeight: 650 }}>{shown.length} documents required</div>
            {shown.map((item) => <div key={item.id} className="small text-2">• {item.name}{item.conditional && <span className="muted"> · if applicable</span>}</div>)}
          </div>
        </section>
      </div>
      {editing && <RuleEditor rule={editing === "new" ? null : editing} pending={pending} lockTrigger={Boolean(builtinEdit)} triggerText={builtinEdit?.triggerText} onClose={() => { setEditing(null); setBuiltinEdit(null); }} onSave={(rule, mode) => {
        const save = builtinEdit
          ? saveBuiltinOverride(builtinEdit.id, { name: rule.name, section: rule.section, conditional: rule.conditional, source: rule.source, reason: rule.reason })
          : saveLibraryRule(rule, mode);
        void run(() => save, builtinEdit ? "Rule updated" : mode === "add" ? "Rule added to the draft" : "Rule updated").then((saved) => { if (saved) { setEditing(null); setBuiltinEdit(null); } });
      }} />}
      {confirmDelete && (
        <Modal title={`Delete “${confirmDelete.name}”?`} description="The change stays in the draft until Compliance publishes it. Cases already open keep their current version." onClose={() => setConfirmDelete(null)} footer={<><button className="btn btn-secondary" onClick={() => setConfirmDelete(null)}>Cancel</button><button className="btn btn-danger" disabled={pending} onClick={() => void removeConfirmed()}>Delete</button></>}>
          <p className="text-2">{confirmDelete.builtin ? "This builtin rule will be left out of the next published version. You can restore it before publishing." : "This rule will be removed from the draft."}</p>
        </Modal>
      )}
    </div></div>
  );
}

function RuleActions({ pending, deleted, onEdit, onDelete }: { pending: boolean; deleted?: boolean; onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="row">
      <button className="btn btn-secondary btn-sm" disabled={pending || deleted} onClick={onEdit}><Pencil />Edit</button>
      <button className={`btn btn-sm ${deleted ? "btn-secondary" : "btn-danger"}`} disabled={pending} onClick={onDelete}>{deleted ? "Restore" : <><Trash2 />Delete</>}</button>
    </div>
  );
}

function RuleEditor({ rule, pending, onClose, onSave, lockTrigger, triggerText }: { rule: LibraryRule | null; pending: boolean; onClose: () => void; onSave: (rule: LibraryRule, mode: "add" | "edit") => void; lockTrigger?: boolean; triggerText?: string }) {
  const [name, setName] = useState(rule?.name ?? "");
  const [section, setSection] = useState(rule?.section ?? SECTIONS[0] ?? "Entity Formation & Authorization");
  const [conditional, setConditional] = useState(rule?.conditional ?? true);
  const [source, setSource] = useState(rule?.source ?? "");
  const [reason, setReason] = useState(rule?.reason ?? "");
  const [kind, setKind] = useState<RuleTriggerKind>(rule?.trigger.kind ?? "ALWAYS");
  const [entityTypes, setEntityTypes] = useState<EntityType[]>(rule?.trigger.entityTypes ?? []);
  const [residencies, setResidencies] = useState<TaxResidency[]>(rule?.trigger.taxResidencies ?? []);
  const [feature, setFeature] = useState<AccountFeature>(rule?.trigger.feature ?? "MARGIN");
  const toggle = <T extends string>(list: T[], value: T, set: (next: T[]) => void) => set(list.includes(value) ? list.filter((item) => item !== value) : [...list, value]);

  return (
    <Modal
      title={rule ? "Edit rule" : "Add rule"}
      description="This stays in the draft until Compliance publishes the version."
      onClose={onClose}
      footer={<><button className="btn btn-secondary" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={pending || name.trim().length < 2 || (kind === "ENTITY" && !entityTypes.length) || (kind === "TAX" && !residencies.length)} onClick={() => onSave({
        id: rule?.id ?? uid("rule"), name: name.trim(), section, conditional, source: source.trim() || "Internal", reason: reason.trim() || name.trim(), enabled: rule?.enabled ?? true,
        trigger: { kind, entityTypes: kind === "ENTITY" ? entityTypes : undefined, taxResidencies: kind === "TAX" ? residencies : undefined, feature: kind === "FEATURE" ? feature : undefined },
      }, rule ? "edit" : "add")}>Save to draft</button></>}
    >
      <div className="stack">
        <label className="field"><span>Document name</span><input className="input" autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Certified translation of the trust deed" /></label>
        <div className="grid-2">
          <label className="field"><span>Section</span><select className="select" value={section} onChange={(event) => setSection(event.target.value)}>{SECTIONS.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label className="field"><span>Source</span><input className="input" value={source} onChange={(event) => setSource(event.target.value)} placeholder="e.g. FCC guide §5.2" /></label>
        </div>
        <label className="field"><span>Why it is required</span><input className="input" value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <label className="check"><input type="checkbox" checked={conditional} onChange={(event) => setConditional(event.target.checked)} />Mark as IF APPLICABLE</label>
        {lockTrigger ? <p className="small text-2">Triggered when: {triggerText}. Builtin triggers stay in the rule engine. To change the condition, delete this rule and add a new one.</p> : (
          <>
            <label className="field"><span>Triggered when</span><select className="select" value={kind} onChange={(event) => setKind(event.target.value as RuleTriggerKind)}>{TRIGGERS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
            {kind === "ENTITY" && <div className="grid-2" style={{ gap: 6 }}>{ENTITY_TYPES.map((type) => <label key={type} className="check"><input type="checkbox" checked={entityTypes.includes(type)} onChange={() => toggle(entityTypes, type, setEntityTypes)} />{ENTITY_LABELS[type]}</label>)}</div>}
            {kind === "TAX" && <div className="grid-2" style={{ gap: 6 }}>{(Object.keys(TAX_LABELS) as TaxResidency[]).map((value) => <label key={value} className="check"><input type="checkbox" checked={residencies.includes(value)} onChange={() => toggle(residencies, value, setResidencies)} />{TAX_LABELS[value].label}</label>)}</div>}
            {kind === "FEATURE" && <label className="field"><span>Feature</span><select className="select" value={feature} onChange={(event) => setFeature(event.target.value as AccountFeature)}>{ACCOUNT_FEATURES.map((item) => <option key={item} value={item}>{FEATURE_LABELS[item].label}</option>)}</select></label>}
          </>
        )}
      </div>
    </Modal>
  );
}
