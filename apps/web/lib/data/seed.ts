import { RULE_VERSION, type EntityType, type Party } from "@fcc/domain";
import { initialLibrary } from "@/lib/rules/library";
import type { AuditEvent, CaseDocument, CaseRecord, ChecklistItemState, Db, DocStatus, ProfileDraft, User } from "@/lib/types";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

export const USERS: User[] = [
  { id: "u-advisor", name: "Sarah Whitfield", email: "sarah.whitfield@fidelity.ca", role: "ADVISOR", team: "WI Toronto" },
  { id: "u-ops", name: "Marcus Lee", email: "marcus.lee@fidelity.ca", role: "OPERATIONS", team: "Account Operations" },
  { id: "u-compliance", name: "Priya Raman", email: "priya.raman@fidelity.ca", role: "COMPLIANCE", team: "Compliance Pre-review" },
  { id: "u-admin", name: "Daniel Okafor", email: "daniel.okafor@fidelity.ca", role: "ADMIN", team: "Platform Admin" },
  { id: "u-advisor-2", name: "Julien Tremblay", email: "julien.tremblay@fidelity.ca", role: "ADVISOR", team: "PI Montréal" },
];

type PartyInput = Partial<Party> & Pick<Party, "id" | "legalName" | "kind">;

function party(parentId: string | null, input: PartyInput): Party {
  return {
    parentId, ownershipPercent: 0, isController: false, isSigningAuthority: false, isUsPerson: false, isPepHio: false,
    country: input.kind === "PERSON" ? "Canada" : undefined, ...input,
  };
}

function profile(input: Partial<ProfileDraft> = {}): ProfileDraft {
  return { province: "", taxResidency: null, features: [], trustedContact: null, trustedContactName: "", ...input };
}

function doc(id: string, requirementId: string | null, fileName: string, at: number, uploadedBy = "u-advisor", sizeBytes = 420_000): CaseDocument {
  return { id, requirementId, fileName, sizeBytes, mimeType: "application/pdf", uploadedBy, uploadedAt: new Date(at).toISOString(), extraction: "EXTRACTED" };
}

function items(entries: Array<[string, DocStatus, string[]?]>, at: number, by = "u-advisor"): Record<string, ChecklistItemState> {
  return Object.fromEntries(entries.map(([id, status, documentIds]) => [id, { status, documentIds: documentIds ?? [], updatedAt: new Date(at).toISOString(), updatedBy: by }]));
}

interface CaseInput {
  id: string; reference: string; legalName: string; entityType: EntityType; status: CaseRecord["status"];
  ownerId: string; jurisdiction: string; registrationNumber: string; createdDaysAgo: number; updatedHoursAgo: number; dueInDays: number;
  version: number; parties: Party[]; profile: ProfileDraft; documents?: CaseDocument[]; checklist?: Record<string, ChecklistItemState>;
  tasks?: CaseRecord["tasks"]; customRequirements?: CaseRecord["customRequirements"];
}

function record(now: number, input: CaseInput): CaseRecord {
  return {
    id: input.id, reference: input.reference, version: input.version, legalName: input.legalName, entityType: input.entityType,
    status: input.status, parties: input.parties, profile: input.profile, ruleVersion: RULE_VERSION,
    createdAt: new Date(now - input.createdDaysAgo * DAY).toISOString(), updatedAt: new Date(now - input.updatedHoursAgo * HOUR).toISOString(),
    ownerId: input.ownerId, jurisdiction: input.jurisdiction, registrationNumber: input.registrationNumber,
    checklist: input.checklist ?? {}, customRequirements: input.customRequirements ?? [], documents: input.documents ?? [], tasks: input.tasks ?? [],
    dueDate: new Date(now + input.dueInDays * DAY).toISOString(),
  };
}

