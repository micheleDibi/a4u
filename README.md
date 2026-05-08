# a4u — Piattaforma per la generazione di corsi universitari

Monorepo con backend FastAPI, frontend React + Vite + Tailwind, database
PostgreSQL. La piattaforma copre l'intero ciclo di vita di un corso
universitario:

1. **Pre-processing** dei materiali sorgente (PDF, DOCX, RTF, TXT) con
   estrazione testo e summary.
2. **Generazione AI** dell'architettura del corso (moduli + lezioni).
3. **Generazione AI** della struttura delle lezioni (sezioni + asset).
4. **Generazione AI** del contenuto delle lezioni (testo, equazioni
   LaTeX, diagrammi Mermaid, tabelle, esempi, riferimenti).
5. **Editing manuale** di ogni livello con editor specializzati
   (rich-text TipTap per il body, KaTeX preview per le formule, live
   preview Mermaid per i diagrammi, table editor visuale).
6. **Export PDF** per lezione con template grafico configurabile
   (sfondo, loghi, font, colori, page size, margini).

Il dominio multi-tenant: ogni utente appartiene a una o più
organizzazioni con permessi granulari per-azione. Auth basata su
HttpOnly cookies con refresh-token rotation, audit log immutabile,
rate limiting e lockout.

## Stack tecnologico

| Layer | Tecnologie |
|---|---|
| Backend API | FastAPI · SQLAlchemy 2 async · asyncpg · Pydantic v2 · structlog |
| Migrations | Alembic |
| AI generator | OpenAI / Anthropic (model swap via config); batch parallelo con Semaphore + progress % |
| PDF export | **WeasyPrint** (CSS Paged Media) · `latex2mathml` per equazioni · Playwright headless solo per pre-render Mermaid → SVG |
| Document extract | `pdfplumber`, `python-docx`, `docx2txt`, `striprtf` |
| Frontend | React 18 · Vite · TypeScript · Tailwind 4 · Radix UI (shadcn pattern) · TanStack Query · React Hook Form + Zod · i18next (24 locali) · TipTap · KaTeX · Mermaid |
| Database | PostgreSQL 16 |
| Deploy | Docker (multi-stage) · Nginx |

## Prerequisiti

- **Docker Desktop** (per Postgres in dev).
- **Python 3.12** dedicato (su Windows: `py -3.12 --version`; altrimenti
  da python.org). Lo usiamo per il venv del backend, separato dalla
  Python di sistema.
- **Node.js 20+** con `npm`.
- **GTK3 runtime** (solo Windows local-dev, per WeasyPrint):
  ```powershell
  winget install tschoonj.GTKForWindows
  ```
  Su Linux/Docker il runtime è gestito dal Dockerfile (Pango, Cairo,
  HarfBuzz, Pixbuf, fonts DejaVu/Liberation).
- **Playwright Chromium** (per il pre-render dei diagrammi Mermaid):
  ```bash
  cd backend && .venv/Scripts/python.exe -m playwright install chromium
  ```

## Avvio rapido (development)

```bash
# 1. Env vars (root + frontend)
cp .env.example .env                       # docker-compose + backend
cp frontend/.env.example frontend/.env     # Vite (client-side)

# 2. PostgreSQL
docker compose up -d postgres

# 3. Backend
cd backend
py -3.12 -m venv .venv
source .venv/Scripts/activate              # macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
python -m playwright install chromium      # browser per pre-render Mermaid

alembic upgrade head                       # schema + seed permessi/ruoli/admin
uvicorn app.main:app --reload --port 8000

# 4. Frontend (terminale separato)
cd frontend
npm install
npm run start                              # vite, porta 5173
```

Apri `http://localhost:5173` ed effettua il login con
`BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` (vedi `.env`).

## Struttura

