# 01 — Data model

Schema delle 6 tabelle del dominio Corsi (oltre a `organization_course_settings`
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
| `lesson_duration_minutes` | smallint | NOT NULL, CHECK `>= 1` | snapshot |
| `assessment_lesson_enabled` | bool | NOT NULL | snapshot |
| `multiple_choice_questions_count` | smallint | NOT NULL, CHECK `>= 0` | snapshot |
| `open_questions_count` | smallint | NOT NULL, CHECK `>= 0` | snapshot |
| `assignee_user_id` | UUID | FK `users.id` RESTRICT | docente assegnato |
| `created_by_user_id` | UUID | FK `users.id` SET NULL, nullable | |
| `status` | str(40) | NOT NULL, default `draft`, CHECK ∈ 14 valori | state machine pipeline AI |
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
content_pending, content_ready, content_approved, slides_pending, slides_ready,
speech_pending, speech_ready, published, archived
```

`lessons_structure_*` di livello corso è **derivato** dagli stati per-modulo (vedi `course_module` sotto). La transizione è gestita dal service `course_lesson_structure_service._recompute_course_lessons_structure_status`.

`content_*` di livello corso è **derivato** dagli stati per-lezione: almeno
1 lezione `pending|processing|failed` → `content_pending`; tutte `ready|approved`
(almeno 1 `ready`) → `content_ready`; tutte `approved` → `content_approved`.

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
| timestamps | | |

UNIQUE `(course_id, position)`, UNIQUE `(course_id, module_code)`. Index su `course_id`.

`lessons_structure_status` ∈ `empty | pending | processing | ready | approved | failed`.

---

## `course_lesson` — `app/models/course_lesson.py`

Lezioni dei moduli. La lezione introduttiva (`is_introductory=true`) ha la
`recommended_bibliography` valorizzata. I 4 campi JSONB
`learning_objectives`, `mandatory_topics`, `prerequisites`, `section_outline`
sono il payload di Fase 2 (popolati dal worker o dall'edit manuale).

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
| `content_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ `empty/pending/processing/ready/approved/failed` (Fase 3 — migration 0015) |
| `content_raw` | JSONB | nullable — output AI completo verbatim §6.3 |
| `content_tokens` | JSONB | nullable — `{prompt, completion, total, model}` |
| `content_attempts` | smallint | NOT NULL, default 0 |
| `content_error` | text | nullable |
| `content_generated_at` | datetime tz | nullable |
| `content_approved_at` | datetime tz | nullable |
| `content_regeneration_hint` | text | nullable — hint utente §9.3 |
| `content_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `content_progress_phase` | str(50) | nullable — `preparing_prompt / calling_openai / materializing` |
| `pdf_status` | str(40) | NOT NULL, default `empty`, CHECK ∈ `empty/pending/processing/ready/failed` (§7 — migration 0016) |
| `pdf_progress` | smallint | NOT NULL, default 0, CHECK 0..100 |
| `pdf_progress_phase` | str(50) | nullable — `preparing / rendering_html / rendering_pdf` |
| `pdf_path` | str(500) | nullable — relativo a `GENERATED_PDFS_DIR` |
| `pdf_template_id` | UUID | FK `pdf_templates.id` SET NULL — snapshot template ultima generazione |
| `pdf_attempts` | smallint | NOT NULL, default 0 |
| `pdf_error` | text | nullable |
| `pdf_generated_at` | datetime tz | nullable |
| timestamps | | |

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

## Frontend mirror types

`frontend/src/api/courses.ts` espone i tipi TypeScript speculari:

- `CourseOut`, `CourseListItemOut`, `CourseCreateInput`, `CourseUpdateInput`
- `CourseDocumentOut`, `CourseDocumentDetailOut`
- `CourseModuleOut`, `CourseLessonOut` (esteso con campi Fase 2 + Fase 3 + §7 PDF + meta modulo)
- `DocumentSummaryOut` + sotto-tipi (`KeyConcept`, `Definition`, ecc.)
- `RecommendedBibliographyItem`
- `ArchitectureTokens`, `LessonStructureTokens`, `LessonContentTokens`, `GlossaryTokens`
- `LessonsStructureModuleStatus`, `LessonContentStatus`, `LessonPdfStatus`, `GlossaryStatus` (`empty | pending | processing | ready | approved | failed`)
- `LessonStructureMandatoryTopic`, `LessonStructureSectionOutline`,
  `LessonStructureUpdateInput`
- `LessonContentRaw` + sotto-tipi (`Section`, `VisualAsset`, `Table`,
  `Equation`, `Example`, `Reference`, `CoverageCheck`),
  `LessonContentUpdateInput`
- `GlossaryRaw`, `GlossaryTerm`
