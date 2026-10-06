"""``python -m fcc_api.seed`` 与 ``python -m fcc_api.seed --reset``。"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace, UnionType
from typing import Union, get_args, get_origin, get_type_hints

from fcc_api.config import Settings
from fcc_api.seed.data import (
    RULE_VERSION,
    AuditRow,
    CaseRow,
    CatalogExpect,
    CATALOG,
    EXPECTED_COUNTS,
    EXPECTED_ROOTS,
    SeedBundle,
    build_seed,
    case_analyze_payload,
    validate_seed_shape,
)

_API_ROOT = Path(__file__).resolve().parents[3]
_COUNT_TABLES = tuple(EXPECTED_COUNTS)


class SeedCheckError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="写入 FCC 演示种子")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="DROP SCHEMA public CASCADE 后重新迁移并写入",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    if settings.app_env not in {"local", "test"}:
        print("APP_ENV must be local or test", file=sys.stderr)
        raise SystemExit(1)

    bundle = build_seed()
    shape_errors = validate_seed_shape(bundle)
    if shape_errors:
        _fail("种子数据形状不符合 seed.ts", shape_errors)

    if args.reset:
        _require_alembic_files()

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from fcc_api.db.session import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        print("连不上 PostgreSQL，已停止。", file=sys.stderr)
        print(str(exc).splitlines()[0], file=sys.stderr)
        raise SystemExit(1) from exc

    if args.reset:
        _reset_public_schema(engine)
        _alembic_upgrade()
        engine.dispose()

    catalog_note = ""
    try:
        with engine.begin() as connection:
            _insert_all(connection, bundle)
            errors = _verify_database(connection, bundle)
            catalog_errors, catalog_note = _catalog_parity(bundle)
            errors.extend(catalog_errors)
            if errors:
                raise SeedCheckError(errors)
    except SeedCheckError as exc:
        _fail("种子自检失败，已回滚", exc.errors)

    print(
        "种子已写入："
        f"users={EXPECTED_COUNTS['users']} "
        f"cases={EXPECTED_COUNTS['cases']} "
        f"parties={EXPECTED_COUNTS['parties']} "
        f"documents={EXPECTED_COUNTS['case_documents']} "
        f"checklist={EXPECTED_COUNTS['checklist_items']} "
        f"tasks={EXPECTED_COUNTS['review_tasks']} "
        f"audit={EXPECTED_COUNTS['audit_events']}"
    )
    print("自检通过：7 笔案件各有一个根节点，文件与清单 documentIds 一致。")
    print(catalog_note)


def _fail(title: str, errors: list[str]) -> None:
    print(f"{title}：", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)


def _require_alembic_files() -> None:
    ini = _API_ROOT / "alembic.ini"
    env = _API_ROOT / "migrations" / "env.py"
    if ini.is_file() and env.is_file():
        return
    print(
        "找不到 migrations/env.py 或 alembic.ini，无法 alembic upgrade head。",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _reset_public_schema(engine: object) -> None:
    from sqlalchemy import text

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:  # type: ignore[attr-defined]
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()  # type: ignore[attr-defined]


def _alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    previous = Path.cwd()
    os.chdir(_API_ROOT)
    try:
        command.upgrade(Config(str(_API_ROOT / "alembic.ini")), "head")
    finally:
        os.chdir(previous)


def _pg_array(values: tuple[str, ...] | list[str]) -> str:
    if not values:
        return "{}"
    return "{" + ",".join(values) + "}"


def _insert_all(connection: object, bundle: SeedBundle) -> None:
    from sqlalchemy import text

    execute = connection.execute  # type: ignore[attr-defined]
    present = execute(
        text("SELECT version FROM rule_versions WHERE version = :version"),
        {"version": RULE_VERSION},
    ).scalar_one_or_none()
    if present is None:
        raise SeedCheckError([
            "缺少内置规则版本 demo-2026-10-04，请先 alembic upgrade head",
        ])

    execute(
        text(
            """
            INSERT INTO users (id, name, email, role, team, active, created_at)
            VALUES (:id, :name, :email, :role, :team, true, :created_at)
            """
        ),
        [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "team": user.team,
                "created_at": bundle.now,
            }
            for user in bundle.users
        ],
    )
    execute(
        text(
            """
            INSERT INTO cases (
                id, reference, version, legal_name, entity_type, status, rule_version,
                owner_id, created_by, jurisdiction, registration_number, province,
                tax_residency, features, trusted_contact, trusted_contact_name,
                due_date, submitted_at, created_at, updated_at
            ) VALUES (
                :id, :reference, :version, :legal_name, :entity_type, :status, :rule_version,
                :owner_id, :created_by, :jurisdiction, :registration_number, :province,
                :tax_residency, CAST(:features AS text[]), :trusted_contact,
                :trusted_contact_name, :due_date, :submitted_at, :created_at, :updated_at
            )
            """
        ),
        [_case_params(row) for row in bundle.cases],
    )
    execute(
        text(
            """
            INSERT INTO parties (
                case_id, id, parent_id, position, kind, legal_name, entity_type,
                country, title, us_tax_class, ownership_percent, is_controller,
                is_signing_authority, is_us_person, is_pep_hio
            ) VALUES (
                :case_id, :id, :parent_id, :position, :kind, :legal_name, :entity_type,
                :country, :title, NULL, :ownership_percent, :is_controller,
                :is_signing_authority, :is_us_person, :is_pep_hio
            )
            """
        ),
        [
            {
                "case_id": row.case_id,
                "id": row.party.id,
                "parent_id": row.party.parent_id,
                "position": row.position,
                "kind": row.party.kind,
                "legal_name": row.party.legal_name,
                "entity_type": row.party.entity_type,
                "country": row.party.country,
                "title": row.party.title,
                "ownership_percent": row.party.ownership_percent,
                "is_controller": row.party.is_controller,
                "is_signing_authority": row.party.is_signing_authority,
                "is_us_person": row.party.is_us_person,
                "is_pep_hio": row.party.is_pep_hio,
            }
            for case in bundle.cases
            for row in case.parties
        ],
    )
    checklist_rows = [row for case in bundle.cases for row in case.checklist]
    if checklist_rows:
        execute(
            text(
                """
                INSERT INTO checklist_items (
                    case_id, requirement_id, status, updated_at, updated_by
                ) VALUES (
                    :case_id, :requirement_id, :status, :updated_at, :updated_by
                )
                """
            ),
            [
                {
                    "case_id": row.case_id,
                    "requirement_id": row.item.requirement_id,
                    "status": row.item.status,
                    "updated_at": row.updated_at,
                    "updated_by": row.item.updated_by,
                }
                for row in checklist_rows
            ],
        )
    documents = [row for case in bundle.cases for row in case.documents]
    if documents:
        execute(
            text(
                """
                INSERT INTO case_documents (
                    id, case_id, requirement_id, file_name, size_bytes, mime_type,
                    uploaded_by, uploaded_at, extraction, storage_state, scan_status,
                    object_key, sha256
                ) VALUES (
                    :id, :case_id, :requirement_id, :file_name, :size_bytes,
                    'application/pdf', :uploaded_by, :uploaded_at, 'EXTRACTED',
                    'METADATA_ONLY', 'NOT_APPLICABLE', NULL, NULL
                )
                """
            ),
            [
                {
                    "id": row.document.id,
                    "case_id": row.case_id,
                    "requirement_id": row.document.requirement_id,
                    "file_name": row.document.file_name,
                    "size_bytes": row.document.size_bytes,
                    "uploaded_by": row.document.uploaded_by,
                    "uploaded_at": row.uploaded_at,
                }
                for row in documents
            ],
        )
        execute(
            text(
                """
                INSERT INTO document_extractions (
                    id, document_id, status, adapter, model, page_count,
                    error_code, started_at, finished_at
                ) VALUES (
                    :id, :document_id, 'EXTRACTED', 'seed', NULL, 0,
                    NULL, :started_at, :finished_at
                )
                """
            ),
            [
                {
                    "id": row.extraction_id,
                    "document_id": row.document.id,
                    "started_at": row.uploaded_at,
                    "finished_at": row.uploaded_at,
                }
                for row in documents
            ],
        )
    tasks = [row for case in bundle.cases for row in case.tasks]
    if tasks:
        execute(
            text(
                """
                INSERT INTO review_tasks (
                    id, case_id, title, party_id, requirement_id, done, source,
                    created_by, created_at, done_by, done_at
                ) VALUES (
                    :id, :case_id, :title, :party_id, :requirement_id, false, :source,
                    :created_by, :created_at, NULL, NULL
                )
                """
            ),
            [
                {
                    "id": row.task.id,
                    "case_id": row.case_id,
                    "title": row.task.title,
                    "party_id": row.task.party_id,
                    "requirement_id": row.task.requirement_id,
                    "source": row.task.source,
                    "created_by": row.task.created_by,
                    "created_at": row.created_at,
                }
                for row in tasks
            ],
        )
    execute(
        text(
            """
            INSERT INTO case_reference_counters (year, last_value)
            VALUES (:year, :last_value)
            """
        ),
        {"year": 2026, "last_value": 144},
    )
    execute(
        text(
            """
            INSERT INTO audit_events (
                id, scope, case_id, actor_id, action, summary, at, version, changes,
                ai_model, ai_accepted, ai_rule_version, rule_version,
                before_value, after_value, correlation_id, request_id
            ) VALUES (
                :id, 'CASE', :case_id, :actor_id, :action, :summary, :at, :version,
                CAST(:changes AS jsonb), :ai_model, :ai_accepted, :ai_rule_version,
                :rule_version, NULL, NULL, 'seed', 'seed'
            )
            """
        ),
        [_audit_params(row) for row in bundle.audit_insert_order()],
    )


def _case_params(row: CaseRow) -> dict[str, object]:
    spec = row.case
    return {
        "id": spec.id,
        "reference": spec.reference,
        "version": spec.version,
        "legal_name": spec.legal_name,
        "entity_type": spec.entity_type,
        "status": spec.status,
        "rule_version": RULE_VERSION,
        "owner_id": spec.owner_id,
        "created_by": spec.owner_id,
        "jurisdiction": spec.jurisdiction,
        "registration_number": spec.registration_number,
        "province": spec.province,
        "tax_residency": spec.tax_residency,
        "features": _pg_array(spec.features),
        "trusted_contact": spec.trusted_contact,
        "trusted_contact_name": spec.trusted_contact_name,
        "due_date": row.due_date,
        "submitted_at": row.submitted_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _audit_params(row: AuditRow) -> dict[str, object]:
    event = row.event
    changes = None
    if event.changes:
        changes = json.dumps(
            [
                {"field": field, "from": source, "to": target}
                for field, source, target in event.changes
            ],
            ensure_ascii=False,
        )
    return {
        "id": event.id,
        "case_id": event.case_id,
        "actor_id": event.actor_id,
        "action": event.action,
        "summary": event.summary,
        "at": row.at,
        "version": event.version,
        "changes": changes,
        "ai_model": event.ai_model,
        "ai_accepted": event.ai_accepted,
        "ai_rule_version": event.ai_rule_version,
        "rule_version": RULE_VERSION,
    }


def _verify_database(connection: object, bundle: SeedBundle) -> list[str]:
    from sqlalchemy import text

    execute = connection.execute  # type: ignore[attr-defined]
    errors: list[str] = []
    for table in _COUNT_TABLES:
        count = int(execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
        if count != EXPECTED_COUNTS[table]:
            errors.append(f"{table} 行数 {count}，期望 {EXPECTED_COUNTS[table]}")

    roots = {
        case_id: party_id
        for case_id, party_id in execute(
            text(
                """
                SELECT case_id, id FROM parties
                WHERE parent_id IS NULL
                ORDER BY case_id
                """
            )
        )
    }
    if dict(roots) != EXPECTED_ROOTS:
        errors.append(f"根节点 {dict(roots)}，期望 {EXPECTED_ROOTS}")

    documents = list(
        execute(
            text(
                """
                SELECT id, requirement_id, storage_state, scan_status,
                       object_key, sha256, extraction, mime_type
                FROM case_documents
                ORDER BY id
                """
            )
        )
    )
    for row in documents:
        if row.storage_state != "METADATA_ONLY" or row.object_key is not None:
            errors.append(f"{row.id} 不是 METADATA_ONLY 或写了 object_key")
        if row.sha256 is not None or row.scan_status != "NOT_APPLICABLE":
            errors.append(f"{row.id} 的 sha256 或 scan_status 不符合种子")
        if row.extraction != "EXTRACTED" or row.mime_type != "application/pdf":
            errors.append(f"{row.id} 的 extraction 或 mime_type 不符合种子")
    from fcc_api.seed.data import EXPECTED_DOCUMENT_REQUIREMENTS

    actual = {row.id: row.requirement_id for row in documents}
    if actual != EXPECTED_DOCUMENT_REQUIREMENTS:
        errors.append("库中文件 requirement_id 与 seed.ts documentIds 不一致")

    extractions = list(
        execute(
            text(
                """
                SELECT document_id, status, adapter, page_count
                FROM document_extractions
                """
            )
        )
    )
    if any(row.status != "EXTRACTED" or row.adapter != "seed" or row.page_count != 0 for row in extractions):
        errors.append("document_extractions 不是 status=EXTRACTED、adapter=seed、page_count=0")

    audit_ids = [
        row.id
        for row in execute(text("SELECT id FROM audit_events ORDER BY seq DESC"))
    ]
    expected_ids = [row.event.id for row in bundle.audit]
    if audit_ids != expected_ids:
        errors.append(f"审计 seq 倒序 {audit_ids}，期望 {expected_ids}")

    counter = execute(
        text("SELECT year, last_value FROM case_reference_counters")
    ).one()
    if (counter.year, counter.last_value) != (2026, 144):
        errors.append(f"参考号计数器 {(counter.year, counter.last_value)}，期望 (2026, 144)")

    bytea = int(
        execute(
            text(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'public' AND data_type = 'bytea'
                """
            )
        ).scalar_one()
    )
    if bytea != 0:
        errors.append(f"public 中有 {bytea} 个 bytea 列")

    submitted = execute(
        text("SELECT submitted_at IS NOT NULL FROM cases WHERE id = 'case-0137'")
    ).scalar_one()
    if not submitted:
        errors.append("case-0137 的 submitted_at 应取审计 a-9 的 t(20)")
    others = int(
        execute(
            text(
                """
                SELECT count(*) FROM cases
                WHERE id <> 'case-0137' AND submitted_at IS NOT NULL
                """
            )
        ).scalar_one()
    )
    if others:
        errors.append("除 case-0137 外还有案件写了 submitted_at")
    return errors


