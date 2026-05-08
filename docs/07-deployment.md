# 07 — Deployment

## Modalità sviluppo

Vedi [03 — Getting started](03-getting-started.md). Solo Postgres in Docker;
backend e frontend in processi locali con hot reload.

## Modalità produzione (Docker compose)

Tre servizi: `postgres`, `backend`, `frontend` (nginx serve i file statici e
fa proxy verso il backend).

```bash
# Configurare le env vars produzione
cp .env.example .env.production
# editare .env.production con valori reali

# Build + avvio
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d --build

# Migrazioni
docker compose exec backend alembic upgrade head

# Verifica
curl https://your-domain/api/v1/system/health
```

### docker-compose.prod.yml

Override del `docker-compose.yml` con:

- `postgres`: `ports: []` (non esposto pubblicamente).
- `backend`:
  - immagine buildata da `backend/Dockerfile` (multi-stage, user non-root).
  - env: `ENV=production`, `LOG_FORMAT=json`, `COOKIE_SECURE=true`.
  - `DATABASE_URL` punta a `postgres` (rete docker).
  - volume `uploads:/app/uploads` per persistenza upload.
  - healthcheck su `/api/v1/system/health`.
- `frontend`:
  - immagine multi-stage `node→nginx`.
  - serve `dist/` con `nginx.conf` (gzip, headers di sicurezza, SPA fallback).
  - proxy `/api/` e `/uploads/` verso `backend:8000`.
  - porta 80 esposta.

### Reverse proxy esterno (consigliato)

Mettere un reverse proxy davanti (Caddy, Nginx, Cloudflare) per:
- terminazione TLS / Let's Encrypt;
- CDN (asset statici frontend);
- WAF / DDoS;
- Header HSTS preload.

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
docker compose -f ... build backend
docker compose -f ... up -d --no-deps --no-build backend
# Postgres non si tocca; le migrazioni le esegui prima del rollout backend.
```

Per Kubernetes / cloud, usare le stesse immagini con readiness probe su
`/api/v1/system/ready` e liveness su `/api/v1/system/health`.

## Backup e ripristino

```bash
# Backup database
docker compose exec -T postgres pg_dump -U a4u -d a4u | gzip > backup-$(date +%F).sql.gz

# Backup uploads
tar czf uploads-$(date +%F).tar.gz -C backend uploads/

# Ripristino DB
gunzip -c backup-2026-04-26.sql.gz | docker compose exec -T postgres psql -U a4u -d a4u

# Ripristino uploads
tar xzf uploads-2026-04-26.tar.gz -C backend/
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
