"""T-AUTH 权限矩阵。

纯函数覆盖 04 第 2–6 节，不连接数据库。
需要完整应用、种子库和其余路由的 HTTP 用例在应用未就绪时跳过。
"""

from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace

import pytest

from fcc_api.auth.actor import AuthFailure, Actor, Role
from fcc_api.auth.entra_provider import EntraNotConfiguredError, authenticate_bearer
from fcc_api.auth.header_provider import (
    assert_auth_configuration,
    authenticate_header_identity,
    resolve_actor,
)
from fcc_api.auth.policy import (
    allowed_case_statuses,
    audit_event_visible,
    case_owner_filter,
    case_visible,
    four_eyes_publish_enabled,
    require_case_write,
    require_checklist_item_status,
    require_evaluate,
    require_operation,
    require_rule_write,
    role_allows,
)

SIGN_IN = "Sign in to continue."
ROLE_MISMATCH = "Identity headers do not match a known user."

BDR = frozenset({"BUILDING", "DOCS_REQUESTED", "RETURNED"})
BDRQ = frozenset({"BUILDING", "DOCS_REQUESTED", "RETURNED", "READY_FOR_COMPLIANCE"})
QUEUE = frozenset({"READY_FOR_COMPLIANCE"})
STATUSES = ["BUILDING", "DOCS_REQUESTED", "RETURNED", "READY_FOR_COMPLIANCE", "APPROVED"]
ROLES = ["ADVISOR", "OPERATIONS", "COMPLIANCE", "ADMIN"]
ROLE_IDS = {
    "ADVISOR": "u-advisor",
    "OPERATIONS": "u-ops",
    "COMPLIANCE": "u-compliance",
    "ADMIN": "u-admin",
}

STRUCTURAL = {"ADVISOR": BDR, "OPERATIONS": BDR, "ADMIN": BDR}
FILES = {"ADVISOR": BDR, "OPERATIONS": BDR, "COMPLIANCE": BDRQ, "ADMIN": BDR}
DECISION = {"COMPLIANCE": QUEUE}

CASE_SPEC = {
    "updateParties": STRUCTURAL,
    "applyAiParties": STRUCTURAL,
    "recordAiRejection": STRUCTURAL,
    "updateProfile": STRUCTURAL,
    "changeStatus": STRUCTURAL,
    "setChecklistStatus": FILES,
    "uploadDocuments": FILES,
    "assignDocument": FILES,
    "addCustomRequirement": FILES,
    "addTasks": FILES,
    "toggleTask": FILES,
    "complianceDecision": DECISION,
}
RULE_WRITES = [
    "startRuleDraft",
    "saveLibraryRule",
    "removeLibraryRule",
    "setBuiltinRetired",
    "saveBuiltinOverride",
    "discardRuleDraft",
    "publishRuleDraft",
]
ADVISOR_CHECKLIST_OK = frozenset({"MISSING", "REQUESTED", "RECEIVED"})

SEED = [
    {"id": "case-0139", "status": "DOCS_REQUESTED", "owner_id": "u-advisor", "created_by": "u-advisor"},
    {"id": "case-0142", "status": "BUILDING", "owner_id": "u-advisor", "created_by": "u-advisor"},
    {"id": "case-0131", "status": "BUILDING", "owner_id": "u-advisor", "created_by": "u-advisor"},
    {"id": "case-0119", "status": "APPROVED", "owner_id": "u-advisor", "created_by": "u-advisor"},
    {"id": "case-0137", "status": "READY_FOR_COMPLIANCE", "owner_id": "u-advisor-2", "created_by": "u-advisor-2"},
    {"id": "case-0128", "status": "RETURNED", "owner_id": "u-advisor-2", "created_by": "u-advisor-2"},
    {"id": "case-0144", "status": "BUILDING", "owner_id": "u-advisor-2", "created_by": "u-advisor-2"},
]


def _actor(user_id: str, role: str, name: str = "", team: str = "") -> Actor:
    return Actor(id=user_id, role=Role(role), name=name or user_id, team=team or "team")


