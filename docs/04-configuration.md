# 04 — Configuration

Esistono **due** soli file di env:

- **`.env`** alla root del repo — letto da `docker compose` (per i `${VAR}`
  in `docker-compose.yml`/`docker-compose.prod.yml`) e dal backend FastAPI
  (`pydantic-settings` punta qui via path assoluto in `app/core/config.py`).
- **`frontend/.env`** — letto da Vite, che ha la sua convenzione (carica
  solo dalla project root del frontend).

Il backend rifiuta l'avvio se `JWT_SECRET` ha lunghezza < 32 byte
(validatore Pydantic). In container produzione le variabili arrivano dal
blocco `environment:` di `docker-compose.prod.yml`, non dal file (sono
forwardate con `${VAR:-default}` per ogni knob significativo).

## Variabili nel `.env` di root

### Postgres (lette da docker-compose)

| Variabile | Default | Descrizione |
|---|---|---|
| `POSTGRES_USER` | `a4u` | Utente DB del container. |
| `POSTGRES_PASSWORD` | `a4u_dev_password` | Password. |
| `POSTGRES_DB` | `a4u` | Nome del database iniziale. |
| `POSTGRES_PORT` | `5432` | Porta esposta sull'host (solo in dev — in prod il container postgres non è esposto). |

### Porte esposte sul host (lette da docker-compose.prod.yml)

| Variabile | Default | Descrizione |
|---|---|---|
| `FRONTEND_PORT` | `80` | Porta pubblica del frontend nginx (serve `dist/` + proxy `/api/` e `/uploads/` verso il backend). Cambia se la 80 è occupata o se hai un reverse proxy esterno (Caddy/Cloudflare). |
| `BACKEND_PORT` | `127.0.0.1:8000` | Backend FastAPI. **Default = solo localhost** del server: il traffico esterno passa via il proxy del frontend nginx. Imposta `8000` per esporlo su tutte le interfacce, `192.168.1.10:8000` per limitarlo a una specifica (multi-homed server). |

### Backend core (lette da FastAPI)

| Variabile | Default | Descrizione |
|---|---|---|
| `ENV` | `development` | `development \| test \| production`. Determina log format, secure cookie, swagger pubblico. |
| `LOG_LEVEL` | `INFO` | Livello log (`DEBUG \| INFO \| WARNING \| ERROR`). |
| `LOG_FORMAT` | `console` | `console` (dev colorato) o `json` (produzione → ingest aggregatore). |
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
| `UPLOAD_DIR` | `./uploads` | Cartella per i file caricati (loghi org, avatar, materiali corso, asset template PDF). |
| `UPLOAD_MAX_MB` | `5` | Limite backend per upload immagini. **NB**: il frontend nginx ha un suo limite (`client_max_body_size 25m` in `frontend/nginx.conf`); per file più grandi alzali entrambi. |
| `AVATAR_AUDIO_MAX_MB` | `10` | Limite per il file audio dell'avatar utente. |
| `COURSE_DOCUMENT_MAX_MB` | `25` | Limite per i documenti caricati nei corsi (PDF/DOC/DOCX/TXT/MD/RTF). |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | URL base per costruire `accept_url` degli inviti, e per i template PDF (asset relativi → URL pubblico se data-URL non disponibile). In dev locale, deve essere raggiungibile da MiniMax → tunnel ngrok. |
| `BOOTSTRAP_ADMIN_EMAIL` | _(vuoto)_ | Se settato + password: l'admin viene creato/promosso al seed. |
| `BOOTSTRAP_ADMIN_PASSWORD` | _(vuoto)_ | Vedi sopra. Soggetta alla policy: ≥10 chars, una maiuscola, un numero. |
| `BOOTSTRAP_ADMIN_FULL_NAME` | `Platform Admin` | Nome dell'utente bootstrap. |
| `RATE_LIMIT_LOGIN_PER_MIN` | `5` | Richieste login per minuto per IP. |
| `LOGIN_LOCKOUT_THRESHOLD` | `10` | Tentativi falliti per bloccare l'account. |
| `LOGIN_LOCKOUT_MINUTES` | `15` | Durata del blocco dopo lockout. |
| `SENTRY_DSN` | _(vuoto)_ | Se valorizzato, attiva Sentry su backend. |

