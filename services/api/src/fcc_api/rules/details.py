"""Account-detail gate used by the workbench, from apps/web/lib/insights.ts.

This is the DETAILS gate. validate_profile in the domain package only checks
the province and is not used here.
"""

from dataclasses import dataclass

from fcc_api.rules.types import AccountProfile, ProfileDraft


@dataclass
class DetailsGap:
    field: str
    message: str


def validate_details(profile: ProfileDraft) -> list[DetailsGap]:
    gaps: list[DetailsGap] = []
    if not profile.province:
        gaps.append(
            DetailsGap(
                field="province", message="Select the province or territory of registration."
            )
        )
    if not profile.tax_residency:
        gaps.append(DetailsGap(field="taxResidency", message="Select the entity's tax residency."))
    if profile.trusted_contact is None:
        gaps.append(
            DetailsGap(
                field="trustedContact",
                message="Record whether a Trusted Contact Person is designated.",
            )
        )
    if profile.trusted_contact and not profile.trusted_contact_name.strip():
        gaps.append(
            DetailsGap(
                field="trustedContactName", message="Enter the Trusted Contact Person's name."
            )
        )
    return gaps


def to_account_profile(profile: ProfileDraft) -> AccountProfile:
    tax_residency = profile.tax_residency if profile.tax_residency is not None else "CANADA"
    trusted_contact = profile.trusted_contact if profile.trusted_contact is not None else False
    return AccountProfile(
        province=profile.province,
        tax_residency=tax_residency,
        features=list(profile.features),
        trusted_contact=trusted_contact,
    )
