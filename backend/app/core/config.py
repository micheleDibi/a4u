from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Risolve il path assoluto di `.env` alla root del repository:
#   backend/app/core/config.py → parents[3] = repo root.
# Pydantic Settings ignora silenziosamente il file se non esiste
# (es. in container produzione le var arrivano da `environment:`).
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT_ENV),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    database_url: str = "postgresql+asyncpg://a4u:a4u_dev_password@localhost:5432/a4u"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 15
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 7

    frontend_origin: str = "http://localhost:5173"
    cookie_domain: str | None = None
    cookie_secure: bool = False

    upload_dir: str = "./uploads"
    upload_max_mb: int = 5
    avatar_audio_max_mb: int = 10
    course_document_max_mb: int = 25
    public_base_url: str = "http://localhost:8000"

    minimax_api_key: str | None = None
    minimax_base_url: str = "https://api.minimax.io"
    minimax_video_model: str = "MiniMax-Hailuo-02"
    minimax_clip_duration: int = 6
    minimax_clip_resolution: str = "1080P"
    minimax_poll_interval_seconds: int = 10

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_translate_batch_size: int = 40
    openai_summarize_model: str = "gpt-4o-mini"
    openai_summarize_max_tokens: int = 8000
    openai_modules_lessons_model: str = "gpt-5.5"
    openai_architecture_max_tokens: int = 8000
    # Reasoning effort: vedi `.env.example` per spiegazione + valori validi.
    # Su modelli non-reasoning il backend NON invia il parametro (no error).
    openai_architecture_reasoning_effort: str = "medium"
    openai_lesson_structure_model: str = "gpt-5.5"
    # gpt-5.5 consuma molti token nel reasoning prima di emettere il JSON.
    # 16000 lascia margine per ~5 lezioni × 4 sezioni con rationale lunghi
    # + reasoning. Se vedi `lessons_structure_output_truncated`, alza ancora.
    openai_lesson_structure_max_tokens: int = 16000
    openai_lesson_structure_reasoning_effort: str = "medium"
    course_document_max_chars: int = 120_000
    course_document_poll_interval_seconds: int = 4
    course_architecture_poll_interval_seconds: int = 4
    course_architecture_documents_context_max_chars: int = 60_000
    course_lesson_structure_poll_interval_seconds: int = 4
    course_lesson_structure_max_concurrency: int = 5
    course_lesson_structure_documents_context_max_chars: int = 30_000
    # Numero massimo di retry automatici dopo errore (transitorio o
    # validazione). Il worker re-imposta status='pending' invece di
    # 'failed' finché attempts < auto_retry_max. UX: l'utente non
    # vede mai l'errore, vede solo "in elaborazione" finché passa.
    course_lesson_structure_auto_retry_max: int = 5

    # Glossario corso (§10.1) — chiamata AI single-shot, prerequisito Fase 3.
    openai_glossary_model: str = "gpt-5.5"
    openai_glossary_max_tokens: int = 4_000
    course_glossary_documents_context_max_chars: int = 20_000

    # Fase 3 — Contenuti delle lezioni (§6).
    # Output lezione 8-15k tokens + reasoning gpt-5.5 → cap alto (32000).
    openai_lesson_content_model: str = "gpt-5.5"
    openai_lesson_content_max_tokens: int = 32_000
    # Default `high` perché il task contenuto lezione è il più complesso del
    # pipeline (markdown lungo + asset + bibliografia + JSON schema strict).
    openai_lesson_content_reasoning_effort: str = "high"
    course_lesson_content_poll_interval_seconds: int = 4
    # Cap=3: output 5x più grande di Fase 2, evita rate-limit OpenAI.
    course_lesson_content_max_concurrency: int = 3
    course_lesson_content_documents_context_max_chars: int = 20_000
    # Auto-retry trasparente per l'utente. Vedi
    # `course_lesson_structure_auto_retry_max`.
    course_lesson_content_auto_retry_max: int = 5

    # Fase 4 — Slide della lezione (§7).
    # Output 4-8k tokens + reasoning. Cap=16000 per non troncare.
    openai_lesson_slides_model: str = "gpt-5.5"
    openai_lesson_slides_max_tokens: int = 16_000
    openai_lesson_slides_reasoning_effort: str = "medium"
    course_lesson_slides_poll_interval_seconds: int = 4
    # Cap=3 come content: input ~8-18k, output ~4-8k, niente bottleneck.
    course_lesson_slides_max_concurrency: int = 3
    # Auto-retry trasparente per l'utente. Vedi
    # `course_lesson_structure_auto_retry_max`.
    course_lesson_slides_auto_retry_max: int = 5

    # §7 — Export PDF lezioni.
    # Cap=2: rendering Playwright è I/O+CPU intensive (Chromium istanza).
    course_lesson_pdf_poll_interval_seconds: int = 4
    course_lesson_pdf_max_concurrency: int = 2
    # Auto-retry trasparente per l'utente. Vedi
    # `course_lesson_structure_auto_retry_max`.
    course_lesson_pdf_auto_retry_max: int = 5
    # Directory di output per i PDF generati. Path relativo alla root
    # del backend o assoluto. Il file system è la persistence layer:
    # niente object storage in MVP.
    generated_pdfs_dir: str = "generated_pdfs"

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_full_name: str = "Platform Admin"

    rate_limit_login_per_min: int = 5
    login_lockout_threshold: int = 10
    login_lockout_minutes: int = 15

    sentry_dsn: str | None = None

    @field_validator("cookie_domain", mode="before")
    @classmethod
    def _empty_string_to_none(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value

    @field_validator(
        "sentry_dsn",
        "bootstrap_admin_email",
        "bootstrap_admin_password",
        "minimax_api_key",
        "openai_api_key",
        mode="before",
    )
    @classmethod
    def _none_if_empty(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value

    @property
    def upload_root(self) -> Path:
        return Path(self.upload_dir).resolve()

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_allow_origins(self) -> list[str]:
        return [self.frontend_origin]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor; per `lifespan` ricaricare manualmente è raro."""
    return Settings()  # type: ignore[call-arg]
