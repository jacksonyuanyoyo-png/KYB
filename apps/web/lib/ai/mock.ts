import type { EntityType, Party } from "@fcc/domain";
import { formatPercent } from "@/lib/format";
import type { CaseInsight } from "@/lib/insights";
import { ENTITY_LABELS } from "@/lib/labels";
import type { CaseRecord } from "@/lib/types";

export const AI_MODELS = {
  extract: "doc-extract-demo",
  classify: "entity-classifier-demo",
  assistant: "case-assistant-demo",
  review: "pre-review-demo",
} as const;

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export interface EntityTypeSuggestion {
  type: EntityType;
  confidence: number;
  reasons: string[];
}

const KEYWORDS: Array<[RegExp, EntityType, string]> = [
  [/\b(ipp|individual pension|rca|retirement compensation)\b/i, "ipp_rca", "Name references a pension plan or RCA."],
  [/\b(trust|trustee|settlor)\b/i, "trust", "Name references a trust."],
  [/\b(lp|l\.p\.|limited partnership|partnership|partners)\b/i, "partnership", "Name references a partnership."],
  [/\b(foundation|charity|charitable|society|non-profit|not-for-profit)\b/i, "charity", "Name suggests a charitable or not-for-profit body."],
  [/\b(condominium|condo|strata)\b/i, "condo", "Name references a condominium corporation."],
  [/\b(estate of|estate)\b/i, "estate", "Name references an estate."],
  [/\b(first nation|band|nation|métis|metis|inuit)\b/i, "first_nation", "Name references an Indigenous governing body."],
  [/\b(fund|pooled)\b/i, "pooled_fund", "Name references a pooled or investment fund."],
  [/\b(union|association|club|local \d+)\b/i, "association", "Name references an association or union."],
  [/\b(inc|incorporated|corp|corporation|ltd|limited|ltée)\b/i, "corporation", "Legal suffix indicates an incorporated company."],
];

export async function suggestEntityType(legalName: string, notes: string): Promise<EntityTypeSuggestion | null> {
  await wait(700);
  const text = `${legalName} ${notes}`;
  const match = KEYWORDS.find(([pattern]) => pattern.test(text));
  if (!match) return null;
  const [, type, reason] = match;
  return {
    type,
    confidence: notes.trim() ? 0.91 : 0.78,
    reasons: [reason, `Matches the Manual Account Opening subtype “${ENTITY_LABELS[type]}”.`, "Confirm against the formation document before continuing."],
  };
}

export interface ExtractedParty {
  tempId: string;
  kind: Party["kind"];
  legalName: string;
  entityType?: EntityType;
  title: string;
  ownershipPercent: number;
  isController: boolean;
  isSigningAuthority: boolean;
  country: string;
  confidence: number;
  citation: string;
}

