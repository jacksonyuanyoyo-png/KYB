"""PDF form fill. A missing template returns 422 TEMPLATE_MISSING and does not change ownership."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ALLOWED_SOURCES = frozenset(
    {
        "case.legalName",
        "case.reference",
        "case.province",
        "case.entityType",
        "party.legalName",
        "party.ownershipPercent",
    }
)

_TEMPLATE_MESSAGE = "The form template is not available."


@dataclass(frozen=True)
class FilledForm:
    pdf_bytes: bytes
    blanks: list[str]
    requirement_id: str
    file_name: str
    template_version: str


class FormFiller(Protocol):
    name: str

    def fill(
        self,
        *,
        case_id: str,
        template_id: str,
        values: Mapping[str, str] | None = None,
    ) -> FilledForm: ...


class MissingTemplateFiller:
    name = "missing-template"

    def fill(
        self,
        *,
        case_id: str,
        template_id: str,
        values: Mapping[str, str] | None = None,
    ) -> FilledForm:
        del case_id, template_id, values
        from fcc_api.errors import ApiError

        message = _TEMPLATE_MESSAGE
        details = {"gate": "TEMPLATE_MISSING"}
        try:
            raise ApiError(422, "GATE_FAILED", message, details)
        except TypeError:
            raise ApiError(status_code=422, code="GATE_FAILED", message=message, details=details) from None


class TemplateCatalogFiller:
    """Fill a local template. Unknown field sources are refused and produce no PDF."""

    name = "template-catalog"

    def fill(
        self,
        *,
        case_id: str,
        template_id: str,
        values: Mapping[str, str] | None = None,
    ) -> FilledForm:
        located = _locate_template(template_id)
        if located is None:
            return MissingTemplateFiller().fill(case_id=case_id, template_id=template_id)
        provided = dict(values or {})
        blanks: list[str] = []
        filled: list[tuple[str, str]] = []
        for pdf_field, source in located.fields:
            if source not in ALLOWED_SOURCES:
                return MissingTemplateFiller().fill(case_id="", template_id=template_id)
            text = str(provided.get(source, "") or "").strip()
            if text == "":
                blanks.append(pdf_field)
            else:
                filled.append((pdf_field, text))
        pdf_bytes = _render_pdf(located.pdf_bytes, filled)
        version = located.template_version
        file_name = _file_name(located.template_id, version)
        return FilledForm(
            pdf_bytes=pdf_bytes,
            blanks=blanks,
            requirement_id=located.requirement_id,
            file_name=file_name,
            template_version=version,
        )


@dataclass(frozen=True)
class _LocatedTemplate:
    template_id: str
    template_version: str
    requirement_id: str
    fields: tuple[tuple[str, str], ...]
    pdf_bytes: bytes


def get_form_filler(settings: object | None = None) -> FormFiller:
    del settings
    return TemplateCatalogFiller()


def template_root() -> Path | None:
    """Directory tests can point at a temporary template catalog."""

    raw = os.environ.get("FORM_TEMPLATE_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return None


def _locate_template(template_id: str) -> _LocatedTemplate | None:
    root = template_root()
    if root is None or not root.is_dir() or not template_id or "/" in template_id or ".." in template_id:
        return None
    root_resolved = root.resolve()
    matches: list[tuple[str, _LocatedTemplate]] = []
    for path in root_resolved.rglob("*.map.json"):
        if not _inside(root_resolved, path):
            continue
        located = _read_map(root_resolved, path, template_id)
        if located is not None:
            matches.append((located.template_version, located))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[-1][1]


def _read_map(root: Path, path: Path, template_id: str) -> _LocatedTemplate | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("templateId") or "") != template_id:
        return None
    template_version = str(payload.get("templateVersion") or "").strip()
    requirement_id = str(payload.get("requirementId") or "").strip()
    raw_fields = payload.get("fields")
    if not template_version or not requirement_id or not isinstance(raw_fields, list):
        return None
    fields: list[tuple[str, str]] = []
    for item in raw_fields:
        if not isinstance(item, dict):
            return None
        pdf_field = str(item.get("pdfField") or "").strip()
        source = str(item.get("source") or "").strip()
        if not pdf_field or not source:
            return None
        if source not in ALLOWED_SOURCES:
            return None
        fields.append((pdf_field, source))
    pdf_path = _pdf_path(root, path, payload, template_version)
    if pdf_path is None:
        return None
    try:
        pdf_bytes = pdf_path.read_bytes()
    except OSError:
        return None
    if not pdf_bytes.startswith(b"%PDF-"):
        return None
    return _LocatedTemplate(
        template_id=template_id,
        template_version=template_version,
        requirement_id=requirement_id,
        fields=tuple(fields),
        pdf_bytes=pdf_bytes,
    )


def _pdf_path(root: Path, map_path: Path, payload: dict[str, object], template_version: str) -> Path | None:
    sibling = map_path.with_name(f"{template_version}.pdf")
    if sibling.is_file() and _inside(root, sibling):
        return sibling
    pointer = payload.get("pdf")
    if not isinstance(pointer, str) or not pointer.strip():
        return None
    candidate = (map_path.parent / pointer).resolve()
    if candidate.is_file() and _inside(root, candidate):
        return candidate
    return None


def _inside(root: Path, path: Path) -> bool:
    resolved = path.resolve()
    return resolved == root or root in resolved.parents


def _render_pdf(template: bytes, filled: list[tuple[str, str]]) -> bytes:
    lines = [b"%FCC-FORM-FILL"]
    for name, value in filled:
        safe_name = " ".join(name.split())
        safe_value = " ".join(value.split())
        lines.append(f"% {safe_name}={safe_value}".encode())
    return template + b"\n" + b"\n".join(lines) + b"\n%%EOF\n"


def _file_name(template_id: str, template_version: str) -> str:
    raw = f"{template_id}-{template_version}.pdf"
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in raw)
    return (cleaned or "form.pdf")[:255]
