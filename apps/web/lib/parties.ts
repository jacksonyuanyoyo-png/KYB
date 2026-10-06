import type { Party } from "@fcc/domain";
import { ENTITY_LABELS, US_TAX_LABELS } from "@/lib/labels";
import type { AuditChange } from "@/lib/types";

export function descendantIds(parties: Party[], id: string): Set<string> {
  const ids = new Set([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const party of parties) {
      if (party.parentId && ids.has(party.parentId) && !ids.has(party.id)) { ids.add(party.id); grew = true; }
    }
  }
  return ids;
}

export function removeSubtree(parties: Party[], id: string): Party[] {
  const ids = descendantIds(parties, id);
  return parties.filter((party) => !ids.has(party.id));
}

const yesNo = (value: boolean) => (value ? "Yes" : "No");

export function describeChanges(before: Party, after: Party): AuditChange[] {
  const prefix = after.legalName;
  const fields: Array<[string, string, string]> = [
    ["Legal name", before.legalName, after.legalName],
    ["Entity type", before.entityType ? ENTITY_LABELS[before.entityType] : "", after.entityType ? ENTITY_LABELS[after.entityType] : ""],
    ["Role", before.title ?? "", after.title ?? ""],
    ["Country", before.country ?? "", after.country ?? ""],
    ["Ownership", `${before.ownershipPercent}%`, `${after.ownershipPercent}%`],
    ["Controller", yesNo(before.isController), yesNo(after.isController)],
    ["Signing authority", yesNo(before.isSigningAuthority), yesNo(after.isSigningAuthority)],
    ["US tax classification", before.usTaxClass ? US_TAX_LABELS[before.usTaxClass].label : "", after.usTaxClass ? US_TAX_LABELS[after.usTaxClass].label : ""],
    ["US person", yesNo(before.isUsPerson), yesNo(after.isUsPerson)],
    ["PEP / HIO", yesNo(before.isPepHio), yesNo(after.isPepHio)],
  ];
  return fields.filter(([, from, to]) => from !== to).map(([field, from, to]) => ({ field: `${prefix} · ${field}`, from: from || "—", to: to || "—" }));
}
