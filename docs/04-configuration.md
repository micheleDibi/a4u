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
| `MINIMAX_VIDEO_MODEL` | `MiniMax-Hailuo-02` | Modello video. Supporta la modalità FLF (First-and-Last-Frame): inviando `first_frame_image` + `last_frame_image` interpola un video tra le due immagini. |
| `MINIMAX_CLIP_DURATION` | `6` | Durata in secondi di ogni clip. |
| `MINIMAX_CLIP_RESOLUTION` | `1080P` | Risoluzione richiesta al provider. |
| `MINIMAX_POLL_INTERVAL_SECONDS` | `10` | Intervallo del worker che processa le clip pending/processing. |

### RunPod — TTS XTTS-v2 (Fase 6 — video MP4 della lezione)

La generazione del video MP4 della lezione (Fase 6) sintetizza il
discorso con XTTS-v2 su un endpoint **RunPod Serverless GPU** (l'handler
è nella cartella `XTTS/` del repo). Il backend è solo un client HTTP: non
ha torch/coqui. Se `RUNPOD_API_KEY` o `RUNPOD_TTS_ENDPOINT_ID` non sono
valorizzate, la generazione video resta disabilitata.

| Variabile | Default | Descrizione |
|---|---|---|
| `RUNPOD_API_KEY` | _(vuoto)_ | API key RunPod. Vuoto → generazione video disabilitata. Riusato anche per l'endpoint MuseTalk (stesso account). |
| `RUNPOD_TTS_ENDPOINT_ID` | _(vuoto)_ | Endpoint ID dell'endpoint Serverless GPU del TTS XTTS-v2. Vuoto → generazione video disabilitata. |
| `RUNPOD_BASE_URL` | `https://api.runpod.ai` | Base URL dell'API RunPod. |
| `RUNPOD_TTS_TIMEOUT_SECONDS` | `1800` (30 min) | Timeout wall-clock totale di un job TTS (assorbe il cold start della GPU). |
| `RUNPOD_TTS_POLL_INTERVAL_SECONDS` | `3` | Intervallo di polling del job TTS quando si fa fallback su `/status`. |

### RunPod MuseTalk + Cloudflare R2 (Fase 6b — "Video con Avatar")

Il "Video con Avatar" (Fase 6b) sovrappone al video MP4 della lezione un
avatar parlante con lip-sync prodotto da **MuseTalk** su un secondo
endpoint RunPod Serverless GPU. Il client MuseTalk è vendored in
`backend/app/musetalk_client/` e gira come subprocess isolato; **Cloudflare
R2** (storage S3-compatible) è lo storage di transito per
video/audio/output del job. Queste variabili vengono passate al
subprocess come environment. Se mancano, il "Video con Avatar" è
disabilitato.

| Variabile | Default | Descrizione |
|---|---|---|
| `RUNPOD_MUSETALK_ENDPOINT_ID` | _(vuoto)_ | Endpoint ID dell'endpoint Serverless GPU dedicato a MuseTalk. Stesso account del TTS (`RUNPOD_API_KEY` riusato). Vuoto → "Video con Avatar" disabilitato. |
| `R2_ENDPOINT` | _(vuoto)_ | URL dell'endpoint R2 (`https://<account>.r2.cloudflarestorage.com`). |
| `R2_BUCKET` | _(vuoto)_ | Nome del bucket R2 di transito. |
| `R2_ACCESS_KEY_ID` | _(vuoto)_ | Access key ID R2. |
| `R2_SECRET_ACCESS_KEY` | _(vuoto)_ | Secret access key R2. |

### Worker video (Fase 6) e video-con-avatar (Fase 6b)

I due worker async che orchestrano la generazione video. Cap di
concorrenza `1` di default: un solo job GPU per volta (costoso). Gli
auto-retry sono prudenti perché ogni tentativo richiede minuti.

