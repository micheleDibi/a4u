# 05 — API reference (corsi)

Reference completa degli endpoint del dominio Corsi sotto `/api/v1/orgs/{org_id}/courses`.

Convenzioni come da [api-reference globale](../api-reference.md): cookie HttpOnly,
errori `{code, message, request_id?, meta?}`, content-type JSON salvo upload.

## Lista & CRUD

### `GET /orgs/{org_id}/courses`

Lista paginata + filtri + ordinamento + indicatori di completezza
pipeline per ogni corso.

`course:view`. I `member` vedono solo i corsi a loro assegnati (filtro
service-side); chi ha `course:view_all` (org_admin / creator / platform
admin) vede tutto.

Query params (tutti opzionali):

| Param | Tipo | Default | Note |
|---|---|---|---|
| `page` | int ≥ 1 | 1 | paginazione 1-based |
| `page_size` | int 1..200 | 20 | |
| `q` | str ≤ 200 | — | ricerca ILIKE su `title` + `objectives` |
| `status` | `CourseStatus` | — | uno dei 17 valori (vedi `01-data-model`) |
| `assignee_user_id` | UUID | — | filtra per docente assegnato |
| `language_code` | str ≤ 10 | — | filtra per lingua corso |
| `created_after` | ISO datetime | — | inclusivo `created_at ≥` |
| `created_before` | ISO datetime | — | inclusivo `created_at ≤` |
| `updated_after` | ISO datetime | — | inclusivo `updated_at ≥` |
| `updated_before` | ISO datetime | — | inclusivo `updated_at ≤` |
| `sort_by` | `created_at` \| `updated_at` | `updated_at` | colonna di ordinamento |
| `sort_dir` | `asc` \| `desc` | `desc` | direzione |

Tie-break su `id` per stabilità della paginazione.

Risposta (`Page[CourseListItemOut]`):

```json
{
  "items": [{
    "id": "...",
    "title": "...",
    "status": "content_ready",
    "language_code": "it",
    "assignee": { "id": "...", "email": "...", "full_name": "..." },
    "modules_count": 5,
    "cfu": 6,
    "updated_at": "2026-05-12T14:23:00Z",
    "created_at": "2026-05-01T09:00:00Z",
    "lessons_progress": {
      "total": 8,            // lezioni didattiche (esclude is_assessment)
      "content_ready": 5,    // content_status in ('ready','approved')
      "slides_ready": 3,     // slides_status in ('ready','approved')
      "videos_ready": 0,     // video_status == 'ready'
      "avatar_videos_ready": 0  // avatar_video_status == 'ready'
    }
  }],
  "meta": { "page": 1, "page_size": 20, "total": 47 }
}
```

`lessons_progress` è calcolato da una seconda query aggregata
(`COUNT(*) FILTER (WHERE …)` Postgres) sui soli `course_id` della
pagina corrente. Se il corso non ha lezioni didattiche `total` è 0 e i
contatori sono tutti 0 (la UI lo visualizza con "—").

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

## Duplicazione corso in altra lingua

Vedi [15 — Duplicazione corso in altra lingua](15-course-duplication.md) per
il design completo (pipeline worker, traduzione, embed nella lista).

### `POST /orgs/{org_id}/courses/{course_id}/duplicate?target_language_code=X`

`course:duplicate`. 202. Crea un job `pending`; il worker
`course_duplication_worker` lo prende in carico, clona la shell del
corso target e traduce via OpenAI tutti i contenuti.

Query: `target_language_code` obbligatorio (codice ISO, deve essere
diverso dalla lingua corrente del corso e corrispondere a una
`Language` attiva in DB).

Response: `CourseDuplicationJobOut` con `id`, `status="pending"`,
`progress=0`, `progress_phase=null`, `progress_detail=null`,
`target_course_id=null` (popolato dopo la phase `cloning_structure`).

Schema completo `CourseDuplicationJobOut`: `id`, `source_course_id`,
`target_course_id`, `target_language_code`, `status`, `progress`,
`progress_phase`, `progress_detail`, `error`, `attempts`, `tokens`,
`requested_by_user_id`, `started_at`, `finished_at`, `created_at`,
`updated_at`.

