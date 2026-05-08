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
- [09 — PDF export (§7)](09-pdf-export.md): export PDF per lezione tramite WeasyPrint (CSS Paged Media completo) + Jinja2 + markdown-it-py + Playwright (pre-render Mermaid → SVG) + latex2mathml, applicando il `pdf_templates` dell'organizzazione — §7 di `prompt_generazione_corsi.md`.

## Stato pipeline AI (5 fasi)

Il documento `prompt_generazione_corsi.md` definisce 5 fasi sequenziali. Stato corrente:

| Fase | Spec | Implementata? |
|---|---|---|
| **Pre-processing** | Appendice A | ✅ Iterazione A |
| **Fase 1: Architettura** (moduli + lezioni) | §4 | ✅ Iterazione B |
| **Fase 2: Struttura lezioni** | §5 | ✅ Iterazione C |
| **Fase 3: Contenuti + Glossario** | §6 + §10.1 | ✅ Iterazione D |
| **§7: Export PDF lezioni** | §7 | ✅ Iterazione E |
| **Fase 4: Slide** | §7bis | ⏳ |
| **Fase 5: Discorso** | §8 | ⏳ |

> **Nota terminologia**: `prompt_generazione_corsi.md` chiama §7 sia il
> documento PDF della lezione sia la generazione delle slide della
> presentazione. In questa codebase: §7 = export PDF (implementato),
> Fase 4 = generazione slide (TODO).

A complemento della pipeline AI è stato implementato un **CRUD manuale** delle lezioni e dei
moduli, con **generazione AI per singolo modulo** quando l'utente ne crea uno nuovo manualmente.

## State machine `course.status`

```
draft
  │
  │  POST /architecture/generate
  ▼
architecture_pending  ──► (worker) ──►  architecture_ready  ──► POST /architecture/approve ──►  architecture_approved
                                              ▲                                                      │
                                              └──────────────── POST /architecture/generate (rigenera)
                                                                                                     │
                                                                                                     │  POST /lessons-structure/generate-all (o per modulo)
                                                                                                     ▼
                                  lessons_structure_pending  ──► (worker parallelo) ──►  lessons_structure_ready  ──► POST /lessons-structure/approve-all (o per modulo)
                                              ▲                                                      │                                            │
                                              └────────── POST .../generate (rigenera) ────────────┘                                                ▼
                                                                                                                                       lessons_structure_approved
                                                                                                                                                    │
                                                                                                                                                    ▼
                                                                                                                                  (fasi successive: content, slides, speech, published)

archived          ◄── (terminal)
```

Lo `course.status` per Fase 2 è **derivato** dagli stati per-modulo:
- almeno 1 modulo in `pending|processing` → `lessons_structure_pending`
- TUTTI i moduli in `ready|approved` (e almeno 1 in `ready`) → `lessons_structure_ready`
- TUTTI i moduli in `approved` → `lessons_structure_approved`

Failure paths: ogni `*_pending` su errore → `draft` (per architettura) o `failed` per il singolo modulo (per Fase 2), con `*_error` populato. L'utente ri-tenta manualmente.

## Permessi correlati

Tutti sotto namespace `course:*`. Vedi [06 — Permissions](../06-permissions.md).

| Codice | Significato |
|---|---|
| `course:view` | Vedere i corsi assegnati (member) o tutti (org_admin/creator) |
| `course:create` | Creare un nuovo corso |
| `course:edit` | Modificare titolo, obiettivi, tassonomie, parametri, documenti, e fare CRUD manuale di moduli/lezioni |
| `course:delete` | Eliminare un corso (cascade documenti + architettura) |
| `course:assign` | Cambiare l'assegnatario |
| `course:generate` | Avviare la generazione architettura (Fase 1), approvarla, rigenerare le lezioni di un modulo via AI |
| `course_config:manage` | Modificare `OrganizationCourseSettings` |

## File chiave (mappa rapida)

### Backend

