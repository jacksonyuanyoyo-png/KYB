"""Entra JWKS 缓存。测试注入 fetcher，默认实现才会访问登录主机。"""

from __future__ import annotations

import base64
import binascii
import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger("fcc_api.auth")

JWKS_CACHE_SECONDS = 3600

JwksFetcher = Callable[[str], Mapping[str, Any]]
Clock = Callable[[], float]

_shared_lock = threading.Lock()
_shared_caches: dict[tuple[str, str], JwksCache] = {}
_crypto_warned = False


def jwks_urls(issuer: str, tenant_id: str) -> list[str]:
    """先 `{issuer}/keys`，不行时再用租户 discovery 地址。"""

    urls: list[str] = []
    cleaned_issuer = issuer.strip().rstrip("/")
    if cleaned_issuer:
        urls.append(f"{cleaned_issuer}/keys")
    cleaned_tenant = tenant_id.strip().strip("/")
    if cleaned_tenant:
        urls.append(
            f"https://login.microsoftonline.com/{cleaned_tenant}/discovery/v2.0/keys"
        )
    return urls


def b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64url") from exc


def find_jwk(document: Mapping[str, Any], kid: str) -> dict[str, Any] | None:
    keys = document.get("keys")
    if not isinstance(keys, list):
        return None
    for key in keys:
        if not isinstance(key, Mapping) or key.get("kid") != kid:
            continue
        if key.get("kty") not in (None, "RSA"):
            continue
        if key.get("alg") not in (None, "RS256"):
            continue
        if key.get("use") not in (None, "sig"):
            continue
        return dict(key)
    return None


def verify_rs256_signature(
    jwk: Mapping[str, Any],
    signing_input: bytes,
    signature: bytes,
) -> bool:
    """用 JWK 的 RSA 公钥校验 RS256。未安装 cryptography 时失败关闭。

    测试应传入自己的验签函数，不要依赖本函数，也不要访问外网。
    """

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
    except ImportError:
        _warn_verifier_missing()
        return False
    try:
        modulus = int.from_bytes(b64url_decode(str(jwk["n"])), "big")
        exponent = int.from_bytes(b64url_decode(str(jwk["e"])), "big")
        public_key = RSAPublicNumbers(exponent, modulus).public_key()
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        return False
    return True


def shared_jwks_cache(issuer: str, tenant_id: str) -> JwksCache:
    """进程内按 issuer 与 tenant 复用同一份 3600 秒缓存。"""

    key = (issuer, tenant_id)
    with _shared_lock:
        cache = _shared_caches.get(key)
        if cache is None:
            cache = JwksCache(issuer=issuer, tenant_id=tenant_id)
            _shared_caches[key] = cache
        return cache


class JwksCache:
    """按 kid 取公钥。缓存 3600 秒；未知 kid 强制再拉一次。"""

    def __init__(
        self,
        *,
        issuer: str,
        tenant_id: str,
        fetcher: JwksFetcher | None = None,
        ttl_seconds: int = JWKS_CACHE_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        self._urls = jwks_urls(issuer, tenant_id)
        self._fetcher = fetcher or default_jwks_fetcher
        self._ttl_seconds = ttl_seconds
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._document: dict[str, Any] | None = None
        self._fetched_at = 0.0

    def key_for(self, kid: str) -> dict[str, Any] | None:
        with self._lock:
            found = find_jwk(self._document_unlocked(force=False), kid)
            if found is not None:
                return found
            return find_jwk(self._document_unlocked(force=True), kid)

    def _document_unlocked(self, *, force: bool) -> dict[str, Any]:
        now = self._clock()
        fresh = self._document is not None and (now - self._fetched_at) < self._ttl_seconds
        if fresh and not force:
            return self._document or {"keys": []}
        self._document = self._download()
        self._fetched_at = now
        return self._document

    def _download(self) -> dict[str, Any]:
        for url in self._urls:
            try:
                document = self._fetcher(url)
            except Exception:
                continue
            if not isinstance(document, Mapping):
                continue
            keys = document.get("keys")
            if isinstance(keys, list):
                return {"keys": [dict(item) for item in keys if isinstance(item, Mapping)]}
        return {"keys": []}


def default_jwks_fetcher(url: str) -> dict[str, Any]:
    """生产拉取。测试必须改注入 fetcher，不能走到这里。"""

    import httpx

    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    document = response.json()
    if not isinstance(document, dict):
        raise ValueError("JWKS response is not an object")
    return document


def _warn_verifier_missing() -> None:
    global _crypto_warned
    if _crypto_warned:
        return
    _crypto_warned = True
    logger.warning("rs256_verifier_unavailable")