### MiniMax (avatar clip generation)

| Variabile | Default | Descrizione |
|---|---|---|
| `MINIMAX_API_KEY` | _(vuoto)_ | API key MiniMax. Se vuoto, le clip avatar restano in stato `pending` finché non viene configurata. |
| `MINIMAX_BASE_URL` | `https://api.minimax.io` | Base URL del provider. |
| `MINIMAX_VIDEO_MODEL` | `MiniMax-Hailuo-02` | Modello video. |
| `MINIMAX_CLIP_DURATION` | `6` | Durata in secondi di ogni clip. |
| `MINIMAX_CLIP_RESOLUTION` | `1080P` | Risoluzione richiesta al provider. |
| `MINIMAX_POLL_INTERVAL_SECONDS` | `10` | Intervallo del worker che processa le clip pending/processing. |

### OpenAI — modelli e budget token

Tutte le pipeline AI condividono `OPENAI_API_KEY` + `OPENAI_BASE_URL`. Ogni
fase ha il suo modello + cap di token configurabile a parte.

| Variabile | Default | Descrizione |
|---|---|---|
| `OPENAI_API_KEY` | _(vuoto)_ | API key OpenAI. Se vuota, tutti gli endpoint AI rispondono `422 openai_not_configured`; le pipeline corso lasciano i task in `pending` finché non valorizzata. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Override per gateway compatibili (Azure OpenAI, vLLM, Together, Groq). |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modello per la **traduzione i18n** (Completa con AI). |
| `OPENAI_TRANSLATE_BATCH_SIZE` | `40` | Numero di chiavi inviate per richiesta `/chat/completions`. |
| `OPENAI_SUMMARIZE_MODEL` | `gpt-4o-mini` | Modello per il **summary documenti corso** (Appendice A). |
| `OPENAI_SUMMARIZE_MAX_TOKENS` | `8000` | `max_completion_tokens` per il summarize service. |
| `OPENAI_GLOSSARY_MODEL` | `gpt-5.5` | Modello per il **glossario corso** (§10.1). |
| `OPENAI_GLOSSARY_MAX_TOKENS` | `4000` | `max_completion_tokens` per il glossario. |
| `OPENAI_MODULES_LESSONS_MODEL` | `gpt-5.5` | Modello condiviso da **Fase 1 — architettura corso** e dalla generazione AI delle lezioni di un singolo modulo (manual editing). |
| `OPENAI_ARCHITECTURE_MAX_TOKENS` | `8000` | `max_completion_tokens` per architettura corso. Alza a `16000` se vedi `finish_reason=length`. |
| `OPENAI_ARCHITECTURE_REASONING_EFFORT` | `medium` | Reasoning effort per Fase 1. Valori `[minimal, low, medium, high]`. Vedi sezione "Reasoning effort" sotto. |
| `OPENAI_LESSON_STRUCTURE_MODEL` | `gpt-5.5` | Modello per **Fase 2 — struttura lezioni**. |
| `OPENAI_LESSON_STRUCTURE_MAX_TOKENS` | `16000` | Cap per ciascun modulo elaborato in Fase 2 (gpt-5.5 consuma parecchi token in reasoning prima del JSON). |
| `OPENAI_LESSON_STRUCTURE_REASONING_EFFORT` | `medium` | Reasoning effort per Fase 2. |
| `OPENAI_LESSON_CONTENT_MODEL` | `gpt-5.5` | Modello per **Fase 3 — contenuto lezione**. |
| `OPENAI_LESSON_CONTENT_MAX_TOKENS` | `32000` | Cap per ciascuna lezione elaborata in Fase 3 (output 8-15k token + reasoning). |
| `OPENAI_LESSON_CONTENT_REASONING_EFFORT` | `high` | Reasoning effort per Fase 3 (default più alto: il task è il più complesso del pipeline). |

### OpenAI — parallelismo + auto-retry worker corso

I worker batch del pipeline corso (Fase 2, Fase 3, PDF) elaborano i task in
parallelo con un cap di concorrenza configurabile. I default sono prudenti
per non triggerare rate-limit OpenAI con tier free/1.