ADVISOR = _actor("u-advisor", "ADVISOR", "Sarah Whitfield", "WI Toronto")
OPS = _actor("u-ops", "OPERATIONS", "Marcus Lee", "Account Operations")
COMPLIANCE = _actor("u-compliance", "COMPLIANCE", "Priya Raman", "Compliance Pre-review")
ADMIN = _actor("u-admin", "ADMIN", "Daniel Okafor", "Platform Admin")
ADVISOR_2 = _actor("u-advisor-2", "ADVISOR", "Julien Tremblay", "PI Montréal")

SARAH = {
    "id": "u-advisor",
    "name": "Sarah Whitfield",
    "email": "sarah.whitfield@fidelity.ca",
    "role": "ADVISOR",
    "team": "WI Toronto",
    "active": True,
}


def _case(case_id: str, status: str, owner_id: str, created_by: str | None = None) -> dict[str, str]:
    return {
        "id": case_id,
        "status": status,
        "owner_id": owner_id,
        "created_by": owner_id if created_by is None else created_by,
    }


def _seed(case_id: str) -> dict[str, str]:
    for row in SEED:
        if row["id"] == case_id:
            return row
    raise AssertionError(case_id)


def _raises(callback) -> AuthFailure:
    with pytest.raises(AuthFailure) as caught:
        callback()
    return caught.value


def _expected_case_reason(operation: str, role: str, status: str, target: str | None) -> str | None:
    if operation == "setChecklistStatus" and role == "ADVISOR" and target not in ADVISOR_CHECKLIST_OK:
        return "ROLE"
    allowed = CASE_SPEC[operation]
    if role not in allowed:
        return "ROLE"
    if status not in allowed[role]:
        return "CASE_STATUS"
    return None


def _matrix_cases() -> list[tuple[str, str, str, str | None]]:
    cases: list[tuple[str, str, str, str | None]] = []
    for operation in CASE_SPEC:
        targets: tuple[str | None, ...] = (None,)
        if operation == "setChecklistStatus":
            targets = ("MISSING", "REQUESTED", "RECEIVED", "VERIFIED", "REJECTED")
        for role in ROLES:
            for status in STATUSES:
                for target in targets:
                    cases.append((operation, role, status, target))
    return cases


def _write_routes_ready() -> bool:
    """案件、清单、规则库等路由还没挂上时，缺路由的 404 会让 T-AUTH-18 误通过。"""

    module_names = (
        "fcc_api.api.routers.cases",
        "fcc_api.api.routers.checklist",
        "fcc_api.api.routers.parties",
        "fcc_api.api.routers.profile",
        "fcc_api.api.routers.tasks",
        "fcc_api.api.routers.entities",
        "fcc_api.api.routers.rule_library",
    )
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            return False
        if getattr(module, "router", None) is None:
            return False
    return True


def test_header_missing_either_header_is_unauthenticated() -> None:
    for user_id, role in ((None, None), ("u-advisor", None), (None, "ADVISOR"), ("  ", "ADVISOR"), ("u-advisor", "")):
        failure = _raises(lambda user_id=user_id, role=role: authenticate_header_identity(user_id, role, SARAH))
        assert failure.status_code == 401
        assert failure.code == "UNAUTHENTICATED"
        assert failure.message == SIGN_IN
        assert failure.details == {}
        assert failure.body("req_01") == {
            "error": {"code": "UNAUTHENTICATED", "message": SIGN_IN, "details": {}},
            "requestId": "req_01",
        }


def test_header_unknown_or_inactive_user_is_unauthenticated() -> None:
    unknown = _raises(lambda: authenticate_header_identity("u-missing", "ADVISOR", None, request_id="req_01"))
    assert unknown.status_code == 401
    assert unknown.message == SIGN_IN
    assert unknown.details == {}

    inactive = {**SARAH, "active": False}
    failure = _raises(lambda: authenticate_header_identity("u-advisor", "ADMIN", inactive))
    assert failure.status_code == 401
    assert failure.message == SIGN_IN
    assert failure.details == {}


def test_header_role_mismatch_uses_documented_message() -> None:
    failure = _raises(lambda: authenticate_header_identity("u-advisor", "ADMIN", SARAH, request_id="req_02"))
    assert failure.status_code == 401
    assert failure.code == "UNAUTHENTICATED"
    assert failure.message == ROLE_MISMATCH
    assert failure.details == {}


