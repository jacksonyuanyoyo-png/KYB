"use client";

import { RULE_VERSION, validateOwnership, type CaseStatus, type EntityType, type Party } from "@fcc/domain";
import { ConflictError, GateError } from "@/lib/data/actions-errors";
import {
  apiAddCustomRequirement, apiAddTasks, apiApplyAiParties, apiAssignDocument, apiChangeStatus,
  apiComplianceDecision, apiCreateCase, apiDiscardRuleDraft, apiPublishRuleDraft, apiRecordAiRejection,
  apiRemoveLibraryRule, apiSaveBuiltinOverride, apiSaveLibraryRule, apiSetBuiltinRetired, apiSetChecklistStatus,
  apiStartRuleDraft, apiToggleTask, apiUpdateParties, apiUpdateProfile, apiUploadDocuments,
} from "@/lib/data/api";
import { rememberIdentity, isApiMode } from "@/lib/data/source";
import { getDb, replaceDb, setDb } from "@/lib/data/store";
import { uid } from "@/lib/format";
import { analyze } from "@/lib/insights";
import { initialLibrary } from "@/lib/rules/library";
import { STATUS_LABELS } from "@/lib/labels";
import type { AuditEvent, BuiltinOverride, CaseDocument, CaseRecord, DocStatus, LibraryRule, ProfileDraft, ReviewTask, Role, RuleDraft, RuleLibraryState, TaskSource } from "@/lib/types";

export { ConflictError, GateError };

type EventInput = Omit<AuditEvent, "id" | "caseId" | "actorId" | "at" | "version">;

const LATENCY_MS = 120;
const pause = () => new Promise((resolve) => setTimeout(resolve, LATENCY_MS));

function sessionUser() {
  const db = getDb();
  return db.users.find((user) => user.id === db.sessionUserId) ?? db.users[0]!;
}

async function mutateCase(caseId: string, baseVersion: number, mutate: (record: CaseRecord) => CaseRecord, event: EventInput): Promise<CaseRecord> {
  await pause();
  const db = getDb();
  const current = db.cases.find((item) => item.id === caseId);
  if (!current) throw new Error("Case not found.");
  if (current.version !== baseVersion) throw new ConflictError();
  const at = new Date().toISOString();
  const actor = sessionUser();
  const next = { ...mutate(current), version: current.version + 1, updatedAt: at };
  const audit: AuditEvent = { id: uid("evt"), caseId, actorId: actor.id, at, version: next.version, ...event };
  setDb({ ...db, cases: db.cases.map((item) => (item.id === caseId ? next : item)), audit: [audit, ...db.audit] });
  return next;
}

export function switchUser(userId: string) {
  if (isApiMode()) {
    const user = getDb().users.find((item) => item.id === userId);
    if (!user) return;
    rememberIdentity(user.id, user.role);
    replaceDb({ ...getDb(), sessionUserId: user.id });
    void import("@/lib/data/api").then(async ({ loadRemoteDb }) => {
      const { replaceDb: write } = await import("@/lib/data/store");
      write(await loadRemoteDb());
    });
    return;
  }
  setDb({ ...getDb(), sessionUserId: userId });
}

export interface NewCaseInput {
  legalName: string;
  entityType: EntityType;
  jurisdiction: string;
  registrationNumber: string;
  ownerId: string;
  aiEntityType?: { suggested: EntityType; accepted: boolean };
}

export async function createCase(input: NewCaseInput): Promise<CaseRecord> {
  if (isApiMode()) return apiCreateCase(input);
  await pause();
  const db = getDb();
  const actor = sessionUser();
  const at = new Date().toISOString();
  const sequence = Math.max(...db.cases.map((item) => Number(item.reference.split("-").at(-1)) || 0)) + 1;
  const id = uid("case");
  const record: CaseRecord = {
    id, reference: `FCC-2026-${String(sequence).padStart(4, "0")}`, version: 1, legalName: input.legalName.trim(), entityType: input.entityType,
    status: "BUILDING", ruleVersion: db.ruleLibrary?.publishedVersion ?? RULE_VERSION, createdAt: at, updatedAt: at, ownerId: input.ownerId,
    jurisdiction: input.jurisdiction, registrationNumber: input.registrationNumber,
    parties: [{ id: uid("p"), parentId: null, kind: "ENTITY", legalName: input.legalName.trim(), entityType: input.entityType, ownershipPercent: 100, isController: false, isSigningAuthority: false, isUsPerson: false, isPepHio: false }],
    profile: { province: "", taxResidency: null, features: [], trustedContact: null, trustedContactName: "" },
    checklist: {}, customRequirements: [], documents: [], tasks: [],
    dueDate: new Date(Date.now() + 10 * 86_400_000).toISOString(),
  };
  const events: AuditEvent[] = [{ id: uid("evt"), caseId: id, actorId: actor.id, action: "CASE_CREATED", summary: `Created case for ${record.legalName}`, at, version: 1 }];
  if (input.aiEntityType) {
    events.unshift({
      id: uid("evt"), caseId: id, actorId: actor.id, action: "AI_SUGGESTION", at, version: 1,
      summary: `Entity type suggested: ${input.aiEntityType.suggested} (${input.aiEntityType.accepted ? "accepted" : "overridden"})`,
      ai: { model: "entity-classifier-demo", accepted: input.aiEntityType.accepted, ruleVersion: RULE_VERSION },
    });
  }
  setDb({ ...db, cases: [record, ...db.cases], audit: [...events, ...db.audit] });
  return record;
}

