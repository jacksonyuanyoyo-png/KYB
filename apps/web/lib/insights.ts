import {
  effectiveOwnership, generateRequirements, personsToIdentify, validateOwnership,
  type AccountProfile, type Requirement, type ValidationIssue,
} from "@fcc/domain";
import { libraryRequirements, publishedAdjustments } from "@/lib/rules/library";
import type { CaseRecord, ProfileDraft, RuleLibraryState } from "@/lib/types";

export type Stage = "OWNERSHIP" | "DETAILS" | "DOCUMENTS" | "COMPLIANCE" | "DONE";

export const STAGES: Array<{ id: Stage; label: string; segment: string }> = [
  { id: "OWNERSHIP", label: "Ownership", segment: "" },
  { id: "DETAILS", label: "Account details", segment: "details" },
  { id: "DOCUMENTS", label: "Documents", segment: "documents" },
  { id: "COMPLIANCE", label: "Compliance review", segment: "review" },
];

export interface DetailsGap {
  field: "province" | "taxResidency" | "trustedContact" | "trustedContactName";
  message: string;
}

export function validateDetails(profile: ProfileDraft): DetailsGap[] {
  const gaps: DetailsGap[] = [];
  if (!profile.province) gaps.push({ field: "province", message: "Select the province or territory of registration." });
  if (!profile.taxResidency) gaps.push({ field: "taxResidency", message: "Select the entity's tax residency." });
  if (profile.trustedContact === null) gaps.push({ field: "trustedContact", message: "Record whether a Trusted Contact Person is designated." });
  if (profile.trustedContact && !profile.trustedContactName.trim()) gaps.push({ field: "trustedContactName", message: "Enter the Trusted Contact Person's name." });
  return gaps;
}

export function toAccountProfile(profile: ProfileDraft): AccountProfile {
  return {
    province: profile.province,
    taxResidency: profile.taxResidency ?? "CANADA",
    features: profile.features,
    trustedContact: profile.trustedContact ?? false,
  };
}

export interface ChecklistRow extends Requirement {
  custom: boolean;
}

export function checklistFor(record: CaseRecord, library?: RuleLibraryState): ChecklistRow[] {
  const account = { ...record, profile: toAccountProfile(record.profile) };
  const adjustment = library ? publishedAdjustments(library, record.ruleVersion) : { extras: [], disabled: [], overrides: {} };
  const generated = generateRequirements(account)
    .filter((item) => !adjustment.disabled.includes(item.id))
    .map((item) => ({ ...item, ...adjustment.overrides[item.id], custom: false }));
  const fromLibrary = libraryRequirements(adjustment.extras, account).map((item) => ({ ...item, custom: false }));
  const custom = record.customRequirements.map((item): ChecklistRow => ({
    id: item.id, section: "Additional Requirements", name: item.name, conditional: true,
    source: "Added by staff", reason: "Additional requirement recorded on this case.", partyIds: [], custom: true,
  }));
  return [...generated, ...fromLibrary, ...custom];
}

export function isCollected(record: CaseRecord, requirementId: string): boolean {
  const status = record.checklist[requirementId]?.status;
  return status === "RECEIVED" || status === "VERIFIED";
}

export interface CaseInsight {
  ownershipIssues: ValidationIssue[];
  detailsGaps: DetailsGap[];
  checklist: ChecklistRow[];
  collected: number;
  stage: Stage;
  blocker: string | null;
  identify: ReturnType<typeof personsToIdentify>;
  effective: Map<string, number>;
  openTasks: number;
}

export function analyze(record: CaseRecord, library?: RuleLibraryState): CaseInsight {
  const ownershipIssues = validateOwnership(record);
  const detailsGaps = validateDetails(record.profile);
  const checklist = checklistFor(record, library);
  const collected = checklist.filter((item) => isCollected(record, item.id)).length;
  const openTasks = record.tasks.filter((task) => !task.done).length;
  let stage: Stage;
  let blocker: string | null = null;
  if (record.status === "APPROVED") stage = "DONE";
  else if (record.status === "READY_FOR_COMPLIANCE") stage = "COMPLIANCE";
  else if (ownershipIssues.length) { stage = "OWNERSHIP"; blocker = ownershipIssues[0]?.message ?? null; }
  else if (detailsGaps.length) { stage = "DETAILS"; blocker = detailsGaps[0]?.message ?? null; }
  else { stage = "DOCUMENTS"; blocker = collected < checklist.length ? `${checklist.length - collected} document${checklist.length - collected === 1 ? "" : "s"} outstanding` : null; }
  if (record.status === "RETURNED" && openTasks) blocker = `${openTasks} compliance task${openTasks === 1 ? "" : "s"} open`;
  return {
    ownershipIssues, detailsGaps, checklist, collected, stage, blocker, openTasks,
    identify: personsToIdentify(record.parties),
    effective: effectiveOwnership(record.parties),
  };
}

export function stageIndex(stage: Stage): number {
  return stage === "DONE" ? STAGES.length : STAGES.findIndex((item) => item.id === stage);
}
