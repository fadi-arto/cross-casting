"""Layer: Config — Pydantic settings loaded from .env."""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)


class AppSettings(_Env):
    app_env: Literal["development", "test", "production"] = "development"
    max_pdf_pages: int = Field(50, ge=1)
    render_dpi: int = Field(150, ge=72, le=400)
    abs_tolerance: Decimal = Decimal("0.01")
    # Pages read in parallel (one VLM call each). 1 = sequential. Keep within the
    # provider's rate limit; the SDK retries 429s with backoff.
    page_concurrency: int = Field(4, ge=1, le=16)
    # A page is not sent to the table reader when OCR read it (≥ 20 words) and found
    # at most this many amounts — cover, auditor's report, policy text. Needs a real
    # OCR backend (no OCR = every page is read). -1 disables skipping.
    skip_page_max_amounts: int = Field(4, ge=-1)
    # Comma-separated list of allowed frontend origins, e.g.
    # CORS_ORIGINS=http://localhost:5173,https://app.example.com  ("*" = any)
    cors_origins: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]


class VlmSettings(_Env):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, env_prefix="VLM_",
        protected_namespaces=(),
    )
    backend: Literal["stub", "local", "anthropic"] = "stub"
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen2.5vl:7b"
    api_key: SecretStr = SecretStr("not-needed-locally")
    json_mode: Literal["json_object", "json_schema", "none"] = "json_object"
    timeout_seconds: float = 180.0
    max_retries: int = 2
    max_tokens: int = 8192
    temperature: float = 0.0
    seed: int | None = 0
    # Directory for cached page readings (same page + model + prompt → no new call).
    # Empty = no cache.
    cache_dir: str = ""


class AnthropicSettings(_Env):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, env_prefix="ANTHROPIC_",
        protected_namespaces=(), env_ignore_empty=True,   # "ANTHROPIC_EFFORT=" → unset
    )
    api_key: SecretStr | None = None
    model: str = "claude-haiku-5-5"
    # Optional overrides — when unset, VLM_TIMEOUT_SECONDS / VLM_MAX_TOKENS apply.
    timeout_seconds: float | None = None
    max_tokens: int | None = None
    max_retries: int = 2
    # low | medium | high | xhigh | max — empty = the model's default
    effort: str | None = None

    @property
    def has_key(self) -> bool:
        return self.api_key is not None and bool(self.api_key.get_secret_value().strip())


class OcrSettings(_Env):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, env_prefix="OCR_"
    )
    backend: Literal["none", "stub", "tesseract"] = "stub"
    langs: str = "ara+eng"
    timeout_seconds: int = Field(60, ge=1)


class LlmSettings(_Env):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, env_prefix="LLM_",
        protected_namespaces=(),
    )
    backend: Literal["none", "local"] = "none"
    base_url: str = "http://localhost:11434/v1"
    model: str = ""
    api_key: SecretStr = SecretStr("not-needed-locally")
    json_mode: Literal["json_object", "none"] = "json_object"
    timeout_seconds: float = 120.0
    max_tokens: int = 4096
    temperature: float = 0.0

    @property
    def enabled(self) -> bool:
        return self.backend != "none" and bool(self.model.strip())


class DatabaseSettings(_Env):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, env_prefix="DB_",
    )
    host: str = "localhost"
    port: int = 5432
    name: str = "crosscasting"
    user: str = "crosscasting"
    password: SecretStr = SecretStr("changeme_in_production")

    @property
    def url(self) -> str:
        pw = self.password.get_secret_value()
        return f"postgresql+psycopg2://{self.user}:{pw}@{self.host}:{self.port}/{self.name}"


class StorageSettings(_Env):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, env_prefix="MINIO_",
    )
    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: SecretStr = SecretStr("changeme_in_production")
    secure: bool = False
    bucket: str = "crosscasting-files"
    presigned_url_expires_hours: int = 24
    # Address browsers use to reach MinIO (host:port, no scheme), e.g. localhost:9010
    # or files.example.com. Download links are signed for it. Empty = internal endpoint.
    public_endpoint: str = ""
    public_secure: bool | None = None          # default: same as MINIO_SECURE
    region: str = "us-east-1"


class Settings(BaseModel):
    app: AppSettings
    vlm: VlmSettings
    anthropic: AnthropicSettings
    ocr: OcrSettings
    llm: LlmSettings
    db: DatabaseSettings
    storage: StorageSettings

    @property
    def vlm_label(self) -> str:
        if self.vlm.backend == "anthropic":
            return f"anthropic/{self.anthropic.model}"
        if self.vlm.backend == "local":
            return f"local/{self.vlm.model}"
        return "stub"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app=AppSettings(),
        vlm=VlmSettings(),
        anthropic=AnthropicSettings(),
        ocr=OcrSettings(),
        llm=LlmSettings(),
        db=DatabaseSettings(),
        storage=StorageSettings(),
    )
