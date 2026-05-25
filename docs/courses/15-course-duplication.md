# 15 — Duplicazione corso in altra lingua

Feature: dall'elenco corsi, menu `⋮` di una riga → "Duplica in altra
lingua". L'utente sceglie la lingua target dal dropdown delle lingue
attive del sistema (esclusa la lingua corrente del corso). Un job
background traduce via OpenAI tutti i contenuti e crea un corso target
identico per struttura/contenuti/configurazione, nella nuova lingua.

**Non vengono copiati**: Video MP4 (Fase 6) e Video con Avatar (Fase 6b).
Il corso target parte con `video_status = avatar_video_status = "empty"`;
l'utente li rigenererà dalle rispettive schede del corso duplicato.

**PDF non vengono copiati** (file in lingua sorgente, non più validi):
`pdf_status`, `slides_pdf_status`, `speech_pdf_status` resettati a
`empty`, path `None`. L'utente li rigenererà dalle 3 schede.

**Tutto il resto viene tradotto via OpenAI**: titolo, obiettivi, key
topics, architettura, lessons structure, content (incluse domande di
verifica), slide, discorso, glossario, summary dei documenti caricati.

**Documenti**: il file originale viene copiato fisicamente su
filesystem (`shutil.copy2`) con nuovo `filename_stored` UUID. Il
`summary` JSONB viene tradotto.

## Permesso

Nuovo permesso `COURSE_DUPLICATE = "course:duplicate"`. Default per
ruolo:

| Ruolo | Default |
|---|---|
| `creator` | ✅ |
| `org_admin` | ✅ |
| `manager` | ✅ |
| `member` | ❌ |

Definito in `backend/app/core/permissions.py` + mirror in
`frontend/src/lib/permissions.ts`. Descrizione localizzata in
`PERMISSION_DESCRIPTIONS` (`seed.py`) + chiave i18n
`permissions.course:duplicate`.

Il seed `_seed_permissions` è idempotente: al primo boot dopo il
deploy crea automaticamente la riga `Permission(code="course:duplicate")`
e la linka ai 3 ruoli sopra.

## Tabella `course_duplication_job`

Migration: `backend/alembic/versions/0031_course_duplication.py`.

Schema:

```sql
CREATE TABLE course_duplication_job (
  id                     UUID PRIMARY KEY,
  source_course_id       UUID NOT NULL REFERENCES course(id) ON DELETE CASCADE,
  target_course_id       UUID REFERENCES course(id) ON DELETE SET NULL,
  target_language_code   VARCHAR(10) NOT NULL REFERENCES languages(code) ON DELETE RESTRICT,
  status                 VARCHAR(40) NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','processing','ready','failed')),
  progress               SMALLINT NOT NULL DEFAULT 0
                         CHECK (progress >= 0 AND progress <= 100),
  progress_phase         VARCHAR(50),
  error                  TEXT,
  attempts               SMALLINT NOT NULL DEFAULT 0,
  tokens                 JSONB,
  requested_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
  started_at             TIMESTAMPTZ,
  finished_at            TIMESTAMPTZ,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_course_duplication_job_source ON course_duplication_job(source_course_id);
CREATE INDEX ix_course_duplication_job_target ON course_duplication_job(target_course_id);
CREATE INDEX ix_course_duplication_job_status ON course_duplication_job(status);

-- Unique parziale che impedisce job concorrenti per la stessa coppia
-- (source, target_lang) quando uno è ancora attivo.
CREATE UNIQUE INDEX uq_course_duplication_active
  ON course_duplication_job (source_course_id, target_language_code)
  WHERE status IN ('pending','processing');
```

Il `target_course_id` viene popolato dopo la phase `cloning_structure`
del worker. Da quel momento la lista corsi mostra il nuovo corso con
il badge di duplicazione attiva.

## Worker `course_duplication_worker`

Pattern speculare a `course_lesson_content_worker.py`: async loop +
polling DB + semaforo + auto-retry trasparente.

- **Concorrenza globale**: cap 1
  (`course_duplication_max_concurrent_jobs=1`). Ogni job può consumare
  molti token OpenAI; un job alla volta evita rate-limit e tiene il
  consumo prevedibile.
