# 01 — Overview

`a4u` è una piattaforma multi-tenant per la creazione di corsi universitari
con una pipeline a 6 fasi: 5 fasi AI (architettura → struttura lezioni →
contenuti → slide → discorso) + una fase di produzione del video MP4 della
lezione (TTS + slide + encoding). Le iterazioni iniziali hanno implementato
le foundation (identità, organizzazioni, ruoli, template, avatar); le
iterazioni successive hanno costruito sopra di esse il **dominio Corsi**,
oggi nucleo del prodotto.

Stato corrente delle fasi della pipeline (vedi
[Courses overview](courses/README.md) per il dettaglio):

| Fase | Stato |
|---|---|
| Pre-processing documenti (Appendice A) | ✅ Implementata |
| Fase 1 — Architettura del corso (§4) | ✅ Implementata |
| Fase 2 — Struttura lezioni (§5) | ✅ Implementata |
| Fase 3 — Contenuti (§6) + Glossario (§10.1) | ✅ Implementata |
| §7 — Export PDF lezione testo | ✅ Implementata |
| Fase 4 — Slide della lezione | ✅ Implementata |
| Fase 4 — Export PDF slide | ✅ Implementata |
| Fase 5 — Discorso temporizzato (§8) | ✅ Implementata |
| Fase 5 — Export PDF discorso | ✅ Implementata |
| Fase 6 — Video MP4 della lezione (§9) | ✅ Implementata |
| Fase 6b — "Video con Avatar" (§9b) | ✅ Implementata |

> La pipeline è oggi a **6 fasi** (architettura → struttura → contenuti →
> slide → discorso → video). La Fase 6 è la prima fase non-AI: nessuna
> chiamata OpenAI, orchestra TTS su GPU + rendering + encoding. La
> Fase 6b ("Video con Avatar") è una fase aggiuntiva opzionale che
> sovrappone un avatar parlante con lip-sync al video della Fase 6.

A complemento è disponibile un **CRUD manuale** dei moduli e delle lezioni
con generazione AI per singolo modulo quando l'utente lo aggiunge manualmente.
Quando la verifica di apprendimento finale è attiva, l'**ultima lezione di
ogni modulo** è una **lezione di verifica delle competenze** (assessment)
anziché una lezione didattica.

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
   Hailuo-2.3.
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
- **Pipeline AI completa (Pre-processing + Fasi 1-5 + Glossario + 3 PDF)**:
  dodici worker async (lifespan-managed), di cui dieci della pipeline AI/PDF
  e due dedicati al video. Le fasi batch (struttura, content,
  slide, discorso, e i tre PDF) dispatchano i task **in parallelo** con
  `asyncio.Semaphore` (cap configurabile per ciascuna fase) e sessioni DB
  per task; il claim atomico in `_tick` dedupa anche con coda satura. Tutti
  i worker hanno **auto-retry trasparente** prima del fail terminale. Servizi
  OpenAI per ciascuna fase (`openai_summarize_service`, `openai_architecture_service`,
  `openai_module_lessons_service`, `openai_lesson_structure_service`,
  `openai_lesson_content_service`, `openai_lesson_slides_service`,
  `openai_lesson_speech_service`, `openai_glossary_service`, e il
  `openai_client` condiviso con helper `apply_reasoning_effort` per
  gpt-5.x/o1/o3/o4) + validazione stretta via Pydantic + JSON Schema strict
  di OpenAI.
- **Fase 6 — Video MP4 della lezione**: dalle slide approvate (Fase 4) e
  dal discorso approvato (Fase 5) il worker `course_lesson_video_worker`
  produce un MP4 H.264+AAC. È la prima fase **non-AI**: la sintesi vocale
  XTTS-v2 (voce clonata dell'avatar dell'assegnatario) gira su un endpoint
  **RunPod Serverless GPU** (handler nella cartella `XTTS/` del repo), le
  slide diventano PNG via Playwright, l'encoding è fatto con ffmpeg.
  L'audio TTS è messo in cache su disco: rigenerare un video senza
  cambiare testo/voce/lingua salta del tutto la GPU. Vedi
  [Courses 12 — Lesson video](courses/12-lesson-video.md).
- **Fase 6b — "Video con Avatar"**: il worker
  `course_lesson_avatar_video_worker` prende il video MP4 della Fase 6 e
  vi sovrappone in basso a destra un **avatar parlante con lip-sync**,
  sincronizzato sull'audio della lezione. Il lip-sync è prodotto da
  **MuseTalk** su un secondo endpoint RunPod GPU, con Cloudflare R2 come
  storage di transito; il client MuseTalk è vendored in
  `backend/app/musetalk_client/` ed eseguito come subprocess. Vedi
  [Courses 13 — Avatar video](courses/13-avatar-video.md).
- **Lezione di verifica delle competenze** (assessment): se la verifica di
  apprendimento finale è attiva sull'organizzazione e i moduli hanno ≥ 2
  lezioni, l'ultima lezione di ogni modulo è una verifica — domande a
  scelta multipla + domande aperte con chiave di correzione — invece di
  una lezione didattica. Si genera nella Fase 3 (in parallelo alle lezioni
  didattiche) ed è esclusa da slide/discorso/video/PDF. Vedi
  [Courses 14 — Assessment lesson](courses/14-assessment-lesson.md).
