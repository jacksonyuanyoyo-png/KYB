export const ENTITY_TYPES = [
  "corporation", "charity", "trust", "ipp_rca", "partnership",
  "estate", "condo", "pooled_fund", "association", "first_nation",
] as const;

export type EntityType = (typeof ENTITY_TYPES)[number];
export type CaseStatus = "BUILDING" | "DOCS_REQUESTED" | "READY_FOR_COMPLIANCE" | "RETURNED" | "APPROVED";
export type TaxResidency = "CANADA" | "US" | "INTERNATIONAL" | "MIXED";
export type PartyKind = "ENTITY" | "PERSON";
export const US_TAX_CLASSES = ["complex", "simple", "unsure"] as const;
export type UsTaxClass = (typeof US_TAX_CLASSES)[number];
export type AccountFeature = "MARGIN" | "OPTIONS" | "COD_DVP" | "FPL";

export const ACCOUNT_FEATURES: readonly AccountFeature[] = ["MARGIN", "OPTIONS", "COD_DVP", "FPL"];
export const BENEFICIAL_OWNER_THRESHOLD = 25;

export interface Party {
  id: string;
  parentId: string | null;
  kind: PartyKind;
  legalName: string;
  entityType?: EntityType;
  country?: string;
  title?: string;
  /** US tax classification of an intermediate entity. Collected for later form selection; it does not change the checklist by itself. */
  usTaxClass?: UsTaxClass;
  ownershipPercent: number;
  isController: boolean;
  isSigningAuthority: boolean;
  isUsPerson: boolean;
  isPepHio: boolean;
}

export interface AccountProfile {
  province: string;
  taxResidency: TaxResidency;
  features: AccountFeature[];
  trustedContact: boolean;
}

export interface AccountCase {
  id: string;
  version: number;
  legalName: string;
  entityType: EntityType;
  status: CaseStatus;
  parties: Party[];
  profile?: AccountProfile;
  ruleVersion: string;
  createdAt: string;
  updatedAt: string;
}

export interface ValidationIssue {
  code: "MISSING_ROOT" | "MISSING_OWNER" | "OWNERSHIP_TOTAL" | "ENTITY_LEAF" | "PROFILE_INCOMPLETE";
  partyId?: string;
  message: string;
}

export interface Requirement {
  id: string;
  section: string;
  name: string;
  conditional: boolean;
  source: string;
  reason: string;
  partyIds: string[];
}

export const RULE_VERSION = "demo-2026-10-04";

export function validateOwnership(account: Pick<AccountCase, "parties">): ValidationIssue[] {
  const root = account.parties.find((party) => party.parentId === null);
  if (!root) return [{ code: "MISSING_ROOT", message: "A root entity is required." }];
  const issues: ValidationIssue[] = [];
  for (const entity of account.parties.filter((party) => party.kind === "ENTITY")) {
    const children = account.parties.filter((party) => party.parentId === entity.id);
    if (children.length === 0) {
      issues.push({ code: entity.id === root.id ? "MISSING_OWNER" : "ENTITY_LEAF", partyId: entity.id, message: `${entity.legalName} must disclose an owner or controller.` });
      continue;
    }
    const total = children.reduce((sum, party) => sum + party.ownershipPercent, 0);
    if (total > 0 && Math.abs(total - 100) > 0.01) {
      issues.push({ code: "OWNERSHIP_TOTAL", partyId: entity.id, message: `${entity.legalName} ownership totals ${total}%.` });
    }
  }
  return issues;
}

export function validateProfile(profile: AccountProfile | undefined): ValidationIssue[] {
  if (!profile?.province) return [{ code: "PROFILE_INCOMPLETE", message: "Province or territory of registration is required." }];
  return [];
}

/** Effective ownership of each party in the root entity, multiplied down the chain. */
export function effectiveOwnership(parties: Party[]): Map<string, number> {
  const byId = new Map(parties.map((party) => [party.id, party]));
  const result = new Map<string, number>();
  const resolve = (party: Party): number => {
    const cached = result.get(party.id);
    if (cached !== undefined) return cached;
    const parent = party.parentId ? byId.get(party.parentId) : undefined;
    const value = parent ? (resolve(parent) * party.ownershipPercent) / 100 : 100;
    result.set(party.id, value);
    return value;
  };
  parties.forEach(resolve);
  return result;
}

/** Natural persons FINTRAC requires us to identify: ≥25% effective ownership, or control/signing authority. */
export function personsToIdentify(parties: Party[]): Party[] {
  const effective = effectiveOwnership(parties);
  return parties.filter((party) => party.kind === "PERSON" && (
    (effective.get(party.id) ?? 0) >= BENEFICIAL_OWNER_THRESHOLD || party.isController || party.isSigningAuthority
  ));
}

