# 04 — Configuration

Esistono **due** soli file di env:

- **`.env`** alla root del repo — letto da `docker compose` (per i `${VAR}`
  in `docker-compose.yml`) e dal backend FastAPI (`pydantic-settings` punta
  qui via path assoluto in `app/core/config.py`).
- **`frontend/.env`** — letto da Vite, che ha la sua convenzione (carica
  solo dalla project root del frontend).

Il backend rifiuta l'avvio se `JWT_SECRET` ha lunghezza < 32 byte
(validatore Pydantic). In container produzione le variabili arrivano dal
blocco `environment:` di `docker-compose.prod.yml`, non dal file.

## Variabili nel `.env` di root

### Postgres (lette da docker-compose)

| Variabile | Default | Descrizione |
|---|---|---|
| `POSTGRES_USER` | `a4u` | Utente DB del container. |
| `POSTGRES_PASSWORD` | `a4u_dev_password` | Password. |
| `POSTGRES_DB` | `a4u` | Nome del database iniziale. |
| `POSTGRES_PORT` | `5432` | Porta esposta sull'host. |

### Backend (lette da FastAPI)

| Variabile | Default | Descrizione |
|---|---|---|
| `ENV` | `development` | `development | test | production`. Determina log format, secure cookie, docs swagger pubbliche. |
| `LOG_LEVEL` | `INFO` | Livello log (`DEBUG | INFO | WARNING | ERROR`). |
| `LOG_FORMAT` | `console` | `console` (dev colorato) o `json` (produzione). |
| `DATABASE_URL` | `postgresql+asyncpg://a4u:a4u_dev_password@localhost:5432/a4u` | Connection string SQLAlchemy async. |
| `DATABASE_POOL_SIZE` | `10` | Connessioni base nel pool. |
| `DATABASE_MAX_OVERFLOW` | `20` | Connessioni extra oltre il pool. |
| `JWT_SECRET` | — *(obbligatorio, ≥32 byte)* | Segreto firma JWT. Generare con `openssl rand -hex 48`. |
| `JWT_ALGORITHM` | `HS256` | Algoritmo firma. |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` (15 min) | TTL access token. |
| `REFRESH_TOKEN_TTL_SECONDS` | `604800` (7 gg) | TTL refresh token. |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | Origin consentito CORS. Una sola entry. |
| `COOKIE_DOMAIN` | _(vuoto)_ | Domain cookie. Vuoto → host corrente. |
| `COOKIE_SECURE` | `false` | `true` in produzione (HTTPS richiesto). |
| `UPLOAD_DIR` | `./uploads` | Cartella assoluta o relativa per i file caricati. |
| `UPLOAD_MAX_MB` | `5` | Dimensione massima per file. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | URL base usato per costruire `accept_url` degli inviti. |
| `BOOTSTRAP_ADMIN_EMAIL` | _(vuoto)_ | Se settato + password: l'admin viene creato/promosso al seed. |
| `BOOTSTRAP_ADMIN_PASSWORD` | _(vuoto)_ | Vedi sopra. Soggetta alla policy: ≥10 chars, una maiuscola, un numero. |
| `BOOTSTRAP_ADMIN_FULL_NAME` | `Platform Admin` | Nome dell'utente bootstrap. |
| `RATE_LIMIT_LOGIN_PER_MIN` | `5` | Richieste login per minuto per IP. |
| `LOGIN_LOCKOUT_THRESHOLD` | `10` | Tentativi falliti per bloccare l'account. |
| `LOGIN_LOCKOUT_MINUTES` | `15` | Durata del blocco dopo lockout. |
| `AVATAR_AUDIO_MAX_MB` | `10` | Dimensione massima per il file audio dell'avatar utente. |
| `MINIMAX_API_KEY` | _(vuoto)_ | API key MiniMax. Se vuoto, le clip avatar restano in stato `pending` finché non viene configurata. |
| `MINIMAX_BASE_URL` | `https://api.minimax.io` | Base URL del provider MiniMax. |
| `MINIMAX_VIDEO_MODEL` | `MiniMax-Hailuo-02` | Modello di generazione video. |
| `MINIMAX_CLIP_DURATION` | `6` | Durata in secondi di ogni clip. |
| `MINIMAX_CLIP_RESOLUTION` | `1080P` | Risoluzione richiesta al provider. |
| `MINIMAX_POLL_INTERVAL_SECONDS` | `10` | Intervallo del worker di polling che processa le clip pending/processing. |
| `OPENAI_API_KEY` | _(vuoto)_ | API key OpenAI. Se vuota, il bottone "Completa con AI" + le pipeline corsi rispondono 422 con codice `openai_not_configured`. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Base URL del provider OpenAI (utile per gateway compatibili). |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modello usato per la traduzione automatica i18n. |
| `OPENAI_TRANSLATE_BATCH_SIZE` | `80` | Dimensione del batch di chiavi inviate per richiesta a `/chat/completions`. |
| `OPENAI_SUMMARIZE_MODEL` | `gpt-4o-mini` | Modello usato per il pre-processing dei documenti corso (Appendice A). |
| `OPENAI_SUMMARIZE_MAX_TOKENS` | `8000` | `max_completion_tokens` per le risposte del summarize service. |
| `OPENAI_ARCHITECTURE_MODEL` | `gpt-5.5` | Modello per la generazione architettura corso (Fase 1). |
| `OPENAI_ARCHITECTURE_MAX_TOKENS` | `8000` | `max_completion_tokens` per architettura + lezioni (cap condiviso). |
| `OPENAI_MODULES_LESSONS_MODEL` | `gpt-5.5` | Modello per la generazione AI delle lezioni di un singolo modulo (manual editing). |
| `OPENAI_LESSON_STRUCTURE_MODEL` | `gpt-5.5` | Modello per la struttura delle lezioni (Fase 2). |
| `OPENAI_LESSON_STRUCTURE_MAX_TOKENS` | `6000` | `max_completion_tokens` per ciascun modulo elaborato in Fase 2. |
| `COURSE_DOCUMENT_MAX_CHARS` | `120000` | Troncamento del testo estratto da un documento prima del summarize (~30k token). |
| `COURSE_DOCUMENT_POLL_INTERVAL_SECONDS` | `4` | Intervallo del worker che processa i documenti `pending`/`processing`. |
| `COURSE_ARCHITECTURE_POLL_INTERVAL_SECONDS` | `4` | Intervallo del worker Fase 1 (architettura corso). |
| `COURSE_ARCHITECTURE_DOCUMENTS_CONTEXT_MAX_CHARS` | `60000` | Limite dei summary documento concatenati nel prompt di Fase 1 (gli eccessi vengono troncati). |
| `COURSE_LESSON_STRUCTURE_POLL_INTERVAL_SECONDS` | `4` | Intervallo del worker Fase 2 (struttura lezioni). |
| `COURSE_LESSON_STRUCTURE_MAX_CONCURRENCY` | `5` | Cap moduli elaborati in parallelo dal worker Fase 2 (`asyncio.Semaphore`). Aumenta con cautela in base al tier OpenAI. |
| `COURSE_LESSON_STRUCTURE_DOCUMENTS_CONTEXT_MAX_CHARS` | `30000` | Limite dei summary documento concatenati nel prompt di Fase 2. |
| `SENTRY_DSN` | _(vuoto)_ | Se valorizzato, attiva Sentry su backend. |

