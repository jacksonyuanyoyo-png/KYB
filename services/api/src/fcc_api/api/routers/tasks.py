"""复核任务的创建与勾选。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.schemas.cases import CaseDetailResponse
from fcc_api.schemas.tasks import AddTasksIn, ToggleTaskIn
from fcc_api.services.case_write import get_actor, request_ids
from fcc_api.services.tasks import add_tasks, toggle_task
from fcc_api.services.queries import TaskListResponse, list_tasks

router = APIRouter(prefix="/api/v1", tags=["tasks"])


@router.get("/tasks", response_model=TaskListResponse)
def get_tasks(
    done: bool = False,
    limit: int = Query(5),
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> TaskListResponse:
    return list_tasks(session, actor, done=done, limit=limit)


@router.post("/cases/{caseId}/tasks", status_code=201, response_model=CaseDetailResponse)
def post_tasks(
    caseId: str,
    body: AddTasksIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return add_tasks(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post("/cases/{caseId}/tasks/{taskId}/toggle", response_model=CaseDetailResponse)
def post_toggle_task(
    caseId: str,
    taskId: str,
    body: ToggleTaskIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return toggle_task(
        session,
        actor,
        caseId,
        taskId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )
