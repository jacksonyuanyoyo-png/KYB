"""Rule approval routes. Role checks live here because policy has no approval operation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor, Role, fail_auth
from fcc_api.auth.dependencies import get_actor
from fcc_api.schemas.approvals import ApproveRuleApprovalIn, RuleApprovalListOut, RuleApprovalOut, SubmitRuleApprovalIn
from fcc_api.services.approvals import approve_rule_approval, list_rule_approvals, submit_rule_approval

router = APIRouter(prefix="/api/v1/rule-approvals", tags=["rule-approvals"])


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else ""


def _require_role(actor: Actor, role: Role, *, message: str, operation: str, request_id: str) -> None:
    if actor.role == role:
        return
    fail_auth(
        status_code=403,
        code="FORBIDDEN",
        message=message,
        details={"reason": "ROLE", "role": actor.role.value, "operation": operation},
        reason="ROLE",
        operation=operation,
        user_id=actor.id,
        role=actor.role.value,
        request_id=request_id,
    )


@router.get("", response_model=RuleApprovalListOut)
def get_rule_approvals(
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RuleApprovalListOut:
    try:
        return list_rule_approvals(session, actor)
    except Exception:
        session.rollback()
        raise


@router.post("", response_model=RuleApprovalOut)
def post_rule_approval(
    body: SubmitRuleApprovalIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RuleApprovalOut:
    request_id = _request_id(request)
    _require_role(
        actor,
        Role.ADMIN,
        message="Only an admin can submit a rule approval.",
        operation="submitRuleApproval",
        request_id=request_id,
    )
    try:
        result = submit_rule_approval(session, actor, body, request_id=request_id)
    except Exception:
        session.rollback()
        raise
    response.status_code = 201 if result.created else 200
    return result.approval


@router.post("/{approval_id}/approve", response_model=RuleApprovalOut)
def post_rule_approval_decision(
    approval_id: str,
    body: ApproveRuleApprovalIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RuleApprovalOut:
    request_id = _request_id(request)
    _require_role(
        actor,
        Role.COMPLIANCE,
        message="Only Compliance can approve a rule.",
        operation="approveRuleApproval",
        request_id=request_id,
    )
    try:
        return approve_rule_approval(
            session,
            actor,
            approval_id,
            body.approval_ticket,
            request_id=request_id,
        )
    except Exception:
        session.rollback()
        raise
