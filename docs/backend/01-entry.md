# Backend 01 — Entry point

## `backend/app/__init__.py`

**Scopo**: package marker e versione.

**Esporta**:
- `__version__: str = "0.1.0"`.

Nessuna logica.

---

## `backend/app/main.py`

**Scopo**: app factory FastAPI. Compone middleware, monta routers, monta lo
static file server per gli upload, registra exception handlers, gestisce il
lifespan (startup + shutdown).

### Esporta

- `lifespan(app: FastAPI)`: async context manager, decorato con
  `@asynccontextmanager`, eseguito da FastAPI all'avvio e shutdown.
- `create_app() -> FastAPI`: factory. Costruisce l'app, attacca middleware,
  router, exception handlers. Usata anche dai test.
- `app: FastAPI = create_app()`: istanza globale per `uvicorn app.main:app`.

### `lifespan(app)`

**Async context manager** per il ciclo di vita dell'app.

**Sezione startup**:

1. Carica `Settings` (`get_settings()`).
2. Configura logging via `configure_logging(settings)`.
3. Se `SENTRY_DSN` valorizzato, inizializza `sentry_sdk` con
   `traces_sample_rate=0.1`, `send_default_pii=False`.
4. Apre una sessione DB e:
   - esegue `SELECT 1` per verificare la connettività;
   - chiama `ensure_seed(session)` per ruoli, permessi, role_permissions e
     bootstrap admin (idempotente);
   - commit.
   In caso di errore: logga e rilancia (l'app non parte).
5. Crea `upload_root` e le sue sub-cartelle se mancanti:
   `organizations`, `avatars`, `templates`, `courses`, `lesson_assets`,
   `lesson_videos`, `lesson_avatar_videos`. (`lesson_videos` ospita gli
   MP4 della Fase 6; `lesson_avatar_videos` i "Video con avatar" della
   Fase 6b.)
6. Avvia, in ordine, i worker async di background. Ognuno è esposto dal
   suo modulo in `app/services/` con la coppia
   `start_worker()` / `stop_worker()`: `start_worker()` lancia un
   `asyncio.Task` singleton che fa polling del DB; lo stato è sempre
   persistito su DB, quindi a ogni restart i worker recuperano da soli i
   task lasciati in sospeso. I worker avviati:
   - `avatar_clip_worker` — genera/polla le clip avatar su MiniMax
     (ogni `MINIMAX_POLL_INTERVAL_SECONDS`).
   - `course_document_worker` — pre-processing dei documenti di corso
     (Appendice A → riassunto strutturato).
   - `course_architecture_worker` — generazione architettura corso
     (Fase 1).
   - `course_lesson_structure_worker` — generazione struttura lezioni
     (Fase 2), dispatch parallelo dei moduli `pending`.
   - `course_lesson_content_worker` — generazione contenuti lezioni
     (Fase 3), dispatch parallelo con cap di concorrenza.
   - `course_lesson_slides_worker` — generazione slide lezioni (Fase 4).
   - `course_lesson_pdf_worker` — export PDF lezione testo.
   - `course_lesson_slides_pdf_worker` — export PDF slide (Fase 4).
   - `course_lesson_speech_worker` — generazione discorso temporizzato
     (Fase 5).
   - `course_lesson_speech_pdf_worker` — export PDF discorso (Fase 5).
   - `course_lesson_video_worker` — generazione video MP4 (Fase 6):
     orchestra TTS su RunPod GPU + slide Playwright + ffmpeg (cap 1 di
     default).
   - `course_lesson_avatar_video_worker` — "Video con avatar"
     (Fase 6b): orchestra il subprocess MuseTalk di lip-sync su RunPod
     GPU + overlay ffmpeg (cap 1 di default).
7. Logga `startup_complete`.

**Sezione shutdown**:

1. `await ..._worker.stop_worker()` per tutti i worker, in ordine
   inverso rispetto allo startup (da `course_lesson_avatar_video_worker`
   a `avatar_clip_worker`): ogni `stop_worker()` cancella il task e ne
   attende il join.
2. `await engine.dispose()`.
3. Logga `shutdown_complete`.

### `create_app() -> FastAPI`

Costruisce l'app:

1. `Settings` per parametri.
2. `FastAPI(title, version, lifespan=lifespan, docs_url, openapi_url, redoc_url=None)`.
   In produzione `docs_url=None` (Swagger UI nascosto).
3. **Rate limiter**: `app.state.limiter = limiter`,
   `add_exception_handler(RateLimitExceeded, rate_limit_handler)`.
4. **Middleware (ordine FastAPI: l'ultimo registrato è il PRIMO eseguito)**:
   - `SecurityHeadersMiddleware` (registrato per ultimo → eseguito sull'output).
   - `CsrfOriginMiddleware`.
   - `AccessLogMiddleware`.
   - `RequestIDMiddleware` (eseguito per primo sull'ingresso → propaga
     `request_id` agli altri middleware tramite context-var).
   - `CORSMiddleware`: `allow_origins=[FRONTEND_ORIGIN]`,
     `allow_credentials=True`, `allow_methods` ristretti,
     `allow_headers=["Authorization","Content-Type","X-Request-ID"]`,
     `max_age=600`.
5. **StaticFiles**: monta `UPLOAD_DIR` su `/uploads` per servire i file
   caricati (immagini di org/avatar/template, asset delle lezioni) e i
   video generati delle lezioni — `StaticFiles` supporta nativamente
   l'HTTP Range usato dai player. Crea la cartella se manca.
6. **Routers**: `app.include_router(api_router)` (prefisso `/api/v1`).
7. **Exception handlers**: `register_exception_handlers(app)` configura i
   gestori per `AppError`, `StarletteHTTPException`,
   `RequestValidationError`, `IntegrityError`, `Exception`.
8. Restituisce l'app.

### Note operative

- `app` è valutato a import time dell'istanza globale; `create_app()` viene
  chiamato anche dai test per creare app isolate.
- `lifespan` non è eseguito da `httpx.ASGITransport` di default. I test
  invocano `ensure_seed` esplicitamente in `conftest.py`.
- Il logging viene configurato ad ogni startup (non re-init in caso di
  reload uvicorn).
