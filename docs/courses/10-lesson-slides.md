# 10 — Lesson slides (Fase 4)

Generazione AI delle **slide della presentazione** per ogni lezione.
Pipeline parallela (cap=3 di default) con stato per-lezione su
`course_lesson.slides_status` e auto-retry trasparente. Riferimento
spec: §7 (sezione "slides") di `prompt_generazione_corsi.md`.

## Cosa fa

Per ogni lezione con `content_status ∈ {ready, approved}`, una chiamata
OpenAI produce la sequenza di slide dimensionata sui
`minuti_per_lezione` del corso. Le slide:

- **Riusano** gli asset di Fase 3 (visual_assets, tables, equations,
  examples) tramite `references_assets[]` con asset_id
- Possono creare **nuovi asset** (`new_assets[]`) quando il contenuto
  richiede una visualizzazione che non è già stata prodotta in Fase 3
- Hanno tipo classificato (16 valori: title, agenda, prerequisites,
  concept, definition, diagram, formula, table, example, case_study,
  exercise, discussion, summary, takeaways, references, bibliography)
- Hanno opzionalmente un **`body`** (prosa breve di 1-3 frasi, max 600
  char) per evitare slide tutte-bullet visivamente piatte

## Stato per-lezione

`course_lesson.slides_status` ∈
`empty → pending → processing → ready → approved | failed`.

Auto-retry trasparente prima di `failed`: se l'errore è recuperabile
(rate-limit OpenAI, validazione §7.4 fallita, materializzazione fallita)
e `slides_attempts < COURSE_LESSON_SLIDES_AUTO_RETRY_MAX` (default 5),
il worker riporta lo status a `pending` e ritenta al tick successivo.
La UI vede solo "in elaborazione" finché passa.

`course.status` (`slides_pending` / `slides_ready` / `slides_approved`)
è derivato dagli stati per-lezione (`_recompute_course_slides_status`).

## Pre-condizione

`lesson.content_status ∈ {ready, approved}` AND `lesson.content_raw` valorizzato.

Se la pre-condizione non è soddisfatta al momento del dispatch, il
worker fa un fail terminale **non recuperabile** con messaggio
"Genera prima il contenuto" — non viene ritentato.

## Flusso di generazione

```
[utente] POST /lessons/{id}/slides/generate (con hint opzionale)
  └─► course_lesson_slides_service.request_lesson_slides_generation
       ├─► validate course.status ∈ {content_ready, content_approved, slides_*}
       ├─► validate lesson.content_status ∈ {ready, approved}
       ├─► lesson.slides_status = "pending"
       ├─► lesson.slides_regeneration_hint = hint
       ├─► reset slides_pdf_status='empty' se era ready/failed (PDF obsoleto)
       ├─► _recompute_course_slides_status(course)
       └─► audit course.lesson.slides.generate.requested

[worker] course_lesson_slides_worker._tick (ogni 4s)
  └─► SELECT lessons WHERE slides_status='pending'
      ├─► claim atomico in _inflight (PRIMA del semaforo)
      └─► fire-and-forget _bound_process(lesson_id)

[worker task] _bound_process → semaphore.acquire → _process_one
  ├─► reload lesson + course (eager load completo)
  ├─► pre-check content_status (terminal fail se non ready/approved)
  ├─► lesson.slides_status = "processing", attempts++
  ├─► build_user_prompt(course, lesson) = §7.2 + §9.4 se rigenerazione
  │    (include content_raw + bibliografia + hint utente)
  ├─► progress ticker (background) ease-out 15→85%
  ├─► openai_lesson_slides_service.generate_lesson_slides(...)
  │    ├─► system prompt §7.1 + REGENERATION_SUFFIX se rigenerazione
  │    ├─► response_format json_schema strict (§7.3)
  │    └─► return (LessonSlidesOutput, usage)
  ├─► cancel-check (re-leggi slides_status — utente potrebbe aver cancellato)
  ├─► materialize_lesson_slides (validazioni §7.4)
  │    1. lesson_id == lesson_code
  │    2. total_slides == len(slides)
  │    3. slide_number sequenziali 1..N
  │    4. slide_id univoci
  │    5. total_slides nel range atteso per minuti_per_lezione (±20%)
  │    6. references_assets risolvibili (Fase 3 ∪ new_assets)
  │    7. source_section_id esiste in Fase 3 (se non vuoto)
  │    8. ogni section è referenziata da almeno una slide (soft warning)
  ├─► lesson.slides_raw = output
  ├─► lesson.slides_tokens = usage
  ├─► lesson.slides_status = "ready", progress = 100
  ├─► _recompute_course_slides_status(course)
  └─► audit course.lesson.slides.generated
```

