import type { AccountCase, AccountFeature, EntityType, Party, TaxResidency } from "@fcc/domain";

export type Role = "ADVISOR" | "OPERATIONS" | "COMPLIANCE" | "ADMIN";

export interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
  team: string;
}

export interface ProfileDraft {
  province: string;
  taxResidency: TaxResidency | null;
  features: AccountFeature[];
  trustedContact: boolean | null;
  trustedContactName: string;
}

export type DocStatus = "MISSING" | "REQUESTED" | "RECEIVED" | "VERIFIED" | "REJECTED";
export type ExtractionStatus = "NONE" | "PROCESSING" | "EXTRACTED" | "FAILED";

export interface CaseDocument {
  id: string;
  requirementId: string | null;
  fileName: string;
  sizeBytes: number;
  mimeType: string;
  uploadedBy: string;
  uploadedAt: string;
  extraction: ExtractionStatus;
  storageState?: string;
}

export interface ChecklistItemState {
  status: DocStatus;
  documentIds: string[];
  updatedAt?: string;
  updatedBy?: string;
}

export interface CustomRequirement {
  id: string;
  name: string;
  createdBy: string;
  createdAt: string;
}

export type TaskSource = "COMPLIANCE" | "AI" | "MANUAL";

export interface ReviewTask {
  id: string;
  title: string;
  partyId?: string;
  requirementId?: string;
  done: boolean;
  source: TaskSource;
  createdBy: string;
  createdAt: string;
}

export interface CaseRecord extends Omit<AccountCase, "profile"> {
  reference: string;
  ownerId: string;
  jurisdiction: string;
  registrationNumber: string;
  profile: ProfileDraft;
  checklist: Record<string, ChecklistItemState>;
  customRequirements: CustomRequirement[];
  documents: CaseDocument[];
  tasks: ReviewTask[];
  dueDate: string;
}

export type AuditAction =
  | "CASE_CREATED"
  | "OWNERSHIP_UPDATED"
  | "PROFILE_UPDATED"
  | "DOCUMENT_UPLOADED"
  | "CHECKLIST_UPDATED"
  | "REQUIREMENT_ADDED"
  | "STATUS_CHANGED"
  | "COMPLIANCE_DECISION"
  | "AI_SUGGESTION"
  | "TASK_UPDATED"
  | "RULE_LIBRARY";

export interface AuditChange {
  field: string;
  from: string;
  to: string;
}

export interface AuditEvent {
  id: string;
  caseId: string;
  actorId: string;
  action: AuditAction;
  summary: string;
  at: string;
  version: number;
  changes?: AuditChange[];
  ai?: { model: string; accepted: boolean | null; decision?: "suggested" | "accepted" | "rejected"; ruleVersion: string };
}

export type RuleTriggerKind = "ALWAYS" | "ENTITY" | "TAX" | "FEATURE" | "PERSON" | "PEP" | "US_PERSON" | "TRUSTED_CONTACT";

export interface RuleTrigger {
  kind: RuleTriggerKind;
  entityTypes?: EntityType[];
  taxResidencies?: TaxResidency[];
  feature?: AccountFeature;
}

/** A rule added on top of the coded demonstrator. Builtin triggers stay in the domain package. */
export interface LibraryRule {
  id: string;
  name: string;
  section: string;
  conditional: boolean;
  source: string;
  reason: string;
  enabled: boolean;
  trigger: RuleTrigger;
}

/** Editable fields of a coded rule. Its trigger stays in the domain package. */
export interface BuiltinOverride {
  name: string;
  section: string;
  conditional: boolean;
  source: string;
  reason: string;
}

export interface RuleDraft {
  version: string;
  extras: LibraryRule[];
  disabled: string[];
  overrides: Record<string, BuiltinOverride>;
}

export interface RuleLibraryState {
  /** Version assigned to newly created cases. */
  publishedVersion: string;
  publishedExtras: LibraryRule[];
  /** Builtin rule ids omitted from publishedVersion. Ignored while publishedVersion is still the coded demonstrator. */
  publishedDisabled: string[];
  publishedOverrides: Record<string, BuiltinOverride>;
  draft: null | RuleDraft;
}

export interface Db {
  sessionUserId: string;
  users: User[];
  cases: CaseRecord[];
  audit: AuditEvent[];
  ruleLibrary: RuleLibraryState;
}

export type { Party };