- **Tre pipeline PDF** indipendenti (`pdf_*` testo, `slides_pdf_*` slide,
  `speech_pdf_*` discorso) con stack comune (WeasyPrint + Jinja2 + Playwright
  per pre-render Mermaid solo dove serve) ma layout dedicati: A4 portrait
  single-column per testo e discorso, slide split bullet/asset per il PDF
  slide, per-slide grouping con timeline cumulativa per il PDF discorso.
- **CRUD manuale completo** per tutti i payload AI (moduli, lezioni
  architettura, struttura Fase 2, contenuto Fase 3, slide Fase 4, discorso
  Fase 5) con rinumerazione automatica dei codici (M1, M1.L1, ecc.) e fix
  anti-collisione su reorder. Endpoint PATCH dedicati per ciascuna fase + 4
  endpoint export PDF per ciascuna delle tre pipeline.
- **Stale-detection cascata** (`*_modified_at` settati solo dai CRUD,
  `isStructureStale → isContentStale → isPdfStale → isSlidesStale →
  isSlidesPdfStale → isSpeechStale → isSpeechPdfStale`): la UI segnala
  con `<StalenessAlert>` che qualcosa a monte è cambiato dopo l'ultima
  generazione AI a valle, ma non blocca — è un suggerimento. Vedi
  [Courses README — Stale-detection](courses/README.md#stale-detection-cascata).
- **TTS-safety validation** lato BE+FE per Fase 5 (regola §8.5 punto 5):
  testo segmento privo di caratteri proibiti (`*`, `_`, `` ` ``, `#`, `\`, `$`),
  abbreviazioni note (`es.`, `etc.`, ...), comandi LaTeX (`\frac`, `\sum`, ...).
  Hard fail server-side; warning chip inline nell'editor frontend per UX
  immediata. Words-per-minute env-driven (130 IT / 150 EN) per la coerenza
  durata × word_count.
- **UI editor a tab** (8 voci in modalità edit): Informazioni di base,
  Inquadramento didattico, Documenti, Architettura, Struttura lezioni,
  Contenuti lezioni, **Slide**, **Discorso** — con auto-save debounced,
  polling per stato pipeline (esteso a tutti i 10 worker), optimistic update
  sui reorder, **ETA + tempo medio per task** durante i batch
  (`useBatchEta` / `useTaskEta`), Mermaid live (con pre-validazione syntax
  e error UI controllata), KaTeX, editor TipTap user-friendly per il
  contenuto lezione, editor segmenti con TTS-safety inline + auto-durata
  da word count, progress bar per ogni operazione AI/PDF.

## Cosa NON è ancora incluso (e perché)

- **Generazione PPTX effettiva** dai template slide (oggi solo preview
  HTML + export PDF slide via WeasyPrint — il rendering native PowerPoint
  richiede python-pptx che non è ancora integrato).
- **Invio email reali** per inviti/reset password (l'API ritorna il token in chiaro,
  predisposto per integrazione SMTP).
- **Multi-lingua attiva**: i18n predisposto. **IT/EN canoniche**, le altre 22
  lingue UE sono completate in-app dall'utente via "Completa con AI" sulle
  chiavi vuote.

## Stack tecnologico

| Livello | Tecnologia |
|---|---|
| Frontend | React 18, Vite, TypeScript, Tailwind v4 + shadcn/ui + Radix primitives, TanStack Query, axios, sonner, **katex** + **mermaid** (rendering live lezioni), **TipTap** (editor user-friendly markdown), i18next (24 lingue UE) |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, Pydantic v2, structlog, slowapi, Pillow, PyJWT, passlib(bcrypt), httpx (OpenAI), **pdfplumber + python-docx + docx2txt + striprtf** (estrazione documenti), **WeasyPrint** (HTML → PDF Paged Media), **Playwright** (Chromium pre-render Mermaid → SVG + render slide → PNG per il video), **latex2mathml** (LaTeX → MathML), **markdown-it-py + Jinja2** (rendering lezione PDF), **ffmpeg** (encoding video MP4 + overlay avatar), **boto3 + requests** (client MuseTalk vendored: R2 + RunPod) |
| GPU esterna | **RunPod Serverless GPU** — endpoint TTS XTTS-v2 (immagine in `XTTS/`) + endpoint MuseTalk lip-sync |
| Database | PostgreSQL 16 (Docker compose) |
| File storage | Filesystem locale (`backend/uploads/` — include video MP4 generati, cache audio TTS, manifest MuseTalk — + `backend/generated_pdfs/`) servito tramite `StaticFiles` con HTTP Range; **Cloudflare R2** (S3-compatible) come storage di transito per il job MuseTalk |
| Auth | JWT in cookie HttpOnly + refresh token con rotation |
| CI | GitHub Actions (lint, type-check, test, build) |

## Convenzioni di codifica

- **Backend**: type hints ovunque (`Mapped[...]` per modelli, `Annotated[...]` per
  dipendenze), `ruff` per lint+format, `mypy` per type-check.
- **Frontend**: TypeScript `strict`, ESLint con regole React Hooks/Refresh.
  Stile via Tailwind v4 + shadcn/ui (no Material-UI).
- **Logging**: tutto strutturato. In dev formato console colorato, in produzione JSON.
- **Errori**: il backend non ritorna mai stack trace al client; gli errori sono
  serializzati come `{ code, message, request_id, meta? }`.
- **Audit**: ogni azione sensibile (login, create/update/delete, role change,
  permission override) lascia una riga in `audit_logs` con `request_id`.
