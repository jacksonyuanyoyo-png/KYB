"""Entra 访问令牌与季度访问复核。不访问外网，不走真实租户。"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fcc_api.api.routers.admin import router
from fcc_api.auth.actor import SIGN_IN_MESSAGE, Actor, AuthFailure, Role
from fcc_api.auth.dependencies import get_actor, get_db_session, register_auth_exception_handler
from fcc_api.auth.entra_provider import (
    MFA_MESSAGE,
    ROLE_AMBIGUOUS_MESSAGE,
    EntraNotConfiguredError,
    authenticate_bearer,
)
from fcc_api.auth.header_provider import resolve_actor
from fcc_api.auth.jwks import JwksCache, jwks_urls

ISSUER = "https://login.microsoftonline.com/tenant-1/v2.0"
TENANT = "tenant-1"
AUDIENCE = "api://fcc"
CLIENT_ID = "client-1"
ADMIN_GROUP = "11111111-1111-1111-1111-111111111111"
ADVISOR_GROUP = "22222222-2222-2222-2222-222222222222"
FROZEN = datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc)
FROZEN_UNIX = int(FROZEN.timestamp())


class MemoryUsers:
    def __init__(self, users: list[Any] | None = None) -> None:
        self.users = list(users or [])

    def get_user_by_entra_oid(self, oid: str) -> Any | None:
        for user in self.users:
            if getattr(user, "entra_oid", None) == oid:
                return user
        return None

    def add(self, user: Any) -> None:
        self.users.append(user)

    def list_active_users(self) -> list[Any]:
        return list(self.users)

    def commit(self) -> None:
        return None


def _settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "entra_tenant_id": TENANT,
        "entra_client_id": CLIENT_ID,
        "entra_audience": AUDIENCE,
        "entra_issuer": ISSUER,
        "entra_required_acrs": "c1",
        "entra_role_group_map": json.dumps(
            {ADMIN_GROUP: "ADMIN", ADVISOR_GROUP: "ADVISOR"}
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _claims(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "azp": CLIENT_ID,
        "exp": FROZEN_UNIX + 600,
        "nbf": FROZEN_UNIX - 30,
        "amr": ["pwd", "mfa"],
        "oid": "oid-user",
        "name": "Ada Admin",
        "preferred_username": "ada@example.com",
        "groups": [ADMIN_GROUP],
    }
    payload.update(overrides)
    return payload


def _segment(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _token(
    payload: dict[str, Any] | None = None,
    *,
    alg: str = "RS256",
    kid: str = "kid-1",
    signature: str = "c2ln",
) -> str:
    header = _segment({"alg": alg, "kid": kid, "typ": "JWT"})
    body = _segment(_claims() if payload is None else payload)
    return f"{header}.{body}.{signature}"


def _cache(fetcher: Callable[[str], dict[str, Any]] | None = None, **kwargs: Any) -> JwksCache:
    def static(url: str) -> dict[str, Any]:
        return {"keys": [{"kty": "RSA", "kid": "kid-1", "alg": "RS256", "use": "sig", "n": "x", "e": "AQAB"}]}

    return JwksCache(issuer=ISSUER, tenant_id=TENANT, fetcher=fetcher or static, **kwargs)


def _accept(jwk: dict[str, Any], signing_input: bytes, signature: bytes) -> bool:
    return jwk.get("kid") == "kid-1" and bool(signing_input) and bool(signature)


def _raises(fn: Callable[[], Any]) -> AuthFailure:
    with pytest.raises(AuthFailure) as caught:
        fn()
    return caught.value


def _login(
    token: str,
    session: MemoryUsers | None = None,
    *,
    jwks: Any = None,
    verify_signature: Callable[..., bool] | None = None,
    settings: Any = None,
) -> Actor:
    return authenticate_bearer(
        token,
        _settings() if settings is None else settings,
        session if session is not None else MemoryUsers(),
        jwks=jwks if jwks is not None else _cache(),
        verify_signature=verify_signature or _accept,
        now=FROZEN,
    )


def _existing_user(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": "u-existing",
        "name": "Old Name",
        "email": "old@example.com",
        "role": "ADVISOR",
        "team": "Toronto",
        "active": True,
        "entra_oid": "oid-user",
        "last_login_at": None,
        "deactivated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _application(actor: Actor | None, users: list[Any]) -> FastAPI:
    application = FastAPI()
    register_auth_exception_handler(application)
    application.include_router(router)
    directory = MemoryUsers(users)

    def override_db() -> Any:
        yield directory

    application.dependency_overrides[get_db_session] = override_db
    if actor is not None:
        application.dependency_overrides[get_actor] = lambda: actor
    return application


def test_missing_token_is_401() -> None:
    session = MemoryUsers([_existing_user()])
    for token in (None, "", "   ", "Bearer", "Basic abc"):
        failure = _raises(lambda token=token: _login(token, session))  # type: ignore[arg-type]
        assert failure.status_code == 401
        assert failure.code == "UNAUTHENTICATED"
        assert failure.message == SIGN_IN_MESSAGE
        assert failure.details == {}
    assert session.users[0].active is True


def test_alg_none_and_symmetric_are_401() -> None:
    calls: list[str] = []

    def fetcher(url: str) -> dict[str, Any]:
        calls.append(url)
        raise AssertionError("jwks")

    def verify(*_args: Any) -> bool:
        raise AssertionError("verify")

    session = MemoryUsers([_existing_user()])
    for alg in ("none", "HS256", "HS512"):
        failure = _raises(
            lambda alg=alg: _login(
                _token(alg=alg),
                session,
                jwks=_cache(fetcher),
                verify_signature=verify,
            )
        )
        assert failure.status_code == 401
        assert failure.message == SIGN_IN_MESSAGE
        assert failure.details == {}
    assert calls == []
    assert session.users[0].active is True


def test_empty_group_mapping_is_403_and_deactivates_existing_user() -> None:
    user = _existing_user()
    session = MemoryUsers([user])
    failure = _raises(lambda: _login(_token(_claims(groups=[])), session))
    assert failure.status_code == 403
    assert failure.code == "FORBIDDEN"
    assert failure.message == ROLE_AMBIGUOUS_MESSAGE
    assert failure.details == {"reason": "ROLE_AMBIGUOUS"}
    assert user.active is False
    assert user.deactivated_at == FROZEN
    assert user.role == "ADVISOR"


def test_multiple_roles_do_not_deactivate() -> None:
    user = _existing_user()
    session = MemoryUsers([user])
    token = _token(_claims(groups=[ADMIN_GROUP, ADVISOR_GROUP]))
    failure = _raises(lambda: _login(token, session))
    assert failure.status_code == 403
    assert failure.details["reason"] == "ROLE_AMBIGUOUS"
    assert user.active is True
    assert user.deactivated_at is None


def test_unknown_kid_refreshes_once_then_401() -> None:
    calls: list[str] = []

    def fetcher(url: str) -> dict[str, Any]:
        calls.append(url)
        return {"keys": [{"kty": "RSA", "kid": "other", "alg": "RS256"}]}

    failure = _raises(lambda: _login(_token(), MemoryUsers(), jwks=_cache(fetcher)))
    assert failure.status_code == 401
    assert failure.message == SIGN_IN_MESSAGE
    assert calls == [f"{ISSUER}/keys", f"{ISSUER}/keys"]


def test_jwks_cache_lasts_3600_seconds_and_unknown_kid_forces_refresh() -> None:
    calls: list[str] = []
    documents = [
        {"keys": [{"kty": "RSA", "kid": "kid-1"}]},
        {"keys": [{"kty": "RSA", "kid": "kid-1"}]},
        {"keys": [{"kty": "RSA", "kid": "kid-2"}]},
    ]

    def fetcher(url: str) -> dict[str, Any]:
        calls.append(url)
        return documents.pop(0)

    clock = {"now": 1_000.0}
    cache = _cache(fetcher, clock=lambda: clock["now"], ttl_seconds=3600)
    assert cache.key_for("kid-1")["kid"] == "kid-1"
    clock["now"] = 4_599.0
    assert cache.key_for("kid-1")["kid"] == "kid-1"
    assert len(calls) == 1
    clock["now"] = 4_600.0
    assert cache.key_for("kid-1")["kid"] == "kid-1"
    assert len(calls) == 2
    found = cache.key_for("kid-2")
    assert found is not None and found["kid"] == "kid-2"
    assert len(calls) == 3


def test_jwks_falls_back_when_issuer_keys_fail() -> None:
    calls: list[str] = []

    def fetcher(url: str) -> dict[str, Any]:
        calls.append(url)
        if "/discovery/" not in url:
            raise ConnectionError("issuer keys unavailable")
        return {"keys": [{"kty": "RSA", "kid": "kid-1"}]}

    cache = _cache(fetcher)
    assert cache.key_for("kid-1")["kid"] == "kid-1"
    assert calls == [
        f"{ISSUER}/keys",
        f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
    ]
    assert jwks_urls(f"{ISSUER}/", TENANT) == calls


def test_mfa_required_when_amr_and_acrs_are_absent() -> None:
    user = _existing_user()
    session = MemoryUsers([user])
    token = _token(_claims(amr=["pwd"], acrs="c0"))
    failure = _raises(lambda: _login(token, session))
    assert failure.status_code == 401
    assert failure.message == MFA_MESSAGE
    assert failure.details == {"reason": "MFA_REQUIRED"}
    assert user.active is True


def test_acrs_satisfies_mfa_and_single_role_inserts_user() -> None:
    session = MemoryUsers()
    token = _token(_claims(amr=["pwd"], acrs="c1", groups=[ADVISOR_GROUP], name="Sarah Whitfield"))
    actor = _login(token, session)
    assert actor.role == Role.ADVISOR
    assert actor.name == "Sarah Whitfield"
    assert actor.team == ""
    assert actor.id.startswith("usr_")
    assert len(session.users) == 1
    stored = session.users[0]
    assert stored.id == actor.id
    assert stored.email == "ada@example.com"
    assert stored.role == "ADVISOR"
    assert stored.active is True
    assert stored.entra_oid == "oid-user"
    assert stored.last_login_at == FROZEN
    assert stored.deactivated_at is None

    renamed = _token(
        _claims(amr=["pwd"], acrs="c1", groups=[ADVISOR_GROUP], name="Sarah W.")
    )
    again = _login(renamed, session)
    assert again.id == actor.id
    assert len(session.users) == 1
    assert stored.name == "Sarah W."
    assert stored.last_login_at == FROZEN


def test_single_role_restores_deactivated_user() -> None:
    user = _existing_user(active=False, deactivated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    session = MemoryUsers([user])
    actor = _login(_token(), session)
    assert actor.id == "u-existing"
    assert actor.role == Role.ADMIN
    assert user.active is True
    assert user.deactivated_at is None
    assert user.role == "ADMIN"
    assert user.name == "Ada Admin"
    assert user.email == "ada@example.com"
    assert user.last_login_at == FROZEN


def test_groups_claim_must_be_an_array() -> None:
    user = _existing_user()
    session = MemoryUsers([user])
    claims = _claims()
    del claims["groups"]
    failure = _raises(lambda: _login(_token(claims), session))
    assert failure.status_code == 401
    assert failure.message == SIGN_IN_MESSAGE
    assert user.active is True


def test_clock_skew_is_sixty_seconds() -> None:
    within = _token(_claims(exp=FROZEN_UNIX - 60, nbf=FROZEN_UNIX + 60))
    actor = _login(within, MemoryUsers())
    assert actor.role == Role.ADMIN

    expired = _raises(lambda: _login(_token(_claims(exp=FROZEN_UNIX - 61)), MemoryUsers()))
    assert expired.status_code == 401
    assert expired.message == SIGN_IN_MESSAGE
    early = _raises(lambda: _login(_token(_claims(nbf=FROZEN_UNIX + 61)), MemoryUsers()))
    assert early.status_code == 401


def test_audience_list_and_appid_are_accepted() -> None:
    claims = _claims(aud=[AUDIENCE, "other"], appid=CLIENT_ID)
    del claims["azp"]
    actor = _login(_token(claims), MemoryUsers())
    assert actor.role == Role.ADMIN
    assert actor.name == "Ada Admin"


def test_authenticate_bearer_without_settings_stays_unconfigured() -> None:
    with pytest.raises(EntraNotConfiguredError, match="未配置"):
        authenticate_bearer(None)
    with pytest.raises(EntraNotConfiguredError, match="未配置"):
        authenticate_bearer("Bearer secret-token", session=MemoryUsers(), request_id="req_01")


def test_header_mode_does_not_call_entra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "header")

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("entra")

    monkeypatch.setattr("fcc_api.auth.entra_provider.authenticate_bearer", boom)

    class HeaderSession:
        def get(self, _model: Any, user_id: str) -> dict[str, Any] | None:
            if user_id != "u-advisor":
                return None
            return {
                "id": "u-advisor",
                "role": "ADVISOR",
                "name": "Sarah Whitfield",
                "team": "WI Toronto",
                "active": True,
            }

    request = SimpleNamespace(
        headers={"x-user-id": "u-advisor", "x-user-role": "ADVISOR"},
        state=SimpleNamespace(),
    )
    actor = resolve_actor(request, HeaderSession())
    assert actor == Actor(id="u-advisor", role=Role.ADVISOR, name="Sarah Whitfield", team="WI Toronto")


def test_admin_can_read_access_review() -> None:
    login_at = datetime(2026, 10, 1, 15, 30, tzinfo=timezone.utc)
    users = [
        SimpleNamespace(
            id="u-zoe",
            name="Zoe Advisor",
            email="zoe@example.com",
            role="ADVISOR",
            active=True,
            last_login_at=login_at,
            deactivated_at=None,
        ),
        SimpleNamespace(
            id="u-ada",
            name="Ada Admin",
            email="ada@example.com",
            role="ADMIN",
            active=True,
            last_login_at=None,
            deactivated_at=None,
        ),
        SimpleNamespace(
            id="u-old",
            name="Old User",
            email="old@example.com",
            role="OPERATIONS",
            active=False,
            last_login_at=None,
            deactivated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ),
    ]
    application = _application(
        Actor(id="u-admin", role=Role.ADMIN, name="Ada Admin", team="Control"),
        users,
    )
    with TestClient(application) as client:
        response = client.get("/api/v1/admin/access-review")
    assert response.status_code == 200
    assert response.headers["X-Export-Rows"] == "2"
    assert response.json() == [
        {
            "id": "u-ada",
            "name": "Ada Admin",
            "email": "ada@example.com",
            "role": "ADMIN",
            "lastLoginAt": None,
            "deactivatedAt": None,
        },
        {
            "id": "u-zoe",
            "name": "Zoe Advisor",
            "email": "zoe@example.com",
            "role": "ADVISOR",
            "lastLoginAt": "2026-10-01T15:30:00Z",
            "deactivatedAt": None,
        },
    ]
    assert "case" not in response.text


def test_advisor_access_review_is_403() -> None:
    users = [
        SimpleNamespace(
            id="u-ada",
            name="Ada Admin",
            email="ada@example.com",
            role="ADMIN",
            active=True,
            last_login_at=None,
            deactivated_at=None,
        )
    ]
    application = _application(
        Actor(id="u-advisor", role=Role.ADVISOR, name="Sarah Whitfield", team="WI Toronto"),
        users,
    )
    with TestClient(application) as client:
        response = client.get("/api/v1/admin/access-review")
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["details"]["reason"] == "ROLE"
    assert body["error"]["message"] == "Only an admin can review access."
    assert response.headers.get("X-Export-Rows") is None
    assert "ada@example.com" not in response.text


def test_access_review_header_mode_without_identity_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "header")

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("entra")

    monkeypatch.setattr("fcc_api.auth.entra_provider.authenticate_bearer", boom)
    application = _application(None, [])
    with TestClient(application) as client:
        response = client.get("/api/v1/admin/access-review")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == SIGN_IN_MESSAGE
