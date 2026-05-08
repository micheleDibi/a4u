# 07 — Lesson structure (Fase 2)

Generazione AI della **struttura delle lezioni**: per ogni lezione di un modulo già
approvato in Fase 1, l'AI produce **obiettivi formativi**, **temi obbligatori**,
**prerequisiti** e **scaletta delle sezioni**. Implementa §5 di
`prompt_generazione_corsi.md` con regenerazione mirata (§9.2).

A differenza della Fase 1 che è single-shot a livello corso, **Fase 2 lavora a granularità
modulo**: ogni modulo ha un proprio stato indipendente
(`empty → pending → processing → ready → approved`, oppure `failed`) e il batch dispatcha
in **parallelo** tutti i moduli con un cap di concorrenza.

## Flusso

### Per modulo (singolo)

```
empty / ready / approved
   │
   │  POST /modules/{mid}/lessons-structure/generate { regeneration_hint? }
   ▼
module.lessons_structure_status = 'pending'
   │
   │  worker tick (poll ogni 4s) → vede status 'pending'
   ▼
status='processing', progress=5%, phase=preparing_prompt
   │
   │  load_course_full + build_user_prompt (§5.2)
   ▼
progress=15%, phase=calling_openai
   │   ┌──────────────────────────────────────────────┐
   │   │ ticker background: ease-out 15→85% in 40s    │
   │   │ con sessione DB indipendente                 │
   │   └──────────────────────────────────────────────┘
   │   generate_lesson_structure (§5.1 system + §5.3 schema)
   ▼
progress=90%, phase=materializing
   │
   │  validazioni §5.4 + scrittura su course_lesson (4 campi JSONB)
   ▼
status='ready', progress=100%
   │
   │  POST /modules/{mid}/lessons-structure/approve
   ▼
status='approved'
```

### Batch (tutti i moduli)

```
POST /lessons-structure/generate-all { regeneration_hint? }
   │
   ▼
Per OGNI modulo: lessons_structure_status='pending'
course.status = 'lessons_structure_pending'
   │
   │  worker tick → SELECT module_id WHERE lessons_structure_status='pending'
   ▼
Per ogni module_id NON in `_inflight`:
   asyncio.create_task(_bound_process(module_id))
                       └─ async with _semaphore (cap = 5):
                          async with AsyncSessionLocal() as task_db:
                              await _process_one(task_db, module_id)
   │
   ▼
Fino a 5 moduli elaborati in parallelo. Quando uno termina,
il prossimo `pending` viene dispatcato al tick successivo.
   │
   ▼
Quando TUTTI ready/approved → course.status = 'lessons_structure_ready'
Quando TUTTI approved → course.status = 'lessons_structure_approved'
```

In caso di errore (OpenAI 4xx/5xx, validazione fallita, JSON malformato):
`module.lessons_structure_status = 'failed'`, `lessons_structure_error` popolato.
L'utente fa "Riprova" sul singolo modulo.

## Worker parallelo — `course_lesson_structure_worker.py`

Riferimento: `backend/app/services/course_lesson_structure_worker.py`.

Differenze chiave dal worker di Fase 1 (`course_architecture_worker.py`):

| Aspetto | Fase 1 | Fase 2 |
|---|---|---|
| Granularità lavoro | Course | Module |
| Dispatch | Sequenziale (1 alla volta) | **Parallelo** (cap = 5) |
| Sessioni DB | 1 condivisa | 1 per task — **AsyncSession indipendente** |
| Coordinamento | — | `_inflight: set[UUID]` + `asyncio.Semaphore` |
| Cancellazione | Singolo `task.cancel()` | `asyncio.gather(*_active_tasks)` su shutdown |

Pattern del worker (claim atomico in `_tick`, dispatch poi):

