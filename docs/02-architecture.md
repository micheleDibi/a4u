# 02 — Architecture

## Topologia

```
                     ┌─────────────────────────────────┐
                     │           Browser               │
                     │  React app (Vite/MUI)           │
                     │  cookie: access_token (HttpOnly)│
                     │  cookie: refresh_token (HttpOnly│
                     │          path=/api/v1/auth/refresh)
                     └──────────────────┬──────────────┘
                                        │
                       Dev: vite proxy ─┤
                       Prod: nginx     │  /api/* + /uploads/*
                                        ▼
                     ┌─────────────────────────────────┐
                     │      FastAPI (uvicorn)          │
                     │  middleware:                    │
                     │   1. CORS                       │
                     │   2. RequestID                  │
                     │   3. AccessLog                  │
                     │   4. CsrfOrigin                 │
                     │   5. SecurityHeaders            │
                     │  routers /api/v1                │
                     │  StaticFiles /uploads           │
                     │  rate-limit (slowapi)           │
                     │  exception handlers             │
                     └──────────────────┬──────────────┘
                                        │ async (asyncpg)
                                        ▼
                     ┌─────────────────────────────────┐
                     │      PostgreSQL 16              │
                     │  citext extension               │
                     │  uuid-ossp extension            │
                     │  statement_timeout=30s          │
                     └─────────────────────────────────┘
```

## Layer del backend

```
api/v1/*       (router HTTP, validazione I/O via Pydantic, CORS-friendly)
   │
   ├─ deps   (get_db, get_current_user, require_platform_admin)
   ├─ permissions.require(*codes)
   │
   ▼
services/*     (logica di dominio, transazione, audit)
   │
   ▼
models/*       (SQLAlchemy 2 ORM, vincoli, indici)
   │
   ▼
db/session     (engine async, session factory, pool tuning)
```

Regola di dipendenza: i layer **inferiori non importano da quelli superiori**.
I router conoscono i services e gli schemas; i services conoscono i models, i
core helper, e l'audit; i models conoscono solo `db.base`.

## Flusso di una richiesta autenticata

Esempio: `GET /api/v1/orgs/{org_id}/templates/slide` come `org_admin`.

1. **Browser** invia GET con cookie `access_token`. Stesso origin del frontend
   (in dev via proxy Vite, in prod via nginx).
2. **`CORSMiddleware`**: validazione origin (consente solo `FRONTEND_ORIGIN`).
3. **`RequestIDMiddleware`**: legge/genera `X-Request-ID`, lo mette nel
   contesto structlog.
4. **`AccessLogMiddleware`**: cronometra la richiesta.
5. **`CsrfOriginMiddleware`**: il metodo `GET` è "safe", passa.
   Per metodi mutating (`POST/PUT/PATCH/DELETE`) verifica `Origin`/`Referer`.
6. **`SecurityHeadersMiddleware`**: aggiunge headers di sicurezza alla response.
7. **Router** matcha la rotta. Risolve dipendenze:
   - `get_db()` apre una sessione async.
   - `get_current_user()` decodifica `access_token`, carica `User`, contesto
     log con `user_id`.
   - `require(P.TEMPLATE_SLIDE_MANAGE)`:
     - bypass se `user.is_platform_admin`;
     - altrimenti carica `Membership` per `(user, org_id)`;
     - calcola `permissions = role_default ⊕ org_overrides ⊕ membership_overrides`;
     - 403 se manca anche solo un permesso richiesto.
8. **Handler** chiama `template_service.list_slide_templates(db, org_id)`.
9. **Service** esegue query SQLAlchemy 2 async, ritorna `list[SlideTemplate]`.
10. **Schema Pydantic** converte i modelli in DTO `SlideTemplateOut[]`.
11. La sessione `get_db` esegue `commit()` (no-op se solo SELECT).
12. **AccessLog** logga la riga con `duration_ms`, `status=200`.
13. **Response** torna al client con header `X-Request-ID`.

In caso di errore di dominio (es. `PermissionDeniedError`):
- viene catturato dall'exception handler dedicato (`app/core/errors.py`),
- `get_db` esegue `rollback()`,
- la risposta JSON è `{ code: "permission_denied", message, request_id, meta }`.

## Flusso refresh token (rotation + reuse-detection)

```
Client → POST /api/v1/auth/refresh   (cookie refresh_token)
         │
         ▼
auth_service.rotate_refresh
   1. decode JWT (verifica firma + scadenza)
   2. carica RefreshToken per jti
   3. confronta token_hash con hash_secret(token in cookie)
   4. se rt.revoked_at != None → reuse detection:
        - revoca TUTTI i refresh dell'utente (chain-revoke)
        - audit "auth.refresh.reuse_detected"
        - 401 token_reused
   5. emette nuovo (access, refresh), salva nuovo RefreshToken,
      marca il vecchio come revoked + replaced_by_id
   6. set-cookie nuovi, audit "auth.refresh.success"
```

## Flusso autenticazione iniziale

```
POST /api/v1/auth/login {email, password}
   │
   ├─ rate-limit slowapi: 5/min/IP
   ├─ lockout: se user.locked_until > now → 429 account_locked
   ├─ verifica password (bcrypt)
   │     ├─ ok    → audit success, set cookies, last_login_at = now
   │     └─ fail  → failed_login_count++, eventuale locked_until+= 15m,
   │                audit failure, COMMIT, raise 401 invalid_credentials
   │                  (commit prima di raise: lockout deve persistere)
   ▼
GET /api/v1/auth/me
   restituisce { user, organizations: [{id,name,role,permissions}], is_platform_admin }
   le permissions sono GIÀ risolte (default ⊕ override org ⊕ override membership)
```

