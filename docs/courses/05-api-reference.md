# 05 — API reference (corsi)

Reference completa degli endpoint del dominio Corsi sotto `/api/v1/orgs/{org_id}/courses`.

Convenzioni come da [api-reference globale](../api-reference.md): cookie HttpOnly,
errori `{code, message, request_id?, meta?}`, content-type JSON salvo upload.

## Lista & CRUD

### `GET /orgs/{org_id}/courses`

Lista paginata.

Query: `page`, `page_size`, `q` (titolo/obiettivi), `status`.

`course:view`. I `member` vedono solo i corsi a loro assegnati (filtro
service-side); `org_admin`/`creator` vedono tutto.

Risposta:

```json
{
  "items": [{ "id": "...", "title": "...", "status": "draft", ... }],
  "meta": { "page": 1, "page_size": 20, "total": 47 }
}
```

### `POST /orgs/{org_id}/courses`

`course:create`. Body:

```json
{
  "title": "string",
  "objectives": "string",
  "language_code": "it",
  "cfu": 6,
  "argomenti_chiave": ["..."],
  "assignee_user_id": "uuid?",
  "taxonomies": { "categoria": "uuid?", "stile_insegnamento": "uuid?", ... }
}
```

201 → `CourseOut`.

### `GET /orgs/{org_id}/courses/{course_id}`

`course:view`. Ritorna `CourseOut` con tutto il dettaglio (documents, modules,
taxonomies, architettura meta + progress).

### `PATCH /orgs/{org_id}/courses/{course_id}`

`course:edit`. Body parziale come `CourseUpdateInput`. Auto-save 1.5s debounce dal frontend.

### `PATCH /orgs/{org_id}/courses/{course_id}/assignee`

`course:assign`. Body `{assignee_user_id: uuid}`.

### `DELETE /orgs/{org_id}/courses/{course_id}`

`course:delete`. 204. Cascade su documenti, moduli, lezioni; rimuove anche file
caricati su disco.

## Documenti

### `POST /orgs/{org_id}/courses/{course_id}/documents`

`course:edit`. Multipart `file`. Limite 25 MB. Mime accettati: PDF, DOC, DOCX,
TXT, MD, RTF.

### `GET /orgs/{org_id}/courses/{course_id}/documents`

`course:view`.

### `GET /orgs/{org_id}/courses/{course_id}/documents/{doc_id}?include_summary=true`

`course:view`. `include_summary=true` esplode il `summary` JSONB nel campo
strutturato. Senza il flag, il summary è omesso (per non gonfiare la list response).

### `POST /orgs/{org_id}/courses/{course_id}/documents/{doc_id}/reprocess`

`course:edit`. 202. Reset a `pending`; il worker riprende.

### `DELETE /orgs/{org_id}/courses/{course_id}/documents/{doc_id}`

`course:edit`. 204.

## Architettura (Fase 1)

### `POST /orgs/{org_id}/courses/{course_id}/architecture/generate`

`course:generate`. Body `{regeneration_hint: string | null}` (max 2000 char).
202 → `CourseOut` con `status='architecture_pending'`. Worker prende al prossimo tick.

### `POST /orgs/{org_id}/courses/{course_id}/architecture/approve`

`course:generate`. Solo `architecture_ready` → `architecture_approved`.

## Struttura lezioni (Fase 2)

Granularità modulo. Vedi [07 — Lesson structure](07-lesson-structure.md).

### `POST /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons-structure/generate`

`course:generate`. Body `{regeneration_hint: string | null}` (max 2000 char).
202 → `CourseOut`. Set `module.lessons_structure_status='pending'`. Worker
parallelo dispatcha al prossimo tick.

Errori:
- `409 invalid_course_status` se `course.status` non è in
  `architecture_approved | lessons_structure_*`.
- `404 module_not_found` se il modulo non appartiene al corso.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-structure/generate-all`

`course:generate`. Body `{regeneration_hint: string | null}`.
202 → `CourseOut`. Set TUTTI i moduli a `pending`. Il worker parallelo
elabora con cap di concorrenza.

### `POST /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons-structure/approve`

`course:generate`. Solo se `module.lessons_structure_status='ready'`.
200 → `CourseOut`. Side-effect su `course.status` (potrebbe diventare
`lessons_structure_approved` se tutti gli altri moduli sono già `approved`).

Errori:
- `409 module_not_ready_for_approve` se lo stato non è `ready`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-structure/approve-all`

