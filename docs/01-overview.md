# 01 — Overview

`a4u` è una piattaforma multi-tenant per la creazione di corsi universitari
con pipeline AI a 5 fasi (architettura → struttura lezioni → contenuti →
slide → discorso). Le iterazioni iniziali hanno implementato le foundation
(identità, organizzazioni, ruoli, template, avatar); le iterazioni successive
hanno costruito sopra di esse il **dominio Corsi**, oggi nucleo del prodotto.

Stato corrente delle 5 fasi della pipeline (vedi
[Courses overview](courses/README.md) per il dettaglio):

| Fase | Stato |
|---|---|
| Pre-processing documenti (Appendice A) | ✅ Implementata |
| Fase 1 — Architettura del corso (§4) | ✅ Implementata |
| Fase 2 — Struttura lezioni (§5) | ✅ Implementata |
| Fase 3 — Contenuti (§6) | ⏳ |
| Fase 4 — Slide (§7) | ⏳ |
| Fase 5 — Discorso (§8) | ⏳ |

A complemento è disponibile un **CRUD manuale** dei moduli e delle lezioni
con generazione AI per singolo modulo quando l'utente lo aggiunge manualmente.

## Attori e ruoli

A livello piattaforma esistono due ruoli globali:

| Ruolo | Identificato da | Capacità |
|---|---|---|
| **Admin di piattaforma** | `users.is_platform_admin = true` | Bypass di tutti i controlli, gestisce tutte le organizzazioni e tutti gli utenti, modifica i permessi default globali. |
| **Utente** | `users.is_platform_admin = false` | Accede solo alle organizzazioni di cui è membro, con i permessi del proprio ruolo (eventualmente override). |

Dentro ogni organizzazione esistono quattro **ruoli interni**, definiti in
`app/core/permissions.py` (classe `R`) e seedati in `organization_roles`:

| Code | Nome (it) | Rank | Tipico utilizzo |
|---|---|---|---|
| `creator` | Creatore | 10 | Massimo livello, gestisce permessi e può trasferire la titolarità. |
| `org_admin` | Amministratore organizzazione | 20 | Amministra tutto tranne i permessi e il transfer. |
| `manager` | Manager | 30 | Gestisce contenuti (oggi visualizza membri; domani gestirà corsi). |
| `member` | Membro | 40 | Accesso minimo (oggi nessun permesso di default). |

Il rank è usato per i vincoli "non puoi promuovere a ruolo superiore al tuo".
Il `creator` è unico per organizzazione e si trasferisce con `transfer-creator`.

## Cosa include questa iterazione

