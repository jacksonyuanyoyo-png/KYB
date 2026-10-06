"""模型输出校验：引用必须落在页正文上，多余字段丢掉，百分比只在两种情况下保留。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

_ENTITY_KEYS = (
    "tempKey",
    "kind",
    "legalName",
    "entityType",
    "title",
    "country",
    "confidence",
    "pageNo",
    "quote",
)
_RELATION_KEYS = (
    "relationType",
    "fromTempKey",
    "toTempKey",
    "ownershipPercent",
    "shares",
    "totalShares",
    "confidence",
    "pageNo",
    "quote",
)
_PROPOSAL_KEYS = (
    "legalName",
    "ownershipPercent",
    "title",
    "isController",
    "isSigningAuthority",
    "isUsPerson",
    "isPepHio",
    "parentName",
)
_RELATION_TYPES = frozenset({
    "OWNS",
    "CONTROLS",
    "SIGNS",
    "DIRECTOR_OF",
    "TRUSTEE_OF",
    "BENEFICIARY_OF",
    "OFFICER_OF",
})
_ENTITY_TARGETS = frozenset({
    "OWNS",
    "CONTROLS",
    "DIRECTOR_OF",
    "TRUSTEE_OF",
    "BENEFICIARY_OF",
    "OFFICER_OF",
})
_CYCLE_TYPES = frozenset({"OWNS", "CONTROLS"})
_CONFIDENCE_FLOOR = 0.75
_QUOTE_LIMIT = 240

_APPROVE_CASE = re.compile(r"\bapprove\b.{0,40}\bcase\b", re.IGNORECASE)
_CLEAR_PEP = re.compile(
    r"\bclear\b.{0,40}\b(?:pep|hio)\b|\b(?:pep|hio)\b.{0,40}\bclear\b",
    re.IGNORECASE,
)
_IGNORE_RULES = re.compile(
    r"\bignore\b.{0,40}\b(?:rules|instructions)\b",
    re.IGNORECASE,
)
_WAIVED = re.compile(r"\bwaiv(?:e|ed|er)\b", re.IGNORECASE)
_PEP_CLAIM = re.compile(
    r"\b(?:not a pep|is not a pep|isn't a pep|cleared pep|pep clear|no pep)\b",
    re.IGNORECASE,
)
_SANCTIONS_CLAIM = re.compile(r"\bsanctions?\b", re.IGNORECASE)
_FATCA_CLAIM = re.compile(r"\bfatca\b", re.IGNORECASE)
_DASHED_ID = re.compile(r"\d{3}-\d{3}-\d{3}")
_LONG_DIGITS = re.compile(r"\d{9,}")


@dataclass
class RelationValidation:
    entities: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def redact_identifiers(text: str) -> str:
    """送模型前的副本。库里的页正文保持原文。"""

    redacted = _DASHED_ID.sub("[REDACTED_ID]", text)
    return _LONG_DIGITS.sub("[REDACTED_ID]", redacted)


def rejects_assistant_instruction(sentence: str) -> bool:
    """员工这句话若是在要求批准案件、清除 PEP 或忽略规则，则不生成建议。"""

    return bool(
        _APPROVE_CASE.search(sentence)
        or _CLEAR_PEP.search(sentence)
        or _IGNORE_RULES.search(sentence)
    )


def clears_existing_pep(proposal: dict[str, Any], pep_names: set[str]) -> bool:
    """不能用 isPepHio false 覆盖图上已经是 true 的人。"""

    if proposal.get("isPepHio") is True:
        return False
    name = str(proposal.get("legalName", "")).casefold().strip()
    return bool(name) and name in pep_names


def validate_assistant_output(
    sentence: str,
    payload: object,
    *,
    entity_names: list[str],
    pep_names: set[str] | None = None,
) -> dict[str, Any] | None:
    """模型给出的草稿。注入或越权字段使 proposal 为空。"""

    if rejects_assistant_instruction(sentence):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("proposal", payload if "legalName" in payload else None)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    if any(_forbidden_proposal_key(key) for key in raw):
        return None
    if any(_clearance_text(value) for value in raw.values() if isinstance(value, str)):
        return None
    proposal = {key: raw[key] for key in _PROPOSAL_KEYS if key in raw}
    parent = proposal.get("parentName")
    if parent is not None:
        wanted = str(parent).casefold().strip()
        copied = next(
            (name for name in entity_names if name.casefold().strip() == wanted),
            None,
        )
        proposal["parentName"] = copied
    if clears_existing_pep(proposal, pep_names or set()):
        return None
    if "isPepHio" in proposal and proposal["isPepHio"] is False and _CLEAR_PEP.search(sentence):
        return None
    return proposal


def validate_relation_output(payload: object, pages: dict[int, str]) -> RelationValidation:
    """夹具里的模型 JSON 与页正文。对不上的引用丢掉，股数没有总数时百分比为空。"""

    result = RelationValidation()
    if not isinstance(payload, dict):
        return result
    raw_entities = payload.get("entities")
    raw_relations = payload.get("relations")
    entities: list[dict[str, Any]] = []
    if isinstance(raw_entities, list):
        for item in raw_entities:
            kept = _entity(item, pages, result.warnings)
            if kept is not None:
                entities.append(kept)
    by_key = {item["tempKey"]: item for item in entities}
    relations: list[dict[str, Any]] = []
    if isinstance(raw_relations, list):
        for item in raw_relations:
            kept = _relation(item, pages, by_key, result.warnings)
            if kept is not None:
                relations.append(kept)
    result.relations = _drop_cycles(relations, result.warnings)
    result.entities = entities
    return result


def validate_pre_review_output(payload: object, pages: dict[int, str]) -> list[dict[str, Any]]:
    """只留下带引用、且没有豁免或 PEP 断言的发现。"""

    if not isinstance(payload, dict):
        return []
    raw = payload.get("findings")
    if not isinstance(raw, list):
        return []
    kept: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if any(_sensitive_key(key) for key in item):
            continue
        blob = " ".join(str(value) for value in item.values() if isinstance(value, str))
        if _WAIVED.search(blob) or _PEP_CLAIM.search(blob) or _FATCA_CLAIM.search(blob):
            continue
        if _SANCTIONS_CLAIM.search(blob) and re.search(r"\bclear", blob, re.IGNORECASE):
            continue
        severity = item.get("severity")
        quote = item.get("quote")
        page_no = item.get("pageNo")
        if severity not in {"high", "medium", "low"}:
            continue
        if not isinstance(page_no, int) or isinstance(page_no, bool):
            continue
        page = pages.get(page_no)
        if not isinstance(quote, str) or page is None or not _grounded(quote, page):
            continue
        finding = {
            "severity": severity,
            "quote": quote,
            "pageNo": page_no,
        }
        if isinstance(item.get("title"), str):
            finding["title"] = item["title"]
        if isinstance(item.get("detail"), str):
            finding["detail"] = item["detail"]
        if isinstance(item.get("requirementId"), str):
            finding["requirementId"] = item["requirementId"]
        kept.append(finding)
    return kept


def _entity(item: object, pages: dict[int, str], warnings: list[str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    cleaned, dropped = _take(item, _ENTITY_KEYS)
    if dropped:
        warnings.append("FIELD_DROPPED")
    quote = cleaned.get("quote")
    page_no = cleaned.get("pageNo")
    page = pages.get(page_no) if isinstance(page_no, int) and not isinstance(page_no, bool) else None
    if not isinstance(quote, str) or page is None or not _grounded(quote, page):
        warnings.append("UNGROUNDED")
        return None
    kind = cleaned.get("kind")
    legal_name = cleaned.get("legalName")
    temp_key = cleaned.get("tempKey")
    confidence = _unit_interval(cleaned.get("confidence"))
    if kind not in {"PERSON", "ENTITY"} or not isinstance(legal_name, str) or not legal_name.strip():
        return None
    if not isinstance(temp_key, str) or not temp_key.strip() or confidence is None:
        return None
    kept: dict[str, Any] = {
        "tempKey": temp_key,
        "kind": kind,
        "legalName": legal_name,
        "confidence": confidence,
        "pageNo": page_no,
        "quote": quote,
        "includeByDefault": confidence >= _CONFIDENCE_FLOOR,
    }
    for optional in ("entityType", "title", "country"):
        value = cleaned.get(optional)
        if isinstance(value, str) and value.strip():
            kept[optional] = value
    return kept


def _relation(
    item: object,
    pages: dict[int, str],
    entities: dict[str, dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    cleaned, dropped = _take(item, _RELATION_KEYS)
    if dropped:
        warnings.append("FIELD_DROPPED")
    quote = cleaned.get("quote")
    page_no = cleaned.get("pageNo")
    page = pages.get(page_no) if isinstance(page_no, int) and not isinstance(page_no, bool) else None
    if not isinstance(quote, str) or page is None or not _grounded(quote, page):
        warnings.append("UNGROUNDED")
        return None
    relation_type = cleaned.get("relationType")
    source = cleaned.get("fromTempKey")
    target = cleaned.get("toTempKey")
    confidence = _unit_interval(cleaned.get("confidence"))
    if relation_type not in _RELATION_TYPES or confidence is None:
        return None
    if not isinstance(source, str) or not isinstance(target, str):
        return None
    if source not in entities and source != "CASE_ROOT":
        return None
    if target not in entities and target != "CASE_ROOT":
        return None
    if relation_type in _ENTITY_TARGETS and not _is_entity_side(target, entities):
        return None
    percent, percent_warnings, drop = _ownership(cleaned, quote, page)
    warnings.extend(percent_warnings)
    if drop:
        return None
    kept = {
        "relationType": relation_type,
        "fromTempKey": source,
        "toTempKey": target,
        "ownershipPercent": percent,
        "confidence": confidence,
        "pageNo": page_no,
        "quote": quote,
    }
    if "shares" in cleaned:
        kept["shares"] = cleaned.get("shares")
    if "totalShares" in cleaned:
        kept["totalShares"] = cleaned.get("totalShares")
    return kept


def _ownership(
    cleaned: dict[str, Any],
    quote: str,
    page: str,
) -> tuple[int | float | None, list[str], bool]:
    has_shares = "shares" in cleaned and cleaned.get("shares") is not None
    has_total = "totalShares" in cleaned and cleaned.get("totalShares") is not None
    if has_shares and not has_total:
        return None, ["SHARES_WITHOUT_TOTAL"], False
    if has_shares and has_total:
        shares = _plain_number(cleaned.get("shares"))
        total = _plain_number(cleaned.get("totalShares"))
        if shares is None or total is None or total == 0:
            return None, ["PERCENT_DROPPED"], True
        percent = (Decimal(str(shares)) / Decimal(str(total)) * Decimal(100)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        number = float(percent)
        if number < 0 or number > 100:
            return None, ["PERCENT_DROPPED"], True
        return _whole(number), [], False
    if "ownershipPercent" not in cleaned or cleaned.get("ownershipPercent") is None:
        return None, [], False
    number = _plain_number(cleaned.get("ownershipPercent"))
    if number is None or number < 0 or number > 100:
        return None, ["PERCENT_DROPPED"], True
    if "%" not in quote and "%" not in page:
        return None, [], False
    return _whole(number), [], False


def _drop_cycles(relations: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    edges: dict[str, list[str]] = {}
    kept: list[dict[str, Any]] = []
    for relation in relations:
        if relation["relationType"] not in _CYCLE_TYPES:
            kept.append(relation)
            continue
        source = relation["fromTempKey"]
        target = relation["toTempKey"]
        if _reaches(edges, target, source):
            warnings.append("CYCLE_DROPPED")
            continue
        edges.setdefault(source, []).append(target)
        kept.append(relation)
    return kept


def _reaches(edges: dict[str, list[str]], start: str, goal: str) -> bool:
    if start == goal:
        return True
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return False


def _is_entity_side(temp_key: str, entities: dict[str, dict[str, Any]]) -> bool:
    if temp_key == "CASE_ROOT":
        return True
    entity = entities.get(temp_key)
    return entity is not None and entity.get("kind") == "ENTITY"


def _take(raw: dict[str, Any], allowed: tuple[str, ...]) -> tuple[dict[str, Any], bool]:
    allowed_set = set(allowed)
    cleaned = {key: value for key, value in raw.items() if key in allowed_set}
    return cleaned, any(key not in allowed_set for key in raw)


def _grounded(quote: str, page: str) -> bool:
    if not quote or len(quote) > _QUOTE_LIMIT:
        return False
    return quote in page


def _unit_interval(value: object) -> int | float | None:
    number = _plain_number(value)
    if number is None or number < 0 or number > 1:
        return None
    return _whole(number)


def _plain_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _whole(number: float) -> int | float:
    if number.is_integer():
        return int(number)
    return number


def _folded(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def _sensitive_key(key: str) -> bool:
    folded = _folded(key)
    if folded in {"ispephio", "sanctionsclear", "fatcastatus", "sanctions", "fatca", "crs"}:
        return True
    return "sanction" in folded or "fatca" in folded or folded == "crs"


def _forbidden_proposal_key(key: str) -> bool:
    if _folded(key) == "ispephio":
        return False
    return _sensitive_key(key)


def _clearance_text(value: str) -> bool:
    return bool(_PEP_CLAIM.search(value) or _WAIVED.search(value) or _FATCA_CLAIM.search(value))