export function updateParties(record: CaseRecord, parties: Party[], summary: string, changes?: AuditEvent["changes"]) {
  if (isApiMode()) return apiUpdateParties(record, parties, summary, changes);
  return mutateCase(record.id, record.version, (current) => ({ ...current, parties }), { action: "OWNERSHIP_UPDATED", summary, changes });
}

export function applyAiParties(record: CaseRecord, parties: Party[], summary: string, model: string) {
  if (isApiMode()) return apiApplyAiParties(record, parties, summary, model);
  return mutateCase(record.id, record.version, (current) => ({ ...current, parties }), {
    action: "AI_SUGGESTION", summary, ai: { model, accepted: true, ruleVersion: record.ruleVersion },
  });
}

export function recordAiRejection(record: CaseRecord, summary: string, model: string) {
  if (isApiMode()) return apiRecordAiRejection(record, summary, model);
  return mutateCase(record.id, record.version, (current) => current, {
    action: "AI_SUGGESTION", summary, ai: { model, accepted: false, ruleVersion: record.ruleVersion },
  });
}

export function updateProfile(record: CaseRecord, profile: ProfileDraft, change: { field: string; from: string; to: string }) {
  if (isApiMode()) return apiUpdateProfile(record, profile, change);
  return mutateCase(record.id, record.version, (current) => ({ ...current, profile }), {
    action: "PROFILE_UPDATED", summary: `Updated ${change.field.toLowerCase()}`, changes: [change],
  });
}

export function setChecklistStatus(record: CaseRecord, requirementId: string, requirementName: string, status: DocStatus) {
  if (isApiMode()) return apiSetChecklistStatus(record, requirementId, status);
  const actor = sessionUser();
  const previous = record.checklist[requirementId]?.status ?? "MISSING";
  return mutateCase(record.id, record.version, (current) => ({
    ...current,
    checklist: { ...current.checklist, [requirementId]: { documentIds: current.checklist[requirementId]?.documentIds ?? [], status, updatedAt: new Date().toISOString(), updatedBy: actor.id } },
  }), { action: "CHECKLIST_UPDATED", summary: `${requirementName}: ${previous.toLowerCase()} → ${status.toLowerCase()}`, changes: [{ field: requirementName, from: previous, to: status }] });
}

export function uploadDocuments(record: CaseRecord, requirementId: string | null, files: Array<Pick<File, "name" | "size" | "type"> & { blob?: Blob }>) {
  if (isApiMode()) return apiUploadDocuments(record, requirementId, files);
  const actor = sessionUser();
  const at = new Date().toISOString();
  const documents: CaseDocument[] = files.map((file) => ({
    id: uid("doc"), requirementId, fileName: file.name, sizeBytes: file.size, mimeType: file.type || "application/octet-stream",
    uploadedBy: actor.id, uploadedAt: at, extraction: "PROCESSING",
  }));
  const updated = mutateCase(record.id, record.version, (current) => {
    const checklist = { ...current.checklist };
    if (requirementId) {
      const existing = checklist[requirementId];
      checklist[requirementId] = {
        status: existing?.status === "VERIFIED" ? "VERIFIED" : "RECEIVED",
        documentIds: [...(existing?.documentIds ?? []), ...documents.map((item) => item.id)], updatedAt: at, updatedBy: actor.id,
      };
    }
    return { ...current, documents: [...current.documents, ...documents], checklist };
  }, { action: "DOCUMENT_UPLOADED", summary: `Uploaded ${files.map((file) => file.name).join(", ")}` });
  setTimeout(() => {
    const db = getDb();
    setDb({
      ...db,
      cases: db.cases.map((item) => item.id !== record.id ? item : {
        ...item, documents: item.documents.map((docItem) => documents.some((created) => created.id === docItem.id) ? { ...docItem, extraction: "EXTRACTED" } : docItem),
      }),
    });
  }, 2200);
  return updated;
}

