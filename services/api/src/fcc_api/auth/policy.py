"""写权限矩阵、案件可见范围，以及 404 → 403 ROLE → 403 CASE_STATUS。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fcc_api.auth.actor import Actor, Role, fail_auth, record_value

_OPEN = frozenset({"BUILDING", "DOCS_REQUESTED", "RETURNED"})
_OPEN_AND_QUEUE = frozenset({"BUILDING", "DOCS_REQUESTED", "RETURNED", "READY_FOR_COMPLIANCE"})
_QUEUE_ONLY = frozenset({"READY_FOR_COMPLIANCE"})

_ADVISOR_OPS_ADMIN = frozenset({Role.ADVISOR, Role.OPERATIONS, Role.ADMIN})
_ADVISOR_OPS = frozenset({Role.ADVISOR, Role.OPERATIONS})
_OPS_ADMIN = frozenset({Role.OPERATIONS, Role.ADMIN})
_COMPLIANCE_ADMIN = frozenset({Role.COMPLIANCE, Role.ADMIN})
_ALL_ROLES = frozenset({Role.ADVISOR, Role.OPERATIONS, Role.COMPLIANCE, Role.ADMIN})
_ADMIN_ONLY = frozenset({Role.ADMIN})
_COMPLIANCE_ONLY = frozenset({Role.COMPLIANCE})

ADVISOR_CHECKLIST_TARGETS = frozenset({"MISSING", "REQUESTED", "RECEIVED"})
VERIFIED_OR_REJECTED = frozenset({"VERIFIED", "REJECTED"})

RULE_LIBRARY_WRITES = (
    "startRuleDraft",
    "saveLibraryRule",
    "removeLibraryRule",
    "setBuiltinRetired",
    "saveBuiltinOverride",
    "discardRuleDraft",
    "publishRuleDraft",
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class OperationRule:
    """一个操作允许的角色，以及每个角色可写的案件状态。"""

    roles: frozenset[Role]
    statuses: Mapping[Role, frozenset[str]]
    case_scoped: bool


def _status_map(*pairs: tuple[Role, frozenset[str]]) -> Mapping[Role, frozenset[str]]:
    return dict(pairs)


_STRUCTURAL = _status_map(
    (Role.ADVISOR, _OPEN),
    (Role.OPERATIONS, _OPEN),
    (Role.ADMIN, _OPEN),
)
_FILES = _status_map(
    (Role.ADVISOR, _OPEN),
    (Role.OPERATIONS, _OPEN),
    (Role.COMPLIANCE, _OPEN_AND_QUEUE),
    (Role.ADMIN, _OPEN),
)
_DECISION = _status_map((Role.COMPLIANCE, _QUEUE_ONLY))
_ANY_STATUS = frozenset(
    {"BUILDING", "DOCS_REQUESTED", "READY_FOR_COMPLIANCE", "RETURNED", "APPROVED"}
)
# 保全要能盖住已批准案件，释放也发生在保留期之后，所以不沿用顾问可写状态。
_LEGAL_HOLD = _status_map(
    (Role.COMPLIANCE, _ANY_STATUS),
    (Role.ADMIN, _ANY_STATUS),
)
_SUBMISSION = _status_map(
    (Role.OPERATIONS, frozenset({"APPROVED"})),
    (Role.ADMIN, frozenset({"APPROVED"})),
)
_PORTAL_INVITE = _status_map(
    (Role.ADVISOR, frozenset({"DOCS_REQUESTED"})),
    (Role.OPERATIONS, frozenset({"DOCS_REQUESTED"})),
)
_DISPOSITION = _status_map((Role.COMPLIANCE, _OPEN))
_NO_STATUS: Mapping[Role, frozenset[str]] = {}


def _case_rule(roles: frozenset[Role], statuses: Mapping[Role, frozenset[str]]) -> OperationRule:
    return OperationRule(roles=roles, statuses=statuses, case_scoped=True)


def _global_rule(roles: frozenset[Role]) -> OperationRule:
    return OperationRule(roles=roles, statuses=_NO_STATUS, case_scoped=False)


OPERATION_RULES: dict[str, OperationRule] = {
    "createCase": _global_rule(_ADVISOR_OPS_ADMIN),
    "updateParties": _case_rule(_ADVISOR_OPS_ADMIN, _STRUCTURAL),
    "applyAiParties": _case_rule(_ADVISOR_OPS_ADMIN, _STRUCTURAL),
    "recordAiRejection": _case_rule(_ADVISOR_OPS_ADMIN, _STRUCTURAL),
    "updateProfile": _case_rule(_ADVISOR_OPS_ADMIN, _STRUCTURAL),
    "setChecklistStatus": _case_rule(_ALL_ROLES, _FILES),
    "uploadDocuments": _case_rule(_ALL_ROLES, _FILES),
    "assignDocument": _case_rule(_ALL_ROLES, _FILES),
    "addCustomRequirement": _case_rule(_ALL_ROLES, _FILES),
    "addTasks": _case_rule(_ALL_ROLES, _FILES),
    "toggleTask": _case_rule(_ALL_ROLES, _FILES),
    "changeStatus": _case_rule(_ADVISOR_OPS_ADMIN, _STRUCTURAL),
    "complianceDecision": _case_rule(_COMPLIANCE_ONLY, _DECISION),
    "startRuleDraft": _global_rule(_ADMIN_ONLY),
    "saveLibraryRule": _global_rule(_ADMIN_ONLY),
    "removeLibraryRule": _global_rule(_ADMIN_ONLY),
    "setBuiltinRetired": _global_rule(_ADMIN_ONLY),
    "saveBuiltinOverride": _global_rule(_ADMIN_ONLY),
    "discardRuleDraft": _global_rule(_ADMIN_ONLY),
    "publishRuleDraft": _global_rule(_ADMIN_ONLY),
    "create_rule_approval": _global_rule(_ADMIN_ONLY),
    "approve_rule_approval": _global_rule(_COMPLIANCE_ONLY),
    "create_legal_hold": _case_rule(_COMPLIANCE_ADMIN, _LEGAL_HOLD),
    "release_legal_hold": _case_rule(_COMPLIANCE_ADMIN, _LEGAL_HOLD),
    "read_audit_values": _global_rule(_COMPLIANCE_ADMIN),
    "run_screening": _case_rule(_ADVISOR_OPS_ADMIN, _STRUCTURAL),
    "screening_disposition": _case_rule(_COMPLIANCE_ONLY, _DISPOSITION),
    "ai_suggest": _case_rule(_ADVISOR_OPS_ADMIN, _STRUCTURAL),
    "fill_form": _case_rule(_ADVISOR_OPS_ADMIN, _STRUCTURAL),
    "create_submission": _case_rule(_OPS_ADMIN, _SUBMISSION),
    "create_portal_invite": _case_rule(_ADVISOR_OPS, _PORTAL_INVITE),
    "access_review": _global_rule(_ADMIN_ONLY),
}

_OPERATION_ALIASES = {
    "create_case": "createCase",
    "update_parties": "updateParties",
    "apply_ai_parties": "applyAiParties",
    "record_ai_rejection": "recordAiRejection",
    "update_profile": "updateProfile",
    "set_checklist_status": "setChecklistStatus",
    "upload_documents": "uploadDocuments",
    "createUpload": "uploadDocuments",
    "completeUpload": "uploadDocuments",
    "assign_document": "assignDocument",
    "add_custom_requirement": "addCustomRequirement",
    "add_tasks": "addTasks",
    "toggle_task": "toggleTask",
    "change_status": "changeStatus",
    "compliance_decision": "complianceDecision",
    "start_rule_draft": "startRuleDraft",
    "save_library_rule": "saveLibraryRule",
    "remove_library_rule": "removeLibraryRule",
    "set_builtin_retired": "setBuiltinRetired",
    "save_builtin_override": "saveBuiltinOverride",
    "discard_rule_draft": "discardRuleDraft",
    "publish_rule_draft": "publishRuleDraft",
    "createRuleApproval": "create_rule_approval",
    "approveRuleApproval": "approve_rule_approval",
    "createLegalHold": "create_legal_hold",
    "releaseLegalHold": "release_legal_hold",
    "readAuditValues": "read_audit_values",
    "runScreening": "run_screening",
    "screeningDisposition": "screening_disposition",
    "aiSuggest": "ai_suggest",
    "fillForm": "fill_form",
    "createSubmission": "create_submission",
    "createPortalInvite": "create_portal_invite",
    "accessReview": "access_review",
}


def canonical_operation(operation: str) -> str:
    if operation in OPERATION_RULES:
        return operation
    mapped = _OPERATION_ALIASES.get(operation)
    if mapped is None:
        raise ValueError(f"Unknown operation: {operation}")
    return mapped


def four_eyes_publish_enabled(explicit: bool | None = None) -> bool:
    """本地默认关闭。显式参数优先于环境变量 `FOUR_EYES_PUBLISH`。"""

    if explicit is not None:
        return explicit
    raw = os.environ.get("FOUR_EYES_PUBLISH", "false")
    return raw.strip().lower() in _TRUE_VALUES


def allowed_roles(operation: str, *, four_eyes: bool | None = None) -> frozenset[Role]:
    name = canonical_operation(operation)
    if name == "publishRuleDraft" and four_eyes_publish_enabled(four_eyes):
        return _COMPLIANCE_ONLY
    return OPERATION_RULES[name].roles


def allowed_case_statuses(role: Role | str, operation: str) -> frozenset[str]:
    name = canonical_operation(operation)
    parsed = role if isinstance(role, Role) else Role(str(role))
    rule = OPERATION_RULES[name]
    if not rule.case_scoped or parsed not in rule.roles:
        return frozenset()
    return rule.statuses.get(parsed, frozenset())


def role_allows(
    actor: Actor,
    operation: str,
    *,
    target_status: str | None = None,
    four_eyes: bool | None = None,
) -> bool:
    """角色是否可以执行该操作。清单目标状态属于这一步，不属于案件状态锁。"""

    name = canonical_operation(operation)
    if actor.role not in allowed_roles(name, four_eyes=four_eyes):
        return False
    if name == "setChecklistStatus" and actor.role == Role.ADVISOR and target_status is not None:
        return target_status in ADVISOR_CHECKLIST_TARGETS
    return True


def case_visible(actor: Actor, case_row: Any) -> bool:
    """ADVISOR 只能看到自己负责或自己创建的案件。其余角色看到全部。"""

    if case_row is None:
        return False
    if actor.role != Role.ADVISOR:
        return True
    owner = record_value(case_row, "owner_id", "ownerId")
    created = record_value(case_row, "created_by", "createdBy")
    return actor.id == owner or actor.id == created


def case_owner_filter(actor: Actor) -> str | None:
    """顾问列表查询应同时匹配 `owner_id` 与 `created_by`。其他角色不过滤。"""

    if actor.role == Role.ADVISOR:
        return actor.id
    return None


def audit_event_visible(actor: Actor, *, scope: str, case_row: Any = None) -> bool:
    """顾问只看可见案件的 CASE 事件，以及全部 RULE_LIBRARY 事件。"""

    if actor.role != Role.ADVISOR:
        return True
    if scope == "RULE_LIBRARY":
        return True
    if scope == "CASE":
        return case_visible(actor, case_row)
    return False


def require_case_visible(
    actor: Actor,
    case_row: Any,
    *,
    case_id: str | None = None,
    request_id: str | None = None,
) -> None:
    if case_row is None or not case_visible(actor, case_row):
        _not_found(_case_identifier(case_row, case_id), request_id=request_id, actor=actor)


def require_case_write(
    actor: Actor,
    case_row: Any,
    operation: str,
    *,
    target_status: str | None = None,
    case_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """案件写入的第 3 到第 5 步：可见 → 角色 → 状态锁。

    调用前应已完成身份（401）和请求体校验（400）。
    版本冲突（409）、子资源校验和清单项 `ITEM_STATUS`（第 8 步）由 service 继续做。
    `setChecklistStatus` 必须传入 `target_status`，这样顾问标 `VERIFIED` / `REJECTED` 会在状态锁之前返回 ROLE。
    """

    name = canonical_operation(operation)
    rule = OPERATION_RULES[name]
    if not rule.case_scoped:
        raise ValueError(f"{name} is not a case write; call require_operation")
    if name == "setChecklistStatus" and target_status is None:
        raise ValueError("setChecklistStatus requires target_status")

    resolved_case_id = _case_identifier(case_row, case_id)
    if case_row is None or not case_visible(actor, case_row):
        _not_found(resolved_case_id, request_id=request_id, actor=actor)

    if not role_allows(actor, name, target_status=target_status):
        _forbid_role(actor, name, case_id=resolved_case_id, request_id=request_id, four_eyes=False)

    status = record_value(case_row, "status")
    allowed = allowed_case_statuses(actor.role, name)
    if status not in allowed:
        _forbid_status(
            actor,
            name,
            status=None if status is None else str(status),
            case_id=resolved_case_id,
            request_id=request_id,
        )


def require_operation(
    actor: Actor,
    operation: str,
    *,
    started_by: str | None = None,
    four_eyes: bool | None = None,
    request_id: str | None = None,
) -> None:
    """不依赖案件行的角色检查：建案、规则库写入。"""

    name = canonical_operation(operation)
    if name in RULE_LIBRARY_WRITES:
        require_rule_write(
            actor,
            name,
            started_by=started_by,
            four_eyes=four_eyes,
            request_id=request_id,
        )
        return
    if OPERATION_RULES[name].case_scoped:
        raise ValueError(f"{name} is case-scoped; call require_case_write")
    if not role_allows(actor, name, four_eyes=four_eyes):
        _forbid_role(actor, name, request_id=request_id, four_eyes=four_eyes_publish_enabled(four_eyes))


def require_rule_write(
    actor: Actor,
    operation: str,
    *,
    started_by: str | None = None,
    four_eyes: bool | None = None,
    request_id: str | None = None,
) -> None:
    """七个规则库写入。默认只有 ADMIN。四眼打开时，只有发布改为另一名 COMPLIANCE。"""

    name = canonical_operation(operation)
    if name not in RULE_LIBRARY_WRITES:
        raise ValueError(f"{name} is not a rule-library write")
    enabled = four_eyes_publish_enabled(four_eyes)
    if name == "publishRuleDraft" and enabled:
        if actor.role != Role.COMPLIANCE:
            _forbid_role(actor, name, request_id=request_id, four_eyes=True)
        if started_by is None:
            raise ValueError("publishRuleDraft with FOUR_EYES_PUBLISH requires started_by")
        if started_by == actor.id:
            fail_auth(
                status_code=403,
                code="FORBIDDEN",
                message="The approver must be a different person from the submitter.",
                details={"reason": "FOUR_EYES"},
                reason="FOUR_EYES",
                operation=name,
                user_id=actor.id,
                role=actor.role.value,
                request_id=request_id,
            )
        return
    if actor.role not in OPERATION_RULES[name].roles:
        _forbid_role(actor, name, request_id=request_id, four_eyes=False)


def require_checklist_item_status(
    actor: Actor,
    current_status: str,
    target_status: str,
    *,
    case_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """第 8 步。顾问不能修改当前已经是 VERIFIED 的清单项。应在版本比较之后调用。"""

    if actor.role == Role.ADVISOR and current_status == "VERIFIED":
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message="This checklist item is VERIFIED and cannot be changed by your role.",
            details={
                "reason": "ITEM_STATUS",
                "status": "VERIFIED",
                "operation": "setChecklistStatus",
                "role": actor.role.value,
                "targetStatus": target_status,
            },
            reason="ITEM_STATUS",
            operation="setChecklistStatus",
            case_id=case_id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )


def require_evaluate(
    actor: Actor,
    target: str,
    *,
    request_id: str | None = None,
) -> None:
    """`PUBLISHED` 四个角色都可以。`DRAFT` 只有 ADMIN。"""

    normalized = target.strip().upper()
    if normalized == "PUBLISHED":
        return
    if normalized != "DRAFT":
        raise ValueError(f"Unknown evaluate target: {target}")
    if actor.role != Role.ADMIN:
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message="Your role cannot evaluate a draft rule version.",
            details={"reason": "ROLE", "role": actor.role.value, "operation": "evaluateDraft"},
            reason="ROLE",
            operation="evaluateDraft",
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )


def _case_identifier(case_row: Any, case_id: str | None) -> str | None:
    if case_id:
        return case_id
    value = record_value(case_row, "id", "case_id", "caseId")
    if value is None:
        return None
    return str(value)


def _not_found(case_id: str | None, *, request_id: str | None, actor: Actor) -> None:
    fail_auth(
        status_code=404,
        code="NOT_FOUND",
        message="Case not found.",
        details={"resource": "case", "id": case_id or ""},
        reason="NOT_FOUND",
        case_id=case_id,
        user_id=actor.id,
        role=actor.role.value,
        request_id=request_id,
    )


def _forbid_role(
    actor: Actor,
    operation: str,
    *,
    case_id: str | None = None,
    request_id: str | None = None,
    four_eyes: bool,
) -> None:
    fail_auth(
        status_code=403,
        code="FORBIDDEN",
        message=_role_message(operation, actor.role, four_eyes=four_eyes),
        details={"reason": "ROLE", "role": actor.role.value, "operation": operation},
        reason="ROLE",
        operation=operation,
        case_id=case_id,
        user_id=actor.id,
        role=actor.role.value,
        request_id=request_id,
    )


def _forbid_status(
    actor: Actor,
    operation: str,
    *,
    status: str | None,
    case_id: str | None,
    request_id: str | None,
) -> None:
    shown = status or ""
    fail_auth(
        status_code=403,
        code="FORBIDDEN",
        message=_case_status_message(shown),
        details={"reason": "CASE_STATUS", "status": shown, "operation": operation},
        reason="CASE_STATUS",
        operation=operation,
        case_id=case_id,
        user_id=actor.id,
        role=actor.role.value,
        request_id=request_id,
    )


def _role_message(operation: str, role: Role, *, four_eyes: bool) -> str:
    if operation == "setChecklistStatus" and role == Role.ADVISOR:
        return "Your role cannot mark checklist items as VERIFIED or REJECTED."
    if operation == "complianceDecision":
        return "Only Compliance can approve or return a case."
    if operation == "publishRuleDraft":
        if four_eyes:
            return "Only Compliance can publish a rule version."
        return "Only an admin can publish a rule version."
    if operation in RULE_LIBRARY_WRITES:
        return "Only an admin can draft rules."
    if operation == "createCase":
        return "Your role cannot create cases."
    if operation == "create_rule_approval":
        return "Only an admin can submit a rule approval."
    if operation == "approve_rule_approval":
        return "Only Compliance can approve a rule."
    if operation in {"create_legal_hold", "release_legal_hold"}:
        return "Only Compliance or an admin can change a legal hold."
    if operation == "read_audit_values":
        return "Only Compliance or an admin can read audit values."
    if operation == "screening_disposition":
        return "Only Compliance can record a screening disposition."
    if operation == "create_submission":
        return "Only Operations or an admin can submit a case."
    if operation == "create_portal_invite":
        return "Your role cannot invite a portal user."
    if operation == "access_review":
        return "Only an admin can review access."
    return "Your role cannot perform this operation."


def _case_status_message(status: str) -> str:
    if status == "APPROVED":
        return "This case is APPROVED and can no longer be edited."
    return f"This case is {status} and can no longer be edited by your role."