`course:generate`. Solo se TUTTI i moduli sono in `ready`.
200 → `CourseOut` con `status='lessons_structure_approved'`.

Errori:
- `409 not_all_modules_ready` se almeno un modulo non è `ready`.

### `PATCH /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/structure`

`course:edit`. Body `LessonStructureUpdateInput` (4 campi opzionali):

```json
{
  "learning_objectives": ["..."],
  "mandatory_topics": [{"topic_id": "T1", "topic": "...", "rationale": "..."}],
  "prerequisites": ["..."],
  "section_outline": [{"section_id": "S1", "title": "...", "purpose": "...", "covers_topic_ids": ["T1"]}]
}
```

200 → `CourseOut`.

Errori:
- `409 lessons_structure_not_editable` se il modulo della lezione non è in `ready/approved`.
- `422` su validazione (topic_id duplicati, sezione_id duplicati,
  covers_topic_ids invalidi).

## CRUD moduli

### `POST /orgs/{org_id}/courses/{course_id}/modules`

`course:edit`. Body `{title, description?}`. Append in fondo. 201 → `CourseOut`.

### `PATCH /orgs/{org_id}/courses/{course_id}/modules/{module_id}`

`course:edit`. Body parziale `{title?, description?}`.

### `DELETE /orgs/{org_id}/courses/{course_id}/modules/{module_id}`

`course:edit`. Cascade delete delle lezioni. Renumera codici/posizioni dei
moduli rimanenti.

### `POST /orgs/{org_id}/courses/{course_id}/modules/reorder`

`course:edit`. Body `{ids: uuid[]}` con tutti gli ID dei moduli nell'ordine
desiderato. Renumera codici e lesson_code.

### `POST /orgs/{org_id}/courses/{course_id}/modules/{module_id}/generate-lessons`

`course:generate`. Sync (~20-30s, attende OpenAI). Cancella le lezioni
esistenti del modulo e le rigenera via AI con N=`course.lessons_per_module`.

> Frontend deve usare timeout esplicito (default axios 20s è troppo basso).

## CRUD lezioni

### `POST /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons`

`course:edit`. Body:

```json
{
  "title": "string",
  "summary": "string?",
  "is_introductory": false,
  "recommended_bibliography": [{...}]
}
```

### `PATCH /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}`

`course:edit`. Body parziale (4 campi opzionali).

### `DELETE /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}`

`course:edit`. Renumera position/lesson_code delle lezioni rimanenti del modulo.

### `POST /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons/reorder`

`course:edit`. Body `{ids: uuid[]}` lezioni del modulo.

## Contenuti lezioni (Fase 3)

Granularità lezione. Vedi [08 — Lesson content](08-lesson-content.md).

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/content/generate`

`course:generate`. Body `{regeneration_hint: string | null}` (max 2000 char).
202 → `CourseOut`. Set `lesson.content_status='pending'`. Worker parallelo
(cap default 3) dispatcha al prossimo tick.

Errori:
- `409 invalid_lesson_status_for_content_generation` se la lezione non è
  in stato compatibile (richiede modulo `lessons_structure_approved`).
- `404 lesson_not_found`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-content/generate-all`

`course:generate`. Body `{regeneration_hint: string | null}`. 202 →
`CourseOut`. Set TUTTE le lezioni eligibili a `pending`. Il worker
parallelo elabora con cap di concorrenza.

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/content/approve`

`course:generate`. Solo se `lesson.content_status='ready'`. 200 →
`CourseOut`. Side-effect su `course.status`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-content/approve-all`

`course:generate`. Solo se TUTTE le lezioni esportabili sono in `ready`.
200 → `CourseOut` con `status='content_approved'`.

### `PATCH /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/content`

`course:edit`. Body `LessonContentUpdateInput` (campi opzionali del
`content_raw`: introduction, sections, summary, key_takeaways,
visual_assets, tables, equations, examples, references, coverage_check).
Validazioni allentate (solo unicità ID, no coverage hard).

200 → `CourseOut`.

Errori:
- `409 lesson_content_not_editable` se status non in `ready/approved`.
- `422` su validazione (asset_id duplicati, etc.).

## Glossario (§10.1)

### `POST /orgs/{org_id}/courses/{course_id}/glossary/regenerate`

