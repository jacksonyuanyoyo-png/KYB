"""Submit and approve rule_approvals rows.

``content_sha256`` is the SHA-256 of canonical JSON for the display fields
name, section, conditional, source, reason, and trigger. Keys are sorted, there
is no insignificant whitespace, and the bytes are UTF-8. A builtin without an
override uses the rules-page default reason, which is the rule name. An open
draft extra or override replaces that display. The trigger for a builtin stays
the rules-page text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Text, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fcc_api.auth.actor import Actor, fail_auth
from fcc_api.db.models.rules import RuleDraft, RuleLibraryState, RuleVersion
from fcc_api.errors import not_found, unavailable, validation_failed, version_conflict
from fcc_api.ids import new_id
from fcc_api.rules.builtin_catalog import BUILTIN, BuiltinRule
from fcc_api.rules.constants import RULE_VERSION
from fcc_api.schemas.approvals import RuleApprovalListOut, RuleApprovalOut, SubmitRuleApprovalIn

_COLUMNS = """
    id, rule_id, builtin_version, owner_name, source_url,
    effective_on, review_due_on, test_ids, content_sha256,
    approval_status, submitted_by, approved_by, approval_ticket,
    submitted_at, approved_at
"""

_FIND_ID = text(f"SELECT {_COLUMNS} FROM rule_approvals WHERE id = :id")
_FIND_HASH = text(
    f"""
    SELECT {_COLUMNS}
    FROM rule_approvals
    WHERE rule_id = :rule_id
      AND builtin_version = :builtin_version
      AND content_sha256 = :content_sha256
    ORDER BY CASE WHEN approval_status = 'PENDING' THEN 0 ELSE 1 END, submitted_at DESC
    LIMIT 1
    """
)
_LIST = text(f"SELECT {_COLUMNS} FROM rule_approvals ORDER BY submitted_at DESC, id DESC")
_INSERT = text(
    """
    INSERT INTO rule_approvals (
        id, rule_id, builtin_version, owner_name, source_url,
        effective_on, review_due_on, test_ids, content_sha256,
        approval_status, submitted_by, approved_by, approval_ticket,
        submitted_at, approved_at
    ) VALUES (
        :id, :rule_id, :builtin_version, :owner_name, :source_url,
        :effective_on, :review_due_on, :test_ids, :content_sha256,
        'PENDING', :submitted_by, NULL, :approval_ticket,
        :submitted_at, NULL
    )
    """
).bindparams(bindparam("test_ids", type_=ARRAY(Text())))
_APPROVE = text(
    f"""
    UPDATE rule_approvals
    SET approval_status = 'APPROVED',
        approved_by = :approved_by,
        approved_at = :approved_at,
        approval_ticket = :approval_ticket
    WHERE id = :id
      AND approval_status = 'PENDING'
      AND submitted_by <> :approved_by
      AND effective_on IS NOT NULL
      AND review_due_on IS NOT NULL
    RETURNING {_COLUMNS}
    """
)

FOUR_EYES_MESSAGE = "The approver must be a different person from the submitter."


@dataclass(frozen=True)
class SubmitResult:
    approval: RuleApprovalOut
    created: bool


def catalog_display(rule_id: str) -> dict[str, Any] | None:
    """Display fields for one builtin, before a draft override is applied."""

    rule = _builtins().get(rule_id)
    if rule is None:
        return None
    return _builtin_display(rule)


def content_sha256(display: Mapping[str, Any]) -> str:
    """SHA-256 hex of the canonical display JSON."""

    encoded = canonical_display_json(display).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_display_json(display: Mapping[str, Any]) -> str:
    payload = canonical_display(display)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_display(display: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "conditional": bool(display["conditional"]),
        "name": str(display["name"]),
        "reason": "" if display.get("reason") is None else str(display.get("reason")),
        "section": str(display["section"]),
        "source": str(display["source"]),
        "trigger": _trigger_value(display.get("trigger")),
    }


def display_fields_for_rule(session: Session, rule_id: str) -> dict[str, Any]:
    """Current builtin catalog, or the open draft when that rule is in it."""

    draft = _open_draft(session)
    if draft is not None:
        for extra in _json_list(draft.extras):
            if str(extra.get("id")) == rule_id:
                return _library_display(extra)
    builtin = _builtins().get(rule_id)
    published = _published_version(session)
    if builtin is not None:
        display = _builtin_display(builtin)
        override = _override_for(draft, published, rule_id)
        if override is not None:
            display = _apply_override(display, override)
        return canonical_display(display)
    if _adjustments_apply(published):
        for extra in _json_list(published.extras):
            if str(extra.get("id")) == rule_id:
                return _library_display(extra)
    raise not_found("Rule not found.", {"resource": "rule", "id": rule_id})


def submit_rule_approval(
    session: Session,
    actor: Actor,
    body: SubmitRuleApprovalIn,
    *,
    request_id: str,
) -> SubmitResult:
    """Insert a PENDING row. The same pending hash returns the original row."""

    del request_id
    display = display_fields_for_rule(session, body.rule_id)
    expected_version = _expected_builtin_version(session, _open_draft(session))
    if body.builtin_version != expected_version:
        raise validation_failed(
            "Request body is invalid.",
            {
                "fields": [
                    {
                        "path": "builtinVersion",
                        "message": "builtinVersion does not match the published rule implementation.",
                    }
                ]
            },
        )
    digest = content_sha256(display)
    existing = _mapping(
        session,
        _FIND_HASH,
        {"rule_id": body.rule_id, "builtin_version": body.builtin_version, "content_sha256": digest},
    )
    if existing is not None:
        return _existing_submit(existing)

    approval_id = new_id("apr")
    params = {
        "id": approval_id,
        "rule_id": body.rule_id,
        "builtin_version": body.builtin_version,
        "owner_name": body.owner_name,
        "source_url": body.source_url,
        "effective_on": body.effective_on,
        "review_due_on": body.review_due_on,
        "test_ids": list(body.test_ids),
        "content_sha256": digest,
        "submitted_by": actor.id,
        "approval_ticket": body.approval_ticket,
        "submitted_at": datetime.now(UTC),
    }
    try:
        with session.begin_nested():
            session.execute(_INSERT, params)
    except IntegrityError as exc:
        if not _unique_violation(exc):
            raise
        raced = _mapping(
            session,
            _FIND_HASH,
            {"rule_id": body.rule_id, "builtin_version": body.builtin_version, "content_sha256": digest},
        )
        if raced is None:
            raise
        return _existing_submit(raced)
    session.commit()
    stored = _mapping(session, _FIND_ID, {"id": approval_id})
    if stored is None:
        raise unavailable("Rule approval could not be stored.")
    return SubmitResult(approval=_approval_out(stored), created=True)


def approve_rule_approval(
    session: Session,
    actor: Actor,
    approval_id: str,
    approval_ticket: str,
    *,
    request_id: str,
) -> RuleApprovalOut:
    """Mark one PENDING row APPROVED. Does not touch any other row."""

    row = _mapping(session, _FIND_ID, {"id": approval_id})
    if row is None:
        raise not_found("Rule approval not found.", {"resource": "ruleApproval", "id": approval_id})
    if row["submitted_by"] is not None and str(row["submitted_by"]) == actor.id:
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message=FOUR_EYES_MESSAGE,
            details={"reason": "FOUR_EYES"},
            reason="FOUR_EYES",
            operation="approveRuleApproval",
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )
    if str(row["approval_status"]) != "PENDING":
        raise version_conflict(
            "This rule approval is no longer pending.",
            {
                "resource": "ruleApproval",
                "id": approval_id,
                "approvalStatus": row["approval_status"],
            },
        )
    if row["effective_on"] is None or row["review_due_on"] is None:
        raise validation_failed(
            "Request body is invalid.",
            {
                "fields": [
                    {
                        "path": "approvalId",
                        "message": (
                            "A pending approval needs an effective date and a review date "
                            "before it can be approved."
                        ),
                    }
                ]
            },
        )
    approved_at = datetime.now(UTC)
    updated = session.execute(
        _APPROVE,
        {
            "id": approval_id,
            "approved_by": actor.id,
            "approved_at": approved_at,
            "approval_ticket": approval_ticket,
        },
    ).mappings().one_or_none()
    if updated is None:
        raise version_conflict(
            "This rule approval is no longer pending.",
            {"resource": "ruleApproval", "id": approval_id, "approvalStatus": row["approval_status"]},
        )
    session.commit()
    return _approval_out(updated)


def list_rule_approvals(session: Session, actor: Actor) -> RuleApprovalListOut:
    """Every signed-in role can read the table. The caller is not used as a filter."""

    if not actor.id:
        raise not_found("Rule approval not found.", {"resource": "ruleApproval", "id": ""})
    rows = session.execute(_LIST).mappings().all()
    return RuleApprovalListOut(items=[_approval_out(row) for row in rows])


def _existing_submit(row: Mapping[str, Any]) -> SubmitResult:
    if str(row["approval_status"]) == "PENDING":
        return SubmitResult(approval=_approval_out(row), created=False)
    raise version_conflict(
        "A rule approval already exists for this rule content.",
        {
            "resource": "ruleApproval",
            "id": row["id"],
            "approvalStatus": row["approval_status"],
        },
    )


def _approval_out(row: Mapping[str, Any]) -> RuleApprovalOut:
    test_ids = row["test_ids"] if row["test_ids"] is not None else []
    return RuleApprovalOut(
        id=str(row["id"]),
        rule_id=str(row["rule_id"]),
        builtin_version=str(row["builtin_version"]),
        owner_name=str(row["owner_name"] or ""),
        source_url=str(row["source_url"] or ""),
        effective_on=_as_date(row["effective_on"]),
        review_due_on=_as_date(row["review_due_on"]),
        test_ids=[str(item) for item in test_ids],
        content_sha256=None if row["content_sha256"] is None else str(row["content_sha256"]).strip(),
        approval_status=str(row["approval_status"]),
        submitted_by=None if row["submitted_by"] is None else str(row["submitted_by"]),
        approved_by=None if row["approved_by"] is None else str(row["approved_by"]),
        approval_ticket=None if row["approval_ticket"] is None else str(row["approval_ticket"]),
        submitted_at=row["submitted_at"],
        approved_at=row["approved_at"],
    )


def _override_for(draft: RuleDraft | None, published: RuleVersion, rule_id: str) -> dict[str, Any] | None:
    if draft is not None:
        override = _json_dict(draft.overrides).get(rule_id)
    elif _adjustments_apply(published):
        override = _json_dict(published.overrides).get(rule_id)
    else:
        override = None
    if isinstance(override, dict):
        return override
    return None


def _apply_override(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ("name", "section", "source", "reason"):
        if override.get(key) is not None:
            merged[key] = override[key]
    if override.get("conditional") is not None:
        merged["conditional"] = bool(override["conditional"])
    return merged


def _builtin_display(rule: BuiltinRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "section": rule.section,
        "conditional": rule.conditional,
        "source": rule.source,
        "reason": rule.name,
        "trigger": rule.trigger,
    }


def _library_display(rule: Mapping[str, Any]) -> dict[str, Any]:
    return canonical_display(
        {
            "name": rule.get("name") or "",
            "section": rule.get("section") or "",
            "conditional": bool(rule.get("conditional")),
            "source": rule.get("source") or "",
            "reason": rule.get("reason") or "",
            "trigger": rule.get("trigger"),
        }
    )


def _trigger_value(trigger: Any) -> Any:
    if isinstance(trigger, str) or trigger is None:
        return trigger
    if not isinstance(trigger, Mapping):
        return trigger
    kind = trigger.get("kind")
    normalized: dict[str, Any] = {"kind": kind}
    if kind == "ENTITY":
        values = trigger.get("entityTypes", trigger.get("entity_types")) or []
        normalized["entityTypes"] = list(values)
    elif kind == "TAX":
        values = trigger.get("taxResidencies", trigger.get("tax_residencies")) or []
        normalized["taxResidencies"] = list(values)
    elif kind == "FEATURE":
        normalized["feature"] = trigger.get("feature")
    return normalized


def _expected_builtin_version(session: Session, draft: RuleDraft | None) -> str:
    if draft is not None:
        base = session.get(RuleVersion, draft.base_version)
        if base is not None:
            return str(base.builtin_version)
    return str(_published_version(session).builtin_version)


def _published_version(session: Session) -> RuleVersion:
    state = session.get(RuleLibraryState, 1)
    if state is None:
        raise unavailable("Rule library is not available.")
    row = session.get(RuleVersion, state.published_version)
    if row is None:
        raise unavailable("Rule library is not available.")
    return row


def _open_draft(session: Session) -> RuleDraft | None:
    return session.scalars(select(RuleDraft).where(RuleDraft.status == "OPEN")).one_or_none()


def _adjustments_apply(row: RuleVersion) -> bool:
    return row.kind != "BUILTIN" and row.version != RULE_VERSION


def _builtins() -> dict[str, BuiltinRule]:
    return {item.id: item for item in BUILTIN}


def _json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _mapping(session: Session, statement: Any, params: dict[str, Any]) -> Mapping[str, Any] | None:
    return session.execute(statement, params).mappings().one_or_none()


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _unique_violation(exc: IntegrityError) -> bool:
    origin = getattr(exc, "orig", None)
    sqlstate = getattr(origin, "sqlstate", None) or getattr(origin, "pgcode", None)
    return sqlstate == "23505"
