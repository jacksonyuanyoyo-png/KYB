"""启用中的提示词文件。正文只存在于 prompts/ 下的版本文件。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROMPT_FILES = (
    "relations.v1.txt",
    "classify.v1.txt",
    "assistant_parse.v1.txt",
    "pre_review.v1.txt",
)

PROMPT_IDS = {
    "relations": "relations.v1",
    "classify": "classify.v1",
    "assistant_parse": "assistant_parse.v1",
    "pre_review": "pre_review.v1",
}


def prompts_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"


def fixture_path() -> Path:
    here = Path(__file__).resolve()
    return here.parents[3] / "tests" / "ai" / "fixtures" / "prompt_set_v1.json"


def prompt_digests() -> dict[str, str]:
    folder = prompts_dir()
    return {
        name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
        for name in PROMPT_FILES
    }


def recorded_digests(path: Path | None = None) -> dict[str, str]:
    payload = json.loads((path or fixture_path()).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def ensure_prompt_hashes(
    app_env: str | None = None,
    recorded: dict[str, str] | None = None,
) -> None:
    """生产环境核对四个 txt 的 SHA-256。不一致则拒绝启动，日志不含正文。"""

    if app_env is None:
        from fcc_api.config import get_settings

        app_env = get_settings().app_env
    if app_env != "production":
        return
    actual = prompt_digests()
    expected = recorded if recorded is not None else recorded_digests()
    mismatch = set(PROMPT_FILES) != set(expected) or any(
        expected.get(name) != actual.get(name) for name in PROMPT_FILES
    )
    if mismatch:
        raise SystemExit("Refusing to start: AI_PROMPT_SET hash mismatch.")
