"use client";

import { ConflictError, GateError } from "@/lib/data/actions-errors";
import { getDb, rememberInsight, replaceDb } from "@/lib/data/store";
import type { CaseInsight, ChecklistRow, DetailsGap, Stage } from "@/lib/insights";
import { identity } from "@/lib/data/source";
import type { BuiltinOverride, AuditEvent, CaseRecord, Db, DocStatus, LibraryRule, ProfileDraft, ReviewTask, RuleLibraryState, TaskSource, User } from "@/lib/types";
import type { CaseStatus, EntityType, Party } from "@fcc/domain";

function baseUrl(): string {
  return (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
}

let libraryVersion = 1;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const who = identity();
  const headers = new Headers(init.headers);
  headers.set("X-User-Id", who.id);
  headers.set("X-User-Role", who.role);
  if (init.body && !(init.body instanceof FormData) && typeof init.body === "string") {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${baseUrl()}${path}`, { ...init, headers });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error ?? {};
    if (response.status === 409 || error.code === "VERSION_CONFLICT") throw new ConflictError();
    if (response.status === 422 || error.code === "GATE_FAILED") throw new GateError(error.message || "This action is blocked.");
    throw new Error(error.message || `Request failed (${response.status}).`);
  }
  return payload as T;
}

function toInsight(raw: Record<string, unknown>, parties: Party[]): CaseInsight {
  const identify = new Set((raw.identify as string[]) ?? []);
  const effective = new Map<string, number>(Object.entries((raw.effective as Record<string, number>) ?? {}).map(([key, value]) => [key, Number(value)]));
  return {
    ownershipIssues: (raw.ownershipIssues as CaseInsight["ownershipIssues"]) ?? [],
    detailsGaps: (raw.detailsGaps as DetailsGap[]) ?? [],
    checklist: ((raw.checklist as ChecklistRow[]) ?? []).map((item) => ({ ...item, custom: Boolean(item.custom), partyIds: item.partyIds ?? [] })),
    collected: Number(raw.collected ?? 0),
    stage: (raw.stage as Stage) ?? "OWNERSHIP",
    blocker: (raw.blocker as string | null) ?? null,
    identify: parties.filter((party) => identify.has(party.id)),
    effective,
    openTasks: Number(raw.openTasks ?? 0),
  };
}

function asCase(raw: Record<string, unknown>): CaseRecord {
  const checklist = (raw.checklist ?? {}) as Record<string, { status: DocStatus; documentIds?: string[]; updatedAt?: string; updatedBy?: string }>;
  return {
    id: String(raw.id),
    reference: String(raw.reference),
    version: Number(raw.version),
    legalName: String(raw.legalName),
    entityType: raw.entityType as EntityType,
    status: raw.status as CaseRecord["status"],
    ruleVersion: String(raw.ruleVersion),
    createdAt: String(raw.createdAt),
    updatedAt: String(raw.updatedAt),
    ownerId: String(raw.ownerId),
    jurisdiction: String(raw.jurisdiction ?? ""),
    registrationNumber: String(raw.registrationNumber ?? ""),
    dueDate: String(raw.dueDate ?? raw.updatedAt),
    parties: (raw.parties as Party[]) ?? [],
    profile: raw.profile as ProfileDraft,
    checklist: Object.fromEntries(Object.entries(checklist).map(([id, item]) => [id, {
      status: item.status,
      documentIds: item.documentIds ?? [],
      updatedAt: item.updatedAt,
      updatedBy: item.updatedBy,
    }])),
    customRequirements: (raw.customRequirements as CaseRecord["customRequirements"]) ?? [],
    documents: (raw.documents as CaseRecord["documents"]) ?? [],
    tasks: (raw.tasks as ReviewTask[]) ?? [],
  };
}

function installCase(payload: { case: Record<string, unknown>; insight?: Record<string, unknown> }): CaseRecord {
  const record = asCase(payload.case);
  if (payload.insight) rememberInsight(record.id, toInsight(payload.insight, record.parties));
  const db = getDb();
  const exists = db.cases.some((item) => item.id === record.id);
  replaceDb({
    ...db,
    cases: exists ? db.cases.map((item) => (item.id === record.id ? record : item)) : [record, ...db.cases],
  });
  return record;
}

function asLibrary(raw: Record<string, unknown>): RuleLibraryState {
  libraryVersion = Number(raw.version ?? libraryVersion);
  const draft = raw.draft as RuleLibraryState["draft"];
  return {
    publishedVersion: String(raw.publishedVersion),
    publishedExtras: (raw.publishedExtras as LibraryRule[]) ?? [],
    publishedDisabled: (raw.publishedDisabled as string[]) ?? [],
    publishedOverrides: (raw.publishedOverrides as RuleLibraryState["publishedOverrides"]) ?? {},
    draft: draft ?? null,
  };
}

function installLibrary(raw: Record<string, unknown>) {
  const db = getDb();
  replaceDb({ ...db, ruleLibrary: asLibrary(raw) });
}

async function refreshAudit() {
  const page = await request<{ items: AuditEvent[] }>("/api/v1/audit?limit=200");
  replaceDb({ ...getDb(), audit: page.items ?? [] });
}

export async function loadRemoteDb(): Promise<Db> {
  const who = identity();
  const [me, users, listed, library, audit] = await Promise.all([
    request<User>("/api/v1/me"),
    request<User[]>("/api/v1/users"),
    request<{ items: Array<{ id: string }> }>("/api/v1/cases?limit=500"),
    request<Record<string, unknown>>("/api/v1/rule-library"),
    request<{ items: AuditEvent[] }>("/api/v1/audit?limit=200"),
  ]);
  const details = await Promise.all(listed.items.map((item) => request<{ case: Record<string, unknown>; insight: Record<string, unknown> }>(`/api/v1/cases/${item.id}`)));
  libraryVersion = Number(library.version ?? 1);
  for (const item of details) {
    const record = asCase(item.case);
    if (item.insight) rememberInsight(record.id, toInsight(item.insight, record.parties));
  }
  return {
    sessionUserId: me.id || who.id,
    users,
    cases: details.map((item) => asCase(item.case)),
    audit: audit.items ?? [],
    ruleLibrary: asLibrary(library),
  };
}

type Uploadable = { name: string; size: number; type: string; blob?: Blob };

function fileBody(file: Uploadable): Blob {
  if (file.blob) return file.blob;
  if (typeof Blob !== "undefined" && file instanceof Blob) return file;
  throw new Error("This file has no bytes to upload.");
}

export async function apiCreateCase(input: {
  legalName: string;
  entityType: EntityType;
  jurisdiction: string;
  registrationNumber: string;
  ownerId: string;
  aiEntityType?: { suggested: EntityType; accepted: boolean };
}): Promise<CaseRecord> {
  const detail = await request<{ case: Record<string, unknown> }>("/api/v1/cases", {
    method: "POST",
    body: JSON.stringify(input),
  });
  const record = installCase(detail);
  await refreshAudit();
  return record;
}

export async function apiUpdateParties(record: CaseRecord, parties: Party[], summary: string, changes?: AuditEvent["changes"]) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/parties`, {
    method: "PUT",
    body: JSON.stringify({ version: record.version, parties, summary, changes }),
  });
  return installCase(detail);
}