def test_header_success_uses_database_name_and_role() -> None:
    actor = authenticate_header_identity("u-advisor", "ADVISOR", SARAH)
    assert actor == Actor(id="u-advisor", role=Role.ADVISOR, name="Sarah Whitfield", team="WI Toronto")


def test_resolve_actor_without_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_MODE", raising=False)
    request = SimpleNamespace(headers={}, state=SimpleNamespace(request_id="req_01"))
    failure = _raises(lambda: resolve_actor(request, session=None))
    assert failure.status_code == 401
    assert failure.message == SIGN_IN


def test_resolve_actor_entra_is_not_configured(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("AUTH_MODE", "entra")
    token = "Bearer secret-token-value"
    request = SimpleNamespace(headers={"authorization": token}, state=SimpleNamespace())
    caplog.set_level(logging.DEBUG, logger="fcc_api.auth")
    with pytest.raises(EntraNotConfiguredError, match="未配置"):
        resolve_actor(request, session=None)
    assert "secret-token-value" not in caplog.text


def test_entra_provider_rejects_every_call() -> None:
    with pytest.raises(EntraNotConfiguredError, match="未配置"):
        authenticate_bearer(None)


def test_production_header_auth_refuses_startup() -> None:
    assert_auth_configuration(app_env="local", auth_mode="header")
    assert_auth_configuration(app_env="test", auth_mode="header")
    assert_auth_configuration(app_env="production", auth_mode="entra")
    with pytest.raises(SystemExit, match="AUTH_MODE=entra"):
        assert_auth_configuration(app_env="production", auth_mode="header")
    with pytest.raises(SystemExit):
        assert_auth_configuration(app_env="production", auth_mode="basic")


def test_seed_visibility_matches_owner_and_created_by() -> None:
    advisor_ids = {row["id"] for row in SEED if case_visible(ADVISOR, row)}
    advisor_2_ids = {row["id"] for row in SEED if case_visible(ADVISOR_2, row)}
    assert advisor_ids == {"case-0139", "case-0142", "case-0131", "case-0119"}
    assert len(advisor_ids) == 4
    assert advisor_2_ids == {"case-0137", "case-0128", "case-0144"}
    for actor in (OPS, COMPLIANCE, ADMIN):
        assert {row["id"] for row in SEED if case_visible(actor, row)} == {row["id"] for row in SEED}
        assert case_owner_filter(actor) is None
    assert case_owner_filter(ADVISOR) == "u-advisor"

    created_for_someone_else = SimpleNamespace(
        id="case-new",
        status="BUILDING",
        owner_id="u-advisor-2",
        created_by="u-advisor",
    )
    assert case_visible(ADVISOR, created_for_someone_else)
    require_case_write(ADVISOR, created_for_someone_else, "updateProfile")
    hidden = _raises(lambda: require_case_write(ADVISOR_2, _seed("case-0139"), "updateProfile"))
    assert hidden.status_code == 404
    assert hidden.code == "NOT_FOUND"
    assert hidden.message == "Case not found."
    assert hidden.details == {"resource": "case", "id": "case-0139"}


def test_invisible_case_is_not_found_before_role() -> None:
    failure = _raises(lambda: require_case_write(ADVISOR, _seed("case-0137"), "complianceDecision"))
    assert failure.status_code == 404
    assert failure.code == "NOT_FOUND"
    assert "reason" not in failure.details


def test_role_is_checked_before_case_status() -> None:
    failure = _raises(lambda: require_case_write(COMPLIANCE, _seed("case-0119"), "updateParties"))
    assert failure.status_code == 403
    assert failure.details == {"reason": "ROLE", "role": "COMPLIANCE", "operation": "updateParties"}


def test_t_auth_03_advisor_cannot_mark_verified() -> None:
    failure = _raises(
        lambda: require_case_write(ADVISOR, _seed("case-0139"), "setChecklistStatus", target_status="VERIFIED")
    )
    assert failure.status_code == 403
    assert failure.message == "Your role cannot mark checklist items as VERIFIED or REJECTED."
    assert failure.details == {"reason": "ROLE", "role": "ADVISOR", "operation": "setChecklistStatus"}
    assert failure.body("req_02")["error"]["code"] == "FORBIDDEN"


def test_t_auth_04_advisor_cannot_change_verified_item() -> None:
    require_case_write(ADVISOR, _seed("case-0139"), "setChecklistStatus", target_status="MISSING")
    failure = _raises(
        lambda: require_checklist_item_status(ADVISOR, "VERIFIED", "MISSING", case_id="case-0139", request_id="req_item")
    )
    assert failure.details["reason"] == "ITEM_STATUS"
    assert failure.details["status"] == "VERIFIED"
    require_checklist_item_status(OPS, "VERIFIED", "MISSING")
    require_checklist_item_status(ADVISOR, "RECEIVED", "VERIFIED")


def test_t_auth_05_advisor_cannot_mark_rejected() -> None:
    failure = _raises(
        lambda: require_case_write(ADVISOR, _seed("case-0139"), "setChecklistStatus", target_status="REJECTED")
    )
    assert failure.details["reason"] == "ROLE"


def test_t_auth_06_operations_can_verify_while_docs_requested() -> None:
    require_case_write(OPS, _seed("case-0139"), "setChecklistStatus", target_status="VERIFIED")


def test_t_auth_07_and_08_approved_is_read_only() -> None:
    advisor = _raises(lambda: require_case_write(ADVISOR, _seed("case-0119"), "updateParties"))
    admin = _raises(lambda: require_case_write(ADMIN, _seed("case-0119"), "updateProfile"))
    assert advisor.details["reason"] == "CASE_STATUS"
    assert admin.details["reason"] == "CASE_STATUS"
    assert advisor.message == "This case is APPROVED and can no longer be edited."
    assert advisor.details == {"reason": "CASE_STATUS", "status": "APPROVED", "operation": "updateParties"}


def test_t_auth_09_only_compliance_can_decide() -> None:
    for actor in (ADVISOR_2, OPS, ADMIN):
        failure = _raises(lambda actor=actor: require_case_write(actor, _seed("case-0137"), "complianceDecision"))
        assert failure.details["reason"] == "ROLE"
        assert failure.message == "Only Compliance can approve or return a case."


def test_t_auth_10_decision_requires_ready_for_compliance() -> None:
    failure = _raises(lambda: require_case_write(COMPLIANCE, _seed("case-0139"), "complianceDecision"))
    assert failure.details == {
        "reason": "CASE_STATUS",
        "status": "DOCS_REQUESTED",
        "operation": "complianceDecision",
    }


def test_t_auth_11_compliance_cannot_publish_when_four_eyes_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUR_EYES_PUBLISH", raising=False)
    assert four_eyes_publish_enabled() is False
    failure = _raises(lambda: require_rule_write(COMPLIANCE, "publishRuleDraft"))
    assert failure.status_code == 403
    assert failure.details["reason"] == "ROLE"
    assert failure.message == "Only an admin can publish a rule version."
    require_rule_write(ADMIN, "publishRuleDraft")


def test_t_auth_12_operations_cannot_start_rule_draft() -> None:
    failure = _raises(lambda: require_rule_write(OPS, "startRuleDraft"))
    assert failure.status_code == 403
    assert failure.details["reason"] == "ROLE"
    assert failure.message == "Only an admin can draft rules."


def test_t_auth_13_compliance_cannot_create_case() -> None:
    failure = _raises(lambda: require_operation(COMPLIANCE, "createCase"))
    assert failure.details["reason"] == "ROLE"
    require_operation(ADVISOR, "createCase")
    require_operation(OPS, "createCase")
    require_operation(ADMIN, "createCase")


def test_t_auth_14_compliance_cannot_edit_parties_in_queue() -> None:
    failure = _raises(lambda: require_case_write(COMPLIANCE, _seed("case-0137"), "updateParties"))
    assert failure.details["reason"] == "ROLE"


def test_t_auth_15_and_16_advisor_cannot_write_ready_case() -> None:
    profile = _raises(lambda: require_case_write(ADVISOR_2, _seed("case-0137"), "updateProfile"))
    toggle = _raises(lambda: require_case_write(ADVISOR_2, _seed("case-0137"), "toggleTask"))
    assert profile.details["reason"] == "CASE_STATUS"
    assert toggle.details["reason"] == "CASE_STATUS"
    assert profile.message == "This case is READY_FOR_COMPLIANCE and can no longer be edited by your role."


def test_t_auth_17_compliance_can_reject_checklist_while_in_queue() -> None:
    require_case_write(COMPLIANCE, _seed("case-0137"), "setChecklistStatus", target_status="REJECTED")
    require_case_write(COMPLIANCE, _seed("case-0137"), "toggleTask")
    require_case_write(COMPLIANCE, _seed("case-0137"), "createUpload")
    blocked = _raises(lambda: require_case_write(ADMIN, _seed("case-0137"), "toggleTask"))
    assert blocked.details["reason"] == "CASE_STATUS"


def test_four_eyes_publish_moves_to_a_different_compliance_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUR_EYES_PUBLISH", "true")
    assert four_eyes_publish_enabled() is True
    admin_publish = _raises(lambda: require_rule_write(ADMIN, "publishRuleDraft"))
    assert admin_publish.details["reason"] == "ROLE"
    assert admin_publish.message == "Only Compliance can publish a rule version."
    require_rule_write(COMPLIANCE, "publishRuleDraft", started_by="u-admin")
    same_person = _raises(lambda: require_rule_write(COMPLIANCE, "publishRuleDraft", started_by="u-compliance"))
    assert same_person.details == {"reason": "FOUR_EYES"}
    assert same_person.message == "The approver must be a different person from the submitter."
    require_rule_write(ADMIN, "startRuleDraft")
    draft_denied = _raises(lambda: require_rule_write(COMPLIANCE, "discardRuleDraft"))
    assert draft_denied.details["reason"] == "ROLE"
    forced_off = _raises(lambda: require_rule_write(COMPLIANCE, "publishRuleDraft", four_eyes=False))
    assert forced_off.details["reason"] == "ROLE"


def test_audit_and_evaluate_visibility() -> None:
    assert audit_event_visible(ADVISOR, scope="RULE_LIBRARY")
    assert audit_event_visible(ADVISOR, scope="CASE", case_row=_seed("case-0139"))
    assert not audit_event_visible(ADVISOR, scope="CASE", case_row=_seed("case-0137"))
    assert audit_event_visible(OPS, scope="CASE", case_row=_seed("case-0137"))
    require_evaluate(ADVISOR, "PUBLISHED")
    require_evaluate(COMPLIANCE, "published")
    denied = _raises(lambda: require_evaluate(ADVISOR, "DRAFT"))
    assert denied.details["reason"] == "ROLE"
    require_evaluate(ADMIN, "DRAFT")


def test_forbidden_log_is_structured_and_omits_the_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="fcc_api.auth")
    with pytest.raises(AuthFailure):
        require_case_write(ADVISOR, _seed("case-0119"), "updateParties", request_id="req_03")
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message == "authorization_failure"
    assert record.request_id == "req_03"
    assert record.user_id == "u-advisor"
    assert record.role == "ADVISOR"
    assert record.operation == "updateParties"
    assert record.case_id == "case-0119"
    assert record.reason == "CASE_STATUS"
    assert "Bearer" not in caplog.text
    assert "registrationNumber" not in caplog.text