```
a4u/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routers
│   │   ├── core/           # config, logging, errors, audit, security
│   │   ├── db/             # AsyncEngine, base, session
│   │   ├── models/         # SQLAlchemy models
│   │   ├── schemas/        # Pydantic request/response
│   │   ├── services/       # business logic (corsi, AI, PDF, workers)
│   │   ├── templates/      # Jinja2 lesson_pdf.html.j2
│   │   ├── middleware/     # CORS, request id, error handler
│   │   ├── i18n/           # backend strings
│   │   ├── utils/          # pagination ecc.
│   │   └── main.py         # FastAPI app + lifespan workers
│   ├── alembic/            # migrations
│   ├── tests/              # smoke tests
│   ├── Dockerfile          # multi-stage con Pango/Cairo per WeasyPrint
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/            # axios clients (TanStack Query)
│   │   ├── auth/           # login, ProtectedRoute, providers
│   │   ├── components/     # shared (forms, dialogs, KaTeX, Mermaid, ecc.)
│   │   ├── hooks/          # custom hooks
│   │   ├── i18n/           # i18next + 24 locali
│   │   ├── pages/          # admin, org, courses, templates
│   │   ├── providers/      # query client, auth, theme
│   │   ├── routes/         # router + role gates
│   │   └── App.tsx
│   ├── public/
│   ├── Dockerfile          # build + nginx static serve
│   └── package.json
├── docs/                   # documentazione platform + dominio corsi
│   ├── courses/            # 9 file: data model, AI generation, PDF export, ...
│   ├── backend/
│   ├── frontend/
│   └── *.md
├── docker-compose.yml      # dev: solo Postgres
├── docker-compose.prod.yml # prod: backend + frontend + Postgres
└── .github/workflows/      # CI backend + frontend
```

## Documentazione

Documentazione approfondita in `docs/`:

- **Top-level**: overview, architecture, getting-started, configuration,
  security, permissions, deployment, api-reference.
- **Backend deep-dive** (`docs/backend/`): entry, core, middleware, DB,
  models, schemas, services, API, utils, alembic, tests.
- **Frontend deep-dive** (`docs/frontend/`): entry, API client, auth,
  routing, components, pages, lib, hooks, i18n.
- **Domain corsi** (`docs/courses/`): per fase di pipeline:
  1. data model
  2. document preprocessing
  3. architecture generation
  4. manual editing
  5. API reference
  6. frontend
  7. lesson structure
  8. lesson content
  9. **PDF export** (WeasyPrint + CSS Paged Media)

## Test

```bash
cd backend && source .venv/Scripts/activate && pytest
cd frontend && npm run lint && npm run type-check
```

## Note di sicurezza

- Cookie HttpOnly + SameSite=Lax + Secure (in `production`).
- Refresh-token con rotation e reuse-detection (chain-revoke).
- Rate limit su login (`slowapi`) + lockout dopo 10 tentativi falliti.
- Audit log immutabile per azioni sensibili.
- Upload immagini ri-encoded da Pillow (strip EXIF, anti path-traversal).
- Security headers + middleware CSRF check su mutating endpoints.
- Path-traversal protection sugli asset upload (`Path.relative_to(upload_root)`).

## Auto-retry trasparente sui worker AI

I worker di generazione (architecture / structure / content / PDF)
implementano auto-retry: in caso di errore (timeout AI, parsing
failure, Playwright crash, ecc.) ripristinano `pending` invece di
transire a `failed`, finché `attempts < auto_retry_max` (default 5).
La UI vede solo "in elaborazione" durante i retry, mai i messaggi di
errore intermedi. Solo dopo `auto_retry_max` esaurito → `failed`.

## Roadmap

- [ ] Generazione PPTX dai template slide (oggi solo PDF lezione).
- [ ] PDF aggregato di intero corso (oggi solo per-lezione).
- [ ] Object storage S3/MinIO per asset (oggi filesystem locale).
- [ ] Streaming SSE del progresso (oggi polling).
- [ ] Diff-detection automatica tra `content_raw` modificato e PDF già
      generato (oggi flag manuale "Rigenera PDF").
- [ ] Invio email reali per inviti / reset password.
- [ ] Pre-render Mermaid offline (oggi via CDN jsdelivr).
- [ ] Spell-check e linting LaTeX (oggi solo errore visivo nel preview).