| Variabile | Default | Descrizione |
|---|---|---|
| `COURSE_LESSON_VIDEO_POLL_INTERVAL_SECONDS` | `4` | Polling del worker video MP4. |
| `COURSE_LESSON_VIDEO_MAX_CONCURRENCY` | `1` | Cap di video generati in parallelo. Il TTS gira su RunPod; localmente restano slide-render (Playwright) + encoding (ffmpeg). |
| `COURSE_LESSON_VIDEO_AUTO_RETRY_MAX` | `3` | Retry trasparenti su errore recuperabile (timeout/errore RunPod, errore ffmpeg) prima di `failed`. Le pre-condizioni mancanti vanno a `failed` immediato. |
| `COURSE_LESSON_AVATAR_VIDEO_POLL_INTERVAL_SECONDS` | `4` | Polling del worker "Video con Avatar". |
| `COURSE_LESSON_AVATAR_VIDEO_MAX_CONCURRENCY` | `1` | Cap di video-con-avatar generati in parallelo. |
| `COURSE_LESSON_AVATAR_VIDEO_AUTO_RETRY_MAX` | `3` | Retry trasparenti su errore transitorio. Un timeout RunPod (`TIMED_OUT`) è invece terminale (si ripeterebbe identico). |
| `COURSE_LESSON_AVATAR_VIDEO_TIMEOUT_SECONDS` | `10800` (3 h) | Timeout wall-clock del subprocess MuseTalk (preprocess + lip-sync + download). Generoso: assorbe cold start GPU + audio molto lunghi. |

### Encoding video (ffmpeg)

Parametri dell'encoding ffmpeg del video MP4 della lezione. Richiede il
binario `ffmpeg` in PATH (o un path assoluto).

| Variabile | Default | Descrizione |
|---|---|---|
| `VIDEO_RESOLUTION` | `1920x1080` | Risoluzione di riferimento dell'encoding. |
| `VIDEO_FRAMERATE` | `30` | Frame rate del video. |
| `VIDEO_AUDIO_BITRATE` | `192k` | Bitrate della traccia audio AAC. |
| `VIDEO_AUDIO_SAMPLE_RATE` | `48000` | Sample rate dell'audio del video. |
| `VIDEO_VIDEO_CODEC` | `libx264` | Codec video (H.264). |
| `VIDEO_CRF` | `23` | Constant Rate Factor (`18` alta qualità, `28` compresso). |
| `VIDEO_PRESET` | `veryfast` | Preset libx264. Per slide statiche (`-tune stillimage`) `veryfast` dà qualità identica a `medium` ma 3-5× più veloce. Valori: `ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow`. |
| `VIDEO_PIXEL_FORMAT` | `yuv420p` | Pixel format (compat HTML5/QuickTime). |
| `LESSON_VIDEO_MAX_MB` | `500` | Safety upper bound sulla dimensione del file video. |
| `FFMPEG_BINARY` | `ffmpeg` | Nome o path assoluto del binario ffmpeg. |

### Overlay avatar (Fase 6b)

Parametri della sovrapposizione dell'avatar parlante sul video MP4 della
lezione, e del downscale delle clip prima del lip-sync.

