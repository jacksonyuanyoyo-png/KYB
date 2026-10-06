import { RULE_VERSION, type AccountCase, type Requirement } from "@fcc/domain";
import type { LibraryRule, RuleLibraryState, RuleTrigger } from "@/lib/types";

export function initialLibrary(): RuleLibraryState {
  return { publishedVersion: RULE_VERSION, publishedExtras: [], publishedDisabled: [], publishedOverrides: {}, draft: null };
}

export function triggerLabel(trigger: RuleTrigger): string {
  switch (trigger.kind) {
    case "ALWAYS": return "Always";
    case "ENTITY": return `Entity is ${(trigger.entityTypes ?? []).join(", ") || "—"}`;
    case "TAX": return `Tax residency is ${(trigger.taxResidencies ?? []).join(", ") || "—"}`;
    case "FEATURE": return trigger.feature ? `${trigger.feature} selected` : "Account feature selected";
    case "PERSON": return "Any natural person in the structure";
    case "PEP": return "Any person flagged PEP / HIO";
    case "US_PERSON": return "US tax residency, or any US person in the structure";
    case "TRUSTED_CONTACT": return "Trusted Contact designated";
    default: return "Always";
  }
}

export function ruleApplies(rule: LibraryRule, account: AccountCase): boolean {
  if (!rule.enabled) return false;
  const profile = account.profile;
  const trigger = rule.trigger;
  switch (trigger.kind) {
    case "ALWAYS": return true;
    case "ENTITY": return (trigger.entityTypes ?? []).includes(account.entityType);
    case "TAX": return Boolean(profile && (trigger.taxResidencies ?? []).includes(profile.taxResidency));
    case "FEATURE": return Boolean(trigger.feature && profile?.features.includes(trigger.feature));
    case "PERSON": return account.parties.some((party) => party.kind === "PERSON");
    case "PEP": return account.parties.some((party) => party.isPepHio);
    case "US_PERSON": return profile?.taxResidency === "US" || account.parties.some((party) => party.isUsPerson);
    case "TRUSTED_CONTACT": return profile?.trustedContact ?? false;
    default: return false;
  }
}

export function libraryRequirements(rules: LibraryRule[], account: AccountCase): Requirement[] {
  return rules.filter((rule) => ruleApplies(rule, account)).map((rule) => ({
    id: rule.id, section: rule.section, name: rule.name, conditional: rule.conditional, source: rule.source, reason: rule.reason, partyIds: [],
  }));
}

/** Extras and retirements apply only to cases opened on a published version after the coded demonstrator. */
export function publishedAdjustments(library: RuleLibraryState, ruleVersion: string): { extras: LibraryRule[]; disabled: string[]; overrides: RuleLibraryState["publishedOverrides"] } {
  if (ruleVersion !== library.publishedVersion || library.publishedVersion === RULE_VERSION) return { extras: [], disabled: [], overrides: {} };
  return { extras: library.publishedExtras, disabled: library.publishedDisabled, overrides: library.publishedOverrides ?? {} };
}
