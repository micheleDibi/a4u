# Documentazione a4u

Documentazione completa della piattaforma multi-tenant per la creazione di corsi
universitari `a4u` (`C:\Users\michele\Developer\a4u`).

Ogni file di codice del repository è documentato sotto questa cartella, suddiviso per
backend, frontend, database e API.

## Indice

### Sezioni introduttive

- [01 — Overview](01-overview.md): cosa è a4u, scope, fuori scope.
- [02 — Architecture](02-architecture.md): layers, flusso richiesta, responsabilità.
- [03 — Getting started](03-getting-started.md): istruzioni di setup passo passo.
- [04 — Configuration](04-configuration.md): variabili d'ambiente backend e frontend.
- [05 — Security](05-security.md): modello di minaccia e mitigazioni.
- [06 — Permissions model](06-permissions.md): RBAC con override a 2 livelli.
- [07 — Deployment](07-deployment.md): Docker compose, CI/CD, release.

### Backend (`backend/app`)

- [Backend overview](backend/README.md)
- [01 — Entry: app/__init__.py + main.py](backend/01-entry.md)
- [02 — core/](backend/02-core.md) (config, logging, security, deps, errors, audit, rate_limit, permissions)
- [03 — middleware/](backend/03-middleware.md) (request_id, access_log, security_headers, csrf)
- [04 — db/](backend/04-db.md) (base, session, seed)
- [05 — models/](backend/05-models.md) (13 modelli SQLAlchemy 2)
- [06 — schemas/](backend/06-schemas.md) (Pydantic v2 DTO)
- [07 — services/](backend/07-services.md) (logica di dominio)
- [08 — api/v1/](backend/08-api.md) (router REST)
- [09 — utils/](backend/09-utils.md)
- [10 — alembic/](backend/10-alembic.md)
- [11 — tests/](backend/11-tests.md)

### Frontend (`frontend/src`)

Stack: **React 18 + Vite + Tailwind v4 + shadcn/ui + i18next** (24 lingue UE).

- [Frontend overview](frontend/README.md)
- [01 — Entry: `main.tsx`, `App.tsx`, `index.css`, ThemeProvider, ThemeToggle, LanguageSwitcher](frontend/01-entry.md)
- [02 — api/](frontend/02-api-client.md) (axios + 9 moduli endpoint)
- [03 — auth/](frontend/03-auth.md) (AuthContext, ProtectedRoute, PermissionGate)
- [04 — routing](frontend/04-routing.md) (router, RootRedirect)
- [05 — components/](frontend/05-components.md) (ui shadcn, layout, forms, feedback, shared, templates)
- [06 — pages/](frontend/06-pages.md) (auth, admin, org)
- [07 — lib/](frontend/07-lib.md) (permissions, errors, format, logger, utils/cn)
- [08 — hooks](frontend/08-hooks.md)
- [09 — i18n (24 lingue UE)](frontend/09-i18n.md)

### Courses (dominio principale)

Documentazione dedicata della feature **Corsi** — pipeline AI a 5 fasi (oggi
implementate Pre-processing + Fase 1) + CRUD manuale di moduli/lezioni:

- [Courses overview](courses/README.md)
- [01 — Data model](courses/01-data-model.md): course, course_document, course_module, course_lesson, course_taxonomy_term, language.
- [02 — Document pre-processing](courses/02-document-preprocessing.md): worker estrazione testo + summarize OpenAI (Appendice A).
- [03 — Architecture generation (Fase 1)](courses/03-architecture-generation.md): worker AI con progress tracking + materializzazione moduli/lezioni.
- [04 — Manual editing & AI lesson generation](courses/04-manual-editing.md): CRUD inline + auto-trigger AI sui moduli aggiunti manualmente.
- [05 — API reference (corsi)](courses/05-api-reference.md): 21 endpoint sotto `/orgs/{org_id}/courses`.
- [06 — Frontend](courses/06-frontend.md): pages, dialog, optimistic update, KaTeX, i18n.

### Database & API

- [Database schema](database/schema.md): tabelle, colonne, vincoli, indici.
- [API reference](api-reference.md): tutti gli endpoint con request/response (vedi anche [Courses API](courses/05-api-reference.md) dedicata).

## Convenzioni

Ogni voce di documentazione segue lo schema:

- **File**: percorso assoluto rispetto alla root del repo.
- **Scopo**: cosa fa il modulo.
- **Esporta**: cosa è importabile dall'esterno.
- Per ciascuna funzione/classe/componente:
  - **Firma** (TypeScript / Python).
  - **Parametri**.
  - **Restituisce**.
  - **Comportamento e note**.

Le note operative (errori, edge case, sicurezza) sono evidenziate.