| Variabile | Default | Descrizione |
|---|---|---|
| `AVATAR_VIDEO_OVERLAY_SCALE` | `0.24` | Lato del quadrato dell'avatar come frazione della larghezza del video (0,24 = 24 %). L'avatar è ancorato in basso a destra. |
| `AVATAR_VIDEO_OVERLAY_MARGIN` | `24` | Distanza dell'avatar dai bordi destro/inferiore, in pixel. |
| `AVATAR_VIDEO_CLIP_RESOLUTION` | `640` | Risoluzione (lato del quadrato) a cui le clip MiniMax (1080×1080) vengono ridimensionate prima del lip-sync su RunPod. A 1080 il job sforerebbe il tetto di 60 min; 640 riporta i tempi nella norma senza perdita visibile. |

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
| `OPENAI_PAPER_SUMMARY_MODEL` | `gpt-4o-mini` | Modello per il **riassunto AI dei paper scientifici** (`openai_paper_summary_service`, sincrono, no persistenza). Vedi [Courses 16](courses/16-paper-search.md). |
| `OPENAI_PAPER_SUMMARY_MAX_TOKENS` | `3000` | `max_completion_tokens` per il riassunto paper. |
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
| `OPENAI_LESSON_SLIDES_MODEL` | `gpt-5.5` | Modello per **Fase 4 — slide della lezione**. |
| `OPENAI_LESSON_SLIDES_MAX_TOKENS` | `16000` | Cap per ciascuna lezione elaborata in Fase 4 (output ~4-8k token + reasoning). |
| `OPENAI_LESSON_SLIDES_REASONING_EFFORT` | `medium` | Reasoning effort per Fase 4. |
| `OPENAI_LESSON_SPEECH_MODEL` | `gpt-5.5` | Modello per **Fase 5 — discorso temporizzato**. |
| `OPENAI_LESSON_SPEECH_MAX_TOKENS` | `16000` | Cap per ciascuna lezione elaborata in Fase 5 (output prosa pura ~6-12k token + reasoning; alza per lezioni 90 min ≈ 11.7k parole IT). |
| `OPENAI_LESSON_SPEECH_REASONING_EFFORT` | `medium` | Reasoning effort per Fase 5. |

### OpenAI — parallelismo + auto-retry worker corso

I worker batch del pipeline corso (Fase 2, Fase 3, Fase 4, Fase 5, e i tre
PDF — testo / slide / discorso) elaborano i task in parallelo con un cap di
concorrenza configurabile. I default sono prudenti per non triggerare
rate-limit OpenAI con tier free/1.

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
| `COURSE_LESSON_SLIDES_POLL_INTERVAL_SECONDS` | `4` | Polling worker Fase 4. |
| `COURSE_LESSON_SLIDES_MAX_CONCURRENCY` | `3` | Cap lezioni parallele Fase 4 (input ~8-18k token = content_raw, output ~4-8k). |
| `COURSE_LESSON_SLIDES_AUTO_RETRY_MAX` | `5` | Numero massimo di retry trasparenti prima di transitare a `failed`. |
| `COURSE_LESSON_SPEECH_POLL_INTERVAL_SECONDS` | `4` | Polling worker Fase 5. |
| `COURSE_LESSON_SPEECH_MAX_CONCURRENCY` | `3` | Cap lezioni parallele Fase 5 (input ~12-25k token = content+slides, output ~6-12k prosa pura). |
| `COURSE_LESSON_SPEECH_AUTO_RETRY_MAX` | `5` | Vedi sopra. Cause frequenti di retry: TTS-safety (l'AI mette `\frac` o `*` nel testo), durata fuori range. |
| `COURSE_GLOSSARY_DOCUMENTS_CONTEXT_MAX_CHARS` | `20000` | Budget summary nel prompt glossario. |
| `COURSE_LESSON_PDF_POLL_INTERVAL_SECONDS` | `4` | Polling worker PDF (condiviso da tutti e tre i worker PDF: testo, slide, discorso). |
| `COURSE_LESSON_PDF_MAX_CONCURRENCY` | `2` | Cap PDF paralleli (WeasyPrint + Chromium pre-render mermaid per testo/slide → CPU/RAM bound, **non** OpenAI). Speech PDF non usa Mermaid. |
| `COURSE_LESSON_PDF_AUTO_RETRY_MAX` | `5` | Vedi sopra. Condiviso dai 3 worker PDF. |
| `GENERATED_PDFS_DIR` | `generated_pdfs` | Directory output PDF generati. Path relativo o assoluto. Subfolder per (org, course); file: `{lesson_id}.pdf` (testo), `{lesson_id}_slides.pdf` (slide), `{lesson_id}_speech.pdf` (discorso). |

### Tempi stimati (corso 30 lezioni) per `COURSE_LESSON_CONTENT_MAX_CONCURRENCY`

| Concurrency | Tempo totale | Tier OpenAI consigliato |
|---|---|---|
| `3` (default) | ~90-120 min | Tier 1 — sicuro |
| `8` | ~30-45 min | Tier 2+ |
| `15` | ~15-25 min | Tier 3+ — rischio 429 più alto |

Se vedi log `lesson_content_auto_retry` con errori `rate_limit_exceeded`,
scendi di 2-3 unità. Il sistema retry-a in trasparenza ma rallenta il batch.

### Ricerca paper scientifici (OpenAlex + enrichment)

La tab "Documenti" di un corso può cercare e importare paper accademici
da fonti aperte. **Nessuna API key necessaria** per i tre provider:
- **OpenAlex** è la *primary search* (discovery + paginazione cursor-based
  su ~250M paper, `backend/app/services/openalex_client.py:238`).
- **Semantic Scholar** e **Crossref** sono usati solo come *enrichment
  on-demand* (per singolo paper con DOI, mai durante la search di lista),
  per recuperare TL;DR, abstract pulito, subjects e references count.

`PAPERS_POLITE_EMAIL` viene aggiunta come `mailto:` nello `User-Agent`
condiviso dai tre client: con email valorizzata l'IP entra nel **"polite
pool"** dei provider (rate-limit più permissivo,
`backend/app/services/openalex_client.py:72`); vuota → `User-Agent` senza
`mailto:`.

