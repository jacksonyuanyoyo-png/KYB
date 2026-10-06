"""Signed local upload and download. Mounted only when STORAGE_BACKEND=local."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.services.documents import open_local_document, receive_local_upload

router = APIRouter(prefix="/api/v1/local-storage", tags=["local-storage"])


def _iter_file(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            yield chunk


@router.put("/uploads/{uploadId}", status_code=204)
async def put_upload(
    uploadId: str,
    request: Request,
    expires: int,
    sig: str,
    session: Session = Depends(get_db),
) -> Response:
    await receive_local_upload(
        session,
        upload_id=uploadId,
        expires=expires,
        signature=sig,
        stream=request.stream(),
    )
    return Response(status_code=204)


@router.get("/documents/{documentId}")
def get_document(
    documentId: str,
    expires: int,
    sig: str,
    session: Session = Depends(get_db),
) -> StreamingResponse:
    download = open_local_document(session, document_id=documentId, expires=expires, signature=sig)
    return StreamingResponse(
        _iter_file(download.path),
        media_type=download.media_type,
        headers={"Content-Disposition": download.disposition},
    )
