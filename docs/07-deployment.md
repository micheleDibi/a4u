# 07 — Deployment

## Modalità sviluppo

Vedi [03 — Getting started](03-getting-started.md). Solo Postgres in Docker;
backend e frontend in processi locali con hot reload.

## Modalità produzione (Docker compose)

Tre servizi: `postgres`, `backend`, `frontend` (nginx serve i file statici e
fa proxy verso il backend).

### Prerequisiti server

- Linux con Docker Engine 24+ e Docker Compose plugin v2.
- Almeno **4 GB RAM** consigliata (idle ~600 MB; durante batch PDF il pre-render
  Mermaid carica un'istanza Chromium per lezione → 150-300 MB ognuna, con
  `COURSE_LESSON_PDF_MAX_CONCURRENCY=2` di default servono ~800 MB extra in
  picco; WeasyPrint stesso è leggero ~50 MB/render).
- Almeno **5 GB disco** per immagini Docker, browser Chromium di
  Playwright (~300 MB), generated PDFs e uploads. **I video MP4 generati**
  (Fasi 6/6b) vivono nel volume `uploads` e pesano ~25 MB ogni 10 min di
  lezione: per corsi con molte lezioni video preventivare spazio extra
  (un corso da 30 lezioni × ~15 min ≈ 1-1,5 GB di soli video, ×2 se si
  genera anche la variante con avatar).
- I task GPU (TTS XTTS-v2, MuseTalk lip-sync) **non** girano sul server:
  sono delegati a endpoint RunPod (vedi "Prerequisiti esterni"). Il
  server fa solo orchestrazione, render slide e encoding ffmpeg.
- Una porta pubblica (80 e/o 443) e un dominio con DNS che punta al server.

#### Installare Docker (server senza Docker)

Se `docker --version` risponde `command not found`, installa con lo
script ufficiale (funziona su Ubuntu, Debian, Rocky, AlmaLinux,
Fedora, openSUSE, ecc.):

```bash
# Install Docker Engine + Compose plugin
curl -fsSL https://get.docker.com | sudo sh

# Aggiungi l'utente al gruppo docker (no sudo per i comandi successivi)
sudo usermod -aG docker $USER
newgrp docker

# Autostart al boot + avvio immediato
sudo systemctl enable --now docker

# Verifica
docker --version
docker compose version
docker run --rm hello-world
```

In alternativa segui la procedura manuale da
<https://docs.docker.com/engine/install/> per la tua distro
specifica.

### Prerequisiti esterni (servizi cloud)

Oltre al server Docker, la generazione dei contenuti richiede alcuni
servizi esterni. **Sono tutti opzionali**: senza, la piattaforma
funziona e le rispettive feature restano disabilitate (i task restano
in `pending` o le rotte rispondono con un errore di pre-condizione).

| Servizio | Necessario per | Variabili |
|---|---|---|
| **OpenAI** (o gateway compatibile) | Pipeline AI corso (Fasi 1-5, glossario, traduzioni i18n) | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
| **MiniMax** | Clip video dell'avatar utente | `MINIMAX_API_KEY` |
| **RunPod — endpoint TTS** | Video MP4 della lezione (Fase 6) | `RUNPOD_API_KEY`, `RUNPOD_TTS_ENDPOINT_ID` |
| **RunPod — endpoint MuseTalk** | "Video con Avatar" (Fase 6b) | `RUNPOD_API_KEY` (riusato), `RUNPOD_MUSETALK_ENDPOINT_ID` |
| **Cloudflare R2** | "Video con Avatar" — storage di transito | `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |

#### RunPod — due endpoint Serverless GPU

La generazione video delega i task GPU-intensivi a **due endpoint RunPod
Serverless GPU** distinti, sullo stesso account (la `RUNPOD_API_KEY` è
unica):

1. **Endpoint TTS XTTS-v2** — sintesi vocale del discorso. L'immagine
   Docker si costruisce **dalla cartella `XTTS/` del repo**:

   ```bash
   cd XTTS
   docker build --platform linux/amd64 -t <registry>/a4u-xtts:latest .
   docker push <registry>/a4u-xtts:latest
   ```

   `--platform linux/amd64` è obbligatorio (RunPod gira su x86, anche se
   builda da Mac ARM). Push su GHCR o Docker Hub, poi crea un endpoint
   RunPod Serverless da quell'immagine (GPU RTX 4090 / L40S, scale-to-zero
   con `Active Workers=0`, `Execution Timeout ≥ 900s`). Procedura
   dettagliata, contratto I/O e costi: `XTTS/README.md`.

2. **Endpoint MuseTalk** — lip-sync dell'avatar parlante. Endpoint
   serverless dedicato, costruito dall'immagine del progetto MuseTalk-API.
   Il client che lo invoca è vendored in `backend/app/musetalk_client/`
   ed è già nell'immagine del backend.

Annota gli **Endpoint ID** di entrambi e valorizza
`RUNPOD_TTS_ENDPOINT_ID` / `RUNPOD_MUSETALK_ENDPOINT_ID`.

#### Cloudflare R2 — bucket di transito

Il client MuseTalk usa un bucket R2 (S3-compatible) come storage di
transito per i file del job. Crea un bucket dalla dashboard Cloudflare,
genera un token S3 (access key id + secret) e valorizza le quattro
variabili `R2_*` (`R2_ENDPOINT` ha forma
`https://<account>.r2.cloudflarestorage.com`).