| Variabile | Default | Descrizione |
|---|---|---|
| `OPENALEX_BASE_URL` | `https://api.openalex.org` | Base URL OpenAlex (primary search). |
| `SEMANTIC_SCHOLAR_BASE_URL` | `https://api.semanticscholar.org` | Base URL Semantic Scholar (enrichment on-demand: TL;DR + fallback PDF OA). |
| `CROSSREF_BASE_URL` | `https://api.crossref.org` | Base URL Crossref (enrichment on-demand: abstract pulito, subjects, references count). |
| `PAPERS_POLITE_EMAIL` | _(vuoto)_ | Email messa come `mailto:` nello `User-Agent` dei 3 provider per entrare nel "polite pool" (rate-limit migliore). Vuota → `User-Agent` senza `mailto:`. |

> Il modello + cap di token del riassunto AI dei paper sono nella tabella
> [OpenAI — modelli e budget token](#openai--modelli-e-budget-token)
> (`OPENAI_PAPER_SUMMARY_MODEL` / `OPENAI_PAPER_SUMMARY_MAX_TOKENS`).

Deep-dive completo (architettura multi-source, 3 endpoint, import,
relevance score, riassunto AI): [Courses 16 — Paper search](courses/16-paper-search.md).

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

> **Clip loopabili (FLF)**: per ogni clip in loop il backend passa
> `last_frame_image = first_frame_image` (la stessa URL avatar), attivando
> la modalità FLF (First-and-Last-Frame) di `MiniMax-Hailuo-02`
> (`app/services/minimax_service.py:80`, `:100`). Così ogni clip torna
> alla posa iniziale → è loopabile su sé stessa e interscambiabile con le
> altre clip del pool da cui MuseTalk pesca per la lip-sync (concatenazioni
> fluide, niente stacchi alle giunzioni). Prima (`MiniMax-Hailuo-2.3`, I2V
> puro senza FLF) la loopabilità era guidata solo dal prompt. L'API sceglie
> FLF in base ai campi presenti nel body, quindi non serve un flag esplicito.

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

## RunPod integration — TTS XTTS-v2 e MuseTalk lip-sync

Le ultime due fasi della pipeline corsi (video MP4 della lezione e
"Video con Avatar") delegano i task GPU-intensivi a **due endpoint
RunPod Serverless GPU** distinti. Il backend non ha né torch/coqui né i
modelli di lip-sync: è solo un client HTTP/subprocess.

| Endpoint | Variabile endpoint ID | Cosa fa | Storage di transito |
|---|---|---|---|
| **TTS XTTS-v2** (Fase 6) | `RUNPOD_TTS_ENDPOINT_ID` | Sintesi vocale del discorso con voce clonata dell'avatar | — (campione vocale via URL `/uploads/...`) |
| **MuseTalk** (Fase 6b) | `RUNPOD_MUSETALK_ENDPOINT_ID` | Lip-sync dell'avatar parlante | Cloudflare R2 (`R2_*`) |

`RUNPOD_API_KEY` è **unica** e condivisa dai due endpoint (stesso
account RunPod). `RUNPOD_BASE_URL` è comune.

### Ottenere gli endpoint RunPod

1. Registrarsi su `https://www.runpod.io` e generare una API key dalla
   sezione *Settings → API Keys* (permesso sugli endpoint serverless).
2. **TTS**: costruire l'immagine Docker dalla cartella `XTTS/` del repo
   (`docker build --platform linux/amd64 ...`), pushare su un registry
   (GHCR/Docker Hub), creare un endpoint RunPod Serverless da quell'immagine.
   Procedura completa in `XTTS/README.md`.
3. **MuseTalk**: creare un secondo endpoint serverless dedicato a MuseTalk
   (immagine del progetto MuseTalk-API).
4. Valorizzare `RUNPOD_API_KEY`, `RUNPOD_TTS_ENDPOINT_ID` e
   `RUNPOD_MUSETALK_ENDPOINT_ID` nel `.env`.

### Cloudflare R2

Il client MuseTalk usa un bucket R2 (S3-compatible) come storage di
transito per i file di video/audio/output del job. Creare un bucket R2
dalla dashboard Cloudflare e generare un token S3 (access key id +
secret); valorizzare `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`.

### Comportamento se RunPod/R2 non sono configurati

- Se `RUNPOD_API_KEY` o `RUNPOD_TTS_ENDPOINT_ID` mancano: la generazione
  del video MP4 è disabilitata. Le rotte `video/generate` rifiutano a
  monte; il worker non avvia job.
- Se `RUNPOD_MUSETALK_ENDPOINT_ID` o una delle `R2_*` mancano: il "Video
  con Avatar" è disabilitato (il worker fallisce con un errore di
  pre-condizione esplicito).