Schema compatto embedded in `CourseListItemOut.duplication_job`:
`id`, `source_course_id`, `target_course_id`, `target_language_code`,
`status`, `progress`, `progress_phase`, `progress_detail`, `started_at`.
`started_at` è esposto nel Compact (oltre che nel full) per
permettere al FE di calcolare l'ETA stimato nel badge.

Errori:
- `409 duplicate_same_language` — lingua target uguale alla lingua del
  corso sorgente.
- `409 duplicate_already_in_progress` — esiste già un job
  `pending`/`processing` per la stessa coppia `(source, target_lang)`.
- `404 language_not_available` — la lingua non esiste in DB o non è
  attiva.

### `GET /orgs/{org_id}/courses/{course_id}/duplications`

`course:view`. Lista tutti i job (qualsiasi stato) in cui il corso è
source O target. Ordinato per `created_at` DESC. Usato dalla UI per
verifiche puntuali; in generale la lista corsi embed il job attivo
direttamente in `CourseListItemOut.duplication_job` (con polling 3s
quando ci sono job attivi nella pagina).

### `POST /orgs/{org_id}/duplication-jobs/{job_id}/cancel`

`course:duplicate`. Cancel idempotente di un job
`pending`/`processing` → `failed` con error "Annullata dall'utente".
Il corso target eventualmente già creato resta in DB con i contenuti
parzialmente tradotti (l'utente lo elimina manualmente o
ri-richiede la duplicazione). **Nota**: su fail TERMINALE del job
(5 retry esauriti o timeout 90 min) il target viene invece eliminato
automaticamente con cascade — vedi [15 — Duplicazione corso](15-course-duplication.md#cleanup-automatico-target).

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

### `PATCH /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/assessment`

`course:edit`. Patch manuale della **verifica delle competenze** — il
`content_raw` di una lezione `is_assessment`. Vedi
[14 — Assessment lesson](14-assessment-lesson.md).

Body `LessonAssessmentUpdateInput` (liste opzionali):

```json
{
  "multiple_choice_questions": [
    {
      "question_id": "q-1a2b3c4d",
      "text": "...",
      "options": [{"option_id": "A", "text": "..."}, {"option_id": "B", "text": "..."}],
      "correct_option_id": "A"
    }
  ],
  "open_questions": [
    {"question_id": "q-5e6f7a8b", "text": "...", "expected_answer": "..."}
  ]
}
```

200 → `CourseOut`. Guard `lesson.is_assessment` + status `ready`/`approved`.
Stesse validazioni MC della materializzazione AI (`question_id` unici,
ogni MC con esattamente una opzione corretta, `correct_option_id`
referenzia un'opzione esistente).

Errori:
- `409 lesson_not_assessment` se la lezione non è una verifica.
- `409 lesson_content_not_editable` se status non in `ready/approved`.
- `422` su validazione (MC con 0 o 2 opzioni corrette, ID duplicati).

## Glossario (§10.1)

### `POST /orgs/{org_id}/courses/{course_id}/glossary/regenerate`

`course:generate`. **Sync** (~10-20s, attende OpenAI). Restituisce
`CourseOut` con `glossary_status='ready'` e `glossary_raw` popolato.

Auto-trigger: il worker Fase 3 chiama internamente
`ensure_glossary_ready` al primo task del corso se
`glossary_status not in ('ready','approved')`.

## Slide della lezione (Fase 4)

Granularità lezione. Vedi [10 — Lesson slides](10-lesson-slides.md).

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/slides/generate`

`course:generate`. Body `{regeneration_hint: string | null}` (max 2000 char).
202 → `CourseOut`. Set `lesson.slides_status='pending'`. Worker parallelo
(cap default 3) dispatcha al prossimo tick.

Pre-condizione: `lesson.content_status ∈ {ready, approved}` (servono le slide
hanno bisogno di `content_raw` come input).

**Side-effect**: se la lezione aveva un `slides_pdf_status` in `ready/failed`,
viene resettato a `empty` (il PDF slide diventa obsoleto).

Errori:
- `409 invalid_course_status_for_slides` se `course.status` non ammette Fase 4.
- `409 lesson_content_not_ready_for_slides` se la lezione non ha contenuto pronto.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-slides/generate-all`

`course:generate`. Body `{regeneration_hint: string | null}`. 202 → `CourseOut`.
Marca tutte le lezioni con `content_status ∈ {ready, approved}` come `pending`.
Reset `slides_pdf_status='empty'` per tutte.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-slides/generate-missing`

`course:generate`. 202 → `CourseOut`. Marca SOLO le lezioni con
`slides_status='empty'` AND `content_status ∈ {ready, approved}`. Utile dopo
aggiunta di una nuova lezione manuale.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-slides/cancel-all`

`course:generate`. 200 → `CourseOut`. Annulla tutte le generazioni `pending|processing`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/slides/approve`

`course:generate`. Solo se `lesson.slides_status='ready'`. 200 → `CourseOut`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-slides/approve-all`

`course:generate`. Approva tutte le lezioni `ready`. 200 → `CourseOut` con
`status='slides_approved'` se tutte sono diventate approved.

### `PATCH /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/slides`

`course:edit`. Body `LessonSlidesUpdateInput` (campi opzionali):

```json
{
  "slides": [{"slide_number": 1, "slide_id": "S01", "type": "title", "title": "...", "body": "...", "bullets": [], "references_assets": [], "source_section_id": ""}],
  "new_assets": [{"asset_id": "fig_new_1", "format": "mermaid", "content": "...", "caption": "...", "alt_text": "..."}]
}
```

200 → `CourseOut`. Set `lesson.slides_modified_at = now()`.

Errori:
- `409 lesson_slides_not_editable` se status non in `ready/approved`.
- `422` su validazione (slide_id duplicati, slide_number non sequenziali, references_assets verso ID inesistenti).

## Discorso temporizzato (Fase 5)

Granularità lezione. Vedi [11 — Lesson speech](11-lesson-speech.md).

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/speech/generate`

`course:generate`. Body `{regeneration_hint: string | null}` (max 2000 char).
202 → `CourseOut`. Set `lesson.speech_status='pending'`. Worker parallelo
(cap default 3) dispatcha al prossimo tick.

Pre-condizione: `lesson.slides_status ∈ {ready, approved}` (servono le slide
come input alla generazione del discorso).

**Side-effect**: reset `speech_pdf_status='empty'` (PDF obsoleto).

Errori:
- `409 invalid_course_status_for_speech` se `course.status` non ammette Fase 5.
- `409 lesson_slides_not_ready_for_speech` se la lezione non ha slide pronte.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-speech/generate-all`

`course:generate`. Body `{regeneration_hint: string | null}`. 202 → `CourseOut`.
Marca tutte le lezioni con `slides_status ∈ {ready, approved}` come `pending`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-speech/generate-missing`

`course:generate`. 202 → `CourseOut`. Marca SOLO le lezioni con
`speech_status='empty'` AND `slides_status ∈ {ready, approved}`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-speech/cancel-all`

`course:generate`. 200 → `CourseOut`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/speech/approve`

`course:generate`. Solo se `lesson.speech_status='ready'`. 200 → `CourseOut`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-speech/approve-all`

`course:generate`. 200 → `CourseOut` con `status='speech_approved'` se applicabile.

### `PATCH /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/speech`

`course:edit`. Body `LessonSpeechUpdateInput`:

```json
{
  "speech_segments": [
    {
      "segment_id": "SEG001",
      "slide_id": "S01",
      "text": "Benvenuti, in questa lezione...",
      "estimated_duration_seconds": 25,
      "delivery_notes": "Tono caloroso."
    }
  ],
  "slide_to_segments_map": [
    {
      "slide_id": "S01",
      "segment_ids": ["SEG001"],
      "slide_total_duration_seconds": 25
    }
  ]
}
```

Validazioni server-side (8 regole §8.5, vedi [11 — Lesson speech](11-lesson-speech.md)):
1. ogni `slide_id` esiste in `slides_raw.slides`
2. ogni slide ha almeno un segmento
3. `segment_id` univoci
4. `sum(estimated_duration_seconds) ∈ [target × 0.95, target × 1.05]`
5. word count coerente con duration × wpm (130 IT / 150 EN) ±15% (soft warning)
6. `slide_to_segments_map` coerente
7. **TTS-safety**: testo segmento privo di caratteri proibiti (`*`, `_`, `` ` ``, `#`, `\`, `$`), abbreviazioni (`es.`, `etc.`, ...), comandi LaTeX
8. durate per slide quadrate alla somma segmenti

200 → `CourseOut`. Set `lesson.speech_modified_at = now()`.

Errori:
- `409 lesson_speech_not_editable` se status non in `ready/approved`.
- `422 lesson_speech_tts_unsafe` se il testo viola TTS-safety.
- `422 lesson_speech_duration_out_of_range` se durata totale fuori ±5%.
- `422 lesson_speech_uncovered_slides` se almeno una slide non ha segmenti.
- `422 lesson_speech_map_*` su inconsistenze nel `slide_to_segments_map`.

## Lesson assets (upload immagini + image→Mermaid)

Endpoint complementari all'editor contenuti (Fase 3) per gestire gli
asset visivi del nuovo flusso (commit `92d5f37`): l'utente carica
un'immagine come asset, decide se mantenerla così com'è (`format=image`)
o digitalizzarla in codice Mermaid via OpenAI Vision (`format=mermaid`).
Vedi anche [08 — Lesson content](08-lesson-content.md#asset-visivi-mermaid--immagini-caricate).

### `POST /orgs/{org_id}/courses/{course_id}/lesson-assets/upload`

`course:edit`. Multipart `file` (campo unico). 201 →
`{ "path": "lesson_assets/{course_id}/{uuid}.{ext}", "url": "/uploads/lesson_assets/{course_id}/{uuid}.{ext}" }`.

- Pipeline: validazione MIME (`image/jpeg|image/png|image/webp`) +
  size (≤ `upload_max_mb`, default 5 MB) + ri-encoding via Pillow (strip
  metadata EXIF, resize a 2400px max-dimension) → salvataggio in
  `{UPLOAD_ROOT}/lesson_assets/{course_id}/{uuid}.{ext}`. Riusa
  `file_service.save_upload_image` (vedi
  [`file_service.py:81`](../../backend/app/services/file_service.py)).
- L'asset NON viene scritto nel `content_raw` dal backend: il frontend
  riceve `path` e lo inserisce nel suo stato locale come
  `visual_assets[*]` con `format="image"` + `content=path`. Il salvataggio
  avviene poi al PATCH normale del `content_raw`.

Errori:
- `422 invalid_mime`, `422 file_too_large`, `422 invalid_image`,
  `422 empty_file`, `422 invalid_subdir` (dal layer file_service).

### `POST /orgs/{org_id}/courses/{course_id}/lesson-assets/convert-to-mermaid`

`course:edit`. Body JSON `{ "path": "lesson_assets/{course_id}/{uuid}.png" }`.
200 → `{ "mermaid_code": "flowchart TD\n  A --> B\n  ...", "usage": {...} }`.

- Carica il file dal filesystem (verificando che il path sia sotto
  `lesson_assets/{course_id}/` — niente cross-tenant leak), lo encoda in
  base64 e chiama OpenAI Vision via
  `openai_image_to_mermaid_service.convert_image_to_mermaid`.
- Modello: `settings.openai_image_to_mermaid_model` (default `gpt-4o`).
- Validazione Mermaid superficiale: il codice deve iniziare con una
  keyword nota (`flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|journey|mindmap|timeline|...`).
  La validazione semantica vera avviene sul frontend tramite la live
  preview dell'editor Mermaid.
- L'`usage` ritornato (token + costo USD via
  `openai_pricing.build_usage_dict`) è solo informativo: il backend NON
  lo persiste (la conversione è on-demand, non parte di una pipeline batch).
- L'immagine originale NON viene cancellata qui — sarà rimossa dal
  cleanup orfani al successivo PATCH `content_raw` se l'asset viene
  modificato da `format=image` a `format=mermaid`.

Errori:
- `404 lesson_asset_not_in_course` — path fuori dal subdir
  `lesson_assets/{course_id}/` (cross-tenant safety).
- `404 invalid_lesson_asset_path` — path malformato (`..`, doppi slash).
- `404 lesson_asset_file_missing` — file non più sul filesystem.
- `409 lesson_asset_unsupported_ext` — estensione non in `{png, jpg, jpeg, webp}`.
- `409 openai_not_configured` — `OPENAI_API_KEY` mancante.
- `409 image_to_mermaid_failed` — modello ha risposto `UNRECOGNIZED`
  (immagine senza schema riconoscibile), output non-Mermaid, errore HTTP
  da OpenAI, o response in formato inatteso. `meta.message` contiene un
  testo localizzato per la UI.

## Export PDF lezione testo (§7)

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

### `GET /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons-pdf/download-merged`

`course:view`. Bundle batch per-modulo: un singolo PDF concatenato di
tutte le lezioni del modulo, in ordine di `lesson_code`. Costruito con
`pypdf.PdfWriter.append()` (preserva metadati, font, immagini incorporate
e segnalibri di partenza di ogni PDF lezione). Filename:
`"{course} — {module_code} {module_title} (Contenuti).pdf"`.

Pre-condizione: TUTTE le lezioni del modulo devono avere
`pdf_status='ready'` con file presente sul filesystem. Errori:
- `409 module_has_no_lessons` — modulo senza lezioni.
- `409 module_pdfs_not_ready` — almeno una lezione non in `ready`
  (con `meta.missing_lessons = [lesson_id, ...]`).
- `404 module_pdf_file_missing` — file mancante sul FS per una lezione.

### `GET /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons-pdf/download-zip`

`course:view`. Stessa pre-condizione del merged. Restituisce uno
`application/zip` (`ZIP_DEFLATED`) con UN PDF per lezione, filename
identico al download per-lezione singola (così se l'utente esegue
l'unzip ottiene file ordinati e nominati come si aspetta). Filename
dello zip: `"{course} — {module_code} {module_title} (Contenuti).zip"`.
Stessi codici d'errore del merged.

## Export PDF slide (Fase 4)

Pipeline parallela e indipendente dal PDF testo. Path file:
`{org}/{course}/{lesson}_slides.pdf`. Template: `slide_templates`
(unificato con avatar video — migration 0022).

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/slides-pdf/export`

`course:generate`. 202 → `CourseOut`. Pre-condizione:
`lesson.slides_status ∈ {ready, approved}`. Query opzionale
`?pdf_template_id={uuid}` (validato come `slide_template`, NON
`pdf_template`).

### `POST /orgs/{org_id}/courses/{course_id}/lessons-slides-pdf/export-all`

`course:generate`. 202 → `CourseOut`. Marca tutte le lezioni con slide
ready/approved come `slides_pdf_status='pending'`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-slides-pdf/cancel-all`

`course:generate`. 200 → `CourseOut`.

### `GET /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/slides-pdf/download`

`course:view`. Restituisce `application/pdf` con filename
`"{course} — {lesson_code} {title} (slide).pdf"`. 404 se
`slides_pdf_status != 'ready'` (`slides_pdf_not_ready`) o file mancante
(`slides_pdf_file_missing`).

### `GET /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons-slides-pdf/download-merged`

`course:view`. Equivalente per la pipeline slide: un solo PDF concatenato
di tutte le slide del modulo. Pre-condizione:
`slides_pdf_status='ready'` su tutte le lezioni. Filename:
`"{course} — {module_code} {module_title} (Slide).pdf"`. Stessi codici
d'errore del merged contenuti.

### `GET /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons-slides-pdf/download-zip`

`course:view`. ZIP delle slide per ogni lezione. Filename:
`"{course} — {module_code} {module_title} (Slide).zip"`.

## Export PDF discorso (Fase 5)

Pipeline parallela e indipendente dal PDF slide. Path file:
`{org}/{course}/{lesson}_speech.pdf`. Template: `pdf_templates`
(stesso del PDF lezione testo — A4 portrait, single-column block-flow).

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/speech-pdf/export`

`course:generate`. 202 → `CourseOut`. Pre-condizione:
`lesson.speech_status ∈ {ready, approved}`. Query opzionale
`?pdf_template_id={uuid}` (validato come `pdf_template`, kind=lesson).

### `POST /orgs/{org_id}/courses/{course_id}/lessons-speech-pdf/export-all`

`course:generate`. 202 → `CourseOut`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-speech-pdf/cancel-all`

`course:generate`. 200 → `CourseOut`.

### `GET /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/speech-pdf/download`

`course:view`. Restituisce `application/pdf` con filename
`"{course} — {lesson_code} {title} (discorso).pdf"`. 404 se
`speech_pdf_status != 'ready'` (`speech_pdf_not_ready`) o file mancante
(`speech_pdf_file_missing`).

### `GET /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons-speech-pdf/download-merged`

`course:view`. Equivalente per la pipeline discorso. Pre-condizione:
`speech_pdf_status='ready'` su tutte le lezioni. Filename:
`"{course} — {module_code} {module_title} (Discorso).pdf"`.

### `GET /orgs/{org_id}/courses/{course_id}/modules/{module_id}/lessons-speech-pdf/download-zip`

`course:view`. ZIP del discorso per ogni lezione. Filename:
`"{course} — {module_code} {module_title} (Discorso).zip"`.

> I 6 endpoint batch per-modulo (merged + zip × 3 pipeline) sono
> implementati in `course_module_pdf_service.py` con due primitive
> condivise: `merge_module_pdfs(...)` (pypdf) e `zip_module_pdfs(...)`
> (stdlib `zipfile`). Il `kind` (`content`/`slides`/`speech`) seleziona
> quale `*_pdf_status`/`*_pdf_path` leggere, ma il resolver del path
> assoluto è unico (`course_lesson_pdf_service.pdf_absolute_path`)
> perché tutti e tre i PDF condividono la stessa root.

## Video MP4 della lezione (Fase 6)

Granularità lezione. Pipeline async (TTS RunPod GPU + slide PNG +
ffmpeg), nessuna chiamata OpenAI. Vedi
[12 — Lesson video](12-lesson-video.md). I 6 endpoint sono in
`courses.py` (sezione "Fase 6"), service `course_lesson_video_service.py`.

Le **lezioni di verifica** (`is_assessment`) non sono mai eleggibili.

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/video/generate`

`course:generate`. Body `LessonVideoGenerateInput` (oggetto vuoto,
`extra="forbid"`; riservato a future opzioni preset). 202 →
`LessonVideoStatusOut`. Set `lesson.video_status='pending'`; il worker
prende al prossimo tick.

Pre-condizioni: `speech_status='approved'` AND `slides_status='approved'`
AND `Avatar.audio_path` dell'assegnatario del corso presente.

Errori:
- `409 speech_not_approved` se il discorso non è approvato.
- `409 slides_not_approved` se le slide non sono approvate.
- `409 voice_sample_missing` se l'avatar dell'assegnatario non ha il
  campione vocale.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-video/generate-batch`

`course:generate`. 202 → `LessonVideoBatchOut`. Marca come `pending`
tutte le lezioni eleggibili (speech+slides approved AND video non già in
flight). Il worker le elabora una alla volta (cap default 1).

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/video/cancel`

`course:generate`. 200 → `LessonVideoStatusOut`. `pending`/`processing`
→ `cancelled`. Idempotente.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-video/cancel-batch`

`course:generate`. 200 → `LessonVideoBatchOut`. Annulla tutte le
generazioni video in flight.

### `GET /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/video/status`

`course:view`. 200 → `LessonVideoStatusOut`. Polling-friendly: il FE
rinfresca ogni 2 s mentre c'è almeno un job in flight.

### `GET /orgs/{org_id}/courses/{course_id}/lessons-video/status`

`course:view`. 200 → `LessonVideoBatchOut` — aggregato pagina-corso
(`items`, contatori, `eligible_count`, `aggregate_progress`) pronto per
la scheda "Video".

## Video con avatar (Fase 6b)

Granularità lezione. Sovrappone l'avatar parlante (lip-sync MuseTalk su
RunPod) al video MP4 già generato. Vedi
[13 — Avatar video](13-avatar-video.md). I 6 endpoint sono in
`courses.py` (sezione "Fase 6b"), service
`course_lesson_avatar_video_service.py`.

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/avatar-video/generate`

`course:generate`. Body `LessonAvatarVideoGenerateInput` (oggetto vuoto,
`extra="forbid"`). 202 → `LessonAvatarVideoStatusOut`. Set
`lesson.avatar_video_status='pending'`.

Pre-condizioni: `video_status='ready'` (il video MP4 della lezione deve
esistere) AND l'avatar dell'assegnatario ha ≥ 1 clip MiniMax pronta.

Errori:
- `409 lesson_video_not_ready` se il video della lezione non è `ready`.
- `409 avatar_clips_not_ready` se l'avatar dell'assegnatario non ha clip
  pronte.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-avatar-video/generate-batch`

`course:generate`. 202 → `LessonAvatarVideoBatchOut`. Marca come
`pending` tutte le lezioni eleggibili (video della lezione `ready` AND
avatar con clip pronte AND non già in flight). Worker uno alla volta.

### `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/avatar-video/cancel`

`course:generate`. 200 → `LessonAvatarVideoStatusOut`. `pending`/
`processing` → `cancelled`. Idempotente.

### `POST /orgs/{org_id}/courses/{course_id}/lessons-avatar-video/cancel-batch`

`course:generate`. 200 → `LessonAvatarVideoBatchOut`. Annulla tutte le
generazioni in flight.

### `GET /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/avatar-video/status`

`course:view`. 200 → `LessonAvatarVideoStatusOut`. Polling-friendly.

### `GET /orgs/{org_id}/courses/{course_id}/lessons-avatar-video/status`

`course:view`. 200 → `LessonAvatarVideoBatchOut` — aggregato
pagina-corso per la scheda "Video con avatar" (`items`, contatori,
`eligible_count`, `aggregate_progress`, `avatar_clips_ready`).

---

## Parametri MuseTalk dell'avatar

### `PATCH /me/avatar/musetalk-params`

Endpoint sotto `/api/v1/me/avatar` (router `me_avatar.py`), **non**
sotto `/orgs/.../courses`. Aggiorna i tre parametri MuseTalk per-avatar
usati dalla generazione del "Video con avatar" delle lezioni (Fase 6b).
Solo autenticazione utente (agisce sull'avatar dell'utente corrente),
nessun permesso org.

Body `AvatarMusetalkParamsUpdate` (tutti obbligatori, la UI invia
sempre i tre valori):

```json
{
  "musetalk_extra_margin": 15,
  "musetalk_left_cheek_width": 110,
  "musetalk_right_cheek_width": 110
}
```

Range: `musetalk_extra_margin` 0..200, `musetalk_left_cheek_width` e
`musetalk_right_cheek_width` 0..400. 200 → `AvatarOut`.

Errori:
- `404 avatar_not_found` se l'utente non ha un avatar.

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
| `slide_template_not_found` | 404 | `pdf_template_id` query param (per slide PDF) non appartiene all'org come `slide_template` |
| `invalid_course_status_for_slides` | 409 | Generate slide da course.status non ammesso (Fase 4) |
| `lesson_content_not_ready_for_slides` | 409 | Generate slide su lezione senza content ready/approved |
| `lesson_slides_not_editable` | 409 | PATCH slide fuori da `ready/approved` |
| `lesson_slides_not_ready` | 409 | Approve slide su lezione non in `ready` |
| `lesson_slides_id_mismatch` | 422 | Output AI ha `lesson_id` diverso dal `lesson_code` atteso |
| `lesson_slides_total_mismatch` | 422 | `total_slides ≠ len(slides)` |
| `lesson_slides_nonsequential` | 422 | `slide_number` non sequenziali 1..N |
| `lesson_slides_duplicate_slide_id` | 422 | `slide_id` duplicati |
| `lesson_slides_duplicate_new_asset_id` | 422 | `new_asset_id` duplicati |
| `lesson_slides_unknown_asset_ref` | 422 | `references_assets` punta a ID non risolvibile |
| `lesson_slides_unknown_source_section` | 422 | `source_section_id` non esiste in Fase 3 |
| `lesson_slides_count_out_of_range` | 422 | total_slides molto fuori range atteso per durata lezione |
| `slides_pdf_already_in_progress` | 409 | Export PDF slide già in corso |
| `invalid_lesson_slides_status_for_pdf` | 409 | Export PDF slide da lezione senza slide ready/approved |
| `no_eligible_lessons_for_slides_pdf` | 409 | Export-all PDF slide senza lezioni esportabili |
| `slides_pdf_not_ready` | 404 | Download PDF slide su `slides_pdf_status` ≠ ready |
| `slides_pdf_file_missing` | 404 | File PDF slide mancante |
| `invalid_course_status_for_speech` | 409 | Generate discorso da course.status non ammesso (Fase 5) |
| `lesson_slides_not_ready_for_speech` | 409 | Generate discorso su lezione senza slide ready/approved |
| `lesson_speech_not_editable` | 409 | PATCH discorso fuori da `ready/approved` |
| `lesson_speech_not_ready` | 409 | Approve discorso su lezione non in `ready` |
| `lesson_speech_id_mismatch` | 422 | Output AI con `lesson_id` errato |
| `lesson_speech_no_slides_input` | 422 | `slides_raw` mancante o malformato |
| `lesson_speech_unknown_slide_ref` | 422 | Segmento ancora a `slide_id` inesistente |
| `lesson_speech_uncovered_slides` | 422 | Almeno una slide senza segmento di parlato |
| `lesson_speech_duplicate_segment_id` | 422 | `segment_id` duplicati |
| `lesson_speech_duration_out_of_range` | 422 | `sum(estimated_duration_seconds)` fuori da [target × 0.95, target × 1.05] |
| `lesson_speech_tts_unsafe` | 422 | Testo segmento contiene caratteri/abbreviazioni/LaTeX proibiti |
| `lesson_speech_map_unknown_slide` | 422 | `slide_to_segments_map` referenzia slide inesistente |
| `lesson_speech_map_unknown_segment` | 422 | `slide_to_segments_map` referenzia segmento inesistente |
| `lesson_speech_map_inconsistent_slide_id` | 422 | Mappatura segmento incoerente con `speech_segments` |
| `lesson_speech_map_duration_mismatch` | 422 | `slide_total_duration_seconds` ≠ somma durate segmenti slide |
| `lesson_speech_map_orphan_segments` | 422 | Segmenti non listati in `slide_to_segments_map` |
| `speech_pdf_already_in_progress` | 409 | Export PDF discorso già in corso |
| `invalid_lesson_speech_status_for_pdf` | 409 | Export PDF discorso da lezione senza discorso ready/approved |
| `no_eligible_lessons_for_speech_pdf` | 409 | Export-all PDF discorso senza lezioni esportabili |
| `speech_pdf_not_ready` | 404 | Download PDF discorso su `speech_pdf_status` ≠ ready |
| `speech_pdf_file_missing` | 404 | File PDF discorso mancante |
| `module_has_no_lessons` | 409 | Bundle PDF modulo richiesto su un modulo senza lezioni |
| `module_pdfs_not_ready` | 409 | Bundle PDF modulo (merged/zip) richiesto quando almeno una lezione non ha `*_pdf_status='ready'`. `meta.missing_lessons` elenca gli UUID delle lezioni mancanti |
| `module_pdf_file_missing` | 404 | Bundle PDF modulo: file mancante sul filesystem per una delle lezioni (DB ha `*_pdf_path` ma il file è stato rimosso) |
| `lesson_asset_not_in_course` | 404 | Endpoint `/lesson-assets/convert-to-mermaid`: path fuori dal subdir `lesson_assets/{course_id}/` (cross-tenant safety) |
| `invalid_lesson_asset_path` | 404 | Path lesson asset malformato (`..`, doppi slash) |
| `lesson_asset_file_missing` | 404 | File lesson asset non più sul filesystem |
| `lesson_asset_unsupported_ext` | 409 | Estensione lesson asset non in `{png, jpg, jpeg, webp}` |
| `openai_not_configured` | 409 | `OPENAI_API_KEY` non configurata sul server (rilevato anche dall'endpoint image→Mermaid) |
| `image_to_mermaid_failed` | 409 | Conversione Vision API fallita: `UNRECOGNIZED`, output non-Mermaid, o errore HTTP da OpenAI |
| `invalid_mime` / `file_too_large` / `invalid_image` / `empty_file` / `invalid_subdir` | 422 | Errori validation di `file_service.save_upload_image` su upload (lesson asset, template, avatar, document) |
| `lesson_not_assessment` | 409 | PATCH `/lessons/{id}/assessment` su una lezione che non è una verifica (`is_assessment=false`) |
| `speech_not_approved` | 409 | Generate video (Fase 6) su lezione con `speech_status` ≠ approved |
| `slides_not_approved` | 409 | Generate video (Fase 6) su lezione con `slides_status` ≠ approved |
| `voice_sample_missing` | 409 | Generate video (Fase 6) quando l'avatar dell'assegnatario non ha il campione vocale (`Avatar.audio_path` NULL) |
| `lesson_video_not_ready` | 409 | Generate video con avatar (Fase 6b) su lezione con `video_status` ≠ ready |
| `avatar_clips_not_ready` | 409 | Generate video con avatar (Fase 6b) quando l'avatar dell'assegnatario non ha clip MiniMax pronte |
| `avatar_not_found` | 404 | `PATCH /me/avatar/musetalk-params` quando l'utente corrente non ha un avatar |
