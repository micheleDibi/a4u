# 05 — Security

Posture: production-ready dal primo scaffold. Le mitigazioni elencate sono già
attive nel codice; non sono "TODO".

## Autenticazione

- **Hashing password**: `bcrypt` con `rounds=12` via `passlib.CryptContext`.
- **Policy password** (`is_password_strong`): ≥ 10 caratteri, almeno una
  maiuscola, almeno una cifra. Validata da Pydantic in `UserCreateAdmin` e
  `InvitationAcceptRequest`.
- **JWT**: HS256, segreto ≥ 32 byte (validato da Pydantic). Claim:
  `sub` (UUID utente), `type` (`access` | `refresh`), `iat`, `exp`,
  `jti` (solo refresh, ricerca in tabella per revoca).
- **Cookie**:
  - `access_token`: HttpOnly, SameSite=Lax, Secure (in prod), path=/, TTL 15 min.
  - `refresh_token`: HttpOnly, SameSite=Lax, Secure (in prod),
    **path=`/api/v1/auth/refresh`** (limita la trasmissione), TTL 7 giorni.
- **Refresh rotation**: ogni `/auth/refresh` emette un nuovo refresh, il vecchio
  è marcato `revoked_at` e collegato al successore via `replaced_by_id`.
- **Reuse-detection**: se un refresh già revocato viene riusato, **chain-revoke**
  di tutti i refresh di quell'utente; risposta 401 `token_reused`.
- **Lockout login**: dopo `LOGIN_LOCKOUT_THRESHOLD` (default 10) tentativi
  falliti per email, l'utente è bloccato per `LOGIN_LOCKOUT_MINUTES` (default 15).
  Tentativi e lockout vengono **committati** anche in caso di errore (commit
  esplicito prima di `raise`).
- **Rate limit**: `slowapi` con bucket per IP. Limiti specifici:
  - `/auth/login`: 5/min/IP.
  - `/auth/refresh`: 30/min/IP.
  - `/system/log-client`: 60/min/IP.
  - default globale: 200/min.
- **Audit login**: `auth.login.success`, `auth.login.failure`, `auth.login.locked`,
  `auth.refresh.success`, `auth.refresh.reuse_detected`, `auth.logout`.

## CSRF

Cookie `SameSite=Lax` previene la maggior parte degli attacchi CSRF. In
aggiunta, `CsrfOriginMiddleware` rifiuta richieste mutating (`POST/PUT/PATCH/
DELETE`) il cui `Origin` o `Referer` non corrisponde a `FRONTEND_ORIGIN`.
Se non c'è né `Origin` né `Referer`, è ammesso solo se il client passa un
`Authorization: Bearer ...` (CLI/test).

## Headers di sicurezza

`SecurityHeadersMiddleware` aggiunge a ogni response:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: geolocation=(), microphone=(), camera=()`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` (solo prod)
- `Content-Security-Policy: default-src 'self'; img-src 'self' data: blob:;
  style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self';
  frame-ancestors 'none'`

## Upload immagini e audio

`file_service.save_upload_image()`:

- Controlla `Content-Type` ∈ `{image/png, image/jpeg, image/webp}`. **SVG escluso**
  (rischio XSS).
- Limita dimensione a `UPLOAD_MAX_MB`.
- Apre con Pillow (`Image.open`); UnidentifiedImageError → 400.
- `ImageOps.exif_transpose` rispetta orientamento ed elimina i metadata EXIF
  (geolocalizzazione, modello fotocamera, ecc.).
- Ridimensiona se `max(w, h) > 4096`.
- Ri-encoda in PNG/JPEG (per JPEG converte a `RGB` se necessario).
- Salva con nome `<uuid>.ext`. **Mai usare il filename del client** (path
  traversal, caratteri speciali).
- `_ensure_within(root, target)` verifica che il path risolto sia dentro
  `UPLOAD_DIR` (anti path-traversal).

`file_service.save_upload_audio()` (avatar utente):

- Whitelist MIME: `audio/webm`, `audio/ogg`, `audio/mpeg`, `audio/mp4`,
  `audio/wav`, `audio/x-wav`, `audio/m4a`, `audio/x-m4a`, `audio/aac`.