export async function extractFormationParties(record: CaseRecord, fileNames: string[]): Promise<ExtractedParty[]> {
  await wait(400);
  const source = fileNames[0] ?? "Formation document";
  const register = fileNames.find((name) => /register|ledger|shareholder/i.test(name)) ?? source;
  const base = record.legalName.split(/\s+/)[0] ?? "Northern";
  const byType: Partial<Record<EntityType, ExtractedParty[]>> = {
    trust: [
      { tempId: "x1", kind: "PERSON", legalName: "Catherine Ward", title: "Trustee", ownershipPercent: 0, isController: true, isSigningAuthority: true, country: "Canada", confidence: 0.96, citation: `${source} · §2.1 Appointment of Trustees` },
      { tempId: "x2", kind: "PERSON", legalName: "Liam Ward", title: "Beneficiary", ownershipPercent: 50, isController: false, isSigningAuthority: false, country: "Canada", confidence: 0.88, citation: `${source} · Schedule A` },
      { tempId: "x3", kind: "PERSON", legalName: "Ava Ward", title: "Beneficiary", ownershipPercent: 50, isController: false, isSigningAuthority: false, country: "Canada", confidence: 0.86, citation: `${source} · Schedule A` },
    ],
    condo: [
      { tempId: "x1", kind: "PERSON", legalName: "Michael Stewart", title: "Director", ownershipPercent: 0, isController: true, isSigningAuthority: true, country: "Canada", confidence: 0.94, citation: `${source} · Board of Directors` },
      { tempId: "x2", kind: "PERSON", legalName: "Anita Desai", title: "Director", ownershipPercent: 0, isController: true, isSigningAuthority: false, country: "Canada", confidence: 0.92, citation: `${source} · Board of Directors` },
      { tempId: "x3", kind: "PERSON", legalName: "Paul Nguyen", title: "Authorized Signatory", ownershipPercent: 0, isController: false, isSigningAuthority: true, country: "Canada", confidence: 0.81, citation: `${source} · By-law No. 3, s.4` },
    ],
  };
  const fallback: ExtractedParty[] = [
    { tempId: "x1", kind: "PERSON", legalName: "Jordan Blake", title: "Director", ownershipPercent: 60, isController: true, isSigningAuthority: true, country: "Canada", confidence: 0.95, citation: `${register} · p.2, line 1` },
    { tempId: "x2", kind: "ENTITY", legalName: `${base} Lane Holdings Inc.`, entityType: "corporation", title: "Shareholder", ownershipPercent: 40, isController: false, isSigningAuthority: false, country: "Canada", confidence: 0.89, citation: `${register} · p.2, line 2` },
    { tempId: "x3", kind: "PERSON", legalName: "Mira Shah", title: "Corporate Secretary", ownershipPercent: 0, isController: false, isSigningAuthority: true, country: "Canada", confidence: 0.72, citation: `${source} · Officers, s.7.2` },
  ];
  return byType[record.entityType] ?? fallback;
}

export interface PartyProposal {
  legalName: string;
  ownershipPercent: number;
  title: string;
  isController: boolean;
  isSigningAuthority: boolean;
  isUsPerson: boolean;
  isPepHio: boolean;
  parentName: string;
}

const ROLE_PATTERN = /(director|trustee|executor|officer|signing authority|signatory|controller|general partner|chief|councillor)/i;

