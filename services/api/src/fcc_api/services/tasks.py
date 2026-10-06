"""追加与勾选复核任务。source 只允许 AI 或 MANUAL。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fcc_api.auth.actor import Actor
from fcc_api.db.models.parties import Party
from fcc_api.db.models.tasks import ReviewTask
from fcc_api.errors import ApiError
from fcc_api.ids import new_id
from fcc_api.schemas.cases import CaseDetailResponse
from fcc_api.schemas.common import to_js_iso
from fcc_api.schemas.tasks import AddTasksIn, ToggleTaskIn
from fcc_api.services.case_write import case_write
from fcc_api.services.cases import load_case_detail

_SOURCES = frozenset({"AI", "MANUAL"})


def add_tasks(
    session: Session,
    actor: Actor,
    case_id: str,
    body: AddTasksIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    if body.source not in _SOURCES:
        raise _invalid([{"path": "source", "message": "source must be AI or MANUAL."}])
    if not body.tasks or len(body.tasks) > 50:
        raise _invalid([{"path": "tasks", "message": "Provide between 1 and 50 tasks."}])
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="addTasks",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        known = set(session.scalars(select(Party.id).where(Party.case_id == case_id)))
        created: list[dict] = []
        for index, item in enumerate(body.tasks):
            title = item.title.strip()
            if not title or len(title) > 500:
                raise _invalid([
                    {"path": f"tasks[{index}].title", "message": "Title must be 1–500 characters."}
                ])
            if item.party_id is not None and item.party_id not in known:
                raise _invalid([
                    {"path": f"tasks[{index}].partyId", "message": "Party is not on this case."}
                ])
            task_id = new_id("task")
            session.add(
                ReviewTask(
                    id=task_id,
                    case_id=case_id,
                    title=title,
                    party_id=item.party_id,
                    requirement_id=item.requirement_id,
                    done=False,
                    source=body.source,
                    created_by=actor.id,
                    created_at=writer.started_at,
                    done_by=None,
                    done_at=None,
                )
            )
            created.append({
                "id": task_id,
                "title": title,
                "partyId": item.party_id,
                "requirementId": item.requirement_id,
                "done": False,
                "source": body.source,
                "createdBy": actor.id,
                "createdAt": to_js_iso(writer.started_at),
            })
        count = len(created)
        suffix = "" if count == 1 else "s"
        writer.audit(
            action="TASK_UPDATED",
            summary=f"Created {count} follow-up task{suffix}",
            before=None,
            after={"tasks": created},
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def toggle_task(
    session: Session,
    actor: Actor,
    case_id: str,
    task_id: str,
    body: ToggleTaskIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="toggleTask",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        task = session.get(ReviewTask, task_id)
        if task is None or task.case_id != case_id:
            raise ApiError(404, "NOT_FOUND", "Task not found.", {"resource": "task", "id": task_id})
        was_done = task.done
        task.done = not was_done
        if task.done:
            task.done_by = actor.id
            task.done_at = writer.started_at
        else:
            task.done_by = None
            task.done_at = None
        verb = "Reopened" if was_done else "Completed"
        writer.audit(
            action="TASK_UPDATED",
            summary=f"{verb} task: {task.title}",
            before={"done": was_done},
            after={"done": task.done},
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def _invalid(fields: list[dict[str, str]]) -> ApiError:
    return ApiError(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": fields})
