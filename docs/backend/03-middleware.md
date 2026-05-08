# Backend 03 — `app/middleware/`

Quattro middleware ASGI custom. L'ordine di registrazione in `main.py` è
inverso all'ordine di esecuzione (FastAPI/Starlette: l'ultimo aggiunto è il
primo a vedere la richiesta).

Ordine di esecuzione effettivo sull'ingresso: **CORSMiddleware →
RequestIDMiddleware → AccessLogMiddleware → CsrfOriginMiddleware →
SecurityHeadersMiddleware → handler → response → middleware in ordine
inverso**.

---

## `app/middleware/__init__.py`

Vuoto.

---

## `app/middleware/request_id.py`

**Scopo**: assegnare un ID univoco a ogni richiesta, propagarlo in structlog
e nelle response per tracciamento.

### Costanti

- `HEADER = "X-Request-ID"`.

### Classi

#### `class RequestIDMiddleware(BaseHTTPMiddleware)`

`async def dispatch(request, call_next) -> Response`:

1. Legge `X-Request-ID` dall'header. Se assente, genera `uuid.uuid4().hex`.
2. Setta `request_id_ctx` (context-var di `app.core.logging`).
3. Salva `rid` su `request.state.request_id` per accesso da handler.
4. Esegue `call_next`.
5. In `finally`: reset del context-var.
6. Aggiunge `X-Request-ID` alla response.

---

## `app/middleware/access_log.py`

**Scopo**: log strutturato di ogni richiesta HTTP con durata.

### Logger

- `log = get_logger("app.access")`.

### Classi

#### `class AccessLogMiddleware(BaseHTTPMiddleware)`

`async def dispatch(request, call_next) -> Response`:

1. `started = time.perf_counter()`.
2. Esegue `call_next`. In caso di eccezione il log viene comunque emesso
   (durata calcolata in `finally`), default `status=500`.
3. Logga `http_request` con: `method`, `path`, `status`, `duration_ms`
   (round 2 decimali), `ip` (`request.client.host`), `user_agent`.

> Il `request_id` arriva automaticamente dal context-var.

---

## `app/middleware/security_headers.py`

**Scopo**: aggiungere headers di sicurezza alla response.

### Classi

#### `class SecurityHeadersMiddleware(BaseHTTPMiddleware)`

`async def dispatch(request, call_next) -> Response`:

1. Esegue `call_next`.
2. Setta (con `setdefault` per non sovrascrivere se già presenti):
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Permissions-Policy: geolocation=(), microphone=(), camera=()`
   - In produzione: `Strict-Transport-Security: max-age=31536000;
     includeSubDomains`.
   - `Content-Security-Policy: default-src 'self'; img-src 'self' data: blob:;
     style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self';
     frame-ancestors 'none'`.

> `style-src 'unsafe-inline'` è necessaria per Material UI (emotion). Se
> in futuro si usa `cssNonce`, restringere.

---

## `app/middleware/csrf.py`

**Scopo**: difesa CSRF leggera basata su Origin/Referer per richieste
mutating.

### Costanti

- `UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}`.

### Classi

#### `class CsrfOriginMiddleware(BaseHTTPMiddleware)`

`async def dispatch(request, call_next) -> Response`:

1. Se il metodo non è in `UNSAFE_METHODS` → passa.
2. Carica `settings`. `allowed = FRONTEND_ORIGIN` (senza trailing slash).
3. Lettura `Origin` (priorità) e `Referer` dagli header.
4. Validazione:
   - `Origin == allowed` → ok.
   - `Referer` inizia con `allowed` → ok.
   - Nessuno dei due ma c'è un `Authorization: Bearer ...` → ok (CLI/test).
   - Altrimenti: 403 JSON `{ code: "csrf_origin_invalid", message }`.
5. `call_next`.

> Il check è in addition al SameSite=Lax dei cookie. Per richieste GET
> (safe) non si applica.