- Limita dimensione a `AVATAR_AUDIO_MAX_MB` (default 10MB).
- Salva con nome `<uuid>.<ext>` derivata dal MIME (mai dal client) sotto
  `uploads/avatars/<user_id>/`.
- Stesso `_ensure_within` per anti path-traversal.

`file_service.save_upload_document()` (documenti corso):

- Whitelist MIME: `application/pdf`, `application/msword` (DOC),
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
  (DOCX), `text/plain` (TXT), `text/markdown` (MD), `application/rtf` /
  `text/rtf` (RTF). **Niente DOCX con macro** (`.docm`).
- Limita dimensione a 25 MB (hardcoded in `add_document` service, supera
  `UPLOAD_MAX_MB` perché i PDF didattici tipicamente sono > 5 MB).
- Salva con nome `<uuid>.<ext>` derivata dal MIME sotto
  `uploads/courses/<course_id>/`. Mai usare il filename del client.
- Stesso `_ensure_within` per anti path-traversal.
- L'estrazione testo (`document_extraction_service`) usa librerie pure
  Python (pdfplumber, python-docx, docx2txt, striprtf) che NON
  eseguono macro né script embedded.

## SQL injection / ORM

Tutto via SQLAlchemy 2 ORM con statement parametrizzati. Nessuna concatenazione
di stringhe SQL nel codice applicativo.

## XSS

React fa escape automatico del testo. Non sono usati `dangerouslySetInnerHTML`
nel codebase. Le immagini caricate sono PNG/JPEG/WEBP riencodate, non SVG.
La CSP `script-src 'self'` blocca script inline.

## Prompt injection / sanitizzazione input LLM

L'input utente non-fidato che finisce in una conversazione con un LLM (oggi
la chat assistente **Nova**) passa per `app/core/prompt_safety.py`, che è
la **difesa di primo livello** contro i tentativi di prompt injection. Il
modello a tre livelli è:

1. `prompt_safety.sanitize_user_input(text, max_length=2000)`: rimuove i
   pattern noti di injection (sostituendoli con `[rimosso]`) e tronca a 2000
   caratteri. I pattern sono compilati una volta sola, case-insensitive, e
   coprono varianti IT/EN/tecniche: "ignora le istruzioni precedenti" /
   "ignore previous instructions", "sei ora …" / "you are now", "act as a …"
   / "agisci come", "system prompt", "dimentica tutto" / "forget everything",
   `jailbreak`, `DAN mode`, "developer mode" / "modalità sviluppatore",
   "reveal/rivela il prompt", ecc.
2. **System prompt** dell'LLM con regole di rifiuto esplicite (out-of-scope,
   manipolazione).
3. Filtro lato **modello** stesso.

`prompt_safety.contains_injection_attempt(text)` è usata in parallelo per
l'audit: in `nova_service`, se l'input contiene un pattern noto, viene
loggato `nova_injection_attempt` (con `user_id`, `page`, lunghezza — **mai**
il contenuto) e l'utente riceve una risposta standard **senza** alcuna
chiamata a OpenAI. È una mitigazione, non una garanzia: riduce la superficie
di attacco più ovvia, non sostituisce le altre due linee di difesa. Vedi
[Backend 02 — Core](backend/02-core.md) (`app/core/prompt_safety.py`).

## Gestione utenti (platform admin) e self-service

### Lato platform admin

Il router `admin_users` (`/admin/users`, gate `PlatformAdmin`) gestisce il
ciclo di vita degli account. **Nessuna eliminazione definitiva**: rimuovere
un account significa disattivarlo (`is_active=False`), operazione reversibile.
Le invarianti di sicurezza vivono in `user_admin_service`:

- **Self-guard**: un admin **non** può disattivare il proprio account
  (`409 cannot_deactivate_self`) né rimuoversi il ruolo di platform admin
  (`409 cannot_demote_self`) — evita un lockout immediato.
- **Last-active-admin**: la piattaforma deve sempre avere almeno un platform
  admin **attivo**. La demozione/disattivazione dell'unico rimasto è bloccata
  (`409 last_active_admin`).
- **Set password (reset manuale, no SMTP)**: l'admin imposta una password
  robusta per l'utente target (`POST /admin/users/{user_id}/password`).
  L'operazione **revoca tutti i refresh token vivi** dell'utente (forza il
  re-login); gli access token JWT già emessi restano validi fino alla
  scadenza del TTL (~15 min), coerente con l'architettura stateless degli
  access token.