export async function apiApplyAiParties(record: CaseRecord, parties: Party[], summary: string, model: string) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/ai-suggestions/accept`, {
    method: "POST",
    body: JSON.stringify({ version: record.version, parties, summary, model }),
  });
  return installCase(detail);
}

export async function apiRecordAiRejection(record: CaseRecord, summary: string, model: string) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/ai-suggestions/reject`, {
    method: "POST",
    body: JSON.stringify({ version: record.version, summary, model }),
  });
  return installCase(detail);
}

export async function apiUpdateProfile(record: CaseRecord, profile: ProfileDraft, change: { field: string; from: string; to: string }) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/profile`, {
    method: "PUT",
    body: JSON.stringify({ version: record.version, profile, change }),
  });
  return installCase(detail);
}

export async function apiSetChecklistStatus(record: CaseRecord, requirementId: string, status: DocStatus) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/checklist/${requirementId}`, {
    method: "PUT",
    body: JSON.stringify({ version: record.version, status }),
  });
  return installCase(detail);
}

export async function apiUploadDocuments(record: CaseRecord, requirementId: string | null, files: Uploadable[]) {
  const opened = await request<{ batchId: string; slots: Array<{ uploadId: string; url: string; headers: Record<string, string> }> }>(
    `/api/v1/cases/${record.id}/uploads`,
    {
      method: "POST",
      body: JSON.stringify({
        requirementId,
        files: files.map((file) => ({ fileName: file.name, sizeBytes: file.size, mimeType: file.type || "application/octet-stream" })),
      }),
    },
  );
  await Promise.all(opened.slots.map(async (slot, index) => {
    const url = slot.url.startsWith("http") ? slot.url : `${baseUrl()}${slot.url}`;
    const response = await fetch(url, { method: "PUT", headers: slot.headers, body: fileBody(files[index]!) });
    if (!response.ok) throw new Error("Upload failed.");
  }));
  const latest = getDb().cases.find((item) => item.id === record.id) ?? record;
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/documents`, {
    method: "POST",
    body: JSON.stringify({ version: latest.version, batchId: opened.batchId, uploadIds: opened.slots.map((slot) => slot.uploadId) }),
  });
  return installCase(detail);
}

export async function apiAssignDocument(record: CaseRecord, documentId: string, requirementId: string) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/documents/${documentId}/requirement`, {
    method: "PUT",
    body: JSON.stringify({ version: record.version, requirementId }),
  });
  return installCase(detail);
}