```python
_semaphore: asyncio.Semaphore | None = None
_inflight: set[uuid.UUID] = set()
_inflight_lock = asyncio.Lock()
_active_tasks: set[asyncio.Task] = set()

async def _tick() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CourseModule.id).where(
                CourseModule.lessons_structure_status == "pending"
            )
        )
        module_ids = [row[0] for row in result.all()]

    # Dedup + claim ATOMICO sotto lock. Il claim DEVE avvenire qui (e
    # non dentro `_bound_process` dopo aver acquisito il semaforo) per
    # evitare che il tick successivo ridispatchi le task ancora in coda
    # dietro al semaforo (cap=5 ma `pending_count > 5` è normale durante
    # un batch grosso). Senza claim qui → storm di skip log.
    async with _inflight_lock:
        new_ids = [mid for mid in module_ids if mid not in _inflight]
        for mid in new_ids:
            _inflight.add(mid)

    for module_id in new_ids:
        task = asyncio.create_task(_bound_process(module_id))
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)

async def _bound_process(module_id: uuid.UUID) -> None:
    assert _semaphore is not None
    try:
        async with _semaphore:
            async with AsyncSessionLocal() as task_db:
                await _process_one(task_db, module_id)
    finally:
        async with _inflight_lock:
            _inflight.discard(module_id)
```

Vantaggi:
- **Throughput**: 6 moduli con cap=5 → ~80s totali invece di ~3-4 min sequenziali.
- **Sessioni DB indipendenti**: nessuna race su `flush`/`commit`.
- **Doppio dispatch evitato anche con coda satura**: il claim atomico in `_tick`
  marca tutti i nuovi `module_id` come inflight prima del dispatch, così il
  tick successivo li dedupa anche se sono ancora in coda dietro al semaforo.
- **Shutdown pulito**: `stop_worker()` cancella tutti i task in flight via
  `asyncio.gather`.

Il **ticker di progresso** (`_progress_ticker`) ha la sua sessione DB autonoma per
non bloccare la transazione del task durante l'attesa OpenAI:

```python
async def _progress_ticker(module_id: uuid.UUID, start: int, end: int, total_seconds: float):
    interval = 2.0
    elapsed = 0.0
    while elapsed < total_seconds:
        await asyncio.sleep(interval)
        elapsed += interval
        ratio = min(1.0, elapsed / total_seconds)
        eased = 1 - (1 - ratio) ** 2
        pct = int(start + (end - start) * eased)
        async with AsyncSessionLocal() as t_db:
            await t_db.execute(
                update(CourseModule)
                .where(
                    CourseModule.id == module_id,
                    CourseModule.lessons_structure_status == "processing",
                )
                .values(lessons_structure_progress=pct)
            )
            await t_db.commit()
```

Curva ease-out: `eased = 1 - (1 - ratio)²`. Aggiornamento ogni 2s, fascia 15→85%.

## Service di orchestrazione — `course_lesson_structure_service.py`

### `request_module_generation`

```python
async def request_module_generation(
    db, course, module, *, hint, actor_id
) -> Course
```

- Verifica `course.status ∈ {architecture_approved, lessons_structure_*}`
- Imposta `module.lessons_structure_status = 'pending'`, clear error, salva hint
- Aggiorna `course.status` a `lessons_structure_pending` se non già in lessons_structure_*
- Audit `course.module.lessons_structure.generate.requested`

### `request_all_modules_generation`

```python
async def request_all_modules_generation(
    db, course, *, hint, actor_id
) -> Course
```

- Imposta TUTTI i moduli del corso a `pending`, salva hint su ognuno
- `course.status = 'lessons_structure_pending'`
- Audit `course.lessons_structure.generate.requested` con `meta.modules_count = N`

### `materialize_module_structure`

```python
async def materialize_module_structure(
    db, course, module, *, output, tokens
) -> Course
```

**Validazioni §5.4** prima della scrittura:

1. **Conteggio lezioni**: `len(output.lessons) == len(module.lessons)` — altrimenti
   `OpenAILessonStructureError("lessons count mismatch")`
2. **Match lesson_id ↔ lesson_code**: ogni `output.lessons[i].lesson_id` deve
   esistere come `lesson_code` (M1.L1, …) in `module.lessons`
3. **Prefisso obiettivi**: tutti gli `objective` devono iniziare con
   - `"Lo studente sarà in grado di"` per IT
   - `"The student will be able to"` per EN
   - per altre lingue, validazione soft (warning solo)
4. **Coverage temi**: per ogni lezione, l'unione dei `covers_topic_ids` su tutte le
   sezioni deve coprire **TUTTI** i `mandatory_topics.topic_id`
5. **Riferimenti validi**: ogni `covers_topic_ids[i]` deve esistere come `topic_id`
   nei `mandatory_topics` della stessa lezione
