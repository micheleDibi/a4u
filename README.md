# a4u — Piattaforma per la generazione di corsi universitari

Monorepo per la creazione end-to-end di **corsi universitari assistita
da AI**: dal materiale sorgente fino al PDF stampabile, alle slide e al
discorso temporizzato per il docente. Stack moderno (FastAPI · React 18 ·
PostgreSQL 16) con pipeline AI a 5 fasi parallele e tre pipeline di
export PDF dedicate.

> **Stato**: tutte le 5 fasi della pipeline AI sono implementate e in
> produzione interna, insieme ai tre flussi di export PDF (lezione testo,
> slide, discorso). Vedi [Stato pipeline](#stato-pipeline).

---

## Indice

- [Cosa fa](#cosa-fa)
- [Stato pipeline](#stato-pipeline)
- [Stack tecnologico](#stack-tecnologico)
- [Prerequisiti](#prerequisiti)
- [Avvio rapido (development)](#avvio-rapido-development)
- [Struttura del repo](#struttura-del-repo)
- [Documentazione](#documentazione)
- [Test](#test)
- [Pattern operativi](#pattern-operativi)
- [Note di sicurezza](#note-di-sicurezza)
- [Roadmap](#roadmap)

---

## Cosa fa

```
[ Documenti caricati ]                             [ Output finali ]
   PDF / DOCX / RTF                                 PDF lezione testo
        │                                           PDF slide
        │  Pre-processing                           PDF discorso
        ▼  (estrazione + summary)
   ┌──────────────┐
   │ Fase 1       │  Architettura: moduli + lezioni
   │ Architettura │
   └──────┬───────┘
          ▼
   ┌──────────────┐
   │ Fase 2       │  Per ciascun modulo, in parallelo:
   │ Struttura    │  obiettivi + temi + prerequisiti + scaletta
   └──────┬───────┘
          ▼
   ┌──────────────┐
   │ Fase 3       │  Per ciascuna lezione, in parallelo:
   │ Contenuti    │  testo + diagrammi Mermaid + equazioni LaTeX
   └──────┬───────┘  + tabelle + esempi + bibliografia + glossario
          ▼
   ┌──────────────┐
   │ Fase 4       │  Slide della presentazione (riusano gli asset
   │ Slide        │  di Fase 3, body field opzionale, 16 tipi slide)
   └──────┬───────┘
          ▼
   ┌──────────────┐
   │ Fase 5       │  Discorso temporizzato TTS-friendly per il
   │ Discorso     │  docente (130 wpm IT / 150 wpm EN, ±5%
   └──────────────┘  rispetto a minuti_per_lezione × 60)
```

A ogni livello l'utente può **modificare manualmente** il payload AI con
editor specializzati (TipTap WYSIWYG per il testo, KaTeX live preview
per le formule, preview Mermaid per i diagrammi, table editor visuale,
editor segmenti discorso con auto-durata e validazione TTS-safety
inline). L'AI può rigenerare singole entità o batch interi con `regeneration_hint`
testuale. La piattaforma è **multi-tenant**: ogni utente appartiene a
una o più organizzazioni con permessi granulari, autenticazione con
HttpOnly cookies + refresh-token rotation, audit log immutabile, rate
limit e lockout.

---

## Stato pipeline

| Fase | Spec | Stato | Worker | Cap parallelo |
|---|---|---|---|---|
| Pre-processing documenti | Appendice A | implementata | `course_document_worker` | 1 |
| Fase 1 — Architettura | §4 | implementata | `course_architecture_worker` | 1 |
| Fase 2 — Struttura lezioni | §5 | implementata | `course_lesson_structure_worker` | 5 |
| Fase 3 — Contenuti + Glossario | §6 + §10.1 | implementata | `course_lesson_content_worker` | 3 |
| Fase 4 — Slide | §7 (slides) | implementata | `course_lesson_slides_worker` | 3 |
| Fase 5 — Discorso temporizzato | §8 + §9.5 | implementata | `course_lesson_speech_worker` | 3 |
| §7 — Export PDF lezione testo | §7 | implementata | `course_lesson_pdf_worker` | 2 |
| Export PDF slide | Fase 4 | implementata | `course_lesson_slides_pdf_worker` | 2 |
| Export PDF discorso | Fase 5 | implementata | `course_lesson_speech_pdf_worker` | 2 |

I cap di concorrenza sono env-driven (`COURSE_LESSON_*_MAX_CONCURRENCY`)
e separati per fase, così tier OpenAI alti possono saturare il
parallelismo senza rischio rate-limit incrociati.

---

## Stack tecnologico

| Layer | Tecnologie |
|---|---|
| Backend API | FastAPI · SQLAlchemy 2 async · asyncpg · Pydantic v2 · structlog |
| Migrations | Alembic (24 revisions) |
| AI generator | OpenAI gpt-5.5 (reasoning) · JSON schema strict · auto-retry trasparente · Semaphore per fase + claim atomico anti-double-dispatch |
| PDF export | WeasyPrint (CSS Paged Media completo) · `latex2mathml` · Playwright headless solo per pre-render Mermaid → SVG (no JS in WeasyPrint) |
| Document extract | `pdfplumber` · `python-docx` · `docx2txt` · `striprtf` |
| Frontend | React 18 · Vite · TypeScript · Tailwind 4 · Radix UI (shadcn pattern) · TanStack Query · React Hook Form + Zod · i18next (24 locali) · TipTap · KaTeX · Mermaid |
| Database | PostgreSQL 16 |
| Deploy | Docker (multi-stage) · Nginx |

---

## Prerequisiti

- **Docker Desktop** (per Postgres in dev).
- **Python 3.12** dedicato (su Windows: `py -3.12 --version`; altrimenti
  da python.org). Lo usiamo per il venv del backend, separato dal
  Python di sistema.
- **Node.js 20+** con `npm`.
- **GTK3 runtime** (solo Windows local-dev, per WeasyPrint):

  ```powershell
  winget install tschoonj.GTKForWindows
  ```

  Su Linux/Docker il runtime è gestito dal Dockerfile (Pango, Cairo,
  HarfBuzz, Pixbuf, fonts DejaVu/Liberation).
- **Playwright Chromium** (per il pre-render dei diagrammi Mermaid in
  Fase 3 e nell'export PDF testo/slide):

  ```bash
  cd backend && .venv/Scripts/python.exe -m playwright install chromium
  ```

  Il PDF discorso non usa Playwright (è prosa pura).

---

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

### Configurazione minima OpenAI

Nel file `.env` imposta:

```
OPENAI_API_KEY=sk-...
```

I modelli e i parametri di reasoning hanno default sensati per ciascuna
fase (`OPENAI_LESSON_*_MODEL=gpt-5.5`,
`OPENAI_LESSON_*_REASONING_EFFORT=medium|high`). Vedi
[`docs/04-configuration.md`](docs/04-configuration.md#openai) per i
trade-off di concorrenza, retry e budget token.

---

## Struttura del repo

```
a4u/
├── backend/
│   ├── app/
│   │   ├── api/v1/         # FastAPI routers (~50 endpoint corsi)
│   │   ├── core/           # config, logging, errors, audit, security
│   │   ├── db/             # AsyncEngine, base, session
│   │   ├── models/         # SQLAlchemy models
│   │   ├── schemas/        # Pydantic request/response
│   │   ├── services/       # business logic (~25 file domain corsi)
│   │   ├── templates/      # Jinja2: lesson_pdf, lesson_slides_pdf, lesson_speech_pdf
│   │   ├── middleware/     # CORS, request id, error handler
│   │   ├── i18n/           # backend strings
│   │   └── main.py         # FastAPI app + 10 worker lifespan
│   ├── alembic/versions/   # 24 migrations (0001-0024)
│   ├── tests/              # smoke tests
│   ├── Dockerfile          # multi-stage con Pango/Cairo per WeasyPrint
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/            # axios clients (TanStack Query)
│   │   ├── auth/           # login, ProtectedRoute, providers
│   │   ├── components/     # shared (forms, dialogs, KaTeX, Mermaid, ApprovalBadge, StalenessAlert)
│   │   ├── hooks/          # useBatchEta, useTaskEta, ...
│   │   ├── i18n/           # i18next + IT/EN canonici (altre 22 lingue auto-tradotte in-app)
│   │   ├── lib/            # staleness.ts (7 helper cascata) + slides.ts
│   │   ├── pages/          # admin, org, courses (CourseEditorPage con 8 tab), templates
│   │   ├── providers/      # query client, auth, theme
│   │   └── routes/         # router + role gates
│   ├── public/
│   ├── Dockerfile          # build + nginx static serve
│   └── package.json
├── docs/                   # documentazione platform + dominio corsi
│   ├── courses/            # 11 file dedicati al dominio corsi
│   ├── backend/            # deep-dive backend
│   ├── frontend/           # deep-dive frontend
│   ├── database/           # schema completo
│   └── *.md                # overview, architecture, configuration, security, ...
├── docker-compose.yml      # dev: solo Postgres
├── docker-compose.prod.yml # prod: backend + frontend + Postgres
└── .github/workflows/      # CI backend + frontend
```

---

## Documentazione

Tutta la documentazione vive in [`docs/`](docs/) ed è organizzata per area.

### Top-level

| File | Contenuto |
|---|---|
| [`README.md`](docs/README.md) | Indice generale della documentazione |
| [`01-overview.md`](docs/01-overview.md) | Visione d'insieme della piattaforma + stato pipeline |
| [`02-architecture.md`](docs/02-architecture.md) | Architettura backend/frontend, pattern worker async |
| [`03-getting-started.md`](docs/03-getting-started.md) | Setup ambiente, primo avvio |
| [`04-configuration.md`](docs/04-configuration.md) | Tutte le env (OpenAI, worker concurrency, retry, ecc.) |
| [`05-security.md`](docs/05-security.md) | Auth, cookie, rate limit, lockout, CSRF, audit |
| [`06-permissions.md`](docs/06-permissions.md) | Ruoli, permessi, override per-membership |
| [`07-deployment.md`](docs/07-deployment.md) | Docker compose prod, healthcheck, backup |
| [`api-reference.md`](docs/api-reference.md) | Tutti gli endpoint platform |

### Dominio corsi

| File | Argomento |
|---|---|
| [`courses/README.md`](docs/courses/README.md) | Indice del dominio + state machine + mappa file BE/FE |
| [`courses/01-data-model.md`](docs/courses/01-data-model.md) | Tabelle, colonne, vincoli, schemi `*_raw` |
| [`courses/02-document-preprocessing.md`](docs/courses/02-document-preprocessing.md) | Pipeline estrazione + summary AI |
| [`courses/03-architecture-generation.md`](docs/courses/03-architecture-generation.md) | Fase 1 — architettura del corso |
| [`courses/04-manual-editing.md`](docs/courses/04-manual-editing.md) | CRUD manuale moduli/lezioni + AI per modulo |
| [`courses/05-api-reference.md`](docs/courses/05-api-reference.md) | ~50 endpoint sotto `/orgs/{org_id}/courses` |
| [`courses/06-frontend.md`](docs/courses/06-frontend.md) | UI con 8 tab, dialog, polling, ETA |
| [`courses/07-lesson-structure.md`](docs/courses/07-lesson-structure.md) | Fase 2 — struttura lezioni |
| [`courses/08-lesson-content.md`](docs/courses/08-lesson-content.md) | Fase 3 — contenuti + glossario |
| [`courses/09-pdf-export.md`](docs/courses/09-pdf-export.md) | Tre pipeline PDF (testo/slide/discorso) |
| [`courses/10-lesson-slides.md`](docs/courses/10-lesson-slides.md) | Fase 4 — slide della lezione |
| [`courses/11-lesson-speech.md`](docs/courses/11-lesson-speech.md) | Fase 5 — discorso temporizzato + TTS-safety |

### Backend & Frontend deep-dive

- [`docs/backend/`](docs/backend/) — entry, core, middleware, DB, models, schemas, services, API, alembic, tests
- [`docs/frontend/`](docs/frontend/) — entry, API client, auth, routing, components, pages, lib, hooks, i18n
- [`docs/database/schema.md`](docs/database/schema.md) — schema PostgreSQL completo con vincoli e indici

---

## Test

```bash
# Backend
cd backend && source .venv/Scripts/activate && pytest

# Frontend
cd frontend && npm run lint && npm run type-check
```

---

## Pattern operativi

### Auto-retry trasparente sui worker AI

Tutti i worker di generazione (architecture, structure, content, slides,
speech, e i tre PDF) implementano auto-retry: in caso di errore
recuperabile (timeout AI, parsing failure, validazione, Playwright crash,
rate-limit OpenAI) **ripristinano `pending` invece di transire a
`failed`**, finché `attempts < auto_retry_max` (default 5). La UI vede
solo "in elaborazione" durante i retry, mai i messaggi di errore
intermedi. Solo dopo `auto_retry_max` esaurito → `failed` (terminale).

Errori non recuperabili (`OPENAI_API_KEY` mancante, pre-condizione di
fase non soddisfatta) sono terminal subito, senza retry.

### Atomic claim anti-double-dispatch

Ciascun worker batch fa il claim del task in `_inflight: set[UUID]`
**prima** di acquisire il semaforo: questo evita che un task in coda
venga ri-dispatched dal tick successivo se la coda è satura. Il `_tick`
ritorna immediatamente dopo il fire-and-forget; il task in attesa dietro
al semaforo non è "perduto" ma nemmeno duplicato.

### Stale-detection cascata

Quando l'utente modifica manualmente un payload a monte (architettura,
struttura, contenuto, slide), il timestamp `*_modified_at` viene
settato. Il frontend confronta in cascata con i `*_generated_at` a valle
e mostra `<StalenessAlert>` quando qualcosa è disallineato — **non
blocca**, è un suggerimento. Sette helper in
[`frontend/src/lib/staleness.ts`](frontend/src/lib/staleness.ts):

```
isStructureStale → isContentStale → isPdfStale
                                   → isSlidesStale → isSlidesPdfStale
                                                   → isSpeechStale → isSpeechPdfStale
```

I worker AI **non** toccano i `*_modified_at` (solo i CRUD manuali lo
fanno), così la rigenerazione AI non si auto-segnala come stale.

### Reset PDF su rigenerazione AI

Quando l'utente rigenera content/slide/discorso, lo status PDF a valle
(`pdf_status` / `slides_pdf_status` / `speech_pdf_status`) viene
resettato a `empty` per impedire il download di un PDF stale. L'utente
deve esplicitamente cliccare "Esporta PDF" per produrre la versione
allineata.

---

## Note di sicurezza

- **Cookie HttpOnly + SameSite=Lax + Secure** (in `production`).
- **Refresh-token con rotation e reuse-detection** (chain-revoke).
- **Rate limit** su login (`slowapi`) + lockout dopo 10 tentativi falliti.
- **Audit log immutabile** per azioni sensibili (auth, mutating, AI
  generation, PDF export).
- **Upload immagini** ri-encoded da Pillow (strip EXIF, anti
  path-traversal, dimensioni cap).
- **Security headers** (`Content-Security-Policy`,
  `Strict-Transport-Security`, `X-Frame-Options`, ...) + middleware CSRF
  check su mutating endpoints.
- **Path-traversal protection** sugli asset upload
  (`Path.relative_to(upload_root)`).
- **TTS-safety validation** server-side per Fase 5: il testo dei
  segmenti del discorso non può contenere markdown (`*`, `_`, `` ` ``,
  `#`, `\`, `$`), abbreviazioni note (`es.`, `etc.`, ...) o comandi
  LaTeX (`\frac`, `\sum`, ...). Hard fail in `materialize_lesson_speech`.

Vedi [`docs/05-security.md`](docs/05-security.md) per il dettaglio
completo.

---

## Roadmap

- [ ] **Pipeline TTS+video**: lo schema `speech_segments` di Fase 5 è
      già pronto per servire un futuro pipeline TTS (es. ElevenLabs,
      Azure Speech) + montaggio video con sincronizzazione slide. Vedi
      [`docs/courses/11-lesson-speech.md`](docs/courses/11-lesson-speech.md#forward-compat-pipeline-ttsvideo).
- [ ] **Generazione PPTX** dai template slide (oggi solo PDF slide via
      WeasyPrint; il rendering native PowerPoint richiede `python-pptx`
      che non è ancora integrato).
- [ ] **PDF aggregato di intero corso** (zip o single-PDF concatenato;
      oggi solo per-lezione).
- [ ] **Object storage S3/MinIO** per asset (oggi filesystem locale,
      single-host).
- [ ] **Streaming SSE** del progresso al client (oggi polling
      `refetchInterval`).
- [ ] **Pre-render Mermaid offline** (oggi via CDN jsdelivr; soluzione
      future: bundle locale `mermaid.esm` o pre-rendering via mermaid-cli
      + node).
- [ ] **Invio email reali** per inviti / reset password (oggi l'API
      ritorna il token in chiaro, predisposto per integrazione SMTP).
- [ ] **Spell-check e linting LaTeX** (oggi solo errore visivo nel
      preview).
