# Backend 02 — `app/core/`

Modulo centrale: configurazione, sicurezza, logging, dipendenze FastAPI,
errori, audit, rate-limit, risoluzione permessi.

---

## `app/core/__init__.py`

Vuoto (package marker).

---

## `app/core/config.py`

**Scopo**: lettura e validazione delle variabili d'ambiente con
`pydantic-settings`. Singleton in cache.

### Classi

#### `class Settings(BaseSettings)`

Configura `model_config = SettingsConfigDict(env_file=".env",
env_file_encoding="utf-8", case_sensitive=False, extra="ignore")`.

**Campi**: vedi [04 — Configuration](../04-configuration.md) per la lista
completa con descrizione.

**Validatori**:

- `_empty_string_to_none(value)`: `cookie_domain` letta da env stringa vuota
  diventa `None`.
- `_none_if_empty(value)`: stesso pattern (stringa vuota → `None`) applicato
  a **tutte le credenziali/segreti opzionali** (`config.py:318-337`):
  `sentry_dsn`, `bootstrap_admin_email`, `bootstrap_admin_password`,
  `minimax_api_key`, `openai_api_key`, `runpod_api_key`,
  `runpod_tts_endpoint_id`, `runpod_musetalk_endpoint_id`, `r2_endpoint`,
  `r2_bucket`, `r2_access_key_id`, `r2_secret_access_key`.

**Proprietà**:

- `upload_root: Path` → `Path(self.upload_dir).resolve()`.
- `is_production: bool` → `self.env == "production"`.
- `cors_allow_origins: list[str]` → `[self.frontend_origin]`.

**Vincoli**: `jwt_secret` ha `min_length=32`, l'avvio fallisce se più corto.

### Funzioni

#### `get_settings() -> Settings`

Decorata con `@lru_cache(maxsize=1)`: restituisce lo stesso oggetto in tutto
il processo. Va invocata in cima ai moduli che leggono config.

---

## `app/core/course_phase_order.py`

**Scopo**: definire l'**ordine totale monotono** degli stati di
`Course.status` e impedirne la regressione. La pipeline corsi avanza per
fasi sequenziali (architecture → lessons structure → content → slides →
speech → video → avatar_video → published/archived); dentro la stessa fase
vale `pending < ready < approved` (video/avatar_video non hanno `approved`,
vedi `app/schemas/course.py`).

### Costanti

#### `COURSE_STATUS_RANK: dict[str, int]`

Mappa ogni stato al suo rank intero (`draft=0` … `archived=21`,
`course_phase_order.py:27-50`). È la **fonte di verità** dell'ordinamento
delle fasi:

- usata da `advance_course_status` per il gating monotono lato backend;
- **mirrorata 1:1 lato frontend** in
  `frontend/src/pages/org/courses/components/CoursePhaseStepper.tsx`
  (`COURSE_STATUS_RANK` + helper `isCourseAtLeast`), che la usa per il
  gating delle 4 macro-fasi e delle sub-tab dell'editor (stepper a 4 fasi).

> **Invariante di drift**: i due `COURSE_STATUS_RANK` (questo modulo +
> `CoursePhaseStepper.tsx`) sono tenuti allineati a mano. Aggiungendo o
> rinumerando uno stato, aggiornare **entrambi**.

> **Nota**: il gating di editabilità lato backend **non** usa questo rank.
> `course_architecture_crud.EDITABLE_STATUSES`
> (`course_architecture_crud.py:56-69`) è una whitelist letterale hard-coded
> e il modulo non importa `course_phase_order`. Il ragionamento per-rank
> sull'editabilità (es. "fase ≥ X") vive **solo nel frontend**
> (`CoursePhaseStepper.tsx` + i `disabled` dei `TabsTrigger` in
> `CourseEditorPage.tsx`).

### Funzioni

#### `advance_course_status(course: Course, new_status: str) -> None`

Assegna `course.status = new_status` **solo se non è una regressione di
fase**, ossia se `COURSE_STATUS_RANK[new_status] >= COURSE_STATUS_RANK[current]`
(`course_phase_order.py:53-68`). Stati ignoti hanno rank `0`.

Chiamata dai 6 `_recompute_course_*_status` dei service di lezione
(`course_lesson_{structure,content,slides,speech,video,avatar_video}_service.py`,
es. `course_lesson_content_service.py:1083-1114`) e dal
`course_duplication_service`. Ogni service ricalcola lo stato del corso in
base allo stato delle proprie lezioni, ma non può riportarlo indietro:
previene il bug "approvo le slide → poi modifico/approvo un contenuto → il
corso torna a `content_approved` → non posso più generare il discorso".

