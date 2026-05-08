# 03 — Getting started

## Prerequisiti

- **Docker Desktop**.
- **Python 3.12** installato sul sistema. Su Windows verifica con
  `py -3.12 --version`; se manca, scaricalo dall'installer ufficiale
  python.org (3.12.x). La Python di sistema può essere diversa: il venv
  del backend userà comunque 3.12.
- **Node.js 20+** con `npm`.
- **Git** (consigliato).

Sistemi testati: Windows 11 con shell `bash` (Git Bash), macOS, Linux.

## Setup primo avvio

Dalla root del repo:

```bash
# 1. Variabili d'ambiente
#    `.env` alla root è l'unico file condiviso tra docker-compose e backend.
#    Il frontend ha il proprio file (Vite legge solo dalla sua project root).
cp .env.example .env
cp frontend/.env.example frontend/.env

# 2. Database
docker compose up -d postgres
# verifica: docker compose ps  → postgres healthy

# 3. Backend (Git Bash su Windows)
cd backend
py -3.12 -m venv .venv
source .venv/Scripts/activate           # macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"                 # progetto + tool di sviluppo

alembic upgrade head                    # schema + seed permessi/ruoli + bootstrap admin
uvicorn app.main:app --reload --port 8000

# 4. Frontend (in un altro terminale)
cd frontend
npm install
npm run start                           # alias di vite, porta 5173
```

> Su PowerShell l'attivazione del venv è `.venv\Scripts\Activate.ps1`;
> su `cmd` è `.venv\Scripts\activate.bat`.

Apri `http://localhost:5173` ed effettua il login con:
- Email: valore di `BOOTSTRAP_ADMIN_EMAIL` in `.env` (default `admin@a4u.local`).
- Password: valore di `BOOTSTRAP_ADMIN_PASSWORD` (default `ChangeMe123!`).

> Cambia questi valori prima di committare. La password deve avere almeno
> 10 caratteri, una maiuscola, un numero.

## Sessioni di lavoro successive

Una volta creato il `.venv`, basta riattivarlo:

```bash
cd backend
source .venv/Scripts/activate
uvicorn app.main:app --reload --port 8000
```

Per disattivarlo: `deactivate`.

## Comandi utili

### Backend

Tutti i comandi presuppongono il venv già attivato.

```bash
cd backend
source .venv/Scripts/activate

# Test
pytest                                  # smoke test (auth, permessi, health)
pytest -k auth                          # solo i test che matchano "auth"
pytest -x -vv                           # ferma al primo fail, verbose

# Lint & format
ruff check .                            # lint
ruff format .                           # format
mypy app                                # type check

# Migrazioni
alembic revision --autogenerate -m "descrizione"
alembic upgrade head
alembic downgrade -1
alembic history

# REPL interattivo con app
python -c "from app.main import app; print(app.routes)"
```

### Frontend

```bash
cd frontend

npm run start          # dev server (porta 5173, hot reload)
npm run build          # build produzione (dist/)
npm run preview        # serve localmente la build
npm run lint           # ESLint
npm run type-check     # tsc --noEmit
```

### Database

```bash
# Connessione psql (interattiva)
docker exec -it a4u-postgres psql -U a4u -d a4u

# Backup
docker exec a4u-postgres pg_dump -U a4u a4u > dump.sql

# Reset totale (perde dati!)
docker compose down -v && docker compose up -d postgres
cd backend && source .venv/Scripts/activate && alembic upgrade head
```

## Struttura del repository

```
a4u/
├── README.md                       # istruzioni di avvio sintetiche
├── docs/                           # questa documentazione
├── docker-compose.yml              # servizio postgres
├── docker-compose.prod.yml         # overlay produzione
├── .github/workflows/              # CI GitHub Actions
├── backend/                        # vedi docs/backend/
└── frontend/                       # vedi docs/frontend/
```

## Reset di sviluppo

Se vuoi ripartire da zero (perde dati e file caricati):

```bash
# Stop e rimuovi volumi Postgres
docker compose down -v

# Pulisci uploads
rm -rf backend/uploads/{organizations,avatars,templates,courses}/*
mkdir -p backend/uploads/{organizations,avatars,templates,courses}
touch backend/uploads/{organizations,avatars,templates,courses}/.gitkeep

# `uploads/avatars/<user_id>/` è creata automaticamente al primo upload.
# `uploads/courses/<course_id>/` è creata automaticamente al primo upload documento.

# Re-avvia
docker compose up -d postgres
cd backend && source .venv/Scripts/activate && alembic upgrade head
```

## Risoluzione problemi

| Sintomo | Causa probabile | Soluzione |
|---|---|---|
| `connection refused localhost:5432` | Postgres non in healthcheck | `docker compose ps` e attendere; `docker compose logs postgres` |
| `ImportError bcrypt` durante `pip install` | hai usato Python ≥ 3.13 dove bcrypt 4.0.1 manca di wheel | ricrea il venv con `py -3.12 -m venv .venv` |
| `pip install -e ".[dev]"` molto lento | nessun cache locale | normale al primo lancio; le successive sono veloci |
| `uvicorn: command not found` | venv non attivato | `source .venv/Scripts/activate` |
| `401 token_invalid` dopo edit `.env` | `JWT_SECRET` cambiato | logout (cancella cookie) o `TRUNCATE refresh_tokens` |
| `403 csrf_origin_invalid` | Origin browser non corrisponde | aggiornare `FRONTEND_ORIGIN` in `.env` |
| Frontend non vede api in dev | proxy Vite non attivo | controllare `frontend/vite.config.ts` (proxy /api e /uploads) |
| Test pytest non trovano DB | DATABASE_URL test non corretto | impostare `DATABASE_URL` in env oppure variabile in `tests/conftest.py` |
| Documenti corso restano in `pending` | `OPENAI_API_KEY` mancante o `course_document_worker` non avviato | verifica la key in `.env` + log avvio (`worker_started course_document`) |
| Architettura corso resta in `architecture_pending` | come sopra ma per il worker architettura | verifica `worker_started course_architecture` nei log |
| `[OpenAI 400] Unsupported parameter: 'max_tokens'` | hai cambiato `OPENAI_*_MODEL` a un modello che richiede `max_completion_tokens` (es. gpt-5.x) | il codice già usa il param corretto per gpt-5.x; se cambi model verifica compatibilità |
| `timeout exceeded 20000 ms` su generate-lessons | manca timeout override sull'axios call | il codice ha `timeout: 300_000`; se hai customizzato, ripristina |
| `UniqueViolationError uq_course_lesson_code` su reorder | tentativo di renumber per-modulo (bug storico) | la fix applica bumping globale `_tmp_{counter}`, vedi `course_architecture_crud.reorder_modules` |
