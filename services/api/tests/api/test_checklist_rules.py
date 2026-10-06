"""清单随税务身份、实体类型和股权标志变化。T-CHK-01 到 T-CHK-10。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


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


def _ids(response_json: dict) -> list[str]:
    return [item["id"] for item in response_json["insight"]["checklist"]]


def _profile(db_client, headers: dict, case_id: str, version: int, profile: dict, change: dict):
    return db_client.put(
        f"/api/v1/cases/{case_id}/profile",
        headers=headers,
        json={"version": version, "profile": profile, "change": change},
    )


def test_t_chk_01_mixed_to_canada_drops_w8_and_rc519(db_client, db_session, role_headers) -> None:
    response = _profile(
        db_client,
        role_headers("ADVISOR"),
        "case-0139",
        9,
        {
            "province": "ON",
            "taxResidency": "CANADA",
            "features": ["MARGIN", "OPTIONS"],
            "trustedContact": True,
            "trustedContactName": "Helen Chen",
        },
        {"field": "Tax residency", "from": "Mixed", "to": "Canada"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["case"]["version"] == 10
    assert payload["case"]["profile"]["taxResidency"] == "CANADA"
    ids = _ids(payload)
    assert len(ids) == 11
    assert "w8" not in ids
    assert "rc519" not in ids
    assert "w9" in ids
    assert payload["insight"]["collected"] == 4
    still = db_session.execute(
        text(
            """
            SELECT status FROM checklist_items
            WHERE case_id = 'case-0139' AND requirement_id = 'rc519'
            """
        )
    ).scalar_one()
    assert still == "REQUESTED"
    assert "rc519" not in ids


def test_t_chk_02_us_residency(db_client, role_headers) -> None:
    response = _profile(
        db_client,
        role_headers("ADVISOR"),
        "case-0139",
        9,
        {
            "province": "ON",
            "taxResidency": "US",
            "features": ["MARGIN", "OPTIONS"],
            "trustedContact": True,
            "trustedContactName": "Helen Chen",
        },
        {"field": "Tax residency", "from": "Mixed", "to": "United States"},
    )
    assert response.status_code == 200, response.text
    ids = _ids(response.json())
    assert "w9" in ids
    assert "rc519" in ids
    assert "w8" not in ids
    assert "nffe" not in ids


def test_t_chk_03_international_nffe_parties(db_client, role_headers) -> None:
    response = _profile(
        db_client,
        role_headers("ADVISOR"),
        "case-0139",
        9,
        {
            "province": "ON",
            "taxResidency": "INTERNATIONAL",
            "features": ["MARGIN", "OPTIONS"],
            "trustedContact": True,
            "trustedContactName": "Helen Chen",
        },
        {"field": "Tax residency", "from": "Mixed", "to": "International"},
    )
    assert response.status_code == 200, response.text
    rows = {item["id"]: item for item in response.json()["insight"]["checklist"]}
    assert "w8" in rows
    assert "rc519" in rows
    assert "nffe" in rows
    assert rows["nffe"]["partyIds"] == ["mr-alice", "mr-david"]


def test_t_chk_04_corporation_and_trust_checklists(db_client, role_headers) -> None:
    headers = role_headers("ADVISOR")
    corporation = db_client.post(
        "/api/v1/cases",
        headers=headers,
        json={
            "legalName": "Checklist Corporation",
            "entityType": "corporation",
            "jurisdiction": "Ontario",
            "registrationNumber": "",
            "ownerId": "u-advisor",
        },
    )
    trust = db_client.post(
        "/api/v1/cases",
        headers=headers,
        json={
            "legalName": "Checklist Trust",
            "entityType": "trust",
            "jurisdiction": "Ontario",
            "registrationNumber": "",
            "ownerId": "u-advisor",
        },
    )
    assert corporation.status_code == 201, corporation.text
    assert trust.status_code == 201, trust.text
    assert corporation.headers["location"].endswith(f"/api/v1/cases/{corporation.json()['case']['id']}")
    assert _ids(corporation.json()) == [
        "naaf",
        "formation",
        "resolution",
        "beneficial-owner",
        "directors",
    ]
    assert _ids(trust.json()) == ["naaf", "formation", "beneficial-owner"]


def test_t_chk_05_features_and_trusted_contact(db_client, role_headers) -> None:
    headers = role_headers("ADVISOR")
    filled = _profile(
        db_client,
        headers,
        "case-0142",
        4,
        {
            "province": "ON",
            "taxResidency": "CANADA",
            "features": [],
            "trustedContact": False,
            "trustedContactName": "",
        },
        {"field": "Province", "from": "", "to": "Ontario"},
    )
    assert filled.status_code == 200, filled.text
    version = filled.json()["case"]["version"]
    response = _profile(
        db_client,
        headers,
        "case-0142",
        version,
        {
            "province": "ON",
            "taxResidency": "CANADA",
            "features": ["OPTIONS", "FPL"],
            "trustedContact": True,
            "trustedContactName": "A B",
        },
        {"field": "Account features", "from": "", "to": "Options, FPL"},
    )
    assert response.status_code == 200, response.text
    ids = _ids(response.json())
    assert "options" in ids
    assert "fpl" in ids
    assert "tcp" in ids


def test_t_chk_06_pep_adds_requirement(db_client, db_session, role_headers) -> None:
    parties = _parties(db_session, "case-0142")
    for item in parties:
        if item["id"] == "nw-omar":
            item["isPepHio"] = True
    response = db_client.put(
        "/api/v1/cases/case-0142/parties",
        headers=role_headers("ADVISOR"),
        json={"version": 4, "parties": parties, "summary": "Flagged Omar as PEP"},
    )
    assert response.status_code == 200, response.text
    rows = {item["id"]: item for item in response.json()["insight"]["checklist"]}
    assert rows["pep"]["partyIds"] == ["nw-omar"]


def test_t_chk_07_us_person_adds_w9(db_client, db_session, role_headers) -> None:
    parties = _parties(db_session, "case-0142")
    for item in parties:
        if item["id"] == "nw-grace":
            item["isUsPerson"] = True
    response = db_client.put(
        "/api/v1/cases/case-0142/parties",
        headers=role_headers("ADVISOR"),
        json={"version": 4, "parties": parties, "summary": "Flagged Grace as a US person"},
    )
    assert response.status_code == 200, response.text
    rows = {item["id"]: item for item in response.json()["insight"]["checklist"]}
    assert rows["w9"]["partyIds"] == ["nw-grace"]
    assert response.json()["case"]["profile"]["taxResidency"] == "CANADA"


def test_t_chk_08_custom_requirement_appended(db_client, role_headers) -> None:
    response = db_client.post(
        "/api/v1/cases/case-0139/custom-requirements",
        headers=role_headers("ADVISOR"),
        json={"version": 9, "name": "Certified translation"},
    )
    assert response.status_code == 201, response.text
    checklist = response.json()["insight"]["checklist"]
    custom = checklist[-1]
    assert custom["name"] == "Certified translation"
    assert custom["section"] == "Additional Requirements"
    assert custom["custom"] is True
    assert custom["conditional"] is True


def test_t_chk_09_verified_item_stays_verified_after_upload(db_client, role_headers) -> None:
    headers = role_headers("OPERATIONS")
    pdf = b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    opened = db_client.post(
        "/api/v1/cases/case-0139/uploads",
        headers=headers,
        json={
            "requirementId": "naaf",
            "files": [{
                "fileName": "NAAF_extra.pdf",
                "sizeBytes": len(pdf),
                "mimeType": "application/pdf",
            }],
        },
    )
    assert opened.status_code == 201, opened.text
    slot = opened.json()["slots"][0]
    stored = db_client.put(slot["url"], headers=slot["headers"], content=pdf)
    assert stored.status_code in (200, 204), stored.text
    finished = db_client.post(
        "/api/v1/cases/case-0139/documents",
        headers=headers,
        json={"version": 9, "batchId": opened.json()["batchId"], "uploadIds": [slot["uploadId"]]},
    )
    assert finished.status_code == 201, finished.text
    naaf = finished.json()["case"]["checklist"]["naaf"]
    assert naaf["status"] == "VERIFIED"
    assert naaf["documentIds"][0] == "d-1"
    assert len(naaf["documentIds"]) == 2


def test_t_chk_10_assign_document_marks_received(db_client, db_session, role_headers) -> None:
    response = db_client.put(
        "/api/v1/cases/case-0139/documents/d-3/requirement",
        headers=role_headers("ADVISOR"),
        json={"version": 9, "requirementId": "beneficial-owner"},
    )
    assert response.status_code == 200, response.text
    requirement = db_session.execute(
        text("SELECT requirement_id FROM case_documents WHERE id = 'd-3'")
    ).scalar_one()
    status = db_session.execute(
        text(
            """
            SELECT status FROM checklist_items
            WHERE case_id = 'case-0139' AND requirement_id = 'beneficial-owner'
            """
        )
    ).scalar_one()
    assert requirement == "beneficial-owner"
    assert status == "RECEIVED"
    summary = db_session.execute(
        text(
            """
            SELECT summary FROM audit_events
            WHERE case_id = 'case-0139' AND action = 'CHECKLIST_UPDATED'
            ORDER BY seq DESC LIMIT 1
            """
        )
    ).scalar_one()
    assert summary == "Linked Shareholder_Register_2026.pdf to Beneficial Owner Identification"
