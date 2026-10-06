"""客户门户邀请与会话。

邮箱与令牌只以 SHA-256 写入 ``portal_invites``。明文邮箱不入库、不写日志。
文档没有单独的会话表，会话哈希与过期时间写在可空列
``session_token_hash``、``session_expires_at``。缺表或缺这些列时返回 503。
"""

from __future__ import annotations

import hashlib
import os
import secrets
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from fcc_api.auth.actor import Actor, Role
from fcc_api.auth.policy import case_visible
from fcc_api.db.models.cases import Case
from fcc_api.db.models.checklist import ChecklistItem
from fcc_api.errors import forbidden, not_found, unauthenticated, unavailable, validation_failed
from fcc_api.ids import new_id
from fcc_api.schemas.common import to_js_iso
from fcc_api.schemas.documents import CreateUploadsIn, CreateUploadsOut
from fcc_api.services.case_write import case_write

_INVITE_OPERATION = "createPortalInvite"
_CASE_WRITE_OPERATION = "updateProfile"
_SESSION_HOURS = 12
_DEFAULT_INVITE_DAYS = 7
_MISSING_SCHEMA = frozenset({"42P01", "42703"})
_SIGN_IN = "Sign in to continue."


@dataclass(frozen=True)
class PortalInvite:
    invite_path: str
    expires_at: str


@dataclass(frozen=True)
class PortalSession:
    token: str
    expires_at: str


@dataclass(frozen=True)
class PortalPrincipal:
    invite_id: str
    case_id: str
    created_by: str


def create_portal_invite(
    session: Session,
    actor: Actor,
    case_id: str,
    version: int,
    email: str,
    *,
    request_id: str,
    correlation_id: str,
) -> PortalInvite:
    """ADVISOR 与 OPERATIONS，且案件状态为 DOCS_REQUESTED。版本不一致由案件写入事务返回 409。"""

    normalized = _normalize_email(email)
    case = session.get(Case, case_id)
    if case is None or not case_visible(actor, case):
        raise not_found("Case not found.", {"resource": "case", "id": case_id})
    if actor.role not in {Role.ADVISOR, Role.OPERATIONS}:
        raise forbidden(
            "Your role cannot perform this operation.",
            {"reason": "ROLE", "role": actor.role.value, "operation": _INVITE_OPERATION},
        )
    if case.status != "DOCS_REQUESTED":
        raise _case_status(case.status)

    token = _new_secret()
    email_hash = _sha256(normalized)
    token_hash = _sha256(token)
    invite_id = new_id("inv")
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=version,
        operation=_CASE_WRITE_OPERATION,
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        if writer.case.status != "DOCS_REQUESTED":
            raise _case_status(writer.case.status)
        expires_at = writer.started_at + timedelta(days=_invite_ttl_days())
        _execute(
            session,
            """
            INSERT INTO portal_invites (
                id, case_id, email_sha256, token_hash, expires_at, created_by, created_at
            ) VALUES (
                :id, :case_id, :email_sha256, :token_hash, :expires_at, :created_by, :created_at
            )
            """,
            {
                "id": invite_id,
                "case_id": case_id,
                "email_sha256": email_hash,
                "token_hash": token_hash,
                "expires_at": expires_at,
                "created_by": actor.id,
                "created_at": writer.started_at,
            },
        )
        writer.audit(
            action="CHECKLIST_UPDATED",
            summary="Issued a customer portal invite.",
        )
    return PortalInvite(invite_path=f"/portal/invite/{token}", expires_at=to_js_iso(expires_at))


def open_portal_session(session: Session, token: str) -> PortalSession:
    """用邀请令牌换门户会话。库中只存会话哈希，有效期 12 小时。"""

    digest = _presented_digest(token)
    if digest is None:
        raise unauthenticated(_SIGN_IN)
    now = datetime.now(UTC)
    try:
        row = session.execute(
            text(
                """
                SELECT id, expires_at
                FROM portal_invites
                WHERE token_hash = :digest
                """
            ),
            {"digest": digest},
        ).mappings().first()
    except DBAPIError as exc:
        _schema_or_raise(session, exc)
        raise
    if row is None or _aware(row["expires_at"]) <= now:
        raise unauthenticated(_SIGN_IN)

    session_token = _new_secret()
    session_expires = now + timedelta(hours=_SESSION_HOURS)
    try:
        session.execute(
            text(
                """
                UPDATE portal_invites
                SET session_token_hash = :session_hash,
                    session_expires_at = :session_expires
                WHERE id = :id
                """
            ),
            {
                "session_hash": _sha256(session_token),
                "session_expires": session_expires,
                "id": row["id"],
            },
        )
        session.commit()
    except DBAPIError as exc:
        _schema_or_raise(session, exc)
        raise
    return PortalSession(token=session_token, expires_at=to_js_iso(session_expires))