- **Audit**: ogni azione scrive `user.create`, `user.update`,
  `user.password_reset` con attore, target e campi modificati.

### Self-service profilo personale

Il router `auth` (`/auth/me*`, solo autenticazione) consente all'utente di
gestire il proprio account:

- **Nome** (`PATCH /auth/me`): nessuna re-auth (azione a basso rischio).
- **Cambio email** (`POST /auth/me/change-email`): richiede la password
  attuale (`401 invalid_current_password`). Le sessioni **restano valide**
  (l'email è un identificatore, non una credenziale).
- **Cambio password** (`POST /auth/me/change-password`, rate-limit `5/min`):
  richiede la password attuale, valida la nuova con `is_password_strong` e
  rifiuta password identica all'attuale (`422 password_unchanged`). **Non**
  revoca le proprie sessioni: l'utente resta loggato sul device corrente
  (asimmetria voluta rispetto al reset lato admin, che invece forza il
  re-login perché implica un possibile compromesso dell'account).
- **Audit**: `user.profile.update`, `user.email.change`,
  `user.password.change`.

## Audit log

Tabella `audit_logs` (append-only via convenzione applicativa):

- Login (success/failure/locked/refresh/reuse/logout).
- Org create/update/delete/transfer-creator.
- Membership create/role-change/remove.
- Permission updates a livello globale, organizzazione, utente.
- Invitation create/accept.
- Template slide/PDF create/update/delete.
- Avatar utente create/update/delete e regenerate clip.
- Avatar clip prompt config admin create/update/delete/reorder.
- Corso create/update/delete/assignee-change.
- Documento corso upload/delete/reprocess + esiti del worker
  (`course.document.summary.{ready,failed}`).
- Architettura corso generate/approve + esiti del worker
  (`course.architecture.{generated,failed}`).
- CRUD manuale moduli/lezioni + reorder + AI generate-lessons.
- Gestione utenti platform-admin: `user.create`, `user.update`,
  `user.password_reset`.
- Self-service profilo: `user.profile.update`, `user.email.change`,
  `user.password.change`.

Ogni riga include `request_id`, `actor_user_id`, `organization_id` (se rilevante),
`metadata` JSONB con dettagli, `ip`, `user_agent`.

## Limiti DB

- Pool con `pool_pre_ping=True` evita connessioni stale.
- Postgres `statement_timeout=30s` (configurato in `docker-compose.yml`).
- Tutti gli indici critici (UNIQUE su email, FK con ondelete corretto, indici
  per query frequenti su `(organization_id, created_at)`, `token_hash`, ecc.).

## Bootstrap admin

`db.seed.ensure_seed`:
- Se `BOOTSTRAP_ADMIN_EMAIL` e `BOOTSTRAP_ADMIN_PASSWORD` sono valorizzati e
  l'utente non esiste → crea con `is_platform_admin=True`.
- Se l'utente esiste ma non è admin → lo promuove (idempotente).
- Se le env sono vuote, salta.

In produzione **non** usare le credenziali di default. Cambiale prima della
prima migrazione e usa un secret manager.

## Threat model (sintesi)

| Minaccia | Mitigazione principale |
|---|---|
| Credential stuffing | Rate-limit per IP + lockout per email + audit |
| Token theft (XSS) | Cookie HttpOnly, refresh con rotation+reuse-detection |
| Cookie theft (network) | `Secure` flag in prod, HTTPS, HSTS |
| CSRF | SameSite=Lax + Origin/Referer middleware |
| Path traversal upload | UUID filename + `_ensure_within` |
| EXIF leak | Pillow re-encode con `exif_transpose` |
| SQL injection | ORM only |
| XSS | React + CSP + no SVG upload |
| Refresh replay | Reuse-detection con chain-revoke |
| Privilege escalation | Server-side checks (`creator` non perde permessi critici) |
| Admin self-lockout | Self-guard + last-active-admin in `user_admin_service` |
| Prompt injection (LLM) | `prompt_safety` (sanitize + detect) + system prompt + filtro modello |
| Information leak in errori | Exception handlers retornano `{code,message}` senza stack |
| Slow query DOS | `pool_pre_ping`, `statement_timeout=30s` |
| Mass-targeting login | Rate-limit + lockout + audit |
