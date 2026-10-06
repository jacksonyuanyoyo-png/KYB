"use client";

import { ArrowRight, Clock, FileSearch, FolderKanban, FolderPlus, ShieldCheck, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { RULE_VERSION } from "@fcc/domain";
import { AuditIcon } from "@/components/case/AuditIcon";
import { EmptyState, EntityIcon, StatusBadge } from "@/components/ui";
import { caseInsight, useSession, userName } from "@/lib/data/hooks";
import { daysUntil, timeAgo } from "@/lib/format";

const CHART_W = 640;
const CHART_H = 220;
const CHART_PAD_X = 28;
const CHART_PAD_TOP = 16;
const CHART_BASELINE = 186;

const weekLabelFormat = new Intl.DateTimeFormat("en-US", { month: "short" });

export default function DashboardPage() {
  const session = useSession()!;
  const { db, user } = session;

  const rows = useMemo(() => db.cases.map((record) => ({ record, insight: caseInsight(record, db.ruleLibrary) })), [db.cases, db.ruleLibrary]);
  const open = rows.filter(({ record }) => record.status !== "APPROVED");
  const blockedStructure = open.filter(({ insight }) => insight.stage === "OWNERSHIP" || insight.stage === "DETAILS");
  const waitingDocs = open.filter(({ insight }) => insight.stage === "DOCUMENTS");
  const ready = open.filter(({ record }) => record.status === "READY_FOR_COMPLIANCE");
  const approvedCount = db.cases.length - open.length;
  const openTaskCount = db.cases.reduce((sum, record) => sum + record.tasks.filter((task) => !task.done).length, 0);
  const attention = [...open].filter(({ record }) => record.status !== "READY_FOR_COMPLIANCE" || user.role === "COMPLIANCE")
    .sort((a, b) => new Date(a.record.dueDate).getTime() - new Date(b.record.dueDate).getTime()).slice(0, 6);
  const recent = db.audit.slice(0, 6);
  const weeks = useMemo(() => weekActivity(db.cases, db.audit), [db.cases, db.audit]);
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  const yMax = Math.max(1, ...weeks.map((week) => week.cases), ...weeks.map((week) => week.audits));
  const plotW = CHART_W - CHART_PAD_X * 2;
  const plotH = CHART_BASELINE - CHART_PAD_TOP;
  const xAt = (index: number) => CHART_PAD_X + (index * plotW) / (weeks.length - 1);
  const yAt = (value: number) => CHART_BASELINE - (value / yMax) * plotH;
  const casePts = weeks.map((week, index) => ({ x: xAt(index), y: yAt(week.cases) }));
  const auditPts = weeks.map((week, index) => ({ x: xAt(index), y: yAt(week.audits) }));

  return (
    <div className="page"><div className="page-narrow">
      <div className="notice">
        <TriangleAlert />
        <span><strong>Demonstration ruleset {RULE_VERSION}.</strong> Ownership thresholds, FATCA/CRS, PEP/HIO and form selection need written FCC Compliance sign-off before production use.</span>
        <Link href="/rules" className="btn btn-sm btn-secondary" style={{ marginLeft: "auto" }}>Review rules</Link>
      </div>

      <div className="page-header">
        <div>
          <div className="eyebrow">Account opening pre-review</div>
          <h1>{greeting}, {user.name.split(" ")[0]}</h1>
          <p>Every case is traced to the people who own or control it, then checked against the rule engine before it reaches Compliance.</p>
        </div>
      </div>

      <div className="home-top">
        <section className="card home-hero">
          <div className="home-kicker"><i />Open cases</div>
          <div className="home-figure">{open.length}</div>
          <div className="home-delta"><b>{approvedCount} approved</b><span>of {db.cases.length} total</span></div>
          <div className="home-pills">
            <div className="home-pill"><b>{blockedStructure.length}</b>Blocked on structure</div>
            <div className="home-pill"><b>{waitingDocs.length}</b>Collecting documents</div>
            <div className="home-pill"><b>{ready.length}</b>Ready for compliance</div>
            <div className="home-pill"><b>{openTaskCount}</b>Open tasks</div>
          </div>
        </section>

        <section className="card home-actions">
          <div className="home-actions-head"><h2><i />Quick actions</h2></div>
          <div className="home-actions-grid">
            <Link href="/cases/new" className="home-action">
              <span className="home-action-icon"><FolderPlus /></span>
              <strong>New case</strong>
              <small>Start an account opening</small>
            </Link>
            <Link href="/cases" className="home-action">
              <span className="home-action-icon"><FolderKanban /></span>
              <strong>All cases</strong>
              <small>{open.length} still open</small>
            </Link>
            <Link href="/compliance" className="home-action">
              <span className="home-action-icon"><ShieldCheck /></span>
              <strong>Compliance</strong>
              <small>{ready.length} ready for review</small>
            </Link>
            <Link href="/document-ai" className="home-action">
              <span className="home-action-icon"><FileSearch /></span>
              <strong>Document AI</strong>
              <small>Extract parties from a file</small>
            </Link>
          </div>
        </section>
      </div>

      <section className="card home-chart">
        <div className="home-chart-head">
          <div>
            <h2><i />Case activity</h2>
            <p>New cases and audit events, last 8 weeks</p>
          </div>
          <div className="home-legend">
            <span><i style={{ background: "#f97316" }} />New cases</span>
            <span><i style={{ background: "#60a5fa" }} />Audit events</span>
          </div>
        </div>
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} role="img" aria-label="New cases and audit events over the last 8 weeks">
          {[0, 1, 2, 3].map((step) => {
            const y = CHART_PAD_TOP + (plotH * step) / 3;
            return <line key={step} x1={CHART_PAD_X} x2={CHART_W - CHART_PAD_X} y1={y} y2={y} stroke="#eceef2" strokeWidth="1" vectorEffect="non-scaling-stroke" />;
          })}
          <path d={areaPath(casePts, CHART_BASELINE)} fill="rgba(249,115,22,0.16)" stroke="none" />
          <path d={linePath(casePts)} fill="none" stroke="#f97316" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          <path d={linePath(auditPts)} fill="none" stroke="#60a5fa" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          {casePts.map((point, index) => {
            const week = weeks[index];
            if (!week) return null;
            return (
              <g key={`case-${week.start}`}>
                <title>{`${week.label}: ${week.cases} new cases`}</title>
                <circle cx={point.x} cy={point.y} r="3" fill="#f97316" />
              </g>
            );
          })}
          {auditPts.map((point, index) => {
            const week = weeks[index];
            if (!week) return null;
            return (
              <g key={`audit-${week.start}`}>
                <title>{`${week.label}: ${week.audits} audit events`}</title>
                <circle cx={point.x} cy={point.y} r="3" fill="#60a5fa" />
              </g>
            );
          })}
          {weeks.map((week, index) => (
            <text key={week.start} x={xAt(index)} y="208" textAnchor="middle" fill="#9ca3af" fontSize="11">{week.label}</text>
          ))}
        </svg>
      </section>

      <div className="home-bottom">
        <section className="card">
          <div className="card-header"><div><h2>Recent activity</h2><p>Append-only audit trail across all cases</p></div><Link href="/audit" className="btn btn-sm btn-ghost">Audit log <ArrowRight /></Link></div>
          <div className="card-body" style={{ paddingTop: 8 }}>
            {recent.length === 0 && <EmptyState icon={<ShieldCheck />} title="No activity yet" />}
            {recent.map((event) => {
              const record = db.cases.find((item) => item.id === event.caseId);
              return (
                <div key={event.id} className="activity">
                  <AuditIcon action={event.action} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12.5 }}><strong>{userName(db, event.actorId)}</strong> <span className="text-2">{event.summary}</span></div>
                    <div className="small muted">{record?.legalName} · v{event.version} · {timeAgo(event.at)}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <div><h2>Needs attention</h2><p>Sorted by due date</p></div>
            <Link href="/cases" className="btn btn-sm btn-ghost">All cases <ArrowRight /></Link>
          </div>
          {attention.length === 0 && <EmptyState icon={<ShieldCheck />} title="Nothing waiting">All open cases are with Compliance.</EmptyState>}
          {attention.map(({ record, insight }) => {
            const due = daysUntil(record.dueDate);
            return (
              <Link key={record.id} href={`/cases/${record.id}`} className="home-attention">
                <EntityIcon type={record.entityType} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row"><span className="cell-title" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{record.legalName}</span><StatusBadge status={record.status} /></div>
                  <div className="cell-sub" style={{ marginTop: 2 }}>{insight.blocker ?? "Ready to submit"}</div>
                </div>
                <span className={`due${due < 0 ? " overdue" : due <= 2 ? " soon" : ""}`} style={{ flexShrink: 0 }}>
                  <Clock size={12} style={{ verticalAlign: -1, marginRight: 4 }} />{due < 0 ? `${-due}d late` : due === 0 ? "Today" : `${due}d left`}
                </span>
              </Link>
            );
          })}
        </section>
      </div>
    </div></div>
  );
}

function mondayBuckets(now: Date): Array<{ start: Date; end: Date }> {
  const cursor = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const weekday = cursor.getDay();
  cursor.setDate(cursor.getDate() - (weekday === 0 ? 6 : weekday - 1));
  return Array.from({ length: 8 }, (_, index) => {
    const start = new Date(cursor);
    start.setDate(start.getDate() - (7 - index) * 7);
    const end = new Date(start);
    end.setDate(end.getDate() + 7);
    return { start, end };
  });
}

function inBucket(iso: string, start: Date, end: Date): boolean {
  const time = new Date(iso).getTime();
  return time >= start.getTime() && time < end.getTime();
}

function weekActivity(cases: Array<{ createdAt: string }>, audit: Array<{ at: string }>) {
  return mondayBuckets(new Date()).map((bucket) => ({
    start: bucket.start.toISOString(),
    label: `${bucket.start.getDate()} ${weekLabelFormat.format(bucket.start)}`,
    cases: cases.filter((record) => inBucket(record.createdAt, bucket.start, bucket.end)).length,
    audits: audit.filter((event) => inBucket(event.at, bucket.start, bucket.end)).length,
  }));
}

function linePath(points: Array<{ x: number; y: number }>): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
}

function areaPath(points: Array<{ x: number; y: number }>, baseline: number): string {
  const first = points[0];
  const last = points[points.length - 1];
  if (!first || !last) return "";
  return `${linePath(points)} L${last.x.toFixed(2)} ${baseline} L${first.x.toFixed(2)} ${baseline} Z`;
}
