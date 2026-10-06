"""设立文件、实体分类、预审和案件助手。这些接口不改 parties，也不增加案件 version。"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fcc_api.adapters.llm import get_language_model
from fcc_api.ai import PROMPT_IDS
from fcc_api.ai.validate import (
    clears_existing_pep,
    rejects_assistant_instruction,
    validate_assistant_output,
)
from fcc_api.auth.actor import Actor, Role, fail_auth
from fcc_api.auth.policy import require_case_visible
from fcc_api.config import get_settings
from fcc_api.db.models.cases import Case
from fcc_api.db.models.documents import CaseDocument
from fcc_api.db.models.extractions import DocumentExtraction
from fcc_api.db.models.launch import AiSuggestion, DocumentEntity, DocumentRelation
from fcc_api.db.models.parties import Party
from fcc_api.errors import ApiError
from fcc_api.ids import new_id
from fcc_api.labels import ENTITY_LABELS
from fcc_api.rules.constants import ENTITY_TYPES
from fcc_api.rules.insight import analyze
from fcc_api.rules.types import Party as RuleParty
from fcc_api.schemas.common import js_number
from fcc_api.services.case_write import insert_audit
from fcc_api.services.cases import _bundle

logger = logging.getLogger("fcc_api.ai")

_UNAVAILABLE = "The assistant is not configured."
_OPEN = frozenset({"BUILDING", "DOCS_REQUESTED", "RETURNED"})
_WRITERS = frozenset({Role.ADVISOR, Role.OPERATIONS, Role.ADMIN})
_READ_ONLY = frozenset({"APPROVED", "READY_FOR_COMPLIANCE"})
_CONTROLLER = frozenset({"CONTROLS", "DIRECTOR_OF", "TRUSTEE_OF"})
_CONFIDENCE_FLOOR = 0.75
_ASSISTANT_LIMIT = 30

_LETTER = r"[^\W\d_]"
_NAME = rf"[A-Z](?:{_LETTER}|[-'.])+(?:\s+[A-Z](?:{_LETTER}|[-'.])+)+"
_HOLDS = re.compile(rf"^\s*({_NAME})\s+(?:holds|owns|has)\s+(\d+(?:\.\d+)?)\s*%")
_ROLE_ONLY = re.compile(rf"^\s*({_NAME})\s+is\s+(?:a|an|the)\s+")
_ROLE = re.compile(
    r"(director|trustee|executor|officer|signing authority|signatory|"
    r"controller|general partner|chief|councillor)",
    re.IGNORECASE,
)
_UNDER = re.compile(
    rf"\b(?:of|in)\s+([A-Z](?:{_LETTER}|[&'.\s-])+?(?:Inc\.?|Ltd\.?|Corp\.?|LP|Trust))"
)
_US_PERSON = re.compile(r"\b(us person|u\.s\. person|american|us citizen)\b", re.IGNORECASE)
_PEP = re.compile(r"\b(pep|hio|politically exposed|head of an international)\b", re.IGNORECASE)
_GAPS = re.compile(r"why|continue|missing|blocked|stuck|gap", re.IGNORECASE)
_FILES = re.compile(r"document|checklist|outstanding|need|collect", re.IGNORECASE)
_ENTITY_TYPE = re.compile(r"entity type|subtype|which type|classify", re.IGNORECASE)

_HELP = (
    "I can:\n"
    "• explain why the case can't move to the next step\n"
    "• list outstanding documents and why each is required\n"
    "• check the entity subtype\n"
    "• turn a sentence like \"Jane Doe owns 30% and is a director\" into a draft node\n\n"
    "I can't clear PEP status, sanctions or FATCA classification — those come from "
    "approved screening systems and Compliance."
)
_READ_ONLY_TEXT = (
    "This case is read-only at its current status, so I can't propose changes to the structure."
)


def extract_formation(
    session: Session,
    actor: Actor,
    case_id: str,
    document_ids: list[str],
    *,
    request_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    case = _visible_case(session, actor, case_id, request_id)
    _require_writer(session, actor, case, "extractFormation", request_id)
    documents = _documents(session, case_id, document_ids)
    for document in documents:
        if document.extraction != "EXTRACTED" or document.relation_status != "READY":
            _abort(
                session,
                ApiError(
                    422,
                    "GATE_FAILED",
                    "Document reading has not finished.",
                    {"gate": "EXTRACTION_INCOMPLETE"},
                ),
            )
    parties = list(session.scalars(select(Party).where(Party.case_id == case.id)))
    entities, model_name, prompt_version = _formation_entities(session, documents, parties)
    body = {
        "suggestionId": new_id("sug"),
        "model": model_name or _named_model("extract"),
        "warnings": [],
        "entities": entities,
    }
    _store(
        session,
        case,
        actor,
        kind="EXTRACT_FORMATION",
        model=body["model"],
        prompt_version=prompt_version or PROMPT_IDS["relations"],
        body=body,
        summary="Suggested parties from formation documents",
        request_id=request_id,
        correlation_id=correlation_id,
        started=started,
        input_chars=0,
    )
    return body


def classify_name(session: Session, legal_name: str, notes: str) -> dict[str, Any]:
    """新建案件页还没有案件 id。只返回建议，不写案件、不写审计。"""

    parsed = _complete_json(
        session,
        system=_prompt_text("classify.v1.txt"),
        user=f"<legal_name>{legal_name}</legal_name>\n<notes>{notes}</notes>",
        max_tokens=1024,
    )
    entity_type = parsed.get("type")
    if entity_type not in ENTITY_TYPES:
        entity_type = None
    reasons = parsed.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    return {
        "type": entity_type,
        "confidence": _confidence(parsed.get("confidence")),
        "reasons": [str(item) for item in reasons if isinstance(item, str)][:3],
        "model": _configured_model_name(),
    }


def classify_entity(
    session: Session,
    actor: Actor,
    case_id: str,
    *,
    legal_name: str,
    notes: str,
    request_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    case = _visible_case(session, actor, case_id, request_id)
    _require_writer(session, actor, case, "classifyEntity", request_id)
    _require_capacity(session, case.id)
    parsed = _complete_json(
        session,
        system=_prompt_text("classify.v1.txt"),
        user=f"<legal_name>{legal_name}</legal_name>\n<notes>{notes}</notes>",
        max_tokens=1024,
    )
    entity_type = parsed.get("type")
    if entity_type not in ENTITY_TYPES:
        entity_type = None
    reasons = parsed.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    reasons = [str(item) for item in reasons if isinstance(item, str)][:3]
    model_name = _configured_model_name()
    body = {
        "suggestionId": new_id("sug"),
        "model": model_name,
        "type": entity_type,
        "confidence": _confidence(parsed.get("confidence")),
        "reasons": reasons,
    }
    _store(
        session,
        case,
        actor,
        kind="CLASSIFY_ENTITY",
        model=model_name,
        prompt_version="classify.v1",
        body=body,
        summary="Suggested an entity subtype",
        request_id=request_id,
        correlation_id=correlation_id,
        started=started,
        input_chars=len(legal_name) + len(notes),
    )
    return body


def pre_review(
    session: Session,
    actor: Actor,
    case_id: str,
    *,
    request_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    case = _visible_case(session, actor, case_id, request_id)
    _require_writer(session, actor, case, "preReview", request_id)
    bundle = _bundle(session, case)
    insight = analyze(bundle.record, bundle.snapshots)
    findings = _rule_findings(bundle.record, insight.identify)
    body = {
        "suggestionId": new_id("sug"),
        "model": "rule-engine",
        "findings": findings,
    }
    _store(
        session,
        case,
        actor,
        kind="PRE_REVIEW",
        model="rule-engine",
        prompt_version=None,
        body=body,
        summary="Suggested pre-review findings",
        request_id=request_id,
        correlation_id=correlation_id,
        started=started,
        input_chars=0,
    )
    return body


def assistant(
    session: Session,
    actor: Actor,
    case_id: str,
    message: str,
    *,
    request_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    case = _visible_case(session, actor, case_id, request_id)
    bundle = _bundle(session, case)
    insight = analyze(bundle.record, bundle.snapshots)
    parties = list(bundle.record.parties)
    editable = _editable(actor, case.status)
    proposal = _parse_instruction(message, parties, case.legal_name)
    if proposal is not None:
        if clears_existing_pep(proposal, _pep_names(parties)) or not editable:
            text_out = _READ_ONLY_TEXT if not editable else _HELP
            kind = "ANSWER" if not editable else "HELP"
            return _assistant_body(
                session, case, actor, kind=kind, text=text_out, proposal=None,
                request_id=request_id, correlation_id=correlation_id, started=started,
                input_chars=len(message),
            )
        return _assistant_body(
            session, case, actor, kind="PROPOSAL",
            text=_proposal_text(proposal, parties), proposal=proposal,
            request_id=request_id, correlation_id=correlation_id, started=started,
            input_chars=len(message),
        )
    if _GAPS.search(message):
        return _assistant_body(
            session, case, actor, kind="ANSWER",
            text=_gap_text(bundle.record, insight), proposal=None,
            request_id=request_id, correlation_id=correlation_id, started=started,
            input_chars=len(message),
        )
    if _FILES.search(message):
        return _assistant_body(
            session, case, actor, kind="ANSWER",
            text=_file_text(bundle.record, insight), proposal=None,
            request_id=request_id, correlation_id=correlation_id, started=started,
            input_chars=len(message),
        )
    if _ENTITY_TYPE.search(message):
        _require_capacity(session, case.id)
        parsed = _complete_json(
            session,
            system=_prompt_text("classify.v1.txt"),
            user=f"<legal_name>{case.legal_name}</legal_name>\n<notes></notes>",
            max_tokens=1024,
        )
        entity_type = parsed.get("type") if parsed.get("type") in ENTITY_TYPES else None
        text_out = (
            "The name alone doesn't tell me enough. Check the formation document."
            if entity_type is None
            else f"I'd classify this as {entity_type}."
        )
        return _assistant_body(
            session, case, actor, kind="ANSWER", text=text_out, proposal=None,
            model=_configured_model_name(), prompt_version="classify.v1",
            request_id=request_id, correlation_id=correlation_id, started=started,
            input_chars=len(message),
        )
    if rejects_assistant_instruction(message):
        return _assistant_body(
            session, case, actor, kind="HELP", text=_HELP, proposal=None,
            request_id=request_id, correlation_id=correlation_id, started=started,
            input_chars=len(message),
        )
    _require_capacity(session, case.id)
    names = [party.legal_name for party in parties if party.kind == "ENTITY"]
    parsed = _complete_json(
        session,
        system=_prompt_text("assistant_parse.v1.txt"),
        user=f"<sentence>{message}</sentence>\n<entities>{', '.join(names)}</entities>",
        max_tokens=1024,
    )
    proposal = validate_assistant_output(
        message,
        parsed,
        entity_names=names,
        pep_names=_pep_names(parties),
    )
    if (
        proposal is None
        or not editable
        or not proposal.get("legalName")
        or not proposal.get("parentName")
    ):
        text_out = _READ_ONLY_TEXT if not editable else _HELP
        kind = "ANSWER" if not editable else "HELP"
        return _assistant_body(
            session, case, actor, kind=kind, text=text_out, proposal=None,
            model=_configured_model_name(), prompt_version="assistant_parse.v1",
            request_id=request_id, correlation_id=correlation_id, started=started,
            input_chars=len(message),
        )
    return _assistant_body(
        session, case, actor, kind="PROPOSAL",
        text=_proposal_text(proposal, parties), proposal=proposal,
        model=_configured_model_name(), prompt_version="assistant_parse.v1",
        request_id=request_id, correlation_id=correlation_id, started=started,
        input_chars=len(message),
    )


def _assistant_body(
    session: Session,
    case: Case,
    actor: Actor,
    *,
    kind: str,
    text: str,
    proposal: dict[str, Any] | None,
    request_id: str,
    correlation_id: str,
    started: float,
    input_chars: int,
    model: str = "rule-engine",
    prompt_version: str | None = None,
) -> dict[str, Any]:
    body = {
        "suggestionId": new_id("sug"),
        "kind": kind,
        "model": model,
        "text": text,
        "proposal": proposal,
    }
    _store(
        session,
        case,
        actor,
        kind="ASSISTANT",
        model=model,
        prompt_version=prompt_version,
        body=body,
        summary="Answered with the case assistant",
        request_id=request_id,
        correlation_id=correlation_id,
        started=started,
        input_chars=input_chars,
    )
    return body


def _store(
    session: Session,
    case: Case,
    actor: Actor,
    *,
    kind: str,
    model: str,
    prompt_version: str | None,
    body: dict[str, Any],
    summary: str,
    request_id: str,
    correlation_id: str,
    started: float,
    input_chars: int,
) -> None:
    if not _table_exists(session, "ai_suggestions"):
        _abort(session, ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {}))
    now = datetime.now(UTC)
    session.add(
        AiSuggestion(
            id=str(body["suggestionId"]),
            case_id=case.id,
            kind=kind,
            stage="SUGGESTED",
            prompt_version=prompt_version,
            model=model,
            body=body,
            created_by=actor.id,
            created_at=now,
        )
    )
    ai_meta: dict[str, Any] = {"accepted": False, "stage": "SUGGESTED", "model": model}
    if prompt_version:
        ai_meta["promptVersion"] = prompt_version
    insert_audit(
        session,
        case=case,
        actor=actor,
        at=now,
        version=case.version,
        request_id=request_id,
        correlation_id=correlation_id,
        action="AI_SUGGESTION",
        summary=summary,
        after={"suggestion": body, "ai": ai_meta},
        ai_model=model,
        ai_accepted=False,
    )
    session.commit()
    _log_call(
        model=model,
        prompt_version=prompt_version,
        started=started,
        input_chars=input_chars,
        output_chars=len(json.dumps(body, ensure_ascii=False)),
        finish_reason="stop",
        warnings=[],
    )


def _formation_entities(
    session: Session,
    documents: list[CaseDocument],
    parties: list[Party],
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    names: dict[str, str] = {}
    for party in parties:
        names.setdefault(party.legal_name.casefold().strip(), party.id)
    used: set[str] = set()
    entities: list[dict[str, Any]] = []
    model_name: str | None = None
    prompt_version: str | None = None
    for document in documents:
        extraction = session.scalar(
            select(DocumentExtraction)
            .where(
                DocumentExtraction.document_id == document.id,
                DocumentExtraction.status == "EXTRACTED",
            )
            .order_by(DocumentExtraction.started_at.desc())
        )
        if extraction is None:
            continue
        if model_name is None and extraction.relation_model:
            model_name = extraction.relation_model
        if prompt_version is None and extraction.relation_prompt_version:
            prompt_version = extraction.relation_prompt_version
        rows = list(
            session.scalars(
                select(DocumentEntity).where(DocumentEntity.extraction_id == extraction.id)
            )
        )
        relations = list(
            session.scalars(
                select(DocumentRelation).where(DocumentRelation.extraction_id == extraction.id)
            )
        )
        key_map = {row.temp_key: _unique_key(used, row.temp_key) for row in rows}
        grouped: dict[str, list[tuple[DocumentRelation, str]]] = {}
        for relation in relations:
            source = key_map.get(relation.from_temp_key, relation.from_temp_key)
            if relation.to_temp_key == "CASE_ROOT":
                target = "CASE_ROOT"
            else:
                target = key_map.get(relation.to_temp_key, relation.to_temp_key)
            grouped.setdefault(source, []).append((relation, target))
        for row in rows:
            temp_id = key_map[row.temp_key]
            related = grouped.get(temp_id, [])
            types: list[str] = []
            percent = None
            parent = "CASE_ROOT"
            controller = False
            signer = False
            for relation, target in related:
                if relation.relation_type not in types:
                    types.append(relation.relation_type)
                if relation.relation_type == "OWNS" and percent is None:
                    percent = relation.ownership_percent
                    parent = target
                if relation.relation_type in _CONTROLLER:
                    controller = True
                if relation.relation_type == "SIGNS":
                    signer = True
            confidence = float(row.confidence)
            item: dict[str, Any] = {
                "tempId": temp_id,
                "kind": row.kind,
                "legalName": row.legal_name,
                "title": row.title or "",
                "ownershipPercent": js_number(0 if percent is None else percent),
                "isController": controller,
                "isSigningAuthority": signer,
                "country": row.country or "",
                "confidence": js_number(confidence),
                "includeByDefault": confidence >= _CONFIDENCE_FLOOR,
                "citation": row.citation,
                "parentTempId": parent,
                "matchedPartyId": names.get(row.legal_name.casefold().strip()),
                "relationTypes": types,
            }
            if row.entity_type:
                item["entityType"] = row.entity_type
            entities.append(item)
    return entities, model_name, prompt_version


def _documents(session: Session, case_id: str, document_ids: list[str]) -> list[CaseDocument]:
    rows = list(
        session.scalars(
            select(CaseDocument).where(
                CaseDocument.case_id == case_id,
                CaseDocument.id.in_(document_ids),
            )
        )
    )
    found = {row.id: row for row in rows}
    missing = next((item for item in document_ids if item not in found), None)
    if missing is not None:
        _abort(
            session,
            ApiError(404, "NOT_FOUND", "Document not found.", {"resource": "document", "id": missing}),
        )
    return [found[item] for item in document_ids]


def _rule_findings(record: Any, identify: list[RuleParty]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    identity = record.checklist.get("identity")
    has_identity = bool(identity and identity.document_ids)
    if not has_identity:
        for person in identify:
            reason = (
                "control or signing authority"
                if person.is_signing_authority or person.is_controller
                else "≥25% ownership"
            )
            findings.append({
                "id": f"id-{person.id}",
                "severity": "high",
                "title": f"No ID evidence for {person.legal_name}",
                "detail": (
                    f"{person.legal_name} must be identified ({reason}). "
                    "No identity document is attached yet."
                ),
                "requirementId": "identity",
                "partyId": person.id,
            })
    pep = next((party for party in record.parties if party.is_pep_hio), None)
    if pep is not None:
        findings.append({
            "id": "f4",
            "severity": "low",
            "title": "Screening result required",
            "detail": (
                f"{pep.legal_name} is flagged PEP / HIO. Attach the result from the "
                "approved screening system; the assistant cannot clear PEP status."
            ),
            "requirementId": "pep",
            "partyId": pep.id,
        })
    return findings


def _parse_instruction(
    text: str,
    parties: list[RuleParty],
    root_name: str,
) -> dict[str, Any] | None:
    held = _HOLDS.match(text)
    role_only = _ROLE_ONLY.match(text)
    name = held.group(1) if held else role_only.group(1) if role_only else None
    if not name:
        return None
    role_match = _ROLE.search(text)
    role = role_match.group(1) if role_match else ""
    titled = re.sub(r"\b\w", lambda match: match.group(0).upper(), role) if role else "Shareholder"
    if titled == "Signatory":
        titled = "Authorized Signatory"
    root = next((party for party in parties if party.parent_id is None), None)
    under = _UNDER.search(text)
    parent = root
    if under is not None:
        prefix = under.group(1).strip().casefold()
        named = next(
            (
                party
                for party in parties
                if party.kind == "ENTITY" and party.legal_name.casefold().startswith(prefix)
            ),
            None,
        )
        if named is not None:
            parent = named
    parent_name = parent.legal_name if parent is not None else root_name
    percent = float(held.group(2)) if held else 0
    return {
        "legalName": name,
        "ownershipPercent": js_number(percent),
        "title": titled,
        "isController": bool(
            re.search(r"director|trustee|executor|controller|general partner|chief", role, re.I)
        ),
        "isSigningAuthority": bool(re.search(r"sign|director|trustee|executor|chief", text, re.I)),
        "isUsPerson": bool(_US_PERSON.search(text)),
        "isPepHio": bool(_PEP.search(text)),
        "parentName": parent_name,
    }


def _proposal_text(proposal: dict[str, Any], parties: list[RuleParty]) -> str:
    parent_name = str(proposal["parentName"])
    parent = next((party for party in parties if party.legal_name == parent_name), None)
    parent_id = parent.id if parent is not None else None
    total = sum(
        float(party.ownership_percent) for party in parties if party.parent_id == parent_id
    )
    total += float(proposal.get("ownershipPercent") or 0)
    shown = parent_name[:-1] if parent_name.endswith(".") else parent_name
    warning = ""
    if total > 100:
        warning = (
            f"\n\nHeads up: interests under {parent_name} would total {_format_percent(total)}. "
            "Adjust another holder after applying."
        )
    return (
        f"Here's what I would add under {shown}. Nothing is saved until you confirm.{warning}"
    )


def _gap_text(record: Any, insight: Any) -> str:
    gaps = _explain_gaps(record, insight)
    if not gaps:
        outstanding = len(insight.checklist) - insight.collected
        if outstanding:
            suffix = " is" if outstanding == 1 else "s are"
            return (
                "The structure and account details are complete. "
                f"{outstanding} document{suffix} still outstanding on the checklist."
            )
        return "Nothing is blocking this case. You can submit it for compliance review."
    blocks = [
        f"{index}. {gap['title']}\n{gap['detail']}\n→ {gap['action']}"
        for index, gap in enumerate(gaps, start=1)
    ]
    return "\n\n".join(blocks)


def _explain_gaps(record: Any, insight: Any) -> list[dict[str, str]]:
    by_id = {party.id: party for party in record.parties}
    gaps: list[dict[str, str]] = []
    for issue in insight.ownership_issues:
        party = by_id.get(issue.party_id) if issue.party_id else None
        name = party.legal_name if party is not None else record.legal_name
        children = [item for item in record.parties if item.parent_id == issue.party_id]
        total = sum(float(item.ownership_percent) for item in children)
        if issue.code == "MISSING_OWNER":
            gaps.append({
                "title": "No owners or controllers yet",
                "detail": (
                    f"{name} has nobody attached. FINTRAC needs to know who owns it "
                    "and who can act for it."
                ),
                "action": "Select the root card and add at least one person or entity.",
            })
        elif issue.code == "ENTITY_LEAF":
            label = ENTITY_LABELS.get(party.entity_type, "entity") if party else "entity"
            gaps.append({
                "title": f"{name} is not traced to people",
                "detail": (
                    f"{name} is a {label}. Ownership must be followed through every "
                    "company or trust until it ends at natural persons."
                ),
                "action": f"Select {name} and add its shareholders, partners or trustees.",
            })
        elif issue.code == "OWNERSHIP_TOTAL":
            shown = _format_percent(total)
            if total < 100:
                remainder = f"{_format_percent(100 - total)} is unaccounted for."
            else:
                remainder = "Interests overlap or are double-counted."
            gaps.append({
                "title": f"{name} totals {shown}",
                "detail": (
                    f"Disclosed interests under {name} add up to {shown}, not 100%. {remainder}"
                ),
                "action": f"Open {name} and correct the percentages, or add the missing holder.",
            })
        else:
            gaps.append({
                "title": "Structure incomplete",
                "detail": issue.message,
                "action": "Review the ownership graph.",
            })
    for gap in insight.details_gaps:
        gaps.append({
            "title": "Account details missing",
            "detail": gap.message,
            "action": "Open the Account details step.",
        })
    return gaps


def _file_text(record: Any, insight: Any) -> str:
    if insight.ownership_issues or insight.details_gaps:
        return (
            "The checklist isn't generated yet — the ownership structure and account details "
            "must be complete first. Ask me \"why can't I continue?\" for specifics."
        )
    missing = [item for item in insight.checklist if not _is_collected(record, item.id)]
    if not missing:
        return "Every checklist item is collected."
    lines = []
    for item in missing:
        marker = " (if applicable)" if item.conditional else ""
        lines.append(f"• {item.name}{marker} — {item.reason}")
    return "Still outstanding:\n" + "\n".join(lines)


def _is_collected(record: Any, requirement_id: str) -> bool:
    state = record.checklist.get(requirement_id)
    if state is None:
        return False
    return state.status in {"RECEIVED", "VERIFIED"}


def _format_percent(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number)}%"
    return f"{number:.1f}%"


def _pep_names(parties: list[RuleParty]) -> set[str]:
    return {party.legal_name.casefold().strip() for party in parties if party.is_pep_hio}


def _editable(actor: Actor, status: str) -> bool:
    if actor.role == Role.COMPLIANCE:
        return False
    return status not in _READ_ONLY


def _visible_case(session: Session, actor: Actor, case_id: str, request_id: str) -> Case:
    case = session.get(Case, case_id)
    try:
        require_case_visible(actor, case, case_id=case_id, request_id=request_id)
    except ApiError:
        session.rollback()
        raise
    assert case is not None
    return case


def _require_writer(
    session: Session,
    actor: Actor,
    case: Case,
    operation: str,
    request_id: str,
) -> None:
    if actor.role not in _WRITERS:
        session.rollback()
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message="Your role cannot perform this operation.",
            details={"reason": "ROLE", "role": actor.role.value, "operation": operation},
            reason="ROLE",
            operation=operation,
            case_id=case.id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )
    if case.status not in _OPEN:
        session.rollback()
        message = (
            "This case is APPROVED and can no longer be edited."
            if case.status == "APPROVED"
            else f"This case is {case.status} and can no longer be edited by your role."
        )
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message=message,
            details={"reason": "CASE_STATUS", "status": case.status, "operation": operation},
            reason="CASE_STATUS",
            operation=operation,
            case_id=case.id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )


def _require_model(session: Session, kind: str) -> None:
    del kind
    settings = get_settings()
    adapter = str(getattr(settings, "llm_adapter", "noop") or "noop").strip().lower()
    if adapter in {"", "noop"}:
        _abort(session, ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {}))


def _configured_model_name() -> str:
    settings = get_settings()
    return str(getattr(settings, "llm_model", "") or "gpt-4.1-mini")


def _prompt_text(filename: str) -> str:
    from fcc_api.ai import prompts_dir

    return (prompts_dir() / filename).read_text(encoding="utf-8")


def _complete_json(session: Session, *, system: str, user: str, max_tokens: int) -> dict[str, Any]:
    settings = get_settings()
    adapter = str(getattr(settings, "llm_adapter", "noop") or "noop").strip().lower()
    if adapter in {"", "noop"}:
        _abort(session, ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {}))
    try:
        return get_language_model(settings).complete_json(system=system, user=user, max_tokens=max_tokens)
    except ApiError as exc:
        _abort(session, exc)


def _require_capacity(session: Session, case_id: str) -> None:
    if not _table_exists(session, "ai_suggestions"):
        return
    count = session.execute(
        text(
            """
            SELECT count(*)
            FROM ai_suggestions
            WHERE case_id = :case_id
              AND kind IN ('ASSISTANT', 'CLASSIFY_ENTITY')
              AND prompt_version IS NOT NULL
              AND created_at >= now() - interval '1 hour'
            """
        ),
        {"case_id": case_id},
    ).scalar_one()
    if int(count) >= _ASSISTANT_LIMIT:
        _abort(
            session,
            ApiError(
                422,
                "GATE_FAILED",
                "Too many reading requests on this case. Try again later.",
                {"gate": "AI_BUSY"},
            ),
        )


def _named_model(slot: str) -> str:
    defaults = {
        "extract": "doc-extract-demo",
        "classify": "entity-classifier-demo",
        "assistant": "case-assistant-demo",
        "review": "pre-review-demo",
    }
    models = get_settings().ai_models
    index = {"extract": 0, "classify": 1, "assistant": 2, "review": 3}[slot]
    if len(models) > index and models[index]:
        return models[index]
    return defaults[slot]


def _unique_key(used: set[str], key: str) -> str:
    if key not in used:
        used.add(key)
        return key
    index = 2
    while f"{key}_{index}" in used:
        index += 1
    renamed = f"{key}_{index}"
    used.add(renamed)
    return renamed


def _confidence(value: object) -> float | None:
    """模型有时把 0.9 写成字符串，或把 90 当成百分数。统一收成 0 到 1。"""

    if isinstance(value, str):
        text_value = value.strip().rstrip("%").strip()
        try:
            value = float(text_value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number > 1 and number <= 100:
        number = number / 100
    if number < 0 or number > 1:
        return None
    return number


def _table_exists(session: Session, table: str) -> bool:
    found = session.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = :table
            """
        ),
        {"table": table},
    ).first()
    return found is not None


def _abort(session: Session, exc: Exception) -> Any:
    session.rollback()
    raise exc


def _log_call(
    *,
    model: str,
    prompt_version: str | None,
    started: float,
    input_chars: int,
    output_chars: int,
    finish_reason: str,
    warnings: list[str],
) -> None:
    counts: dict[str, int] = {}
    for warning in warnings:
        counts[warning] = counts.get(warning, 0) + 1
    logger.info(
        "ai_suggestion",
        extra={
            "model": model,
            "prompt_version": prompt_version or "",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "input_chars": input_chars,
            "output_chars": output_chars,
            "finish_reason": finish_reason,
            "warning_counts": counts,
        },
    )