> **NB su `PUBLIC_BASE_URL`** — i worker RunPod scaricano via HTTP il
> campione vocale dell'avatar (`/uploads/...`) e l'immagine sorgente per
> MiniMax. In produzione `PUBLIC_BASE_URL` deve quindi puntare a un
> dominio pubblicamente raggiungibile; in dev locale serve un tunnel
> (ngrok). Vedi [04 — Configuration](04-configuration.md).

### Servizi e relativi runtime requirements

| Servizio | Immagine base | Runtime extra |
|---|---|---|
| `postgres` | `postgres:16-alpine` | – |
| `backend` | `python:3.12-slim` | Pango/Cairo (WeasyPrint), Chromium di Playwright (mermaid pre-render), `ffmpeg` (encoding video Fase 6/6b) — già nel `Dockerfile` |
| `frontend` | `node:20` (build) → `nginx:alpine` (runtime) | – |

> **Importante** — il `backend/Dockerfile` installa Pango/Cairo +
> Chromium con tutte le sue runtime deps (libnss, libgbm, libasound,
> ...) + `ffmpeg` nel layer runtime. NON serve installare nulla a parte
> sul host. Il TTS XTTS-v2 e il lip-sync MuseTalk **non** girano nel
> container backend: sono delegati a endpoint RunPod GPU (vedi
> "Prerequisiti esterni" sopra). Il client MuseTalk è vendored in
> `backend/app/musetalk_client/` e gira come subprocess.

### Workflow di deploy (prima volta)

```bash
# 1. Clona il repo
git clone https://github.com/micheleDibi/a4u.git
cd a4u

# 2. Configura le env vars di produzione
cp .env.example .env
# Edita .env con valori reali — vedi sotto la checklist

# 3. Avvia SOLO Postgres (deve essere healthy prima delle migrazioni)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d --build postgres

# 4. Aspetta che postgres sia healthy
until docker compose -f docker-compose.yml -f docker-compose.prod.yml ps postgres | grep -q healthy; do sleep 2; done

# 5. Migrazioni Alembic in container EFFIMERO (`run --rm` non `exec`).
#    L'app fa seed delle tabelle al boot via lifespan, quindi il backend
#    NON può partire finché lo schema non è creato — chicken-and-egg.
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm backend alembic upgrade head

# 6. (Opzionale) Restore del DB seed PRIMA di avviare il backend
gunzip -c /tmp/db_seed.sql.gz | docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres psql -U a4u -d a4u

# 7. Avvia backend + frontend
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d

# 8. Healthcheck (sostituisci :PORT con il valore di FRONTEND_PORT in .env)
curl http://localhost:${FRONTEND_PORT:-80}/api/v1/system/health
```

> **NB sul `run --rm` vs `exec`** — al primissimo deploy il container
> `backend` crasha al boot perché `ensure_seed()` in `lifespan` interroga
> tabelle che non esistono ancora. `docker compose exec` fallisce
> ("Container is restarting"). `run --rm backend alembic upgrade head`
> avvia un container effimero, esegue le migrazioni, esce — niente
> dipendenza dal backend running. Per migrazioni successive (dopo che
> il backend è healthy) puoi usare sia `run --rm` sia `exec`.

### `.env` di produzione — checklist minima