export function seedDb(now = Date.now()): Db {
  const t = (hoursAgo: number) => now - hoursAgo * HOUR;

  const cases: CaseRecord[] = [
    record(now, {
      id: "case-0139", reference: "FCC-2026-0139", legalName: "Maple Ridge Holdings Inc.", entityType: "corporation", status: "DOCS_REQUESTED",
      ownerId: "u-advisor", jurisdiction: "Ontario (OBCA)", registrationNumber: "OBCA 2748113", createdDaysAgo: 6, updatedHoursAgo: 3, dueInDays: 4, version: 9,
      parties: [
        party(null, { id: "mr-root", kind: "ENTITY", legalName: "Maple Ridge Holdings Inc.", entityType: "corporation", ownershipPercent: 100 }),
        party("mr-root", { id: "mr-alice", kind: "PERSON", legalName: "Alice Chen", title: "Director", ownershipPercent: 40, isController: true, isSigningAuthority: true }),
        party("mr-root", { id: "mr-harbour", kind: "ENTITY", legalName: "Harbourview Capital Ltd.", entityType: "corporation", country: "Canada", ownershipPercent: 35 }),
        party("mr-harbour", { id: "mr-raj", kind: "PERSON", legalName: "Raj Patel", title: "Shareholder", ownershipPercent: 70 }),
        party("mr-harbour", { id: "mr-mei", kind: "PERSON", legalName: "Mei Lin", country: "United States", title: "Shareholder", ownershipPercent: 30, isUsPerson: true }),
        party("mr-root", { id: "mr-david", kind: "PERSON", legalName: "David Okoye", title: "Officer", ownershipPercent: 25, isPepHio: true }),
      ],
      profile: profile({ province: "ON", taxResidency: "MIXED", features: ["MARGIN", "OPTIONS"], trustedContact: true, trustedContactName: "Helen Chen" }),
      documents: [
        doc("d-1", "naaf", "NAAF_MapleRidge_signed.pdf", t(70)),
        doc("d-2", "formation", "Articles_of_Incorporation_2748113.pdf", t(70), "u-advisor", 1_820_000),
        doc("d-3", null, "Shareholder_Register_2026.pdf", t(69), "u-advisor", 260_000),
        doc("d-4", "resolution", "Board_Resolution_Trading_Authority.pdf", t(26)),
        doc("d-5", "margin", "Margin_Agreement_signed.pdf", t(5), "u-ops"),
      ],
      checklist: items([
        ["naaf", "VERIFIED", ["d-1"]], ["formation", "RECEIVED", ["d-2"]], ["resolution", "RECEIVED", ["d-4"]], ["margin", "RECEIVED", ["d-5"]],
        ["beneficial-owner", "REQUESTED"], ["identity", "REQUESTED"], ["pep", "REQUESTED"], ["w9", "REQUESTED"], ["rc519", "REQUESTED"],
      ], t(5)),
    }),
    record(now, {
      id: "case-0142", reference: "FCC-2026-0142", legalName: "Northwind Community Foundation", entityType: "charity", status: "BUILDING",
      ownerId: "u-advisor", jurisdiction: "Canada (CNCA)", registrationNumber: "BN 81920 4471 RR0001", createdDaysAgo: 2, updatedHoursAgo: 1, dueInDays: 9, version: 4,
      parties: [
        party(null, { id: "nw-root", kind: "ENTITY", legalName: "Northwind Community Foundation", entityType: "charity", ownershipPercent: 100 }),
        party("nw-root", { id: "nw-grace", kind: "PERSON", legalName: "Grace Morrison", title: "Director", isController: true, isSigningAuthority: true }),
        party("nw-root", { id: "nw-omar", kind: "PERSON", legalName: "Omar Haddad", title: "Director", isController: true }),
        party("nw-root", { id: "nw-lena", kind: "PERSON", legalName: "Lena Kowalski", title: "Officer", isSigningAuthority: true }),
      ],
      profile: profile({ taxResidency: "CANADA", features: [] }),
    }),
    record(now, {
      id: "case-0137", reference: "FCC-2026-0137", legalName: "Thompson Family Trust", entityType: "trust", status: "READY_FOR_COMPLIANCE",
      ownerId: "u-advisor-2", jurisdiction: "British Columbia", registrationNumber: "Trust deed 2019-03-14", createdDaysAgo: 11, updatedHoursAgo: 20, dueInDays: 1, version: 14,
      parties: [
        party(null, { id: "tf-root", kind: "ENTITY", legalName: "Thompson Family Trust", entityType: "trust", ownershipPercent: 100 }),
        party("tf-root", { id: "tf-robert", kind: "PERSON", legalName: "Robert Thompson", title: "Trustee", isController: true, isSigningAuthority: true }),
        party("tf-root", { id: "tf-emily", kind: "PERSON", legalName: "Emily Thompson", title: "Beneficiary", ownershipPercent: 50 }),
        party("tf-root", { id: "tf-james", kind: "PERSON", legalName: "James Thompson", title: "Beneficiary", ownershipPercent: 50 }),
      ],
      profile: profile({ province: "BC", taxResidency: "CANADA", features: ["COD_DVP"], trustedContact: false }),
      documents: [
        doc("t-1", "naaf", "NAAF_Thompson_Trust.pdf", t(120), "u-advisor-2"),
        doc("t-2", "formation", "Trust_Deed_2019.pdf", t(120), "u-advisor-2", 2_400_000),
        doc("t-3", "beneficial-owner", "Beneficial_Ownership_Declaration.pdf", t(48), "u-advisor-2"),
        doc("t-4", "identity", "ID_Verification_Pack.pdf", t(30), "u-ops", 3_100_000),
        doc("t-5", "cod-dvp", "DVP_Settlement_Instructions.pdf", t(22), "u-ops"),
      ],
      checklist: items([
        ["naaf", "VERIFIED", ["t-1"]], ["formation", "VERIFIED", ["t-2"]], ["beneficial-owner", "RECEIVED", ["t-3"]], ["identity", "RECEIVED", ["t-4"]], ["cod-dvp", "RECEIVED", ["t-5"]],
      ], t(22), "u-ops"),
    }),
    record(now, {
      id: "case-0131", reference: "FCC-2026-0131", legalName: "Lakeshore Condominium Corporation No. 482", entityType: "condo", status: "BUILDING",
      ownerId: "u-advisor", jurisdiction: "Ontario (Condominium Act)", registrationNumber: "TSCC 482", createdDaysAgo: 1, updatedHoursAgo: 6, dueInDays: 12, version: 1,
      parties: [party(null, { id: "lc-root", kind: "ENTITY", legalName: "Lakeshore Condominium Corporation No. 482", entityType: "condo", ownershipPercent: 100 })],
      profile: profile(),
    }),
    record(now, {
      id: "case-0128", reference: "FCC-2026-0128", legalName: "Pacific Rim Ventures LP", entityType: "partnership", status: "RETURNED",
      ownerId: "u-advisor-2", jurisdiction: "British Columbia", registrationNumber: "LP1180042", createdDaysAgo: 15, updatedHoursAgo: 28, dueInDays: -1, version: 17,
      parties: [
        party(null, { id: "pr-root", kind: "ENTITY", legalName: "Pacific Rim Ventures LP", entityType: "partnership", ownershipPercent: 100 }),
        party("pr-root", { id: "pr-gp", kind: "ENTITY", legalName: "Pacific Rim GP Inc.", entityType: "corporation", country: "Canada", title: "General Partner", ownershipPercent: 1, isController: true }),
        party("pr-root", { id: "pr-wei", kind: "PERSON", legalName: "Wei Zhang", country: "Singapore", title: "Limited Partner", ownershipPercent: 60 }),
        party("pr-root", { id: "pr-sophie", kind: "PERSON", legalName: "Sophie Martin", country: "France", title: "Limited Partner", ownershipPercent: 30 }),
      ],
      profile: profile({ province: "BC", taxResidency: "INTERNATIONAL", features: ["FPL"], trustedContact: false }),
      documents: [
        doc("p-1", "naaf", "NAAF_PacificRim.pdf", t(300), "u-advisor-2"),
        doc("p-2", "formation", "LP_Agreement_Amended.pdf", t(300), "u-advisor-2", 3_900_000),
        doc("p-3", "w8", "W-8BEN-E_PacificRim.pdf", t(90), "u-advisor-2"),
      ],
      checklist: items([["naaf", "VERIFIED", ["p-1"]], ["formation", "REJECTED", ["p-2"]], ["w8", "RECEIVED", ["p-3"]], ["identity", "REQUESTED"], ["beneficial-owner", "REQUESTED"]], t(28), "u-compliance"),
      tasks: [
        { id: "task-1", title: "Disclose the shareholders of Pacific Rim GP Inc. down to natural persons.", partyId: "pr-gp", done: false, source: "COMPLIANCE", createdBy: "u-compliance", createdAt: new Date(t(28)).toISOString() },
        { id: "task-2", title: "Limited partner interests total 91%; confirm the remaining 9%.", partyId: "pr-root", done: false, source: "COMPLIANCE", createdBy: "u-compliance", createdAt: new Date(t(28)).toISOString() },
        { id: "task-3", title: "LP agreement is missing Schedule B (signing pages).", requirementId: "formation", done: false, source: "COMPLIANCE", createdBy: "u-compliance", createdAt: new Date(t(28)).toISOString() },
      ],
    }),
    record(now, {
      id: "case-0119", reference: "FCC-2026-0119", legalName: "Eagle River First Nation", entityType: "first_nation", status: "APPROVED",
      ownerId: "u-advisor", jurisdiction: "Canada (Indian Act)", registrationNumber: "Band No. 612", createdDaysAgo: 24, updatedHoursAgo: 96, dueInDays: -10, version: 21,
      parties: [
        party(null, { id: "er-root", kind: "ENTITY", legalName: "Eagle River First Nation", entityType: "first_nation", ownershipPercent: 100 }),
        party("er-root", { id: "er-chief", kind: "PERSON", legalName: "Chief Thomas Redsky", title: "Chief", isController: true, isSigningAuthority: true }),
        party("er-root", { id: "er-council", kind: "PERSON", legalName: "Marie Whitehorse", title: "Councillor", isSigningAuthority: true }),
      ],
      profile: profile({ province: "MB", taxResidency: "CANADA", features: [], trustedContact: false }),
      documents: [
        doc("e-1", "naaf", "NAAF_EagleRiver.pdf", t(500)), doc("e-2", "formation", "Band_Council_Governance.pdf", t(500)),
        doc("e-3", "resolution", "BCR_2026-14_Investment_Account.pdf", t(400)), doc("e-4", "identity", "ID_Chief_Council.pdf", t(300), "u-ops"),
      ],
      checklist: items([["naaf", "VERIFIED", ["e-1"]], ["formation", "VERIFIED", ["e-2"]], ["resolution", "VERIFIED", ["e-3"]], ["identity", "VERIFIED", ["e-4"]]], t(96), "u-compliance"),
    }),
    record(now, {
      id: "case-0144", reference: "FCC-2026-0144", legalName: "Okafor Dental Professional Corporation IPP", entityType: "ipp_rca", status: "BUILDING",
      ownerId: "u-advisor-2", jurisdiction: "Alberta", registrationNumber: "CRA RPP 1453870", createdDaysAgo: 0, updatedHoursAgo: 0.5, dueInDays: 14, version: 2,
      parties: [
        party(null, { id: "od-root", kind: "ENTITY", legalName: "Okafor Dental Professional Corporation IPP", entityType: "ipp_rca", ownershipPercent: 100 }),
        party("od-root", { id: "od-sponsor", kind: "ENTITY", legalName: "Okafor Dental Professional Corporation", entityType: "corporation", country: "Canada", title: "Plan Sponsor", ownershipPercent: 100, isController: true }),
      ],
      profile: profile({ province: "AB" }),
    }),
  ];

  const audit: AuditEvent[] = [
    { id: "a-1", caseId: "case-0139", actorId: "u-advisor", action: "CASE_CREATED", summary: "Created case for Maple Ridge Holdings Inc.", at: new Date(t(144)).toISOString(), version: 1 },
    { id: "a-2", caseId: "case-0139", actorId: "u-advisor", action: "AI_SUGGESTION", summary: "Accepted 4 parties extracted from Shareholder_Register_2026.pdf", at: new Date(t(69)).toISOString(), version: 3, ai: { model: "doc-extract-demo", accepted: true, ruleVersion: RULE_VERSION } },
    { id: "a-3", caseId: "case-0139", actorId: "u-advisor", action: "OWNERSHIP_UPDATED", summary: "Marked David Okoye as PEP / HIO", at: new Date(t(60)).toISOString(), version: 5, changes: [{ field: "David Okoye · PEP / HIO", from: "No", to: "Yes" }] },
    { id: "a-4", caseId: "case-0139", actorId: "u-advisor", action: "PROFILE_UPDATED", summary: "Set tax residency to Mixed", at: new Date(t(50)).toISOString(), version: 6, changes: [{ field: "Tax residency", from: "Canada", to: "Mixed" }] },
    { id: "a-5", caseId: "case-0139", actorId: "u-advisor", action: "STATUS_CHANGED", summary: "Requested outstanding documents from client", at: new Date(t(26)).toISOString(), version: 8, changes: [{ field: "Status", from: "Building", to: "Docs requested" }] },
    { id: "a-6", caseId: "case-0139", actorId: "u-ops", action: "DOCUMENT_UPLOADED", summary: "Uploaded Margin_Agreement_signed.pdf", at: new Date(t(3)).toISOString(), version: 9 },
    { id: "a-7", caseId: "case-0142", actorId: "u-advisor", action: "CASE_CREATED", summary: "Created case for Northwind Community Foundation", at: new Date(t(48)).toISOString(), version: 1 },
    { id: "a-8", caseId: "case-0142", actorId: "u-advisor", action: "OWNERSHIP_UPDATED", summary: "Added 3 directors and officers", at: new Date(t(1)).toISOString(), version: 4 },
    { id: "a-9", caseId: "case-0137", actorId: "u-advisor-2", action: "STATUS_CHANGED", summary: "Submitted for compliance review", at: new Date(t(20)).toISOString(), version: 14, changes: [{ field: "Status", from: "Docs requested", to: "Ready for compliance" }] },
    { id: "a-10", caseId: "case-0128", actorId: "u-compliance", action: "COMPLIANCE_DECISION", summary: "Returned to advisor with 3 comments", at: new Date(t(28)).toISOString(), version: 17, changes: [{ field: "Status", from: "Ready for compliance", to: "Returned" }] },
    { id: "a-11", caseId: "case-0119", actorId: "u-compliance", action: "COMPLIANCE_DECISION", summary: "Approved — beneficial ownership traced to band council", at: new Date(t(96)).toISOString(), version: 21, changes: [{ field: "Status", from: "Ready for compliance", to: "Approved" }] },
    { id: "a-12", caseId: "case-0131", actorId: "u-advisor", action: "CASE_CREATED", summary: "Created case for Lakeshore Condominium Corporation No. 482", at: new Date(t(6)).toISOString(), version: 1 },
    { id: "a-13", caseId: "case-0144", actorId: "u-advisor-2", action: "CASE_CREATED", summary: "Created case for Okafor Dental Professional Corporation IPP", at: new Date(t(1)).toISOString(), version: 1 },
    { id: "a-14", caseId: "case-0144", actorId: "u-advisor-2", action: "AI_SUGGESTION", summary: "Entity type suggested: IPP / RCA (accepted)", at: new Date(t(1)).toISOString(), version: 1, ai: { model: "entity-classifier-demo", accepted: true, ruleVersion: RULE_VERSION } },
  ];

  return { sessionUserId: "u-advisor", users: USERS, cases, audit, ruleLibrary: initialLibrary() };
}
