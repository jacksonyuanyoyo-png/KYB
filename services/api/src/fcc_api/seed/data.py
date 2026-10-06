"""从 ``apps/web/lib/data/seed.ts`` 移植的演示数据。

时间以执行时刻为 now。``t(h) = now - h 小时``，``DAY = 24 小时``。
文件只保留元数据，不带字节。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

RULE_VERSION = "demo-2026-10-04"
COUNTER_YEAR = 2026
COUNTER_LAST_VALUE = 144

# 02 第 6.4 节写「共 25 行」，分项加总与 seed.ts 都是 24。以 seed.ts 为准。
EXPECTED_COUNTS: dict[str, int] = {
    "users": 5,
    "cases": 7,
    "parties": 24,
    "case_documents": 17,
    "document_extractions": 17,
    "extraction_pages": 0,
    "extraction_fields": 0,
    "checklist_items": 23,
    "review_tasks": 3,
    "custom_requirements": 0,
    "audit_events": 14,
    "rule_versions": 1,
    "rule_library_state": 1,
    "rule_drafts": 0,
    "upload_slots": 0,
    "case_reference_counters": 1,
}

EXPECTED_ROOTS: dict[str, str] = {
    "case-0139": "mr-root",
    "case-0142": "nw-root",
    "case-0137": "tf-root",
    "case-0131": "lc-root",
    "case-0128": "pr-root",
    "case-0119": "er-root",
    "case-0144": "od-root",
}

# 与 seed.ts 清单 documentIds 对照，独立于下面的构造函数。
EXPECTED_DOCUMENT_REQUIREMENTS: dict[str, str | None] = {
    "d-1": "naaf",
    "d-2": "formation",
    "d-3": None,
    "d-4": "resolution",
    "d-5": "margin",
    "t-1": "naaf",
    "t-2": "formation",
    "t-3": "beneficial-owner",
    "t-4": "identity",
    "t-5": "cod-dvp",
    "p-1": "naaf",
    "p-2": "formation",
    "p-3": "w8",
    "e-1": "naaf",
    "e-2": "formation",
    "e-3": "resolution",
    "e-4": "identity",
}


@dataclass(frozen=True)
class IssueExpect:
    code: str
    party_id: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class CatalogExpect:
    """docs/backend/10-test-catalog.md 第 2 节。"""

    case_id: str
    ownership_issues: tuple[IssueExpect, ...]
    detail_fields: tuple[str, ...]
    checklist_ids: tuple[str, ...]
    collected: int
    stage: str
    blocker: str | None
    identify: tuple[str, ...]
    party_ids: tuple[tuple[str, tuple[str, ...]], ...] = ()
    effective: tuple[tuple[str, float], ...] = ()


CATALOG: tuple[CatalogExpect, ...] = (
    CatalogExpect(
        "case-0139",
        (),
        (),
        (
            "naaf", "formation", "resolution", "beneficial-owner", "directors",
            "identity", "pep", "margin", "options", "tcp", "w9", "w8", "rc519",
        ),
        4,
        "DOCUMENTS",
        "9 documents outstanding",
        ("mr-alice", "mr-david"),
        (
            ("resolution", ("mr-alice",)),
            ("beneficial-owner", ("mr-alice", "mr-david")),
            ("identity", ("mr-alice", "mr-david")),
            ("pep", ("mr-david",)),
            ("w9", ("mr-mei",)),
        ),
        (("mr-raj", 24.5), ("mr-mei", 10.5), ("mr-david", 25.0)),
    ),
    CatalogExpect(
        "case-0142",
        (),
        ("province", "trustedContact"),
        ("naaf", "formation", "resolution", "directors", "identity"),
        0,
        "DETAILS",
        "Select the province or territory of registration.",
        ("nw-grace", "nw-omar", "nw-lena"),
    ),
    CatalogExpect(
        "case-0137",
        (),
        (),
        ("naaf", "formation", "beneficial-owner", "identity", "cod-dvp"),
        5,
        "COMPLIANCE",
        None,
        ("tf-robert", "tf-emily", "tf-james"),
    ),
    CatalogExpect(
        "case-0131",
        (IssueExpect("MISSING_OWNER", "lc-root"),),
        ("province", "taxResidency", "trustedContact"),
        ("naaf", "formation", "resolution", "directors"),
        0,
        "OWNERSHIP",
        "Lakeshore Condominium Corporation No. 482 must disclose an owner or controller.",
        (),
    ),
    CatalogExpect(
        "case-0128",
        (
            IssueExpect(
                "OWNERSHIP_TOTAL",
                "pr-root",
                "Pacific Rim Ventures LP ownership totals 91%.",
            ),
            IssueExpect("ENTITY_LEAF", "pr-gp"),
        ),
        (),
        (
            "naaf", "formation", "beneficial-owner", "identity",
            "fpl", "w8", "rc519", "nffe",
        ),
        2,
        "OWNERSHIP",
        "3 compliance tasks open",
        ("pr-wei", "pr-sophie"),
        (("nffe", ("pr-wei", "pr-sophie")),),
    ),
    CatalogExpect(
        "case-0119",
        (),
        (),
        ("naaf", "formation", "resolution", "identity"),
        4,
        "DONE",
        None,
        ("er-chief", "er-council"),
    ),
    CatalogExpect(
        "case-0144",
        (IssueExpect("ENTITY_LEAF", "od-sponsor"),),
        ("taxResidency", "trustedContact"),
        ("naaf", "formation", "beneficial-owner"),
        0,
        "OWNERSHIP",
        "Okafor Dental Professional Corporation must disclose an owner or controller.",
        (),
    ),
)


@dataclass(frozen=True)
class UserSeed:
    id: str
    name: str
    email: str
    role: str
    team: str


@dataclass(frozen=True)
class PartySeed:
    id: str
    parent_id: str | None
    kind: str
    legal_name: str
    entity_type: str | None = None
    country: str | None = None
    title: str | None = None
    ownership_percent: Decimal = Decimal("0")
    is_controller: bool = False
    is_signing_authority: bool = False
    is_us_person: bool = False
    is_pep_hio: bool = False


@dataclass(frozen=True)
class DocumentSeed:
    id: str
    requirement_id: str | None
    file_name: str
    hours_ago: float
    uploaded_by: str = "u-advisor"
    size_bytes: int = 420_000


@dataclass(frozen=True)
class ChecklistSeed:
    requirement_id: str
    status: str
    document_ids: tuple[str, ...]
    hours_ago: float
    updated_by: str


@dataclass(frozen=True)
class TaskSeed:
    id: str
    title: str
    hours_ago: float
    party_id: str | None = None
    requirement_id: str | None = None
    source: str = "COMPLIANCE"
    created_by: str = "u-compliance"


@dataclass(frozen=True)
class AuditSeed:
    id: str
    case_id: str
    actor_id: str
    action: str
    summary: str
    hours_ago: float
    version: int
    changes: tuple[tuple[str, str, str], ...] = ()
    ai_model: str | None = None
    ai_accepted: bool | None = None
    ai_rule_version: str | None = None


@dataclass(frozen=True)
class CaseSeed:
    id: str
    reference: str
    legal_name: str
    entity_type: str
    status: str
    owner_id: str
    jurisdiction: str
    registration_number: str
    created_days_ago: float
    updated_hours_ago: float
    due_in_days: float
    version: int
    parties: tuple[PartySeed, ...]
    province: str = ""
    tax_residency: str | None = None
    features: tuple[str, ...] = ()
    trusted_contact: bool | None = None
    trusted_contact_name: str = ""
    documents: tuple[DocumentSeed, ...] = ()
    checklist: tuple[ChecklistSeed, ...] = ()
    tasks: tuple[TaskSeed, ...] = ()
    submitted_hours_ago: float | None = None


@dataclass(frozen=True)
class PartyRow:
    case_id: str
    position: int
    party: PartySeed


@dataclass(frozen=True)
class DocumentRow:
    case_id: str
    document: DocumentSeed
    uploaded_at: datetime
    extraction_id: str


@dataclass(frozen=True)
class ChecklistRow:
    case_id: str
    item: ChecklistSeed
    updated_at: datetime


@dataclass(frozen=True)
class TaskRow:
    case_id: str
    task: TaskSeed
    created_at: datetime


@dataclass(frozen=True)
class AuditRow:
    event: AuditSeed
    at: datetime


@dataclass(frozen=True)
class CaseRow:
    case: CaseSeed
    created_at: datetime
    updated_at: datetime
    due_date: datetime
    submitted_at: datetime | None
    parties: tuple[PartyRow, ...]
    documents: tuple[DocumentRow, ...]
    checklist: tuple[ChecklistRow, ...]
    tasks: tuple[TaskRow, ...]


@dataclass(frozen=True)
class SeedBundle:
    now: datetime
    users: tuple[UserSeed, ...]
    cases: tuple[CaseRow, ...]
    audit: tuple[AuditRow, ...]

    def audit_insert_order(self) -> tuple[AuditRow, ...]:
        """seed.ts 数组倒序插入，使 ORDER BY seq DESC 与数组顺序一致。"""

        return tuple(reversed(self.audit))


def _party(
    parent_id: str | None,
    *,
    id: str,
    kind: str,
    legal_name: str,
    entity_type: str | None = None,
    country: str | None = None,
    country_set: bool = False,
    title: str | None = None,
    ownership_percent: int | str = 0,
    is_controller: bool = False,
    is_signing_authority: bool = False,
    is_us_person: bool = False,
    is_pep_hio: bool = False,
) -> PartySeed:
    """对齐 seed.ts 的 party()：自然人默认 Canada，实体默认没有国家。"""

    if not country_set:
        resolved_country = "Canada" if kind == "PERSON" else None
    else:
        resolved_country = country
    return PartySeed(
        id=id,
        parent_id=parent_id,
        kind=kind,
        legal_name=legal_name,
        entity_type=None if kind == "PERSON" else entity_type,
        country=resolved_country,
        title=title,
        ownership_percent=Decimal(ownership_percent),
        is_controller=is_controller,
        is_signing_authority=is_signing_authority,
        is_us_person=is_us_person,
        is_pep_hio=is_pep_hio,
    )


def _items(
    entries: tuple[tuple[str, str, tuple[str, ...]], ...],
    hours_ago: float,
    updated_by: str = "u-advisor",
) -> tuple[ChecklistSeed, ...]:
    return tuple(
        ChecklistSeed(requirement_id, status, document_ids, hours_ago, updated_by)
        for requirement_id, status, document_ids in entries
    )


def users() -> tuple[UserSeed, ...]:
    return (
        UserSeed(
            "u-advisor", "Sarah Whitfield", "sarah.whitfield@fidelity.ca",
            "ADVISOR", "WI Toronto",
        ),
        UserSeed(
            "u-ops", "Marcus Lee", "marcus.lee@fidelity.ca",
            "OPERATIONS", "Account Operations",
        ),
        UserSeed(
            "u-compliance", "Priya Raman", "priya.raman@fidelity.ca",
            "COMPLIANCE", "Compliance Pre-review",
        ),
        UserSeed(
            "u-admin", "Daniel Okafor", "daniel.okafor@fidelity.ca",
            "ADMIN", "Platform Admin",
        ),
        UserSeed(
            "u-advisor-2", "Julien Tremblay", "julien.tremblay@fidelity.ca",
            "ADVISOR", "PI Montréal",
        ),
    )


def case_specs() -> tuple[CaseSeed, ...]:
    return (
        CaseSeed(
            id="case-0139",
            reference="FCC-2026-0139",
            legal_name="Maple Ridge Holdings Inc.",
            entity_type="corporation",
            status="DOCS_REQUESTED",
            owner_id="u-advisor",
            jurisdiction="Ontario (OBCA)",
            registration_number="OBCA 2748113",
            created_days_ago=6,
            updated_hours_ago=3,
            due_in_days=4,
            version=9,
            province="ON",
            tax_residency="MIXED",
            features=("MARGIN", "OPTIONS"),
            trusted_contact=True,
            trusted_contact_name="Helen Chen",
            parties=(
                _party(None, id="mr-root", kind="ENTITY", legal_name="Maple Ridge Holdings Inc.", entity_type="corporation", ownership_percent=100),
                _party("mr-root", id="mr-alice", kind="PERSON", legal_name="Alice Chen", title="Director", ownership_percent=40, is_controller=True, is_signing_authority=True),
                _party("mr-root", id="mr-harbour", kind="ENTITY", legal_name="Harbourview Capital Ltd.", entity_type="corporation", country="Canada", country_set=True, ownership_percent=35),
                _party("mr-harbour", id="mr-raj", kind="PERSON", legal_name="Raj Patel", title="Shareholder", ownership_percent=70),
                _party("mr-harbour", id="mr-mei", kind="PERSON", legal_name="Mei Lin", country="United States", country_set=True, title="Shareholder", ownership_percent=30, is_us_person=True),
                _party("mr-root", id="mr-david", kind="PERSON", legal_name="David Okoye", title="Officer", ownership_percent=25, is_pep_hio=True),
            ),
            documents=(
                DocumentSeed("d-1", "naaf", "NAAF_MapleRidge_signed.pdf", 70),
                DocumentSeed("d-2", "formation", "Articles_of_Incorporation_2748113.pdf", 70, size_bytes=1_820_000),
                DocumentSeed("d-3", None, "Shareholder_Register_2026.pdf", 69, size_bytes=260_000),
                DocumentSeed("d-4", "resolution", "Board_Resolution_Trading_Authority.pdf", 26),
                DocumentSeed("d-5", "margin", "Margin_Agreement_signed.pdf", 5, uploaded_by="u-ops"),
            ),
            checklist=_items(
                (
                    ("naaf", "VERIFIED", ("d-1",)),
                    ("formation", "RECEIVED", ("d-2",)),
                    ("resolution", "RECEIVED", ("d-4",)),
                    ("margin", "RECEIVED", ("d-5",)),
                    ("beneficial-owner", "REQUESTED", ()),
                    ("identity", "REQUESTED", ()),
                    ("pep", "REQUESTED", ()),
                    ("w9", "REQUESTED", ()),
                    ("rc519", "REQUESTED", ()),
                ),
                5,
            ),
        ),
        CaseSeed(
            id="case-0142",
            reference="FCC-2026-0142",
            legal_name="Northwind Community Foundation",
            entity_type="charity",
            status="BUILDING",
            owner_id="u-advisor",
            jurisdiction="Canada (CNCA)",
            registration_number="BN 81920 4471 RR0001",
            created_days_ago=2,
            updated_hours_ago=1,
            due_in_days=9,
            version=4,
            tax_residency="CANADA",
            parties=(
                _party(None, id="nw-root", kind="ENTITY", legal_name="Northwind Community Foundation", entity_type="charity", ownership_percent=100),
                _party("nw-root", id="nw-grace", kind="PERSON", legal_name="Grace Morrison", title="Director", is_controller=True, is_signing_authority=True),
                _party("nw-root", id="nw-omar", kind="PERSON", legal_name="Omar Haddad", title="Director", is_controller=True),
                _party("nw-root", id="nw-lena", kind="PERSON", legal_name="Lena Kowalski", title="Officer", is_signing_authority=True),
            ),
        ),
        CaseSeed(
            id="case-0137",
            reference="FCC-2026-0137",
            legal_name="Thompson Family Trust",
            entity_type="trust",
            status="READY_FOR_COMPLIANCE",
            owner_id="u-advisor-2",
            jurisdiction="British Columbia",
            registration_number="Trust deed 2019-03-14",
            created_days_ago=11,
            updated_hours_ago=20,
            due_in_days=1,
            version=14,
            province="BC",
            tax_residency="CANADA",
            features=("COD_DVP",),
            trusted_contact=False,
            submitted_hours_ago=20,
            parties=(
                _party(None, id="tf-root", kind="ENTITY", legal_name="Thompson Family Trust", entity_type="trust", ownership_percent=100),
                _party("tf-root", id="tf-robert", kind="PERSON", legal_name="Robert Thompson", title="Trustee", is_controller=True, is_signing_authority=True),
                _party("tf-root", id="tf-emily", kind="PERSON", legal_name="Emily Thompson", title="Beneficiary", ownership_percent=50),
                _party("tf-root", id="tf-james", kind="PERSON", legal_name="James Thompson", title="Beneficiary", ownership_percent=50),
            ),
            documents=(
                DocumentSeed("t-1", "naaf", "NAAF_Thompson_Trust.pdf", 120, uploaded_by="u-advisor-2"),
                DocumentSeed("t-2", "formation", "Trust_Deed_2019.pdf", 120, uploaded_by="u-advisor-2", size_bytes=2_400_000),
                DocumentSeed("t-3", "beneficial-owner", "Beneficial_Ownership_Declaration.pdf", 48, uploaded_by="u-advisor-2"),
                DocumentSeed("t-4", "identity", "ID_Verification_Pack.pdf", 30, uploaded_by="u-ops", size_bytes=3_100_000),
                DocumentSeed("t-5", "cod-dvp", "DVP_Settlement_Instructions.pdf", 22, uploaded_by="u-ops"),
            ),
            checklist=_items(
                (
                    ("naaf", "VERIFIED", ("t-1",)),
                    ("formation", "VERIFIED", ("t-2",)),
                    ("beneficial-owner", "RECEIVED", ("t-3",)),
                    ("identity", "RECEIVED", ("t-4",)),
                    ("cod-dvp", "RECEIVED", ("t-5",)),
                ),
                22,
                "u-ops",
            ),
        ),
        CaseSeed(
            id="case-0131",
            reference="FCC-2026-0131",
            legal_name="Lakeshore Condominium Corporation No. 482",
            entity_type="condo",
            status="BUILDING",
            owner_id="u-advisor",
            jurisdiction="Ontario (Condominium Act)",
            registration_number="TSCC 482",
            created_days_ago=1,
            updated_hours_ago=6,
            due_in_days=12,
            version=1,
            parties=(
                _party(
                    None,
                    id="lc-root",
                    kind="ENTITY",
                    legal_name="Lakeshore Condominium Corporation No. 482",
                    entity_type="condo",
                    ownership_percent=100,
                ),
            ),
        ),
        CaseSeed(
            id="case-0128",
            reference="FCC-2026-0128",
            legal_name="Pacific Rim Ventures LP",
            entity_type="partnership",
            status="RETURNED",
            owner_id="u-advisor-2",
            jurisdiction="British Columbia",
            registration_number="LP1180042",
            created_days_ago=15,
            updated_hours_ago=28,
            due_in_days=-1,
            version=17,
            province="BC",
            tax_residency="INTERNATIONAL",
            features=("FPL",),
            trusted_contact=False,
            parties=(
                _party(None, id="pr-root", kind="ENTITY", legal_name="Pacific Rim Ventures LP", entity_type="partnership", ownership_percent=100),
                _party("pr-root", id="pr-gp", kind="ENTITY", legal_name="Pacific Rim GP Inc.", entity_type="corporation", country="Canada", country_set=True, title="General Partner", ownership_percent=1, is_controller=True),
                _party("pr-root", id="pr-wei", kind="PERSON", legal_name="Wei Zhang", country="Singapore", country_set=True, title="Limited Partner", ownership_percent=60),
                _party("pr-root", id="pr-sophie", kind="PERSON", legal_name="Sophie Martin", country="France", country_set=True, title="Limited Partner", ownership_percent=30),
            ),
            documents=(
                DocumentSeed("p-1", "naaf", "NAAF_PacificRim.pdf", 300, uploaded_by="u-advisor-2"),
                DocumentSeed("p-2", "formation", "LP_Agreement_Amended.pdf", 300, uploaded_by="u-advisor-2", size_bytes=3_900_000),
                DocumentSeed("p-3", "w8", "W-8BEN-E_PacificRim.pdf", 90, uploaded_by="u-advisor-2"),
            ),
            checklist=_items(
                (
                    ("naaf", "VERIFIED", ("p-1",)),
                    ("formation", "REJECTED", ("p-2",)),
                    ("w8", "RECEIVED", ("p-3",)),
                    ("identity", "REQUESTED", ()),
                    ("beneficial-owner", "REQUESTED", ()),
                ),
                28,
                "u-compliance",
            ),
            tasks=(
                TaskSeed(
                    "task-1",
                    "Disclose the shareholders of Pacific Rim GP Inc. down to natural persons.",
                    28,
                    party_id="pr-gp",
                ),
                TaskSeed(
                    "task-2",
                    "Limited partner interests total 91%; confirm the remaining 9%.",
                    28,
                    party_id="pr-root",
                ),
                TaskSeed(
                    "task-3",
                    "LP agreement is missing Schedule B (signing pages).",
                    28,
                    requirement_id="formation",
                ),
            ),
        ),
        CaseSeed(
            id="case-0119",
            reference="FCC-2026-0119",
            legal_name="Eagle River First Nation",
            entity_type="first_nation",
            status="APPROVED",
            owner_id="u-advisor",
            jurisdiction="Canada (Indian Act)",
            registration_number="Band No. 612",
            created_days_ago=24,
            updated_hours_ago=96,
            due_in_days=-10,
            version=21,
            province="MB",
            tax_residency="CANADA",
            trusted_contact=False,
            parties=(
                _party(None, id="er-root", kind="ENTITY", legal_name="Eagle River First Nation", entity_type="first_nation", ownership_percent=100),
                _party("er-root", id="er-chief", kind="PERSON", legal_name="Chief Thomas Redsky", title="Chief", is_controller=True, is_signing_authority=True),
                _party("er-root", id="er-council", kind="PERSON", legal_name="Marie Whitehorse", title="Councillor", is_signing_authority=True),
            ),
            documents=(
                DocumentSeed("e-1", "naaf", "NAAF_EagleRiver.pdf", 500),
                DocumentSeed("e-2", "formation", "Band_Council_Governance.pdf", 500),
                DocumentSeed("e-3", "resolution", "BCR_2026-14_Investment_Account.pdf", 400),
                DocumentSeed("e-4", "identity", "ID_Chief_Council.pdf", 300, uploaded_by="u-ops"),
            ),
            checklist=_items(
                (
                    ("naaf", "VERIFIED", ("e-1",)),
                    ("formation", "VERIFIED", ("e-2",)),
                    ("resolution", "VERIFIED", ("e-3",)),
                    ("identity", "VERIFIED", ("e-4",)),
                ),
                96,
                "u-compliance",
            ),
        ),
        CaseSeed(
            id="case-0144",
            reference="FCC-2026-0144",
            legal_name="Okafor Dental Professional Corporation IPP",
            entity_type="ipp_rca",
            status="BUILDING",
            owner_id="u-advisor-2",
            jurisdiction="Alberta",
            registration_number="CRA RPP 1453870",
            created_days_ago=0,
            updated_hours_ago=0.5,
            due_in_days=14,
            version=2,
            province="AB",
            parties=(
                _party(None, id="od-root", kind="ENTITY", legal_name="Okafor Dental Professional Corporation IPP", entity_type="ipp_rca", ownership_percent=100),
                _party("od-root", id="od-sponsor", kind="ENTITY", legal_name="Okafor Dental Professional Corporation", entity_type="corporation", country="Canada", country_set=True, title="Plan Sponsor", ownership_percent=100, is_controller=True),
            ),
        ),
    )


def audit_specs() -> tuple[AuditSeed, ...]:
    """与 seed.ts 数组顺序一致（展示时最新在前的约定；本数组本身从 a-1 开始）。"""

    return (
        AuditSeed("a-1", "case-0139", "u-advisor", "CASE_CREATED", "Created case for Maple Ridge Holdings Inc.", 144, 1),
        AuditSeed(
            "a-2", "case-0139", "u-advisor", "AI_SUGGESTION",
            "Accepted 4 parties extracted from Shareholder_Register_2026.pdf",
            69, 3,
            ai_model="doc-extract-demo", ai_accepted=True, ai_rule_version=RULE_VERSION,
        ),
        AuditSeed(
            "a-3", "case-0139", "u-advisor", "OWNERSHIP_UPDATED",
            "Marked David Okoye as PEP / HIO", 60, 5,
            changes=(("David Okoye · PEP / HIO", "No", "Yes"),),
        ),
        AuditSeed(
            "a-4", "case-0139", "u-advisor", "PROFILE_UPDATED",
            "Set tax residency to Mixed", 50, 6,
            changes=(("Tax residency", "Canada", "Mixed"),),
        ),
        AuditSeed(
            "a-5", "case-0139", "u-advisor", "STATUS_CHANGED",
            "Requested outstanding documents from client", 26, 8,
            changes=(("Status", "Building", "Docs requested"),),
        ),
        AuditSeed("a-6", "case-0139", "u-ops", "DOCUMENT_UPLOADED", "Uploaded Margin_Agreement_signed.pdf", 3, 9),
        AuditSeed("a-7", "case-0142", "u-advisor", "CASE_CREATED", "Created case for Northwind Community Foundation", 48, 1),
        AuditSeed("a-8", "case-0142", "u-advisor", "OWNERSHIP_UPDATED", "Added 3 directors and officers", 1, 4),
        AuditSeed(
            "a-9", "case-0137", "u-advisor-2", "STATUS_CHANGED",
            "Submitted for compliance review", 20, 14,
            changes=(("Status", "Docs requested", "Ready for compliance"),),
        ),
        AuditSeed(
            "a-10", "case-0128", "u-compliance", "COMPLIANCE_DECISION",
            "Returned to advisor with 3 comments", 28, 17,
            changes=(("Status", "Ready for compliance", "Returned"),),
        ),
        AuditSeed(
            "a-11", "case-0119", "u-compliance", "COMPLIANCE_DECISION",
            "Approved — beneficial ownership traced to band council", 96, 21,
            changes=(("Status", "Ready for compliance", "Approved"),),
        ),
        AuditSeed(
            "a-12", "case-0131", "u-advisor", "CASE_CREATED",
            "Created case for Lakeshore Condominium Corporation No. 482", 6, 1,
        ),
        AuditSeed(
            "a-13", "case-0144", "u-advisor-2", "CASE_CREATED",
            "Created case for Okafor Dental Professional Corporation IPP", 1, 1,
        ),
        AuditSeed(
            "a-14", "case-0144", "u-advisor-2", "AI_SUGGESTION",
            "Entity type suggested: IPP / RCA (accepted)", 1, 1,
            ai_model="entity-classifier-demo", ai_accepted=True, ai_rule_version=RULE_VERSION,
        ),
    )


def _ago(now: datetime, hours: float) -> datetime:
    return now - timedelta(hours=hours)


def build_seed(now: datetime | None = None) -> SeedBundle:
    moment = now or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    cases: list[CaseRow] = []
    for spec in case_specs():
        documents = tuple(
            DocumentRow(
                case_id=spec.id,
                document=document,
                uploaded_at=_ago(moment, document.hours_ago),
                extraction_id=f"ext_{document.id}",
            )
            for document in spec.documents
        )
        checklist = tuple(
            ChecklistRow(spec.id, item, _ago(moment, item.hours_ago))
            for item in spec.checklist
        )
        tasks = tuple(
            TaskRow(spec.id, task, _ago(moment, task.hours_ago))
            for task in spec.tasks
        )
        parties = tuple(
            PartyRow(spec.id, index, party)
            for index, party in enumerate(spec.parties)
        )
        submitted = (
            None
            if spec.submitted_hours_ago is None
            else _ago(moment, spec.submitted_hours_ago)
        )
        cases.append(
            CaseRow(
                case=spec,
                created_at=moment - timedelta(days=spec.created_days_ago),
                updated_at=_ago(moment, spec.updated_hours_ago),
                due_date=moment + timedelta(days=spec.due_in_days),
                submitted_at=submitted,
                parties=parties,
                documents=documents,
                checklist=checklist,
                tasks=tasks,
            )
        )
    audit = tuple(
        AuditRow(event, _ago(moment, event.hours_ago)) for event in audit_specs()
    )
    return SeedBundle(now=moment, users=users(), cases=tuple(cases), audit=audit)


def validate_seed_shape(bundle: SeedBundle) -> list[str]:
    """写入前核对行数、根节点，以及文件与清单 documentIds 一致。"""

    errors: list[str] = []
    if len(bundle.users) != EXPECTED_COUNTS["users"]:
        errors.append(f"用户 {len(bundle.users)}，期望 {EXPECTED_COUNTS['users']}")
    if len(bundle.cases) != EXPECTED_COUNTS["cases"]:
        errors.append(f"案件 {len(bundle.cases)}，期望 {EXPECTED_COUNTS['cases']}")
    parties = [row for case in bundle.cases for row in case.parties]
    documents = [row for case in bundle.cases for row in case.documents]
    checklist = [row for case in bundle.cases for row in case.checklist]
    tasks = [row for case in bundle.cases for row in case.tasks]
    if len(parties) != EXPECTED_COUNTS["parties"]:
        errors.append(f"股权节点 {len(parties)}，期望 {EXPECTED_COUNTS['parties']}")
    if len(documents) != EXPECTED_COUNTS["case_documents"]:
        errors.append(f"文件 {len(documents)}，期望 {EXPECTED_COUNTS['case_documents']}")
    if len(checklist) != EXPECTED_COUNTS["checklist_items"]:
        errors.append(f"清单 {len(checklist)}，期望 {EXPECTED_COUNTS['checklist_items']}")
    if len(tasks) != EXPECTED_COUNTS["review_tasks"]:
        errors.append(f"任务 {len(tasks)}，期望 {EXPECTED_COUNTS['review_tasks']}")
    if len(bundle.audit) != EXPECTED_COUNTS["audit_events"]:
        errors.append(f"审计 {len(bundle.audit)}，期望 {EXPECTED_COUNTS['audit_events']}")

    roots = {
        case.case.id: [row.party.id for row in case.parties if row.party.parent_id is None]
        for case in bundle.cases
    }
    if set(roots) != set(EXPECTED_ROOTS):
        errors.append(f"案件集合 {sorted(roots)} 与期望根节点不一致")
    for case_id, expected_root in EXPECTED_ROOTS.items():
        found = roots.get(case_id, [])
        if found != [expected_root]:
            errors.append(f"{case_id} 根节点 {found}，期望 [{expected_root}]")

    linked: dict[str, list[str]] = {}
    for row in checklist:
        for document_id in row.item.document_ids:
            linked.setdefault(document_id, []).append(row.item.requirement_id)
    actual_requirements: dict[str, str | None] = {}
    for row in documents:
        actual_requirements[row.document.id] = row.document.requirement_id
        owners = linked.get(row.document.id, [])
        if row.document.requirement_id is None:
            if owners:
                errors.append(f"{row.document.id} 未归属，但清单引用了它：{owners}")
        elif owners != [row.document.requirement_id]:
            errors.append(
                f"{row.document.id} requirement_id={row.document.requirement_id}，"
                f"清单 documentIds={owners}"
            )
    if actual_requirements != EXPECTED_DOCUMENT_REQUIREMENTS:
        errors.append("文件 requirement_id 与 seed.ts 的 documentIds 对照表不一致")
    for document_id, owners in linked.items():
        if document_id not in actual_requirements:
            errors.append(f"清单引用了不存在的文件 {document_id}")
    return errors


def case_analyze_payload(case: CaseRow) -> dict[str, object]:
    """给规则包 analyze 用的案件快照。同时带 snake_case 和 camelCase。"""

    spec = case.case
    parties = []
    for row in case.parties:
        party = row.party
        parties.append(
            {
                "id": party.id,
                "parent_id": party.parent_id,
                "parentId": party.parent_id,
                "kind": party.kind,
                "legal_name": party.legal_name,
                "legalName": party.legal_name,
                "entity_type": party.entity_type,
                "entityType": party.entity_type,
                "country": party.country,
                "title": party.title,
                "us_tax_class": None,
                "usTaxClass": None,
                "ownership_percent": float(party.ownership_percent),
                "ownershipPercent": float(party.ownership_percent),
                "is_controller": party.is_controller,
                "isController": party.is_controller,
                "is_signing_authority": party.is_signing_authority,
                "isSigningAuthority": party.is_signing_authority,
                "is_us_person": party.is_us_person,
                "isUsPerson": party.is_us_person,
                "is_pep_hio": party.is_pep_hio,
                "isPepHio": party.is_pep_hio,
            }
        )
    profile = {
        "province": spec.province,
        "tax_residency": spec.tax_residency,
        "taxResidency": spec.tax_residency,
        "features": list(spec.features),
        "trusted_contact": spec.trusted_contact,
        "trustedContact": spec.trusted_contact,
        "trusted_contact_name": spec.trusted_contact_name,
        "trustedContactName": spec.trusted_contact_name,
    }
    checklist = {
        row.item.requirement_id: {
            "status": row.item.status,
            "document_ids": list(row.item.document_ids),
            "documentIds": list(row.item.document_ids),
            "updated_by": row.item.updated_by,
            "updatedBy": row.item.updated_by,
        }
        for row in case.checklist
    }
    tasks = [
        {
            "id": row.task.id,
            "title": row.task.title,
            "party_id": row.task.party_id,
            "partyId": row.task.party_id,
            "requirement_id": row.task.requirement_id,
            "requirementId": row.task.requirement_id,
            "done": False,
            "source": row.task.source,
            "created_by": row.task.created_by,
            "createdBy": row.task.created_by,
        }
        for row in case.tasks
    ]
    return {
        "id": spec.id,
        "reference": spec.reference,
        "version": spec.version,
        "legal_name": spec.legal_name,
        "legalName": spec.legal_name,
        "entity_type": spec.entity_type,
        "entityType": spec.entity_type,
        "status": spec.status,
        "parties": parties,
        "profile": profile,
        "rule_version": RULE_VERSION,
        "ruleVersion": RULE_VERSION,
        "owner_id": spec.owner_id,
        "ownerId": spec.owner_id,
        "checklist": checklist,
        "custom_requirements": [],
        "customRequirements": [],
        "documents": [],
        "tasks": tasks,
    }
