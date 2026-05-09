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
| `architecture_tokens` | JSONB | nullable | `{prompt, completion, total, model}` |
| `architecture_error` | text | nullable | ultimo errore |
| `architecture_generated_at` | datetime tz | nullable | |
| `architecture_regeneration_hint` | text | nullable | hint utente per ultima rigenerazione |
| `architecture_progress` | smallint | NOT NULL, default 0 | 0-100, aggiornato dal worker |
| `architecture_progress_phase` | str(50) | nullable | chiave i18n della fase corrente |
| `didactic_setup_confirmed_at` | datetime tz | nullable | lock setup didattico (Tab 1+2 read-only quando valorizzato) — migration 0017 |
| `glossary_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ 6 valori | Fase 3 — §10.1 (migration 0015) |
| `glossary_raw` | JSONB | nullable | `{course_id, terms:[{term, translation, usage_note}]}` |
| `glossary_tokens` | JSONB | nullable | `{prompt, completion, total, model}` |
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
- 8 × `*_term → CourseTaxonomyTerm | None`

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
| `lessons_structure_tokens` | JSONB | nullable (`{prompt, completion, total, model}`) |
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

Tabella estesa per **5 fasi della pipeline AI**: Fase 2 (struttura), Fase 3
(contenuto + PDF), Fase 4 (slide + PDF slide), Fase 5 (discorso + PDF discorso).

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
| `content_tokens` | JSONB | nullable — `{prompt, completion, total, model}` |
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
| `slides_tokens` | JSONB | nullable — `{prompt, completion, total, model}` |
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
| `speech_tokens` | JSONB | nullable — `{prompt, completion, total, model}` |
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

UNIQUE `(module_id, position)`, UNIQUE `(course_id, lesson_code)`. Index su `module_id` e `course_id`.

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
      "asset_type": "diagram",
      "format": "mermaid",
      "content": "graph LR\nA --> B",
      "caption": "...",
      "alt_text": "..."
    }
  ]
}
```

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

## Frontend mirror types

`frontend/src/api/courses.ts` espone i tipi TypeScript speculari:

- `CourseOut`, `CourseListItemOut`, `CourseCreateInput`, `CourseUpdateInput`
- `CourseDocumentOut`, `CourseDocumentDetailOut`
- `CourseModuleOut` (esteso con meta Fase 2 + `architecture_modified_at`)
- `CourseLessonOut` (esteso con campi tutte le fasi: Fase 2 / Fase 3 + PDF / Fase 4 + PDF slide / Fase 5 + PDF discorso)
- `DocumentSummaryOut` + sotto-tipi (`KeyConcept`, `Definition`, ecc.)
- `RecommendedBibliographyItem`
- `ArchitectureTokens`, `LessonStructureTokens`, `LessonContentTokens`, `LessonSlidesTokens`, `LessonSpeechTokens`, `GlossaryTokens`
- Status: `LessonsStructureModuleStatus`, `LessonContentStatus`, `LessonPdfStatus`, `LessonSlidesStatus`, `LessonSpeechStatus`, `GlossaryStatus` (`empty | pending | processing | ready | approved | failed` per quelli con approved; `empty | pending | processing | ready | failed` per i PDF)
- Fase 2: `LessonStructureMandatoryTopic`, `LessonStructureSectionOutline`, `LessonStructureUpdateInput`
- Fase 3: `LessonContentRaw` + sotto-tipi (`Section`, `VisualAsset`, `Table`, `Equation`, `Example`, `Reference`, `CoverageCheck`), `LessonContentUpdateInput`
- Fase 4: `LessonSlideItem` (con `body` field + `references_assets[]`), `LessonSlideNewAsset`, `LessonSlidesRaw`, `LessonSlidesUpdateInput`, `SlideType` (16 valori)
- Fase 5: `LessonSpeechSegment`, `LessonSlideSegmentsMapEntry`, `LessonSpeechRaw`, `LessonSpeechUpdateInput`
- `GlossaryRaw`, `GlossaryTerm`