- **Concorrenza interna**: dentro al job le lezioni vengono tradotte
  in parallelo cap 3 per fase
  (`course_duplication_lesson_translate_concurrency=3`). Una `asyncio.gather`
  per fase su semaforo locale: tutto il `content_raw` in parallelo,
  poi tutto `slides_raw`, poi tutto `speech_raw`.
- **Poll interval**: 4 secondi.
- **Auto-retry**: cap 5
  (`course_duplication_auto_retry_max=5`). Su `OpenAIError` transient
  il job torna a `pending` con `attempts+1`. Cap esaurito → `failed`
  con `error` valorizzato. `OpenAINotConfiguredError` è non-recuperabile
  (terminale subito).
- **Cancellation**: il service `cancel_duplication` mette il job in
  `failed` (idempotente: no-op se già `ready`/`failed`). Il worker,
  prima di scrivere `ready` nella phase finale, verifica `job.status`
  per evitare race window.

### Pipeline `_process_one(job_id)` — 8 fasi

| Phase | progress_phase | progress % |
|---|---|---|
| 1 | `loading_source` | 2% |
| 2 | `cloning_structure` | 5% → 8% |
| 3 | `translating_architecture` | 12% → 20% |
| 4 | `translating_lesson_metadata` | 22% → 28% |
| 5 | `translating_content` | 30% → 50% |
| 6 | `translating_slides` | 55% → 70% |
| 7 | `translating_speech` | 75% → 85% |
| 8 | `translating_glossary_documents` | 88% → 95% |
| 9 | `finalizing` | 95% → 100% |

`_finalize` allinea `target.status = source.status` via
`advance_course_status` (monotonia: video/avatar restano `empty`).

## Service `course_duplication_service`

API pubblica:

- `request_course_duplication(db, source_course, target_language_code, actor_id) → CourseDuplicationJob`
  - Validazione: lingua target ≠ lingua sorgente, lingua attiva in
    DB, no job già attivo per stessa coppia (controllo applicativo +
    unique parziale DB).
  - Audit log `course.duplicate.request`.
- `cancel_duplication(db, job, actor_id) → CourseDuplicationJob`
  - Idempotente. Audit `course.duplicate.cancelled`.
- `list_duplications_for_course(db, course_id) → list[Job]`
  - Tutti i job (qualsiasi stato) in cui il corso è source O target.

Helper interni chiamati dal worker:

- `_clone_course_structure`: crea il corso target come clone dello
  shell di source. Copia metadati, taxonomy term_id, configurazione AI,
  documenti (file su filesystem + nuovo `filename_stored` UUID). I
  campi JSONB testuali (`architecture_raw`, `content_raw`, `slides_raw`,
  `speech_raw`, `glossary_raw`, `documents.summary`) sono copiati
  AS-IS — saranno tradotti in-place dalle fasi successive. Video,
  avatar video e PDF resettati a `empty`.
- `_translate_jsonb_inplace(data, paths, source_lang_*, target_lang_*)`:
  estrae le foglie testuali dichiarate in `paths`, fa un singolo
  `translate_batch` (efficienza token + costo), e riapplica le
  traduzioni in-place mantenendo struttura e tutti i campi non-stringa
  (UUID, numeri, codice mermaid, LaTeX) intatti.
- 7 funzioni granulari: `_translate_course_metadata`,
  `_translate_architecture`, `_translate_lesson` (con `phase` ∈
  meta/content/slides/speech), `_translate_glossary`,
  `_translate_document_summaries`.
- `_finalize`: chiama `advance_course_status` per allineare lo status
  del target.

## Translate paths

File `backend/app/services/course_duplication_paths.py`: single-source-of-truth
dichiarativa di **quali campi dei JSONB sono testo localizzabile**.

Sintassi:
- `"a"` → `obj["a"]` string
- `"a.b"` → object nested
- `"a[]"` → array di stringhe
- `"a[].b"` → field di oggetti in array

Costanti:

