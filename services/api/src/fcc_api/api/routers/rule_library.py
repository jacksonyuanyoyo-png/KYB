"""Rule library reads and the seven library writes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from fcc_api.api.deps import get_db
from fcc_api.schemas.rules import (
    EvaluateBody,
    EvaluateResponse,
    OverrideBuiltinBody,
    PublishBody,
    RetireBuiltinBody,
    RuleLibraryResponse,
    SaveExtraBody,
    VersionBody,
    error_response,
    request_id_of,
)
from fcc_api.services.case_write import get_actor, request_ids
from fcc_api.services.rule_library import (
    discard_rule_draft,
    evaluate_rules,
    get_rule_library,
    publish_rule_draft,
    remove_library_rule,
    save_builtin_override,
    save_library_rule,
    set_builtin_retired,
    start_rule_draft,
)

router = APIRouter(prefix="/api/v1/rule-library", tags=["rule-library"])


def _call(session: Any, request: Request, fn: Any) -> Any:
    try:
        return fn()
    except Exception as exc:
        rendered = error_response(exc, request_id_of(request))
        if rendered is not None:
            session.rollback()
            return rendered
        raise


def _ids(request: Request) -> tuple[str, str]:
    return request_ids(request)


def _dump(model: Any, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=model.model_dump(mode="json", by_alias=True))


@router.get("", response_model=RuleLibraryResponse)
def read_rule_library(request: Request, session: Any = Depends(get_db), actor: Any = Depends(get_actor)) -> Any:
    return _call(session, request, lambda: get_rule_library(session, actor))


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate(body: EvaluateBody, request: Request, session: Any = Depends(get_db), actor: Any = Depends(get_actor)) -> Any:
    account = body.account.model_dump(by_alias=True)
    return _call(session, request, lambda: evaluate_rules(session, actor, body.target, account))


@router.post("/draft", response_model=RuleLibraryResponse)
def start_draft(body: VersionBody, request: Request, session: Any = Depends(get_db), actor: Any = Depends(get_actor)) -> Any:
    request_id, correlation_id = _ids(request)

    def run() -> Response:
        payload, status_code = start_rule_draft(
            session, actor, body.version, request_id=request_id, correlation_id=correlation_id
        )
        return _dump(payload, status_code)

    return _call(session, request, run)


@router.post("/draft/extras", response_model=RuleLibraryResponse)
def add_extra(body: SaveExtraBody, request: Request, session: Any = Depends(get_db), actor: Any = Depends(get_actor)) -> Any:
    request_id, correlation_id = _ids(request)
    rule = body.rule.model_dump(by_alias=True, exclude_none=True)
    return _call(
        session,
        request,
        lambda: save_library_rule(
            session, actor, body.version, rule, mode="add", request_id=request_id, correlation_id=correlation_id
        ),
    )


@router.put("/draft/extras/{rule_id}", response_model=RuleLibraryResponse)
def edit_extra(
    rule_id: str,
    body: SaveExtraBody,
    request: Request,
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    request_id, correlation_id = _ids(request)
    rule = body.rule.model_dump(by_alias=True, exclude_none=True)
    return _call(
        session,
        request,
        lambda: save_library_rule(
            session,
            actor,
            body.version,
            rule,
            mode="edit",
            path_rule_id=rule_id,
            request_id=request_id,
            correlation_id=correlation_id,
        ),
    )


@router.delete("/draft/extras/{rule_id}", response_model=RuleLibraryResponse)
def delete_extra(
    rule_id: str,
    request: Request,
    version: int = Query(..., ge=1),
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    request_id, correlation_id = _ids(request)
    return _call(
        session,
        request,
        lambda: remove_library_rule(
            session, actor, version, rule_id, request_id=request_id, correlation_id=correlation_id
        ),
    )


@router.put("/draft/builtins/{rule_id}/retired", response_model=RuleLibraryResponse)
def retire_builtin(
    rule_id: str,
    body: RetireBuiltinBody,
    request: Request,
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    request_id, correlation_id = _ids(request)
    return _call(
        session,
        request,
        lambda: set_builtin_retired(
            session, actor, body.version, rule_id, body.retired, request_id=request_id, correlation_id=correlation_id
        ),
    )


@router.put("/draft/builtins/{rule_id}/override", response_model=RuleLibraryResponse)
def override_builtin(
    rule_id: str,
    body: OverrideBuiltinBody,
    request: Request,
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    request_id, correlation_id = _ids(request)
    override = body.override.model_dump(by_alias=True)
    return _call(
        session,
        request,
        lambda: save_builtin_override(
            session, actor, body.version, rule_id, override, request_id=request_id, correlation_id=correlation_id
        ),
    )


@router.delete("/draft", response_model=RuleLibraryResponse)
def discard_draft(
    request: Request,
    version: int = Query(..., ge=1),
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    request_id, correlation_id = _ids(request)
    return _call(
        session,
        request,
        lambda: discard_rule_draft(session, actor, version, request_id=request_id, correlation_id=correlation_id),
    )


@router.post("/draft/publish", response_model=RuleLibraryResponse)
def publish_draft(
    body: PublishBody,
    request: Request,
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    request_id, correlation_id = _ids(request)
    return _call(
        session,
        request,
        lambda: publish_rule_draft(
            session,
            actor,
            body.version,
            approval_ids=body.approval_ids,
            request_id=request_id,
            correlation_id=correlation_id,
        ),
    )
