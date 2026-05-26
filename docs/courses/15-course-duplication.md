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

**Status target cappato a `slides_approved`**: se il corso source è
oltre questa soglia (es. `speech_approved`, `published`), il target
viene capato a `slides_approved`. Le lezioni con `speech_status="approved"`
vengono downgradate a `"ready"` per forzare la riapprovazione manuale
del discorso tradotto (la qualità della traduzione AI sul discorso è
la più delicata da rivedere). Video e avatar restano `empty`.

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

Migration:
- `backend/alembic/versions/0031_course_duplication.py` — schema iniziale
- `backend/alembic/versions/0032_duplication_progress_detail.py` —
  aggiunge `progress_detail VARCHAR(200)` per il sotto-progresso UX
  ("23/48 lezioni completate")

Schema corrente:

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
  progress_detail        VARCHAR(200),               -- 0032: sub-progress UX
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
- **Concorrenza interna lezioni**: cap configurabile via
  `course_duplication_lesson_translate_concurrency` (default **20**).
  La phase combined (vedi sotto) usa `sem = lesson_cap * 3` = 60 task
  concorrenti (1 per ogni phase content/slides/speech di ogni lezione).
- **Concorrenza chiamate OpenAI**: global semaphore
  `_translate_global_sem` cap **80** chiamate translate_batch concorrenti
  (configurabile via `openai_translate_global_concurrency`). Tutte le
  chiamate (architecture, moduli, lezioni, glossary, documents) ci
  passano.
- **Poll interval**: 4 secondi.
- **Job total timeout**: 90 min (`course_duplication_job_timeout_minutes`).
  Oltre, il job viene marcato `failed` con cleanup automatico del
  target.
- **Auto-retry**: cap 5 (`course_duplication_auto_retry_max=5`). Su
  `OpenAIError` transient il job torna a `pending` con `attempts+1`. Cap
  esaurito → `failed`. **Importante**: con il design multi-pass
  attuale della combined phase, il retry totale del job è raro
  (avviene solo per fail di Phase 3, 4 o 7 — la Phase 5 non solleva mai
  per fail di lezioni).
- **Cancellation**: il service `cancel_duplication` mette il job in
  `failed` (idempotente). Il worker verifica `job.status` prima della
  phase `finalizing` per evitare race window.

### Pipeline `_process_one(job_id)` — 5 phase + finalize

| Phase | progress_phase | progress % | Resume guard |
|---|---|---|---|
| 1 | `loading_source` | 2% | — |
| 2 | `cloning_structure` | 5% → 8% | idempotenza clone (vedi sotto) |
| 3 | `translating_architecture` | 12% → 20% | skip se `resume_from_pct >= 20` |
| 4 | `translating_lesson_metadata` | 25% → 35% | skip se `resume_from_pct >= 35` |
| 5 | `translating_lesson_content_slides_speech` | 40% → 85% | skip se `resume_from_pct >= 85` |
| 7 | `translating_glossary_documents` | 88% → 95% | skip se `resume_from_pct >= 95` |
| Finalize | (null) | 95% → 100% | — |

`_finalize` allinea `target.status` via `advance_course_status`
applicando il cap `slides_approved` (vedi overview).

### Resume-from-progress nei retry

Al claim di un attempt, il worker memorizza il `job.progress` corrente
PRIMA di resettarlo a 2:

```python
resume_from_pct = job.progress if (job.attempts or 0) > 0 else 0
```

Ogni phase atomica (3, 4, 5, 7) controlla `resume_from_pct < soglia`
prima di eseguirsi. Se la phase era già committata da un attempt
precedente, viene skippata con log
`course_duplication_phase_skipped_resume`.

**Effetto pratico**: un fail durante Phase 7 non rifa più
architecture + meta + combined (~30 min) ma solo Phase 7 (~3-5 min).

### Idempotenza clone (Phase 2)

Su retry, il worker verifica se `job.target_course_id` è già
valorizzato. Se sì E il Course esiste, riusa il target esistente. Se
la riga è stata eliminata manualmente fra gli attempt, nullifica
`target_course_id` e ricloara.

Log: `course_duplication_clone_skipped_idempotent`.

### Phase 3 — architecture + moduli paralleli