export async function apiAddCustomRequirement(record: CaseRecord, name: string) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/custom-requirements`, {
    method: "POST",
    body: JSON.stringify({ version: record.version, name }),
  });
  return installCase(detail);
}

export async function apiAddTasks(record: CaseRecord, tasks: Array<Pick<ReviewTask, "title" | "partyId" | "requirementId">>, source: TaskSource) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/tasks`, {
    method: "POST",
    body: JSON.stringify({ version: record.version, source, tasks }),
  });
  return installCase(detail);
}

export async function apiToggleTask(record: CaseRecord, taskId: string) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/tasks/${taskId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ version: record.version }),
  });
  return installCase(detail);
}

export async function apiChangeStatus(record: CaseRecord, status: CaseStatus, summary: string) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/status`, {
    method: "POST",
    body: JSON.stringify({ version: record.version, status, summary }),
  });
  return installCase(detail);
}

export async function apiComplianceDecision(record: CaseRecord, decision: "APPROVE" | "RETURN", comments: Array<Pick<ReviewTask, "title" | "partyId" | "requirementId">>) {
  const detail = await request<{ case: Record<string, unknown> }>(`/api/v1/cases/${record.id}/compliance-decision`, {
    method: "POST",
    body: JSON.stringify({ version: record.version, decision, comments }),
  });
  return installCase(detail);
}

async function libraryWrite(path: string, method: string, body?: unknown) {
  const payload = await request<Record<string, unknown>>(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  installLibrary(payload);
  await refreshAudit();
  return payload;
}

export async function apiStartRuleDraft() {
  const payload = await libraryWrite("/api/v1/rule-library/draft", "POST", { version: libraryVersion });
  const draft = payload.draft as { version?: string } | null;
  return draft?.version ?? "";
}

export async function apiSaveLibraryRule(rule: LibraryRule, mode: "add" | "edit") {
  if (mode === "add") await libraryWrite("/api/v1/rule-library/draft/extras", "POST", { version: libraryVersion, rule });
  else await libraryWrite(`/api/v1/rule-library/draft/extras/${rule.id}`, "PUT", { version: libraryVersion, rule });
  return true as const;
}

export async function apiRemoveLibraryRule(ruleId: string) {
  await libraryWrite(`/api/v1/rule-library/draft/extras/${ruleId}?version=${libraryVersion}`, "DELETE");
}

export async function apiSetBuiltinRetired(ruleId: string, retired: boolean) {
  await libraryWrite(`/api/v1/rule-library/draft/builtins/${ruleId}/retired`, "PUT", { version: libraryVersion, retired });
}

export async function apiSaveBuiltinOverride(ruleId: string, override: BuiltinOverride) {
  await libraryWrite(`/api/v1/rule-library/draft/builtins/${ruleId}/override`, "PUT", { version: libraryVersion, override });
  return true as const;
}

export async function apiDiscardRuleDraft() {
  await libraryWrite(`/api/v1/rule-library/draft?version=${libraryVersion}`, "DELETE");
}

export async function apiPublishRuleDraft() {
  await libraryWrite("/api/v1/rule-library/draft/publish", "POST", { version: libraryVersion });
}

export interface AssistantReply {
  suggestionId: string;
  kind: string;
  text: string;
  model: string;
  proposal: { legalName: string; ownershipPercent: number; title: string; isController: boolean; isSigningAuthority: boolean; isUsPerson: boolean; isPepHio: boolean; parentName: string } | null;
}

export async function apiAskAssistant(caseId: string, message: string, onDelta?: (text: string) => void): Promise<AssistantReply> {
  const who = identity();
  const headers = new Headers();
  headers.set("X-User-Id", who.id);
  headers.set("X-User-Role", who.role);
  headers.set("Content-Type", "application/json");
  headers.set("Accept", "text/event-stream");
  const response = await fetch(`${baseUrl()}/api/v1/cases/${caseId}/ai/assistant`, {
    method: "POST",
    headers,
    body: JSON.stringify({ message }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = (payload as { error?: { code?: string; message?: string } }).error ?? {};
    if (response.status === 409 || error.code === "VERSION_CONFLICT") throw new ConflictError();
    if (response.status === 422 || error.code === "GATE_FAILED") throw new GateError(error.message || "This action is blocked.");
    throw new Error(error.message || `Request failed (${response.status}).`);
  }
  if (!response.body) throw new Error("The assistant is unavailable.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let doneBody: AssistantReply | null = null;
  const take = (frame: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    const data = JSON.parse(dataLines.join("\n")) as { text?: string; message?: string };
    if (event === "delta" && data.text) onDelta?.(data.text);
    if (event === "done") doneBody = data as AssistantReply;
    if (event === "error") throw new Error(data.message || "The assistant is unavailable.");
  };
  while (true) {
    const step = await reader.read();
    if (step.done) break;
    buffer += decoder.decode(step.value, { stream: true });
    let split = buffer.indexOf("\n\n");
    while (split >= 0) {
      take(buffer.slice(0, split));
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf("\n\n");
    }
  }
  if (buffer.trim()) take(buffer);
  if (!doneBody) throw new Error("The assistant is unavailable.");
  return doneBody;
}

export async function apiClassify(legalName: string, notes: string) {
  return request<{ type: EntityType | null; confidence: number | null; reasons: string[] }>("/api/v1/ai/classify-entity", {
    method: "POST",
    body: JSON.stringify({ legalName, notes }),
  });
}

export async function apiExtractFormation(caseId: string, documentIds: string[]) {
  return request<{ entities: Array<Record<string, unknown>> }>(`/api/v1/cases/${caseId}/ai/extract-formation`, {
    method: "POST",
    body: JSON.stringify({ documentIds }),
  });
}

export async function apiPreReview(caseId: string) {
  return request<{ findings: Array<{ id: string; severity: "high" | "medium" | "low"; title: string; detail: string; requirementId?: string; partyId?: string }> }>(
    `/api/v1/cases/${caseId}/ai/pre-review`,
    { method: "POST" },
  );
}

export async function apiEvaluateRules(input: { entityType: string; taxResidency: string; features: string[]; trustedContact: boolean; usPerson: boolean; pep: boolean; target?: "PUBLISHED" | "DRAFT" }) {
  return request<{ requirements: Array<{ id: string; section: string; name: string; conditional: boolean; source: string; reason: string; partyIds?: string[] }> }>(
    "/api/v1/rule-library/evaluate",
    { method: "POST", body: JSON.stringify({ target: input.target ?? "PUBLISHED", input }) },
  );
}

export async function apiTaskTotal() {
  const page = await request<{ total: number }>("/api/v1/tasks?done=false&limit=1");
  return page.total ?? 0;
}

export async function apiSearch(query: string) {
  const q = encodeURIComponent(query);
  const [cases, entities] = await Promise.all([
    request<{ items: Array<Record<string, unknown>> }>(`/api/v1/cases?q=${q}&limit=6`),
    request<{ items: Array<Record<string, unknown>> }>(`/api/v1/entities?q=${q}&kind=ALL`),
  ]);
  return { cases: cases.items ?? [], entities: (entities.items ?? []).slice(0, 6) };
}

export async function apiEntities(kind: string, query: string) {
  const page = await request<{ items: Array<Record<string, unknown>>; counts: Record<string, number> }>(
    `/api/v1/entities?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(query)}`,
  );
  return page;
}

export async function apiDocuments(filter: string) {
  return request<{ items: Array<Record<string, unknown>>; counts: Record<string, number> }>(`/api/v1/documents?filter=${encodeURIComponent(filter)}`);
}

export async function apiAudit(filters: { action?: string; caseId?: string; actorId?: string }) {
  const params = new URLSearchParams();
  if (filters.action) params.set("action", filters.action);
  if (filters.caseId) params.set("caseId", filters.caseId);
  if (filters.actorId) params.set("actorId", filters.actorId);
  params.set("limit", "200");
  return request<{ items: AuditEvent[] }>(`/api/v1/audit?${params.toString()}`);
}

export async function apiComplianceQueue(status: string) {
  return request<{ items: Array<Record<string, unknown>>; counts: Record<string, number> }>(`/api/v1/compliance/queue?status=${encodeURIComponent(status)}`);
}

export async function apiContentUrl(caseId: string, documentId: string) {
  const payload = await request<{ url: string }>(`/api/v1/cases/${caseId}/documents/${documentId}/content-url`);
  return payload.url.startsWith("http") ? payload.url : `${baseUrl()}${payload.url}`;
}
