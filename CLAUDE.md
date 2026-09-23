# Progetto: a4u — Avatar4University

SaaS multi-tenant che genera corsi universitari con l'AI: documenti → architettura → dispense → slide → discorso → video con avatar.
Sviluppo in locale (Postgres in Docker, app nativa); produzione con docker compose, deploy manuale (nessuna CD).

## Stack
- Backend: Python 3.12 (`>=3.12,<3.13`), FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, pydantic-settings
- Frontend: TypeScript, React 18 + Vite 5, TanStack Query, react-i18next, Tailwind 4
- Database: PostgreSQL 16 (dev: `docker compose up -d postgres`; DB dei test `a4u_test`, ricreato dalla suite)
- Servizi esterni: OpenAI, RunPod (TTS XTTS + MuseTalk), MiniMax, Cloudflare R2; PDF con WeasyPrint, render con Playwright/Chromium, ffmpeg

## Mappa del codice
- `backend/app/api/v1/` — router FastAPI
- `backend/app/services/` — logica di business (fasi corso, AI, PDF, video) e worker in background
- `backend/app/models/`, `schemas/`, `db/` — modelli SQLAlchemy, schemi Pydantic, engine/sessione/seed
- `backend/app/core/` — config, errori, permessi, `course_phase_order.py`, `prompt_safety.py`
- `backend/app/templates/` — Jinja2 dei tre PDF (dispense, slide, discorso)
- `backend/alembic/versions/` — migrazioni
- `frontend/src/pages/`, `components/`, `api/`, `i18n/` — pagine, componenti condivisi, client REST, traduzioni
- `XTTS/` — immagine del worker RunPod per il TTS (progetto separato)
- `docs/` — documentazione di piattaforma e del dominio corsi; `docs/PROMPTS.md` descrive i prompt reali
- Punti di ingresso: `backend/app/main.py` (app + worker nel lifespan), `frontend/src/main.tsx`
- Configurazione: `backend/app/core/config.py` + `.env` alla root, `docker-compose.prod.yml` (NON modificare senza chiedere)

## Comandi
- Avvio locale: `docker compose up -d postgres`; poi `cd backend && alembic upgrade head && uvicorn app.main:app --reload --port 8000`; poi `cd frontend && npm run start` (porta 5173)
- Test di un singolo file: `cd backend && DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python3 -m pytest tests/test_<nome>.py -q`
- Test completi backend: `cd backend && DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python3 -m pytest -q` (durata [DA VERIFICARE])
- Frontend (non c'è un test runner): `cd frontend && npm run lint && npm run type-check && npm run build`
- Lint/format backend (come in CI): `cd backend && ruff check . && ruff format --check . && mypy app`
- Coerenza di docs/PROMPTS.md con i prompt: `cd backend && JWT_SECRET=$(printf 'x%.0s' $(seq 1 40)) python3 -m scripts.check_prompts_md`

## Convenzioni
- Codice e nomi in inglese; commenti, docstring e documentazione in italiano
- Commit: prefisso Conventional Commits (`feat(pdf):`, `fix(content):`, `docs:`) + descrizione in italiano
- Ruff: riga da 100 caratteri, target py312
- i18n frontend: nuove stringhe SOLO in `frontend/src/i18n/locales/it.json` e `en.json` (le altre 22 lingue si completano dalla UI)
- Worker AI: sugli errori recuperabili tornano a `pending` (auto-retry) invece di passare a `failed`; solo i CRUD manuali impostano i `*_modified_at`, i worker mai
- Nuova variabile d'ambiente: campo in `Settings` + riga in `.env.example` + riga `VAR: ${VAR:-default}` in `docker-compose.prod.yml` se va sovrascritta in prod
- Input utente verso l'LLM: passa sempre da `core/prompt_safety.py`

## Da non toccare
- `backend/app/musetalk_client/` — copia verbatim del client MuseTalk ("NON MODIFICARE")
- `backend/app/i18n/seed_locales/`
- `XTTS/` — ha un prompt dedicato (`docs/hpc/PROMPT_XTTS.md`)
- Build e cache: `frontend/dist/`, `frontend/node_modules/`, `*.tsbuildinfo`, `.ruff_cache/`, `__pycache__/`

## Trappole note
- `docker-compose.yml` contiene SOLO Postgres: `docker compose up` non avvia l'app
- In prod usare entrambi i file compose e lanciare le migrazioni prima dell'avvio: `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env run --rm backend alembic upgrade head`, poi `up -d`
- Una variabile d'ambiente che manca nel blocco `environment:` di `docker-compose.prod.yml` in prod prende in silenzio il default del codice
- `VITE_*` entrano nell'immagine frontend in fase di build: per cambiarle serve `docker compose build frontend`
- I test creano lo schema con `Base.metadata.create_all`, non con Alembic: CHECK, indici e vincoli vanno dichiarati anche nei modelli
- Su macOS WeasyPrint richiede `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`
- I test che richiedono Chromium, rete (CDN Mermaid), `dot`, vl-convert, sympy o matplotlib vengono saltati, con il motivo, se la dipendenza manca
- La CI parte solo con modifiche in `backend/**` o `frontend/**`: modifiche a root, compose o docs non la avviano
- `COURSE_STATUS_RANK` in `CoursePhaseStepper.tsx` duplica `backend/app/core/course_phase_order.py`: vanno tenuti allineati
