# Courses

Documentazione della feature **Corsi** — il dominio principale di a4u. Implementata in più
iterazioni a partire dalla foundation. Ogni file di codice rilevante è documentato qui.

## Indice

- [01 — Data model](01-data-model.md): tabelle, vincoli, relazioni.
- [02 — Document pre-processing](02-document-preprocessing.md): pipeline di estrazione + riassunto AI dei documenti caricati (Appendice A di `prompt_generazione_corsi.md`).
- [03 — Architecture generation (Fase 1)](03-architecture-generation.md): generazione AI dell'architettura (moduli + lezioni) — §4 di `prompt_generazione_corsi.md`.
- [04 — Manual editing & AI lesson generation](04-manual-editing.md): CRUD manuale di moduli/lezioni + generazione AI delle lezioni di un singolo modulo.
- [05 — API reference](05-api-reference.md): tutti gli endpoint sotto `/orgs/{org_id}/courses`.
- [06 — Frontend](06-frontend.md): pagine, componenti, dialog, polling.
- [07 — Lesson structure (Fase 2)](07-lesson-structure.md): generazione AI parallela della struttura delle lezioni (obiettivi, temi, prerequisiti, scaletta) — §5 di `prompt_generazione_corsi.md`.
- [08 — Lesson content (Fase 3) + Glossario](08-lesson-content.md): generazione AI parallela del testo completo delle lezioni con asset visivi (Mermaid + LaTeX + tabelle), glossario corso, **editor user-friendly** (TipTap + custom Table/Latex/Mermaid editors) — §6 + §10.1 di `prompt_generazione_corsi.md`.
- [09 — PDF export](09-pdf-export.md): export PDF di lezione testo, slide e discorso tramite WeasyPrint (CSS Paged Media completo) + Jinja2 + markdown-it-py + Playwright (pre-render Mermaid → SVG, solo per PDF testo/slide) + latex2mathml — §7 di `prompt_generazione_corsi.md`. Tre pipeline distinte: `pdf_*` (testo), `slides_pdf_*` (slide), `speech_pdf_*` (discorso).
- [10 — Lesson slides (Fase 4)](10-lesson-slides.md): generazione AI delle slide della presentazione, riusando gli asset di Fase 3 + nuovi asset opzionali — §7 (sezione "slides") di `prompt_generazione_corsi.md`.
- [11 — Lesson speech (Fase 5)](11-lesson-speech.md): generazione AI del discorso temporizzato (parlato TTS-friendly suddiviso in segmenti sincronizzati alle slide) con vincolo `sum(durata) ≈ minuti_per_lezione × 60` ±5% e regole 130 wpm IT / 150 wpm EN — §8 + §9.5 di `prompt_generazione_corsi.md`.

## Stato pipeline AI (5 fasi)

Il documento `prompt_generazione_corsi.md` definisce 5 fasi sequenziali. Stato corrente:

| Fase | Spec | Implementata? |
|---|---|---|
| **Pre-processing** | Appendice A | ✅ Iterazione A |
| **Fase 1: Architettura** (moduli + lezioni) | §4 | ✅ Iterazione B |
| **Fase 2: Struttura lezioni** | §5 | ✅ Iterazione C |
| **Fase 3: Contenuti + Glossario** | §6 + §10.1 | ✅ Iterazione D |
| **§7: Export PDF lezione testo** | §7 | ✅ Iterazione E |
| **Fase 4: Slide** (incl. PDF export slide) | §7 (slides) | ✅ Iterazione F |
| **Fase 5: Discorso** (incl. PDF export discorso) | §8 + §9.5 | ✅ Iterazione G |

> **Nota terminologia**: la spec chiama §7 sia il documento PDF della
> lezione testo sia la generazione delle slide della presentazione. In
> questa codebase: §7 = export PDF lezione testo (`pdf_*`), Fase 4 =
> generazione slide (`slides_*`) + relativo PDF (`slides_pdf_*`),
> Fase 5 = generazione discorso (`speech_*`) + relativo PDF
> (`speech_pdf_*`). Tre pipeline PDF sono coesistenti, ognuna con il
> proprio worker e file di output.

A complemento della pipeline AI è stato implementato un **CRUD manuale** delle lezioni e dei
moduli, con **generazione AI per singolo modulo** quando l'utente ne crea uno nuovo manualmente.
Tutte le fasi 2-5 supportano edit manuale (`*_modified_at` per stale-detection cascata).