def _catalog_parity(bundle: SeedBundle) -> tuple[list[str], str]:
    analyze, skip = _load_analyze()
    if analyze is None:
        return [], skip or "清单对拍未跑"
    errors: list[str] = []
    by_id = {case.case.id: case for case in bundle.cases}
    for index, expect in enumerate(CATALOG, start=1):
        label = f"S-{index:02d} {expect.case_id}"
        payload = case_analyze_payload(by_id[expect.case_id])
        try:
            result = _call_analyze(analyze, payload)
        except Exception as exc:
            errors.append(f"{label} analyze 失败：{exc}")
            continue
        errors.extend(_compare_catalog(label, expect, result))
    if errors:
        return errors, "清单对拍失败"
    return [], "清单对拍通过（S-01 到 S-07）"


def _load_analyze() -> tuple[object | None, str | None]:
    try:
        module = importlib.import_module("fcc_api.rules.insight")
    except ImportError as exc:
        return None, f"清单对拍未跑：规则包还不能导入（{exc}）"
    analyze = getattr(module, "analyze", None)
    if analyze is None:
        return None, "清单对拍未跑：fcc_api.rules.insight 没有 analyze"
    return analyze, None


def _call_analyze(analyze: object, payload: dict[str, object]) -> object:
    signature = inspect.signature(analyze)  # type: ignore[arg-type]
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    record = _build_record(analyze, positional[0].name if positional else "", payload)
    if len(positional) >= 2 and positional[1].default is inspect.Parameter.empty:
        library = _optional_value(analyze, positional[1].name)
        return analyze(record, library)  # type: ignore[operator]
    return analyze(record)  # type: ignore[operator]