```env
# Database
POSTGRES_USER=a4u
POSTGRES_PASSWORD=<random_strong_pw_32+_chars>
POSTGRES_DB=a4u

# Backend
ENV=production
LOG_FORMAT=json
JWT_SECRET=<openssl rand -hex 48>
COOKIE_SECURE=true
COOKIE_DOMAIN=your-domain.com
FRONTEND_ORIGIN=https://your-domain.com
PUBLIC_BASE_URL=https://your-domain.com

# Bootstrap admin (opzionale — usato solo al primo avvio se DB vuoto)
BOOTSTRAP_ADMIN_EMAIL=admin@your-domain.com
BOOTSTRAP_ADMIN_PASSWORD=<change_immediately_after_first_login>
BOOTSTRAP_ADMIN_FULL_NAME=Platform Admin

# AI services (opzionali ma necessari per generazione contenuti)
OPENAI_API_KEY=sk-...
MINIMAX_API_KEY=...

# Video MP4 della lezione — Fase 6 (opzionale)
# Endpoint RunPod Serverless GPU del TTS XTTS-v2 (immagine da XTTS/).
RUNPOD_API_KEY=...
RUNPOD_TTS_ENDPOINT_ID=...

# "Video con Avatar" — Fase 6b (opzionale)
# Secondo endpoint RunPod (MuseTalk) + bucket Cloudflare R2 di transito.
RUNPOD_MUSETALK_ENDPOINT_ID=...
R2_ENDPOINT=https://<account>.r2.cloudflarestorage.com
R2_BUCKET=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...

# Error monitoring (opzionale)
SENTRY_DSN=

# Porte
FRONTEND_PORT=80
BACKEND_PORT=127.0.0.1:8000   # vedi sezione "Configurare le porte" sotto
```

> **NB su `JWT_SECRET`**: rigenerare il secret invalida tutte le
> sessioni attive. Se cambi `JWT_SECRET` su un sistema con utenti
> loggati, dovranno fare nuovamente login.

### Configurare le porte (frontend e backend)

Lo stack espone due servizi sul host. Entrambi sono configurabili via env.

| Variabile | Default | Effetto |
|---|---|---|
| `FRONTEND_PORT` | `80` | Porta pubblica del frontend nginx (serve dist + proxy a `/api/`). |
| `BACKEND_PORT` | `127.0.0.1:8000` | Backend FastAPI. Default = solo localhost del server (NON pubblico). |

**Casi tipici per `FRONTEND_PORT`:**

```env
FRONTEND_PORT=80         # default — produzione standard
FRONTEND_PORT=8080       # se la 80 è occupata da altro
FRONTEND_PORT=443        # solo se gestisci TLS dentro nginx (raro: meglio reverse proxy esterno)
```

**Casi tipici per `BACKEND_PORT`:**

```env
# Default: NON esposto pubblicamente, solo accessibile dal server stesso.
# Adatto al 95% dei casi: il traffico passa via il proxy nginx del frontend.
BACKEND_PORT=127.0.0.1:8000

# Esposizione su tutte le interfacce, porta 8000.
# Necessario se webhook esterni o app mobile devono colpire il backend
# direttamente, scavalcando il frontend.
BACKEND_PORT=8000

# Solo su una specifica interfaccia di rete (multi-homed server).
BACKEND_PORT=192.168.1.10:8000

# Porta non standard, su tutte le interfacce.
BACKEND_PORT=9001
```

> ⚠️ Esporre il backend pubblicamente (`BACKEND_PORT=8000`) bypassa
> il reverse proxy nginx, che attua header `X-Forwarded-*`, gzip,
> SPA fallback. Assicurati che `FRONTEND_ORIGIN` e `COOKIE_DOMAIN`
> siano coerenti, e considera comunque di metterci un proxy davanti
> per TLS.

### Servizi del compose

#### `postgres`
- Immagine `postgres:16-alpine`.
- `ports: []` in produzione (non esposto pubblicamente).
- Volume persistente `postgres-data`.
- Healthcheck via `pg_isready`.

#### `backend`
- Build da `backend/Dockerfile` (multi-stage, user non-root).
- Env: `ENV=production`, `LOG_FORMAT=json`, `COOKIE_SECURE=true`.
- `DATABASE_URL` punta a `postgres` (rete docker).
- Volumi persistenti:
  - `uploads:/app/uploads` — loghi org + PDF template, avatar, materiali
    corso, video MP4 generati (`lesson_videos/`, `lesson_avatar_videos/`),
    cache audio TTS (`lesson_audio/`), manifest MuseTalk
    (`musetalk_manifests/`).
  - `generated_pdfs:/app/generated_pdfs` — PDF lezioni renderizzati (sovrascritti ad ogni rigenerazione).