| Variabile | Default | Descrizione |
|---|---|---|
| `COURSE_DOCUMENT_MAX_CHARS` | `120000` | Troncamento testo estratto da documenti prima del summarize (~30k token). |
| `COURSE_DOCUMENT_POLL_INTERVAL_SECONDS` | `4` | Polling worker pre-processing documenti. |
| `COURSE_ARCHITECTURE_POLL_INTERVAL_SECONDS` | `4` | Polling worker Fase 1. |
| `COURSE_ARCHITECTURE_DOCUMENTS_CONTEXT_MAX_CHARS` | `60000` | Budget summary documento concatenati nel prompt Fase 1. |
| `COURSE_LESSON_STRUCTURE_POLL_INTERVAL_SECONDS` | `4` | Polling worker Fase 2. |
| `COURSE_LESSON_STRUCTURE_MAX_CONCURRENCY` | `5` | Cap moduli paralleli Fase 2 (`asyncio.Semaphore`). |
| `COURSE_LESSON_STRUCTURE_DOCUMENTS_CONTEXT_MAX_CHARS` | `30000` | Budget summary nel prompt Fase 2. |
| `COURSE_LESSON_STRUCTURE_AUTO_RETRY_MAX` | `5` | Numero massimo di retry trasparenti prima di transitare a `failed`. |
| `COURSE_LESSON_CONTENT_POLL_INTERVAL_SECONDS` | `4` | Polling worker Fase 3. |
| `COURSE_LESSON_CONTENT_MAX_CONCURRENCY` | `3` | Cap lezioni parallele Fase 3 (output 5x più grande di Fase 2 → cap più basso). |
| `COURSE_LESSON_CONTENT_DOCUMENTS_CONTEXT_MAX_CHARS` | `20000` | Budget summary nel prompt Fase 3. |
| `COURSE_LESSON_CONTENT_AUTO_RETRY_MAX` | `5` | Vedi sopra. |
| `COURSE_GLOSSARY_DOCUMENTS_CONTEXT_MAX_CHARS` | `20000` | Budget summary nel prompt glossario. |
| `COURSE_LESSON_PDF_POLL_INTERVAL_SECONDS` | `4` | Polling worker PDF. |
| `COURSE_LESSON_PDF_MAX_CONCURRENCY` | `2` | Cap PDF paralleli (WeasyPrint + Chromium pre-render mermaid → CPU/RAM bound, **non** OpenAI). |
| `COURSE_LESSON_PDF_AUTO_RETRY_MAX` | `5` | Vedi sopra. |
| `GENERATED_PDFS_DIR` | `generated_pdfs` | Directory output PDF generati. Path relativo o assoluto. |

### Tempi stimati (corso 30 lezioni) per `COURSE_LESSON_CONTENT_MAX_CONCURRENCY`

| Concurrency | Tempo totale | Tier OpenAI consigliato |
|---|---|---|
| `3` (default) | ~90-120 min | Tier 1 — sicuro |
| `8` | ~30-45 min | Tier 2+ |
| `15` | ~15-25 min | Tier 3+ — rischio 429 più alto |

Se vedi log `lesson_content_auto_retry` con errori `rate_limit_exceeded`,
scendi di 2-3 unità. Il sistema retry-a in trasparenza ma rallenta il batch.

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

## Reasoning effort (gpt-5.x / o1 / o3 / o4)

I modelli reasoning OpenAI accettano un parametro `reasoning_effort` che
controlla quanto a lungo il modello "pensa" prima di rispondere. Valori
validi e effetto:

| Valore | Latenza tipica | Quando usarlo |
|---|---|---|
| `minimal` | output quasi immediato | task semplici (solo gpt-5.x — o1/o3 non lo accettano). |
| `low` | ~1-3s reasoning | qualità base. |
| `medium` | ~5-15s reasoning | default consigliato (Fase 1, Fase 2). |
| `high` | ~20-60s reasoning | massima qualità (default Fase 3 — task più complesso). |

Implementazione (`app/services/openai_client.py:apply_reasoning_effort`):
- Helper inserisce `reasoning_effort` nel body della chiamata SOLO se il
  modello è reasoning (prefix `o1`, `o3`, `o4`, `gpt-5`). Su modelli
  classici (`gpt-4o`, `gpt-4o-mini`) il parametro viene **omesso**, evitando
  il `400 invalid_request_error` che OpenAI rifiuterebbe.
