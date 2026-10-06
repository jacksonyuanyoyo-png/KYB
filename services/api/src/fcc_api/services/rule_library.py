"""Rule library writes, published snapshots, and the rule tester.

Seven writes take the client's rule-library version, lock rule_library_state,
and either bump that version or reject with 409. Publishing inserts an
immutable rule_versions row and does not update cases.
"""

from __future__ import annotations

import copy
import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from fcc_api.schemas.rules import (
    ACCOUNT_FEATURES,
    ENTITY_TYPES,
    SECTIONS,
    TAX_RESIDENCIES,
    BuiltinOverrideOut,
    BuiltinRuleOut,
    EvaluateResponse,
    LibraryRuleOut,
    RequirementOut,
    RuleDraftOut,
    RuleLibraryResponse,
    RuleTriggerOut,
    trigger_problems,
)

logger = logging.getLogger("fcc_api.auth")

CONFLICT_MESSAGE = (
    "The rule library was changed by someone else. Reload to see the latest version before saving again."
)
NO_DRAFT_PUBLISH = "There is no draft to publish."
NO_DRAFT_REMOVE = "Start a draft before removing a rule."
NO_DRAFT_DISCARD = "There is no draft to discard."
NO_DRAFT_EVALUATE = "There is no draft to evaluate."
PARITY_MESSAGE = "Rule engine parity check failed; publishing is blocked."
RULE_VERSION_DEMO = "demo-2026-10-04"
LIBRARY_OPERATIONS = (
    "startRuleDraft",
    "saveLibraryRule",
    "removeLibraryRule",
    "setBuiltinRetired",
    "saveBuiltinOverride",
    "discardRuleDraft",
    "publishRuleDraft",
)
BUILTIN_CATALOG: list[dict[str, Any]] = [
    {"id": "naaf", "name": "New Account Application Form (NAAF)", "section": "Entity Formation & Authorization", "trigger": "Always", "source": "FCC guide §5.2", "conditional": False},
    {"id": "formation", "name": "Articles / formation or governing document", "section": "Entity Formation & Authorization", "trigger": "Always", "source": "FCC guide §3", "conditional": False},
    {"id": "resolution", "name": "Corporate resolution / signing authority evidence", "section": "Entity Formation & Authorization", "trigger": "Corporation, Condo, Charity, Association, First Nation", "source": "FCC guide §5.2", "conditional": False},
    {"id": "beneficial-owner", "name": "Beneficial Owner Identification", "section": "Entity Formation & Authorization", "trigger": "Corporation, Partnership, Pooled Fund, Trust, IPP/RCA", "source": "CIRO 3203–3204", "conditional": False},
    {"id": "directors", "name": "Director listing", "section": "Entity Formation & Authorization", "trigger": "Corporation, Charity, Condo", "source": "FCC guide", "conditional": False},
    {"id": "tcp", "name": "Trusted Contact Person form", "section": "Entity Formation & Authorization", "trigger": "Trusted Contact designated", "source": "Compliance approval required", "conditional": True},
    {"id": "identity", "name": "Identity verification for signers and controllers", "section": "Persons to Identify", "trigger": "Any natural person", "source": "FINTRAC", "conditional": False},
    {"id": "pep", "name": "PEP / HIO enhanced review", "section": "Persons to Identify", "trigger": "Any person flagged PEP / HIO", "source": "FINTRAC", "conditional": True},
    {"id": "margin", "name": "Margin agreement", "section": "Account Features", "trigger": "Margin selected", "source": "FCC guide §5.2", "conditional": True},
    {"id": "options", "name": "Options agreement and risk disclosure", "section": "Account Features", "trigger": "Options selected", "source": "FCC guide §5.2", "conditional": True},
    {"id": "cod-dvp", "name": "COD / DVP settlement instructions", "section": "Account Features", "trigger": "COD / DVP selected", "source": "FCC guide §5.2", "conditional": True},
    {"id": "fpl", "name": "Fully Paid Lending agreement and risk disclosure", "section": "Account Features", "trigger": "Fully Paid Lending selected", "source": "FCC guide §5.2", "conditional": True},
    {"id": "w9", "name": "W-9", "section": "IRS / Withholding Tax", "trigger": "US residency or a US person", "source": "FCC guide §5.2", "conditional": True},
    {"id": "w8", "name": "W-8BEN-E or applicable treaty statement", "section": "IRS / Withholding Tax", "trigger": "International or mixed residency", "source": "FCC guide §5.2", "conditional": True},
    {"id": "rc519", "name": "RC519 Declaration of Tax Residence", "section": "FATCA / CRS", "trigger": "US, international, or mixed residency", "source": "FCC guide §5.2", "conditional": True},
    {"id": "nffe", "name": "Passive NFFE controlling-person certification", "section": "FATCA / CRS", "trigger": "International residency", "source": "FCC guide", "conditional": True},
]
BUILTIN_IDS = frozenset(item["id"] for item in BUILTIN_CATALOG)
_FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "parity" / "fixtures" / "domain_cases.json"
_MISSING = object()


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def start_rule_draft(
    session: Session,
    actor: Any,
    version: int,
    *,
    request_id: str,
    correlation_id: str,
) -> tuple[RuleLibraryResponse, int]:
    _authorize(actor, "startRuleDraft", request_id)
    state = _lock(session, version)
    draft = _open_draft(session)
    if draft is not None:
        return _response(session, actor, state), 200
    published = _require_version_row(session, state.published_version)
    now = _now()
    draft = _insert_draft(session, actor, published, now)
    summary = f"{_actor_name(actor)} started rule draft {draft.version}"
    _finish(
        session,
        state,
        actor,
        now,
        summary=summary,
        rule_version=draft.version,
        before={"draft": None, "publishedVersion": published.version},
        after={"draft": _draft_snapshot(draft), "publishedVersion": published.version},
        request_id=request_id,
        correlation_id=correlation_id,
    )
    payload = _response(session, actor, state)
    session.commit()
    return payload, 201