- Chromium di Playwright in `/ms-playwright` (path condiviso, env
  `PLAYWRIGHT_BROWSERS_PATH`). Usato per pre-render Mermaid → SVG (il PDF
  finale è prodotto da WeasyPrint, vedi
  [09 — PDF export](courses/09-pdf-export.md)) e per il rendering delle
  slide a PNG nella generazione video (Fase 6).
- `ffmpeg` per l'encoding del video MP4 della lezione e l'overlay
  dell'avatar (Fasi 6/6b).
- Tutti gli env knob significativi (MiniMax, RunPod TTS + MuseTalk, R2,
  OpenAI per ogni fase, worker concurrency/auto-retry, reasoning_effort,
  encoding video, overlay avatar) sono forwardati con `${VAR:-default}`
  nel blocco `environment:` di `docker-compose.prod.yml` → puoi
  sovrascriverli in `.env` senza toccare il compose file. Le credenziali
  RunPod/R2 vengono lette dal backend e propagate al subprocess MuseTalk.
- Healthcheck su `/api/v1/system/health`.

#### `frontend`
- Build da `frontend/Dockerfile` (`node:20` build → `nginx:alpine` runtime).
- Serve `dist/` con `nginx.conf` (gzip, header di sicurezza, SPA fallback).
- Proxy `/api/` e `/uploads/` verso `backend:8000`.
- Limite upload: `client_max_body_size 25m` configurato in
  `frontend/nginx.conf` (file più grandi servono per documenti corso DOC/PDF
  voluminosi). Se devi alzare il limite, modifica anche `COURSE_DOCUMENT_MAX_MB`
  + `UPLOAD_MAX_MB` lato backend per coerenza.
- Esporre porta `${FRONTEND_PORT:-80}` (poi reverse proxy esterno per TLS).

### Reverse proxy esterno (consigliato)

Lo stack espone solo HTTP sulla porta 80 (frontend nginx). Per
produzione mettere un reverse proxy davanti per TLS, HSTS, CDN, WAF.

#### Caddy (più semplice — TLS automatico via Let's Encrypt)

`/etc/caddy/Caddyfile`:
```caddyfile
your-domain.com {
    reverse_proxy localhost:80
    encode gzip zstd
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
    }
}
```

```bash
sudo systemctl reload caddy
```

#### Nginx + Certbot (alternativa)

Vedi documentazione standard. Esempio minimo:
```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
server { listen 80; server_name your-domain.com; return 301 https://$host$request_uri; }
```

## Seeding iniziale del DB (restore di un dump)

Se vuoi importare lo stato di un DB esistente (es. lo stato del DB di
sviluppo) in un nuovo deploy, segui questo flusso.

### 1. Generare il dump dal DB sorgente

Sul **sistema sorgente** (es. il dev locale dove è il container
`a4u-postgres`):

```bash
# Dump compresso plaintext SQL (più portabile, leggibile in caso di debug)
docker exec a4u-postgres pg_dump -U a4u -d a4u \
    --clean --if-exists --no-owner --no-privileges \
    | gzip > deploy/seed/db_seed.sql.gz
```

Flag importanti:
- `--clean --if-exists`: il dump inizia con `DROP TABLE IF EXISTS` per
  ogni oggetto, così il restore può sovrascrivere uno schema esistente
  (es. quello creato da `alembic upgrade head`) senza errori.
- `--no-owner --no-privileges`: rimuove `ALTER ... OWNER TO ...` e
  `GRANT`, così il dump funziona anche se il DB di destinazione ha un
  utente con nome diverso.

> Il file `db_seed.sql.gz` è gitignorato (contiene email + password
> hashate degli utenti). Va trasferito via canali sicuri.

### 2. Trasferire al server

```bash
# Da locale al server
scp deploy/seed/db_seed.sql.gz user@server:/tmp/
```

### 3. Restore sul server

