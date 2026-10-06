"use client";

import { FolderKanban, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { ENTITY_TYPES, type CaseStatus } from "@fcc/domain";
import { StageChip } from "@/components/case/StageChip";
import { Avatar, EmptyState, EntityIcon, Progress, StatusBadge } from "@/components/ui";
import { caseInsight, useSession, userName } from "@/lib/data/hooks";
import { daysUntil, timeAgo } from "@/lib/format";
import { ENTITY_LABELS, STATUS_LABELS } from "@/lib/labels";

type Filter = "ALL" | "MINE" | CaseStatus;

export default function CasesPage() {
  return <Suspense><CaseQueue /></Suspense>;
}

function CaseQueue() {
  const { db, user } = useSession()!;
  const router = useRouter();
  const params = useSearchParams();
  const [filter, setFilter] = useState<Filter>((params.get("filter") as Filter | null) ?? "ALL");
  const [query, setQuery] = useState("");
  const [entityType, setEntityType] = useState("");

  const rows = useMemo(() => db.cases.map((record) => ({ record, insight: caseInsight(record, db.ruleLibrary) })), [db.cases, db.ruleLibrary]);
  const counts = (value: Filter) => rows.filter(({ record }) => value === "ALL" || (value === "MINE" ? record.ownerId === user.id : record.status === value)).length;
  const filtered = rows
    .filter(({ record }) => filter === "ALL" || (filter === "MINE" ? record.ownerId === user.id : record.status === filter))
    .filter(({ record }) => !entityType || record.entityType === entityType)
    .filter(({ record }) => !query || `${record.legalName} ${record.reference} ${record.registrationNumber}`.toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => new Date(b.record.updatedAt).getTime() - new Date(a.record.updatedAt).getTime());

  const filters: Array<{ id: Filter; label: string }> = [
    { id: "ALL", label: "All" }, { id: "MINE", label: "Assigned to me" },
    ...(Object.keys(STATUS_LABELS) as CaseStatus[]).map((status) => ({ id: status, label: STATUS_LABELS[status] })),
  ];

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Cases</h1><p>Complex entity account opening cases. Open a case to work on its ownership graph, account details and document checklist.</p></div>
        <Link href="/cases/new" className="btn btn-primary"><Plus />New case</Link>
      </div>
      <section className="card">
        <div className="toolbar">
          <div className="filter-chips">
            {filters.map((item) => (
              <button key={item.id} className={`filter-chip${filter === item.id ? " active" : ""}`} onClick={() => setFilter(item.id)}>{item.label}<span>{counts(item.id)}</span></button>
            ))}
          </div>
          <div className="spacer" />
          <select className="select input-sm" style={{ width: 200 }} value={entityType} onChange={(event) => setEntityType(event.target.value)} aria-label="Entity type">
            <option value="">All entity types</option>
            {ENTITY_TYPES.map((type) => <option key={type} value={type}>{ENTITY_LABELS[type]}</option>)}
          </select>
          <div className="input-group" style={{ width: 240 }}>
            <Search /><input className="input input-sm" placeholder="Name, reference, registration…" value={query} onChange={(event) => setQuery(event.target.value)} />
          </div>
        </div>
        {filtered.length === 0 ? (
          <EmptyState icon={<FolderKanban />} title="No cases match" action={<button className="btn btn-secondary btn-sm" onClick={() => { setFilter("ALL"); setQuery(""); setEntityType(""); }}>Clear filters</button>}>Try another status, entity type or search term.</EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Case</th><th>Status</th><th>Stuck at</th><th>Documents</th><th>Owner</th><th>Due</th><th>Updated</th></tr></thead>
              <tbody>
                {filtered.map(({ record, insight }) => {
                  const due = daysUntil(record.dueDate);
                  return (
                    <tr key={record.id} className="clickable" onClick={() => router.push(`/cases/${record.id}`)}>
                      <td>
                        <div className="row" style={{ gap: 12 }}>
                          <EntityIcon type={record.entityType} />
                          <div style={{ minWidth: 0 }}>
                            <Link href={`/cases/${record.id}`} className="cell-title" onClick={(event) => event.stopPropagation()}>{record.legalName}</Link>
                            <div className="cell-sub">{record.reference} · {ENTITY_LABELS[record.entityType]}</div>
                          </div>
                        </div>
                      </td>
                      <td><StatusBadge status={record.status} /></td>
                      <td style={{ maxWidth: 260 }}>
                        <StageChip stage={insight.stage} />
                        {insight.blocker && <div className="cell-sub" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{insight.blocker}</div>}
                      </td>
                      <td style={{ width: 140 }}>
                        <div className="small muted" style={{ marginBottom: 4 }}>{insight.collected} of {insight.checklist.length}</div>
                        <Progress value={insight.checklist.length ? (insight.collected / insight.checklist.length) * 100 : 0} tone={insight.collected === insight.checklist.length ? "green" : undefined} />
                      </td>
                      <td><div className="row"><Avatar name={userName(db, record.ownerId)} size="sm" /><span className="small">{userName(db, record.ownerId)}</span></div></td>
                      <td>{record.status === "APPROVED" ? <span className="muted small">—</span> : <span className={`due${due < 0 ? " overdue" : due <= 2 ? " soon" : ""}`}>{due < 0 ? `${-due}d overdue` : due === 0 ? "Today" : `${due}d`}</span>}</td>
                      <td className="small muted">{timeAgo(record.updatedAt)}<div className="cell-sub">v{record.version}</div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