export function parseInstruction(text: string, record: CaseRecord): PartyProposal | null {
  const match = text.match(/^\s*([A-Z][\p{L}'.-]+(?:\s+[A-Z][\p{L}'.-]+)+)\s+(?:holds|owns|has)\s+(\d+(?:\.\d+)?)\s*%/u);
  const roleOnly = text.match(/^\s*([A-Z][\p{L}'.-]+(?:\s+[A-Z][\p{L}'.-]+)+)\s+is\s+(?:a|an|the)\s+/u);
  const name = match?.[1] ?? roleOnly?.[1];
  if (!name) return null;
  const role = text.match(ROLE_PATTERN)?.[1] ?? "";
  const titled = role ? role.replace(/\b\w/g, (letter) => letter.toUpperCase()) : "Shareholder";
  const root = record.parties.find((party) => party.parentId === null);
  const under = text.match(/\b(?:of|in)\s+([A-Z][\p{L}&'.\s-]+?(?:Inc\.?|Ltd\.?|Corp\.?|LP|Trust))/u)?.[1];
  const parent = record.parties.find((party) => party.kind === "ENTITY" && under && party.legalName.toLowerCase().startsWith(under.toLowerCase().trim())) ?? root;
  return {
    legalName: name,
    ownershipPercent: match ? Number(match[2]) : 0,
    title: titled === "Signatory" ? "Authorized Signatory" : titled,
    isController: /director|trustee|executor|controller|general partner|chief/i.test(role),
    isSigningAuthority: /sign|director|trustee|executor|chief/i.test(text),
    isUsPerson: /\b(us person|u\.s\. person|american|us citizen)\b/i.test(text),
    isPepHio: /\b(pep|hio|politically exposed|head of an international)\b/i.test(text),
    parentName: parent?.legalName ?? record.legalName,
  };
}

export interface GapExplanation {
  partyId?: string;
  title: string;
  detail: string;
  action: string;
}

export function explainGaps(record: CaseRecord, insight: CaseInsight): GapExplanation[] {
  const byId = new Map(record.parties.map((party) => [party.id, party]));
  const ownership = insight.ownershipIssues.map((issue): GapExplanation => {
    const party = issue.partyId ? byId.get(issue.partyId) : undefined;
    const name = party?.legalName ?? record.legalName;
    const children = record.parties.filter((item) => item.parentId === issue.partyId);
    const total = children.reduce((sum, item) => sum + item.ownershipPercent, 0);
    switch (issue.code) {
      case "MISSING_OWNER":
        return { partyId: issue.partyId, title: "No owners or controllers yet", detail: `${name} has nobody attached. FINTRAC needs to know who owns it and who can act for it.`, action: "Select the root card and add at least one person or entity." };
      case "ENTITY_LEAF":
        return { partyId: issue.partyId, title: `${name} is not traced to people`, detail: `${name} is a ${party?.entityType ? ENTITY_LABELS[party.entityType] : "entity"}. Ownership must be followed through every company or trust until it ends at natural persons.`, action: `Select ${name} and add its shareholders, partners or trustees.` };
      case "OWNERSHIP_TOTAL":
        return { partyId: issue.partyId, title: `${name} totals ${formatPercent(total)}`, detail: `Disclosed interests under ${name} add up to ${formatPercent(total)}, not 100%. ${total < 100 ? `${formatPercent(100 - total)} is unaccounted for.` : "Interests overlap or are double-counted."}`, action: `Open ${name} and correct the percentages, or add the missing holder.` };
      default:
        return { partyId: issue.partyId, title: "Structure incomplete", detail: issue.message, action: "Review the ownership graph." };
    }
  });
  const details = insight.detailsGaps.map((gap): GapExplanation => ({ title: "Account details missing", detail: gap.message, action: "Open the Account details step." }));
  return [...ownership, ...details];
}

export interface ReviewFinding {
  id: string;
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
  requirementId?: string;
  partyId?: string;
}

export async function preReview(record: CaseRecord, insight: CaseInsight): Promise<ReviewFinding[]> {
  await wait(1400);
  const findings: ReviewFinding[] = [];
  const signer = record.parties.find((party) => party.isSigningAuthority && party.kind === "PERSON");
  const resolution = record.documents.find((item) => item.requirementId === "resolution");
  if (resolution && signer) {
    const [first, ...rest] = signer.legalName.split(" ");
    findings.push({ id: "f1", severity: "medium", title: "Signer name differs from ownership graph", detail: `${resolution.fileName} is signed by “${first?.[0] ?? ""}. ${rest.join(" ")}”; the graph records “${signer.legalName}”. Confirm they are the same person.`, requirementId: "resolution", partyId: signer.id });
  }
  const formation = record.documents.find((item) => item.requirementId === "formation");
  if (formation) findings.push({ id: "f2", severity: "high", title: "Formation document appears incomplete", detail: `${formation.fileName} references Schedule B, which is not in the uploaded file.`, requirementId: "formation" });
  for (const person of insight.identify) {
    const hasId = (record.checklist.identity?.documentIds.length ?? 0) > 0;
    if (!hasId) findings.push({ id: `id-${person.id}`, severity: "high", title: `No ID evidence for ${person.legalName}`, detail: `${person.legalName} must be identified (${person.isSigningAuthority || person.isController ? "control or signing authority" : "≥25% ownership"}). No identity document is attached yet.`, requirementId: "identity", partyId: person.id });
  }
  const pep = record.parties.find((party) => party.isPepHio);
  if (pep) findings.push({ id: "f4", severity: "low", title: "Screening result required", detail: `${pep.legalName} is flagged PEP / HIO. Attach the result from the approved screening system; the assistant cannot clear PEP status.`, requirementId: "pep", partyId: pep.id });
  return findings;
}