| Costante | Si applica a | Esempio path |
|---|---|---|
| `COURSE_METADATA_TRANSLATE_FIELDS` | `Course` (text columns) | `title`, `objectives`, `course_overview`, `pedagogical_rationale` |
| `COURSE_METADATA_TRANSLATE_LIST_FIELDS` | `Course.argomenti_chiave` | array di stringhe |
| `MODULE_TRANSLATE_FIELDS` | `CourseModule` | `title`, `description` |
| `LESSON_TRANSLATE_FIELDS` | `CourseLesson` | `title`, `summary` |
| `LESSON_JSONB_TRANSLATE_PATHS` | `CourseLesson` JSONB | `mandatory_topics[].topic/rationale`, `section_outline[].title/purpose`, `recommended_bibliography[].*` |
| `ARCHITECTURE_TRANSLATE_PATHS` | `Course.architecture_raw` | `modules[].title/description`, `modules[].lessons[].title/summary/recommended_bibliography[].*` |
| `CONTENT_RAW_TRANSLATE_PATHS` | `CourseLesson.content_raw` (didattica) | `sections[].title/content`, `key_takeaways[]`, `visual_assets[].caption/alt_text`, `tables[].caption`, `equations[].label/explanation`, `examples[].title/content`, `references[].citation`, `coverage_check.objectives_covered[].objective` |
| `ASSESSMENT_RAW_TRANSLATE_PATHS` | `CourseLesson.content_raw` (verifica) | `multiple_choice_questions[].text + options[].text`, `open_questions[].text/expected_answer` |
| `SLIDES_RAW_TRANSLATE_PATHS` | `CourseLesson.slides_raw` | `slides[].title/body/bullets[]`, `new_assets[].caption/alt_text` |
| `SPEECH_RAW_TRANSLATE_PATHS` | `CourseLesson.speech_raw` | `speech_segments[].text/delivery_notes` |
| `GLOSSARY_TRANSLATE_PATHS` | `Course.glossary_raw` | `terms[].term/usage_note`. `translation` viene **azzerato** (era già una traduzione contestuale, non più valida) |
| `DOCUMENT_SUMMARY_TRANSLATE_PATHS` | `CourseDocument.summary` | `source_title`, `abstract`, `structure_outline[]`, `key_concepts[].name/explanation`, `definitions[].term/definition`, `examples_or_cases[].title/synthesis`, `formulas_or_rules[].label/meaning`, `authors_and_references[].value`, `didactic_relevance_tags[]` |

**NON tradotti** (preservati AS-IS): tutti gli ID (`lesson_id`,
`section_id`, `asset_id`, `slide_id`, `segment_id`, ecc.), `format`
(Literal mermaid/image/…), `latex`, `markdown`, `mermaid` code,
`detected_language` (codice ISO), numeri, booleani.

## Endpoint REST

| Verb | Path | Permesso | Descrizione |
|---|---|---|---|
| POST | `/orgs/{org_id}/courses/{course_id}/duplicate?target_language_code=X` | `course:duplicate` | Crea job pending. Response 202 con `CourseDuplicationJobOut`. Errori: 409 `duplicate_same_language`, 409 `duplicate_already_in_progress`, 404 `language_not_available`. |
| GET | `/orgs/{org_id}/courses/{course_id}/duplications` | `course:view` | Lista tutti i job (source o target) per quel corso. |
| POST | `/orgs/{org_id}/duplication-jobs/{job_id}/cancel` | `course:duplicate` | Cancel idempotente (pending\|processing → failed). |

`CourseDuplicationJobOut` (campi):
`id`, `source_course_id`, `target_course_id`, `target_language_code`,
`status`, `progress`, `progress_phase`, `error`, `attempts`, `tokens`,
`requested_by_user_id`, `started_at`, `finished_at`, `created_at`,
`updated_at`.

`CourseDuplicationJobCompact` (subset embedded in
`CourseListItemOut.duplication_job`):
`id`, `source_course_id`, `target_course_id`, `target_language_code`,
`status`, `progress`, `progress_phase`.

## Embed nella lista corsi