In caso di errore recuperabile, `_apply_failure(recoverable=True)`
riporta a `pending` finché `attempts < auto_retry_max`. Errori non
recuperabili (`OpenAINotConfiguredError`, pre-check content) sono
terminal subito.

## OpenAI service — `openai_lesson_slides_service.py`

System prompt (§7.1) tradotto fedelmente dalla spec con regole su:
1. **Riuso asset**: referenzia per ID, niente duplicati
2. **Nuovi asset**: solo se necessario, prefisso `*_new_*`
3. **Numero slide**: range indicativo per durata (15min→6-10, 30min→12-15, ...)
4. **Struttura standard**: title + agenda + prerequisites? + sviluppo + summary + takeaways + references
5. **Contenuto per slide**: title ≤8 parole, body 1-3 frasi opzionale, bullets 0-6 max ~14 parole
6. **Tipi slide**: 16 enum
7. **Caso speciale lezione introduttiva**: bibliografia + benvenuto

Regeneration suffix (§9.4):
> ATTENZIONE: stai RIGENERANDO le slide di una lezione già slidificata.
> Considera la versione precedente e il feedback del docente.
> Mantieni gli stessi asset_id già presenti in Fase 3.
> Se possibile, mantieni lo stesso slide_id per slide che corrispondono
> semanticamente alla versione precedente (utile per riusare il discorso
> esistente nella futura Fase 5).

Settings env-driven:

| Env | Default | Significato |
|---|---|---|
| `OPENAI_LESSON_SLIDES_MODEL` | `gpt-5.5` | Modello reasoning per Fase 4 |
| `OPENAI_LESSON_SLIDES_MAX_TOKENS` | `16000` | `max_completion_tokens` (output ~4-8k + reasoning) |
| `OPENAI_LESSON_SLIDES_REASONING_EFFORT` | `medium` | `minimal/low/medium/high` |
| `COURSE_LESSON_SLIDES_POLL_INTERVAL_SECONDS` | `4` | Tick worker |
| `COURSE_LESSON_SLIDES_MAX_CONCURRENCY` | `3` | Lezioni in parallelo |
| `COURSE_LESSON_SLIDES_AUTO_RETRY_MAX` | `5` | Tentativi prima di fail terminale |

## Schema output (§7.3)

```json
{
  "lesson_id": "M1.L4",
  "total_slides": 12,
  "slides": [
    {
      "slide_number": 1,
      "slide_id": "S01",
      "type": "title",
      "title": "Algoritmi non supervisionati",
      "body": "In questa lezione introduciamo le tecniche di clustering...",
      "bullets": [],
      "references_assets": [],
      "source_section_id": ""
    },
    {
      "slide_number": 5,
      "slide_id": "S05",
      "type": "diagram",
      "title": "Pipeline k-means",
      "body": "",
      "bullets": [],
      "references_assets": ["fig_kmeans_flow"],
      "source_section_id": "S2"
    }
  ],
  "new_assets": [
    {
      "asset_id": "fig_new_recap",
      "format": "mermaid",
      "content": "graph LR\nIntro --> Body --> Summary",
      "caption": "Mappa concettuale di sintesi",
      "alt_text": "Flusso lineare a tre step"
    }
  ]
}
```

## Body field (no slide tutte-bullet)

A seguito del feedback utente sulle slide visivamente piatte, ogni slide ha un campo opzionale `body` (prosa breve di 1-3 frasi, max 600 char) che il prompt suggerisce di alternare con i bullet:

| Tipo slide | Body | Bullets |
|---|---|---|
| `title` | 1 frase (sottotitolo) | nessuno |
| `concept`/`definition` | 2-3 frasi | 0-3 di esempio |
| `agenda`/`takeaways` | vuoto | 3-6 |
| `summary` | 1-2 frasi conclusive | opzionali |

