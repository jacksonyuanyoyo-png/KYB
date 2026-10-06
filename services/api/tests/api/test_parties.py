"""股权树用例 T-TREE-01 到 T-TREE-09。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _one(db: Session, sql: str, **params: object) -> dict:
    return dict(db.execute(text(sql), params).mappings().one())


def _case(db: Session, case_id: str) -> dict:
    return _one(
        db,
        "SELECT version, status, submitted_at, updated_at FROM cases WHERE id = :id",
        id=case_id,
    )


def _audits(db: Session, case_id: str) -> int:
    return int(db.execute(text("SELECT count(*) FROM audit_events WHERE case_id = :id"), {"id": case_id}).scalar_one())


def _parties(db: Session, case_id: str) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT id, parent_id, kind, legal_name, entity_type, country, title,
                   us_tax_class, ownership_percent, is_controller, is_signing_authority,
                   is_us_person, is_pep_hio
            FROM parties WHERE case_id = :id ORDER BY position
            """
        ),
        {"id": case_id},
    ).mappings()
    payload = []
    for row in rows:
        item = {
            "id": row["id"],
            "parentId": row["parent_id"],
            "kind": row["kind"],
            "legalName": row["legal_name"],
            "ownershipPercent": float(row["ownership_percent"]),
            "isController": row["is_controller"],
            "isSigningAuthority": row["is_signing_authority"],
            "isUsPerson": row["is_us_person"],
            "isPepHio": row["is_pep_hio"],
        }
        if row["entity_type"] is not None:
            item["entityType"] = row["entity_type"]
        if row["country"] is not None:
            item["country"] = row["country"]
        if row["title"] is not None:
            item["title"] = row["title"]
        if row["us_tax_class"] is not None:
            item["usTaxClass"] = row["us_tax_class"]
        payload.append(item)
    return payload


def _ctids(db: Session, case_id: str) -> dict[str, str]:
    rows = db.execute(
        text("SELECT id, ctid::text AS ctid FROM parties WHERE case_id = :id"),
        {"id": case_id},
    ).mappings()
    return {row["id"]: row["ctid"] for row in rows}