```bash
ssh user@server
cd /path/to/a4u

# Assicurati che lo stack sia su (postgres healthy)
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

# Caso A: schema vuoto (mai eseguito alembic upgrade head)
gunzip -c /tmp/db_seed.sql.gz \
    | docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres psql -U a4u -d a4u

# Caso B: schema già migrato (alembic upgrade head fatto in precedenza)
# Stesso comando: il dump usa --clean --if-exists, quindi droppa e
# ricrea ogni oggetto. NON serve droppare/ricreare il DB a mano.
gunzip -c /tmp/db_seed.sql.gz \
    | docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres psql -U a4u -d a4u
```

### 4. Riallineare la versione Alembic

Dopo il restore lo schema è quello del momento del dump. Se il
deployment ha migration più recenti, esegui:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend alembic upgrade head
```

Questo applicherà eventuali migration successive senza ricreare lo schema.

### 5. Verifica utenti

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec postgres \
    psql -U a4u -d a4u -c "SELECT email, is_platform_admin, created_at FROM \"user\" ORDER BY created_at;"
```

> **Importante**: se hai usato il dump di sviluppo, gli admin sono
> quelli del dev. Cambia subito le password (login → menu utente →
> "Cambia password") oppure resetta tutte le password via SQL admin.

## CI/CD (GitHub Actions)

Due workflow in `.github/workflows/`:

### `backend-ci.yml`

Trigger: push/PR su path `backend/**`.

Step:
1. Setup Python 3.12 con `actions/setup-python` (cache `pip`).
2. `pip install -e ".[dev]"`.
3. `ruff check .`.
4. `ruff format --check .`.
5. `mypy app`.
6. Avvia Postgres (service container).
7. `alembic upgrade head`.
8. `pytest -q`.

### `frontend-ci.yml`

Trigger: push/PR su path `frontend/**`.

Step:
1. Setup Node 20 + cache npm.
2. `npm ci`.
3. `npm run lint`.
4. `npm run type-check`.
5. `npm run build`.

## Deploy rolling

Per zero-downtime con docker compose:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml build backend
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps --no-build backend
# Postgres non si tocca; le migrazioni le esegui prima del rollout backend.
```

Per Kubernetes / cloud, usare le stesse immagini con readiness probe su
`/api/v1/system/ready` e liveness su `/api/v1/system/health`.

## Backup e ripristino periodici

```bash
# Backup giornaliero del database (cron sul server)
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres \
    pg_dump -U a4u -d a4u --clean --if-exists --no-owner --no-privileges \
    | gzip > /backup/a4u-$(date +%F).sql.gz

# Backup degli uploads
docker run --rm -v a4u_uploads:/data -v /backup:/backup alpine \
    tar czf /backup/uploads-$(date +%F).tar.gz -C /data .

# (Opzionale) Backup dei PDF generati. NB: i PDF sono ricostruibili
# dal contenuto lezione → backup non strettamente necessario, ma utile
# se vuoi evitare al cliente l'attesa del re-rendering.
docker run --rm -v a4u_generated_pdfs:/data -v /backup:/backup alpine \
    tar czf /backup/generated_pdfs-$(date +%F).tar.gz -C /data .

# Ripristino DB
gunzip -c /backup/a4u-2026-04-26.sql.gz \
    | docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres psql -U a4u -d a4u

# Ripristino uploads
docker run --rm -v a4u_uploads:/data -v /backup:/backup alpine \
    tar xzf /backup/uploads-2026-04-26.tar.gz -C /data
