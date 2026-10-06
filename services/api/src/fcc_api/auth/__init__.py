"""身份、案件可见范围，以及写操作权限矩阵。"""

from fcc_api.auth.actor import Actor, AuthFailure, Role
from fcc_api.auth.policy import (
    case_visible,
    require_case_write,
    require_checklist_item_status,
    require_rule_write,
    role_allows,
)

__all__ = [
    "Actor",
    "AuthFailure",
    "Role",
    "case_visible",
    "require_case_write",
    "require_checklist_item_status",
    "require_rule_write",
    "role_allows",
]
