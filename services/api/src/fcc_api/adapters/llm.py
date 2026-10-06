"""OpenAI 兼容聊天接口。noop 不访问网络。"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import httpx

from fcc_api.errors import ApiError

logger = logging.getLogger("fcc_api.adapters.llm")

_UNAVAILABLE = "The assistant is not configured."
_TIMEOUT = 30.0


class LanguageModel(Protocol):
    name: str

    def complete_json(self, *, system: str, user: str, max_tokens: int) -> dict[str, Any]: ...


class NoopLanguageModel:
    name = "noop"

    def complete_json(self, *, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        del system, user, max_tokens
        raise ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {})


class OpenAICompatibleModel:
    """POST {base_url}/chat/completions。base_url 已含 /v1。"""

    name = "openai"

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete_json(self, *, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        raw = self._chat(system=system, user=user, max_tokens=max_tokens)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raw = self._chat(
                system="Return only JSON matching the schema. No markdown.",
                user=raw[:4000],
                max_tokens=max_tokens,
            )
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                raise ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {}) from None
        if not isinstance(parsed, dict):
            raise ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {})
        return parsed

    def _chat(self, *, system: str, user: str, max_tokens: int) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError:
            logger.info("llm_http_error", extra={"model": self.model})
            raise ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {}) from None
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {}) from None


def get_language_model(settings: object | None = None) -> LanguageModel:
    if settings is None:
        from fcc_api.config import get_settings

        settings = get_settings()
    adapter = str(getattr(settings, "llm_adapter", "noop") or "noop").strip().lower()
    if adapter in {"", "noop"}:
        return NoopLanguageModel()
    if adapter == "openai":
        base_url = str(getattr(settings, "llm_base_url", "") or "").strip()
        api_key = str(getattr(settings, "llm_api_key", "") or "").strip()
        model = str(getattr(settings, "llm_model", "") or "").strip()
        if not base_url or not api_key or not model:
            raise ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {})
        return OpenAICompatibleModel(base_url=base_url, api_key=api_key, model=model)
    raise ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {})