def test_t_tree_01_two_roots_rejected(db_client, db_session, role_headers) -> None:
    parties = _parties(db_session, "case-0139")
    parties.append({
        "id": "mr-second",
        "parentId": None,
        "kind": "ENTITY",
        "legalName": "Second Root",
        "entityType": "corporation",
        "ownershipPercent": 100,
        "isController": False,
        "isSigningAuthority": False,
        "isUsPerson": False,
        "isPepHio": False,
    })
    before = _case(db_session, "case-0139")
    audits = _audits(db_session, "case-0139")
    count = db_session.execute(
        text("SELECT count(*) FROM parties WHERE case_id = 'case-0139'")
    ).scalar_one()
    response = db_client.put(
        "/api/v1/cases/case-0139/parties",
        headers=role_headers("ADVISOR"),
        json={"version": 9, "parties": parties, "summary": "Added a second root"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert _case(db_session, "case-0139")["version"] == before["version"]
    assert _case(db_session, "case-0139")["updated_at"] == before["updated_at"]
    assert _audits(db_session, "case-0139") == audits
    assert db_session.execute(
        text("SELECT count(*) FROM parties WHERE case_id = 'case-0139'")
    ).scalar_one() == count


def test_t_tree_02_root_cannot_be_removed(db_client, db_session, role_headers) -> None:
    parties = [item for item in _parties(db_session, "case-0139") if item["id"] != "mr-root"]
    parties[0]["parentId"] = None
    parties[0]["kind"] = "ENTITY"
    parties[0]["entityType"] = "corporation"
    audits = _audits(db_session, "case-0139")
    response = db_client.put(
        "/api/v1/cases/case-0139/parties",
        headers=role_headers("ADVISOR"),
        json={"version": 9, "parties": parties, "summary": "Removed the root"},
    )
    assert response.status_code == 400
    assert _case(db_session, "case-0139")["version"] == 9
    assert _audits(db_session, "case-0139") == audits


def test_t_tree_03_cycle_rejected(db_client, db_session, role_headers) -> None:
    parties = _parties(db_session, "case-0142")
    by_id = {item["id"]: item for item in parties}
    by_id["nw-grace"]["kind"] = "ENTITY"
    by_id["nw-grace"]["entityType"] = "corporation"
    by_id["nw-grace"]["parentId"] = "nw-omar"
    by_id["nw-omar"]["kind"] = "ENTITY"
    by_id["nw-omar"]["entityType"] = "corporation"
    by_id["nw-omar"]["parentId"] = "nw-grace"
    audits = _audits(db_session, "case-0142")
    response = db_client.put(
        "/api/v1/cases/case-0142/parties",
        headers=role_headers("ADVISOR"),
        json={"version": 4, "parties": parties, "summary": "Created a cycle"},
    )
    assert response.status_code == 400
    assert _case(db_session, "case-0142")["version"] == 4
    assert _audits(db_session, "case-0142") == audits


def test_t_tree_04_person_cannot_own_a_child(db_client, db_session, role_headers) -> None:
    parties = _parties(db_session, "case-0142")
    parties.append({
        "id": "nw-child",
        "parentId": "nw-grace",
        "kind": "PERSON",
        "legalName": "Nested Person",
        "country": "Canada",
        "ownershipPercent": 0,
        "isController": False,
        "isSigningAuthority": False,
        "isUsPerson": False,
        "isPepHio": False,
    })
    audits = _audits(db_session, "case-0142")
    response = db_client.put(
        "/api/v1/cases/case-0142/parties",
        headers=role_headers("ADVISOR"),
        json={"version": 4, "parties": parties, "summary": "Nested a person"},
    )
    assert response.status_code == 400
    assert _case(db_session, "case-0142")["version"] == 4
    assert _audits(db_session, "case-0142") == audits


def test_t_tree_05_second_root_violates_unique_index(db_session) -> None:
    nested = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO parties (
                  case_id, id, parent_id, position, kind, legal_name, entity_type,
                  ownership_percent, is_controller, is_signing_authority,
                  is_us_person, is_pep_hio
                ) VALUES (
                  'case-0139', 'mr-extra-root', NULL, 99, 'ENTITY', 'Extra',
                  'corporation', 100, false, false, false, false
                )
                """
            )
        )
        db_session.flush()
    nested.rollback()


def test_t_tree_06_deleted_party_nulls_task(db_client, db_session, role_headers) -> None:
    parties = [item for item in _parties(db_session, "case-0128") if item["id"] != "pr-gp"]
    response = db_client.put(
        "/api/v1/cases/case-0128/parties",
        headers=role_headers("ADVISOR", "u-advisor-2"),
        json={"version": 17, "parties": parties, "summary": "Removed the general partner"},
    )
    assert response.status_code == 200, response.text
    task_1 = db_session.execute(
        text("SELECT party_id FROM review_tasks WHERE id = 'task-1'")
    ).scalar_one()
    task_2 = db_session.execute(
        text("SELECT party_id FROM review_tasks WHERE id = 'task-2'")
    ).scalar_one()
    assert task_1 is None
    assert task_2 == "pr-root"


def test_t_tree_07_incomplete_ownership_can_be_saved(db_client, db_session, role_headers) -> None:
    parties = _parties(db_session, "case-0139")
    for item in parties:
        if item["id"] == "mr-david":
            item["ownershipPercent"] = 5
    response = db_client.put(
        "/api/v1/cases/case-0139/parties",
        headers=role_headers("ADVISOR"),
        json={"version": 9, "parties": parties, "summary": "Reduced David to 5 percent"},
    )
    assert response.status_code == 200, response.text
    codes = [item["code"] for item in response.json()["insight"]["ownershipIssues"]]
    assert "OWNERSHIP_TOTAL" in codes


def test_t_tree_08_title_change_does_not_rebuild_rows(db_client, db_session, role_headers) -> None:
    before = _ctids(db_session, "case-0128")
    parties = _parties(db_session, "case-0128")
    for item in parties:
        if item["id"] == "pr-wei":
            item["title"] = "Limited Partner, updated"
    response = db_client.put(
        "/api/v1/cases/case-0128/parties",
        headers=role_headers("ADVISOR", "u-advisor-2"),
        json={"version": 17, "parties": parties, "summary": "Updated Wei's title"},
    )
    assert response.status_code == 200, response.text
    after = _ctids(db_session, "case-0128")
    for party_id, ctid in before.items():
        if party_id != "pr-wei":
            assert after[party_id] == ctid
    assert db_session.execute(
        text("SELECT party_id FROM review_tasks WHERE id = 'task-2'")
    ).scalar_one() == "pr-root"
    assert db_session.execute(
        text("SELECT party_id FROM review_tasks WHERE id = 'task-1'")
    ).scalar_one() == "pr-gp"


def test_t_tree_09_position_follows_request_order(db_client, db_session, role_headers) -> None:
    parties = _parties(db_session, "case-0142")
    root = parties[0]
    people = list(reversed(parties[1:]))
    response = db_client.put(
        "/api/v1/cases/case-0142/parties",
        headers=role_headers("ADVISOR"),
        json={
            "version": 4,
            "parties": [root, *people],
            "summary": "Reordered the directors",
        },
    )
    assert response.status_code == 200, response.text
    positions = db_session.execute(
        text("SELECT id FROM parties WHERE case_id = 'case-0142' ORDER BY position")
    ).scalars()
    assert list(positions) == ["nw-root", "nw-lena", "nw-omar", "nw-grace"]
    assert response.json()["insight"]["identify"] == ["nw-lena", "nw-omar", "nw-grace"]
