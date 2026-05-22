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
- [12 — Lesson video (Fase 6)](12-lesson-video.md): generazione del video MP4 della lezione — TTS XTTS-v2 su RunPod GPU + rendering slide Playwright + encoding ffmpeg — §9.
- [13 — Avatar video (Fase 6b)](13-avatar-video.md): scheda "Video con Avatar" — lip-sync MuseTalk (RunPod GPU + Cloudflare R2) sovrapposto al video MP4 della lezione — §9b.
- [14 — Assessment lesson](14-assessment-lesson.md): lezione di **verifica delle competenze** — l'ultima lezione di ogni modulo quando la verifica finale è attiva.

## Stato pipeline (5 fasi AI + video + verifica)

Il documento `prompt_generazione_corsi.md` definisce 5 fasi AI sequenziali. A
valle sono state aggiunte la generazione video (Fase 6 / 6b, non-AI) e la
lezione di verifica. Stato corrente:

| Fase | Spec | Stato |
|---|---|---|
| **Pre-processing** | Appendice A | ✅ |
| **Fase 1: Architettura** (moduli + lezioni) | §4 | ✅ |
| **Fase 2: Struttura lezioni** | §5 | ✅ |
| **Fase 3: Contenuti + Glossario** | §6 + §10.1 | ✅ |
| **§7: Export PDF lezione testo** | §7 | ✅ |
| **Fase 4: Slide** (incl. PDF export slide) | §7 (slides) | ✅ |
| **Fase 5: Discorso** (incl. PDF export discorso) | §8 + §9.5 | ✅ |
| **Fase 6: Video MP4** (TTS RunPod + slide + ffmpeg) | §9 | ✅ |
| **Fase 6b: Video con Avatar** (lip-sync MuseTalk) | §9b | ✅ |
| **Verifica competenze** (lezione di assessment) | — | ✅ |

> **Nota terminologia**: la spec chiama §7 sia il documento PDF della
> lezione testo sia la generazione delle slide della presentazione. In
> questa codebase: §7 = export PDF lezione testo (`pdf_*`), Fase 4 =
> generazione slide (`slides_*`) + relativo PDF (`slides_pdf_*`),
> Fase 5 = generazione discorso (`speech_*`) + relativo PDF
> (`speech_pdf_*`), Fase 6 = video MP4 (`video_*`), Fase 6b = video con
> avatar (`avatar_video_*`).

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

> **Fase 6 / 6b non sono fasi di `course.status`**: il video MP4 e il video
> con avatar sono per-lezione, hanno il proprio ciclo di stato
> (`video_status`, `avatar_video_status`) e non concorrono a
> `course.status`.

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

I worker AI **non** toccano i `*_modified_at` (solo i CRUD manuali lo fanno), così la rigenerazione AI non si auto-segnala come stale. Il video (Fase 6) e il video con avatar (Fase 6b) calcolano la propria staleness lato BE confrontando `video_generated_at` / `avatar_video_generated_at` con gli upstream (discorso/slide per il video; il video della lezione per il video con avatar).

## Permessi correlati

Tutti sotto namespace `course:*`. Vedi [06 — Permissions](../06-permissions.md).

| Codice | Significato |
|---|---|
| `course:view` | Vedere i corsi a sé assegnati |
| `course:view_all` | Visibilità cross-utente (vedi anche i corsi assegnati ad altri membri). Additivo a `view`. |
| `course:create` | Creare un nuovo corso con setup didattico completo |
| `course:save_draft` | Salvare uno stub di corso senza setup didattico (titolo + CFU + assegnatario) — pensato per admin che seminano bozze |
| `course:edit` | Modificare titolo, obiettivi, tassonomie, parametri, documenti, e fare CRUD manuale di moduli/lezioni e dei payload AI (struttura, contenuto, slide, discorso, verifica). Non implica visibilità cross-utente. |
| `course:delete` | Eliminare un corso (cascade documenti + architettura) |
| `course:assign` | Cambiare l'assegnatario |
| `course:generate` | Avviare la generazione a tutte le fasi (1-6/6b) + export PDF (lezione/slide/discorso, anche batch per-modulo) + approvare i payload generati |
| `course_config:manage` | Modificare `OrganizationCourseSettings` |

## File chiave (mappa rapida)

### Backend