1. Autenticazione cookie HttpOnly + refresh token con rotation/reuse-detection.
2. Rate limit + lockout dopo `N` tentativi falliti.
3. CRUD organizzazioni con upload del logo (validato e ri-encodato con Pillow).
4. Iscrizione diretta utenti (admin di piattaforma) e invito via token (`creator`/`org_admin`).
5. Gestione membership: assegnazione ruoli, rimozione, transfer del creator.
6. Permessi modificabili a 3 livelli:
   - default globali (modificabili dall'admin di piattaforma);
   - override per organizzazione (modificabili dal `creator` dell'org);
   - override per singolo membership (per persona).
7. Template slide e PDF con upload di immagini (sfondo + 2 loghi) e parametri di
   formattazione (font, colori, dimensioni).
8. **Preview live** dei template nel frontend, aggiornata mentre si compila il form.
9. Avatar personale per utente (1:1, cross-org): immagine quadrata
   1024×1024 prodotta con crop 1:1 obbligatorio + audio (upload o
   registrazione browser) registrato leggendo uno **script
   standardizzato per lingua** servito dall'admin (utile al futuro
   voice cloning) + 5 clip video generate in background tramite MiniMax
   Hailuo-02.
10. Configurazione admin dei prompt clip e degli script di lettura
    audio per ciascuna lingua.
11. Opacità della filigrana di sfondo configurabile per ogni template
    PDF (era hardcoded a 15%).
12. Audit log immutabile per ogni azione sensibile.

## Cosa è stato aggiunto sopra la foundation (dominio Corsi)

- **Modello dati Corsi**: `course`, `course_document`, `course_module`,
  `course_lesson`, `course_taxonomy_term`, `language` — vedi
  [Courses 01 — Data model](courses/01-data-model.md).
- **Permessi `course:*`**: `view`, `create`, `edit`, `delete`, `assign`,
  `generate`, più `course_config:manage`.
- **Pipeline AI Pre-processing + Fase 1 + Fase 2**: tre worker async
  (lifespan-managed). Fase 2 dispatcha i moduli **in parallelo** con
  `asyncio.Semaphore` (cap configurabile) e sessioni DB per task. Cinque servizi
  OpenAI (`openai_summarize_service`, `openai_architecture_service`,
  `openai_module_lessons_service`, `openai_lesson_structure_service`,
  e il `openai_client` condiviso) + validazione stretta via Pydantic + JSON
  Schema strict di OpenAI.
- **CRUD manuale moduli/lezioni**: 8 endpoint con rinumerazione automatica
  dei codici (M1, M1.L1, ecc.) e fix anti-collisione su reorder. Più 1 endpoint
  PATCH per la struttura della singola lezione (Fase 2).
- **UI editor a tab**: Informazioni di base, Inquadramento didattico, Documenti,
  Architettura, **Struttura lezioni** — con auto-save debounced, polling per
  stato pipeline (esteso al worker Fase 2), optimistic update sui reorder,
  KaTeX per le formule nel summary documenti, progress bar per le operazioni AI
  (incluso aggregate per il batch parallelo di Fase 2).

## Cosa NON è ancora incluso (e perché)

- **Fasi 3-5 della pipeline AI**: in roadmap. Pre-processing + Fase 1 + Fase 2
  sono prerequisito per le successive.
- **Generazione PPTX/PDF effettiva** dai template (oggi solo preview HTML).
- **Editor di contenuto del corso** (slide builder, blocchi).
- **Invio email reali** per inviti/reset password (l'API ritorna il token in chiaro,
  predisposto per integrazione SMTP).
- **Multi-lingua attiva**: i18n predisposto. **IT/EN canoniche**, le altre 22
  lingue UE sono completate in-app dall'utente via "Completa con AI" sulle
  chiavi vuote.

## Stack tecnologico

| Livello | Tecnologia |
|---|---|
| Frontend | React 18, Vite, TypeScript, Tailwind v4 + shadcn/ui, TanStack Query, axios, sonner, **katex** (formule), **@radix-ui/react-progress** |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, Pydantic v2, structlog, slowapi, Pillow, PyJWT, passlib(bcrypt), httpx (OpenAI), **pdfplumber + python-docx + docx2txt + striprtf** (estrazione documenti) |
| Database | PostgreSQL 16 (Docker compose) |
| File storage | Filesystem locale (`backend/uploads/`) servito tramite `StaticFiles` |
| Auth | JWT in cookie HttpOnly + refresh token con rotation |
| CI | GitHub Actions (lint, type-check, test, build) |

## Convenzioni di codifica

- **Backend**: type hints ovunque (`Mapped[...]` per modelli, `Annotated[...]` per
  dipendenze), `ruff` per lint+format, `mypy` per type-check.
- **Frontend**: TypeScript `strict`, ESLint con regole React Hooks/Refresh, file MUI
  con tema italiano centralizzato.
- **Logging**: tutto strutturato. In dev formato console colorato, in produzione JSON.
- **Errori**: il backend non ritorna mai stack trace al client; gli errori sono
  serializzati come `{ code, message, request_id, meta? }`.
- **Audit**: ogni azione sensibile (login, create/update/delete, role change,
  permission override) lascia una riga in `audit_logs` con `request_id`.
