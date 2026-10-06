"use client";

import { ArrowRight, Check, CircleCheck, Circle, Lock } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ACCOUNT_FEATURES, type AccountFeature, type TaxResidency } from "@fcc/domain";
import { useCaseContext } from "@/components/case/CaseContext";
import { Avatar, Switch, Tag, useAction } from "@/components/ui";
import { updateProfile } from "@/lib/data/actions";
import { FEATURE_LABELS, PROVINCES, TAX_LABELS } from "@/lib/labels";
import type { ProfileDraft } from "@/lib/types";

export default function DetailsPage() {
  const { record, insight, user } = useCaseContext();
  const { run } = useAction();
  const profile = record.profile;
  const [contactName, setContactName] = useState(profile.trustedContactName);
  useEffect(() => setContactName(profile.trustedContactName), [profile.trustedContactName]);

  const editable = (record.status === "BUILDING" || record.status === "DOCS_REQUESTED" || record.status === "RETURNED") && user.role !== "COMPLIANCE";
  const save = (next: ProfileDraft, field: string, from: string, to: string) => {
    if (!editable || from === to) return;
    void run(() => updateProfile(record, next, { field, from: from || "—", to: to || "—" }));
  };
  const provinceName = (code: string) => PROVINCES.find((item) => item.code === code)?.name ?? "";
  const toggleFeature = (feature: AccountFeature, on: boolean) => {
    const features = on ? [...profile.features, feature] : profile.features.filter((item) => item !== feature);
    save({ ...profile, features }, `${FEATURE_LABELS[feature].label} feature`, on ? "Off" : "On", on ? "On" : "Off");
  };

  if (insight.ownershipIssues.length) {
    return (
      <div className="workspace-main"><div className="card locked" style={{ maxWidth: 640, margin: "40px auto" }}>
        <span className="locked-icon"><Lock /></span>
        <h3 style={{ fontSize: 16, color: "var(--text)" }}>Finish the ownership structure first</h3>
        <p className="muted" style={{ maxWidth: 420 }}>{insight.ownershipIssues.length} issue{insight.ownershipIssues.length === 1 ? "" : "s"} remain: {insight.ownershipIssues[0]?.message}</p>
        <Link href={`/cases/${record.id}`} className="btn btn-primary">Back to ownership</Link>
      </div></div>
    );
  }

  const peps = record.parties.filter((party) => party.isPepHio);
  const usPersons = record.parties.filter((party) => party.isUsPerson);
  const gates: Array<{ label: string; done: boolean }> = [
    { label: "Province or territory", done: Boolean(profile.province) },
    { label: "Tax residency", done: Boolean(profile.taxResidency) },
    { label: "Trusted Contact decision", done: profile.trustedContact !== null && (!profile.trustedContact || Boolean(profile.trustedContactName.trim())) },
  ];
  const sections = [...new Set(insight.checklist.map((item) => item.section))];

  return (
    <div className="workspace">
      <div className="workspace-main"><div style={{ maxWidth: 820 }}>
        <section className="card section-card">
          <div className="card-header"><div className="section-title"><span className="section-num">1</span><div><h3>Registration</h3><p>Where the entity is registered for this account.</p></div></div></div>
          <div className="card-body">
            <label className="field" style={{ maxWidth: 360 }}><span>Province or territory of registration</span>
              <select className="select" disabled={!editable} value={profile.province} onChange={(event) => save({ ...profile, province: event.target.value }, "Province", provinceName(profile.province), provinceName(event.target.value))}>
                <option value="">Select…</option>
                {PROVINCES.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
              </select>
            </label>
          </div>
        </section>

        <section className="card section-card">
          <div className="card-header"><div className="section-title"><span className="section-num">2</span><div><h3>Tax residency</h3><p>Drives IRS withholding forms and FATCA / CRS declarations.</p></div></div></div>
          <div className="card-body choice-grid" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
            {(Object.keys(TAX_LABELS) as TaxResidency[]).map((value) => (
              <button key={value} type="button" disabled={!editable} className={`choice${profile.taxResidency === value ? " selected" : ""}`} onClick={() => save({ ...profile, taxResidency: value }, "Tax residency", profile.taxResidency ? TAX_LABELS[profile.taxResidency].label : "", TAX_LABELS[value].label)}>
                <span><strong>{TAX_LABELS[value].label}</strong><small>{TAX_LABELS[value].description}</small></span>
                {profile.taxResidency === value && <span className="choice-check"><Check /></span>}
              </button>
            ))}
          </div>
        </section>

        <section className="card section-card">
          <div className="card-header"><div className="section-title"><span className="section-num">3</span><div><h3>Account features</h3><p>Each feature adds its own agreement and risk disclosure.</p></div></div></div>
          <div className="card-body stack" style={{ gap: 8 }}>
            {ACCOUNT_FEATURES.map((feature) => {
              const on = profile.features.includes(feature);
              return (
                <div key={feature} className={`toggle-row${on ? " on" : ""}`}>
                  <div style={{ flex: 1 }}><strong>{FEATURE_LABELS[feature].label}</strong><small>{FEATURE_LABELS[feature].description}</small></div>
                  <Switch label={FEATURE_LABELS[feature].label} checked={on} onChange={(value) => toggleFeature(feature, value)} />
                </div>
              );
            })}
          </div>
        </section>

        <section className="card section-card">
          <div className="card-header"><div className="section-title"><span className="section-num">4</span><div><h3>Trusted Contact Person</h3><p>Record the client&apos;s decision either way.</p></div></div></div>
          <div className="card-body stack">
            <div className="segmented" style={{ alignSelf: "flex-start" }}>
              <button disabled={!editable} className={profile.trustedContact === true ? "active" : ""} onClick={() => save({ ...profile, trustedContact: true }, "Trusted Contact", profile.trustedContact === null ? "" : profile.trustedContact ? "Yes" : "No", "Yes")}>Designated</button>
              <button disabled={!editable} className={profile.trustedContact === false ? "active" : ""} onClick={() => save({ ...profile, trustedContact: false, trustedContactName: "" }, "Trusted Contact", profile.trustedContact === null ? "" : profile.trustedContact ? "Yes" : "No", "No")}>Declined</button>
            </div>
            {profile.trustedContact && (
              <label className="field" style={{ maxWidth: 360 }}><span>Trusted Contact name</span>
                <input className="input" disabled={!editable} value={contactName} onChange={(event) => setContactName(event.target.value)} onBlur={() => save({ ...profile, trustedContactName: contactName.trim() }, "Trusted Contact name", profile.trustedContactName, contactName.trim())} placeholder="Full name" />
              </label>
            )}
          </div>
        </section>

        <section className="card section-card">
          <div className="card-header"><div className="section-title"><span className="section-num">5</span><div><h3>Flags carried from ownership</h3><p>Edit these on the ownership graph.</p></div></div></div>
          <div className="card-body grid-2">
            <div>
              <div className="small muted" style={{ marginBottom: 6 }}>PEP / HIO — enhanced due diligence</div>
              {peps.length === 0 ? <span className="small">None flagged</span> : peps.map((party) => (
                <Link key={party.id} href={`/cases/${record.id}?node=${party.id}`} className="person-row"><Avatar name={party.legalName} size="sm" /><span><strong>{party.legalName}</strong><small>{party.title}</small></span><Tag tone="red">PEP</Tag></Link>
              ))}
            </div>
            <div>
              <div className="small muted" style={{ marginBottom: 6 }}>US persons / entities — W-9</div>
              {usPersons.length === 0 ? <span className="small">None flagged</span> : usPersons.map((party) => (
                <Link key={party.id} href={`/cases/${record.id}?node=${party.id}`} className="person-row"><Avatar name={party.legalName} size="sm" /><span><strong>{party.legalName}</strong><small>{party.country}</small></span><Tag tone="amber">US</Tag></Link>
              ))}
            </div>
          </div>
        </section>
      </div></div>

      <aside className="inspector">
        <div className="inspector-section">
          <h3>Required to generate checklist</h3>
          <div className="stack" style={{ gap: 8 }}>
            {gates.map((gate) => (
              <div key={gate.label} className="row" style={{ color: gate.done ? "var(--text)" : "var(--text-3)" }}>
                {gate.done ? <CircleCheck size={16} color="var(--green)" /> : <Circle size={16} />}<span style={{ fontWeight: gate.done ? 500 : 400 }}>{gate.label}</span>
              </div>
            ))}
          </div>
          <Link href={`/cases/${record.id}/documents`} className={`btn btn-primary${insight.detailsGaps.length ? " disabled" : ""}`} style={{ width: "100%", marginTop: 14, pointerEvents: insight.detailsGaps.length ? "none" : undefined, opacity: insight.detailsGaps.length ? 0.5 : 1 }} aria-disabled={insight.detailsGaps.length > 0}>Generate document checklist<ArrowRight /></Link>
        </div>
        <div className="inspector-section">
          <h3>Live preview <Tag tone="blue">{insight.checklist.length} items</Tag></h3>
          <p className="small muted" style={{ marginBottom: 10 }}>What the rule engine will ask for, based on the answers so far.</p>
          {sections.map((section) => (
            <div key={section} style={{ marginBottom: 10 }}>
              <div className="small" style={{ fontWeight: 650, marginBottom: 4 }}>{section}</div>
              {insight.checklist.filter((item) => item.section === section).map((item) => (
                <div key={item.id} className="small text-2" style={{ display: "flex", gap: 6, padding: "2px 0" }}>
                  <span style={{ color: "var(--text-3)" }}>•</span><span>{item.name}{item.conditional && <span className="muted"> · if applicable</span>}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