`_translate_architecture` ora parallelizza i moduli con `asyncio.gather`
ma **ogni modulo apre la propria sessione DB** (via `async_session_factory`).
Senza isolation della sessione, modificare istanze ORM in `asyncio.gather`
sulla stessa `AsyncSession` causava il classico errore SQLAlchemy
`MissingGreenlet: greenlet_spawn has not been called` durante il flush
(vedi storia debugging).

Pattern corretto applicato:

```python
async def _translate_one_module(module_id, position):
    async with async_session_factory() as ldb:
        module = await ldb.get(CourseModule, module_id)
        # ... setattr / flag_modified ...
        await ldb.commit()

module_results = await asyncio.gather(*[
    _translate_one_module(mid, pos)
    for mid, pos in module_ids_positions
], return_exceptions=True)
```

I 6 moduli di un corso medio si traducono in parallelo in **~2 min**
(vs ~10 min sequenziali precedenti).

### Phase 5 — Combined content+slides+speech (multi-pass + fallback model)

La fase più lunga e più fragile, refactor completo per garantire
convergenza:

```python
pending_work: set[tuple[uuid.UUID, str]] = {
    (lid, phase) for lid in lesson_ids for phase in ("content","slides","speech")
}

pass_plan = [
    (0,    None),               # Pass 1: gpt-4o-mini, no wait
    (30,   None),               # Pass 2: gpt-4o-mini, 30s wait
    (90,   None),               # Pass 3: gpt-4o-mini, 90s wait
    (180,  None),               # Pass 4: gpt-4o-mini, 3 min wait
    (30,   fallback_model),     # Pass 5: gpt-4o, 30s wait
    (60,   fallback_model),     # Pass 6: gpt-4o, 1 min wait
]

for sleep_s, model_override in pass_plan:
    if not pending_work: break
    if sleep_s > 0: await asyncio.sleep(sleep_s)
    # gather su pending_work, le phase ok vengono rimosse
    ...
```

Le phase tradotte vengono rimosse dal `pending_work` set. Le residue
passano al pass successivo. Pass 5-6 usano `gpt-4o` (modello fallback,
~16x più costoso ma molto più stabile su transient OpenAI/Cloudflare).

**Niente retry totale del job dalla combined phase**: anche se restano
phase residue dopo tutti i 6 pass + fallback, il job procede al
finalize. Log `course_duplication_combined_residual_failures` con
elenco esplicito (probabilmente <1% delle phase). L'utente le
rigenererà manualmente dal tab specifico della lezione.

### Phase 7 — Glossary + Document summaries

