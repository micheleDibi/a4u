from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter, rate_limit_handler
from app.db.seed import ensure_seed
from app.db.session import async_session_factory, engine
from app.middleware.access_log import AccessLogMiddleware
from app.middleware.csrf import CsrfOriginMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.services import (
    avatar_clip_worker,
    course_architecture_worker,
    course_document_worker,
    course_lesson_avatar_video_worker,
    course_lesson_content_worker,
    course_lesson_pdf_worker,
    course_lesson_slides_pdf_worker,
    course_lesson_slides_worker,
    course_lesson_speech_pdf_worker,
    course_lesson_speech_worker,
    course_lesson_structure_worker,
    course_lesson_video_worker,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("app.startup")

    # Sentry opzionale
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.env,
                traces_sample_rate=0.1,
                send_default_pii=False,
            )
            log.info("sentry_initialized")
        except Exception as exc:  # pragma: no cover
            log.warning("sentry_init_failed", error=str(exc))

    # Sanity check DB + seed
    async with async_session_factory() as session:
        try:
            await session.execute(text("SELECT 1"))
            await ensure_seed(session)
            await session.commit()
            log.info("startup_db_ready")
        except Exception as exc:  # pragma: no cover
            log.error("startup_db_failed", error=str(exc), exc_info=True)
            raise

    # Crea cartelle upload se mancanti
    settings.upload_root.mkdir(parents=True, exist_ok=True)
    for sub in (
        "organizations",
        "avatars",
        "templates",
        "courses",
        "lesson_assets",
        "lesson_videos",
        "lesson_avatar_videos",
    ):
        (settings.upload_root / sub).mkdir(parents=True, exist_ok=True)

    # Worker MiniMax (genera/polla clip avatar). Resuma task pending da DB.
    avatar_clip_worker.start_worker()
    # Worker pre-processing documenti corso (Appendice A → riassunto strutturato).
    course_document_worker.start_worker()
    # Worker generazione architettura corso (Fase 1 della pipeline AI).
    course_architecture_worker.start_worker()
    # Worker generazione struttura lezioni (Fase 2 — §5). Dispatch parallelo
    # dei moduli pending con cap di concorrenza.
    course_lesson_structure_worker.start_worker()
    # Worker generazione contenuti lezioni (Fase 3 — §6). Dispatch parallelo
    # delle lezioni pending con cap (default 3). Auto-genera glossario al
    # primo task se assente (§10.1).
    course_lesson_content_worker.start_worker()
    # Worker generazione slide lezioni (Fase 4 — §7). Dispatch parallelo
    # delle lezioni con `slides_status='pending'`. Pre-condizione:
    # `content_status ∈ (ready, approved)`.
    course_lesson_slides_worker.start_worker()
    # Worker export PDF lezioni (§7). Cap=2: Chromium pesa, niente
    # rate-limit ma I/O+CPU intensive.
    course_lesson_pdf_worker.start_worker()
    # Worker export PDF SLIDE (Fase 4 §7). Stessa pipeline del PDF
    # lezione testo, template dedicato per layout slide A4 landscape.
    course_lesson_slides_pdf_worker.start_worker()
    # Worker generazione discorso temporizzato (Fase 5 — §8). Dispatch
    # parallelo delle lezioni con `speech_status='pending'`.
    # Pre-condizione: `slides_status ∈ (ready, approved)`.
    course_lesson_speech_worker.start_worker()
    # Worker export PDF DISCORSO (Fase 5 §8). Stessa pipeline del PDF
    # lezione testo, template dedicato per layout per-slide con timeline.
    course_lesson_speech_pdf_worker.start_worker()
    # Worker generazione video MP4 (Fase 6 — §9). Cap=1 di default.
    # Orchestrazione: TTS su RunPod GPU + slide Playwright + ffmpeg.
    # Pre-condizione: speech+slides approved AND Avatar.audio_path
    # dell'assegnatario presente AND servizio TTS RunPod configurato.
    course_lesson_video_worker.start_worker()
    # Worker "Video con Avatar" (Fase 6b — §9b). Cap=1 di default.
    # Orchestrazione: subprocess MuseTalk lip-sync su RunPod GPU +
    # overlay ffmpeg. Pre-condizione: video della lezione `ready` AND
    # avatar dell'assegnatario con clip pronte AND MuseTalk configurato.
    course_lesson_avatar_video_worker.start_worker()

    log.info("startup_complete", env=settings.env)
    try:
        yield
    finally:
        await course_lesson_avatar_video_worker.stop_worker()
        await course_lesson_video_worker.stop_worker()
        await course_lesson_speech_pdf_worker.stop_worker()
        await course_lesson_speech_worker.stop_worker()
        await course_lesson_slides_pdf_worker.stop_worker()
        await course_lesson_pdf_worker.stop_worker()
        await course_lesson_slides_worker.stop_worker()
        await course_lesson_content_worker.stop_worker()
        await course_lesson_structure_worker.stop_worker()
        await course_architecture_worker.stop_worker()
        await course_document_worker.stop_worker()
        await avatar_clip_worker.stop_worker()
        await engine.dispose()
        log.info("shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="a4u API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )

    # Rate limit
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    # Middleware: l'ordine conta — il primo aggiunto è l'ultimo eseguito sulla request.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CsrfOriginMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=600,
    )

    # Static uploads
    upload_dir = Path(settings.upload_dir).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

    # Routers
    app.include_router(api_router)

    # Error handlers
    register_exception_handlers(app)

    return app


app = create_app()
