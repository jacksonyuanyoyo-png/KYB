"use client";

import { Bell, CornerDownLeft, FolderKanban, Plus, Search, User } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { apiSearch, apiTaskTotal } from "@/lib/data/api";
import { useSession } from "@/lib/data/hooks";
import { isApiMode } from "@/lib/data/source";
import { ENTITY_LABELS } from "@/lib/labels";
import { EntityIcon, StatusBadge } from "@/components/ui";

const TITLES: Array<[RegExp, string]> = [
  [/^\/$/, "Dashboard"],
  [/^\/cases\/new/, "New case"],
  [/^\/cases\/[^/]+/, "Case workspace"],
  [/^\/cases/, "Cases"],
  [/^\/compliance/, "Compliance review"],
  [/^\/entities/, "Entities & people"],
  [/^\/document-ai/, "Document AI"],
  [/^\/rules/, "Rule library"],
  [/^\/audit/, "Audit log"],
];

export function Topbar() {
  const pathname = usePathname();
  const session = useSession();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const title = TITLES.find(([pattern]) => pattern.test(pathname))?.[1] ?? "Workbench";
  const [openTasks, setOpenTasks] = useState(0);
  const localTasks = session?.db.cases.reduce((sum, item) => sum + item.tasks.filter((task) => !task.done).length, 0) ?? 0;
  useEffect(() => {
    if (!isApiMode()) return;
    void apiTaskTotal().then(setOpenTasks).catch(() => setOpenTasks(0));
  }, [session?.db.cases]);
  const taskCount = isApiMode() ? openTasks : localTasks;

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setPaletteOpen((value) => !value); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <header className="topbar">
      <div className="topbar-title">{title}</div>
      <button className="topbar-search" onClick={() => setPaletteOpen(true)}>
        <Search size={15} /><span>Search cases, entities, people…</span><span className="kbd">⌘K</span>
      </button>
      <div className="topbar-actions">
        <Link href="/cases?filter=RETURNED" className={`btn btn-ghost btn-icon${taskCount ? " icon-dot" : ""}`} aria-label={`${taskCount} open tasks`} title={`${taskCount} open follow-up tasks`}><Bell /></Link>
        {session?.user.role !== "COMPLIANCE" && <Link href="/cases/new" className="btn btn-primary"><Plus />New case</Link>}
      </div>
      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}
    </header>
  );
}

function CommandPalette({ onClose }: { onClose: () => void }) {
  const session = useSession();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const [remote, setRemote] = useState<Array<{ key: string; group: string; href: string; record: { entityType: import("@fcc/domain").EntityType; status: import("@fcc/domain").CaseStatus } | null; label: string; sub: string }>>([]);
  const [settled, setSettled] = useState("");
  const trimmed = query.trim();
  useEffect(() => {
    if (!isApiMode()) return;
    if (trimmed.length < 2) {
      setRemote([]);
      setSettled("");
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(() => {
      void apiSearch(trimmed).then(({ cases, entities }) => {
        const caseRows = cases.slice(0, 6).map((item) => ({
          key: String(item.id),
          group: "Cases",
          href: `/cases/${item.id}`,
          record: { entityType: item.entityType as import("@fcc/domain").EntityType, status: item.status as import("@fcc/domain").CaseStatus },
          label: String(item.legalName),
          sub: `${item.reference} · ${ENTITY_LABELS[item.entityType as import("@fcc/domain").EntityType]}`,
        }));
        const people = entities.map((item) => {
          const party = item.party as { id: string; legalName: string; title?: string; kind: string };
          return {
            key: `${item.caseId}-${party.id}`,
            group: "People & entities",
            href: `/cases/${item.caseId}?node=${party.id}`,
            record: null,
            label: party.legalName,
            sub: `${party.title ?? party.kind.toLowerCase()} in ${item.caseLegalName}`,
          };
        });
        if (!cancelled) setRemote([...caseRows, ...people]);
      }).catch(() => { if (!cancelled) setRemote([]); }).finally(() => { if (!cancelled) setSettled(trimmed); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(handle); };
  }, [trimmed]);
  const waiting = isApiMode() && trimmed.length >= 2 && settled !== trimmed;

  const results = useMemo(() => {
    if (isApiMode()) return remote;
    if (!session) return [];
    const q = query.trim().toLowerCase();
    const cases = session.db.cases
      .filter((item) => !q || item.legalName.toLowerCase().includes(q) || item.reference.toLowerCase().includes(q))
      .slice(0, 6)
      .map((item) => ({ key: item.id, group: "Cases", href: `/cases/${item.id}`, record: item, label: item.legalName, sub: `${item.reference} · ${ENTITY_LABELS[item.entityType]}` }));
    const people = q ? session.db.cases.flatMap((item) => item.parties.filter((party) => party.parentId && party.legalName.toLowerCase().includes(q))
      .map((party) => ({ key: `${item.id}-${party.id}`, group: "People & entities", href: `/cases/${item.id}?node=${party.id}`, record: null, label: party.legalName, sub: `${party.title ?? party.kind.toLowerCase()} in ${item.legalName}` }))).slice(0, 6) : [];
    return [...cases, ...people];
  }, [session, query, remote]);

  const go = (href: string) => { router.push(href); onClose(); };

  return (
    <div className="overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="palette" role="dialog" aria-label="Search">
        <div className="palette-input">
          <Search size={17} color="var(--text-3)" />
          <input
            autoFocus value={query} placeholder="Search by legal name, reference or person…"
            onChange={(event) => { setQuery(event.target.value); setActive(0); }}
            onKeyDown={(event) => {
              if (event.key === "Escape") onClose();
              if (event.key === "ArrowDown") { event.preventDefault(); setActive((value) => Math.min(value + 1, results.length - 1)); }
              if (event.key === "ArrowUp") { event.preventDefault(); setActive((value) => Math.max(value - 1, 0)); }
              if (event.key === "Enter" && results[active]) go(results[active].href);
            }}
          />
          <span className="kbd">ESC</span>
        </div>
        <div className="palette-list">
          {isApiMode() && trimmed.length < 2 && <div className="empty" style={{ padding: 28 }}><p>Type at least two characters.</p></div>}
          {waiting && results.length === 0 && <div className="empty" style={{ padding: 28 }}><p>Searching…</p></div>}
          {!waiting && (!isApiMode() || trimmed.length >= 2) && results.length === 0 && <div className="empty" style={{ padding: 28 }}><p>{`No matches for “${trimmed}”.`}</p></div>}
          {results.map((item, index) => (
            <div key={item.key}>
              {(index === 0 || results[index - 1]?.group !== item.group) && <div className="palette-group">{item.group}</div>}
              <button className={`palette-item${index === active ? " active" : ""}`} onMouseEnter={() => setActive(index)} onClick={() => go(item.href)}>
                {item.record ? <EntityIcon type={item.record.entityType} /> : <span className="entity-icon" style={{ background: "var(--surface-2)" }}>{item.group === "Cases" ? <FolderKanban /> : <User />}</span>}
                <span style={{ flex: 1, minWidth: 0 }}><strong style={{ display: "block" }}>{item.label}</strong><small>{item.sub}</small></span>
                {item.record && <StatusBadge status={item.record.status} />}
                {index === active && <CornerDownLeft size={14} color="var(--text-3)" />}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
