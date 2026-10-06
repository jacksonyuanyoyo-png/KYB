"""整体替换账户详情。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from fcc_api.auth.actor import Actor
from fcc_api.db.models.cases import PROVINCE_CODES, Case
from fcc_api.errors import ApiError
from fcc_api.rules.constants import ACCOUNT_FEATURES
from fcc_api.schemas.cases import CaseDetailResponse, ProfileIn, UpdateProfileIn
from fcc_api.services.case_write import case_write
from fcc_api.services.cases import load_case_detail

_FEATURES = frozenset(ACCOUNT_FEATURES)
_PROVINCES = frozenset(PROVINCE_CODES)


def update_profile(
    session: Session,
    actor: Actor,
    case_id: str,
    body: UpdateProfileIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    profile = _validated(body.profile)
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="updateProfile",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        case = writer.case
        before = _profile_dict(case)
        case.province = profile["province"]
        case.tax_residency = profile["taxResidency"]
        case.features = list(profile["features"])
        case.trusted_contact = profile["trustedContact"]
        case.trusted_contact_name = profile["trustedContactName"]
        writer.audit(
            action="PROFILE_UPDATED",
            summary=f"Updated {body.change.field.strip().lower()}",
            changes=[{
                "field": body.change.field.strip(),
                "from": body.change.from_,
                "to": body.change.to,
            }],
            before=before,
            after=profile,
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def _validated(profile: ProfileIn) -> dict:
    province = profile.province.strip()
    if province and province not in _PROVINCES:
        raise _invalid([{"path": "profile.province", "message": "Value is not allowed."}])
    features = list(profile.features)
    if len(features) != len(set(features)) or any(item not in _FEATURES for item in features):
        raise _invalid([
            {"path": "profile.features", "message": "Features must be a unique subset of account features."}
        ])
    name = profile.trusted_contact_name
    if len(name) > 200:
        raise _invalid([
            {"path": "profile.trustedContactName", "message": "Length must be 0–200 characters."}
        ])
    return {
        "province": province,
        "taxResidency": profile.tax_residency,
        "features": features,
        "trustedContact": profile.trusted_contact,
        "trustedContactName": name,
    }


def _profile_dict(case: Case) -> dict:
    return {
        "province": case.province,
        "taxResidency": case.tax_residency,
        "features": list(case.features or []),
        "trustedContact": case.trusted_contact,
        "trustedContactName": case.trusted_contact_name,
    }


def _invalid(fields: list[dict[str, str]]) -> ApiError:
    return ApiError(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": fields})