Vedi [courses/04 — Manual editing](../courses/04-manual-editing.md) per la
whitelist `EDITABLE_STATUSES` (definita esplicitamente, non derivata da
questo ordinamento).

---

## `app/core/logging.py`

**Scopo**: configurare structlog (JSON in prod, console in dev). Esporre
context-vars per arricchire i log con `request_id` e `user_id`.

### Variabili context-var

- `request_id_ctx: ContextVar[str | None]` (default `None`): popolata da
  `RequestIDMiddleware`.
- `user_id_ctx: ContextVar[str | None]` (default `None`): popolata da
  `get_current_user` quando l'utente è autenticato.

### Funzioni

#### `_add_context_vars(_, __, event_dict)`

Processor structlog: legge le context-var e inietta `request_id`/`user_id`
nel dict del log se non già presenti.

#### `configure_logging(settings: Settings) -> None`

Configura structlog + stdlib `logging`.

- Processor chain comuni: `merge_contextvars`, `add_log_level`,
  `_add_context_vars`, `TimeStamper(iso, utc)`, `StackInfoRenderer`.
- Renderer: `JSONRenderer` se `log_format=json`, altrimenti
  `ConsoleRenderer(colors=True)`.
- Bound logger filtra a `LOG_LEVEL`.
- Handler stdout (`StreamHandler`).
- Reindirizza i logger noti (`uvicorn`, `uvicorn.access`, `uvicorn.error`,
  `sqlalchemy.engine`) sullo stesso handler con livelli sensati.

#### `get_logger(name: str | None = None) -> Any`

Wrapper di `structlog.get_logger`.

---

## `app/core/errors.py`

**Scopo**: classi di errore di dominio + handler HTTP che ritornano JSON
consistente senza trapelare stack trace.

### Classi (gerarchia)

```
Exception
└── AppError
    ├── NotFoundError              (404, code="not_found")
    ├── ConflictError               (409, code="conflict")
    ├── ValidationAppError          (422, code="validation_error")
    ├── AuthenticationError         (401, code="authentication_required")
    ├── PermissionDeniedError       (403, code="permission_denied")
    └── RateLimitedError            (429, code="rate_limited")
```

#### `class AppError(Exception)`

**Costruttore**: `AppError(message: str, *, code: str | None = None,
meta: dict[str, Any] | None = None)`.

**Attributi**:
- `status_code: int` (default 400)
- `code: str` (default `"app_error"`)
- `message: str`
- `meta: dict`

Le sottoclassi sovrascrivono `status_code` e `code` come default; possono
essere ulteriormente personalizzati al momento del raise.

### Funzioni

#### `_payload(code, message, *, meta=None) -> dict`

Costruisce il dict risposta: `{ code, message, request_id?, meta? }`. Legge
`request_id` da `request_id_ctx`.

#### `register_exception_handlers(app: FastAPI) -> None`

Registra 5 handler:

1. `AppError`: status di classe, body `_payload(code, message, meta)`,
   log `warning`.
2. `StarletteHTTPException`: status dell'eccezione, code `"http_<status>"`,
   log `warning`.
3. `RequestValidationError` (422): code `"validation_error"`,
   `meta.errors = exc.errors()`, log `info`.
4. `IntegrityError` (409): code `"conflict"`, log `warning` con
   `error=str(exc.orig)`.
5. Catch-all `Exception` (500): code `"internal_error"`, log `error` con
   `exc_info=True`. Mai stack trace al client.

---

## `app/core/security.py`

**Scopo**: hashing password, emissione/decodifica JWT, generazione di token
opachi sicuri, policy password.

### Costanti

- `_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto",
  bcrypt__rounds=12)`.
- `TokenType = Literal["access", "refresh"]`.

### Funzioni

#### `hash_password(password: str) -> str`

Restituisce bcrypt hash della password.

#### `verify_password(password: str, password_hash: str) -> bool`

Verifica con `passlib`. Restituisce `False` su qualsiasi eccezione.

#### `_now() -> datetime`

`datetime.now(tz=UTC)`.

#### `create_access_token(*, subject: str, extra: dict[str, Any] | None = None) -> str`

Costruisce JWT con claim `sub`, `type="access"`, `iat`, `exp` (ttl da
`access_token_ttl_seconds`). Eventuali claim `extra` vengono fusi (i campi
ufficiali non sono sovrascrivibili).