- Su famiglia `o1*` (che non accetta `minimal`) normalizza a `low`.

Lever per accelerare un corso: abbassare `OPENAI_LESSON_CONTENT_REASONING_EFFORT`
da `high` a `medium` riduce il tempo per lezione del ~40%; qualità leggermente
inferiore (struttura/coerenza un filo meno raffinate, output ancora ottimi).

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

## OpenAI integration — overview pipeline corsi

Il dominio Corsi usa lo stesso `OPENAI_API_KEY` per **sei** pipeline AI,
con un setting `model` + `max_tokens` separato per ognuna:

| Pipeline | Servizio | Endpoint | Sync/Async |
|---|---|---|---|
| **Traduzione i18n** | `openai_translate_service` | `/chat/completions` | sync (admin button) |
| **Pre-processing documenti** (Appendice A) | `openai_summarize_service` | `/chat/completions` | worker async (`course_document_worker`) |
| **Architettura corso** (Fase 1) | `openai_architecture_service`, `openai_module_lessons_service` | `/chat/completions` | worker async (`course_architecture_worker`) + sync (manual editing) |
| **Glossario corso** (§10.1) | `openai_glossary_service` | `/chat/completions` | sync inline (auto-trigger dal worker Fase 3) |
| **Struttura lezioni** (Fase 2) | `openai_lesson_structure_service` | `/chat/completions` | worker async **parallelo** (`course_lesson_structure_worker`) |
| **Contenuto lezione** (Fase 3) | `openai_lesson_content_service` | `/chat/completions` | worker async **parallelo** (`course_lesson_content_worker`) |

Tutte usano `response_format: json_schema` strict. La validazione output
passa per Pydantic prima di scrivere in DB; un mismatch produce auto-retry
trasparente fino a `*_AUTO_RETRY_MAX`, poi `failed` (terminale).

> **Endpoint scelto**: `/chat/completions` ovunque, no Responses API, no
> Batch API. Real-time UX (l'utente vede lo status live nella UI), niente
> lock-in OpenAI (compatibile con Azure/vLLM/Groq), structured outputs
> identici. Vedi [02 — Architecture](02-architecture.md) per il razionale.

> **gpt-5.5 vs altri modelli**: gpt-5.5 richiede `max_completion_tokens`
> (NON `max_tokens`) e non accetta `temperature` custom (solo default 1.0).
> Il codice rispetta automaticamente questi vincoli. Se sostituisci con
> un modello classico (`gpt-4o`), il helper `apply_reasoning_effort` omette
> `reasoning_effort` dal body; resta solo da verificare che `max_tokens`
> sia coerente.

> **Rate-limit**: il semaforo `MAX_CONCURRENCY` è il primo lever. Se
> OpenAI risponde 429 ripetutamente, il worker auto-retry-a fino al
> cap; oltre, transita a `failed` e l'utente fa "Riprova". Vedi anche
> "Tempi stimati" sopra.

Vedi:
- [Courses pre-processing](courses/02-document-preprocessing.md) — Appendice A
- [Architecture generation](courses/03-architecture-generation.md) — Fase 1
- [Manual editing](courses/04-manual-editing.md) — generazione lezioni inline
- [Lesson structure](courses/07-lesson-structure.md) — Fase 2 (worker parallelo)
- [Lesson content](courses/08-lesson-content.md) — Fase 3 + glossario

## Configurazione produzione

In aggiunta ai `.env`:

- `ENV=production` → log JSON, swagger nascosto, security headers HSTS.
- `COOKIE_SECURE=true` → richiede HTTPS.
- `JWT_SECRET` da secret manager esterno (mai committato).
- `FRONTEND_ORIGIN` con dominio reale.
- `PUBLIC_BASE_URL` con dominio reale.
- `SENTRY_DSN` valorizzato.
- `FRONTEND_PORT` / `BACKEND_PORT` impostate se la 80 è occupata o se
  servi via reverse proxy esterno.

Vedi anche [07 — Deployment](07-deployment.md).