```
backend/app/
├── models/
│   ├── course.py                            # tabella course (status, snapshot CFU, architettura meta + glossary_* + video_language_code)
│   ├── course_document.py                   # documenti caricati + summary JSONB
│   ├── course_module.py                     # M1, M2, ... + meta Fase 2 (lessons_structure_*)
│   ├── course_lesson.py                     # M1.L1, ... + is_assessment + Fase 2 + content_* + pdf_* + slides_*/slides_pdf_* + speech_*/speech_pdf_* + video_* (Fase 6) + avatar_video_* (Fase 6b)
│   ├── course_taxonomy.py                   # 8 tassonomie
│   ├── avatar.py / avatar_clip.py           # avatar utente + clip MiniMax + parametri musetalk_* (Fase 6b)
│   ├── pdf_template.py                      # template grafici A4 portrait (lezione testo + discorso)
│   ├── slide_template.py                    # template slide 16:9 (avatar video + export PDF slide, unificati in 0022)
│   └── language.py                          # lingue supportate
├── schemas/
│   ├── course.py / course_architecture.py / course_lesson_structure.py
│   ├── course_lesson_content.py             # Fase 3 — LessonContentRaw + Assessment* (LessonAssessmentOutput, AssessmentMC/Open*)
│   ├── course_lesson_slides.py / course_lesson_speech.py / course_glossary.py
│   ├── course_lesson_video.py               # Fase 6 — LessonVideoStatusOut, LessonVideoBatchOut
│   ├── course_lesson_avatar_video.py        # Fase 6b — LessonAvatarVideoStatusOut, LessonAvatarVideoBatchOut
│   └── avatar.py                            # AvatarOut + AvatarMusetalkParamsUpdate
├── services/
│   ├── course_*_service/_crud/_worker.py    # Fasi 1-5 (vedi doc 03-11)
│   ├── course_lesson_pdf_* / _slides_pdf_* / _speech_pdf_* / course_module_pdf_service.py   # 3 pipeline PDF + bundle modulo
│   ├── openai_*_service.py                  # client OpenAI per ogni fase AI (incl. generate_lesson_assessment)
│   ├── course_lesson_video_service.py       # Fase 6 — API pubblica video (request/cancel/status/DTO)
│   ├── course_lesson_video_worker.py        # Fase 6 — worker async (TTS + slide + encoding)
│   ├── runpod_tts_client.py                 # Fase 6 — client TTS XTTS-v2 su RunPod GPU
│   ├── lesson_audio_cache.py                # Fase 6 — cache su disco dell'audio TTS per-segment
│   ├── lesson_slides_video_render_service.py # Fase 6 — render Playwright slide → PNG 1980×1400
│   ├── lesson_video_compose_service.py      # Fase 6 — composizione MP4 via ffmpeg
│   ├── course_lesson_avatar_video_service.py # Fase 6b — API pubblica video con avatar
│   └── course_lesson_avatar_video_worker.py # Fase 6b — worker async (downscale clip + MuseTalk + overlay)
├── musetalk_client/                         # Fase 6b — client MuseTalk vendored (copia verbatim, NON modificare)
│   └── scripts/client/{synth_random_lipsync,runpod_client,video_assembler,clip_manifest}.py
├── templates/                               # lesson_pdf / lesson_slides_pdf / lesson_speech_pdf .html.j2
├── api/v1/courses.py                        # router REST corsi (~65 endpoint: Fasi 1-6/6b + 3 PDF + assessment)
├── api/v1/me_avatar.py                      # avatar utente + PATCH /me/avatar/musetalk-params
└── alembic/versions/
    ├── 0009 … 0024                          # foundation corsi + Fasi 1-5 + 3 PDF (vedi doc 10-alembic.md)
    ├── 0025_lesson_video.py                 # Fase 6 — 9 colonne video_* su course_lesson
    ├── 0026_avatar_tts_latents_and_course_video_language.py  # course.video_language_code (+ cache latenti XTTS, poi rimossa)
    ├── 0027_drop_avatar_tts_latents.py      # rimuove la cache latenti XTTS (TTS migrato su RunPod GPU)
    ├── 0028_assessment_lesson.py            # flag is_assessment su course_lesson
    └── 0029_avatar_video.py                 # Fase 6b — 8 colonne avatar_video_* + 3 colonne musetalk_* su avatars
```

### Frontend

```
frontend/src/
├── api/courses.ts                                  # client REST corsi (+ namespace lessonVideo, lessonAvatarVideo)
├── api/avatars.ts                                  # myAvatarApi (+ updateMusetalkParams)
├── hooks/
│   ├── useLessonVideo.ts                           # Fase 6 — query/mutation status video
│   └── useLessonAvatarVideo.ts                     # Fase 6b — query/mutation status video con avatar
├── lib/staleness.ts                                # helper di stale-detection cascata
└── pages/org/courses/
    ├── CourseEditorPage.tsx                        # editor con Tabs (10 voci: Base, Didattica, Documenti, Architettura, Struttura, Contenuti, Slide, Discorso, Video, Video con avatar)
    └── components/
        ├── CourseArchitectureView / CourseLessonStructureView / CourseLessonContentView / CourseLessonSlidesView / CourseLessonSpeechView.tsx
        ├── CourseLessonVideoView.tsx               # Fase 6 — scheda Video
        ├── CourseLessonAvatarVideoView.tsx         # Fase 6b — scheda Video con avatar
        ├── LessonAssessmentView.tsx                # vista verifica competenze (read-only)
        ├── LessonAssessmentEditDialog.tsx          # editor verifica competenze
        └── … (dialog generate/edit per ogni fase, vedi doc 06-frontend.md)
```