## Frontend (`frontend/.env`)

| Variabile | Default | Descrizione |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | Prefisso API. In dev è proxato da Vite a localhost:8000. In prod è servito da nginx. |
| `VITE_UPLOADS_BASE_URL` | `/uploads` | Prefisso upload. Stessa logica di proxy. |
| `VITE_SENTRY_DSN` | _(vuoto)_ | Se valorizzato, abilita Sentry sul frontend. |

> Le variabili Vite **devono** iniziare con `VITE_` per essere esposte al client.

## File `.python-version`

`backend/.python-version` contiene `3.12`. È un hint riconosciuto da
`pyenv`/`pyenv-win` e da altri tool che gestiscono versioni Python; il
nostro flusso `pip + venv` non lo usa direttamente, ma resta utile come
documentazione del requisito (`py -3.12 -m venv .venv`).

## MiniMax integration

L'avatar utente genera 5 brevi clip video (loop) tramite il modello
`MiniMax-Hailuo-02`. Il backend invia immagine + prompt al provider, fa
polling del task e scarica il `.mp4` finale. La logica vive in
`app/services/minimax_service.py` + worker `avatar_clip_worker.py`.

### Ottenere una API key

1. Registrarsi su `https://api.minimax.io` (o il dominio di provider
   equivalente abilitato per Hailuo-02).
2. Generare una API key dalla dashboard.
3. Valorizzare `MINIMAX_API_KEY` nel `.env`.

### Sviluppo locale: URL pubblico per l'immagine

MiniMax **scarica** l'immagine sorgente via HTTP da un URL pubblico (non
accetta upload diretto). In dev locale `localhost` non è raggiungibile dal
provider, quindi serve esporre `/uploads/` con un tunnel:

```bash
# Esempio con ngrok
ngrok http 8000
# poi nel .env:
PUBLIC_BASE_URL=https://xxxx-xx-xx-xxx-xxx.ngrok-free.app
```

`PUBLIC_BASE_URL` è già usato per gli `accept_url` degli inviti; qui viene
riutilizzato dallo `storage_service.public_url(path)` per produrre l'URL
da passare a MiniMax.

### Comportamento se la key non è configurata

Se `MINIMAX_API_KEY` è vuoto:

- L'utente può comunque caricare immagine + audio: il record `avatars` è
  creato e le 5 righe `avatar_clips` sono inserite con `status=pending`.
- Il worker rileva l'assenza della key e lascia le clip in `pending` senza
  errori (nessun retry loop su task inesistenti).