`course:generate`. **Sync** (~10-20s, attende OpenAI). Restituisce
`CourseOut` con `glossary_status='ready'` e `glossary_raw` popolato.

Auto-trigger: il worker Fase 3 chiama internamente
`ensure_glossary_ready` al primo task del corso se
`glossary_status not in ('ready','approved')`.

## Export PDF lezioni (§7)

Granularità lezione. Vedi [09 — PDF export](09-pdf-export.md).

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/pdf/export`

`course:generate`. 202 → `CourseOut`. Set `lesson.pdf_status='pending'`.
Worker parallelo (cap default 2) dispatcha al prossimo tick.

Query param opzionale: `?pdf_template_id={uuid}` per scegliere il
template grafico (validato sull'org). Se omesso, il worker usa il
template della lezione (se già impostato) o il default dell'org.

Errori:
- `409 invalid_lesson_content_status_for_pdf` se `content_status` non in
  `{ready, approved}`.
- `409 pdf_already_in_progress` se `pdf_status` è già `pending` o
  `processing`.
- `404 pdf_template_not_found` se `pdf_template_id` non appartiene
  all'org.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-pdf/export-all`

`course:generate`. 202 → `CourseOut`. Marca TUTTE le lezioni esportabili
(`content_status` ∈ ready/approved e `pdf_status` ∈ empty/ready/failed)
come `pending`.

Query param opzionale: `?pdf_template_id={uuid}` applica lo stesso
template a tutte le lezioni esportabili (override del valore
precedente).

Errori:
- `409 no_eligible_lessons_for_pdf` se nessuna lezione è esportabile.
- `404 pdf_template_not_found` se `pdf_template_id` non appartiene
  all'org.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-pdf/cancel-all`

`course:generate`. 200 → `CourseOut`. Annulla tutti gli export in flight
(`pending`/`processing` → `failed` con `pdf_error="Export annullato"`).
Il worker post-Playwright re-controlla lo status e scarta il path se non
è più `processing`.

### `GET /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/pdf/download`

`course:view`. Restituisce `application/pdf` con
`Content-Disposition: attachment; filename="{course} — {lesson_code}
{title}.pdf"`. 404 se `pdf_status != 'ready'` (`pdf_not_ready`) o se il
file non esiste sul filesystem (`pdf_file_missing`).

---

## Errori specifici

| Code | HTTP | Quando |
|---|---|---|
| `course_not_found` | 404 | UUID inesistente o non visibile per il chiamante |
| `architecture_not_editable` | 409 | CRUD manuale fuori da `architecture_ready/approved` |
| `invalid_course_status` | 409 | Generate da status non ammesso |
| `invalid_reorder` | 422 | `ids` non corrisponde all'insieme attuale |
| `module_not_found` / `lesson_not_found` | 404 | |
| `module_lessons_generation_failed` | 422 | Errore OpenAI lato generazione lezioni |
| `lessons_structure_not_editable` | 409 | PATCH lezione fuori da modulo `ready/approved` |
| `module_not_ready_for_approve` | 409 | Approve modulo che non è in `ready` |
| `not_all_modules_ready` | 409 | Approve-all con almeno un modulo non in `ready` |
| `lessons_structure_generation_failed` | 422 | Errore OpenAI lato struttura lezioni (raro: i fail sincroni sono rari, di solito ricadono in `failed` lato modulo) |
| `openai_not_configured` | 422 | `OPENAI_API_KEY` non impostata |
| `lesson_content_not_editable` | 409 | PATCH content fuori da `ready/approved` |
| `invalid_lesson_status_for_content_generation` | 409 | Generate content da modulo non `lessons_structure_approved` |
| `lesson_content_generation_failed` | 422 | Errore OpenAI lato content (sync) |
| `glossary_generation_failed` | 422 | Errore OpenAI lato glossario |
| `invalid_lesson_content_status_for_pdf` | 409 | Export PDF da `content_status` ≠ ready/approved |
| `pdf_already_in_progress` | 409 | Export richiesto su lezione `pending`/`processing` |
| `no_eligible_lessons_for_pdf` | 409 | Export-all senza lezioni esportabili |
| `pdf_not_ready` | 404 | Download richiesto su `pdf_status` ≠ ready |
| `pdf_file_missing` | 404 | File PDF mancante sul filesystem (DB ha `pdf_path` ma il file è stato rimosso) |
| `pdf_template_not_found` | 404 | `pdf_template_id` query param non appartiene all'org del corso |
