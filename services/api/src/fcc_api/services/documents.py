"""Upload slots, document registration, assignment, and content URLs.

Case mutations go through ``fcc_api.services.case_write``. Extraction status
changes use a later transaction and do not bump ``cases.version``.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from fcc_api.adapters.extraction import get_extractor
from fcc_api.adapters.scanning import get_scanner
from fcc_api.auth.policy import case_visible, require_case_write
from fcc_api.schemas.documents import (
    AssignDocumentIn,
    CompleteUploadsIn,
    ContentUrlOut,
    CreateUploadsIn,
    CreateUploadsOut,
    ExtractionFieldOut,
    ExtractionOut,
    ExtractionPageOut,
    ExtractionRunOut,
    UploadFileIn,
    UploadSlotOut,
    format_timestamp,
)
from fcc_api.services.case_write import case_write
from fcc_api.storage import PayloadTooLarge, get_storage
from fcc_api.storage.local import LocalStorage, local_signature_ok

logger = logging.getLogger("fcc_api.services.documents")

_FILE_NOT_STORED = "The original file is not stored for this document."
_FILE_NOT_READY = "This file is not available until scanning has finished."
_SIGNED_FORBIDDEN = "The signed address is invalid or has expired."
_VALIDATION = "Request body is invalid."
_PDF = b"%PDF-"
_PNG = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
_JPEG = bytes((0xFF, 0xD8, 0xFF))
_EXTRACTION_ERRORS = {"TIMEOUT", "UNSUPPORTED", "VENDOR_ERROR"}
_PROMOTE_DELAYS = (0.0, 0.2, 0.4, 0.8, 1.6)


@dataclass(frozen=True)
class LocalDownload:
    path: Path
    media_type: str
    disposition: str


@dataclass(frozen=True)
class _Prepared:
    upload_id: str
    file_name: str
    declared_mime: str
    requirement_id: str | None
    object_key: str
    size: int
    sha256: str
    detected_mime: str
    scanned_by: str | None


@dataclass(frozen=True)
class _Registered:
    id: str
    case_id: str
    object_key: str
    mime_type: str
    sha256: str
    size: int


def create_upload_slots(
    session: Session,
    actor: Any,
    case_id: str,
    body: CreateUploadsIn,
    *,
    request_id: str | None = None,
) -> CreateUploadsOut:
    settings = _settings()
    case = _writable_case(session, actor, case_id, "uploadDocuments", request_id=request_id)
    cleaned = _validate_declared_files(body.files, settings)
    if body.requirement_id is not None:
        _require_requirement(session, case, body.requirement_id)
    storage = get_storage(settings)
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromtimestamp(int(now.timestamp()) + _upload_ttl(settings), timezone.utc)
    batch_id = _new_id("upb")
    upload_slot = _model("fcc_api.db.models.documents", "UploadSlot")
    slots: list[UploadSlotOut] = []
    pending_rows = []
    for file_name, size_bytes, mime_type in cleaned:
        upload_id = _new_id("upl")
        object_key = _object_key(case_id, upload_id)
        presigned = storage.presign_put(
            upload_id=upload_id,
            object_key=object_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            expires_at=expires_at,
        )
        pending_rows.append(
            upload_slot(
                id=upload_id,
                case_id=case_id,
                batch_id=batch_id,
                requirement_id=body.requirement_id,
                file_name=file_name,
                declared_size=size_bytes,
                declared_mime=mime_type,
                object_key=object_key,
                status="PENDING",
                created_by=actor.id,
                created_at=now,
                expires_at=expires_at,
            )
        )
        slots.append(
            UploadSlotOut(
                upload_id=upload_id,
                file_name=file_name,
                method=presigned.method,
                url=presigned.url,
                headers=dict(presigned.headers),
            )
        )
    _expire_case_slots(session, case_id, storage, now)
    session.add_all(pending_rows)
    session.commit()
    return CreateUploadsOut(batch_id=batch_id, expires_at=format_timestamp(expires_at), slots=slots)


async def receive_local_upload(
    session: Session,
    *,
    upload_id: str,
    expires: int,
    signature: str,
    stream: AsyncIterator[bytes],
) -> None:
    settings = _settings()
    _require_local_backend(settings)
    _require_signature(settings, "PUT", upload_id, expires, signature)
    upload_slot = _model("fcc_api.db.models.documents", "UploadSlot")
    slot = session.get(upload_slot, upload_id)
    if slot is None:
        raise _not_found("upload", upload_id, "Upload slot not found.")
    if slot.status != "PENDING" or _aware(slot.expires_at) <= datetime.now(timezone.utc):
        raise _signed_forbidden()
    storage = get_storage(settings)
    if not isinstance(storage, LocalStorage):
        raise _not_found("storage", "local", "Not found.")
    try:
        await storage.write_quarantine(slot.object_key, stream, int(slot.declared_size))
    except PayloadTooLarge:
        raise _validation({"reason": "SIZE_MISMATCH"}) from None


def complete_uploads(
    session: Session,
    actor: Any,
    case_id: str,
    body: CompleteUploadsIn,
    *,
    request_id: str,
    correlation_id: str,
) -> Any:
    settings = _settings()
    _writable_case(session, actor, case_id, "uploadDocuments", request_id=request_id)
    slots = _pending_slots(session, actor, case_id, body.batch_id, body.upload_ids)
    async_scan = _async_scan_enabled(settings)
    prepared, failures = _inspect_slots(slots, async_scan)
    if failures:
        raise _validation({"files": failures})
    requirement_id = prepared[0].requirement_id
    now = datetime.now(timezone.utc)
    registered: list[_Registered] = []
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="uploadDocuments",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        locked = _lock_slots(session, [item.upload_id for item in prepared])
        if len(locked) != len(prepared) or any(slot.status != "PENDING" for slot in locked.values()):
            raise _validation({"fields": [{"path": "uploadIds", "message": "Upload slots are not pending for this batch."}]})
        old_status = None
        new_status = None
        if requirement_id is not None:
            _require_requirement(session, writer.case, requirement_id)
            old_status, new_status = _mark_received(session, case_id, requirement_id, actor.id, now)
        names: list[str] = []
        after_documents: list[dict[str, Any]] = []
        for item in prepared:
            document_id = _new_id("doc")
            _insert_document(session, actor, case_id, item, document_id, now, async_scan)
            _insert_extraction_run(session, document_id, now)
            slot = locked[item.upload_id]
            slot.status = "COMPLETED"
            slot.completed_at = now
            names.append(item.file_name)
            after_documents.append(
                {"id": document_id, "sha256": item.sha256, "sizeBytes": item.size, "mimeType": item.declared_mime}
            )
            registered.append(
                _Registered(
                    id=document_id,
                    case_id=case_id,
                    object_key=item.object_key,
                    mime_type=item.declared_mime,
                    sha256=item.sha256,
                    size=item.size,
                )
            )
        session.flush()
        _write_audit(
            writer,
            action="DOCUMENT_UPLOADED",
            summary=_clip("Uploaded " + ", ".join(names)),
            before=None if requirement_id is None else {"status": old_status},
            after={"documents": after_documents, "status": new_status},
        )
    session.commit()
    if not async_scan:
        storage = get_storage(settings)
        promoted = [item for item in registered if _promote(storage, item)]
        _finish_extractions(session, storage, promoted)
        session.commit()
    session.expire_all()
    return _case_detail(session, actor, case_id)


def assign_document(
    session: Session,
    actor: Any,
    case_id: str,
    document_id: str,
    body: AssignDocumentIn,
    *,
    request_id: str,
    correlation_id: str,
) -> Any:
    now = datetime.now(timezone.utc)
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="assignDocument",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        case_document = _model("fcc_api.db.models.documents", "CaseDocument")
        document = session.scalar(
            select(case_document).where(case_document.id == document_id, case_document.case_id == case_id)
        )
        if document is None:
            raise _not_found("document", document_id, "Document not found.")
        requirement_name = _require_requirement(session, writer.case, body.requirement_id)
        previous_requirement = document.requirement_id
        old_status, new_status = _mark_received(session, case_id, body.requirement_id, actor.id, now)
        document.requirement_id = body.requirement_id
        session.flush()
        _write_audit(
            writer,
            action="CHECKLIST_UPDATED",
            summary=_clip(f"Linked {document.file_name} to {requirement_name}"),
            before={"requirementId": previous_requirement, "status": old_status},
            after={"requirementId": body.requirement_id, "status": new_status},
        )
    session.commit()
    session.expire_all()
    return _case_detail(session, actor, case_id)


def issue_content_url(
    session: Session,
    actor: Any,
    case_id: str,
    document_id: str,
    *,
    request_id: str | None,
) -> ContentUrlOut:
    case = _visible_case(session, actor, case_id)
    del case
    case_document = _model("fcc_api.db.models.documents", "CaseDocument")
    document = session.scalar(
        select(case_document).where(case_document.id == document_id, case_document.case_id == case_id)
    )
    if document is None:
        raise _not_found("document", document_id, "Document not found.")
    if document.storage_state == "METADATA_ONLY" or not document.object_key:
        raise _gate("FILE_NOT_STORED", _FILE_NOT_STORED)
    settings = _settings()
    storage = get_storage(settings)
    if document.scan_status != "CLEAN" or not storage.accepted_ready(document.object_key):
        raise _gate("FILE_NOT_READY", _FILE_NOT_READY)
    expires_at = datetime.fromtimestamp(int(datetime.now(timezone.utc).timestamp()) + _download_ttl(settings), timezone.utc)
    presigned = storage.presign_get(
        document_id=document.id,
        object_key=document.object_key,
        mime_type=document.mime_type,
        file_name=document.file_name,
        expires_at=expires_at,
    )
    logger.info(
        "document_access",
        extra={
            "request_id": request_id,
            "user_id": actor.id,
            "case_id": case_id,
            "document_id": document_id,
            "operation": "contentUrl",
        },
    )
    return ContentUrlOut(
        url=presigned.url,
        expires_at=format_timestamp(presigned.expires_at),
        mime_type=document.mime_type,
        file_name=document.file_name,
    )


def get_document_extraction(
    session: Session,
    actor: Any,
    case_id: str,
    document_id: str,
    *,
    include_pages: bool,
) -> ExtractionOut:
    _visible_case(session, actor, case_id)
    case_document = _model("fcc_api.db.models.documents", "CaseDocument")
    document = session.scalar(
        select(case_document).where(case_document.id == document_id, case_document.case_id == case_id)
    )
    if document is None:
        raise _not_found("document", document_id, "Document not found.")
    document_extraction = _model("fcc_api.db.models.extractions", "DocumentExtraction")
    extraction = session.scalar(
        select(document_extraction)
        .where(document_extraction.document_id == document_id)
        .order_by(document_extraction.started_at.desc())
    )
    if extraction is None:
        return ExtractionOut(extraction=None, fields=[], pages=[] if include_pages else None)
    extraction_field = _model("fcc_api.db.models.extractions", "ExtractionField")
    field_rows = session.scalars(
        select(extraction_field).where(extraction_field.extraction_id == extraction.id).order_by(extraction_field.id)
    ).all()
    pages = None
    if include_pages:
        extraction_page = _model("fcc_api.db.models.extractions", "ExtractionPage")
        page_rows = session.scalars(
            select(extraction_page)
            .where(extraction_page.extraction_id == extraction.id)
            .order_by(extraction_page.page_no)
        ).all()
        pages = [ExtractionPageOut(page_no=row.page_no, text=row.text) for row in page_rows]
    return ExtractionOut(
        extraction=ExtractionRunOut(
            id=extraction.id,
            status=extraction.status,
            adapter=extraction.adapter,
            model=extraction.model,
            page_count=extraction.page_count,
            started_at=format_timestamp(_aware(extraction.started_at)),
            finished_at=format_timestamp(_aware(extraction.finished_at)) if extraction.finished_at else None,
            error_code=extraction.error_code,
        ),
        fields=[
            ExtractionFieldOut(
                group_key=row.group_key,
                field_key=row.field_key,
                value=row.value,
                confidence=float(row.confidence),
                page_no=row.page_no,
                citation=row.citation,
            )
            for row in field_rows
        ],
        pages=pages,
    )


def open_local_document(session: Session, *, document_id: str, expires: int, signature: str) -> LocalDownload:
    settings = _settings()
    _require_local_backend(settings)
    _require_signature(settings, "GET", document_id, expires, signature)
    case_document = _model("fcc_api.db.models.documents", "CaseDocument")
    document = session.get(case_document, document_id)
    storage = get_storage(settings)
    if (
        document is None
        or not document.object_key
        or not isinstance(storage, LocalStorage)
        or not storage.accepted_ready(document.object_key)
    ):
        raise _not_found("document", document_id, "Document not found.")
    disposition = "inline; filename*=UTF-8''" + quote(document.file_name, safe="")
    return LocalDownload(
        path=storage.accepted_path(document.object_key),
        media_type=document.mime_type,
        disposition=disposition,
    )


def _inspect_slots(slots: list[Any], async_scan: bool) -> tuple[list[_Prepared], list[dict[str, str]]]:
    storage = get_storage()
    scanner = None if async_scan else get_scanner()
    prepared: list[_Prepared] = []
    failures: list[dict[str, str]] = []
    for slot in slots:
        stat = storage.inspect(slot.object_key, zone="quarantine")
        if stat is None:
            failures.append({"uploadId": slot.id, "reason": "MISSING_OBJECT"})
            continue
        if stat.size != int(slot.declared_size):
            failures.append({"uploadId": slot.id, "reason": "SIZE_MISMATCH"})
            continue
        detected = _detect_mime(stat.header)
        if detected != slot.declared_mime:
            failures.append({"uploadId": slot.id, "reason": "TYPE_MISMATCH"})
            continue
        scanned_by = None
        if scanner is not None:
            with storage.open(slot.object_key, zone="quarantine") as reader:
                scan_status = scanner.scan(object_reader=reader, sha256=stat.sha256)
            if scan_status == "INFECTED":
                failures.append({"uploadId": slot.id, "reason": "INFECTED"})
                continue
            if scan_status != "CLEAN":
                failures.append({"uploadId": slot.id, "reason": "SCAN_ERROR"})
                continue
            scanned_by = scanner.name
        prepared.append(
            _Prepared(
                upload_id=slot.id,
                file_name=slot.file_name,
                declared_mime=slot.declared_mime,
                requirement_id=slot.requirement_id,
                object_key=slot.object_key,
                size=stat.size,
                sha256=stat.sha256,
                detected_mime=detected,
                scanned_by=scanned_by,
            )
        )
    return prepared, failures


def _insert_document(
    session: Session,
    actor: Any,
    case_id: str,
    item: _Prepared,
    document_id: str,
    now: datetime,
    async_scan: bool,
) -> None:
    case_document = _model("fcc_api.db.models.documents", "CaseDocument")
    session.add(
        case_document(
            id=document_id,
            case_id=case_id,
            requirement_id=item.requirement_id,
            file_name=item.file_name,
            size_bytes=item.size,
            mime_type=item.declared_mime,
            uploaded_by=actor.id,
            uploaded_at=now,
            extraction="PROCESSING",
            storage_state="QUARANTINED" if async_scan else "ACCEPTED",
            scan_status="PENDING" if async_scan else "CLEAN",
            object_key=item.object_key,
            sha256=item.sha256,
            detected_mime=item.detected_mime,
            upload_slot_id=item.upload_id,
            scanned_at=None if async_scan else now,
            scanned_by=item.scanned_by,
        )
    )


def _insert_extraction_run(session: Session, document_id: str, now: datetime) -> None:
    document_extraction = _model("fcc_api.db.models.extractions", "DocumentExtraction")
    session.add(
        document_extraction(
            id=_new_id("ext"),
            document_id=document_id,
            status="PROCESSING",
            adapter=get_extractor().name,
            model=None,
            page_count=None,
            error_code=None,
            started_at=now,
            finished_at=None,
        )
    )


def _finish_extractions(session: Session, storage: Any, documents: list[_Registered]) -> None:
    extractor = get_extractor()
    for item in documents:
        try:
            with storage.open(item.object_key, zone="accepted") as reader:
                result = extractor.extract(document_id=item.id, object_reader=reader, mime_type=item.mime_type)
            _store_extraction(session, item.id, result)
        except Exception:
            _store_extraction_failure(session, item.id)
            logger.info(
                "extraction failed",
                extra={
                    "document_id": item.id,
                    "case_id": item.case_id,
                    "operation": "extract",
                    "error_code": "VENDOR_ERROR",
                },
            )


def _store_extraction(session: Session, document_id: str, result: Any) -> None:
    if result.error_code:
        code = result.error_code if result.error_code in _EXTRACTION_ERRORS else "VENDOR_ERROR"
        _mark_extraction(session, document_id, "FAILED", code, None)
        return
    extraction = _mark_extraction(session, document_id, "EXTRACTED", None, len(result.pages))
    extraction_page = _model("fcc_api.db.models.extractions", "ExtractionPage")
    extraction_field = _model("fcc_api.db.models.extractions", "ExtractionField")
    for page in result.pages:
        session.add(extraction_page(extraction_id=extraction.id, page_no=page.page_no, text=page.text))
    for field in result.fields:
        session.add(
            extraction_field(
                extraction_id=extraction.id,
                group_key=field.group_key,
                field_key=field.field_key,
                value=field.value,
                confidence=field.confidence,
                page_no=field.page_no,
                citation=field.citation,
                bbox=field.bbox,
            )
        )


def _store_extraction_failure(session: Session, document_id: str) -> None:
    _mark_extraction(session, document_id, "FAILED", "VENDOR_ERROR", None)


def _mark_extraction(session: Session, document_id: str, status: str, error_code: str | None, page_count: int | None):
    case_document = _model("fcc_api.db.models.documents", "CaseDocument")
    document_extraction = _model("fcc_api.db.models.extractions", "DocumentExtraction")
    document = session.get(case_document, document_id)
    extraction = session.scalar(
        select(document_extraction)
        .where(document_extraction.document_id == document_id)
        .order_by(document_extraction.started_at.desc())
    )
    now = datetime.now(timezone.utc)
    if document is not None:
        document.extraction = status
    if extraction is not None:
        extraction.status = status
        extraction.error_code = error_code
        extraction.page_count = page_count
        extraction.finished_at = now
        if status == "EXTRACTED":
            extraction.model = None
    return extraction


def _promote(storage: Any, item: _Registered) -> bool:
    for delay in _PROMOTE_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            storage.promote(item.object_key)
            return True
        except Exception:
            logger.info(
                "promote failed",
                extra={"document_id": item.id, "case_id": item.case_id, "operation": "promote", "error_code": "PROMOTE_FAILED"},
            )
    return False


def _pending_slots(session: Session, actor: Any, case_id: str, batch_id: str, upload_ids: list[str]) -> list[Any]:
    if len(upload_ids) != len(set(upload_ids)):
        raise _validation({"fields": [{"path": "uploadIds", "message": "Upload slots are not pending for this batch."}]})
    upload_slot = _model("fcc_api.db.models.documents", "UploadSlot")
    rows = session.scalars(select(upload_slot).where(upload_slot.id.in_(upload_ids))).all()
    by_id = {row.id: row for row in rows}
    now = datetime.now(timezone.utc)
    ordered = []
    requirement_ids: set[str | None] = set()
    for upload_id in upload_ids:
        slot = by_id.get(upload_id)
        if (
            slot is None
            or slot.case_id != case_id
            or slot.batch_id != batch_id
            or slot.created_by != actor.id
            or slot.status != "PENDING"
            or _aware(slot.expires_at) <= now
        ):
            raise _validation({"fields": [{"path": "uploadIds", "message": "Upload slots are not pending for this batch."}]})
        requirement_ids.add(slot.requirement_id)
        ordered.append(slot)
    if len(requirement_ids) != 1:
        raise _validation({"fields": [{"path": "uploadIds", "message": "Upload slots are not pending for this batch."}]})
    return ordered


def _lock_slots(session: Session, upload_ids: list[str]) -> dict[str, Any]:
    upload_slot = _model("fcc_api.db.models.documents", "UploadSlot")
    rows = session.scalars(
        select(upload_slot).where(upload_slot.id.in_(upload_ids)).with_for_update()
    ).all()
    return {row.id: row for row in rows}


def _mark_received(session: Session, case_id: str, requirement_id: str, actor_id: str, now: datetime) -> tuple[str, str]:
    checklist_item = _model("fcc_api.db.models.checklist", "ChecklistItem")
    item = session.scalar(
        select(checklist_item).where(
            checklist_item.case_id == case_id,
            checklist_item.requirement_id == requirement_id,
        )
    )
    old_status = item.status if item is not None else "MISSING"
    new_status = "VERIFIED" if old_status == "VERIFIED" else "RECEIVED"
    if item is None:
        session.add(
            checklist_item(
                case_id=case_id,
                requirement_id=requirement_id,
                status=new_status,
                updated_at=now,
                updated_by=actor_id,
            )
        )
    else:
        item.status = new_status
        item.updated_at = now
        item.updated_by = actor_id
    return old_status, new_status


def _expire_case_slots(session: Session, case_id: str, storage: Any, now: datetime) -> None:
    upload_slot = _model("fcc_api.db.models.documents", "UploadSlot")
    rows = session.scalars(
        select(upload_slot).where(
            upload_slot.case_id == case_id,
            upload_slot.status == "PENDING",
            upload_slot.expires_at <= now,
        )
    ).all()
    for slot in rows:
        slot.status = "EXPIRED"
        try:
            storage.delete_quarantine(slot.object_key)
        except Exception:
            logger.info(
                "quarantine delete failed",
                extra={"upload_id": slot.id, "case_id": case_id, "operation": "expireUpload"},
            )


def _validate_declared_files(files: list[UploadFileIn], settings: object) -> list[tuple[str, int, str]]:
    if not 1 <= len(files) <= 20:
        raise _validation({"fields": [{"path": "files", "message": "Choose between 1 and 20 files."}]})
    allowed = _allowed_mimes(settings)
    max_bytes = _max_bytes(settings)
    cleaned: list[tuple[str, int, str]] = []
    errors: list[dict[str, str]] = []
    for index, spec in enumerate(files):
        try:
            file_name = _clean_file_name(spec.file_name)
        except ValueError:
            errors.append({"path": f"files[{index}].fileName", "message": "File name is invalid."})
            continue
        if spec.size_bytes < 1 or spec.size_bytes > max_bytes:
            errors.append({"path": f"files[{index}].sizeBytes", "message": "File is larger than 25 MB."})
        elif spec.mime_type not in allowed:
            errors.append({"path": f"files[{index}].mimeType", "message": "File type is not allowed."})
        else:
            cleaned.append((file_name, spec.size_bytes, spec.mime_type))
    if errors:
        raise _validation({"fields": errors})
    return cleaned


def _clean_file_name(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1].strip()
    if not base or base in {".", ".."} or "\x00" in base or len(base) > 255:
        raise ValueError("invalid file name")
    return base


def _require_requirement(session: Session, case: Any, requirement_id: str) -> str:
    if not requirement_id.strip():
        raise _validation({"fields": [{"path": "requirementId", "message": "Requirement is not on this case."}]})
    for req_id, name in _checklist(session, case):
        if req_id == requirement_id:
            return name
    raise _validation({"fields": [{"path": "requirementId", "message": "Requirement is not on this case."}]})


def _checklist(session: Session, case: Any) -> list[tuple[str, str]]:
    rows = _generated_requirements(session, case)
    custom_requirement = _model("fcc_api.db.models.checklist", "CustomRequirement")
    custom_rows = session.scalars(select(custom_requirement).where(custom_requirement.case_id == case.id).order_by(custom_requirement.position)).all()
    rows.extend((row.id, row.name) for row in custom_rows)
    return rows


def _generated_requirements(session: Session, case: Any) -> list[tuple[str, str]]:
    from fcc_api.rules.domain import generate_requirements
    from fcc_api.rules.types import AccountCase, AccountProfile, Party

    party_row = _model("fcc_api.db.models.parties", "Party")
    parties = session.scalars(select(party_row).where(party_row.case_id == case.id).order_by(party_row.position)).all()
    party_values = [
        {
            "id": row.id,
            "parentId": row.parent_id,
            "kind": row.kind,
            "legalName": row.legal_name,
            "entityType": row.entity_type,
            "country": row.country,
            "title": row.title,
            "usTaxClass": row.us_tax_class,
            "ownershipPercent": float(row.ownership_percent),
            "isController": row.is_controller,
            "isSigningAuthority": row.is_signing_authority,
            "isUsPerson": row.is_us_person,
            "isPepHio": row.is_pep_hio,
        }
        for row in parties
    ]
    profile = _construct(
        AccountProfile,
        {
            "province": case.province or "",
            "taxResidency": case.tax_residency or "CANADA",
            "features": list(case.features or []),
            "trustedContact": False if case.trusted_contact is None else bool(case.trusted_contact),
        },
    )
    account = _construct(
        AccountCase,
        {
            "id": case.id,
            "version": case.version,
            "legalName": case.legal_name,
            "entityType": case.entity_type,
            "status": case.status,
            "parties": [_construct(Party, value) for value in party_values],
            "profile": profile,
            "ruleVersion": case.rule_version,
            "createdAt": format_timestamp(_aware(case.created_at)),
            "updatedAt": format_timestamp(_aware(case.updated_at)),
        },
    )
    rule_version = _model("fcc_api.db.models.rules", "RuleVersion")
    pinned = session.get(rule_version, case.rule_version)
    builtin_version = getattr(pinned, "builtin_version", None) or "demo-2026-10-04"
    disabled = set(getattr(pinned, "disabled", None) or [])
    overrides = getattr(pinned, "overrides", None) or {}
    import inspect

    if "builtin_version" in inspect.signature(generate_requirements).parameters:
        generated = generate_requirements(account, builtin_version=builtin_version)
    else:
        generated = generate_requirements(account)
    result: list[tuple[str, str]] = []
    for item in generated:
        if _attr(item, "applies") is False:
            continue
        req_id = str(_attr(item, "id"))
        if req_id in disabled:
            continue
        name = str(_attr(item, "name"))
        override = overrides.get(req_id) if isinstance(overrides, dict) else None
        if override is not None:
            overridden = _attr(override, "name")
            if overridden:
                name = str(overridden)
        result.append((req_id, name))
    extras = getattr(pinned, "extras", None) or []
    if extras:
        result.extend(_extra_requirements(extras, account))
    return result


def _extra_requirements(extras: list[Any], account: Any) -> list[tuple[str, str]]:
    from fcc_api.rules.library import library_requirements

    rules = []
    for extra in extras:
        if isinstance(extra, dict) and extra.get("enabled") is False:
            continue
        rules.append(extra)
    produced = library_requirements(rules, account)
    return [(str(_attr(item, "id")), str(_attr(item, "name"))) for item in produced]


def _writable_case(
    session: Session,
    actor: Any,
    case_id: str,
    operation: str,
    *,
    request_id: str | None = None,
) -> Any:
    case = _visible_case(session, actor, case_id)
    require_case_write(actor, case, operation, case_id=case_id, request_id=request_id)
    return case


def _visible_case(session: Session, actor: Any, case_id: str) -> Any:
    case_model = _model("fcc_api.db.models.cases", "Case")
    case = session.get(case_model, case_id)
    if case is None or not _is_visible(actor, case):
        raise _not_found("case", case_id, "Case not found.")
    return case


def _is_visible(actor: Any, case: Any) -> bool:
    return bool(case_visible(actor, case))


def _case_detail(session: Session, actor: Any, case_id: str) -> Any:
    del actor
    from fcc_api.services.cases import load_case_detail

    return load_case_detail(session, case_id)


def _write_audit(writer: Any, *, action: str, summary: str, before: Any, after: Any) -> None:
    attempts = (
        {"action": action, "summary": summary, "changes": None, "before": before, "after": after},
        {"action": action, "summary": summary, "before": before, "after": after},
        {"action": action, "summary": summary, "changes": None, "before_value": before, "after_value": after},
        {"action": action, "summary": summary, "before_value": before, "after_value": after},
    )
    error: TypeError | None = None
    for payload in attempts:
        try:
            writer.audit(**payload)
            return
        except TypeError as exc:
            error = exc
    if error is not None:
        raise error


def _require_local_backend(settings: object) -> None:
    if str(getattr(settings, "storage_backend", "local") or "local").lower() != "local":
        raise _not_found("storage", "local", "Not found.")


def _require_signature(settings: object, method: str, resource_id: str, expires: int, signature: str) -> None:
    secret = str(getattr(settings, "local_url_signing_secret", "") or "")
    now_unix = int(datetime.now(timezone.utc).timestamp())
    if expires <= now_unix or not local_signature_ok(secret, method, resource_id, expires, signature):
        raise _signed_forbidden()


def _detect_mime(header: bytes) -> str | None:
    if header.startswith(_PDF):
        return "application/pdf"
    if header.startswith(_PNG):
        return "image/png"
    if header.startswith(_JPEG):
        return "image/jpeg"
    return None


def _object_key(case_id: str, upload_id: str) -> str:
    if not case_id or "/" in case_id or ".." in case_id or "/" in upload_id or ".." in upload_id:
        raise _not_found("case", case_id, "Case not found.")
    return f"cases/{case_id}/documents/{upload_id}/original"


def _new_id(prefix: str) -> str:
    from fcc_api.ids import new_id

    try:
        return new_id(prefix)
    except ValueError:
        import secrets

        alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
        return prefix + "_" + "".join(secrets.choice(alphabet) for _ in range(8))


def _model(module_name: str, *names: str) -> Any:
    module = importlib.import_module(module_name)
    for name in names:
        value = getattr(module, name, None)
        if value is not None:
            return value
    raise ImportError(f"{module_name} must define {names[0]}")


def _settings() -> object:
    from fcc_api.config import get_settings

    return get_settings()


def _api_error(status: int, code: str, message: str, details: dict[str, Any]):
    from fcc_api.errors import ApiError

    try:
        return ApiError(status, code, message, details)
    except TypeError:
        return ApiError(status_code=status, code=code, message=message, details=details)


def _validation(details: dict[str, Any]):
    return _api_error(400, "VALIDATION_FAILED", _VALIDATION, details)


def _gate(gate: str, message: str):
    return _api_error(422, "GATE_FAILED", message, {"gate": gate})


def _not_found(resource: str, resource_id: str, message: str):
    return _api_error(404, "NOT_FOUND", message, {"resource": resource, "id": resource_id})


def _signed_forbidden():
    return _api_error(403, "FORBIDDEN", _SIGNED_FORBIDDEN, {})


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clip(summary: str) -> str:
    if len(summary) <= 500:
        return summary
    return summary[:499] + "…"


def _max_bytes(settings: object) -> int:
    return int(getattr(settings, "upload_max_bytes", 25_000_000))


def _upload_ttl(settings: object) -> int:
    return int(getattr(settings, "upload_url_ttl_seconds", 900))


def _download_ttl(settings: object) -> int:
    return int(getattr(settings, "download_url_ttl_seconds", 300))


def _allowed_mimes(settings: object) -> set[str]:
    raw = getattr(settings, "upload_allowed_mime", "application/pdf,image/jpeg,image/png")
    if isinstance(raw, str):
        return {part.strip() for part in raw.split(",") if part.strip()}
    return {str(part).strip() for part in raw}


def _async_scan_enabled(settings: object) -> bool:
    value = getattr(settings, "async_scan", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _construct(cls: Any, values: dict[str, Any]) -> Any:
    import dataclasses

    if dataclasses.is_dataclass(cls):
        names = {field.name for field in dataclasses.fields(cls)}
    else:
        names = set(getattr(cls, "__annotations__", {}))
    kwargs: dict[str, Any] = {}
    used: set[str] = set()
    for key, value in values.items():
        for candidate in (key, _camel_to_snake(key), _snake_to_camel(key)):
            if candidate in names and candidate not in used:
                kwargs[candidate] = value
                used.add(candidate)
                break
    return cls(**kwargs)


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
        snake = _camel_to_snake(name)
        camel = _snake_to_camel(name)
        if snake in obj:
            return obj[snake]
        return obj.get(camel)
    if hasattr(obj, name):
        return getattr(obj, name)
    snake = _camel_to_snake(name)
    if hasattr(obj, snake):
        return getattr(obj, snake)
    camel = _snake_to_camel(name)
    if hasattr(obj, camel):
        return getattr(obj, camel)
    return None


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars)


def _snake_to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])