6. **Univocità**: `topic_id` univoci per lezione, `section_id` univoci per lezione

Su validazione OK:
- Scrive 4 campi JSONB su ogni `course_lesson` del modulo (match `lesson_code`):
  `learning_objectives`, `mandatory_topics`, `prerequisites`, `section_outline`
- Aggiorna meta del modulo: `lessons_structure_raw`, `_tokens`, `_status='ready'`,
  `_generated_at`, `_progress=100`, clear error
- Side-effect: chiama `_recompute_course_lessons_structure_status` per aggiornare
  lo status del corso (derivato dagli stati dei moduli)

### `approve_module_structure` / `approve_all_modules_structure`

- `approve_module_structure`: solo se `module.lessons_structure_status = 'ready'`.
  Set a `'approved'` + `approved_at = now()`.
- `approve_all_modules_structure`: solo se TUTTI i moduli sono in `ready`.
  Batch update + `course.status = 'lessons_structure_approved'`.

### `_recompute_course_lessons_structure_status` (privato)

Helper che aggiorna `course.status` in base agli stati dei moduli:
- almeno 1 modulo in `pending|processing` → `lessons_structure_pending`
- TUTTI in `ready|approved` (e almeno 1 in `ready`) → `lessons_structure_ready`
- TUTTI in `approved` → `lessons_structure_approved`