#### `create_refresh_token(*, subject: str, jti: uuid.UUID | None = None) -> tuple[str, uuid.UUID, datetime]`

Genera nuovo refresh token. Genera (o usa il fornito) `jti`. Calcola
`expires_at`. Restituisce `(jwt_string, jti, expires_at)`. Il chiamante
salva la riga in `refresh_tokens` con `token_hash = hash_secret(jwt)`.

#### `decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]`

Decodifica e verifica firma+scadenza con `jwt.decode`. Mappa eccezioni:

- `ExpiredSignatureError` → `AuthenticationError("Token scaduto.",
  code="token_expired")`.
- `InvalidTokenError` → `AuthenticationError("Token non valido.",
  code="token_invalid")`.

Verifica anche `payload["type"] == expected_type`. Restituisce il payload.

#### `hash_secret(value: str) -> str`

`sha256(value).hexdigest()`. Usato per:
- hashing dei refresh JWT prima di salvarli in DB (così, in caso di leak DB,
  non si hanno token validi);
- hashing dei token di invito.

#### `generate_url_safe_token(nbytes: int = 32) -> str`

`secrets.token_urlsafe(nbytes)`. Generatore CSPRNG. Usato per i token di
invito.

#### `is_password_strong(value: str) -> bool`

Valida che `len >= 10`, almeno una maiuscola, almeno una cifra. Boolean.

---

## `app/core/prompt_safety.py`

**Scopo**: sanitizzare l'input utente non-fidato prima di passarlo a un
LLM, mitigando (non eliminando) i tentativi di **prompt injection**. È la
**difesa di primo livello**: la seconda sono le regole di rifiuto esplicite
nel system prompt dell'LLM, la terza il filtro lato modello stesso. Riusabile
da qualsiasi servizio con conversazione LLM su input non-fidato; attualmente
consumato da `nova_service`. Vedi [05 — Security](../05-security.md).

### Costanti

- `_INJECTION_PATTERNS: tuple[re.Pattern[str], ...]`: pattern noti di
  prompt injection compilati una volta al module-load (case-insensitive,
  `re.IGNORECASE`). Coprono varianti IT/EN/tecniche: "ignora le istruzioni
  precedenti" / "ignore previous instructions", "sei ora …" / "you are now",
  "fai finta di essere" / "pretend to be", "act as a …" / "agisci come",
  "system prompt", "nuove/new instructions", "dimentica tutto" / "forget
  everything", "override your …", `jailbreak`, `DAN mode`, "developer mode" /
  "modalità sviluppatore", "reveal/rivela il prompt/istruzioni", "role-play
  as", "ruolo: …".

### Funzioni

#### `sanitize_user_input(text: str, max_length: int = 2000) -> str`

Sanifica `text`: sostituisce ogni match dei pattern con `[rimosso]`, tronca a
`max_length` caratteri e fa `strip()`. Restituisce stringa vuota se l'input
non è una stringa. NON è una difesa completa: riduce solo la superficie di
attacco più ovvia.

#### `contains_injection_attempt(text: str) -> bool`

`True` se `text` contiene almeno un pattern di injection. Usata per
audit/logging (visibilità su chi tenta il bypass): non blocca la chiamata,
perché `sanitize_user_input` neutralizza comunque l'input. Restituisce
`False` se l'input non è una stringa. In `nova_service` un esito positivo fa
loggare `nova_injection_attempt` e restituire la risposta standard senza
chiamare OpenAI.

---

## `app/core/rate_limit.py`

**Scopo**: rate-limiting con `slowapi`. Bucket per IP.

### Esporta

- `limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])`.
- `rate_limit_handler(request, exc)`: handler async che logga
  `rate_limited` e ritorna JSON 429 con `code="rate_limited"`,
  `message`, `request_id`.

I limiti specifici per endpoint sono applicati con `@limiter.limit("5/minute")`
direttamente sui router (vedi `auth.py`, `system.py`).

---

## `app/core/audit.py`

**Scopo**: helper per scrivere righe in `audit_logs`. Append-only,
mai blocca l'azione di business.

### Esporta

- `log = get_logger("app.audit")`.
- `write_audit(...)`: async, vedi sotto.

### Funzione

#### `write_audit(session, *, action, actor_user_id=None, organization_id=None, target_type=None, target_id=None, metadata=None, ip=None, user_agent=None) -> None`

Crea un `AuditLog` e fa `session.flush()`. Cattura ogni eccezione e logga
`audit_write_failed` (mai blocca il flusso). Legge `request_id_ctx` per
popolare il campo `request_id`.

