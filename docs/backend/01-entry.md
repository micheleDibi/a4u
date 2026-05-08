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
5. Crea le sub-cartelle `uploads/{organizations,avatars,templates}` se
   mancanti.
6. Avvia il worker MiniMax: `avatar_clip_worker.start()` lancia un
   `asyncio.Task` singleton che processa le clip in stato
   `pending`/`processing` ogni `MINIMAX_POLL_INTERVAL_SECONDS`. Il task è
   memorizzato in `app.state.avatar_clip_worker_task` per il teardown.
   Lo stato delle clip è in DB, quindi il worker recupera automaticamente
   le clip lasciate in sospeso da un restart.
7. Logga `startup_complete`.

**Sezione shutdown**:

1. `await avatar_clip_worker.stop()` (cancella il task e attende il join).
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
5. **StaticFiles**: monta `UPLOAD_DIR` su `/uploads` per servire le immagini
   caricate. Crea la cartella se manca.
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
