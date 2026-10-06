import secrets
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "production"]
AuthMode = Literal["header", "entra"]
StorageBackend = Literal["local", "s3"]

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"


class Settings(BaseSettings):
    """环境变量名与 docs/backend/00-scope-and-stack.md 第 5 节一致。"""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = "local"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "postgresql+psycopg://fcc:fcc-local-only@localhost:5432/complex_accounts"
    database_url_test: str = (
        "postgresql+psycopg://fcc:fcc-local-only@localhost:5432/complex_accounts_test"
    )
    auth_mode: AuthMode = "header"
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_audience: str = ""
    entra_issuer: str = ""
    entra_role_group_map: str = ""
    cors_allowed_origins: str = "http://localhost:3000"
    storage_backend: StorageBackend = "local"
    local_storage_root: str = "./.local-storage"
    local_url_signing_secret: str = ""
    aws_region: str = ""
    s3_bucket_quarantine: str = ""
    s3_bucket_accepted: str = ""
    s3_kms_key_id: str = ""
    upload_url_ttl_seconds: int = Field(default=900, ge=1)
    download_url_ttl_seconds: int = Field(default=300, ge=1)
    upload_max_bytes: int = Field(default=25_000_000, ge=1)
    upload_allowed_mime: str = "application/pdf,image/jpeg,image/png"
    scan_adapter: str = "noop"
    extraction_adapter: str = "noop"
    ai_model_allowlist: str = (
        "doc-extract-demo,entity-classifier-demo,case-assistant-demo,pre-review-demo"
    )
    log_level: str = "INFO"
    # docs/backend/12-production-launch.md 第 12 节。本地默认不拒绝启动。
    jobs_inline: bool = True
    four_eyes_publish: bool = False
    allow_demo_rules: bool = True
    async_scan: bool = False
    screening_required: bool = False
    entra_required_acrs: str = ""
    cdr_adapter: str = "noop"
    cdr_endpoint: str = ""
    scan_endpoint: str = ""
    extraction_endpoint: str = ""
    llm_endpoint: str = ""
    screening_endpoint: str = ""
    submission_endpoint: str = ""
    llm_adapter: str = "noop"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1-mini"
    screening_adapter: str = "noop"
    submission_adapter: str = "unconfigured"
    submission_callback_secret: str = ""
    s3_bucket_audit: str = ""
    retention_case_days: int = 2555
    retention_document_days: int = 2555
    retention_audit_days: int = 2555
    portal_invite_ttl_days: int = 7
    ai_prompt_set: str = "v1"

    @model_validator(mode="after")
    def fill_signing_secret(self) -> Self:
        if not self.local_url_signing_secret.strip():
            self.local_url_signing_secret = secrets.token_urlsafe(32)
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_allowed_origins.split(",") if item.strip()]

    @property
    def ai_models(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.ai_model_allowlist.split(",") if item.strip())


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None


def ensure_process_may_start(settings: Settings) -> None:
    """APP_ENV=production 且 AUTH_MODE=header 时拒绝启动。不打印密钥。"""
    if settings.app_env == "production" and settings.auth_mode == "header":
        raise SystemExit(
            "Refusing to start: AUTH_MODE=header is not allowed when APP_ENV=production."
        )
