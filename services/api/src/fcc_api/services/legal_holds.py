"""法律保全。原因里不存放证件号，也不把原因写进日志。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fcc_api.auth.actor import Role
from fcc_api.errors import ApiError
from fcc_api.ids import new_id
from fcc_api.schemas.rules import ApiModel
from fcc_api.services.queries import _iso

_SCOPES = frozenset({"CASE", "DOCUMENT"})
_HOLD_ROLES = frozenset({"COMPLIANCE", "ADMIN"})
_DIGIT_RUN = re.compile(r"\d{9,}")
_SIN_LIKE = re.compile(r"\b\d{3}[ -]?\d{3}[ -]?\d{3}\b")


class LegalHoldOut(ApiModel):
    id: str
    scope: str
    target_id: str
    reason: str
    created_by: str
    created_at: str
    released_by: str | None = None
    released_at: str | None = None


def create_legal_hold(
    session: Session,
    actor: Any,
    *,
    scope: str,
    target_id: str,
    reason: str,
    request_id: str | None = None,
) -> LegalHoldOut:
    del request_id
    _require_hold_role(actor, "createLegalHold")
    normalized_scope = scope.strip().upper()
    if normalized_scope not in _SCOPES:
        _invalid("scope", "scope must be CASE or DOCUMENT.")
    target = target_id.strip()
    if not target:
        _invalid("targetId", "targetId is required.")
    cleaned = reason.strip()
    if not 1 <= len(cleaned) <= 500:
        _invalid("reason", "Reason must be 1–500 characters.")
    registration_number = _target_registration_number(session, normalized_scope, target)
    if _contains_identifier(cleaned, registration_number):
        _invalid("reason", "Reason must not include an identification number.")
    hold_id = new_id("hld")
    created_at = datetime.now(UTC)
    try:
        with session.begin_nested():
            session.execute(
                text(
                    """
                    INSERT INTO legal_holds (
                        id, scope, target_id, reason, created_by, created_at
                    ) VALUES (
                        :id, :scope, :target_id, :reason, :created_by, :created_at
                    )
                    """
                ),
                {
                    "id": hold_id,
                    "scope": normalized_scope,
                    "target_id": target,
                    "reason": cleaned,
                    "created_by": actor.id,
                    "created_at": created_at,
                },
            )
            stored = session.execute(
                text("SELECT * FROM legal_holds WHERE id = :id"),
                {"id": hold_id},
            ).one()
    except SQLAlchemyError as exc:
        if _sqlstate(exc) == "42P01":
            raise ApiError(503, "UNAVAILABLE", "Legal holds are not available.", {}) from exc
        raise
    payload = _hold_out(stored)
    session.commit()
    return payload


def release_legal_hold(
    session: Session,
    actor: Any,
    hold_id: str,
    *,
    request_id: str | None = None,
) -> LegalHoldOut:
    del request_id
    _require_hold_role(actor, "releaseLegalHold")
    released_at = datetime.now(UTC)
    try:
        with session.begin_nested():
            current = session.execute(
                text(
                    """
                    SELECT id, released_at
                    FROM legal_holds
                    WHERE id = :id
                    FOR UPDATE
                    """
                ),
                {"id": hold_id},
            ).first()
            if current is None:
                raise ApiError(
                    404,
                    "NOT_FOUND",
                    "Legal hold not found.",
                    {"resource": "legalHold", "id": hold_id},
                )
            if current.released_at is not None:
                raise ApiError(
                    409,
                    "VERSION_CONFLICT",
                    "This legal hold has already been released.",
                    {"resource": "legalHold", "id": hold_id},
                )
            session.execute(
                text(
                    """
                    UPDATE legal_holds
                    SET released_by = :released_by, released_at = :released_at
                    WHERE id = :id AND released_at IS NULL
                    """
                ),
                {"id": hold_id, "released_by": actor.id, "released_at": released_at},
            )
            stored = session.execute(
                text("SELECT * FROM legal_holds WHERE id = :id"),
                {"id": hold_id},
            ).one()
    except SQLAlchemyError as exc:
        if _sqlstate(exc) == "42P01":
            raise ApiError(503, "UNAVAILABLE", "Legal holds are not available.", {}) from exc
        raise
    payload = _hold_out(stored)
    session.commit()
    return payload


def _require_hold_role(actor: Any, operation: str) -> None:
    role = _role_name(actor)
    if role in _HOLD_ROLES:
        return
    raise ApiError(
        403,
        "FORBIDDEN",
        "Your role cannot manage legal holds.",
        {"reason": "ROLE", "role": role, "operation": operation},
    )


def _target_registration_number(session: Session, scope: str, target_id: str) -> str | None:
    if scope == "CASE":
        row = session.execute(
            text("SELECT registration_number FROM cases WHERE id = :id"),
            {"id": target_id},
        ).first()
        if row is None:
            raise ApiError(
                404,
                "NOT_FOUND",
                "Case not found.",
                {"resource": "case", "id": target_id},
            )
        return None if row.registration_number is None else str(row.registration_number)
    row = session.execute(
        text(
            """
            SELECT c.registration_number
            FROM case_documents d
            JOIN cases c ON c.id = d.case_id
            WHERE d.id = :id
            """
        ),
        {"id": target_id},
    ).first()
    if row is None:
        raise ApiError(
            404,
            "NOT_FOUND",
            "Document not found.",
            {"resource": "document", "id": target_id},
        )
    return None if row.registration_number is None else str(row.registration_number)


def _contains_identifier(reason: str, registration_number: str | None) -> bool:
    if _DIGIT_RUN.search(reason) or _SIN_LIKE.search(reason):
        return True
    token = (registration_number or "").strip()
    if len(token) >= 3 and token.casefold() in reason.casefold():
        return True
    return False


def _hold_out(row: Any) -> LegalHoldOut:
    return LegalHoldOut(
        id=row.id,
        scope=row.scope,
        target_id=row.target_id,
        reason=row.reason,
        created_by=row.created_by,
        created_at=_iso(row.created_at) or "",
        released_by=row.released_by,
        released_at=_iso(row.released_at),
    )


def _role_name(actor: Any) -> str:
    role = getattr(actor, "role", "")
    if isinstance(role, Role):
        return role.value
    return str(role)


def _invalid(path: str, message: str) -> None:
    raise ApiError(
        400,
        "VALIDATION_FAILED",
        "Request body is invalid.",
        {"fields": [{"path": path, "message": message}]},
    )


def _sqlstate(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("sqlstate", "pgcode"):
            code = getattr(current, attr, None)
            if code:
                return str(code)
        nested = current.__cause__ or getattr(current, "orig", None)
        current = nested if isinstance(nested, BaseException) else None
    return None
