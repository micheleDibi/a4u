# a4u — Piattaforma corsi universitari

Monorepo con backend FastAPI, frontend React+Vite e database PostgreSQL.
Lo scaffolding di questa fase comprende autenticazione, gestione organizzazioni,
membership con ruoli e permessi configurabili, template slide/PDF con preview live
e avatar (placeholder). I corsi saranno aggiunti in un'iterazione successiva.

## Prerequisiti

- Docker Desktop (per Postgres).
- **Python 3.12** installato a parte (su Windows verifica con `py -3.12 --version`; se manca, scaricalo da python.org). Lo usiamo per creare il venv del backend; la Python di sistema può essere diversa.
- Node.js 20+ con `npm`.

## Avvio rapido (development)

```bash
# 1. Copia le env vars (un solo file alla root + uno per il frontend)
cp .env.example .env                 # letto da docker-compose E dal backend FastAPI
cp frontend/.env.example frontend/.env  # letto da Vite

# 2. Avvia PostgreSQL
docker compose up -d postgres

# 3. Backend (Git Bash su Windows)
cd backend
py -3.12 -m venv .venv                    # venv con Python 3.12
source .venv/Scripts/activate             # macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"                   # installa il progetto + tool di sviluppo

alembic upgrade head                      # crea schema + seed permessi/ruoli/admin
uvicorn app.main:app --reload --port 8000

# 4. Frontend (in un altro terminale)
cd frontend
npm install
npm run start                             # alias di vite (porta 5173)
```

Apri `http://localhost:5173` ed effettua il login con le credenziali di
`BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`.

## Struttura

- `backend/` — FastAPI, SQLAlchemy 2 async, Alembic, structlog
- `frontend/` — React 18, Vite, TypeScript, Material UI v6
- `docker-compose.yml` — Postgres 16
- `docker-compose.prod.yml` — overlay per produzione (immagini built, no bind-mount)

## Test

```bash
cd backend && source .venv/Scripts/activate && pytest
cd frontend && npm run lint && npm run type-check
```

## Note di sicurezza (production-ready)

- Cookie HttpOnly + SameSite=Lax + Secure (in `production`).
- Refresh token con rotation e reuse-detection (chain-revoke).
- Rate limit su login (`slowapi`) e lockout dopo 10 tentativi falliti.
- Audit log immutabile per azioni sensibili.
- Upload immagini ri-encoded da Pillow (strip EXIF, anti path-traversal).
- Security headers + middleware CSRF check su mutating endpoints.

## Roadmap

- [ ] Modello e API per i **corsi** (da definire con specifica dettagliata).
- [ ] Generazione PPTX/PDF effettiva dai template (oggi solo preview HTML).
- [ ] Invio email reali per inviti / reset password.
- [ ] Pipeline CI/CD attiva (oggi solo workflow base).