## State machine `course.status`

```
draft
  │  POST /architecture/generate
  ▼
architecture_pending → architecture_ready → architecture_approved
                                                    │  POST /lessons-structure/generate-all
                                                    ▼
                       lessons_structure_pending → lessons_structure_ready → lessons_structure_approved
                                                    │  POST /lessons-content/generate-all
                                                    ▼
                       content_pending → content_ready → content_approved
                                                    │  POST /lessons-slides/generate-all
                                                    ▼
                       slides_pending → slides_ready → slides_approved
                                                    │  POST /lessons-speech/generate-all
                                                    ▼
                       speech_pending → speech_ready → speech_approved
                                                    │
                                                    ▼
                                                published / archived
```

Lo `course.status` per Fasi 2-5 è **derivato** dagli stati per-lezione (o per-modulo per Fase 2):
- almeno 1 in `pending|processing|failed` → `*_pending`
- TUTTI in `ready|approved` (e almeno 1 in `ready`) → `*_ready`
- TUTTI in `approved` → `*_approved`

Failure paths: ogni `*_pending` su errore → `failed` per la singola entità (o `draft` per l'architettura), con `*_error` populato. **Auto-retry trasparente** prima del fail terminale: se l'errore è recuperabile (rate-limit OpenAI, validazione, materializzazione) e `attempts < auto_retry_max` (default 5), il worker riporta lo status a `pending` e ritenta al tick successivo; la UI mostra solo "in elaborazione" finché passa.

## Stale-detection (cascata)

Ogni operazione di CRUD manuale a monte (struttura, contenuto, slide, discorso) imposta un timestamp `*_modified_at` che il frontend confronta con il `*_generated_at` a valle per dedurre se qualcosa è disallineato. **Non blocca** — è un suggerimento. Vedi `frontend/src/lib/staleness.ts`:

| Helper | Trigger upstream confrontati |
|---|---|
| `isStructureStale(module)` | `module.architecture_modified_at` |
| `isContentStale(lesson, module)` | `lesson_structure_modified_at`, `module.architecture_modified_at` |
| `isPdfStale(lesson)` | `content_generated_at`, `content_modified_at` |
| `isSlidesStale(lesson, module)` | `content_*`, `lesson_structure_modified_at`, `module.architecture_modified_at` |
| `isSlidesPdfStale(lesson)` | `slides_generated_at`, `slides_modified_at` |
| `isSpeechStale(lesson, module)` | `slides_*`, `content_*`, `lesson_structure_modified_at`, `module.architecture_modified_at` |
| `isSpeechPdfStale(lesson)` | `speech_generated_at`, `speech_modified_at` |

I worker AI **non** toccano i `*_modified_at` (solo i CRUD manuali lo fanno), così la rigenerazione AI non si auto-segnala come stale.

## Permessi correlati

Tutti sotto namespace `course:*`. Vedi [06 — Permissions](../06-permissions.md).

| Codice | Significato |
|---|---|
| `course:view` | Vedere i corsi a sé assegnati |
| `course:view_all` | Visibilità cross-utente (vedi anche i corsi assegnati ad altri membri). Additivo a `view`. |
| `course:create` | Creare un nuovo corso con setup didattico completo |
| `course:save_draft` | Salvare uno stub di corso senza setup didattico (titolo + CFU + assegnatario) — pensato per admin che seminano bozze |
| `course:edit` | Modificare titolo, obiettivi, tassonomie, parametri, documenti, e fare CRUD manuale di moduli/lezioni e dei payload AI (struttura, contenuto, slide, discorso). Non implica visibilità cross-utente. |
| `course:delete` | Eliminare un corso (cascade documenti + architettura) |
| `course:assign` | Cambiare l'assegnatario |
| `course:generate` | Avviare la generazione AI a tutte le fasi (1-5) + export PDF (lezione/slide/discorso, anche batch per-modulo) + approvare i payload generati |
| `course_config:manage` | Modificare `OrganizationCourseSettings` |

## File chiave (mappa rapida)

### Backend

```
backend/app/
├── models/
│   ├── course.py                            # tabella course (status, snapshot CFU, architettura meta + glossary_*)
│   ├── course_document.py                   # documenti caricati + summary JSONB
│   ├── course_module.py                     # M1, M2, ... + meta Fase 2 (lessons_structure_*)
│   ├── course_lesson.py                     # M1.L1, ... + Fase 2 + content_* (Fase 3) + pdf_* (§7) + slides_*/slides_pdf_* (Fase 4) + speech_*/speech_pdf_* (Fase 5)
│   ├── course_taxonomy.py                   # 8 tassonomie
│   ├── pdf_template.py                      # template grafici A4 portrait (lezione testo + discorso)
│   ├── slide_template.py                    # template slide 16:9 (avatar video + export PDF slide, unificati in 0022)
│   └── language.py                          # lingue supportate
├── schemas/
│   ├── course.py                            # CourseOut, CourseCreateInput, CourseUpdateInput, ...
│   ├── course_architecture.py               # ArchitectureOutput + CRUD inputs (Module/Lesson) + meta tutte le fasi su CourseLessonOut
│   ├── course_lesson_structure.py           # Pydantic schemas Fase 2 (output AI, update CRUD)
│   ├── course_lesson_content.py             # Fase 3 — LessonContentRaw + LessonContentUpdateInput
│   ├── course_lesson_slides.py              # Fase 4 — LessonSlidesOutput + LessonSlideItem + new_assets + body field
│   ├── course_lesson_speech.py              # Fase 5 — LessonSpeechOutput + LessonSpeechSegment + slide_to_segments_map
│   ├── course_glossary.py                   # GlossaryRaw + GlossaryRegenerateInput
│   └── document_summary.py                  # mirror Pydantic dello schema Appendice A
├── services/
│   ├── course_service.py                    # list/get/create/update/delete corso, gestione documenti
│   ├── course_architecture_service.py       # prompt §4, materializzazione, request_generation, approve
│   ├── course_architecture_crud.py          # CRUD manuale moduli/lezioni + regenerate_module_lessons
│   ├── course_architecture_worker.py        # worker async per Fase 1 + ticker progresso
│   ├── course_lesson_structure_service.py   # Fase 2 — orchestrazione + materializzazione + approve
│   ├── course_lesson_structure_crud.py      # Fase 2 — edit manuale struttura lezione
│   ├── course_lesson_structure_worker.py    # Fase 2 — worker async PARALLELO + semaforo cap concorrenza
│   ├── course_lesson_content_service.py     # Fase 3 — orchestrazione + 10 validazioni §6.4 + approve
│   ├── course_lesson_content_crud.py        # Fase 3 — edit manuale content_raw + cleanup sync degli asset image rimossi (os.unlink dopo commit)
│   ├── course_lesson_content_worker.py      # Fase 3 — worker async PARALLELO + auto-trigger glossary
│   ├── course_lesson_slides_service.py      # Fase 4 — validazione §7.4 + materializzazione + approve + recompute_course_slides_status
│   ├── course_lesson_slides_crud.py         # Fase 4 — edit manuale slides_raw
│   ├── course_lesson_slides_worker.py       # Fase 4 — worker async PARALLELO (cap=3) + auto-retry + atomic claim
│   ├── course_lesson_slides_pdf_service.py  # Fase 4 — render PDF slide (slide_template) + Mermaid pre-render + slide split
│   ├── course_lesson_slides_pdf_worker.py   # Fase 4 — worker PDF slide PARALLELO (cap=2)
│   ├── course_lesson_speech_service.py      # Fase 5 — 8 validazioni §8.5 (incl. TTS-safety) + materializzazione + approve
│   ├── course_lesson_speech_crud.py         # Fase 5 — edit manuale speech_raw + auto-ricalcolo durata da word count × wpm
│   ├── course_lesson_speech_worker.py       # Fase 5 — worker async PARALLELO (cap=3) + pre-check slides ready
│   ├── course_lesson_speech_pdf_service.py  # Fase 5 — render PDF discorso (pdf_template) + format_timeline cumulativa
│   ├── course_lesson_speech_pdf_worker.py   # Fase 5 — worker PDF discorso PARALLELO (cap=2)
│   ├── course_module_pdf_service.py         # Bundle batch per-modulo: merge_module_pdfs (pypdf) + zip_module_pdfs (zipfile) per tutte e 3 le pipeline PDF
│   ├── openai_pricing.py                    # Pricing table per modello OpenAI + estimate_cost_usd + build_usage_dict (telemetria *_tokens)
│   ├── course_glossary_service.py           # §10.1 — sync glossario (regenerate + ensure_glossary_ready)
│   ├── course_lesson_pdf_service.py         # §7 — render HTML lezione testo + Playwright + materialize_lesson_pdf
│   ├── course_lesson_pdf_worker.py          # §7 — worker async PARALLELO (cap=2) + cancel-check
│   ├── course_document_worker.py            # worker async per pre-processing documenti
│   ├── document_extraction_service.py       # estrazione testo (PDF/DOCX/DOC/RTF/TXT/MD)
│   ├── openai_client.py                     # base httpx client + error hierarchy condivisa + apply_reasoning_effort
│   ├── openai_summarize_service.py          # Appendice A
│   ├── openai_architecture_service.py       # §4
│   ├── openai_lesson_structure_service.py   # §5 (system prompt + JSON schema strict)
│   ├── openai_lesson_content_service.py     # §6 (system prompt + JSON schema completo + addendum §9.3) — visual_assets ora restrittivo a format=mermaid
│   ├── openai_image_to_mermaid_service.py   # Vision API on-demand: trasforma un'immagine caricata in codice Mermaid (modello dedicato openai_image_to_mermaid_*)
│   ├── openai_lesson_slides_service.py      # §7 slides (system prompt + JSON schema strict + REGENERATION_SUFFIX §9.4)
│   ├── openai_lesson_speech_service.py      # §8 (system prompt + JSON schema strict + REGENERATION_SUFFIX §9.5 + WORDS_PER_MINUTE)
│   ├── openai_glossary_service.py           # §10.1 (10-30 termini, JSON schema strict)
│   └── openai_module_lessons_service.py     # gen lezioni single-modulo
├── templates/
│   ├── lesson_pdf.html.j2                   # §7 — PDF lezione testo (cover + body + asset blocks + KaTeX/Mermaid inline)
│   ├── lesson_slides_pdf.html.j2            # Fase 4 — PDF slide A4 portrait con tag "Lezione N" + slide split bullet/asset
│   └── lesson_speech_pdf.html.j2            # Fase 5 — PDF discorso A4 portrait raggruppato per slide con timeline cumulativa
├── api/v1/courses.py                        # router REST corsi (~50 endpoint, incluso Fase 4/5 + tre PDF)
└── alembic/versions/
    ├── 0009_course_taxonomy.py
    ├── 0010_courses.py
    ├── 0011_course_document_summary_meta.py
    ├── 0012_course_architecture.py
    ├── 0013_architecture_progress.py
    ├── 0014_lesson_structure.py
    ├── 0015_lesson_content.py               # Fase 3 + glossary
    ├── 0016_lesson_pdf_export.py            # §7 — 8 colonne pdf_* su course_lesson
    ├── 0017_didactic_setup_confirmed.py     # lock setup didattico (Tab 1+2)
    ├── 0018_stale_detection_timestamps.py   # *_modified_at per cascata staleness
    ├── 0019_lesson_slides.py                # Fase 4 — 11 colonne slides_* + speech_approved al CHECK course.status
    ├── 0020_lesson_slides_pdf.py            # Fase 4 — 8 colonne slides_pdf_*
    ├── 0021_pdf_template_kind.py            # discriminatore `kind` su pdf_templates (poi rolled back in 0022)
    ├── 0022_unify_slide_templates.py        # FK slides_pdf_template_id → slide_templates + drop pdf_templates.kind
    ├── 0023_lesson_speech.py                # Fase 5 — 11 colonne speech_* + speech_approved al CHECK course.status
    └── 0024_lesson_speech_pdf.py            # Fase 5 — 8 colonne speech_pdf_*
```

### Frontend

```
frontend/src/
├── api/courses.ts                                  # client REST corsi (namespace lessonsStructure + lessonsContent + lessonPdf + lessonSlides + lessonSlidesPdf + lessonSpeech + lessonSpeechPdf + lessonAssets + glossary). I 3 namespace PDF includono downloadModuleMerged + downloadModuleZip per il bundle per-modulo. Namespace lessonAssets: upload(file) + convertToMermaid(path) per il flow asset visivi Mermaid/image.
├── lib/
│   ├── staleness.ts                                # 7 helper di stale-detection cascata (struttura → contenuto → PDF → slide → PDF slide → discorso → PDF discorso)
│   └── slides.ts                                   # resolveAsset() per render asset slide (visual/table/equation/example/new_visual)
├── components/shared/
│   ├── MarkdownRenderer.tsx                        # render markdown lezioni (lesson-prose + math normalization + asset blocks)
│   ├── MermaidDiagram.tsx                          # lazy-load mermaid + render SVG
│   ├── RichTextEditor.tsx                          # TipTap WYSIWYG con bridge markdown (tiptap-markdown)
│   ├── TableEditor.tsx                             # griglia visuale per tabelle markdown
│   ├── LatexEditor.tsx                             # split textarea + KaTeX live preview + palette simboli
│   ├── MermaidEditor.tsx                           # split textarea + Mermaid live preview + 7 template
│   ├── ApprovalBadge.tsx                           # badge approvato cross-fase (architecture/module/lessonContent/lessonSlides/lessonSpeech)
│   └── StalenessAlert.tsx                          # alert "qualcosa a monte è cambiato" con CTA opzionale (6 kind: structure/content/pdf/slides/speech/speechPdf)
└── pages/org/courses/
    ├── CoursesListPage.tsx                         # lista
    ├── CourseEditorPage.tsx                        # editor con Tabs (8 voci: Base, Didattica, Documenti, Architettura, Struttura, Contenuti, Slide, Discorso)
    └── components/
        ├── CourseDocumentUploader.tsx              # drag&drop + lista + summary dialog trigger
        ├── DocumentSummaryDialog.tsx               # vista riassunto strutturato (sidebar nav + KaTeX per formule)
        ├── CourseArchitectureView.tsx              # vista architettura + CRUD inline
        ├── CourseLessonStructureView.tsx           # vista struttura lezioni Fase 2
        ├── CourseLessonContentView.tsx             # vista contenuti Fase 3 + UI export PDF (§7)
        ├── CourseLessonSlidesView.tsx              # Tab 7 — vista slide Fase 4 + PDF slide
        ├── CourseLessonSpeechView.tsx              # Tab 8 — vista discorso Fase 5 + PDF discorso
        ├── LessonContentView.tsx                   # render lezione (foglio bianco) per status ready/approved
        ├── LessonContentEditDialog.tsx             # editor user-friendly con RichText/Table/Latex/Mermaid + RefIdField + auto-sync refs + AddVisualAssetMenu (upload immagine / scrivi Mermaid) + bottone "Digitalizza in Mermaid" via Vision API + "Evidenzia dove usato" (scroll+flash su contenitore + token <code>)
        ├── LessonContentGenerateDialog.tsx         # dialog generate/regenerate Fase 3
        ├── LessonSlidesView.tsx                    # render read-only slide (card per slide)
        ├── LessonSlidesEditDialog.tsx              # editor manuale slide (slide list + bullets + body + new_assets)
        ├── LessonSlidesGenerateDialog.tsx          # dialog generate/regenerate Fase 4
        ├── LessonSlidesPdfExportDialog.tsx         # dialog export PDF slide (slide_templates)
        ├── LessonSpeechView.tsx                    # render read-only discorso raggruppato per slide con timeline cumulativa
        ├── LessonSpeechEditDialog.tsx              # editor segmenti discorso + auto-durata + TTS-safety inline
        ├── LessonSpeechGenerateDialog.tsx          # dialog generate/regenerate Fase 5
        ├── LessonSpeechPdfExportDialog.tsx         # dialog export PDF discorso (pdf_templates)
        ├── LessonStructureEditDialog.tsx           # editor manuale struttura lezione
        ├── LessonsStructureGenerateDialog.tsx      # dialog generate/regenerate Fase 2
        ├── LessonPdfExportDialog.tsx               # dialog export PDF lezione testo (pdf_templates)
        ├── ModuleEditDialog.tsx                    # form modulo
        ├── LessonEditDialog.tsx                    # form lezione + editor bibliografia
        ├── GenerateArchitectureDialog.tsx          # dialog di generazione/rigenerazione con hint
        ├── KeywordTagsInput.tsx                    # multi-input chip per argomenti chiave
        ├── TaxonomyTermSelect.tsx                  # select tassonomia con hierarchical
        ├── MemberSelect.tsx                        # select assegnatario
        └── CourseStatusBadge.tsx                   # badge stato
```