def load_portal_session(session: Session, token: str) -> PortalPrincipal:
    digest = _presented_digest(token)
    if digest is None:
        raise unauthenticated(_SIGN_IN)
    now = datetime.now(UTC)
    try:
        row = session.execute(
            text(
                """
                SELECT id, case_id, created_by, session_expires_at
                FROM portal_invites
                WHERE session_token_hash = :digest
                """
            ),
            {"digest": digest},
        ).mappings().first()
    except DBAPIError as exc:
        _schema_or_raise(session, exc)
        raise
    if row is None or row["session_expires_at"] is None or _aware(row["session_expires_at"]) <= now:
        raise unauthenticated(_SIGN_IN)
    return PortalPrincipal(
        invite_id=str(row["id"]),
        case_id=str(row["case_id"]),
        created_by=str(row["created_by"]),
    )


def portal_checklist(session: Session, principal: PortalPrincipal) -> list[dict[str, str]]:
    """只返回清单项 id、name、status。"""

    case = session.get(Case, principal.case_id)
    if case is None:
        raise not_found("Case not found.", {"resource": "case", "id": principal.case_id})
    from fcc_api.services.cases import _bundle
    from fcc_api.services.checklist import analyze_rows

    bundle = _bundle(session, case)
    stored = {row.requirement_id: row.status for row in bundle.checklist}
    return [
        {"id": item.id, "name": item.name, "status": stored.get(item.id, "MISSING")}
        for item in analyze_rows(bundle)
    ]


def request_portal_upload(
    session: Session,
    principal: PortalPrincipal,
    body: CreateUploadsIn,
    *,
    request_id: str,
) -> CreateUploadsOut:
    """只给本门户案件、且状态不是 VERIFIED 的清单项申请上传槽。"""

    requirement_id = (body.requirement_id or "").strip()
    if not requirement_id:
        raise validation_failed(
            "Request body is invalid.",
            {"fields": [{"path": "requirementId", "message": "Requirement is not on this case."}]},
        )
    case = session.get(Case, principal.case_id)
    if case is None:
        raise not_found("Case not found.", {"resource": "case", "id": principal.case_id})
    from fcc_api.services.cases import _bundle
    from fcc_api.services.checklist import analyze_rows

    bundle = _bundle(session, case)
    if not any(item.id == requirement_id for item in analyze_rows(bundle)):
        raise not_found(
            "Checklist item not found.",
            {"resource": "checklistItem", "id": requirement_id},
        )
    current = session.scalar(
        select(ChecklistItem.status).where(
            ChecklistItem.case_id == principal.case_id,
            ChecklistItem.requirement_id == requirement_id,
        )
    )
    if current == "VERIFIED":
        raise forbidden(
            "This checklist item is VERIFIED and cannot be changed.",
            {"reason": "ITEM_STATUS", "status": "VERIFIED"},
        )
    from fcc_api.services.documents import create_upload_slots

    return create_upload_slots(
        session,
        _inviter(session, principal.created_by),
        principal.case_id,
        body,
        request_id=request_id,
    )


def _inviter(session: Session, user_id: str) -> Actor:
    from fcc_api.db.models.users import User

    user = session.get(User, user_id)
    if user is None:
        raise forbidden("Your role cannot perform this operation.", {"reason": "ROLE"})
    return Actor(id=user.id, role=Role(str(user.role)), name=user.name, team=user.team)


def _execute(session: Session, statement: str, params: dict[str, Any]) -> None:
    try:
        session.execute(text(statement), params)
    except DBAPIError as exc:
        _schema_or_raise(session, exc)
        raise


def _schema_or_raise(session: Session, exc: DBAPIError) -> None:
    if _missing_portal_schema(exc):
        session.rollback()
        raise unavailable("Portal is not available.") from None


def _missing_portal_schema(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if sqlstate in _MISSING_SCHEMA:
            return True
        nested = getattr(current, "orig", None) or current.__cause__
        current = nested if isinstance(nested, BaseException) else None
    return False


def _normalize_email(email: str) -> str:
    normalized = "".join(email.split()).lower()
    if not normalized or len(normalized) > 320:
        raise validation_failed(
            "Request body is invalid.",
            {"fields": [{"path": "email", "message": "Email is invalid."}]},
        )
    return normalized


def _invite_ttl_days() -> int:
    raw = os.environ.get("PORTAL_INVITE_TTL_DAYS", "").strip()
    if not raw:
        return _DEFAULT_INVITE_DAYS
    try:
        days = int(raw)
    except ValueError:
        return _DEFAULT_INVITE_DAYS
    if days < 1:
        return _DEFAULT_INVITE_DAYS
    return days


def _new_secret() -> str:
    return urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _presented_digest(token: str) -> str | None:
    if not token or len(token) > 128 or any(character.isspace() for character in token):
        return None
    return _sha256(token)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _case_status(status: str):
    if status == "APPROVED":
        message = "This case is APPROVED and can no longer be edited."
    else:
        message = f"This case is {status} and can no longer be edited by your role."
    return forbidden(
        message,
        {"reason": "CASE_STATUS", "status": status, "operation": _INVITE_OPERATION},
    )