## Risoluzione permessi (algoritmo)

Funzione `app.core.permissions.resolve_permissions(db, user, org_id)`:

```
if user.is_platform_admin:
    return ALL_PERMISSION_CODES                # bypass globale

m = membership(user, org_id)                   # 403 not_a_member se assente
base = role_permissions[m.role_id]             # set di codici default

for (code, granted) in organization_role_permissions[org_id, m.role_id]:
    if granted: base.add(code)
    else:       base.discard(code)

for (code, granted) in membership_permission_overrides[m.id]:
    if granted: base.add(code)
    else:       base.discard(code)

return base
```

I vincoli "creator non può perdere `permission:manage` né `org:transfer_creator`"
sono **server-side guards** in `permission_service` (non nel resolver), così il
resolver resta puro.

## Flusso upload immagini

```
multipart/form-data → UploadFile
   │
   ▼
file_service.save_upload_image(upload, subdir):
   1. valida content-type (image/png|jpeg|webp)
   2. legge bytes, valida dimensione max (env UPLOAD_MAX_MB)
   3. PIL.Image.open(BytesIO) → ImageOps.exif_transpose (strip EXIF)
   4. ridimensiona se max(w,h) > 4096
   5. salva nuovo file con UUID (no nome utente) sotto uploads/<subdir>
   6. ritorna path pubblico /uploads/<subdir>/<uuid>.<ext>
   ↳ ogni step può lanciare ValidationAppError (mai TraceBack al client)
```

I modelli salvano solo il path relativo. Il frontend usa `<img src={path}>`
direttamente: in dev il proxy Vite reindirizza a `:8000`, in prod nginx fa
proxy_pass del prefisso `/uploads/`.

## Worker async (lifespan-managed)

Il backend usa **worker single-instance** registrati nel `lifespan` di
FastAPI. Vivono come `asyncio.Task` di lungo periodo, condividono il
process del backend, e si fermano sullo shutdown.

```
app.main lifespan:
   await avatar_clip_worker.start_worker()       # MiniMax video gen
   await course_document_worker.start_worker()   # Pre-processing AI doc
   await course_architecture_worker.start_worker()  # Fase 1 architettura
   yield
   await course_architecture_worker.stop_worker()
   await course_document_worker.stop_worker()
   await avatar_clip_worker.stop_worker()
```

**Pattern condiviso** (vedi `app/services/avatar_clip_worker.py` per il
template):

```python
_task: asyncio.Task | None = None
_stop_event = asyncio.Event()

async def start_worker():
    global _task
    _stop_event.clear()
    _task = asyncio.create_task(_run_loop())

async def stop_worker():
    _stop_event.set()
    if _task: await _task

async def _run_loop():
    while not _stop_event.is_set():
        try:
            await _tick()
        except Exception:
            log.exception("worker_tick_error")
        await asyncio.wait_for(_stop_event.wait(), timeout=POLL_INTERVAL)
```

`_tick()` apre la propria `AsyncSession` (i worker non sono dentro un
contesto request, quindi non possono usare il dep `get_db`). Ogni worker
gestisce la sua tabella di stato `pending`/`processing`/`ready`/`failed`,
con auto-resume al boot delle righe non terminali.

> **Single-instance**: i worker assumono un solo backend istanziato. Per
> scalare in orizzontale serviranno lock distribuiti (advisory lock di
> Postgres o coda esterna). Non è ancora un problema operativo.

## Frontend: data flow

```
component   ──▶ TanStack Query (cache, retry, background refetch)
                  │
                  ▼
              api/<dominio>.ts ──▶ axios apiClient (withCredentials, baseURL)
                                       │
                                       ├─ 401 e non /auth/refresh → tenta refresh, retry
                                       ▼
                                FastAPI backend
```

L'`AuthContext` chiama `/auth/me` all'avvio e dopo login. Il risultato è
disponibile a tutti i componenti via `useAuth()`. Le permission gate consultano
`me.organizations.find(o => o.id === orgId).permissions`, che è già il set
risolto lato server.

## Decisioni architetturali importanti

- **Cookie HttpOnly per JWT**: previene XSS dal rubare i token. Il refresh ha
  `path=/api/v1/auth/refresh` per limitarne la trasmissione.
- **Refresh token rotation**: ogni refresh emette un nuovo token, il vecchio
  diventa invalido. Reuse-detection chain-revoca tutta la catena.
- **Soft delete organizzazioni**: `deleted_at IS NULL` filtra le liste; gli
  audit log e i file caricati restano per ricostruire la storia.
- **Audit log append-only**: nessuna API permette UPDATE/DELETE; manipolazioni
  sono possibili solo via SQL (DBA). In futuro si può rendere append-only a
  livello DB con trigger.
- **Permessi calcolati ad ogni richiesta**: nessuna cache cross-request.
  L'overhead è 3 query indicizzate; trascurabile per gli SLA dell'app.
- **CSRF leggera**: cookie `SameSite=Lax` + check `Origin/Referer` per metodi
  mutating. Niente token CSRF separato (basta per il modello cookie+SameSite).
