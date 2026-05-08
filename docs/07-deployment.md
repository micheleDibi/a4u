# 07 — Deployment

## Modalità sviluppo

Vedi [03 — Getting started](03-getting-started.md). Solo Postgres in Docker;
backend e frontend in processi locali con hot reload.

## Modalità produzione (Docker compose)

Tre servizi: `postgres`, `backend`, `frontend` (nginx serve i file statici e
fa proxy verso il backend).

### Prerequisiti server

- Linux con Docker Engine 24+ e Docker Compose plugin v2.
- Almeno **2 GB RAM** (Chromium di Playwright + WeasyPrint sono CPU+RAM
  intensivi durante l'export PDF; in idle ~400 MB).
- Almeno **5 GB disco** per immagini Docker, browser Chromium di
  Playwright (~300 MB), generated PDFs e uploads.
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

### Servizi e relativi runtime requirements

| Servizio | Immagine base | Runtime extra |
|---|---|---|
| `postgres` | `postgres:16-alpine` | – |
| `backend` | `python:3.12-slim` | Pango/Cairo (WeasyPrint), Chromium di Playwright (mermaid pre-render) — già nel `Dockerfile` |
| `frontend` | `node:20` (build) → `nginx:alpine` (runtime) | – |

> **Importante** — il `backend/Dockerfile` installa Pango/Cairo +
> Chromium con tutte le sue runtime deps (libnss, libgbm, libasound,
> ...) nel layer runtime. NON serve installare nulla a parte sul host.

### Workflow di deploy (prima volta)

```bash
# 1. Clona il repo
git clone https://github.com/micheleDibi/a4u.git
cd a4u

# 2. Configura le env vars di produzione
cp .env.example .env
# Edita .env con valori reali — vedi sotto la checklist

# 3. Build delle immagini + avvio di tutto lo stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d --build

# 4. Migrazioni Alembic (crea schema + seed permessi/ruoli/admin)
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend alembic upgrade head

# 5. (Opzionale) Restore del DB seed — vedi sezione dedicata sotto
gunzip -c /tmp/db_seed.sql.gz | docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres psql -U a4u -d a4u

# 6. Healthcheck
curl http://localhost/api/v1/system/health
```

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
- Volume `uploads:/app/uploads` per persistenza upload (loghi PDF
  template, avatar, materiali corso).
- Chromium di Playwright in `/ms-playwright` (path condiviso, env
  `PLAYWRIGHT_BROWSERS_PATH`).
- Healthcheck su `/api/v1/system/health`.

#### `frontend`
- Build da `frontend/Dockerfile` (`node:20` build → `nginx:alpine` runtime).
- Serve `dist/` con `nginx.conf` (gzip, header di sicurezza, SPA fallback).
- Proxy `/api/` e `/uploads/` verso `backend:8000`.
- Esporre porta 80 (poi reverse proxy esterno per TLS).

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