export function assignDocument(record: CaseRecord, documentId: string, requirementId: string, requirementName: string) {
  if (isApiMode()) return apiAssignDocument(record, documentId, requirementId);
  const actor = sessionUser();
  const document = record.documents.find((item) => item.id === documentId);
  return mutateCase(record.id, record.version, (current) => {
    const existing = current.checklist[requirementId];
    return {
      ...current,
      documents: current.documents.map((item) => (item.id === documentId ? { ...item, requirementId } : item)),
      checklist: {
        ...current.checklist,
        [requirementId]: { status: existing?.status === "VERIFIED" ? "VERIFIED" : "RECEIVED", documentIds: [...(existing?.documentIds ?? []), documentId], updatedAt: new Date().toISOString(), updatedBy: actor.id },
      },
    };
  }, { action: "CHECKLIST_UPDATED", summary: `Linked ${document?.fileName ?? "document"} to ${requirementName}` });
}

export function addCustomRequirement(record: CaseRecord, name: string) {
  if (isApiMode()) return apiAddCustomRequirement(record, name);
  const actor = sessionUser();
  return mutateCase(record.id, record.version, (current) => ({
    ...current, customRequirements: [...current.customRequirements, { id: uid("req"), name, createdBy: actor.id, createdAt: new Date().toISOString() }],
  }), { action: "REQUIREMENT_ADDED", summary: `Added additional requirement “${name}”` });
}

export function addTasks(record: CaseRecord, tasks: Array<Pick<ReviewTask, "title" | "partyId" | "requirementId">>, source: TaskSource) {
  if (isApiMode()) return apiAddTasks(record, tasks, source);
  const actor = sessionUser();
  const at = new Date().toISOString();
  return mutateCase(record.id, record.version, (current) => ({
    ...current, tasks: [...current.tasks, ...tasks.map((task) => ({ ...task, id: uid("task"), done: false, source, createdBy: actor.id, createdAt: at }))],
  }), { action: "TASK_UPDATED", summary: `Created ${tasks.length} follow-up task${tasks.length === 1 ? "" : "s"}` });
}

export function toggleTask(record: CaseRecord, taskId: string) {
  if (isApiMode()) return apiToggleTask(record, taskId);
  const task = record.tasks.find((item) => item.id === taskId);
  return mutateCase(record.id, record.version, (current) => ({
    ...current, tasks: current.tasks.map((item) => (item.id === taskId ? { ...item, done: !item.done } : item)),
  }), { action: "TASK_UPDATED", summary: `${task?.done ? "Reopened" : "Completed"} task: ${task?.title ?? ""}` });
}

export function changeStatus(record: CaseRecord, status: CaseStatus, summary: string) {
  if (isApiMode()) return apiChangeStatus(record, status, summary);
  if (status === "READY_FOR_COMPLIANCE") {
    const insight = analyze(record, getDb().ruleLibrary ?? initialLibrary());
    if (validateOwnership(record).length) throw new GateError("Ownership structure is incomplete.");
    if (insight.detailsGaps.length) throw new GateError("Account details are incomplete.");
    if (insight.collected < insight.checklist.length) throw new GateError("All checklist items must be collected first.");
    if (insight.openTasks) throw new GateError("Resolve open follow-up tasks first.");
  }
  return mutateCase(record.id, record.version, (current) => ({ ...current, status }), {
    action: "STATUS_CHANGED", summary, changes: [{ field: "Status", from: STATUS_LABELS[record.status], to: STATUS_LABELS[status] }],
  });
}

export function complianceDecision(record: CaseRecord, decision: "APPROVE" | "RETURN", comments: Array<Pick<ReviewTask, "title" | "partyId" | "requirementId">>) {
  if (isApiMode()) return apiComplianceDecision(record, decision, comments);
  const actor = sessionUser();
  const at = new Date().toISOString();
  const status: CaseStatus = decision === "APPROVE" ? "APPROVED" : "RETURNED";
  return mutateCase(record.id, record.version, (current) => ({
    ...current, status,
    tasks: [...current.tasks, ...comments.map((comment) => ({ ...comment, id: uid("task"), done: false, source: "COMPLIANCE" as const, createdBy: actor.id, createdAt: at }))],
    checklist: decision === "APPROVE"
      ? Object.fromEntries(Object.entries(current.checklist).map(([key, value]) => [key, value.status === "RECEIVED" ? { ...value, status: "VERIFIED" as const, updatedAt: at, updatedBy: actor.id } : value]))
      : current.checklist,
  }), {
    action: "COMPLIANCE_DECISION",
    summary: decision === "APPROVE" ? "Approved by Compliance" : `Returned to advisor with ${comments.length} comment${comments.length === 1 ? "" : "s"}`,
    changes: [{ field: "Status", from: STATUS_LABELS[record.status], to: STATUS_LABELS[status] }],
  });
}