def _build_record(analyze: object, name: str, payload: dict[str, object]) -> object:
    annotation = inspect.Parameter.empty
    try:
        annotation = get_type_hints(analyze).get(name, inspect.Parameter.empty)  # type: ignore[arg-type]
    except Exception:
        annotation = inspect.Parameter.empty
    if annotation is not inspect.Parameter.empty:
        try:
            return _coerce(annotation, payload)
        except Exception:
            pass
    return _flex(payload)


def _optional_value(analyze: object, name: str) -> object:
    try:
        annotation = get_type_hints(analyze).get(name)  # type: ignore[arg-type]
    except Exception:
        return None
    if annotation is None:
        return None
    inner, optional = _unwrap(annotation)
    if optional or inner is type(None):
        return None
    return None


def _flex(value: object) -> object:
    if isinstance(value, list):
        return [_flex(item) for item in value]
    if not isinstance(value, dict):
        return value
    if not _is_record(value):
        return {key: _flex(item) for key, item in value.items()}
    return SimpleNamespace(**{key: _flex(item) for key, item in value.items()})


def _is_record(value: dict[str, object]) -> bool:
    return all(str(key).isidentifier() for key in value)


def _coerce(annotation: object, value: object) -> object:
    inner, optional = _unwrap(annotation)
    if value is None:
        return None if optional else value
    origin = get_origin(inner)
    if origin in (list, tuple):
        item_type = get_args(inner)[0] if get_args(inner) else object
        items = [_coerce(item_type, item) for item in value]  # type: ignore[union-attr]
        return tuple(items) if origin is tuple else items
    if origin is dict:
        key_type, value_type = get_args(inner) if len(get_args(inner)) == 2 else (str, object)
        return {
            _coerce(key_type, key): _coerce(value_type, item)
            for key, item in value.items()  # type: ignore[union-attr]
        }
    if isinstance(inner, type) and is_dataclass(inner):
        return _fill_dataclass(inner, value)
    if isinstance(inner, type) and issubclass(inner, Enum):
        return inner(value)
    return value