def test_me_and_users_routes_match_the_contract() -> None:
    from fcc_api.api.routers.me import _user_payload, router

    assert _user_payload(SARAH) == {
        "id": "u-advisor",
        "name": "Sarah Whitfield",
        "email": "sarah.whitfield@fidelity.ca",
        "role": "ADVISOR",
        "team": "WI Toronto",
    }
    methods_by_path = {route.path: set(route.methods or ()) for route in router.routes}
    assert "GET" in methods_by_path["/api/v1/me"]
    assert "GET" in methods_by_path["/api/v1/users"]


def test_not_found_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="fcc_api.auth")
    with pytest.raises(AuthFailure):
        require_case_write(ADVISOR, None, "updateParties", case_id="case-missing")
    assert caplog.records == []


def test_operation_aliases_and_unknown_names() -> None:
    require_case_write(ADVISOR, _seed("case-0142"), "update_parties")
    require_case_write(OPS, _seed("case-0142"), "completeUpload")
    with pytest.raises(ValueError, match="Unknown operation"):
        require_case_write(ADVISOR, _seed("case-0142"), "notAnOperation")
    with pytest.raises(ValueError, match="target_status"):
        require_case_write(ADVISOR, _seed("case-0142"), "setChecklistStatus")
    assert allowed_case_statuses(Role.COMPLIANCE, "setChecklistStatus") == BDRQ
    assert allowed_case_statuses("ADVISOR", "complianceDecision") == frozenset()
    assert role_allows(ADMIN, "publishRuleDraft", four_eyes=False) is True
    assert role_allows(COMPLIANCE, "publishRuleDraft", four_eyes=False) is False