`course_service.list_courses` esegue una secondary query su
`CourseDuplicationJob` filtrando per `target_course_id IN page_ids AND
status IN ('pending','processing')`, e popola un `duplication_jobs_map`.
`_build_item` lo include nel response come
`CourseListItemOut.duplication_job` (null se non c'è job attivo).

Il FE polla la lista ogni 3s tramite `useQuery refetchInterval`
condizionato alla presenza di almeno un job attivo nella pagina
corrente.

## Frontend

### Componenti

- **`DuplicateCourseDialog`** (`frontend/src/pages/org/courses/components/`):
  dialog con `Select` di lingue (popolato da `useLanguages()`,
  escludendo `course.language_code` corrente). Mutation
  `coursesApi.duplicate(orgId, courseId, target_language_code)`. Su
  success: toast + invalidate `["courses","list",orgId]` + close.
- **`CourseDuplicationBadge`**: badge "Duplicazione in corso" + Flag
  della lingua target + progress bar + bottone Annulla inline.
  Visibile sotto il titolo della riga nella lista corsi quando
  `course.duplication_job != null`. Il bottone "×" chiama
  `coursesApi.cancelDuplication(orgId, jobId)`.

### Integrazione in `CoursesListPage.tsx`

- `useHasPermission(P.COURSE_DUPLICATE, orgId)` controlla la
  visibilità della voce di menu.
- `DropdownMenuItem` "Duplica in altra lingua" tra "Modifica" e
  "Elimina". Mostrato solo se `canDuplicate && !row.original.duplication_job`
  (non duplichi un corso che è già target di un job).
- `useQuery refetchInterval` condizionato: 3000ms quando almeno una
  riga ha `duplication_job?.status ∈ pending|processing`, altrimenti
  `false` (no polling).
- Render del badge nella colonna `title`, sotto il `<Link>` del corso.

### i18n

Chiavi (it/en) sotto `courses.duplicate`:

- `action`: label voce di menu
- `dialog.{title, message, targetLanguage, confirm}`
- `badge.{label, cancel, cancelled}`
- `toast.{success, error}`

Più `permissions.course:duplicate` (label + descrizione del permesso).

## Costi e durata

Per un corso medio (10 lezioni × 4 fasi JSONB + architecture + glossary
+ N documenti):

- ~50–70 chiamate `translate_batch` distribuite in 4 wave parallele
  cap 3 per fase
- Token totali: ~100K combinati input+output (per gpt-4o o equivalente)
- Costo stimato: ~$1-3 USD/duplicazione
- Wall-clock: ~5–10 minuti

Salvato in `job.tokens.wall_clock_seconds` per audit. In futuro estendibile
con tracking granular di `cost_usd` aggregato per ogni `translate_batch`.

## Configurazione

In `backend/app/core/config.py`:

```python
course_duplication_poll_interval_seconds: int = 4
course_duplication_max_concurrent_jobs: int = 1
course_duplication_lesson_translate_concurrency: int = 3
course_duplication_auto_retry_max: int = 5
```

Override via env vars con prefix `A4U_` (es. `A4U_COURSE_DUPLICATION_AUTO_RETRY_MAX=10`).

## Gestione errori

- **Cancellation mid-job**: il corso target resta in DB con i contenuti
  parzialmente tradotti (es. architecture + content tradotti, ma
  slides/speech ancora con `*_status='empty'` resettati). L'utente
  può eliminare il target manualmente o ri-richiedere la duplicazione
  (genera un nuovo job, sovrascrive il target).
- **Retry esauriti**: `job.status='failed'` con `error` valorizzato. Il
  target eventualmente già creato resta in DB. L'utente decide cosa fare.
- **Race cancel/complete**: il worker verifica `job.status` prima della
  phase `finalizing`. Race window minima accettabile.

## File chiave

| File | Ruolo |
|---|---|
| `backend/alembic/versions/0031_course_duplication.py` | Migration tabella + indici + unique parziale + CHECK |
| `backend/app/models/course_duplication_job.py` | Model SQLAlchemy |
| `backend/app/schemas/course_duplication.py` | Pydantic schemas |
| `backend/app/services/course_duplication_paths.py` | Translate paths (single-source-of-truth) |
| `backend/app/services/course_duplication_service.py` | API + clone + 7 funzioni traduzione + finalize |
| `backend/app/services/course_duplication_worker.py` | Async loop + semaforo + `_process_one` 8 fasi + auto-retry |
| `backend/app/api/v1/courses.py` (3 endpoint) | POST duplicate / GET duplications / POST cancel |
| `frontend/src/pages/org/courses/components/DuplicateCourseDialog.tsx` | Dialog Select lingua |
| `frontend/src/pages/org/courses/components/CourseDuplicationBadge.tsx` | Badge progress + cancel |
| `frontend/src/pages/org/courses/CoursesListPage.tsx` | DropdownMenuItem + badge + polling |
| `frontend/src/api/courses.ts` | Tipi + 3 funzioni API |
