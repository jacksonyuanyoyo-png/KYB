"""BUILTIN ids and labels match the rules page."""

import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fcc_api.rules.builtin_catalog import BUILTIN
from fcc_api.rules.domain import generate_requirements
from fcc_api.rules.types import AccountCase, AccountProfile, Party

_PAGE = (
    Path(__file__).resolve().parents[4]
    / "apps"
    / "web"
    / "app"
    / "(workspace)"
    / "rules"
    / "page.tsx"
)
_RULE = re.compile(
    r'\{\s*id:\s*"(?P<id>[^"]+)",\s*name:\s*"(?P<name>[^"]+)",\s*section:\s*"(?P<section>[^"]+)",\s*'
    r'trigger:\s*"(?P<trigger>[^"]+)",\s*source:\s*"(?P<source>[^"]+)",\s*conditional:\s*(?P<conditional>true|false)\s*\}'
)


def test_builtin_catalog_matches_rules_page() -> None:
    text = _PAGE.read_text()
    start = text.index("const BUILTIN")
    end = text.index("];", start)
    found = list(_RULE.finditer(text[start:end]))
    assert len(found) == 16
    assert len(BUILTIN) == 16
    for rule, match in zip(BUILTIN, found, strict=True):
        assert rule.id == match.group("id")
        assert rule.name == match.group("name")
        assert rule.section == match.group("section")
        assert rule.trigger == match.group("trigger")
        assert rule.source == match.group("source")
        assert rule.conditional is (match.group("conditional") == "true")


def test_engine_can_emit_every_builtin_id() -> None:
    account = AccountCase(
        entity_type="corporation",
        profile=AccountProfile(
            province="ON",
            tax_residency="INTERNATIONAL",
            features=["MARGIN", "OPTIONS", "COD_DVP", "FPL"],
            trusted_contact=True,
        ),
        parties=[
            Party(
                id="root", parent_id=None, kind="ENTITY", legal_name="Root", ownership_percent=100
            ),
            Party(
                id="alice",
                parent_id="root",
                kind="PERSON",
                legal_name="Alice",
                ownership_percent=100,
                is_controller=True,
                is_signing_authority=True,
                is_us_person=True,
                is_pep_hio=True,
            ),
        ],
    )
    assert [item.id for item in generate_requirements(account)] == [
        "naaf",
        "formation",
        "resolution",
        "beneficial-owner",
        "directors",
        "identity",
        "pep",
        "margin",
        "options",
        "cod-dvp",
        "fpl",
        "tcp",
        "w9",
        "w8",
        "rc519",
        "nffe",
    ]
    assert {rule.id for rule in BUILTIN} == {item.id for item in generate_requirements(account)}


def test_rules_package_has_no_forbidden_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "fcc_api" / "rules"
    for path in root.glob("*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            assert not stripped.startswith("from ."), f"{path}:{line_number}"
            assert not stripped.startswith("import ."), f"{path}:{line_number}"
            assert "fastapi" not in stripped, f"{path}:{line_number}"
            assert "sqlalchemy" not in stripped, f"{path}:{line_number}"
            assert "fcc_api.db" not in stripped, f"{path}:{line_number}"