function requireRole(role: Role) {
  const actor = sessionUser();
  if (actor.role !== role) throw new Error(role === "ADMIN" ? "Only an admin can draft rules." : "Only Compliance can publish a rule version.");
  return actor;
}

function libraryOf(db: ReturnType<typeof getDb>): RuleLibraryState {
  return db.ruleLibrary ?? initialLibrary();
}

function blankDraft(library: RuleLibraryState): RuleDraft {
  return {
    version: `draft-${new Date().toISOString().slice(0, 10)}`,
    extras: library.publishedExtras.map((rule) => ({ ...rule })),
    disabled: [...library.publishedDisabled],
    overrides: { ...(library.publishedOverrides ?? {}) },
  };
}

function writeLibrary(library: RuleLibraryState, summary: string) {
  const db = getDb();
  const actor = sessionUser();
  setDb({
    ...db,
    ruleLibrary: library,
    audit: [{ id: uid("evt"), caseId: "rule-library", actorId: actor.id, action: "RULE_LIBRARY", summary, at: new Date().toISOString(), version: 1 }, ...db.audit],
  });
}

export async function startRuleDraft() {
  if (isApiMode()) return apiStartRuleDraft();
  await pause();
  const actor = requireRole("ADMIN");
  const library = libraryOf(getDb());
  if (library.draft) return library.draft.version;
  const draft = blankDraft(library);
  writeLibrary({ ...library, draft }, `${actor.name} started rule draft ${draft.version}`);
  return draft.version;
}

export async function saveLibraryRule(rule: LibraryRule, mode: "add" | "edit"): Promise<true> {
  if (isApiMode()) return apiSaveLibraryRule(rule, mode);
  await pause();
  requireRole("ADMIN");
  const library = libraryOf(getDb());
  const draft = library.draft ?? blankDraft(library);
  const extras = mode === "add" ? [...draft.extras, rule] : draft.extras.map((item) => (item.id === rule.id ? rule : item));
  writeLibrary({ ...library, draft: { ...draft, extras } }, mode === "add" ? `Added draft rule “${rule.name}”` : `Updated draft rule “${rule.name}”`);
  return true;
}

export async function removeLibraryRule(ruleId: string) {
  if (isApiMode()) return apiRemoveLibraryRule(ruleId);
  await pause();
  requireRole("ADMIN");
  const library = libraryOf(getDb());
  if (!library.draft) throw new Error("Start a draft before removing a rule.");
  const rule = library.draft.extras.find((item) => item.id === ruleId);
  writeLibrary({ ...library, draft: { ...library.draft, extras: library.draft.extras.filter((item) => item.id !== ruleId) } }, `Removed draft rule “${rule?.name ?? ruleId}”`);
}

export async function setBuiltinRetired(ruleId: string, retired: boolean) {
  if (isApiMode()) return apiSetBuiltinRetired(ruleId, retired);
  await pause();
  requireRole("ADMIN");
  const library = libraryOf(getDb());
  const draft = library.draft ?? blankDraft(library);
  const disabled = retired ? [...new Set([...draft.disabled, ruleId])] : draft.disabled.filter((id) => id !== ruleId);
  writeLibrary({ ...library, draft: { ...draft, disabled } }, retired ? `Retired builtin rule ${ruleId} in the draft` : `Restored builtin rule ${ruleId}`);
}

export async function saveBuiltinOverride(ruleId: string, override: BuiltinOverride): Promise<true> {
  if (isApiMode()) return apiSaveBuiltinOverride(ruleId, override);
  await pause();
  requireRole("ADMIN");
  const library = libraryOf(getDb());
  const draft = library.draft ?? blankDraft(library);
  writeLibrary({ ...library, draft: { ...draft, overrides: { ...(draft.overrides ?? {}), [ruleId]: override } } }, `Updated builtin rule “${override.name}”`);
  return true;
}

export async function discardRuleDraft() {
  if (isApiMode()) return apiDiscardRuleDraft();
  await pause();
  requireRole("ADMIN");
  const library = libraryOf(getDb());
  writeLibrary({ ...library, draft: null }, "Discarded the unpublished rule draft");
}

export async function publishRuleDraft() {
  if (isApiMode()) return apiPublishRuleDraft();
  await pause();
  requireRole("COMPLIANCE");
  const library = libraryOf(getDb());
  if (!library.draft) throw new Error("There is no draft to publish.");
  const version = library.draft.version.replace(/^draft-/, "published-");
  writeLibrary({
    publishedVersion: version,
    publishedExtras: library.draft.extras.filter((rule) => rule.enabled),
    publishedDisabled: library.draft.disabled,
    publishedOverrides: library.draft.overrides ?? {},
    draft: null,
  }, `Published rule version ${version}. New cases use it; existing cases keep their pinned version.`);
}