- Appena la key viene valorizzata e il backend riavviato, il worker
  riprende le clip pending.

### Tempi di generazione

Tipicamente 2–5 minuti totali per le 5 clip da 6s, in base al carico del
provider. Il worker pollia ogni `MINIMAX_POLL_INTERVAL_SECONDS` (default
10s); lo stato aggregato dell'avatar (`clips_status`) passa
`pending → processing → ready` (o `partial`/`failed`).

## OpenAI integration (i18n auto-translate)

L'admin di piattaforma può completare automaticamente le traduzioni
mancanti di una lingua premendo il bottone "Completa con AI" nelle
pagine `I18nManagerPage` / `I18nLanguageEditorPage`. Il backend
batcha le chiavi non tradotte e le invia a OpenAI Chat Completions con
`response_format={"type": "json_object"}`. La logica vive in
`app/services/openai_translate_service.py` ed è invocata da
`i18n_service.auto_translate_missing()`.

### Ottenere una API key

1. Registrarsi su `https://platform.openai.com/`.
2. Generare una API key dalla dashboard.
3. Valorizzare `OPENAI_API_KEY` nel `.env`.

### Comportamento se la key non è configurata

Se `OPENAI_API_KEY` è vuoto, l'endpoint
`POST /admin/i18n/languages/{code}/auto-translate` risponde con `422
openai_not_configured` e un messaggio leggibile. Le altre funzionalità
i18n (visualizzazione lingue, edit manuale, conteggio non tradotte)
continuano a funzionare normalmente.

### Modello e batch size

- `OPENAI_MODEL` di default è `gpt-4o-mini` (rapporto costo/qualità
  ottimale per traduzioni UI brevi).
- `OPENAI_BASE_URL` è configurabile per puntare a gateway compatibili.
- `OPENAI_TRANSLATE_BATCH_SIZE` di default è `80` (numero di coppie
  `key → value` inviate per ogni richiesta `/chat/completions`).

Il prompt sistema impone regole strict: preservare i placeholder
i18next (`{{name}}`, `{{count}}`, ecc.), mantenere identiche le keys
del JSON, brand `a4u` lowercase, output esclusivamente JSON object.

## OpenAI integration (corsi — pre-processing + Fase 1 + Fase 2)

Il dominio Corsi usa lo stesso `OPENAI_API_KEY` per quattro pipeline AI:

1. **Pre-processing documenti** (Appendice A) — estrae il riassunto strutturato
   da ogni documento caricato. Worker async + `openai_summarize_service`.
2. **Architettura corso** (Fase 1) — produce overview, razionale, moduli e
   lezioni a partire dai parametri del corso e dai summary dei documenti.
   Worker async + `openai_architecture_service`.
3. **Lezioni di un modulo** (manual editing) — generazione AI sincrona delle
   lezioni quando l'utente aggiunge un modulo manualmente. Service
   `openai_module_lessons_service`.
4. **Struttura delle lezioni** (Fase 2) — per ogni lezione, produce obiettivi,
   temi obbligatori, prerequisiti e scaletta. Worker async **parallelo** con
   `asyncio.Semaphore` (cap `COURSE_LESSON_STRUCTURE_MAX_CONCURRENCY`) e
   sessioni DB indipendenti per task. Service `openai_lesson_structure_service`.

Tutte e quattro usano `response_format: json_schema` strict. La validazione
output passa per Pydantic prima di scrivere in DB; un mismatch produce
`failed` (per worker) o `422` (per la chiamata sync).

> **Nota su gpt-5.5**: il modello richiede `max_completion_tokens` (non
> `max_tokens`) e non accetta `temperature` custom (solo default 1.0). Il
> codice rispetta questi vincoli; se cambi modello in `OPENAI_*_MODEL`,
> verifica la compatibilità dei parametri.

> **Nota su rate-limit Fase 2**: il semaforo a 5 è un compromesso safe per
> tier standard di gpt-5.5. Se OpenAI risponde 429, il task fallisce e
> l'utente fa "Riprova" (no backoff automatico in questa iterazione).
> Se hai un tier alto puoi aumentare `COURSE_LESSON_STRUCTURE_MAX_CONCURRENCY`.

Vedi [Courses pre-processing](courses/02-document-preprocessing.md),
[Architecture generation](courses/03-architecture-generation.md),
[Manual editing](courses/04-manual-editing.md) e
[Lesson structure](courses/07-lesson-structure.md) per il dettaglio.

## Configurazione produzione

In aggiunta ai `.env`:

- `ENV=production` → log JSON, swagger nascosto, security headers HSTS.
- `COOKIE_SECURE=true` → richiede HTTPS.
- `JWT_SECRET` da secret manager esterno (mai committato).
- `FRONTEND_ORIGIN` con dominio reale.
- `PUBLIC_BASE_URL` con dominio reale.
- `SENTRY_DSN` valorizzato.

Vedi anche [07 — Deployment](07-deployment.md).