- Le altre funzioni della piattaforma (incluse le Fasi AI 1-5 e i PDF)
  non sono toccate.

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

> Oltre alle pipeline core sopra, il **riassunto AI dei paper scientifici**
> (`openai_paper_summary_service`, sincrono e senza persistenza) usa lo
> stesso `OPENAI_API_KEY` con setting dedicati
> (`OPENAI_PAPER_SUMMARY_MODEL` / `OPENAI_PAPER_SUMMARY_MAX_TOKENS`). Vedi
> [Courses 16 — Paper search](courses/16-paper-search.md).

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
- `RUNPOD_API_KEY` + `RUNPOD_TTS_ENDPOINT_ID` per abilitare la
  generazione video; `RUNPOD_MUSETALK_ENDPOINT_ID` + `R2_*` per il
  "Video con Avatar". In produzione `PUBLIC_BASE_URL` deve essere
  raggiungibile dai worker RunPod (è da lì che scaricano il campione
  vocale dell'avatar).

> **Threading scientifico (XTTS/librosa)**: `docker-compose.prod.yml`
> forwarda `OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `OPENBLAS_NUM_THREADS`
> (default `2`). Su VM senza AVX/AVX2 il default può degradare il
> preprocessing audio residuo lato backend; impostarli a `nproc` nel
> `.env` se necessario.

Vedi anche [07 — Deployment](07-deployment.md).