**Parametri**:

- `session`: `AsyncSession`.
- `action`: codice azione, es. `"organization.create"`, `"auth.login.success"`.
- `actor_user_id`: UUID dell'attore (può essere None per eventi di sistema).
- `organization_id`: UUID dell'org coinvolta.
- `target_type`/`target_id`: opzionali, identificano l'oggetto target.
- `metadata`: JSONB con dettagli.
- `ip`/`user_agent`: opzionali.

---

## `app/core/deps.py`

**Scopo**: dipendenze FastAPI riusabili: sessione DB, utente corrente, admin
piattaforma. Vincola le rotte protette.

### Esporta

- `get_db`: async generator dependency.
- `DbSession = Annotated[AsyncSession, Depends(get_db)]`.
- `get_current_user`: async dependency.
- `CurrentUser = Annotated[User, Depends(get_current_user)]`.
- `require_platform_admin`: dependency.
- `PlatformAdmin = Annotated[User, Depends(require_platform_admin)]`.

### Funzioni

#### `get_db() -> AsyncIterator[AsyncSession]`

Apre una sessione async dal `async_session_factory`. Pattern try/yield/commit
+ except/rollback. Usata come dipendenza FastAPI.

#### `get_current_user(request, db, access_token: str | None = Cookie(alias="access_token")) -> User`

1. Se manca il cookie, prova a leggere `Authorization: Bearer ...` (utile per
   CLI/test).
2. Se ancora assente, alza `AuthenticationError("Autenticazione richiesta.",
   code="not_authenticated")`.
3. Decodifica con `decode_token(..., expected_type="access")`.
4. Estrae `payload["sub"]` come UUID; mappa errori a `token_invalid`.
5. `db.get(User, user_id)`. Se `None` o `not is_active`, alza
   `AuthenticationError("Utente non valido o disattivato.",
   code="user_inactive")`.
6. Setta `user_id_ctx` per arricchire i log successivi.
7. Restituisce `User`.

#### `require_platform_admin(user: CurrentUser) -> User`

Verifica `user.is_platform_admin`, altrimenti
`PermissionDeniedError(code="platform_admin_required")`. Restituisce `User`.

---

## `app/core/permissions.py`

**Scopo**: codici permessi, codici ruoli, default per ruolo, resolver
permessi, dipendenza `require(*codes)`.

### Costanti

- `class P`: contiene tutti i codici permesso (`MEMBER_VIEW`,
  `MEMBER_INVITE`, ..., `ORG_UPDATE`).
- `ALL_PERMISSION_CODES: tuple[str, ...]`.
- `class R`: codici ruoli (`CREATOR`, `ORG_ADMIN`, `MANAGER`, `MEMBER`).
- `ROLE_RANK: dict[str, int]`.
- `ROLE_NAME_IT: dict[str, str]`: etichette italiane.
- `ROLE_DEFAULT_PERMISSIONS: dict[str, set[str]]`: codici → set di codici
  permessi default.
- `CREATOR_REQUIRED_PERMISSIONS: set[str] = {permission:manage,
  org:transfer_creator}`.

### Funzioni

#### `_resolve_for_membership(db, *, membership) -> tuple[set[str], OrganizationRole]`

Helper privato. Carica il `OrganizationRole`, calcola permessi base
(`role_permissions`) e applica gli override `organization_role_permissions`
e `membership_permission_overrides`. Ritorna `(set, role)`.

#### `get_membership(db, *, user_id, organization_id) -> Membership | None`

Query `Membership` per la coppia. Ritorna l'oggetto o `None`.

#### `resolve_permissions(db, *, user, organization_id) -> set[str]`

Algoritmo descritto in [06 — Permissions](../06-permissions.md).
**Bypass per platform admin**. **403 not_a_member** se assente.

#### `require(*codes: str)` factory di dependency

Restituisce `Depends(dependency)` dove `dependency` è una funzione async che:

1. Estrae `org_id` dalla path (con `Path(...)`).
2. Risolve `granted = await resolve_permissions(...)`.
3. Verifica che tutti i `codes` siano in `granted`.
4. Se mancano: `PermissionDeniedError(code="permission_denied",
   meta={"missing": [...]})`.
5. Restituisce `granted` (utile se il router vuole consultarlo).

#### `require_membership()` factory

Variante che richiede solo l'appartenenza. Per platform admin restituisce
`None`. Altrimenti carica `Membership` e 403 se assente.