Le slide pure-bullet sono ancora supportate (basta lasciare `body` vuoto).

## CRUD manuale — `course_lesson_slides_crud.py`

Edit del `slides_raw` finché la lezione è in `ready`/`approved`. Edit
non degrada lo stato (`approved` resta `approved`). Hard fail solo per:

- `slide_id` duplicati o vuoti
- `slide_number` non sequenziali 1..N
- `new_asset_id` duplicati o vuoti
- `references_assets` verso ID non risolvibili (in `content_raw` ∪ `new_assets`)
- `source_section_id` non vuoto verso sezione Fase 3 inesistente

`PATCH /lessons/{id}/slides` setta `slides_modified_at = now()` per
stale-detection downstream (PDF slide e Fase 5 si segnaleranno stale).

## Frontend — `CourseLessonSlidesView.tsx`

Tab "Slide" (settimo tab del wizard). Visibile in `mode === "edit"` da
`course.status` ∈ `{content_ready, content_approved, slides_pending,
slides_ready, slides_approved, ...}`.

Componenti:
- **Header**: aggregate progress + ETA via `useBatchEta`, CTA batch
  (Genera tutto / Rigenera / Genera mancanti / Approva tutto / Annulla,
  + Esporta PDF tutto)
- **Module card** per ciascun modulo, con lista lezioni
- **Lesson row** espandibile:
  - status badge + primary CTA (Genera → Approva → Modifica)
  - kebab menu (Rigenera, Rigenera PDF)
  - progress live + phase
  - `<StalenessAlert kind="slides">` quando `isSlidesStale === true`
  - `<ApprovalBadge level="lessonSlides">` quando approved
  - Expanded: `<LessonSlidesView slides={slides_raw} contentRaw={content_raw} />`
- **Dialogs**: `LessonSlidesGenerateDialog` (4 modes con hint),
  `LessonSlidesEditDialog` (editor verticale slide + new_assets),
  `LessonSlidesPdfExportDialog` (selettore template `slide_templates`)

## File rilevanti

```
backend/app/services/openai_lesson_slides_service.py   # OpenAI call + JSON schema + REGENERATION_SUFFIX
backend/app/services/course_lesson_slides_worker.py    # worker async + auto-retry + atomic claim _inflight
backend/app/services/course_lesson_slides_service.py   # orchestrazione + materialize + 8 validazioni §7.4
backend/app/services/course_lesson_slides_crud.py      # PATCH manuale + validazioni allentate
backend/app/schemas/course_lesson_slides.py            # LessonSlidesOutput + LessonSlideItem + LessonSlideNewAsset
backend/app/api/v1/courses.py                          # 7 endpoint Fase 4 (generate / generate-all / generate-missing / cancel-all / approve / approve-all / patch)
frontend/src/api/courses.ts                            # coursesApi.lessonSlides + tipi
frontend/src/pages/org/courses/components/
  ├── CourseLessonSlidesView.tsx                       # vista batch + per-lezione
  ├── LessonSlidesView.tsx                             # render read-only (card per slide)
  ├── LessonSlidesEditDialog.tsx                       # editor manuale
  └── LessonSlidesGenerateDialog.tsx                   # dialog generate/regenerate (4 modes)
```

## Errori comuni

Vedi tabella completa in [05 — API reference](05-api-reference.md). Più
frequenti:

- `lesson_slides_count_out_of_range` — il modello AI ha generato troppe/troppo poche slide. Risolvere con `regeneration_hint` esplicito sul numero.
- `lesson_slides_unknown_asset_ref` — `references_assets[i]` punta a un asset non risolvibile. Quasi sempre causato da edit manuale post-AI che ha rimosso un asset. Aggiungere il `new_assets[]` o rimuovere il riferimento.
- `lesson_content_not_ready_for_slides` — la lezione non ha contenuto generato/approvato; tornare a Fase 3.
- `OpenAILessonSlidesError` con finish_reason=length — output troncato, alzare `OPENAI_LESSON_SLIDES_MAX_TOKENS`.
