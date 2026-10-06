"use client";

import { Search, Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Avatar, EmptyState, EntityIcon, Tag } from "@/components/ui";
import { apiEntities } from "@/lib/data/api";
import { useSession } from "@/lib/data/hooks";
import { isApiMode } from "@/lib/data/source";
import { formatPercent } from "@/lib/format";
import { ENTITY_LABELS } from "@/lib/labels";
import type { EntityType, Party } from "@fcc/domain";
import { effectiveOwnership, personsToIdentify } from "@fcc/domain";

type Kind = "ALL" | "PERSON" | "ENTITY" | "PEP" | "US";

export default function EntitiesPage() {
  const { db } = useSession()!;
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<Kind>("ALL");

  const [remote, setRemote] = useState<Array<Record<string, unknown>> | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!isApiMode()) return;
    void apiEntities(kind, query).then((page) => {
      setRemote(page.items);
      setCounts(page.counts);
    }).catch(() => setRemote([]));
  }, [kind, query]);

  const rows = useMemo(() => {
    if (isApiMode()) return [];
    return db.cases.flatMap((record) => {
      const identify = new Set(personsToIdentify(record.parties).map((party) => party.id));
      const effective = effectiveOwnership(record.parties);
      return record.parties.filter((party) => party.parentId).map((party) => ({
        party, record, identify: identify.has(party.id), effective: effective.get(party.id) ?? 0,
        parent: record.parties.find((item) => item.id === party.parentId),
      }));
    });
  }, [db.cases]);

  const filtered = rows
    .filter(({ party }) => kind === "ALL" || (kind === "PEP" ? party.isPepHio : kind === "US" ? party.isUsPerson : party.kind === kind))
    .filter(({ party, record }) => !query || `${party.legalName} ${record.legalName}`.toLowerCase().includes(query.toLowerCase()));

  const chips: Array<{ id: Kind; label: string }> = [
    { id: "ALL", label: "All" }, { id: "PERSON", label: "Natural persons" }, { id: "ENTITY", label: "Entities" }, { id: "PEP", label: "PEP / HIO" }, { id: "US", label: "US persons" },
  ];

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Entities &amp; people</h1><p>Everyone disclosed across cases — owners, controllers, signers and intermediate entities. Use it to spot the same person appearing in several structures.</p></div>
      </div>
      <section className="card">
        <div className="toolbar">
          <div className="filter-chips">
            {chips.map((chip) => (
              <button key={chip.id} className={`filter-chip${kind === chip.id ? " active" : ""}`} onClick={() => setKind(chip.id)}>
                {chip.label}<span>{isApiMode() ? (counts[chip.id] ?? 0) : rows.filter(({ party }) => chip.id === "ALL" || (chip.id === "PEP" ? party.isPepHio : chip.id === "US" ? party.isUsPerson : party.kind === chip.id)).length}</span>
              </button>
            ))}
          </div>
          <div className="spacer" />
          <div className="input-group" style={{ width: 260 }}><Search /><input className="input input-sm" placeholder="Search name or case…" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
        </div>
        {isApiMode() ? (remote ?? []).length === 0 ? <EmptyState icon={<Users />} title="No matches" /> : (
          <table className="table">
            <thead><tr><th>Name</th><th>Role</th><th>Direct</th><th>Effective</th><th>Flags</th><th>Case</th></tr></thead>
            <tbody>
              {(remote ?? []).map((item) => {
                const party = item.party as Party;
                return (
                  <tr key={`${item.caseId}-${party.id}`}>
                    <td>
                      <div className="row" style={{ gap: 10 }}>
                        {party.kind === "PERSON" ? <Avatar name={party.legalName} /> : <EntityIcon type={(party.entityType ?? "corporation") as EntityType} />}
                        <div><Link href={`/cases/${item.caseId}?node=${party.id}`} className="cell-title">{party.legalName}</Link><div className="cell-sub">{party.kind === "PERSON" ? party.country : ENTITY_LABELS[(party.entityType ?? "corporation") as EntityType]}</div></div>
                      </div>
                    </td>
                    <td className="small">{party.title ?? "—"}<div className="cell-sub">in {String(item.parentName ?? "")}</div></td>
                    <td>{formatPercent(party.ownershipPercent)}</td>
                    <td>{party.kind === "PERSON" ? formatPercent(Number(item.effective ?? 0)) : "—"}</td>
                    <td><div className="flag-row">
                      {Boolean(item.identify) && <Tag tone="blue">Identify</Tag>}
                      {party.isController && <Tag tone="violet">Controller</Tag>}
                      {party.isSigningAuthority && <Tag tone="violet">Signer</Tag>}
                      {party.isPepHio && <Tag tone="red">PEP / HIO</Tag>}
                      {party.isUsPerson && <Tag tone="amber">US</Tag>}
                    </div></td>
                    <td><Link href={`/cases/${item.caseId}`} className="small" style={{ color: "var(--primary)", fontWeight: 600 }}>{String(item.caseReference)}</Link><div className="cell-sub">{String(item.caseLegalName)}</div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : filtered.length === 0 ? <EmptyState icon={<Users />} title="No matches" /> : (
          <table className="table">
            <thead><tr><th>Name</th><th>Role</th><th>Direct</th><th>Effective</th><th>Flags</th><th>Case</th></tr></thead>
            <tbody>
              {filtered.map(({ party, record, identify, effective, parent }) => (
                <tr key={`${record.id}-${party.id}`}>
                  <td>
                    <div className="row" style={{ gap: 10 }}>
                      {party.kind === "PERSON" ? <Avatar name={party.legalName} /> : <EntityIcon type={party.entityType ?? "corporation"} />}
                      <div><Link href={`/cases/${record.id}?node=${party.id}`} className="cell-title">{party.legalName}</Link><div className="cell-sub">{party.kind === "PERSON" ? party.country : ENTITY_LABELS[party.entityType ?? "corporation"]}</div></div>
                    </div>
                  </td>
                  <td className="small">{party.title ?? "—"}<div className="cell-sub">in {parent?.legalName}</div></td>
                  <td>{formatPercent(party.ownershipPercent)}</td>
                  <td>{party.kind === "PERSON" ? formatPercent(effective) : "—"}</td>
                  <td><div className="flag-row">
                    {identify && <Tag tone="blue">Identify</Tag>}
                    {party.isController && <Tag tone="violet">Controller</Tag>}
                    {party.isSigningAuthority && <Tag tone="violet">Signer</Tag>}
                    {party.isPepHio && <Tag tone="red">PEP / HIO</Tag>}
                    {party.isUsPerson && <Tag tone="amber">US</Tag>}
                  </div></td>
                  <td><Link href={`/cases/${record.id}`} className="small" style={{ color: "var(--primary)", fontWeight: 600 }}>{record.reference}</Link><div className="cell-sub">{record.legalName}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