def save_library_rule(
    session: Session,
    actor: Any,
    version: int,
    rule: dict[str, Any],
    *,
    mode: str,
    path_rule_id: str | None = None,
    request_id: str,
    correlation_id: str,
) -> RuleLibraryResponse:
    _authorize(actor, "saveLibraryRule", request_id)
    state = _lock(session, version)
    cleaned = _validate_library_rule(rule)
    if path_rule_id is not None and path_rule_id != cleaned["id"]:
        _fail(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": [{"path": "rule.id", "message": "rule.id must match the path ruleId."}]})
    draft = _open_draft(session)
    extras = _json_list(getattr(draft, "extras", None)) if draft is not None else []
    if mode != "add" and (draft is None or not any(item.get("id") == cleaned["id"] for item in extras)):
        _fail(404, "NOT_FOUND", "Rule not found.", {"resource": "libraryRule", "id": cleaned["id"]})
    now = _now()
    before_draft = _draft_snapshot(draft) if draft is not None else None
    if draft is None:
        published = _require_version_row(session, state.published_version)
        draft = _insert_draft(session, actor, published, now)
        extras = _json_list(draft.extras)
    if mode == "add" and any(item.get("id") == cleaned["id"] for item in extras):
        _fail(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": [{"path": "rule.id", "message": "An extra rule with this id is already in the draft."}]})
    if mode == "add":
        draft.extras = [*extras, cleaned]
        summary = f"Added draft rule {_q(cleaned['name'])}"
    else:
        draft.extras = [cleaned if item.get("id") == cleaned["id"] else item for item in extras]
        summary = f"Updated draft rule {_q(cleaned['name'])}"
    flag_modified(draft, "extras")
    draft.updated_by = actor.id
    draft.updated_at = now
    _finish(
        session,
        state,
        actor,
        now,
        summary=summary,
        rule_version=draft.version,
        before={"draft": before_draft},
        after={"draft": _draft_snapshot(draft)},
        request_id=request_id,
        correlation_id=correlation_id,
    )
    payload = _response(session, actor, state)
    session.commit()
    return payload


def remove_library_rule(
    session: Session,
    actor: Any,
    version: int,
    rule_id: str,
    *,
    request_id: str,
    correlation_id: str,
) -> RuleLibraryResponse:
    _authorize(actor, "removeLibraryRule", request_id)
    state = _lock(session, version)
    draft = _open_draft(session)
    if draft is None:
        _fail(422, "GATE_FAILED", NO_DRAFT_REMOVE, {"gate": "NO_DRAFT"})
    extras = _json_list(draft.extras)
    match = next((item for item in extras if item.get("id") == rule_id), None)
    if match is None:
        _fail(404, "NOT_FOUND", "Rule not found.", {"resource": "libraryRule", "id": rule_id})
    now = _now()
    before_draft = _draft_snapshot(draft)
    draft.extras = [item for item in extras if item.get("id") != rule_id]
    flag_modified(draft, "extras")
    draft.updated_by = actor.id
    draft.updated_at = now
    _finish(
        session,
        state,
        actor,
        now,
        summary=f"Removed draft rule {_q(str(match.get('name') or rule_id))}",
        rule_version=draft.version,
        before={"draft": before_draft},
        after={"draft": _draft_snapshot(draft)},
        request_id=request_id,
        correlation_id=correlation_id,
    )
    payload = _response(session, actor, state)
    session.commit()
    return payload


def set_builtin_retired(
    session: Session,
    actor: Any,
    version: int,
    rule_id: str,
    retired: bool,
    *,
    request_id: str,
    correlation_id: str,
) -> RuleLibraryResponse:
    _authorize(actor, "setBuiltinRetired", request_id)
    state = _lock(session, version)
    if rule_id not in BUILTIN_IDS:
        _fail(404, "NOT_FOUND", "Rule not found.", {"resource": "builtinRule", "id": rule_id})
    now = _now()
    draft = _open_draft(session)
    before_draft = _draft_snapshot(draft) if draft is not None else None
    if draft is None:
        published = _require_version_row(session, state.published_version)
        draft = _insert_draft(session, actor, published, now)
    disabled = list(draft.disabled or [])
    if retired:
        if rule_id not in disabled:
            disabled.append(rule_id)
        summary = f"Retired builtin rule {rule_id} in the draft"
    else:
        disabled = [item for item in disabled if item != rule_id]
        summary = f"Restored builtin rule {rule_id}"
    draft.disabled = disabled
    flag_modified(draft, "disabled")
    draft.updated_by = actor.id
    draft.updated_at = now
    _finish(
        session,
        state,
        actor,
        now,
        summary=summary,
        rule_version=draft.version,
        before={"draft": before_draft},
        after={"draft": _draft_snapshot(draft)},
        request_id=request_id,
        correlation_id=correlation_id,
    )
    payload = _response(session, actor, state)
    session.commit()
    return payload


def save_builtin_override(
    session: Session,
    actor: Any,
    version: int,
    rule_id: str,
    override: dict[str, Any],
    *,
    request_id: str,
    correlation_id: str,
) -> RuleLibraryResponse:
    _authorize(actor, "saveBuiltinOverride", request_id)
    state = _lock(session, version)
    if rule_id not in BUILTIN_IDS:
        _fail(404, "NOT_FOUND", "Rule not found.", {"resource": "builtinRule", "id": rule_id})
    cleaned = _validate_override(override)
    now = _now()
    draft = _open_draft(session)
    before_draft = _draft_snapshot(draft) if draft is not None else None
    if draft is None:
        published = _require_version_row(session, state.published_version)
        draft = _insert_draft(session, actor, published, now)
    overrides = dict(_json_dict(draft.overrides))
    overrides[rule_id] = cleaned
    draft.overrides = overrides
    flag_modified(draft, "overrides")
    draft.updated_by = actor.id
    draft.updated_at = now
    _finish(
        session,
        state,
        actor,
        now,
        summary=f"Updated builtin rule {_q(cleaned['name'])}",
        rule_version=draft.version,
        before={"draft": before_draft},
        after={"draft": _draft_snapshot(draft)},
        request_id=request_id,
        correlation_id=correlation_id,
    )
    payload = _response(session, actor, state)
    session.commit()
    return payload


def discard_rule_draft(
    session: Session,
    actor: Any,
    version: int,
    *,
    request_id: str,
    correlation_id: str,
) -> RuleLibraryResponse:
    _authorize(actor, "discardRuleDraft", request_id)
    state = _lock(session, version)
    draft = _open_draft(session)
    if draft is None:
        _fail(422, "GATE_FAILED", NO_DRAFT_DISCARD, {"gate": "NO_DRAFT"})
    now = _now()
    before_draft = _draft_snapshot(draft)
    draft.status = "DISCARDED"
    draft.closed_by = actor.id
    draft.closed_at = now
    draft.updated_by = actor.id
    draft.updated_at = now
    _finish(
        session,
        state,
        actor,
        now,
        summary="Discarded the unpublished rule draft",
        rule_version=draft.version,
        before={"draft": before_draft},
        after={"draft": None},
        request_id=request_id,
        correlation_id=correlation_id,
    )
    payload = _response(session, actor, state)
    session.commit()
    return payload


def publish_rule_draft(
    session: Session,
    actor: Any,
    version: int,
    *,
    approval_ids: list[str] | None,
    request_id: str,
    correlation_id: str,
) -> RuleLibraryResponse:
    _authorize_publish(actor, request_id)
    state = _lock(session, version)
    draft = _open_draft(session)
    if draft is None:
        _fail(422, "GATE_FAILED", NO_DRAFT_PUBLISH, {"gate": "NO_DRAFT"})
    if _four_eyes() and draft.started_by == actor.id:
        _deny(
            actor,
            "publishRuleDraft",
            request_id,
            403,
            "FORBIDDEN",
            "The approver must be a different person from the submitter.",
            {"reason": "FOUR_EYES"},
        )
    _validate_draft_contents(draft)
    if _four_eyes():
        _require_approvals(session, draft, approval_ids or [])
    base = _require_version_row(session, draft.base_version)
    builtin_version = str(base.builtin_version)
    try:
        assert_builtin_parity(builtin_version)
    except ServiceError:
        raise
    except Exception as exc:
        if _is_api_error(exc):
            raise
        _fail(422, "GATE_FAILED", PARITY_MESSAGE, {"gate": "RULE_PARITY", "case": type(exc).__name__})
    published_adjustments = _adjustments_of_row(base)
    draft_adjustments = _adjustments_of_row(draft)
    report = _preview_report(
        builtin_version=builtin_version,
        compared_to=str(state.published_version),
        published=published_adjustments,
        draft=draft_adjustments,
    )
    now = _now()
    version_name = _published_name(session, str(draft.version))
    enabled_extras = [item for item in _json_list(draft.extras) if item.get("enabled") is True]
    models = _models()
    snapshot = models["version"](
        version=version_name,
        kind="PUBLISHED",
        builtin_version=builtin_version,
        extras=enabled_extras,
        disabled=list(draft.disabled or []),
        overrides=_json_dict(draft.overrides),
        published_by=actor.id,
        published_at=now,
        parity_report=report,
    )
    session.add(snapshot)
    session.flush()
    before = {
        "publishedVersion": state.published_version,
        "draft": _draft_snapshot(draft),
    }
    previous_published = state.published_version
    state.published_version = version_name
    draft.status = "PUBLISHED"
    draft.closed_by = actor.id
    draft.closed_at = now
    draft.updated_by = actor.id
    draft.updated_at = now
    draft.published_version = version_name
    after: dict[str, Any] = {
        "publishedVersion": version_name,
        "previousPublishedVersion": previous_published,
        "draft": None,
        "parityReport": report,
        "startedBy": draft.started_by,
        "publishedBy": actor.id,
    }
    if approval_ids:
        after["approvalIds"] = list(approval_ids)
    _finish(
        session,
        state,
        actor,
        now,
        summary=f"Published rule version {version_name}. New cases use it; existing cases keep their pinned version.",
        rule_version=version_name,
        before=before,
        after=after,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    payload = _response(session, actor, state)
    session.commit()
    return payload


def get_rule_library(session: Session, actor: Any) -> RuleLibraryResponse:
    state = _load_state(session)
    return _response(session, actor, state)


def evaluate_rules(session: Session, actor: Any, target: str, account_input: dict[str, Any]) -> EvaluateResponse:
    if target not in {"PUBLISHED", "DRAFT"}:
        _fail(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": [{"path": "target", "message": "target must be PUBLISHED or DRAFT."}]})
    state = _load_state(session)
    if target == "DRAFT":
        from fcc_api.auth.policy import require_evaluate

        require_evaluate(actor, "DRAFT", request_id=None)
        draft = _open_draft(session)
        if draft is None:
            _fail(422, "GATE_FAILED", NO_DRAFT_EVALUATE, {"gate": "NO_DRAFT"})
        adjustments = _adjustments_of_row(draft)
        rule_version = str(draft.version)
        base = _require_version_row(session, draft.base_version)
        builtin_version = str(base.builtin_version)
    else:
        published = _require_version_row(session, state.published_version)
        adjustments = _public_adjustments(published)
        rule_version = str(published.version)
        builtin_version = str(published.builtin_version)
    account = tester_account(account_input, rule_version)
    requirements = assemble_requirements(account, adjustments, builtin_version)
    return EvaluateResponse(
        rule_version=rule_version,
        requirements=[RequirementOut.model_validate(item) for item in requirements],
    )


def assert_builtin_parity(builtin_version: str) -> None:
    """Run the parity fixture, or the documented P-* cases when the fixture is absent."""
    cases = _parity_cases()
    for case in cases:
        name = str(case.get("name") or case.get("fn"))
        expected = case["expected"] if "expected" in case else case.get("expectedIds")
        try:
            actual = _run_parity_case(case, builtin_version)
        except ServiceError:
            raise
        except Exception as exc:
            if _is_api_error(exc):
                raise
            _fail(422, "GATE_FAILED", PARITY_MESSAGE, {"gate": "RULE_PARITY", "case": name, "error": type(exc).__name__})
        if not _matches(actual, expected):
            _fail(422, "GATE_FAILED", PARITY_MESSAGE, {"gate": "RULE_PARITY", "case": name})


def assemble_requirements(account: dict[str, Any], adjustments: dict[str, Any], builtin_version: str) -> list[dict[str, Any]]:
    """Builtin requirements, then library extras. Custom case requirements are not included."""
    generated = _generate(account, builtin_version)
    disabled = set(adjustments.get("disabled") or [])
    overrides = adjustments.get("overrides") or {}
    items: list[dict[str, Any]] = []
    for item in generated:
        if item["id"] in disabled:
            continue
        items.append(_apply_override(item, overrides.get(item["id"])))
    items.extend(_extra_requirements(list(adjustments.get("extras") or []), account))
    return items


def tester_account(account_input: dict[str, Any], rule_version: str) -> dict[str, Any]:
    """The rule-page tester: a root entity plus one 100% controlling signer."""
    entity_type = account_input["entityType"] if "entityType" in account_input else account_input["entity_type"]
    tax = account_input["taxResidency"] if "taxResidency" in account_input else account_input["tax_residency"]
    features = list(account_input.get("features") or [])
    trusted = account_input["trustedContact"] if "trustedContact" in account_input else account_input.get("trusted_contact", False)
    us_person = account_input["usPerson"] if "usPerson" in account_input else account_input.get("us_person", False)
    pep = account_input.get("pep", False)
    return {
        "id": "test",
        "version": 1,
        "legalName": "Test",
        "entityType": entity_type,
        "status": "BUILDING",
        "ruleVersion": rule_version,
        "createdAt": "",
        "updatedAt": "",
        "parties": [
            {
                "id": "r",
                "parentId": None,
                "kind": "ENTITY",
                "legalName": "Test",
                "entityType": entity_type,
                "ownershipPercent": 100,
                "isController": False,
                "isSigningAuthority": False,
                "isUsPerson": False,
                "isPepHio": False,
            },
            {
                "id": "p",
                "parentId": "r",
                "kind": "PERSON",
                "legalName": "Person",
                "ownershipPercent": 100,
                "isController": True,
                "isSigningAuthority": True,
                "isUsPerson": bool(us_person),
                "isPepHio": bool(pep),
            },
        ],
        "profile": {"province": "ON", "taxResidency": tax, "features": features, "trustedContact": bool(trusted)},
    }


def _authorize(actor: Any, operation: str, request_id: str) -> None:
    if operation == "publishRuleDraft" and _four_eyes():
        _authorize_publish(actor, request_id)
        return
    policy_fn = _policy_attr("require_operation")
    if policy_fn is not None and operation != "evaluateRuleDraft":
        policy_fn(actor, operation, request_id=request_id or None)
        return
    allowed = {"ADMIN"}
    if operation == "evaluateRuleDraft":
        allowed = {"ADMIN"}
    elif operation in LIBRARY_OPERATIONS and _four_eyes() and operation == "publishRuleDraft":
        allowed = {"COMPLIANCE"}
    if getattr(actor, "role", None) not in allowed:
        message = "Only an admin can publish a rule version." if operation == "publishRuleDraft" else "Only an admin can draft rules."
        if operation == "evaluateRuleDraft":
            message = "Only an admin can preview a rule draft."
        _deny(actor, operation, request_id, 403, "FORBIDDEN", message, {"reason": "ROLE", "role": getattr(actor, "role", None), "operation": operation})


def _authorize_publish(actor: Any, request_id: str) -> None:
    if _four_eyes():
        if getattr(actor, "role", None) != "COMPLIANCE":
            _deny(
                actor,
                "publishRuleDraft",
                request_id,
                403,
                "FORBIDDEN",
                "Only Compliance can publish a rule version.",
                {"reason": "ROLE", "role": getattr(actor, "role", None), "operation": "publishRuleDraft"},
            )
        return
    policy_fn = _policy_attr("require_operation")
    if policy_fn is not None:
        try:
            policy_fn(actor, "publishRuleDraft")
            return
        except Exception as exc:
            if _is_api_error(exc):
                raise
    if getattr(actor, "role", None) != "ADMIN":
        _deny(
            actor,
            "publishRuleDraft",
            request_id,
            403,
            "FORBIDDEN",
            "Only an admin can publish a rule version.",
            {"reason": "ROLE", "role": getattr(actor, "role", None), "operation": "publishRuleDraft"},
        )


def _lock(session: Session, client_version: int) -> Any:
    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    models = _models()
    try:
        state = session.execute(select(models["state"]).where(models["state"].id == 1).with_for_update()).scalar_one()
    except OperationalError as exc:
        session.rollback()
        if _is_lock_timeout(exc):
            _fail(
                409,
                "VERSION_CONFLICT",
                CONFLICT_MESSAGE,
                {"resource": "ruleLibrary", "reason": "LOCK_TIMEOUT", "clientVersion": client_version},
            )
        raise
    if int(state.version) != int(client_version):
        current = int(state.version)
        session.rollback()
        _fail(
            409,
            "VERSION_CONFLICT",
            CONFLICT_MESSAGE,
            {"resource": "ruleLibrary", "clientVersion": int(client_version), "currentVersion": current},
        )
    return state


def _finish(
    session: Session,
    state: Any,
    actor: Any,
    now: datetime,
    *,
    summary: str,
    rule_version: str,
    before: dict[str, Any],
    after: dict[str, Any],
    request_id: str,
    correlation_id: str,
) -> None:
    state.version = int(state.version) + 1
    state.updated_by = actor.id
    state.updated_at = now
    _append_audit(
        session,
        actor=actor,
        summary=summary,
        version=int(state.version),
        rule_version=rule_version,
        before=before,
        after=after,
        at=now,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    session.flush()


def _insert_draft(session: Session, actor: Any, published: Any, now: datetime) -> Any:
    models = _models()
    draft = models["draft"](
        id=_new_id("rdr"),
        version=_draft_name(session, now),
        base_version=published.version,
        extras=copy.deepcopy(_json_list(published.extras)),
        disabled=list(published.disabled or []),
        overrides=copy.deepcopy(_json_dict(published.overrides)),
        status="OPEN",
        started_by=actor.id,
        started_at=now,
        updated_by=actor.id,
        updated_at=now,
        closed_by=None,
        closed_at=None,
        published_version=None,
    )
    session.add(draft)
    session.flush()
    return draft


def _append_audit(
    session: Session,
    *,
    actor: Any,
    summary: str,
    version: int,
    rule_version: str,
    before: dict[str, Any],
    after: dict[str, Any],
    at: datetime,
    request_id: str,
    correlation_id: str,
) -> None:
    event = _models()["audit"](
        id=_new_id("evt"),
        scope="RULE_LIBRARY",
        case_id=None,
        actor_id=actor.id,
        action="RULE_LIBRARY",
        summary=summary,
        at=at,
        version=version,
        changes=None,
        ai_model=None,
        ai_accepted=None,
        ai_rule_version=None,
        rule_version=rule_version,
        before_value=before,
        after_value=after,
        correlation_id=correlation_id,
        request_id=request_id,
    )
    session.add(event)


def _response(session: Session, actor: Any, state: Any) -> RuleLibraryResponse:
    published = _require_version_row(session, state.published_version)
    adjustments = _public_adjustments(published)
    draft = _open_draft(session)
    draft_out = None
    if draft is not None:
        draft_out = RuleDraftOut(
            version=str(draft.version),
            extras=[_rule_out(item) for item in _json_list(draft.extras)],
            disabled=list(draft.disabled or []),
            overrides={key: BuiltinOverrideOut.model_validate(value) for key, value in _json_dict(draft.overrides).items()},
        )
    return RuleLibraryResponse(
        version=int(state.version),
        published_version=str(published.version),
        published_extras=[_rule_out(item) for item in adjustments["extras"]],
        published_disabled=list(adjustments["disabled"]),
        published_overrides={key: BuiltinOverrideOut.model_validate(value) for key, value in adjustments["overrides"].items()},
        draft=draft_out,
        pinned_case_count=_pinned_count(session, actor, str(published.version)),
        builtins=[BuiltinRuleOut.model_validate(item) for item in _builtin_display()],
    )


def _public_adjustments(row: Any) -> dict[str, Any]:
    if str(getattr(row, "kind", "")) == "BUILTIN" or str(row.version) == RULE_VERSION_DEMO:
        return {"extras": [], "disabled": [], "overrides": {}}
    return _adjustments_of_row(row)


def _adjustments_of_row(row: Any) -> dict[str, Any]:
    return {
        "extras": copy.deepcopy(_json_list(getattr(row, "extras", None))),
        "disabled": list(getattr(row, "disabled", None) or []),
        "overrides": copy.deepcopy(_json_dict(getattr(row, "overrides", None))),
    }


def _pinned_count(session: Session, actor: Any, published_version: str) -> int:
    from fcc_api.services.queries import visible_case_clause

    case_model = _table_model("fcc_api.db.models.cases", "cases")
    clause = visible_case_clause(case_model, actor)
    rows = session.scalars(select(case_model.id).where(clause, case_model.rule_version == published_version)).all()
    return len(rows)


def _validate_draft_contents(draft: Any) -> None:
    extras = _json_list(draft.extras)
    seen: set[str] = set()
    fields: list[dict[str, str]] = []
    for index, item in enumerate(extras):
        if not isinstance(item, dict):
            fields.append({"path": f"draft.extras[{index}]", "message": "Extra rule is invalid."})
            continue
        try:
            cleaned = _validate_library_rule(item)
        except ServiceError as exc:
            detail_fields = exc.details.get("fields") if isinstance(exc.details, dict) else None
            if detail_fields:
                fields.extend(detail_fields)
            else:
                fields.append({"path": f"draft.extras[{index}]", "message": exc.message})
            continue
        if cleaned["id"] in seen:
            fields.append({"path": f"draft.extras[{index}].id", "message": "Extra rule ids must be unique."})
        seen.add(cleaned["id"])
    disabled = list(draft.disabled or [])
    if any(item not in BUILTIN_IDS for item in disabled):
        fields.append({"path": "draft.disabled", "message": "disabled must be a subset of builtin rule ids."})
    overrides = _json_dict(draft.overrides)
    if any(key not in BUILTIN_IDS for key in overrides):
        fields.append({"path": "draft.overrides", "message": "override keys must be builtin rule ids."})
    if fields:
        _fail(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": fields})


def _validate_library_rule(rule: dict[str, Any]) -> dict[str, Any]:
    rule_id = str(rule.get("id") or "")
    fields: list[dict[str, str]] = []
    if not _is_rule_id(rule_id):
        fields.append({"path": "rule.id", "message": "Rule id must match ^[A-Za-z0-9_-]{1,64}$."})
    elif rule_id in BUILTIN_IDS:
        fields.append({"path": "rule.id", "message": "Rule id must not match a builtin rule."})
    name = str(rule.get("name") or "").strip()
    if not 2 <= len(name) <= 200:
        fields.append({"path": "rule.name", "message": "Name must be 2–200 characters after trimming."})
    section = rule.get("section")
    if section not in SECTIONS:
        fields.append({"path": "rule.section", "message": "Section is not one of the rule library sections."})
    source = str(rule.get("source") or "")
    reason = str(rule.get("reason") or "")
    if len(source) > 500:
        fields.append({"path": "rule.source", "message": "Source must be at most 500 characters."})
    if len(reason) > 500:
        fields.append({"path": "rule.reason", "message": "Reason must be at most 500 characters."})
    trigger = rule.get("trigger") or {}
    if not isinstance(trigger, dict):
        fields.append({"path": "rule.trigger", "message": "Trigger is invalid."})
        trigger = {}
    problems = trigger_problems(str(trigger.get("kind") or ""), trigger.get("entityTypes", trigger.get("entity_types")), trigger.get("taxResidencies", trigger.get("tax_residencies")), trigger.get("feature"))
    for problem in problems:
        fields.append({"path": "rule.trigger", "message": problem})
    if fields:
        _fail(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": fields})
    kind = trigger.get("kind")
    stored_trigger: dict[str, Any] = {"kind": kind}
    if kind == "ENTITY":
        stored_trigger["entityTypes"] = list(trigger.get("entityTypes") or trigger.get("entity_types") or [])
    elif kind == "TAX":
        stored_trigger["taxResidencies"] = list(trigger.get("taxResidencies") or trigger.get("tax_residencies") or [])
    elif kind == "FEATURE":
        stored_trigger["feature"] = trigger.get("feature")
    return {
        "id": rule_id,
        "name": name,
        "section": section,
        "conditional": bool(rule.get("conditional")),
        "source": source,
        "reason": reason,
        "enabled": bool(rule.get("enabled")),
        "trigger": stored_trigger,
    }


def _validate_override(override: dict[str, Any]) -> dict[str, Any]:
    fields: list[dict[str, str]] = []
    name = str(override.get("name") or "").strip()
    if not 2 <= len(name) <= 200:
        fields.append({"path": "override.name", "message": "Name must be 2–200 characters after trimming."})
    if override.get("section") not in SECTIONS:
        fields.append({"path": "override.section", "message": "Section is not one of the rule library sections."})
    source = str(override.get("source") or "")
    reason = str(override.get("reason") or "")
    if not 1 <= len(source) <= 500:
        fields.append({"path": "override.source", "message": "Source must be 1–500 characters."})
    if not 1 <= len(reason) <= 500:
        fields.append({"path": "override.reason", "message": "Reason must be 1–500 characters."})
    if fields:
        _fail(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": fields})
    return {
        "name": name,
        "section": override.get("section"),
        "conditional": bool(override.get("conditional")),
        "source": source,
        "reason": reason,
    }


def _require_approvals(session: Session, draft: Any, approval_ids: list[str]) -> None:
    if not approval_ids:
        _fail(422, "GATE_FAILED", "Every changed rule needs an unexpired Compliance approval.", {"gate": "RULE_APPROVAL"})
    try:
        model = _table_model("fcc_api.db.models.rules", "rule_approvals")
    except Exception:
        _fail(422, "GATE_FAILED", "Every changed rule needs an unexpired Compliance approval.", {"gate": "RULE_APPROVAL"})
    rows = session.scalars(select(model).where(model.id.in_(approval_ids))).all()
    approved = {row.id for row in rows if getattr(row, "status", None) == "APPROVED"}
    if len(approved) != len(set(approval_ids)):
        _fail(422, "GATE_FAILED", "Every changed rule needs an unexpired Compliance approval.", {"gate": "RULE_APPROVAL"})


def _preview_report(
    *,
    builtin_version: str,
    compared_to: str,
    published: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in _parity_cases():
        if _fn_name(str(case.get("fn") or "")) != "generate_requirements":
            continue
        account = _account_from_case_input(case.get("input") or {})
        published_ids = [item["id"] for item in assemble_requirements(account, _ignore_demo(published, compared_to), builtin_version)]
        draft_ids = [item["id"] for item in assemble_requirements(account, draft, builtin_version)]
        results.append(_diff_row(str(case.get("name")), published_ids, draft_ids))
    for entity_type in ENTITY_TYPES:
        for tax in TAX_RESIDENCIES:
            account = tester_account(
                {"entityType": entity_type, "taxResidency": tax, "features": [], "trustedContact": False, "usPerson": False, "pep": False},
                compared_to,
            )
            published_ids = [item["id"] for item in assemble_requirements(account, _ignore_demo(published, compared_to), builtin_version)]
            draft_ids = [item["id"] for item in assemble_requirements(account, draft, builtin_version)]
            results.append(_diff_row(f"{entity_type} / {tax}", published_ids, draft_ids))
    return {"engine": "pass", "builtinVersion": builtin_version, "comparedTo": compared_to, "results": results}


def _ignore_demo(adjustments: dict[str, Any], version: str) -> dict[str, Any]:
    if version == RULE_VERSION_DEMO:
        return {"extras": [], "disabled": [], "overrides": {}}
    return adjustments


def _diff_row(name: str, published_ids: list[str], draft_ids: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "published": published_ids,
        "draft": draft_ids,
        "added": [item for item in draft_ids if item not in published_ids],
        "removed": [item for item in published_ids if item not in draft_ids],
    }


def _generate(account: dict[str, Any], builtin_version: str) -> list[dict[str, Any]]:
    from fcc_api.rules.domain import generate_requirements

    materialized = _materialize_account(account)
    try:
        raw = generate_requirements(materialized, builtin_version=builtin_version)
    except TypeError:
        raw = generate_requirements(materialized)
    return [_requirement_dict(item) for item in raw]


def _extra_requirements(rules: list[dict[str, Any]], account: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from fcc_api.rules.library import library_requirements
    except ImportError:
        return _local_extras(rules, account)
    materialized_rules = [_materialize_rule(rule) for rule in rules]
    materialized_account = _materialize_account(account)
    return [_requirement_dict(item) for item in library_requirements(materialized_rules, materialized_account)]


def _local_extras(rules: list[dict[str, Any]], account: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for rule in rules:
        if not _rule_applies(rule, account):
            continue
        items.append(
            {
                "id": rule["id"],
                "section": rule["section"],
                "name": rule["name"],
                "conditional": bool(rule["conditional"]),
                "source": rule.get("source") or "",
                "reason": rule.get("reason") or "",
                "partyIds": [],
            }
        )
    return items


def _rule_applies(rule: dict[str, Any], account: dict[str, Any]) -> bool:
    if not rule.get("enabled"):
        return False
    profile = account.get("profile") or {}
    trigger = rule.get("trigger") or {}
    kind = trigger.get("kind")
    parties = account.get("parties") or []
    if kind == "ALWAYS":
        return True
    if kind == "ENTITY":
        return account.get("entityType") in (trigger.get("entityTypes") or [])
    if kind == "TAX":
        return bool(profile) and profile.get("taxResidency") in (trigger.get("taxResidencies") or [])
    if kind == "FEATURE":
        feature = trigger.get("feature")
        return bool(feature) and feature in (profile.get("features") or [])
    if kind == "PERSON":
        return any(party.get("kind") == "PERSON" for party in parties)
    if kind == "PEP":
        return any(party.get("isPepHio") for party in parties)
    if kind == "US_PERSON":
        return profile.get("taxResidency") == "US" or any(party.get("isUsPerson") for party in parties)
    if kind == "TRUSTED_CONTACT":
        return bool(profile.get("trustedContact") or False)
    return False


def _apply_override(item: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    if not override:
        return item
    merged = dict(item)
    for key in ("name", "section", "conditional", "source", "reason"):
        if key in override:
            merged[key] = override[key]
    return merged


def _parity_cases() -> list[dict[str, Any]]:
    if _FIXTURE.is_file():
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        cases = payload.get("cases")
        if isinstance(cases, list) and cases:
            return cases
    return _fallback_parity_cases()


def _fallback_parity_cases() -> list[dict[str, Any]]:
    account = _base_account()
    parties = [
        account["parties"][0],
        {"id": "holdco", "parentId": "root", "kind": "ENTITY", "legalName": "Holdco", "entityType": "corporation", "ownershipPercent": 50, "isController": False, "isSigningAuthority": False, "isUsPerson": False, "isPepHio": False},
        {"id": "bob", "parentId": "holdco", "kind": "PERSON", "legalName": "Bob", "ownershipPercent": 60, "isController": False, "isSigningAuthority": False, "isUsPerson": False, "isPepHio": False},
        {"id": "carol", "parentId": "holdco", "kind": "PERSON", "legalName": "Carol", "ownershipPercent": 40, "isController": False, "isSigningAuthority": False, "isUsPerson": False, "isPepHio": False},
        {"id": "dan", "parentId": "root", "kind": "PERSON", "legalName": "Dan", "ownershipPercent": 50, "isController": False, "isSigningAuthority": False, "isUsPerson": False, "isPepHio": False},
        {"id": "erin", "parentId": "root", "kind": "PERSON", "legalName": "Erin", "ownershipPercent": 0, "isController": False, "isSigningAuthority": True, "isUsPerson": False, "isPepHio": False},
    ]
    canada = ["naaf", "formation", "resolution", "beneficial-owner", "directors", "identity", "margin"]
    return [
        {"name": "accepts a fully disclosed ownership structure", "fn": "validateOwnership", "input": {"parties": account["parties"]}, "expected": []},
        {"name": "adds account-feature requirements", "fn": "generateRequirements", "input": account, "expected": canada},
        {"name": "identifies indirect owners at or above 25% and controllers below it", "fn": "personsToIdentify", "input": {"parties": parties}, "expected": ["bob", "dan", "erin"]},
        {"name": "effective ownership", "fn": "effectiveOwnership", "input": {"parties": parties}, "expected": {"root": 100, "holdco": 50, "bob": 30, "carol": 20, "dan": 50, "erin": 0}},
        {"name": "links identity requirements to the persons that trigger them", "fn": "generateRequirements", "input": account, "expectedIds": {"identity": ["person"]}},
        {"name": "tax CANADA", "fn": "generateRequirements", "input": _with_tax(account, "CANADA"), "expected": canada},
        {"name": "tax US", "fn": "generateRequirements", "input": _with_tax(account, "US"), "expected": [*canada, "w9", "rc519"]},
        {"name": "tax MIXED", "fn": "generateRequirements", "input": _with_tax(account, "MIXED"), "expected": [*canada, "w8", "rc519"]},
        {"name": "tax INTERNATIONAL", "fn": "generateRequirements", "input": _with_tax(account, "INTERNATIONAL"), "expected": [*canada, "w8", "rc519", "nffe"]},
        {"name": "entity trust", "fn": "generateRequirements", "input": {**account, "entityType": "trust"}, "expected": ["naaf", "formation", "beneficial-owner", "identity", "margin"]},
        {"name": "validate profile", "fn": "validateProfile", "input": {"profile": account["profile"]}, "expected": []},
    ]


def _base_account() -> dict[str, Any]:
    return {
        "id": "case-1",
        "version": 1,
        "legalName": "Maple Holdings Inc.",
        "entityType": "corporation",
        "status": "BUILDING",
        "ruleVersion": "demo",
        "createdAt": "2026-10-04T00:00:00.000Z",
        "updatedAt": "2026-10-04T00:00:00.000Z",
        "parties": [
            {"id": "root", "parentId": None, "kind": "ENTITY", "legalName": "Maple Holdings Inc.", "entityType": "corporation", "ownershipPercent": 100, "isController": True, "isSigningAuthority": False, "isUsPerson": False, "isPepHio": False},
            {"id": "person", "parentId": "root", "kind": "PERSON", "legalName": "Alice Chen", "country": "Canada", "ownershipPercent": 100, "isController": True, "isSigningAuthority": True, "isUsPerson": False, "isPepHio": False},
        ],
        "profile": {"province": "ON", "taxResidency": "CANADA", "features": ["MARGIN"], "trustedContact": False},
    }


def _with_tax(account: dict[str, Any], tax: str) -> dict[str, Any]:
    cloned = copy.deepcopy(account)
    cloned["profile"] = {**cloned["profile"], "taxResidency": tax}
    return cloned


def _run_parity_case(case: dict[str, Any], builtin_version: str) -> Any:
    fn_name = _fn_name(str(case.get("fn") or ""))
    payload = case.get("input") or {}
    if fn_name == "generate_requirements":
        from fcc_api.rules.domain import generate_requirements

        account = _materialize_account(_account_from_case_input(payload))
        try:
            result = generate_requirements(account, builtin_version=builtin_version)
        except TypeError:
            result = generate_requirements(account)
        if "expectedIds" in case and "expected" not in case:
            return {item["id"]: item["partyIds"] for item in (_requirement_dict(row) for row in result)}
        if isinstance(case.get("expected"), list) and case["expected"] and isinstance(case["expected"][0], str):
            return [_requirement_dict(row)["id"] for row in result]
        return [_requirement_dict(row) for row in result]
    if fn_name == "identity_party_ids":
        from fcc_api.rules.domain import generate_requirements

        account = _materialize_account(payload)
        try:
            result = generate_requirements(account, builtin_version=builtin_version)
        except TypeError:
            result = generate_requirements(account)
        for row in result:
            item = _requirement_dict(row)
            if item["id"] == "identity":
                return list(item["partyIds"])
        return []
    if fn_name == "validate_ownership":
        from fcc_api.rules.domain import validate_ownership

        parties = _materialize_parties(payload.get("parties") or [])
        return validate_ownership(_party_carrier(parties))
    if fn_name == "persons_to_identify":
        from fcc_api.rules.domain import persons_to_identify

        return _party_ids(persons_to_identify(_materialize_parties(payload.get("parties") or [])))
    if fn_name == "effective_ownership":
        from fcc_api.rules.domain import effective_ownership

        return effective_ownership(_materialize_parties(payload.get("parties") or []))
    if fn_name == "validate_profile":
        from fcc_api.rules.domain import validate_profile

        profile = payload.get("profile") if "profile" in payload else payload
        return validate_profile(_materialize_profile(profile))
    raise RuntimeError(f"Unknown parity function {case.get('fn')}")


def _account_from_case_input(payload: dict[str, Any]) -> dict[str, Any]:
    if "entityType" in payload or "parties" in payload and "legalName" in payload:
        return payload
    if "parties" in payload and "profile" not in payload and "entityType" not in payload:
        return payload
    return payload


def _fn_name(name: str) -> str:
    mapping = {
        "validateOwnership": "validate_ownership",
        "validateProfile": "validate_profile",
        "generateRequirements": "generate_requirements",
        "personsToIdentify": "persons_to_identify",
        "effectiveOwnership": "effective_ownership",
        "identityPartyIds": "identity_party_ids",
        "validate_ownership": "validate_ownership",
        "validate_profile": "validate_profile",
        "generate_requirements": "generate_requirements",
        "persons_to_identify": "persons_to_identify",
        "effective_ownership": "effective_ownership",
        "identity_party_ids": "identity_party_ids",
    }
    return mapping.get(name, name)


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and expected and all(isinstance(value, list) for value in expected.values()):
        return _canon(actual) == _canon(expected)
    return _canon(actual) == _canon(expected)


def _canon(value: Any) -> Any:
    plain = _plain(value)
    if isinstance(plain, dict):
        return {
            _camel_key(str(key)): _canon(item)
            for key, item in plain.items()
            if item is not None and not str(key).startswith("_")
        }
    if isinstance(plain, list):
        if plain and isinstance(plain[0], dict) and "id" in plain[0] and "code" not in plain[0] and "message" not in plain[0]:
            return [_canon(item) for item in plain]
        return [_canon(item) for item in plain]
    if isinstance(plain, float) and plain.is_integer():
        return int(plain)
    return plain


def _requirement_dict(item: Any) -> dict[str, Any]:
    raw = _camel_dict(_plain(item))
    return {
        "id": raw.get("id"),
        "section": raw.get("section"),
        "name": raw.get("name"),
        "conditional": bool(raw.get("conditional")),
        "source": raw.get("source") or "",
        "reason": raw.get("reason") or "",
        "partyIds": list(raw.get("partyIds") or []),
    }


def _materialize_account(account: dict[str, Any]) -> Any:
    try:
        from fcc_api.rules.types import AccountCase
    except ImportError:
        return _Flex(account, nested={"parties", "profile"})
    try:
        return _build(AccountCase, account)
    except Exception:
        return _Flex(account, nested={"parties", "profile"})


def _materialize_parties(parties: list[dict[str, Any]]) -> Any:
    try:
        from fcc_api.rules.types import Party
    except ImportError:
        return [_Flex(party) for party in parties]
    built = []
    for party in parties:
        try:
            built.append(_build(Party, party))
        except Exception:
            return [_Flex(item) for item in parties]
    return built


def _materialize_profile(profile: dict[str, Any] | None) -> Any:
    if profile is None:
        return None
    try:
        from fcc_api.rules.types import AccountProfile
    except ImportError:
        return _Flex(profile)
    try:
        return _build(AccountProfile, profile)
    except Exception:
        return _Flex(profile)


def _materialize_rule(rule: dict[str, Any]) -> Any:
    try:
        from fcc_api.rules.types import LibraryRule
    except ImportError:
        return _Flex(rule, nested={"trigger"})
    try:
        return _build(LibraryRule, rule)
    except Exception:
        return _Flex(rule, nested={"trigger"})


def _party_carrier(parties: Any) -> Any:
    try:
        from fcc_api.rules.types import AccountCase
    except ImportError:
        return _Flex({"parties": parties})
    try:
        if parties and hasattr(parties[0], "__dataclass_fields__"):
            return _build(AccountCase, {"parties": [_plain(party) for party in parties]})
    except Exception:
        pass
    return _Flex({"parties": parties})


def _build(cls: Any, data: dict[str, Any]) -> Any:
    if hasattr(cls, "model_validate"):
        try:
            return cls.model_validate(data)
        except Exception:
            pass
    fields = getattr(cls, "__dataclass_fields__", None)
    if not fields:
        raise TypeError(cls)
    try:
        from typing import get_type_hints

        hints = get_type_hints(cls)
    except Exception:
        hints = {name: field.type for name, field in fields.items()}
    kwargs: dict[str, Any] = {}
    for name in fields:
        raw = _lookup(data, name)
        if raw is _MISSING:
            continue
        kwargs[name] = _convert(hints.get(name), raw)
    return cls(**kwargs)


def _convert(annotation: Any, raw: Any) -> Any:
    annotation = _unwrap_optional(annotation)
    origin = getattr(annotation, "__origin__", None)
    if origin is list and isinstance(raw, list):
        args = getattr(annotation, "__args__", ())
        item_cls = args[0] if args else None
        if isinstance(item_cls, type) and (hasattr(item_cls, "__dataclass_fields__") or hasattr(item_cls, "model_fields")):
            return [_build(item_cls, item) if isinstance(item, dict) else item for item in raw]
        return raw
    if isinstance(annotation, type) and isinstance(raw, dict) and (hasattr(annotation, "__dataclass_fields__") or hasattr(annotation, "model_fields")):
        return _build(annotation, raw)
    return raw


def _unwrap_optional(annotation: Any) -> Any:
    args = getattr(annotation, "__args__", None)
    if not args:
        return annotation
    non_none = [item for item in args if item is not type(None)]
    if len(non_none) == 1 and len(args) != len(non_none):
        return non_none[0]
    return annotation


def _lookup(data: dict[str, Any], field_name: str) -> Any:
    if field_name in data:
        return data[field_name]
    camel = _camel_key(field_name)
    if camel in data:
        return data[camel]
    snake = _snake(field_name)
    if snake in data:
        return data[snake]
    return _MISSING


class _Flex:
    def __init__(self, data: dict[str, Any], nested: set[str] | None = None) -> None:
        nested = nested or set()
        for key, value in data.items():
            snake = _snake(key)
            camel = _camel_key(snake)
            if key in nested or snake in nested or camel in nested:
                if isinstance(value, list):
                    value = [_Flex(item) if isinstance(item, dict) else item for item in value]
                elif isinstance(value, dict):
                    value = _Flex(value)
            setattr(self, key, value)
            setattr(self, snake, value)
            setattr(self, camel, value)


def _draft_name(session: Session, now: datetime) -> str:
    base = f"draft-{now.astimezone(timezone.utc).strftime('%Y-%m-%d')}"
    taken = set(session.scalars(select(_models()["draft"].version)).all())
    return _suffixed(taken, base)


def _published_name(session: Session, draft_version: str) -> str:
    base = draft_version.replace("draft-", "published-", 1)
    taken = set(session.scalars(select(_models()["version"].version)).all())
    return _suffixed(taken, base)


def _suffixed(taken: set[str], base: str) -> str:
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


def _open_draft(session: Session) -> Any | None:
    model = _models()["draft"]
    return session.scalars(select(model).where(model.status == "OPEN")).one_or_none()


def _load_state(session: Session) -> Any:
    model = _models()["state"]
    state = session.get(model, 1)
    if state is None:
        raise RuntimeError("rule_library_state is missing")
    return state


def _require_version_row(session: Session, version: str) -> Any:
    row = session.get(_models()["version"], version)
    if row is None:
        raise RuntimeError(f"rule version {version} is missing")
    return row


def _models() -> dict[str, Any]:
    return {
        "state": _table_model("fcc_api.db.models.rules", "rule_library_state"),
        "draft": _table_model("fcc_api.db.models.rules", "rule_drafts"),
        "version": _table_model("fcc_api.db.models.rules", "rule_versions"),
        "audit": _table_model("fcc_api.db.models.audit", "audit_events"),
    }


def _table_model(module_name: str, table_name: str) -> Any:
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    matches = [obj for _, obj in inspect.getmembers(module, inspect.isclass) if getattr(obj, "__tablename__", None) == table_name]
    if len(matches) != 1:
        raise ImportError(f"{module_name} has no single model for {table_name}")
    return matches[0]


def _builtin_display() -> list[dict[str, Any]]:
    try:
        from fcc_api.rules.builtin_catalog import BUILTINS
    except Exception:
        return BUILTIN_CATALOG
    rows = []
    for item in BUILTINS:
        if isinstance(item, dict):
            rows.append(item)
        else:
            rows.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "section": item.section,
                    "trigger": item.trigger,
                    "source": item.source,
                    "conditional": item.conditional,
                }
            )
    if {row["id"] for row in rows} != BUILTIN_IDS:
        return BUILTIN_CATALOG
    return rows


def _draft_snapshot(draft: Any | None) -> dict[str, Any] | None:
    if draft is None:
        return None
    return {
        "version": draft.version,
        "baseVersion": draft.base_version,
        "extras": _json_list(draft.extras),
        "disabled": list(draft.disabled or []),
        "overrides": _json_dict(draft.overrides),
        "status": draft.status,
    }


def _rule_out(item: dict[str, Any]) -> LibraryRuleOut:
    trigger = dict(item.get("trigger") or {})
    return LibraryRuleOut(
        id=item["id"],
        name=item["name"],
        section=item["section"],
        conditional=bool(item["conditional"]),
        source=item.get("source") or "",
        reason=item.get("reason") or "",
        enabled=bool(item.get("enabled")),
        trigger=RuleTriggerOut.model_validate(trigger),
    )


def _party_ids(result: Any) -> list[str]:
    plain = _plain(result)
    if isinstance(plain, list) and plain and isinstance(plain[0], dict):
        return [str(_camel_dict(item).get("id")) for item in plain]
    if isinstance(plain, list):
        return [str(item) for item in plain]
    return []


def _policy_attr(name: str) -> Any | None:
    try:
        from fcc_api.auth import policy as auth_policy
    except Exception:
        return None
    return getattr(auth_policy, name, None)


def _four_eyes() -> bool:
    try:
        from fcc_api.config import get_settings

        return bool(getattr(get_settings(), "four_eyes_publish", False))
    except Exception:
        return False


def _deny(
    actor: Any,
    operation: str,
    request_id: str,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
) -> None:
    logger.warning(
        "authorization_failed request_id=%s user_id=%s role=%s operation=%s case_id=%s reason=%s",
        request_id or None,
        getattr(actor, "id", None),
        getattr(actor, "role", None),
        operation,
        None,
        details.get("reason"),
    )
    _fail(status_code, code, message, details)


def _fail(status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
    try:
        from fcc_api.errors import ApiError
    except Exception:
        ApiError = ServiceError
    details = details or {}
    try:
        exc = ApiError(status_code=status_code, code=code, message=message, details=details)
    except TypeError:
        try:
            exc = ApiError(status_code, code, message, details)
        except TypeError:
            exc = ServiceError(status_code, code, message, details)
    if not hasattr(exc, "status_code"):
        exc.status_code = status_code
    if not hasattr(exc, "code"):
        exc.code = code
    if not hasattr(exc, "message"):
        exc.message = message
    if not hasattr(exc, "details"):
        exc.details = details
    raise exc


def _is_api_error(exc: Exception) -> bool:
    return getattr(exc, "code", None) in {
        "UNAUTHENTICATED",
        "FORBIDDEN",
        "NOT_FOUND",
        "VERSION_CONFLICT",
        "GATE_FAILED",
        "VALIDATION_FAILED",
    }


def _is_lock_timeout(exc: OperationalError) -> bool:
    origin = getattr(exc, "orig", None)
    sqlstate = getattr(origin, "sqlstate", None) or getattr(origin, "pgcode", None)
    if sqlstate == "55P03":
        return True
    text_value = str(exc).lower()
    return "lock timeout" in text_value or "lock_timeout" in text_value


def _is_rule_id(value: str) -> bool:
    if not 1 <= len(value) <= 64:
        return False
    return all(ch.isalnum() or ch in {"_", "-"} for ch in value)


def _new_id(prefix: str) -> str:
    try:
        from fcc_api.ids import new_id

        return new_id(prefix)
    except Exception:
        alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
        return prefix + "_" + "".join(secrets.choice(alphabet) for _ in range(8))


def _actor_name(actor: Any) -> str:
    return str(getattr(actor, "name", None) or actor.id)


def _q(name: str) -> str:
    return f"\u201c{name}\u201d"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        loaded = json.loads(value)
        return list(loaded)
    return list(value)


def _json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        loaded = json.loads(value)
        return dict(loaded)
    return dict(value)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    fields = getattr(value, "__dataclass_fields__", None)
    if fields is not None:
        return {key: _plain(getattr(value, key)) for key in fields}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "items"):
        return {str(key): _plain(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    return value


def _camel_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {_camel_key(str(key)): item for key, item in value.items()}


def _camel_key(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:] if part)


def _snake(name: str) -> str:
    chars: list[str] = []
    for char in name:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars).lstrip("_")