```
backend/app/
├── models/
│   ├── course.py                            # tabella course (status, snapshot CFU, architettura meta + glossary_*)
│   ├── course_document.py                   # documenti caricati + summary JSONB
│   ├── course_module.py                     # M1, M2, ... + meta Fase 2 (lessons_structure_*)
│   ├── course_lesson.py                     # M1.L1, ... + Fase 2 + content_* (Fase 3) + pdf_* (§7)
│   ├── course_taxonomy.py                   # 8 tassonomie
│   ├── pdf_template.py                      # template grafici org-scope (riusati da §7)
│   └── language.py                          # lingue supportate
├── schemas/
│   ├── course.py                            # CourseOut, CourseCreateInput, CourseUpdateInput, ...
│   ├── course_architecture.py               # ArchitectureOutput + CRUD inputs (Module/Lesson) + meta Fase 3 + §7 su CourseLessonOut
│   ├── course_lesson_structure.py           # Pydantic schemas Fase 2 (output AI, update CRUD)
│   ├── course_lesson_content.py             # Fase 3 — LessonContentRaw + LessonContentUpdateInput
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
│   ├── course_lesson_content_crud.py        # Fase 3 — edit manuale content_raw
│   ├── course_lesson_content_worker.py      # Fase 3 — worker async PARALLELO + auto-trigger glossary
│   ├── course_glossary_service.py           # §10.1 — sync glossario (regenerate + ensure_glossary_ready)
│   ├── course_lesson_pdf_service.py         # §7 — render HTML + Playwright + materialize_lesson_pdf
│   ├── course_lesson_pdf_worker.py          # §7 — worker async PARALLELO (cap=2) + cancel-check
│   ├── course_document_worker.py            # worker async per pre-processing documenti
│   ├── document_extraction_service.py       # estrazione testo (PDF/DOCX/DOC/RTF/TXT/MD)
│   ├── openai_client.py                     # base httpx client + error hierarchy condivisa
│   ├── openai_summarize_service.py          # Appendice A
│   ├── openai_architecture_service.py       # §4
│   ├── openai_lesson_structure_service.py   # §5 (system prompt + JSON schema strict)
│   ├── openai_lesson_content_service.py     # §6 (system prompt + JSON schema completo + addendum §9.3)
│   ├── openai_glossary_service.py           # §10.1 (10-30 termini, JSON schema strict)
│   └── openai_module_lessons_service.py     # gen lezioni single-modulo
├── templates/
│   └── lesson_pdf.html.j2                   # §7 — template Jinja2 per il PDF (cover + body + KaTeX/Mermaid CDN)
├── api/v1/courses.py                        # router REST corsi (~32 endpoint)
└── alembic/versions/
    ├── 0009_course_taxonomy.py
    ├── 0010_courses.py
    ├── 0011_course_document_summary_meta.py
    ├── 0012_course_architecture.py
    ├── 0013_architecture_progress.py
    ├── 0014_lesson_structure.py
    ├── 0015_lesson_content.py               # Fase 3 + glossary
    └── 0016_lesson_pdf_export.py            # §7 — 8 colonne pdf_* su course_lesson
```

### Frontend

```
frontend/src/
├── api/courses.ts                                  # client REST corsi (namespace lessonsStructure + lessonsContent + lessonPdf + glossary)
├── components/shared/
│   ├── MarkdownRenderer.tsx                        # render markdown lezioni (lesson-prose + math normalization + asset blocks)
│   ├── MermaidDiagram.tsx                          # lazy-load mermaid + render SVG
│   ├── RichTextEditor.tsx                          # TipTap WYSIWYG con bridge markdown (tiptap-markdown)
│   ├── TableEditor.tsx                             # griglia visuale per tabelle markdown
│   ├── LatexEditor.tsx                             # split textarea + KaTeX live preview + palette simboli
│   └── MermaidEditor.tsx                           # split textarea + Mermaid live preview + 7 template
└── pages/org/courses/
    ├── CoursesListPage.tsx                         # lista
    ├── CourseEditorPage.tsx                        # editor con Tabs (6 voci: Base, Didattica, Documenti, Architettura, Struttura, Contenuti)
    └── components/
        ├── CourseDocumentUploader.tsx              # drag&drop + lista + summary dialog trigger
        ├── DocumentSummaryDialog.tsx               # vista riassunto strutturato (sidebar nav + KaTeX per formule)
        ├── CourseArchitectureView.tsx              # vista architettura + CRUD inline
        ├── CourseLessonStructureView.tsx           # vista struttura lezioni Fase 2 (aggregate progress + per-modulo)
        ├── CourseLessonContentView.tsx             # Tab 6 — vista contenuti Fase 3 + UI export PDF (§7)
        ├── LessonContentView.tsx                   # render lezione (foglio bianco) per status ready/approved
        ├── LessonContentEditDialog.tsx             # editor user-friendly con RichText/Table/Latex/Mermaid + RefIdField + auto-sync refs
        ├── LessonContentGenerateDialog.tsx         # dialog generate/regenerate Fase 3 (4 modalità)
        ├── LessonStructureEditDialog.tsx           # editor manuale struttura lezione (4 sezioni)
        ├── LessonsStructureGenerateDialog.tsx      # dialog generate/regenerate con hint (4 modalità)
        ├── ModuleEditDialog.tsx                    # form modulo (counter, hint, kbd shortcut)
        ├── LessonEditDialog.tsx                    # form lezione + editor bibliografia
        ├── GenerateArchitectureDialog.tsx          # dialog di generazione/rigenerazione con hint
        ├── KeywordTagsInput.tsx                    # multi-input chip per argomenti chiave
        ├── TaxonomyTermSelect.tsx                  # select tassonomia con hierarchical
        ├── MemberSelect.tsx                        # select assegnatario
        └── CourseStatusBadge.tsx                   # badge stato
```
