# 01 — Data model

Schema delle 7 tabelle del dominio Corsi (oltre a `organization_course_settings`
documentata in [Backend 05](../backend/05-models.md)).

## `course` — `app/models/course.py`

Tabella principale del corso. Snapshot dei parametri della org al momento della creazione
(immutabili anche se i settings org cambiano dopo).

| Campo | Tipo | Vincoli | Note |
|---|---|---|---|
| `id` | UUID | PK | |
| `organization_id` | UUID | FK `organizations.id` CASCADE | |
| `title` | str(200) | NOT NULL | |
| `objectives` | text | NOT NULL, default `""` | |
| `language_code` | str(10) | FK `languages.code` RESTRICT | |
| `video_language_code` | str(10) | FK `languages.code` SET NULL, nullable | override per-corso della lingua TTS dei video (Fase 6) — migration 0026. NULL → fallback su `language_code`. Validato lato API contro `XTTS_SUPPORTED_LANGUAGES` (`tts_languages.py`) |
| `argomenti_chiave` | JSONB | NOT NULL, default `[]` | lista di stringhe (max 30) |
| `cfu` | smallint | NOT NULL, CHECK `>= 1` | snapshot |
| `modules_count` | smallint | NOT NULL, CHECK `>= 1` | snapshot derivato |
| `lessons_per_module` | smallint | NOT NULL, CHECK `>= 1` | snapshot |
| `lesson_duration_minutes` | smallint | NOT NULL, CHECK `>= 1` | snapshot — usato come target durata Fase 5 |
| `assessment_lesson_enabled` | bool | NOT NULL | snapshot |
| `multiple_choice_questions_count` | smallint | NOT NULL, CHECK `>= 0` | snapshot |
| `open_questions_count` | smallint | NOT NULL, CHECK `>= 0` | snapshot |
| `assignee_user_id` | UUID | FK `users.id` RESTRICT | docente assegnato |
| `created_by_user_id` | UUID | FK `users.id` SET NULL, nullable | |
| `status` | str(40) | NOT NULL, default `draft`, CHECK ∈ 17 valori | state machine pipeline AI |
| 8 × `*_term_id` | UUID | FK `course_taxonomy_term.id` SET NULL, nullable | tassonomie |
| `course_overview` | text | nullable | output Fase 1 (overview generale) |
| `pedagogical_rationale` | text | nullable | output Fase 1 |
| `architecture_raw` | JSONB | nullable | output completo OpenAI (audit) |
| `architecture_attempts` | smallint | NOT NULL, default 0 | counter |
| `architecture_tokens` | JSONB | nullable | telemetria AI — vedi [Convenzione `*_tokens`](#convenzione-_tokens-telemetria-ai-per-chiamata) |
| `architecture_error` | text | nullable | ultimo errore |
| `architecture_generated_at` | datetime tz | nullable | |
| `architecture_regeneration_hint` | text | nullable | hint utente per ultima rigenerazione |
| `architecture_progress` | smallint | NOT NULL, default 0 | 0-100, aggiornato dal worker |
| `architecture_progress_phase` | str(50) | nullable | chiave i18n della fase corrente |
| `didactic_setup_confirmed_at` | datetime tz | nullable | lock setup didattico (Tab 1+2 read-only quando valorizzato) — migration 0017 |
| `glossary_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 6 valori | Fase 3 — §10.1 (migration 0015) |
| `glossary_raw` | JSONB | nullable | `{course_id, terms:[{term, translation, usage_note}]}` |
| `glossary_tokens` | JSONB | nullable | schema legacy `{prompt, completion, total, model}` — NON arricchito col tracking esteso |
| `glossary_generated_at` | datetime tz | nullable | |
| `glossary_error` | text | nullable | ultimo errore |
| timestamps | datetime tz | NOT NULL | |

### Status valori

```
draft, architecture_pending, architecture_ready, architecture_approved,
lessons_structure_pending, lessons_structure_ready, lessons_structure_approved,
content_pending, content_ready, content_approved,
slides_pending, slides_ready, slides_approved,
speech_pending, speech_ready, speech_approved,
published, archived
```

(17 valori; `slides_approved` aggiunto dalla migration 0019, `speech_approved` aggiunto dalla migration 0023).

I valori per le Fasi 2-5 (`lessons_structure_*`, `content_*`, `slides_*`, `speech_*`) sono **derivati** dagli stati per-modulo (Fase 2) o per-lezione (Fasi 3-5). Le transizioni sono gestite dai service:

- Fase 2 → `course_lesson_structure_service._recompute_course_lessons_structure_status`
- Fase 3 → `course_lesson_content_service._recompute_course_content_status`
- Fase 4 → `course_lesson_slides_service._recompute_course_slides_status`
- Fase 5 → `course_lesson_speech_service._recompute_course_speech_status`

Regola comune: almeno 1 in `pending|processing|failed` → `*_pending`; tutte in `ready|approved` (almeno 1 `ready`) → `*_ready`; tutte in `approved` → `*_approved`.

### Indici

- `(organization_id, status)`
- `(organization_id, assignee_user_id)`
- `(organization_id, language_code)`

### Relationships

- `documents → CourseDocument[]` (cascade delete)
- `modules → CourseModule[]` (cascade delete, ordered by position)
- `lessons → CourseLesson[]` (cascade delete, ordered by position)
- `language → Language` / `video_language → Language | None` (due FK distinte verso `languages.code`, `foreign_keys=` esplicito)
- 8 × `*_term → CourseTaxonomyTerm | None`

---

## Convenzione: `*_tokens` (telemetria AI per chiamata)

Tutti i campi JSONB chiamati `*_tokens` (su `course`, `course_module`,
`course_lesson`) condividono uno **schema uniforme** popolato dai 5
service AI (architettura, struttura lezioni, contenuti, slide, discorso).
È uno schema arricchito: i record vecchi avevano solo le 4 chiavi
originali (`model`, `prompt`, `completion`, `total`); i nuovi record
contengono anche i campi di tracking introdotti dal commit `764588f`.

```python
{
    # === Campi originali (backward compat) ===
    "model": str,                    # es. "gpt-5.5"
    "prompt": int,                   # = OpenAI usage.prompt_tokens
    "completion": int,               # = OpenAI usage.completion_tokens
    "total": int,                    # = OpenAI usage.total_tokens

    # === Campi nuovi (telemetria) ===
    "reasoning_effort": str | None,  # da settings al call-time; None se modello non-reasoning
    "reasoning_tokens": int,         # = usage.completion_tokens_details.reasoning_tokens (sub di completion)
    "cached_tokens": int,            # = usage.prompt_tokens_details.cached_tokens (sub di prompt)
    "duration_ms": int,              # time.monotonic() attorno alla chiamata HTTP
    "cost_usd": float | None,        # estimate_cost_usd(...) — None se pricing del modello sconosciuto
}
```

Note:
- `reasoning_tokens` è già contato dentro `completion` (non additivo).
- `cached_tokens` è già contato dentro `prompt` (non additivo).
- `cost_usd = None` quando il modello non è in `MODEL_PRICING` (stato
  accettabile: tracking best-effort, niente eccezioni).
- I record antecedenti al commit `764588f` mancano dei 5 campi nuovi;
  consumer futuri devono leggerli con `.get(key, default)`.

Il calcolo è centralizzato in `app.services.openai_pricing`:

| Funzione | Scopo |
|---|---|
| `MODEL_PRICING: dict[str, dict[str, float]]` | Prezzi USD per 1M token (input, output, cached_input) — aggiornare quando OpenAI cambia listino |
| `supports_reasoning(model)` | True per `gpt-5*`, `o1*`, `o3*`, `o4*` |
| `estimate_cost_usd(model, prompt, completion, reasoning_tokens=0, cached_tokens=0) -> float \| None` | `(prompt - cached) * input + cached * cached_input + completion * output`; None se modello sconosciuto |
| `build_usage_dict(*, model, reasoning_effort_setting, openai_usage, duration_ms)` | Helper unico chiamato dai 5 service AI per costruire il dict da salvare |

Migration: **nessuna** — JSONB è schema-less, l'arricchimento è
retro-compatibile.

Glossario corso (`course.glossary_tokens`): esplicitamente NON tracciato
con il nuovo schema arricchito — è considerato out-of-scope rispetto
alle 5 fasi principali. Il campo continua a usare il vecchio schema
`{model, prompt, completion, total}`.

---

## `course_document` — `app/models/course_document.py`

Documenti di riferimento caricati dal docente (PDF, DOCX, DOC, RTF, TXT, MD). Ognuno
viene processato in background dal `course_document_worker` per produrre un riassunto
strutturato (Appendice A).

| Campo | Tipo | Vincoli | Note |
|---|---|---|---|
| `id` | UUID | PK | |
| `course_id` | UUID | FK `course.id` CASCADE | |
| `filename_original` | str(500) | NOT NULL | nome originale (audit) |
| `filename_stored` | str(500) | NOT NULL | nome su disco (UUID) |
| `mime_type` | str(120) | NOT NULL | |
| `size_bytes` | bigint | NOT NULL | |
| `summary` | JSONB | nullable | output Appendice A validato |
| `summary_status` | str(20) | NOT NULL, default `pending` | ∈ `pending`/`processing`/`ready`/`failed` |
| `summary_error` | text | nullable | |
| `summary_generated_at` | datetime tz | nullable | |
| `summary_attempts` | smallint | NOT NULL, default 0 | counter |
| `summary_tokens` | JSONB | nullable | `{prompt, completion, total, model}` |
| `text_extracted_at` | datetime tz | nullable | |
| `text_chars_extracted` | int | nullable | post-troncamento |
| `uploaded_by_user_id` | UUID | FK `users.id` SET NULL, nullable | |
| timestamps | | NOT NULL | |

Path su disco: `{uploads_dir}/courses/{course_id}/{filename_stored}`.

> **Documenti da import paper.** I paper scientifici importati dalla feature
> "Paper Scientifici" (vedi [16 — Paper search](16-paper-search.md)) sono
> `CourseDocument` **standard**: la tabella **non** ha colonne aggiuntive (nessun
> campo `source` sul modello). Oltre ad `add_document` (upload manuale `UploadFile`),
> esiste un secondo entry-point di creazione, `course_service.add_document_from_bytes`
> (`backend/app/services/course_service.py:746`), che riceve `bytes` invece di un
> `UploadFile`: `paper_import_service.import_paper`
> (`backend/app/services/paper_import_service.py:145`) lo usa per salvare il PDF se
> il paper è open-access (`application/pdf`) oppure, in fallback, un `.md` di
> metadati (`text/markdown`). In entrambi i casi il documento nasce con
> `summary_status="pending"` ed è preso in carico dalla pipeline standard
> `course_document_worker` (vedi [02 — Document preprocessing](02-document-preprocessing.md)).
> La provenienza `external_import` è tracciata **solo** nel metadata dell'audit log
> (action `course.document.add_from_bytes`, `metadata.source="external_import"`,
> `course_service.py:801`), **non** sul modello.

---

## `course_module` — `app/models/course_module.py`

Moduli generati dalla Fase 1 (o creati/modificati manualmente). I 10 campi
`lessons_structure_*` sono il payload di Fase 2 (struttura lezioni).

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `course_id` | UUID | FK `course.id` CASCADE |
| `position` | smallint | NOT NULL, CHECK `>= 1` |
| `module_code` | str(20) | NOT NULL (`M1`, `M2`, ...) |
| `title` | str(300) | NOT NULL |
| `description` | text | NOT NULL, default `""` |
| `lessons_structure_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 6 valori |
| `lessons_structure_raw` | JSONB | nullable (output AI completo §5.3) |
| `lessons_structure_tokens` | JSONB | nullable — telemetria AI (vedi convenzione `*_tokens` in cima al doc) |
| `lessons_structure_attempts` | smallint | NOT NULL, default 0 |
| `lessons_structure_error` | str(500) | nullable |
| `lessons_structure_generated_at` | datetime tz | nullable |
| `lessons_structure_approved_at` | datetime tz | nullable |
| `lessons_structure_regeneration_hint` | text | nullable |
| `lessons_structure_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `lessons_structure_progress_phase` | str(50) | nullable |
| `architecture_modified_at` | datetime tz | nullable | stale-detection — set da CRUD manuale moduli/lezioni-architettura (migration 0018) |
| timestamps | | |

UNIQUE `(course_id, position)`, UNIQUE `(course_id, module_code)`. Index su `course_id`.

`lessons_structure_status` ∈ `empty | pending | processing | ready | approved | failed`.

---

## `course_lesson` — `app/models/course_lesson.py`

Lezioni dei moduli. La lezione introduttiva (`is_introductory=true`) ha la
`recommended_bibliography` valorizzata. I 4 campi JSONB
`learning_objectives`, `mandatory_topics`, `prerequisites`, `section_outline`
sono il payload di Fase 2 (popolati dal worker o dall'edit manuale).

Tabella estesa per **5 fasi della pipeline AI** + 2 fasi di generazione
video: Fase 2 (struttura), Fase 3 (contenuto + PDF), Fase 4 (slide + PDF
slide), Fase 5 (discorso + PDF discorso), Fase 6 (video MP4), Fase 6b
(video con avatar).

### Identità + Fase 1/2 (architettura + struttura)

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `module_id` | UUID | FK `course_module.id` CASCADE |
| `course_id` | UUID | FK `course.id` CASCADE |
| `position` | smallint | NOT NULL, CHECK `>= 1` |
| `lesson_code` | str(30) | NOT NULL (`M1.L1`, `M1.L2`, ...) |
| `title` | str(300) | NOT NULL |
| `summary` | text | NOT NULL, default `""` |
| `is_introductory` | bool | NOT NULL, default false |
| `is_assessment` | bool | NOT NULL, default false — lezione di verifica delle competenze (migration 0028). Vedi [14 — Assessment lesson](14-assessment-lesson.md) |
| `recommended_bibliography` | JSONB | NOT NULL, default `[]` |
| `learning_objectives` | JSONB | NOT NULL, default `[]` (Fase 2) |
| `mandatory_topics` | JSONB | NOT NULL, default `[]` (Fase 2) |
| `prerequisites` | JSONB | NOT NULL, default `[]` (Fase 2) |
| `section_outline` | JSONB | NOT NULL, default `[]` (Fase 2) |
| `lesson_structure_modified_at` | datetime tz | nullable | stale-detection — modifica manuale dei 4 campi Fase 2 (migration 0018) |

### Fase 3 — Contenuto

| Campo | Tipo | Vincoli |
|---|---|---|
| `content_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 6 valori — migration 0015 |
| `content_raw` | JSONB | nullable — output AI completo verbatim §6.3 |
| `content_tokens` | JSONB | nullable — telemetria AI (vedi convenzione `*_tokens` in cima al doc) |
| `content_attempts` | smallint | NOT NULL, default 0 |
| `content_error` | text | nullable |
| `content_generated_at` | datetime tz | nullable |
| `content_approved_at` | datetime tz | nullable |
| `content_modified_at` | datetime tz | nullable | stale-detection — modifica manuale `content_raw` (migration 0018) |
| `content_regeneration_hint` | text | nullable — hint utente §9.3 |
| `content_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `content_progress_phase` | str(50) | nullable — `preparing_prompt / calling_openai / materializing` |

### §7 — Export PDF lezione testo

| Campo | Tipo | Vincoli |
|---|---|---|
| `pdf_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 5 valori (no `approved`) — migration 0016 |
| `pdf_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `pdf_progress_phase` | str(50) | nullable — `preparing / rendering_html / rendering_pdf` |
| `pdf_path` | str(500) | nullable — relativo a `GENERATED_PDFS_DIR` |
| `pdf_template_id` | UUID | FK `pdf_templates.id` SET NULL — snapshot template ultima generazione |
| `pdf_attempts` | smallint | NOT NULL, default 0 |
| `pdf_error` | text | nullable |
| `pdf_generated_at` | datetime tz | nullable |

### Fase 4 — Slide della lezione

| Campo | Tipo | Vincoli |
|---|---|---|
| `slides_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 6 valori — migration 0019 |
| `slides_raw` | JSONB | nullable — output AI completo verbatim §7.3 |
| `slides_tokens` | JSONB | nullable — telemetria AI (vedi convenzione `*_tokens` in cima al doc) |
| `slides_attempts` | smallint | NOT NULL, default 0 |
| `slides_error` | text | nullable |
| `slides_generated_at` | datetime tz | nullable |
| `slides_approved_at` | datetime tz | nullable |
| `slides_modified_at` | datetime tz | nullable | stale-detection — modifica manuale `slides_raw` |
| `slides_regeneration_hint` | text | nullable — hint utente §9.4 |
| `slides_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `slides_progress_phase` | str(50) | nullable — `preparing_prompt / calling_openai / materializing` |

### Fase 4 — Export PDF slide

| Campo | Tipo | Vincoli |
|---|---|---|
| `slides_pdf_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 5 valori — migration 0020 |
| `slides_pdf_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `slides_pdf_progress_phase` | str(50) | nullable |
| `slides_pdf_path` | str(500) | nullable — suffisso `_slides.pdf` |
| `slides_pdf_template_id` | UUID | FK `slide_templates.id` SET NULL (migration 0022 — non `pdf_templates`!) |
| `slides_pdf_attempts` | smallint | NOT NULL, default 0 |
| `slides_pdf_error` | text | nullable |
| `slides_pdf_generated_at` | datetime tz | nullable |

### Fase 5 — Discorso temporizzato

| Campo | Tipo | Vincoli |
|---|---|---|
| `speech_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 6 valori — migration 0023 |
| `speech_raw` | JSONB | nullable — output AI completo verbatim §8.4 |
| `speech_tokens` | JSONB | nullable — telemetria AI (vedi convenzione `*_tokens` in cima al doc) |
| `speech_attempts` | smallint | NOT NULL, default 0 |
| `speech_error` | text | nullable |
| `speech_generated_at` | datetime tz | nullable |
| `speech_approved_at` | datetime tz | nullable |
| `speech_modified_at` | datetime tz | nullable | stale-detection — modifica manuale `speech_raw` |
| `speech_regeneration_hint` | text | nullable — hint utente §9.5 |
| `speech_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `speech_progress_phase` | str(50) | nullable — `preparing_prompt / calling_openai / materializing` |

### Fase 5 — Export PDF discorso

| Campo | Tipo | Vincoli |
|---|---|---|
| `speech_pdf_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 5 valori — migration 0024 |
| `speech_pdf_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `speech_pdf_progress_phase` | str(50) | nullable |
| `speech_pdf_path` | str(500) | nullable — suffisso `_speech.pdf` |
| `speech_pdf_template_id` | UUID | FK `pdf_templates.id` SET NULL (kind=lesson — discorso è prosa pura) |
| `speech_pdf_attempts` | smallint | NOT NULL, default 0 |
| `speech_pdf_error` | text | nullable |
| `speech_pdf_generated_at` | datetime tz | nullable |

### Fase 6 — Video MP4 della lezione

8 colonne `video_*` — pipeline async (TTS RunPod + slide PNG + ffmpeg).
Pre-condizione runtime: `speech_status='approved'` AND
`slides_status='approved'`. Vedi [12 — Lesson video](12-lesson-video.md).

| Campo | Tipo | Vincoli |
|---|---|---|
| `video_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 6 valori (`empty\|pending\|processing\|ready\|failed\|cancelled`) — migration 0025 |
| `video_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `video_progress_phase` | str(50) | nullable — `preparing / tts / rendering_slides / encoding` |
| `video_path` | str(500) | nullable — path relativo (`lesson_videos/...`) |
| `video_attempts` | smallint | NOT NULL, default 0 |
| `video_error` | text | nullable |
| `video_generated_at` | datetime tz | nullable |
| `video_tokens` | JSONB | nullable — telemetria run: `audio_duration_s, video_duration_s, encode_duration_ms, tts_duration_ms, device, model_xtts, num_segments, num_slides, file_size_bytes` |

### Fase 6b — Video con avatar

8 colonne `avatar_video_*` — gemelle delle `video_*`. Pipeline async che
sovrappone l'avatar parlante (lip-sync MuseTalk) al video MP4 già
generato. Pre-condizione runtime: `video_status='ready'`. Vedi
[13 — Avatar video](13-avatar-video.md).

| Campo | Tipo | Vincoli |
|---|---|---|
| `avatar_video_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 6 valori (`empty\|pending\|processing\|ready\|failed\|cancelled`) — migration 0029 |
| `avatar_video_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `avatar_video_progress_phase` | str(50) | nullable — `preparing / lipsync / overlay` |
| `avatar_video_path` | str(500) | nullable — path relativo (`lesson_avatar_videos/...`) |
| `avatar_video_attempts` | smallint | NOT NULL, default 0 |
| `avatar_video_error` | text | nullable |
| `avatar_video_generated_at` | datetime tz | nullable |
| `avatar_video_tokens` | JSONB | nullable — telemetria run: `lipsync_duration_s, overlay_duration_ms, runpod_job_id, runpod_execution_time_s, num_unique_clips, manifest_cache_hit, file_size_bytes` |

> Le colonne `video_*` e `avatar_video_*` **non** usano lo schema
> uniforme `*_tokens` della telemetria AI (vedi sopra): non c'è una
> chiamata OpenAI dietro. Sono dict di telemetria di pipeline (durate,
> dimensioni file, device) con uno schema proprio per fase.

UNIQUE `(module_id, position)`, UNIQUE `(course_id, lesson_code)`. Index su `module_id` e `course_id`. Due index dedicati alle query batch dei video: `ix_course_lesson_course_video_status` su `(course_id, video_status)` e `ix_course_lesson_course_avatar_video_status` su `(course_id, avatar_video_status)`.

`recommended_bibliography` è una lista di:

```json
{
  "authors": "string",
  "title": "string",
  "publisher": "string",
  "year": "string",
  "note": "string",
  "source": "from_uploaded_documents | general_knowledge_suggestion",
  "confidence": "confirmed | to_verify"
}
```

> **Regola §4.4** (validata su output AI, non sull'edit manuale): se
> `source = "general_knowledge_suggestion"` allora `confidence = "to_verify"`.

### Schema dei 4 campi Fase 2

```json
// learning_objectives: lista di stringhe (3-6)
["Lo studente sarà in grado di applicare l'algoritmo k-NN…", "..."]

// mandatory_topics: lista di {topic_id, topic, rationale} (3-7)
[{"topic_id": "T1", "topic": "k-Nearest Neighbors", "rationale": "..."}, ...]

// prerequisites: lista di stringhe (0-5)
["Conoscenza di base della distanza euclidea", "..."]

// section_outline: lista di {section_id, title, purpose, covers_topic_ids[]} (3-7)
[{"section_id": "S1", "title": "...", "purpose": "...", "covers_topic_ids": ["T1", "T3"]}, ...]
```

Vincoli applicati nella materializzazione/edit (vedi §5.4 della spec):
- `topic_id` univoci per lezione
- `section_id` univoci per lezione
- `covers_topic_ids[i]` ∈ `mandatory_topics.topic_id`
- L'unione di `covers_topic_ids` su tutte le sezioni copre TUTTI i `topic_id` di
  `mandatory_topics`.

### Asset visivi: schema corrente

`content_raw.visual_assets[*]` segue lo schema `LessonContentVisualAsset`
(`backend/app/schemas/course_lesson_content.py:42`):

```python
{
  "asset_id": str,            # 1-50 chars, univoco per lezione/tipo
  "format": Literal[
    # produciibili dall'editor + dall'AI Fase 3
    "mermaid",   # `content` = codice Mermaid
    "image",     # `content` = path relativo (es. lesson_assets/{cid}/{uuid}.png)
    # — legacy read-only —
    "image_prompt",
    "image_search_query",
    "description",
  ],
  "content": str,
  "caption": str,
  "alt_text": str,
  # asset_type: REMOVED (Pydantic `extra="ignore"` su record vecchi).
}
```

Pre-refactor (commit `92d5f37`) lo schema aveva anche `asset_type`
(`diagram|schema|image|illustration|chart`) — etichetta semantica mai
letta dai renderer. Rimosso dal codice; i record JSONB esistenti possono
ancora contenere il campo, viene ignorato dal Pydantic in lettura.

**`format` ammessi**:
- `mermaid`: l'unica modalità "code-based". Renderizzata in lezione via
  `MermaidDiagram` (lazy) e in PDF via Playwright pre-render → SVG inline.
- `image`: immagine caricata dall'utente via
  `POST /lesson-assets/upload`. `content` è un path relativo sotto
  `lesson_assets/{course_id}/{uuid}.{ext}` servito da
  `StaticFiles("/uploads", ...)`. Rendering: `<img src="/uploads/...">`
  in lezione; data-URL base64 inline nel PDF
  (`_resolve_template_asset_url` in `course_lesson_pdf_service.py`).
- `image_prompt | image_search_query | description`: SOLO legacy. Erano
  i 3 format pre-refactor; l'editor non li produce più. In lettura
  vengono renderizzati come placeholder testuale (vedi
  `MarkdownRenderer.VisualAssetBlock`).

**Cleanup file orfani**: quando un asset con `format=image` viene rimosso
da `content_raw.visual_assets` via PATCH, `update_lesson_content`
(`course_lesson_content_crud.py`) esegue `os.unlink` best-effort sul
file fisico dopo `db.commit()` (safety check: il path deve essere sotto
`lesson_assets/{course_id}/`).

### Schema `slides_raw` (Fase 4 — §7.3)

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
    }
  ],
  "new_assets": [
    {
      "asset_id": "fig_new_1",
      "format": "mermaid",
      "content": "graph LR\nA --> B",
      "caption": "...",
      "alt_text": "..."
    }
  ]
}
```

Nota: il campo `asset_type` (5 etichette) è stato rimosso dallo schema —
era puramente metadata, nessun renderer (frontend o template PDF) lo
leggeva. Record antecedenti possono ancora contenerlo nel JSONB: la
Pydantic `LessonContentVisualAsset` ha `extra="ignore"` per tollerarlo.
Vedi anche la sezione [Asset visivi: schema corrente](#asset-visivi-schema-corrente)
qui sotto.

`slide.type` ∈ `title | agenda | prerequisites | concept | definition | diagram | formula | table | example | case_study | exercise | discussion | summary | takeaways | references | bibliography`. `body` è prosa breve (1-3 frasi, max 600 char) per evitare slide tutte-bullet visivamente piatte.

### Schema `speech_raw` (Fase 5 — §8.4)

```json
{
  "lesson_id": "M1.L4",
  "language": "it",
  "target_duration_seconds": 1800,
  "estimated_total_duration_seconds": 1820,
  "estimated_total_word_count": 3950,
  "speech_segments": [
    {
      "segment_id": "SEG001",
      "slide_id": "S01",
      "text": "Benvenuti, in questa lezione esploreremo...",
      "estimated_duration_seconds": 25,
      "delivery_notes": "Tono caloroso, pausa breve dopo benvenuti."
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

Vincoli applicati nella materializzazione (`materialize_lesson_speech`, regole §8.5):

1. `output.lesson_id == lesson.lesson_code`
2. ogni `speech_segments[i].slide_id` ∈ `slides_raw.slides[*].slide_id`
3. ogni slide di Fase 4 ha **almeno un segmento** associato
4. `segment_id` univoci per lezione
5. `sum(estimated_duration_seconds) ∈ [target × 0.95, target × 1.05]` con `target = lesson_duration_minutes × 60`
6. word count coerente con duration × wpm (130 IT / 150 EN) ±15% → soft warning
7. `slide_to_segments_map` coerente con `speech_segments` (no orfani, durate slide quadrano)
8. **TTS-safety**: testo segmento non contiene `*` `_` `` ` `` `#` `\` `$`, abbreviazioni note (`es.`, `etc.`, `ca.`, `p.es.`, `i.e.`, `e.g.`), comandi LaTeX (`\frac`, `\sum`, ...)

### Stale-detection cascata

Tutti gli `*_modified_at` vengono settati **solo dai CRUD manuali**, mai dai worker AI. La logica frontend in `lib/staleness.ts` confronta i timestamp in cascata per dedurre se un downstream è disallineato:

```
architecture_modified_at  ──► structure  ──► content  ──► slides  ──► speech
                                              ↓             ↓          ↓
                                           pdf_*       slides_pdf_*  speech_pdf_*
```

Quando l'utente rigenera AI un livello, **lo status PDF a valle viene resettato a `empty`** per impedire il download di un PDF stale (vedi `request_lesson_slides_generation` e `request_lesson_speech_generation`).

---

## `course_taxonomy_term` — `app/models/course_taxonomy.py`

Tassonomie per categorizzare i corsi. 8 tipi (`category`, `teaching_style`,
`content_depth`, `teacher_role`, `audience_size`, `knowledge_level`,
`target_audience`, `eqf_level`).

Struttura ad albero (parent_id) con label i18n in JSONB e descrizioni per lingua.

Vedi [migration 0009](../backend/10-alembic.md) per dettagli.

---

## `language` — `app/models/language.py`

Lingue supportate per il corso (codice ISO + nome nativo + bandiera). Seed di
24 lingue UE.

---

## `slide_template` — `app/models/slide_template.py`

Template grafico **unificato** per slide: avatar video (kind=avatar) + export PDF slide (Fase 4). La migration 0022 ha unificato i due template (rimosso `kind` discriminator da `pdf_templates`, FK `slides_pdf_template_id` puntata a `slide_templates`). Rispetto a `pdf_template`, ha `slide_size` (16:9/4:3) invece di `page_size` (A4/A3/Letter) ma stessi campi font/color/margin/background/loghi.

Aggiunte dal migration 0022:
- `margin_mm` SMALLINT NOT NULL DEFAULT 20 CHECK 0..60
- `background_opacity_pct` SMALLINT NOT NULL DEFAULT 15 CHECK 0..100

Il template `pdf_templates` (lezione testo + discorso, A4 portrait) resta separato.

---

## `course_duplication_job` — `app/models/course_duplication_job.py`

Tabella di orchestrazione per il job background di **duplicazione corso
in altra lingua** (vedi [15 — Duplicazione corso](15-course-duplication.md)).
Creata dalla migration 0031 + estesa da 0032 con `progress_detail`.

### Colonne

- `id` UUID PK
- `source_course_id` UUID FK `course(id)` ON DELETE CASCADE — corso
  sorgente
- `target_course_id` UUID FK `course(id)` ON DELETE SET NULL nullable —
  popolato dopo la phase `cloning_structure` del worker
- `target_language_code` VARCHAR(10) FK `languages(code)` ON DELETE
  RESTRICT — lingua target
- `status` VARCHAR(40) NOT NULL `server_default='pending'` — CHECK in
  `('pending','processing','ready','failed')`
- `progress` SMALLINT NOT NULL `server_default='0'` — CHECK 0..100
- `progress_phase` VARCHAR(50) nullable — fase corrente del worker
  (`loading_source` / `cloning_structure` / `translating_architecture` /
  `translating_lesson_metadata` /
  `translating_lesson_content_slides_speech` /
  `translating_glossary_documents` / `finalizing`)
- `progress_detail` VARCHAR(200) nullable — sub-progress UX granulare
  (es. `"23/48 lezioni completate"`, popolato durante la combined
  phase). Migration 0032.
- `error` TEXT nullable — messaggio se `status='failed'`
- `attempts` SMALLINT NOT NULL `server_default='0'` — counter retry
  trasparente (cap 5). Vedi anche resume-from-progress: al retry il
  worker memorizza `job.progress` PRIMA del reset, per skippare le
  phase già completate.
- `tokens` JSONB nullable — aggregato cost / wall_clock_seconds
- `requested_by_user_id` UUID FK `users(id)` ON DELETE SET NULL — chi
  ha avviato la duplicazione
- `started_at`, `finished_at` TIMESTAMPTZ nullable — `started_at` è
  esposto anche nel `CourseDuplicationJobCompact` (oltre che nel full)
  per permettere al FE di calcolare l'ETA stimato nel badge senza
  chiamare l'endpoint dedicato
- `created_at`, `updated_at` TIMESTAMPTZ NOT NULL `default now()`

### Indici e vincoli

- 3 index plain: `source_course_id`, `target_course_id`, `status`
- **Unique parziale** `uq_course_duplication_active` su
  `(source_course_id, target_language_code) WHERE status IN
  ('pending','processing')` — impedisce job concorrenti per stessa
  coppia (source, lingua) a livello DB
- 2 CHECK constraint: `ck_course_duplication_job_status` (enum),
  `ck_course_duplication_job_progress` (0..100)

### Relationships

- `source_course` / `target_course` → `Course` (entrambi
  `foreign_keys=` esplicito perché due FK sulla stessa tabella)
- `target_language` → `Language`
- `requested_by` → `User`

### Note di runtime

- Il worker `course_duplication_worker` polla questa tabella ogni 4s
  (cap globale 1 job alla volta) e dispatcha `_process_one` su
  semaforo locale cap 3 per le lezioni dentro al job.
- `CourseListItemOut.duplication_job` (embedded in `CourseDuplicationJobCompact`)
  viene popolato dal `course_service.list_courses` con i job attivi
  (status ∈ pending|processing) i cui `target_course_id` sono nella
  pagina corrente. Il FE polla la lista ogni 3s finché ci sono job
  attivi nella pagina.

---

## Frontend mirror types

`frontend/src/api/courses.ts` espone i tipi TypeScript speculari:

- `CourseOut`, `CourseListItemOut`, `CourseCreateInput`, `CourseUpdateInput`
- `CourseDocumentOut`, `CourseDocumentDetailOut`
- `CourseModuleOut` (esteso con meta Fase 2 + `architecture_modified_at`)
- `CourseLessonOut` (esteso con campi tutte le fasi: Fase 2 / Fase 3 + PDF / Fase 4 + PDF slide / Fase 5 + PDF discorso / Fase 6 video / Fase 6b video con avatar / `is_assessment`)
- `DocumentSummaryOut` + sotto-tipi (`KeyConcept`, `Definition`, ecc.)
- `RecommendedBibliographyItem`
- `ArchitectureTokens`, `LessonStructureTokens`, `LessonContentTokens`, `LessonSlidesTokens`, `LessonSpeechTokens`, `GlossaryTokens`
- Status: `LessonsStructureModuleStatus`, `LessonContentStatus`, `LessonPdfStatus`, `LessonSlidesStatus`, `LessonSpeechStatus`, `GlossaryStatus` (`empty | pending | processing | ready | approved | failed` per quelli con approved; `empty | pending | processing | ready | failed` per i PDF)
- Fase 2: `LessonStructureMandatoryTopic`, `LessonStructureSectionOutline`, `LessonStructureUpdateInput`
- Fase 3: `LessonContentRaw` + sotto-tipi (`Section`, `VisualAsset`, `Table`, `Equation`, `Example`, `Reference`, `CoverageCheck`), `LessonContentUpdateInput`
- Fase 4: `LessonSlideItem` (con `body` field + `references_assets[]`), `LessonSlideNewAsset`, `LessonSlidesRaw`, `LessonSlidesUpdateInput`, `SlideType` (16 valori)
- Fase 5: `LessonSpeechSegment`, `LessonSlideSegmentsMapEntry`, `LessonSpeechRaw`, `LessonSpeechUpdateInput`
- Fase 6: `LessonVideoStatus`, `LessonVideoPhase`, `LessonVideoTokens`, `LessonVideoStatusOut`, `LessonVideoBatchOut` + `XTTS_SUPPORTED_LANGUAGES` / `isXttsLanguage` (override lingua TTS)
- Fase 6b: `LessonAvatarVideoStatus`, `LessonAvatarVideoPhase`, `LessonAvatarVideoTokens`, `LessonAvatarVideoStatusOut`, `LessonAvatarVideoBatchOut`
- Assessment: `LessonAssessmentRaw`, `LessonAssessmentUpdateInput`, type-guard `isAssessmentRaw` (`content_raw` polimorfico `LessonContentRaw | LessonAssessmentRaw`)
- `GlossaryRaw`, `GlossaryTerm`
- Duplicazione: `CourseDuplicationJobStatus` (`pending|processing|ready|failed`), `CourseDuplicationJobCompact` (embed in `CourseListItemOut.duplication_job`), `CourseDuplicationJobOut` (response endpoint dedicati)