def _fill_dataclass(cls: type, value: object) -> object:
    if not isinstance(value, dict):
        return value
    hints = get_type_hints(cls)
    kwargs: dict[str, object] = {}
    for field in fields(cls):
        raw = _lookup(value, field.name)
        hint = hints.get(field.name, field.type)
        if raw is _MISSING:
            _, optional = _unwrap(hint)
            if optional:
                kwargs[field.name] = None
            continue
        kwargs[field.name] = _coerce(hint, raw)
    return cls(**kwargs)


_MISSING = object()


def _lookup(value: dict[str, object], name: str) -> object:
    if name in value:
        return value[name]
    camel = _snake_to_camel(name)
    if camel in value:
        return value[camel]
    snake = _camel_to_snake(name)
    if snake in value:
        return value[snake]
    return _MISSING


def _unwrap(annotation: object) -> tuple[object, bool]:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _snake_to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _camel_to_snake(name: str) -> str:
    chars: list[str] = []
    for char in name:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars)


def _compare_catalog(label: str, expect: CatalogExpect, result: object) -> list[str]:
    errors: list[str] = []
    issues = _issues(result)
    expected_issues = [
        (item.code, item.party_id, item.message) for item in expect.ownership_issues
    ]
    if len(issues) != len(expected_issues):
        errors.append(f"{label} ownershipIssues {issues}，期望 {expected_issues}")
    else:
        for actual, wanted in zip(issues, expected_issues, strict=True):
            if actual[0] != wanted[0] or actual[1] != wanted[1]:
                errors.append(f"{label} ownershipIssues {actual}，期望 {wanted}")
            elif wanted[2] is not None and actual[2] != wanted[2]:
                errors.append(f"{label} ownership 文案 {actual[2]!r}，期望 {wanted[2]!r}")
    fields_found = _detail_fields(result)
    if tuple(fields_found) != expect.detail_fields:
        errors.append(f"{label} detailsGaps {fields_found}，期望 {list(expect.detail_fields)}")
    checklist = list(_get(result, "checklist") or [])
    ids = [_text(_get(item, "id")) for item in checklist]
    if tuple(ids) != expect.checklist_ids:
        errors.append(f"{label} 清单 {ids}，期望 {list(expect.checklist_ids)}")
    by_requirement = {_text(_get(item, "id")): item for item in checklist}
    for requirement_id, party_ids in expect.party_ids:
        found = _party_ids(by_requirement.get(requirement_id))
        if tuple(found) != party_ids:
            errors.append(f"{label} {requirement_id}.partyIds {found}，期望 {list(party_ids)}")
    collected = _get(result, "collected")
    if collected != expect.collected:
        errors.append(f"{label} collected {collected}，期望 {expect.collected}")
    stage = _text(_get(result, "stage"))
    if stage != expect.stage:
        errors.append(f"{label} stage {stage}，期望 {expect.stage}")
    blocker = _get(result, "blocker")
    blocker_text = None if blocker is None else _text(blocker)
    if blocker_text != expect.blocker:
        errors.append(f"{label} blocker {blocker_text!r}，期望 {expect.blocker!r}")
    identify = _identify(result)
    if tuple(identify) != expect.identify:
        errors.append(f"{label} identify {identify}，期望 {list(expect.identify)}")
    effective = _effective(result)
    for party_id, wanted in expect.effective:
        actual = effective.get(party_id)
        if actual is None or abs(actual - wanted) > 0.011:
            errors.append(f"{label} effective {party_id}={actual}，期望 {wanted}")
    return errors