`_translate_glossary` traduce `target.glossary_raw` (term + usage_note)
e **azzera** `terms[].translation` (era una traduzione contestuale
DALLA lingua source verso un'altra, non più valida nel nuovo contesto).

`_translate_document_summaries` itera sui documenti caricati e traduce
ogni `doc.summary` JSONB. Aggiorna `summary.detected_language` al nuovo
codice target.

Se Phase 7 fallisce (es. transient OpenAI persistenti), si scatena un
retry totale, ma il resume guard skippa Phase 3+4+5 → solo Phase 7 si
rifa.

## Cleanup automatico target

Su fail terminale del job (5 retry esauriti) o su timeout totale (90 min),
il worker elimina automaticamente il `target_course` (cascade su moduli +
lezioni + documenti tramite `ondelete=CASCADE`). Niente più stub di
corsi target falliti accumulati nel DB.

Log: `course_duplication_target_cleaned_up`.

Su cancellation utente: il target NON viene eliminato (parzialmente
tradotto, l'utente decide cosa farne).

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
  campi JSONB testuali sono copiati AS-IS — saranno tradotti in-place
  dalle fasi successive. Video, avatar video e PDF resettati a `empty`.

- `_translate_jsonb_inplace(data, paths, ...)`: estrae le foglie
  testuali dichiarate in `paths`, fa chunk parallelizzati
  (`_TRANSLATE_CHUNK_SIZE = 25` items per chiamata, tutti i chunk in
  `asyncio.gather`), riapplica le traduzioni in-place. I chunk failed
  dopo 4 retry interni vengono loggati come `course_duplication_chunk_failed`
  ma non bloccano il caller.

- `_translate_batch_resilient`: wrapper attorno a `translate_batch` con:
  - 4 tentativi (1 initial + 3 retry)
  - Backoff esponenziale `1s → 3s → 9s`
  - Discriminazione: 4xx → fail immediato (auth/validation, no retry),
    5xx o `status=None` (httpx) → retry
  - Acquire del global semaphore `_translate_global_sem`
  - `model_override` propagato per fallback gpt-4o
  - Log `course_duplication_translate_retry` per ogni retry

- `_translate_course_metadata`, `_translate_architecture`,
  `_translate_lesson`, `_translate_glossary`,
  `_translate_document_summaries`: granulari per phase.

- `_finalize`: cap status target con
  `_cap_status_for_duplication(source.status)` + downgrade speech.

### Cap status helper

```python
_SLIDES_APPROVED_OR_BELOW: frozenset[str] = frozenset({
    "draft",
    "architecture_pending", "architecture_ready", "architecture_approved",
    "lessons_structure_pending", "lessons_structure_ready", "lessons_structure_approved",
    "content_pending", "content_ready", "content_approved",
    "slides_pending", "slides_ready", "slides_approved",
})

def _cap_status_for_duplication(source_status: str) -> str:
    if source_status in _SLIDES_APPROVED_OR_BELOW:
        return source_status
    return "slides_approved"
```

Per le lezioni:

```python
for module in target.modules:
    for lesson in module.lessons:
        if lesson.speech_status == "approved":
            lesson.speech_status = "ready"
            lesson.speech_approved_at = None
```

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
| `LESSON_JSONB_TRANSLATE_PATHS` | `CourseLesson` JSONB | `learning_objectives[]`, `mandatory_topics[].topic/rationale`, `prerequisites[]`, `section_outline[].title/purpose`, `recommended_bibliography[].*` |
| `ARCHITECTURE_TRANSLATE_PATHS` | `Course.architecture_raw` | `modules[].title/description`, `modules[].lessons[].title/summary/recommended_bibliography[].*` |
| `_LESSONS_STRUCTURE_RAW_PATHS` | `CourseModule.lessons_structure_raw` | `lessons[].title/learning_objectives[]/mandatory_topics[].*/prerequisites[]/section_outline[].*` |
| `CONTENT_RAW_TRANSLATE_PATHS` | `CourseLesson.content_raw` (didattica) | `sections[].title/content`, `key_takeaways[]`, `visual_assets[].caption/alt_text`, `tables[].caption`, `equations[].label/explanation`, `examples[].title/content`, `references[].citation`, `coverage_check.objectives_covered[].objective` |
| `ASSESSMENT_RAW_TRANSLATE_PATHS` | `CourseLesson.content_raw` (verifica) | `multiple_choice_questions[].text + options[].text`, `open_questions[].text/expected_answer` |
| `SLIDES_RAW_TRANSLATE_PATHS` | `CourseLesson.slides_raw` | `slides[].title/body/bullets[]`, `new_assets[].caption/alt_text` |
| `SPEECH_RAW_TRANSLATE_PATHS` | `CourseLesson.speech_raw` | `speech_segments[].text/delivery_notes` |
| `GLOSSARY_TRANSLATE_PATHS` | `Course.glossary_raw` | `terms[].term/usage_note`. `translation` viene **azzerato** |
| `DOCUMENT_SUMMARY_TRANSLATE_PATHS` | `CourseDocument.summary` | `source_title`, `abstract`, `structure_outline[]`, `key_concepts[].name/explanation`, `definitions[].term/definition`, `examples_or_cases[].title/synthesis`, `formulas_or_rules[].label/meaning`, `authors_and_references[].value`, `didactic_relevance_tags[]` |

**NON tradotti** (preservati AS-IS): tutti gli ID (`lesson_id`,
`section_id`, `asset_id`, `slide_id`, `segment_id`, ecc.), `format`
(Literal mermaid/image/…), `latex`, `markdown`, `mermaid` code,
`detected_language` (codice ISO), numeri, booleani.

## Client OpenAI condiviso

**Critical**: `backend/app/services/openai_client.py` espone un
**singleton** `httpx.AsyncClient` condiviso da TUTTI i servizi OpenAI
(translate, summarize, lesson_content, slides, speech, architecture,
glossary, nova, ecc.).

Prima del refactor, ogni chiamata costruiva un nuovo
`httpx.AsyncClient`: con 150 chiamate concorrenti (duplicazione tier
5) → 150 TLS handshake + 150 pool separati → frequenti
`httpx.ReadTimeout`, `RemoteProtocolError`, `PoolTimeout` che
apparivano nei log come `[OpenAI None] Errore HTTP verso OpenAI:` con
messaggio vuoto.

Singleton configurato con:

```python
httpx.AsyncClient(
    timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=60.0),
    limits=httpx.Limits(
        max_connections=300,
        max_keepalive_connections=100,
        keepalive_expiry=60.0,
    ),
)
```

API compatibile (`async with get_client() as c:` via `_SharedClientProxy`
con `__aexit__` no-op).

## Endpoint REST

| Verb | Path | Permesso | Descrizione |
|---|---|---|---|
| POST | `/orgs/{org_id}/courses/{course_id}/duplicate?target_language_code=X` | `course:duplicate` | Crea job pending. Response 202 con `CourseDuplicationJobOut`. Errori: 409 `duplicate_same_language`, 409 `duplicate_already_in_progress`, 404 `language_not_available`. |
| GET | `/orgs/{org_id}/courses/{course_id}/duplications` | `course:view` | Lista tutti i job (source o target) per quel corso. |
| POST | `/orgs/{org_id}/duplication-jobs/{job_id}/cancel` | `course:duplicate` | Cancel idempotente (pending\|processing → failed). |

`CourseDuplicationJobOut` (campi):
`id`, `source_course_id`, `target_course_id`, `target_language_code`,
`status`, `progress`, `progress_phase`, `progress_detail`, `error`,
`attempts`, `tokens`, `requested_by_user_id`, `started_at`,
`finished_at`, `created_at`, `updated_at`.

`CourseDuplicationJobCompact` (subset embedded in
`CourseListItemOut.duplication_job`):
`id`, `source_course_id`, `target_course_id`, `target_language_code`,
`status`, `progress`, `progress_phase`, `progress_detail`, `started_at`.

Il `started_at` è esposto nel Compact (oltre che nel full) per
permettere al FE di calcolare l'ETA stimato senza chiamare l'endpoint
dedicato.

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

### `DuplicateCourseDialog`

`frontend/src/pages/org/courses/components/DuplicateCourseDialog.tsx`:
dialog con `Select` di lingue (popolato da `useLanguages()`,
escludendo `course.language_code` corrente). Mutation
`coursesApi.duplicate(orgId, courseId, target_language_code)`. Su
success: toast + invalidate `["courses","list",orgId]` (+ re-invalidate
ogni 2s per 16s totali per non aspettare il polling regolare prima di
vedere comparire il target).

### `CourseDuplicationBadge` (rich UX)

`frontend/src/pages/org/courses/components/CourseDuplicationBadge.tsx`.

Layout 4 righe:

```
┌────────────────────────────────────────────┐
│ 🌐 DUPLICAZIONE → 🇭🇷 Hrvatski    5/7 [✕] │
│ ⟳ Traduzione contenuti, slide e discorso   │
│ 23/48 lezioni completate • ~4 min rimanenti│
│ ████████████████░░░░░░░░░░░░ shimmer  72%  │
└────────────────────────────────────────────┘
```

**1. Header** — icona globe + label `DUPLICAZIONE` + freccia +
bandiera + nome nativo della lingua target (es. "Hrvatski") + step
indicator "5/7" (hover → tooltip pipeline) + bottone `✕` annulla.

**2. Phase label** — spinner `Loader2` animato + label localizzata
della fase corrente. Mapping `progress_phase` →
`courses.duplicate.badge.phases.*`:

| Phase | Label IT | Step |
|---|---|---|
| `loading_source` | Caricamento corso sorgente | 1/7 |
| `cloning_structure` | Clonazione struttura corso | 2/7 |
| `translating_architecture` | Traduzione architettura e moduli | 3/7 |
| `translating_lesson_metadata` | Traduzione titoli e obiettivi lezioni | 4/7 |
| `translating_lesson_content_slides_speech` | Traduzione contenuti, slide e discorso | 5/7 |
| `translating_glossary_documents` | Traduzione glossario e documenti | 6/7 |
| `finalizing` (fallback progress≥95) | Finalizzazione | 7/7 |
| `pending` (status=pending) | In attesa di avvio… | – |

**3. Sub-progress + ETA** — riga opzionale che combina:
- `job.progress_detail` (es. "23/48 lezioni completate") popolato dal
  backend nella combined phase
- ETA calcolato dal FE da `started_at` + `progress`. Format adattivo
  (`~30s rimanenti` / `~4 min rimanenti` / `~1h 20 min rimanenti`).
  Auto-refresh ogni 5s via `setInterval` tra un polling list e l'altro.

**4. Progress bar** — `Progress` shadcn h-2 con classe
`progress-shimmer` (animazione gradient gestita in `index.css`) attiva
durante `status=processing`/`pending`. % prominente a destra.

### Tooltip pipeline

Hover sullo step indicator "5/7" → tooltip con lista completa delle 7
phase:
- Phase completate: ✓
- Phase corrente: spinner + bold
- Phase future: pallino grigio + opacity 50%

### Integrazione in `CoursesListPage.tsx`

- `useHasPermission(P.COURSE_DUPLICATE, orgId)` controlla la
  visibilità della voce di menu.
- `DropdownMenuItem` "Duplica in altra lingua" mostrato solo se
  `canDuplicate && !row.original.duplication_job`.
- `useQuery refetchInterval` condizionato: 3000ms quando almeno una
  riga ha `duplication_job?.status ∈ pending|processing`, altrimenti
  `false` (no polling).
- Render del badge nella colonna `title`, sotto il titolo.

### i18n

Chiavi (it/en) sotto `courses.duplicate`:

- `action`: label voce di menu
- `dialog.{title, message, targetLanguage, confirm}`
- `badge.{label, cancel, cancelled, step, stepsTitle, etaSeconds, etaMinutes, etaHours, phases.*}`
- `toast.{success, error}`

Più `permissions.course:duplicate` (label + descrizione del permesso).

## Configurazione

In `backend/app/core/config.py`:

```python
# Worker
course_duplication_poll_interval_seconds: int = 4
course_duplication_max_concurrent_jobs: int = 1
course_duplication_lesson_translate_concurrency: int = 20
course_duplication_auto_retry_max: int = 5
course_duplication_job_timeout_minutes: int = 90

# OpenAI
openai_model: str = "gpt-4o-mini"
openai_model_fallback: str = "gpt-4o"
openai_translate_global_concurrency: int = 80

# DB pool (richiesto da alta concorrenza)
database_pool_size: int = 20
database_max_overflow: int = 60
```

Override via env vars con prefix `A4U_`:

```env
# Aggressive tuning (richiede Postgres max_connections >= 150-200)
A4U_OPENAI_TRANSLATE_GLOBAL_CONCURRENCY=150
A4U_COURSE_DUPLICATION_LESSON_TRANSLATE_CONCURRENCY=30
A4U_DATABASE_POOL_SIZE=30
A4U_DATABASE_MAX_OVERFLOW=80
A4U_COURSE_DUPLICATION_JOB_TIMEOUT_MINUTES=120
```

**Postgres `max_connections`**: alzato da default 100 a 200 in
`docker-compose.yml` (`-c max_connections=200`) per supportare il pool
backend a 110 connessioni + margine per autovacuum/replication/admin.

## Costi e durata

Per un corso medio (48 lezioni × 3 phase + architecture + 6 moduli +
glossary + N documenti):

- ~150-200 chiamate `translate_batch` totali
- Token totali: ~300-500K input + 200-300K output (gpt-4o-mini)
- Costo stimato: **~$0.20-0.40** per duplicazione
- Wall-clock: **~3-5 minuti** con concorrenza 150 (era ~1h+ prima dei
  fix di parallelizzazione)
- Fallback gpt-4o usato solo su phase residue (tipicamente 5-15 phase
  su ~144 totali). Costo extra trascurabile (~$0.10-0.20).

Salvato in `job.tokens.wall_clock_seconds` per audit. In futuro
estendibile con tracking granulare di `cost_usd`.

## Logging

Eventi strutturati emessi dal worker durante la duplicazione:

| Event | Quando | Level |
|---|---|---|
| `course_duplication_clone_skipped_idempotent` | Retry con target già presente | info |
| `course_duplication_target_missing_reclone` | Target eliminato manualmente fra attempt | warning |
| `course_duplication_resume_from_progress` | Inizio attempt N>1 con resume | info |
| `course_duplication_phase_skipped_resume` | Phase saltata al retry | info |
| `course_duplication_chunk_translated` | Chunk OpenAI completato | info |
| `course_duplication_chunk_failed` | Chunk fallito dopo 4 retry interni | warning |
| `course_duplication_translate_retry` | Retry interno chunk | warning |
| `course_duplication_module_translate_error` | Fail di un modulo in Phase 3 | warning |
| `course_duplication_combined_pass_wait` | Sleep prima di un pass combined | info |
| `course_duplication_combined_pass_start` | Pass combined iniziato | info |
| `course_duplication_combined_pass_done` | Pass combined finito | info |
| `course_duplication_combined_phase_fully_converged` | Tutte le phase tradotte | info |
| `course_duplication_combined_residual_failures` | Phase ancora pending dopo 6 pass | error |
| `course_duplication_lesson_phase_inner_error` | Fail di una singola phase di lezione | warning |
| `course_duplication_target_cleaned_up` | Target eliminato (fail terminale/timeout) | info |
| `course_duplication_job_total_timeout` | Job superato 90 min | error |
| `course_duplication_unhandled_exception` | Eccezione non gestita | error |
| `course_duplication_completed` | Job ready (success) | info |

### Comandi di monitoring

```bash
# Vista alto livello (raccomandato)
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend \
  | grep -E "course_duplication_(resume_from_progress|phase_skipped_resume|combined_pass_(wait|start|done)|combined_phase_fully_converged|combined_residual_failures|target_cleaned_up|clone_skipped_idempotent|completed)"

# Vista completa con chunk
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend \
  | grep -E "course_duplication_(chunk_translated|combined_pass|residual|converged|completed|cleaned_up|second_pass|phase_inner_error|translate_retry|module_translate_error)"

# Solo errori/warning
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend \
  | grep -E "course_duplication_(chunk_failed|phase_inner_error|residual_failures|module_translate_error|target_cleaned_up|job_total_timeout|unhandled_exception)"
```

## Gestione errori

- **Phase residue dopo 6 pass + fallback**: il job procede comunque al
  finalize. L'utente riceve un corso target con la grande maggioranza
  delle lezioni tradotte e può rigenerare manualmente le residue dal
  tab Contenuti/Slide/Discorso della lezione specifica. Log
  `course_duplication_combined_residual_failures` con elenco.
- **Cancellation mid-job**: target resta in DB con i contenuti
  parzialmente tradotti. L'utente lo elimina manualmente o
  ri-richiede la duplicazione.
- **Retry esauriti (5/5)**: `job.status='failed'`. Target eliminato
  automaticamente via cascade (vedi "Cleanup automatico target").
- **Timeout 90 min**: `job.status='failed'` con
  `error="Timeout totale del job (90 min)."`. Target eliminato.
- **Race cancel/complete**: il worker verifica `job.status` prima
  della phase `finalizing`. Race window minima accettabile.

## File chiave

| File | Ruolo |
|---|---|
| `backend/alembic/versions/0031_course_duplication.py` | Migration tabella + indici + unique parziale + CHECK |
| `backend/alembic/versions/0032_duplication_progress_detail.py` | Colonna `progress_detail` per sub-progress UX |
| `backend/app/models/course_duplication_job.py` | Model SQLAlchemy |
| `backend/app/schemas/course_duplication.py` | Pydantic schemas (Compact + Full) |
| `backend/app/services/course_duplication_paths.py` | Translate paths (single-source-of-truth) |
| `backend/app/services/course_duplication_service.py` | API + clone idempotente + 7 funzioni traduzione + finalize con cap status |
| `backend/app/services/course_duplication_worker.py` | Async loop + semaforo + `_process_one` 5 phase + multi-pass + resume + cleanup |
| `backend/app/services/openai_client.py` | Client httpx singleton condiviso (pool 300, keepalive 60s) |
| `backend/app/services/openai_translate_service.py` | `translate_batch` con `model_override` (fallback gpt-4o) |
| `backend/app/api/v1/courses.py` (3 endpoint) | POST duplicate / GET duplications / POST cancel |
| `frontend/src/pages/org/courses/components/DuplicateCourseDialog.tsx` | Dialog Select lingua + invalidate burst |
| `frontend/src/pages/org/courses/components/CourseDuplicationBadge.tsx` | Badge rich UX (ETA + tooltip + shimmer + sub-progress) |
| `frontend/src/pages/org/courses/CoursesListPage.tsx` | DropdownMenuItem + badge + polling condizionato 3s |
| `frontend/src/index.css` | Keyframe `a4u-shimmer` |
| `docker-compose.yml` | `max_connections=200` per supportare alta concorrenza pool DB |
