import secrets

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def new_id(prefix: str) -> str:
    """与前端 uid() 同量级：`{prefix}_` 加 8 位小写字母数字。"""
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{prefix}_{suffix}"