**NON** sovrascrive `course.status` se è in `architecture_*` (es. l'utente ha
rolled-back l'approvazione di Fase 1 — fuori scope qui).

## Wrapper OpenAI — `openai_lesson_structure_service.py`

- **Sistem prompt**: §5.1 verbatim con placeholder `{{lingua}}` sostituito dal
  codice lingua corso. Per **rigenerazione** (`is_regeneration=True`), concatena
  il `LESSON_STRUCTURE_REGENERATION_SUFFIX` di §9.2.
- **User prompt**: `_build_user_prompt` rispetta il template §5.2 con:
  1. parametri corso (lingua, titolo, obiettivi)
  2. dati architettura del modulo target (titolo, descrizione, lezioni come
     `M_x.L_y — title — summary`)
  3. compact view di tutti i moduli del corso (per coerenza globale)
  4. summaries dei documenti caricati (budget
     `course_lesson_structure_documents_context_max_chars`, default 30000)
  5. struttura attuale del modulo (solo se `is_regeneration=True`)
  6. hint utente (se presente)
- **JSON schema strict**: §5.3 verbatim, passato come `response_format`
  con `type: json_schema` e `strict: true`.
- **Modello**: `settings.openai_lesson_structure_model` (default `gpt-5.5`).
- **Token cap**: `settings.openai_lesson_structure_max_tokens` (default 16000).
  Niente `temperature` (gpt-5.5 non lo supporta). gpt-5.5 consuma molti
  token in reasoning prima del JSON: alza ulteriormente se vedi
  `lessons_structure_output_truncated` nei log.
- **Reasoning effort**: `settings.openai_lesson_structure_reasoning_effort`
  (default `medium`). Iniettato via `apply_reasoning_effort()` solo se il
  modello è reasoning (`gpt-5.x`/`o1*`/`o3*`/`o4*`); su modelli classici
  il parametro è omesso. Vedi
  [04 — Configuration](../04-configuration.md#reasoning-effort-gpt-5x--o1--o3--o4).
- **Errori**: `OpenAILessonStructureError(OpenAIError)` per HTTPx fail, JSON
  decode, schema validation. `OpenAINotConfiguredError` riusato.

## CRUD manuale — `course_lesson_structure_crud.py`

`update_lesson_structure(db, course, lesson, *, payload, actor_id) -> Course`:

- **Gating**: solo se `lesson.module.lessons_structure_status ∈ {ready, approved}`,
  altrimenti `ConflictError(code='lessons_structure_not_editable')`.
- **Validazioni client-side replicate server-side**:
  - obiettivi: prefisso lingua-corretto (warning, non blocking lato server)
  - `topic_id` univoci all'interno della lezione
  - `section_id` univoci all'interno della lezione
  - `covers_topic_ids[i]` referenziano `mandatory_topics.topic_id` esistenti
- Patch dei 4 campi opzionali: campi non presenti nel payload restano invariati.
- Audit: `course.lesson.structure.updated` con diff sintetico.
- Side-effect: l'edit manuale **non** degrada lo stato del modulo (un modulo
  `approved` resta `approved` anche dopo un edit manuale).

## API endpoints

| Metodo | Path | Permission | Effetto |
|---|---|---|---|
| `POST` | `/modules/{mid}/lessons-structure/generate` | `course:generate` | Body `LessonStructureGenerateInput`. 202. Set modulo `pending`. |
| `POST` | `/lessons-structure/generate-all` | `course:generate` | Body `LessonStructureGenerateInput`. 202. Set tutti i moduli `pending`. |
| `POST` | `/modules/{mid}/lessons-structure/approve` | `course:generate` | 200. Approve modulo singolo (richiede `ready`). |
| `POST` | `/lessons-structure/approve-all` | `course:generate` | 200. Approve batch (richiede tutti `ready`). |
| `PATCH` | `/lessons/{lid}/structure` | `course:edit` | Body `LessonStructureUpdateInput`. 200. CRUD manuale. |

Tutti restituiscono `CourseOut` aggiornato. Codici errore principali:
`lessons_structure_not_editable`, `module_not_ready_for_approve`,
`not_all_modules_ready`, `module_not_found`, `lesson_not_found`,
`openai_not_configured`, `lessons_structure_generation_failed`.

## Schema dati

### `course_module` — meta Fase 2 (10 colonne)

| Colonna | Tipo | Note |
|---|---|---|
| `lessons_structure_status` | VARCHAR(40) | CHECK in (`empty,pending,processing,ready,approved,failed`) |
| `lessons_structure_raw` | JSONB | output AI completo (verbatim §5.3) |
| `lessons_structure_tokens` | JSONB | `{prompt, completion, total, model}` |
| `lessons_structure_attempts` | SMALLINT | counter retry |
| `lessons_structure_error` | VARCHAR(500) | messaggio errore breve |
| `lessons_structure_generated_at` | TIMESTAMPTZ | timestamp ultima materializzazione OK |
| `lessons_structure_approved_at` | TIMESTAMPTZ | timestamp approve modulo |
| `lessons_structure_regeneration_hint` | TEXT | hint utente per rigenerazione (§9.2) |
| `lessons_structure_progress` | SMALLINT | 0–100 per ticker |
| `lessons_structure_progress_phase` | VARCHAR(50) | etichetta fase |

### `course_lesson` — payload Fase 2 (4 colonne JSONB)

| Colonna | Tipo | Default | Note |
|---|---|---|---|
| `learning_objectives` | JSONB NOT NULL | `'[]'` | array di string, 3-6 per lezione |
| `mandatory_topics` | JSONB NOT NULL | `'[]'` | array di `{topic_id, topic, rationale}` |
| `prerequisites` | JSONB NOT NULL | `'[]'` | array di string, 0-5 per lezione |
| `section_outline` | JSONB NOT NULL | `'[]'` | array di `{section_id, title, purpose, covers_topic_ids[]}` |

Migration: `0014_lesson_structure.py` aggiunge tutte le colonne, estende il
CHECK constraint di `course.status` con i 3 nuovi stati.

## Frontend

### Tab "Struttura lezioni" — `CourseEditorPage.tsx`

5° tab `<TabsTrigger value="lessons-structure">`, gated su
`course.status >= architecture_approved`. Polling esteso: la query del corso
ha `refetchInterval=5000` quando almeno un modulo è in `pending|processing`.

### `CourseLessonStructureView.tsx`

Layout principale del tab:

- **Header card**: titolo, descrizione, pulsante "Genera/Rigenera struttura per
  tutti i moduli", "Approva tutto" (visibile quando tutti i moduli sono `ready`).
- **Aggregate progress bar** (sempre visibile durante batch):
  - Etichetta `{n_completed}/{n_total} moduli completati ({percent}%)`
  - `percent = avg(progress per modulo)` (i moduli `ready/approved` contano 100%)
  - Conteggio moduli `failed` con messaggio destructive
  - **ETA + tempo medio per modulo** durante un batch attivo: `useBatchEta`
    (vedi [Frontend 08 — Hooks](../frontend/08-hooks.md)) deriva la velocità
    dai timestamp `lessons_structure_generated_at` dei moduli completati
    nella recent window (90 min) e stima il tempo rimanente come
    `avgPerModule × remaining`
- **Lista moduli** (una card per modulo): badge stato + pulsante azione contestuale
  - Stato `empty` → "Genera"
  - Stato `pending|processing` → spinner + Progress bar + label fase + percentuale
  - Stato `failed` → alert destructive + dettaglio errore + "Riprova"
  - Stato `ready` → "Rigenera" + "Approva"
  - Stato `approved` → "Rigenera" (read-only il resto)
- **Per ogni lezione del modulo** (in `ready/approved`): row collapsible con
  4 sub-sezioni accordion:
  1. **Obiettivi formativi** — lista numerata
  2. **Temi obbligatori** — chip `topic_id` + topic + rationale
  3. **Prerequisiti** — bullet list
  4. **Scaletta sezioni** — lista numerata con `section_id` + title + purpose + chips coverage

### `LessonStructureEditDialog.tsx`

Dialog `max-w-4xl` per CRUD manuale di una lezione. 4 fieldset:

1. **Obiettivi**: lista riordinabile (↑↓), warning prefisso lingua-corretto
2. **Temi**: lista con auto-genID (T1, T2, …), validation univocità
3. **Prerequisiti**: lista semplice
4. **Sezioni**: lista riordinabile + multi-select dei `topic_id` esistenti

Validazione client-side soft (warning), validazione hard server-side.
`⌘+↵` salva. Cancel o ESC per chiudere senza salvare.

### `LessonsStructureGenerateDialog.tsx`

Dialog di conferma per generate/regenerate. 4 modalità:
`generate-module | regenerate-module | generate-all | regenerate-all`.
Per "regenerate-*", textarea per `regeneration_hint` (max 2000 char).

## Configurazione

`backend/app/core/config.py` — settings dedicati Fase 2:

| Setting | Default | Descrizione |
|---|---|---|
| `openai_lesson_structure_model` | `gpt-5.5` | Modello OpenAI per Fase 2 |
| `openai_lesson_structure_max_tokens` | `16000` | Cap token completion (gpt-5.5 reasoning + ~5 lezioni × 4 sezioni) |
| `openai_lesson_structure_reasoning_effort` | `medium` | `[minimal, low, medium, high]` per gpt-5.x/o1/o3/o4; ignorato su modelli classici |
| `course_lesson_structure_poll_interval_seconds` | `4` | Intervallo polling worker |
| `course_lesson_structure_max_concurrency` | `5` | Cap moduli paralleli (semaforo) |
| `course_lesson_structure_documents_context_max_chars` | `30000` | Budget contesto documenti |
| `course_lesson_structure_auto_retry_max` | `5` | Retry trasparenti prima di transitare a `failed` |

## Audit log

Eventi emessi (vedi `services/audit_service.py`):

- `course.module.lessons_structure.generate.requested` — su trigger per modulo
- `course.lessons_structure.generate.requested` — su trigger batch
  (con `meta.modules_count`)
- `course.module.lessons_structure.generated` — su materializzazione OK
- `course.module.lessons_structure.failed` — su errore con `meta.error_message`
- `course.module.lessons_structure.approved` — su approve singolo
- `course.lessons_structure.approved` — su approve batch
- `course.lesson.structure.updated` — su edit manuale (con diff sintetico)

## Limiti noti

1. **No auto-trigger su approve di Fase 1**: l'utente decide quando avviare Fase 2
   (decisione di prodotto).
2. **Cascade invalidation futura**: cambi di `topic_id`/`section_id` invalideranno
   Fasi 3-5 una volta esistenti. Per ora solo placeholder commentato in
   `materialize_module_structure`.
3. **Rate-limit OpenAI**: il semaforo a 5 è un compromesso safe per gpt-5.5
   tier standard. Su 429 il worker auto-retry-a in trasparenza fino a
   `course_lesson_structure_auto_retry_max` (default 5); oltre, transita a
   `failed` e l'utente fa "Riprova". Per tier 2+ alza il cap (vedi
   tempi stimati in [04 — Configuration](../04-configuration.md)).
4. **Versioning storico**: ogni rigenerazione **sovrascrive** lo stato del modulo.
   `lessons_structure_raw` è snapshot dell'ultima versione.