```

## Osservabilità

### Logs strutturati

In produzione (`LOG_FORMAT=json`) ogni log è una riga JSON:

```json
{
  "event": "http_request",
  "method": "POST",
  "path": "/api/v1/auth/login",
  "status": 200,
  "duration_ms": 87.42,
  "request_id": "ab12cd34...",
  "user_id": null,
  "ip": "203.0.113.1",
  "user_agent": "...",
  "level": "info",
  "timestamp": "2026-04-26T10:30:00Z"
}
```

Indirizzare verso un aggregatore (Loki/Splunk/Datadog) tramite stdout.

### Metriche / tracing

Predisposizioni:

- `SENTRY_DSN`: se valorizzata, attiva error reporting (backend e frontend).
- OpenTelemetry: hook commentati nei middleware; integrazione futura.
- Prometheus: aggiungere `prometheus-fastapi-instrumentator` come step
  successivo (non incluso in questa iterazione).

### Audit log

Tabella `audit_logs`. Da consultare via SQL o esponendo un endpoint admin
read-only (futuro). Esempio:

```sql
SELECT created_at, action, actor_user_id, organization_id, payload
FROM audit_logs
WHERE created_at > now() - interval '24 hours'
ORDER BY created_at DESC;
```

## Hardening produzione checklist

- [ ] `JWT_SECRET` random ≥ 48 byte, da secret manager.
- [ ] `COOKIE_SECURE=true`, dominio configurato.
- [ ] HTTPS terminato dal reverse proxy.
- [ ] HSTS preload registrato.
- [ ] Backup automatici (giornalieri) DB + uploads.
- [ ] Sentry o equivalente attivo.
- [ ] Log centralizzati (stdout → aggregatore).
- [ ] Postgres con volumi persistenti su disco separato.
- [ ] Bootstrap admin **NON** committato nelle env vars; cambiata password subito.
- [ ] CSP testata con `Content-Security-Policy-Report-Only` prima di enforce.
- [ ] Network policy: solo il frontend espone porta 80/443; backend e Postgres
       non esposti pubblicamente.
- [ ] Dipendenze aggiornate (Renovate / Dependabot).
- [ ] Pipeline CI obbligatoria su main.

## Troubleshooting

### Backend container non si avvia con `cannot load library libpango-1.0-0`

Il `backend/Dockerfile` deve installare le runtime deps di WeasyPrint
(`libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`,
`libgdk-pixbuf-2.0-0`, `libffi8`). Verifica che il Dockerfile sia
aggiornato e ribuilda l'immagine:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache backend
```

### Mermaid SVG non viene generato (PDF mostra il fallback `<pre>` con il codice)

Il backend non riesce a lanciare Chromium. Verifica:

```bash
# Chromium presente nell'immagine?
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend \
    ls /ms-playwright/chromium-*/chrome-linux/chrome

# Test manuale
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend \
    python -c "from playwright.sync_api import sync_playwright; \
               p = sync_playwright().start(); b = p.chromium.launch(args=['--no-sandbox']); print('OK'); b.close(); p.stop()"
```

Se il browser binary manca, il `Dockerfile` non ha eseguito
`playwright install chromium` durante il build. Verifica e ricostruisci.

### Errore `connection refused` da frontend → backend

Verifica che il backend sia healthy:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs backend --tail=50
```

Il frontend usa `proxy_pass http://backend:8000` (DNS interno docker
compose). Se il backend non è healthy, nginx restituisce 502.

### `Permission denied` su `/app/generated_pdfs/...` durante export PDF

Il `Dockerfile` deve creare la directory + chown ad app prima dello
`USER app`. Verifica nel Dockerfile:

```dockerfile
RUN mkdir -p /app/uploads/organizations /app/uploads/avatars /app/uploads/templates \
             /app/generated_pdfs \
    && chown -R app:app /app/uploads /app/generated_pdfs
```

Se hai upgradato un container vecchio dove la dir non esisteva, ricrea il
volume:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker volume rm a4u_generated_pdfs
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
# Le lezioni con pdf_status='failed' a causa della permission errata
# vanno resettate via SQL:
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres \
    psql -U a4u -d a4u -c "UPDATE course_lesson SET pdf_status='empty', pdf_attempts=0, pdf_error=NULL WHERE pdf_status='failed' AND pdf_error LIKE '%Permission denied%';"
```

### Storm di log `lesson_*_skip_not_pending` ad alto rate

Era un bug del worker risolto in `87fbf70`. Se vedi questi log su un
deploy vecchio, esegui `git pull` + `docker compose ... up -d --build`
per applicare il fix (claim atomico in `_tick` invece che dentro
`async with _semaphore`).

### Generazione architettura/lezioni si blocca con OpenAI 200 + content vuoto

Causa tipica: `max_completion_tokens` insufficiente per il reasoning del
modello. I gpt-5.x consumano molti token nel "pensiero interno" prima di
emettere JSON, e se il cap è troppo basso il provider risponde con
`finish_reason="length"` e content vuoto. Soluzioni:

1. Alza `OPENAI_*_MAX_TOKENS` (vedi default in
   [04 — Configuration](04-configuration.md)).
2. Abbassa `OPENAI_*_REASONING_EFFORT` (es. `high → medium`): meno token
   in reasoning, più budget per il JSON.
3. Switch a un modello classico non-reasoning (`gpt-4o`): più veloce,
   meno qualità sui prompt complessi, ma niente "reasoning tokens".