export function generateRequirements(account: AccountCase): Requirement[] {
  const profile = account.profile;
  const people = account.parties.filter((party) => party.kind === "PERSON");
  const ids = (list: Party[]) => list.map((party) => party.id);
  const identify = personsToIdentify(account.parties);
  const peps = people.filter((party) => party.isPepHio);
  const usPersons = account.parties.filter((party) => party.isUsPerson);
  const signers = people.filter((party) => party.isSigningAuthority || party.isController);
  const rules: Array<Requirement & { applies: boolean }> = [
    { id: "naaf", section: "Entity Formation & Authorization", name: "New Account Application Form (NAAF)", conditional: false, source: "FCC guide §5.2", reason: "Required for every new account. Include the CRA Business Number when applicable.", partyIds: [], applies: true },
    { id: "formation", section: "Entity Formation & Authorization", name: "Articles / formation or governing document", conditional: false, source: "FCC guide §3", reason: "Confirms the entity exists and who governs it.", partyIds: [], applies: true },
    { id: "resolution", section: "Entity Formation & Authorization", name: "Corporate resolution / signing authority evidence", conditional: false, source: "FCC guide §5.2", reason: "Entity type acts through a board or governing body.", partyIds: ids(signers), applies: ["corporation", "condo", "charity", "association", "first_nation"].includes(account.entityType) },
    { id: "beneficial-owner", section: "Entity Formation & Authorization", name: "Beneficial Owner Identification", conditional: false, source: "CIRO 3203–3204; Compliance approval required", reason: "CIRO/FINTRAC ownership and control record.", partyIds: ids(identify), applies: ["corporation", "partnership", "pooled_fund", "trust", "ipp_rca"].includes(account.entityType) },
    { id: "directors", section: "Entity Formation & Authorization", name: "Director listing", conditional: false, source: "FCC guide", reason: "This entity type is governed by a board of directors.", partyIds: [], applies: ["corporation", "charity", "condo"].includes(account.entityType) },
    { id: "identity", section: "Persons to Identify", name: "Identity verification for signers and controllers", conditional: false, source: "FINTRAC; Compliance approval required", reason: "Persons with ≥25% ownership, control or signing authority must be identified.", partyIds: ids(identify), applies: people.length > 0 },
    { id: "pep", section: "Persons to Identify", name: "PEP / HIO enhanced review", conditional: true, source: "FINTRAC; Compliance approval required", reason: "At least one person is flagged as PEP / HIO.", partyIds: ids(peps), applies: peps.length > 0 },
    { id: "margin", section: "Account Features", name: "Margin agreement", conditional: true, source: "FCC guide §5.2", reason: "Margin feature selected.", partyIds: [], applies: profile?.features.includes("MARGIN") ?? false },
    { id: "options", section: "Account Features", name: "Options agreement and risk disclosure", conditional: true, source: "FCC guide §5.2", reason: "Options feature selected.", partyIds: [], applies: profile?.features.includes("OPTIONS") ?? false },
    { id: "cod-dvp", section: "Account Features", name: "COD / DVP settlement instructions", conditional: true, source: "FCC guide §5.2", reason: "COD / DVP settlement selected.", partyIds: [], applies: profile?.features.includes("COD_DVP") ?? false },
    { id: "fpl", section: "Account Features", name: "Fully Paid Lending agreement and risk disclosure", conditional: true, source: "FCC guide §5.2", reason: "Fully paid securities lending selected.", partyIds: [], applies: profile?.features.includes("FPL") ?? false },
    { id: "tcp", section: "Entity Formation & Authorization", name: "Trusted Contact Person form", conditional: true, source: "Compliance approval required", reason: "A Trusted Contact Person was designated.", partyIds: [], applies: profile?.trustedContact ?? false },
    { id: "w9", section: "IRS / Withholding Tax", name: "W-9", conditional: true, source: "FCC guide §5.2", reason: "US tax residency or a US person in the structure.", partyIds: ids(usPersons), applies: profile?.taxResidency === "US" || usPersons.length > 0 },
    { id: "w8", section: "IRS / Withholding Tax", name: "W-8BEN-E or applicable treaty statement", conditional: true, source: "FCC guide §5.2", reason: "International or mixed tax residency.", partyIds: [], applies: profile?.taxResidency === "INTERNATIONAL" || profile?.taxResidency === "MIXED" },
    { id: "rc519", section: "FATCA / CRS", name: "RC519 Declaration of Tax Residence", conditional: true, source: "FCC guide §5.2", reason: "Tax residency is outside Canada.", partyIds: [], applies: profile?.taxResidency === "US" || profile?.taxResidency === "INTERNATIONAL" || profile?.taxResidency === "MIXED" },
    { id: "nffe", section: "FATCA / CRS", name: "Passive NFFE controlling-person certification", conditional: true, source: "FCC guide", reason: "International residency. If the entity is a passive NFFE, each controlling person at or above 25% certifies.", partyIds: ids(identify), applies: profile?.taxResidency === "INTERNATIONAL" },
  ];
  return rules.filter(({ applies }) => applies).map(({ applies: _applies, ...requirement }) => requirement);
}