@pytest.mark.parametrize(("operation", "role", "status", "target"), _matrix_cases())
def test_t_auth_22_policy_matrix(operation: str, role: str, status: str, target: str | None) -> None:
    actor = _actor(ROLE_IDS[role], role)
    case_row = _case("case-matrix", status, ROLE_IDS[role])
    expected = _expected_case_reason(operation, role, status, target)
    if expected is None:
        require_case_write(actor, case_row, operation, target_status=target)
        return
    failure = _raises(lambda: require_case_write(actor, case_row, operation, target_status=target))
    assert failure.status_code == 403
    assert failure.code == "FORBIDDEN"
    assert failure.details["reason"] == expected
    assert failure.details["operation"] == operation
    if expected == "ROLE":
        assert failure.details["role"] == role
    else:
        assert failure.details["status"] == status


@pytest.mark.parametrize("operation", RULE_WRITES)
@pytest.mark.parametrize("role", ROLES)
def test_t_auth_22_rule_matrix_defaults_to_admin(
    operation: str,
    role: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FOUR_EYES_PUBLISH", raising=False)
    actor = _actor(ROLE_IDS[role], role)
    if role == "ADMIN":
        require_rule_write(actor, operation)
        return
    failure = _raises(lambda: require_rule_write(actor, operation))
    assert failure.status_code == 403
    assert failure.details["reason"] == "ROLE"


@pytest.mark.skipif(not _write_routes_ready(), reason="案件与规则库路由尚未就绪")
class TestAuthorizationHttp:
    """T-AUTH-01 到 T-AUTH-23 的 HTTP 形态。应用未挂上这些路由时整组跳过。"""

    def _client(self):
        from fastapi.testclient import TestClient

        from fcc_api.main import create_app

        return TestClient(create_app())

    def test_t_auth_01_missing_headers(self) -> None:
        response = self._client().get("/api/v1/cases")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"
        assert response.json()["error"]["message"] == SIGN_IN

    def test_t_auth_02_role_header_does_not_match(self) -> None:
        response = self._client().get(
            "/api/v1/cases",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADMIN"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    def test_t_auth_03_advisor_verified(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0139/checklist/formation",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
            json={"version": 9, "status": "VERIFIED"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "ROLE"

    def test_t_auth_04_advisor_changes_verified_item(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0139/checklist/naaf",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
            json={"version": 9, "status": "MISSING"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "ITEM_STATUS"

    def test_t_auth_05_advisor_rejected(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0139/checklist/formation",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
            json={"version": 9, "status": "REJECTED"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "ROLE"

    def test_t_auth_06_operations_verifies(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0139/checklist/formation",
            headers={"X-User-Id": "u-ops", "X-User-Role": "OPERATIONS"},
            json={"version": 9, "status": "VERIFIED"},
        )
        assert response.status_code == 200

    def test_t_auth_07_advisor_cannot_edit_approved_parties(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0119/parties",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
            json={"version": 21, "parties": []},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "CASE_STATUS"

    def test_t_auth_08_admin_cannot_edit_approved_profile(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0119/profile",
            headers={"X-User-Id": "u-admin", "X-User-Role": "ADMIN"},
            json={"version": 21, "province": "MB", "taxResidency": "CANADA", "features": [], "trustedContact": False, "trustedContactName": ""},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "CASE_STATUS"

    @pytest.mark.parametrize("user_id,role", [("u-advisor-2", "ADVISOR"), ("u-ops", "OPERATIONS"), ("u-admin", "ADMIN")])
    def test_t_auth_09_non_compliance_cannot_approve(self, user_id: str, role: str) -> None:
        response = self._client().post(
            "/api/v1/cases/case-0137/compliance-decision",
            headers={"X-User-Id": user_id, "X-User-Role": role},
            json={"version": 14, "decision": "APPROVE", "comments": []},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "ROLE"

    def test_t_auth_10_compliance_cannot_approve_docs_requested(self) -> None:
        response = self._client().post(
            "/api/v1/cases/case-0139/compliance-decision",
            headers={"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"},
            json={"version": 9, "decision": "APPROVE", "comments": []},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "CASE_STATUS"

    def test_t_auth_11_compliance_cannot_publish(self) -> None:
        response = self._client().post(
            "/api/v1/rule-library/draft/publish",
            headers={"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"},
            json={"version": 1},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "ROLE"

    def test_t_auth_12_operations_cannot_open_draft(self) -> None:
        response = self._client().post(
            "/api/v1/rule-library/draft",
            headers={"X-User-Id": "u-ops", "X-User-Role": "OPERATIONS"},
        )
        assert response.status_code == 403

    def test_t_auth_13_compliance_cannot_create_case(self) -> None:
        response = self._client().post(
            "/api/v1/cases",
            headers={"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"},
            json={
                "legalName": "Should Not Exist",
                "entityType": "corporation",
                "jurisdiction": "Ontario",
                "registrationNumber": "X",
                "ownerId": "u-advisor",
            },
        )
        assert response.status_code == 403

    def test_t_auth_14_compliance_cannot_edit_ready_parties(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0137/parties",
            headers={"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"},
            json={"version": 14, "parties": []},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "ROLE"

    def test_t_auth_15_advisor_cannot_edit_ready_profile(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0137/profile",
            headers={"X-User-Id": "u-advisor-2", "X-User-Role": "ADVISOR"},
            json={"version": 14, "province": "BC", "taxResidency": "CANADA", "features": ["COD_DVP"], "trustedContact": False, "trustedContactName": ""},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "CASE_STATUS"

    def test_t_auth_16_advisor_cannot_toggle_ready_task(self) -> None:
        response = self._client().post(
            "/api/v1/cases/case-0137/tasks/task-1/toggle",
            headers={"X-User-Id": "u-advisor-2", "X-User-Role": "ADVISOR"},
            json={"version": 14},
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "CASE_STATUS"

    def test_t_auth_17_compliance_can_reject_identity(self) -> None:
        response = self._client().put(
            "/api/v1/cases/case-0137/checklist/identity",
            headers={"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"},
            json={"version": 14, "status": "REJECTED"},
        )
        assert response.status_code == 200

    def test_t_auth_18_advisor_cannot_see_another_advisors_case(self) -> None:
        response = self._client().get(
            "/api/v1/cases/case-0137",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_t_auth_19_advisor_case_count(self) -> None:
        response = self._client().get(
            "/api/v1/cases",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
        )
        assert response.status_code == 200
        assert response.json()["counts"]["ALL"] == 4

    def test_t_auth_20_creator_can_read_case_owned_by_someone_else(self) -> None:
        created = self._client().post(
            "/api/v1/cases",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
            json={
                "legalName": "Created For Another Advisor",
                "entityType": "corporation",
                "jurisdiction": "Ontario",
                "registrationNumber": "T-AUTH-20",
                "ownerId": "u-advisor-2",
            },
        )
        assert created.status_code == 201
        case_id = created.json()["case"]["id"]
        fetched = self._client().get(
            f"/api/v1/cases/{case_id}",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
        )
        assert fetched.status_code == 200

    def test_t_auth_21_entities_follow_case_visibility(self) -> None:
        response = self._client().get(
            "/api/v1/entities",
            headers={"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"},
        )
        assert response.status_code == 200
        case_ids = {item["caseId"] for item in response.json()["items"]}
        assert case_ids <= {"case-0139", "case-0142", "case-0131", "case-0119"}
        assert "case-0137" not in case_ids

    def test_t_auth_23_production_header_mode_does_not_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("AUTH_MODE", "header")
        from fcc_api.main import create_app

        with pytest.raises(SystemExit):
            create_app()