def _issues(result: object) -> list[tuple[str, str | None, str | None]]:
    parsed: list[tuple[str, str | None, str | None]] = []
    for item in _get(result, "ownership_issues", "ownershipIssues") or []:
        party = _get(item, "party_id", "partyId")
        message = _get(item, "message")
        parsed.append(
            (
                _text(_get(item, "code")),
                None if party is None else _text(party),
                None if message is None else _text(message),
            )
        )
    return parsed


def _detail_fields(result: object) -> list[str]:
    names = {
        "tax_residency": "taxResidency",
        "trusted_contact": "trustedContact",
        "trusted_contact_name": "trustedContactName",
    }
    found = []
    for item in _get(result, "details_gaps", "detailsGaps") or []:
        field = _text(_get(item, "field"))
        found.append(names.get(field, field))
    return found


def _party_ids(item: object) -> list[str]:
    raw = _get(item, "party_ids", "partyIds") or []
    return [_text(value) if isinstance(value, str) else _text(_get(value, "id")) for value in raw]


def _identify(result: object) -> list[str]:
    found = []
    for item in _get(result, "identify") or []:
        found.append(item if isinstance(item, str) else _text(_get(item, "id")))
    return found


def _effective(result: object) -> dict[str, float]:
    raw = _get(result, "effective") or {}
    if hasattr(raw, "items"):
        return {str(key): float(value) for key, value in raw.items()}
    return {}


def _get(obj: object, *names: str) -> object:
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return None
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    nested = getattr(value, "value", None)
    if isinstance(nested, str):
        return nested
    return "" if value is None else str(value)


if __name__ == "__main__":
    main()
