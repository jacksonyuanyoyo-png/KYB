"""Entra 访问令牌校验。

`AUTH_MODE=header` 不会进入本模块。`resolve_actor` 在 entra 模式下仍不传
settings，因此那条旧调用继续抛出 `EntraNotConfiguredError`。传入 settings
之后才按文档 3.2 节校验。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, NoReturn

from fcc_api.auth.actor import SIGN_IN_MESSAGE, Actor, Role, fail_auth, record_value
from fcc_api.auth.jwks import (
    b64url_decode,
    shared_jwks_cache,
    verify_rs256_signature,
)
from fcc_api.ids import new_id

CLOCK_SKEW_SECONDS = 60
MFA_MESSAGE = "Multi-factor authentication is required."
ROLE_AMBIGUOUS_MESSAGE = "Your account is not assigned a single workbench role."
DISABLED_MESSAGE = "This account is disabled."

_KNOWN_ROLES = frozenset(role.value for role in Role)
_USER_FIELDS = (
    "id",
    "name",
    "email",
    "role",
    "team",
    "active",
    "entra_oid",
    "last_login_at",
    "deactivated_at",
)

VerifySignature = Callable[[Mapping[str, Any], bytes, bytes], bool]


class EntraNotConfiguredError(RuntimeError):
    """调用方没有提供 Entra 配置。"""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "Entra ID authentication is not configured（未配置）. "
                "Do not call Entra in local development; use AUTH_MODE=header."
            )
        )


class _RoleMapError(ValueError):
    pass


@dataclass
class _UserRecord:
    id: str
    name: str
    email: str
    role: str
    team: str
    active: bool
    entra_oid: str | None = None
    last_login_at: datetime | None = None
    deactivated_at: datetime | None = None


def authenticate_bearer(
    token: str | None = None,
    settings: Any = None,
    session: Any = None,
    *,
    request_id: str | None = None,
    jwks: Any = None,
    verify_signature: VerifySignature | None = None,
    now: datetime | None = None,
) -> Actor:
    """校验访问令牌并返回当次请求的 Actor。角色以令牌组映射为准。

    `jwks` 需提供 `key_for(kid)`。`verify_signature(jwk, signing_input, signature)`
    返回 True 才算签名通过。两者都是测试注入点，默认验签不访问外网以外的配置，
    但默认 JWKS 拉取会访问 Entra；测试必须传入 `jwks`。
    `session.get_user_by_entra_oid` 存在时用它读写用户，供测试代替数据库。
    """

    if settings is None:
        raise EntraNotConfiguredError()

    moment = _as_utc(now)
    verifier = verify_signature or verify_rs256_signature
    jwt = _bearer_token(token, request_id=request_id)
    header, payload, signing_input, signature = _decode_jwt(jwt, request_id=request_id)
    _require_rs256(header, request_id=request_id)
    key = _public_key(header, settings, jwks, request_id=request_id)
    if verifier(key, signing_input, signature) is not True:
        _unauthenticated(request_id=request_id, reason="BAD_SIGNATURE")
    _require_claims(payload, settings, moment, request_id=request_id)
    _require_mfa(payload, settings, request_id=request_id)
    oid = _required_text(payload.get("oid"))
    if oid is None:
        _unauthenticated(request_id=request_id, reason="MISSING_OID")

    roles = _mapped_roles(payload, settings, request_id=request_id)
    user = _find_user(session, oid)
    if len(roles) != 1:
        if len(roles) == 0 and user is not None:
            _save_user(
                session,
                user,
                {"active": False, "deactivated_at": moment},
                is_new=False,
            )
        _ambiguous(request_id=request_id, user_id=_user_id(user))

    role = roles[0]
    email = _email_of(payload, user, request_id=request_id)
    name = _name_of(payload, user, email)
    team = str(record_value(user, "team") or "") if user is not None else ""
    fields = {
        "name": name,
        "email": email,
        "role": role,
        "team": team,
        "active": True,
        "entra_oid": oid,
        "last_login_at": moment,
        "deactivated_at": None,
    }
    if user is None:
        fields["id"] = new_id("usr")
        stored = _save_user(session, None, fields, is_new=True)
    else:
        fields["id"] = str(record_value(user, "id"))
        stored = _save_user(session, user, fields, is_new=False)
    if record_value(stored, "active") is False:
        fail_auth(
            status_code=401,
            code="UNAUTHENTICATED",
            message=DISABLED_MESSAGE,
            details={},
            reason="INACTIVE_USER",
            user_id=_user_id(stored),
            request_id=request_id,
        )
    return Actor(
        id=str(record_value(stored, "id")),
        role=Role(role),
        name=str(record_value(stored, "name") or ""),
        team=str(record_value(stored, "team") or ""),
    )


def _bearer_token(value: str | None, *, request_id: str | None) -> str:
    if value is None:
        _unauthenticated(request_id=request_id, reason="MISSING_TOKEN")
    text = str(value).strip()
    if not text:
        _unauthenticated(request_id=request_id, reason="MISSING_TOKEN")
    if " " in text:
        scheme, _, rest = text.partition(" ")
        if scheme.lower() != "bearer" or not rest.strip():
            _unauthenticated(request_id=request_id, reason="NOT_BEARER")
        text = rest.strip()
    return text


def _decode_jwt(
    token: str,
    *,
    request_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3 or any(part == "" for part in parts):
        _unauthenticated(request_id=request_id, reason="MALFORMED_JWT")
    try:
        header = json.loads(b64url_decode(parts[0]))
        payload = json.loads(b64url_decode(parts[1]))
        signature = b64url_decode(parts[2])
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    except (ValueError, UnicodeError, json.JSONDecodeError):
        _unauthenticated(request_id=request_id, reason="MALFORMED_JWT")
    if not isinstance(header, dict) or not isinstance(payload, dict):
        _unauthenticated(request_id=request_id, reason="MALFORMED_JWT")
    return header, payload, signing_input, signature


def _require_rs256(header: Mapping[str, Any], *, request_id: str | None) -> None:
    if header.get("alg") != "RS256":
        _unauthenticated(request_id=request_id, reason="BAD_ALGORITHM")


def _public_key(
    header: Mapping[str, Any],
    settings: Any,
    jwks: Any,
    *,
    request_id: str | None,
) -> Mapping[str, Any]:
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        _unauthenticated(request_id=request_id, reason="MISSING_KID")
    cache = jwks if jwks is not None else shared_jwks_cache(
        str(getattr(settings, "entra_issuer", "") or ""),
        str(getattr(settings, "entra_tenant_id", "") or ""),
    )
    key = cache.key_for(kid)
    if not isinstance(key, Mapping):
        _unauthenticated(request_id=request_id, reason="UNKNOWN_KID")
    return key


def _require_claims(
    payload: Mapping[str, Any],
    settings: Any,
    moment: datetime,
    *,
    request_id: str | None,
) -> None:
    issuer = str(getattr(settings, "entra_issuer", "") or "")
    audience = str(getattr(settings, "entra_audience", "") or "")
    client_id = str(getattr(settings, "entra_client_id", "") or "")
    if not issuer or payload.get("iss") != issuer:
        _unauthenticated(request_id=request_id, reason="BAD_ISSUER")
    if not audience or not _audience_matches(payload.get("aud"), audience):
        _unauthenticated(request_id=request_id, reason="BAD_AUDIENCE")
    if not client_id or not _client_matches(payload, client_id):
        _unauthenticated(request_id=request_id, reason="BAD_CLIENT")
    exp = _unix_time(payload.get("exp"))
    nbf = _unix_time(payload.get("nbf"))
    if exp is None or nbf is None:
        _unauthenticated(request_id=request_id, reason="BAD_TIME")
    current = int(moment.timestamp())
    if current > exp + CLOCK_SKEW_SECONDS or current < nbf - CLOCK_SKEW_SECONDS:
        _unauthenticated(request_id=request_id, reason="BAD_TIME")


def _require_mfa(
    payload: Mapping[str, Any],
    settings: Any,
    *,
    request_id: str | None,
) -> None:
    if _amr_has_mfa(payload.get("amr")) or _acrs_matches(payload.get("acrs"), _required_acrs(settings)):
        return
    fail_auth(
        status_code=401,
        code="UNAUTHENTICATED",
        message=MFA_MESSAGE,
        details={"reason": "MFA_REQUIRED"},
        reason="MFA_REQUIRED",
        request_id=request_id,
    )


def _mapped_roles(
    payload: Mapping[str, Any],
    settings: Any,
    *,
    request_id: str | None,
) -> list[str]:
    try:
        role_map = _role_group_map(getattr(settings, "entra_role_group_map", ""))
    except _RoleMapError:
        _unauthenticated(request_id=request_id, reason="BAD_ROLE_MAP")
    groups = payload.get("groups")
    if not isinstance(groups, list):
        _unauthenticated(request_id=request_id, reason="BAD_GROUPS")
    matched: list[str] = []
    for group in groups:
        role = role_map.get(str(group).strip().lower())
        if role is not None and role not in matched:
            matched.append(role)
    return matched


def _role_group_map(raw: Any) -> dict[str, str]:
    if isinstance(raw, Mapping):
        data: Any = raw
    else:
        text = str(raw or "").strip()
        if not text:
            data = {}
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise _RoleMapError from exc
    if not isinstance(data, Mapping):
        raise _RoleMapError
    mapped: dict[str, str] = {}
    for key, value in data.items():
        role = str(value).strip().upper()
        if role in _KNOWN_ROLES:
            mapped[str(key).strip().lower()] = role
    return mapped


def _find_user(session: Any, oid: str) -> Any | None:
    if session is None:
        return None
    finder = getattr(session, "get_user_by_entra_oid", None)
    if callable(finder):
        return finder(oid)
    from sqlalchemy import select

    from fcc_api.db.models.users import User

    return session.scalars(select(User).where(User.entra_oid == oid)).first()


def _save_user(session: Any, user: Any | None, fields: dict[str, Any], *, is_new: bool) -> Any:
    if session is not None and callable(getattr(session, "get_user_by_entra_oid", None)):
        target = user if user is not None else _UserRecord(
            id=str(fields["id"]),
            name=str(fields.get("name") or ""),
            email=str(fields.get("email") or ""),
            role=str(fields.get("role") or ""),
            team=str(fields.get("team") or ""),
            active=bool(fields.get("active", True)),
        )
        for key, value in fields.items():
            setattr(target, key, value)
        if is_new:
            session.add(target)
        _commit(session)
        return target
    return _save_database_user(session, user, fields, is_new=is_new)


def _save_database_user(
    session: Any,
    user: Any | None,
    fields: dict[str, Any],
    *,
    is_new: bool,
) -> Any:
    from fcc_api.db.models.users import User

    if is_new or user is None:
        target = User(
            id=str(fields["id"]),
            name=str(fields.get("name") or ""),
            email=str(fields.get("email") or ""),
            role=str(fields.get("role") or ""),
            team=str(fields.get("team") or ""),
            active=bool(fields.get("active", True)),
            entra_oid=fields.get("entra_oid"),
            last_login_at=fields.get("last_login_at"),
            deactivated_at=fields.get("deactivated_at"),
        )
        if session is not None:
            session.add(target)
    else:
        target = user
        for key in _USER_FIELDS:
            if key == "id" or key not in fields:
                continue
            setattr(target, key, fields[key])
    if session is not None:
        _commit(session)
    return target


def _commit(session: Any) -> None:
    flush = getattr(session, "flush", None)
    if callable(flush):
        flush()
    commit = getattr(session, "commit", None)
    if callable(commit):
        commit()


def _email_of(payload: Mapping[str, Any], user: Any, *, request_id: str | None) -> str:
    email = _required_text(payload.get("preferred_username"))
    if email is not None:
        return email
    existing = _required_text(record_value(user, "email"))
    if existing is not None:
        return existing
    _unauthenticated(request_id=request_id, reason="MISSING_EMAIL")


def _name_of(payload: Mapping[str, Any], user: Any, email: str) -> str:
    name = _required_text(payload.get("name"))
    if name is not None:
        return name
    existing = _required_text(record_value(user, "name"))
    if existing is not None:
        return existing
    return email


def _required_acrs(settings: Any) -> str:
    raw = getattr(settings, "entra_required_acrs", None)
    if raw is None:
        raw = os.environ.get("ENTRA_REQUIRED_ACRS", "c1")
    text = str(raw).strip()
    return text or "c1"


def _amr_has_mfa(amr: Any) -> bool:
    if isinstance(amr, str):
        return amr == "mfa"
    if isinstance(amr, list):
        return any(item == "mfa" for item in amr)
    return False


def _acrs_matches(acrs: Any, required: str) -> bool:
    if isinstance(acrs, str):
        return acrs == required
    if isinstance(acrs, list):
        return any(item == required for item in acrs)
    return False


def _audience_matches(audience: Any, expected: str) -> bool:
    if isinstance(audience, str):
        return audience == expected
    if isinstance(audience, list):
        return any(item == expected for item in audience)
    return False


def _client_matches(payload: Mapping[str, Any], client_id: str) -> bool:
    return payload.get("azp") == client_id or payload.get("appid") == client_id


def _unix_time(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _required_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _as_utc(now: datetime | None) -> datetime:
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _user_id(user: Any) -> str | None:
    value = record_value(user, "id")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _unauthenticated(*, request_id: str | None, reason: str) -> NoReturn:
    fail_auth(
        status_code=401,
        code="UNAUTHENTICATED",
        message=SIGN_IN_MESSAGE,
        details={},
        reason=reason,
        request_id=request_id,
    )


def _ambiguous(*, request_id: str | None, user_id: str | None) -> NoReturn:
    fail_auth(
        status_code=403,
        code="FORBIDDEN",
        message=ROLE_AMBIGUOUS_MESSAGE,
        details={"reason": "ROLE_AMBIGUOUS"},
        reason="ROLE_AMBIGUOUS",
        user_id=user_id,
        request_id=request_id,
    )
