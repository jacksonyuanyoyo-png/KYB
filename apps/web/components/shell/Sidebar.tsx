"use client";

import {
  BadgeCheck, BookOpenCheck, Check, ChevronsUpDown, FileSearch, FolderKanban, History, LayoutDashboard,
  RotateCcw, ShieldCheck, Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { switchUser } from "@/lib/data/actions";
import { useSession } from "@/lib/data/hooks";
import { isApiMode } from "@/lib/data/source";
import { resetDb } from "@/lib/data/store";
import { ROLE_LABELS } from "@/lib/labels";
import { Avatar } from "@/components/ui";

interface NavItem { href: string; label: string; icon: typeof LayoutDashboard; count?: number; exact?: boolean }

export function Sidebar() {
  const pathname = usePathname();
  const session = useSession();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => { if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);

  const cases = session?.db.cases ?? [];
  const open = cases.filter((item) => item.status !== "APPROVED").length;
  const ready = cases.filter((item) => item.status === "READY_FOR_COMPLIANCE").length;

  const groups: Array<{ label?: string; items: NavItem[] }> = [
    { items: [{ href: "/", label: "Dashboard", icon: LayoutDashboard, exact: true }] },
    { label: "Case management", items: [
      { href: "/cases", label: "Cases", icon: FolderKanban, count: open },
      { href: "/compliance", label: "Compliance review", icon: ShieldCheck, count: ready },
      { href: "/entities", label: "Entities & people", icon: Users },
    ] },
    { label: "Tools", items: [{ href: "/document-ai", label: "Document AI", icon: FileSearch }] },
    { label: "Administration", items: [
      { href: "/rules", label: "Rule library", icon: BookOpenCheck },
      { href: "/audit", label: "Audit log", icon: History },
    ] },
  ];

  const isActive = (item: NavItem) => (item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`));

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">F</span>
        <div><div className="brand-name">FIDELITY</div><div className="brand-sub">Complex Account Workbench</div></div>
      </div>
      <nav className="nav" aria-label="Primary">
        {groups.map((group, index) => (
          <div key={group.label ?? index}>
            {group.label && <div className="nav-group">{group.label}</div>}
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <Link key={item.href} href={item.href} className={`nav-link${isActive(item) ? " active" : ""}`}>
                  <Icon />{item.label}
                  {item.count ? <span className="nav-count">{item.count}</span> : null}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      {session && (
        <div ref={menuRef} style={{ position: "relative" }}>
          {menuOpen && (
            <div className="user-menu">
              <div className="user-menu-label">Switch demo user</div>
              {session.db.users.map((user) => (
                <button key={user.id} onClick={() => { switchUser(user.id); setMenuOpen(false); }}>
                  <Avatar name={user.name} size="sm" />
                  <span style={{ flex: 1 }}><strong style={{ fontSize: 12.5 }}>{user.name}</strong><small>{ROLE_LABELS[user.role]} · {user.team}</small></span>
                  {user.id === session.user.id && <Check size={14} color="var(--primary)" />}
                </button>
              ))}
              <div className="divider" style={{ margin: "6px 0" }} />
              {!isApiMode() && <button onClick={() => { resetDb(); setMenuOpen(false); }}><RotateCcw size={14} /><span>Reset demo data</span></button>}
            </div>
          )}
          <button className="user-card" onClick={() => setMenuOpen((value) => !value)} aria-expanded={menuOpen}>
            <Avatar name={session.user.name} />
            <span className="meta">
              <strong>{session.user.name}<BadgeCheck size={13} color="var(--primary)" /></strong>
              <small>{ROLE_LABELS[session.user.role]}</small>
            </span>
            <ChevronsUpDown size={14} color="var(--text-3)" />
          </button>
        </div>
      )}
    </aside>
  );
}
