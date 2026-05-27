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
    # Pool DB: alzato da 10/20 a 20/60 per supportare la duplicazione corso
    # con concorrenza alta. La phase combined apre 3 sessioni in parallelo
    # per lezione (content + slides + speech in 3 sessioni separate); con
    # cap 15-20 lezioni concorrenti si arriva a 45-60 connessioni dedicate
    # al worker. Postgres default supporta 100 connessioni totali, abbiamo
    # ~20 di margine per le richieste utente normali.
    database_pool_size: int = 20
    database_max_overflow: int = 60

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
    # Fallback model usato dalla duplicazione corso quando il modello
    # default fallisce con transient persistenti (5xx, timeout) anche
    # dopo i retry interni. Piu' costoso ma piu' stabile.
    openai_model_fallback: str = "gpt-4o"
    # Cap globale di chiamate OpenAI translate concorrenti durante la
    # duplicazione corso. 80 e' al ~3% del rate-limit gpt-4o-mini tier
    # 2 (30000 RPM): margine ampio per gestire i 520 transient di
    # Cloudflare senza saturare. Alzabile in prod via env.
    openai_translate_global_concurrency: int = 80
    openai_translate_batch_size: int = 40
    openai_summarize_model: str = "gpt-4o-mini"
    openai_summarize_max_tokens: int = 8000
    # Generazione AI di obiettivi corso + argomenti chiave da un
    # documento di riferimento caricato dall'utente (tab "Obiettivi e
    # Argomenti chiave"). Output target: obiettivi 2500-5000 caratteri
    # (prosa articolata) + lista 8-15 argomenti chiave. Max tokens 8000
    # per evitare troncamenti sul JSON (string escaping inflaziona il
    # conteggio token rispetto ai caratteri visibili).
    openai_objectives_model: str = "gpt-4o-mini"
    openai_objectives_max_tokens: int = 8000

    # Ricerca paper scientifici nella tab "Documenti" (multi-source).
    # OpenAlex e' la primary search, Semantic Scholar e Crossref sono
    # usati on-demand per arricchire i metadata di singoli paper.
    # `papers_polite_email` viene messa nel User-Agent come `mailto:` per
    # entrare nel "polite pool" di tutti e 3 i provider (no rate-limit
    # aggressivo). Vuota = User-Agent senza mailto.
    openalex_base_url: str = "https://api.openalex.org"
    semantic_scholar_base_url: str = "https://api.semanticscholar.org"
    crossref_base_url: str = "https://api.crossref.org"
    papers_polite_email: str = ""
    # Riassunto AI di paper (sincrono, no persistenza). Output: riassunto
    # breve + tecnico + parole chiave + limiti dello studio.
    openai_paper_summary_model: str = "gpt-4o-mini"
    openai_paper_summary_max_tokens: int = 3000
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

    # Fase 5 — Discorso temporizzato (§8).
    # Output prosa pura, ~6-12k tokens. Cap=16000 per coprire lezioni
    # lunghe (90 min ≈ 11700 parole IT, ~16k token con reasoning).
    openai_lesson_speech_model: str = "gpt-5.5"
    openai_lesson_speech_max_tokens: int = 16_000
    openai_lesson_speech_reasoning_effort: str = "medium"
    course_lesson_speech_poll_interval_seconds: int = 4
    # Cap=3 come slides/content: input ~12-25k (testo + slide),
    # output ~6-12k, niente bottleneck di rate-limit.
    course_lesson_speech_max_concurrency: int = 3
    # Auto-retry trasparente per l'utente. Vedi
    # `course_lesson_structure_auto_retry_max`.
    course_lesson_speech_auto_retry_max: int = 5

    # Image → Mermaid: chiamata Vision API on-demand quando l'utente
    # carica un'immagine nell'editor lezione e clicca "Digitalizza".
    # Modello deve supportare input multimodale (image_url). gpt-4o è
    # vision-capable e non-reasoning (effort = None default).
    openai_image_to_mermaid_model: str = "gpt-4o"
    openai_image_to_mermaid_reasoning_effort: str | None = None
    openai_image_to_mermaid_max_tokens: int = 4_000

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

    # §9 — Generazione video MP4 (TTS XTTS-v2 su RunPod + slide + ffmpeg).
    # Pre-condizione runtime: speech_status='approved' AND
    # slides_status='approved' AND course.assignee.avatar.audio_path
    # esiste su filesystem AND servizio TTS RunPod configurato.
    #
    # TTS XTTS-v2 su RunPod Serverless GPU (vedi cartella `XTTS/`): il
    # backend invia un job per video e consuma i segment in streaming.
    runpod_api_key: str | None = None
    runpod_tts_endpoint_id: str | None = None
    runpod_base_url: str = "https://api.runpod.ai"
    # Timeout wall-clock totale di un job TTS (assorbe il cold start GPU).
    runpod_tts_timeout_seconds: int = 1800
    runpod_tts_poll_interval_seconds: int = 3

    # Worker video: orchestrazione (TTS remoto + slide + ffmpeg). Default 1.
    course_lesson_video_poll_interval_seconds: int = 4
    course_lesson_video_max_concurrency: int = 1
    course_lesson_video_auto_retry_max: int = 3

    # Encoding ffmpeg (1080p @ 30fps H.264 + AAC).
    video_resolution: str = "1920x1080"
    video_framerate: int = 30
    video_audio_bitrate: str = "192k"
    video_audio_sample_rate: int = 48000
    video_video_codec: str = "libx264"
    video_crf: int = 23  # quality 1080p tipico YouTube
    # libx264 preset: trade-off velocità/compressione. Per slide statiche
    # (`-tune stillimage`) `veryfast` produce file identico a `medium` in
    # qualità percepita ma è 3-5× più veloce. Su CPU senza AVX (QEMU VM)
    # questo taglia ~70% del tempo di encoding. Valori validi:
    # ultrafast, superfast, veryfast, faster, fast, medium, slow, slower,
    # veryslow. Override via env `VIDEO_PRESET`.
    video_preset: str = "veryfast"
    video_pixel_format: str = "yuv420p"  # compat HTML5/Quicktime
    lesson_video_max_mb: int = 500  # safety upper bound
    ffmpeg_binary: str = "ffmpeg"

    # §9b — "Video con Avatar" (lip-sync MuseTalk su RunPod).
    # Il client MuseTalk vendored (`app/musetalk_client/`) gira come
    # subprocess isolato: genera un video di avatar parlante e il worker
    # `course_lesson_avatar_video_worker` lo sovrappone in basso a destra
    # al video MP4 già generato della lezione. Pre-condizione runtime:
    # `video_status='ready'` AND l'avatar dell'assegnatario ha clip pronte.
    #
    # RunPod: stesso account del TTS (`runpod_api_key` riusato), endpoint
    # serverless dedicato a MuseTalk. R2 (Cloudflare, S3-compatible) è lo
    # storage di transito per video/audio/output del job. Queste credenziali
    # vengono passate al subprocess come variabili d'ambiente.
    runpod_musetalk_endpoint_id: str | None = None
    r2_endpoint: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None

    # Worker video con avatar: orchestrazione subprocess MuseTalk +
    # overlay ffmpeg. Cap=1 (un job GPU per volta, costoso).
    course_lesson_avatar_video_poll_interval_seconds: int = 4
    course_lesson_avatar_video_max_concurrency: int = 1
    course_lesson_avatar_video_auto_retry_max: int = 3
    # Timeout wall-clock del subprocess MuseTalk (preprocess + lipsync +
    # download). Generoso: assorbe cold start GPU + audio molto lunghi.
    course_lesson_avatar_video_timeout_seconds: int = 10800

    # Overlay dell'avatar sul video della lezione. Quadrato (le clip
    # MiniMax sono 1:1), ancorato in basso a destra.
    #   scale  = lato del quadrato come frazione della larghezza del video
    #   margin = distanza dai bordi destro/inferiore, in pixel
    avatar_video_overlay_scale: float = 0.24
    avatar_video_overlay_margin: int = 24
    # Risoluzione (lato del quadrato) a cui a4u ridimensiona le clip
    # dell'avatar prima di passarle a MuseTalk. Le clip MiniMax sono
    # 1080×1080: a quella risoluzione il lip-sync su RunPod sfora il tetto
    # di 60 min (blending + encode + RAM scalano con l'area del frame).
    # 640 riporta i tempi nella norma senza perdita visibile — nel video
    # finale l'avatar è ~475px. Vedi
    # `course_lesson_avatar_video_worker._prepare_musetalk_clips`.
    avatar_video_clip_resolution: int = 640

    # Worker duplicazione corso in altra lingua. Job lungo (5-15 min per
    # corso medio): cap globale=1 per evitare conflitti di rate-limit
    # OpenAI tra job. Dentro al job, le lezioni vengono tradotte in
    # parallelo cap=3 per fase (mirror del content worker).
    course_duplication_poll_interval_seconds: int = 4
    course_duplication_max_concurrent_jobs: int = 1
    # Cap di lezioni tradotte in parallelo (per phase). Con la phase
    # combined (content+slides+speech in parallelo dentro la stessa
    # lezione) ogni lezione consuma 3 task local + N chiamate OpenAI
    # (chunk parallelizzati dentro ogni phase). Cap 20 → 60 task local
    # → fino a ~60-120 chiamate OpenAI concorrenti, capped dal
    # `openai_translate_global_concurrency` (80). Pool DB 80 connessioni
    # supporta 60 sessioni concorrenti con margine.
    course_duplication_lesson_translate_concurrency: int = 20
    course_duplication_auto_retry_max: int = 5
    # Timeout massimo (in minuti) per un job di duplicazione completo.
    # Oltre questo limite il job viene marcato `failed` e il target
    # course viene eliminato automaticamente. 90 min copre con margine
    # un corso da 100 lezioni con il retry esponenziale attivo
    # (un corso da 80 lezioni in condizioni normali finisce in ~30 min).
    course_duplication_job_timeout_minutes: int = 90

    # Nova — assistente AI contestuale floating widget. Stateless DB-side.
    # Modello veloce ed economico (chat conversazionale, no JSON schema).
    openai_nova_model: str = "gpt-4o-mini"
    openai_nova_max_tokens: int = 512
    openai_nova_temperature: float = 0.7
    # Cap di messaggi di history inviati dal FE al BE per ogni chat.
    # Mantiene il filo del discorso senza esplodere i token.
    nova_history_cap: int = 10
    # Rate limit per utente (slowapi). 30/min = ~1 msg ogni 2s, generoso
    # per una conversazione fluida ma stoppa abuse.
    nova_rate_limit_per_minute: int = 30

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
        "runpod_api_key",
        "runpod_tts_endpoint_id",
        "runpod_musetalk_endpoint_id",
        "r2_endpoint",
        "r2_bucket",
        "r2_access_key_id",
        "r2_secret_access_key",
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
