import { describe, expect, it } from "vitest";
import { generateRequirements, personsToIdentify, validateOwnership, type AccountCase, type TaxResidency } from "./index.js";

const account: AccountCase = {
  id: "case-1",
  version: 1,
  legalName: "Maple Holdings Inc.",
  entityType: "corporation",
  status: "BUILDING",
  ruleVersion: "demo",
  createdAt: "2026-10-04T00:00:00.000Z",
  updatedAt: "2026-10-04T00:00:00.000Z",
  parties: [
    { id: "root", parentId: null, kind: "ENTITY", legalName: "Maple Holdings Inc.", entityType: "corporation", ownershipPercent: 100, isController: true, isSigningAuthority: false, isUsPerson: false, isPepHio: false },
    { id: "person", parentId: "root", kind: "PERSON", legalName: "Alice Chen", country: "Canada", ownershipPercent: 100, isController: true, isSigningAuthority: true, isUsPerson: false, isPepHio: false },
  ],
  profile: { province: "ON", taxResidency: "CANADA", features: ["MARGIN"], trustedContact: false },
};

describe("domain rules", () => {
  it("accepts a fully disclosed ownership structure", () => {
    expect(validateOwnership(account)).toEqual([]);
  });

  it("adds account-feature requirements", () => {
    expect(generateRequirements(account).map((item) => item.id)).toContain("margin");
  });

  it("identifies indirect owners at or above 25% and controllers below it", () => {
    const parties: AccountCase["parties"] = [
      account.parties[0]!,
      { id: "holdco", parentId: "root", kind: "ENTITY", legalName: "Holdco", entityType: "corporation", ownershipPercent: 50, isController: false, isSigningAuthority: false, isUsPerson: false, isPepHio: false },
      { id: "bob", parentId: "holdco", kind: "PERSON", legalName: "Bob", ownershipPercent: 60, isController: false, isSigningAuthority: false, isUsPerson: false, isPepHio: false },
      { id: "carol", parentId: "holdco", kind: "PERSON", legalName: "Carol", ownershipPercent: 40, isController: false, isSigningAuthority: false, isUsPerson: false, isPepHio: false },
      { id: "dan", parentId: "root", kind: "PERSON", legalName: "Dan", ownershipPercent: 50, isController: false, isSigningAuthority: false, isUsPerson: false, isPepHio: false },
      { id: "erin", parentId: "root", kind: "PERSON", legalName: "Erin", ownershipPercent: 0, isController: false, isSigningAuthority: true, isUsPerson: false, isPepHio: false },
    ];
    expect(personsToIdentify(parties).map((party) => party.id)).toEqual(["bob", "dan", "erin"]);
  });

  it("links identity requirements to the persons that trigger them", () => {
    const identity = generateRequirements(account).find((item) => item.id === "identity");
    expect(identity?.partyIds).toEqual(["person"]);
  });

  it("matches the original guide's tax and director triggers", () => {
    const idsFor = (taxResidency: TaxResidency) =>
      generateRequirements({ ...account, profile: { ...account.profile!, taxResidency } }).map((item) => item.id);
    expect(idsFor("CANADA")).toEqual(expect.arrayContaining(["directors"]));
    expect(idsFor("CANADA")).not.toContain("rc519");
    expect(idsFor("US")).toEqual(expect.arrayContaining(["w9", "rc519"]));
    expect(idsFor("US")).not.toContain("w8");
    expect(idsFor("MIXED")).toEqual(expect.arrayContaining(["w8", "rc519"]));
    expect(idsFor("MIXED")).not.toContain("nffe");
    expect(idsFor("INTERNATIONAL")).toEqual(expect.arrayContaining(["w8", "rc519", "nffe"]));
    const trust = generateRequirements({ ...account, entityType: "trust" }).map((item) => item.id);
    expect(trust).not.toContain("directors");
  });
});
