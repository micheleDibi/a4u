# 14 — Assessment lesson (verifica delle competenze)

Quando il corso ha la **verifica di apprendimento finale** attiva,
l'**ultima lezione di ogni modulo** non è una lezione didattica ma una
**verifica delle competenze**: un elenco di domande a scelta multipla +
domande aperte sugli argomenti del modulo, con chiave di correzione.

## 1. Quando viene creata

Discriminante: `course.assessment_lesson_enabled` (snapshot da
`OrganizationCourseSettings` alla creazione del corso) **AND**
`course.lessons_per_module >= 2`.

- Se attivo: per ogni modulo, delle `lessons_per_module` lezioni totali,
  **`lessons_per_module - 1` sono didattiche + 1 è di verifica** (ultima
  posizione del modulo). Il totale per modulo resta invariato.
- Se `lessons_per_module == 1`: nessuna verifica (un modulo di sola
  verifica non ha senso).

L'AI dell'architettura (Fase 1) genera solo le lezioni didattiche
(`lessons_per_module - 1`); il codice di **materializzazione**
(`course_architecture_service`) **appende** un `CourseLesson` di verifica
con `is_assessment=True`, titolo standard localizzato e ultima posizione.
Lo JSON schema dell'architettura non cambia.

## 2. Data model

Migration **`0028_assessment_lesson.py`** — un solo flag su
`course_lesson`:

| Colonna | Tipo | Note |
|---|---|---|
| `is_assessment` | BOOLEAN | default `false`, NOT NULL — zero impatto sulle righe esistenti |

La lezione di verifica **riusa** la colonna `content_raw` (polimorfica) e
l'intero ciclo `content_status` della Fase 3: così entra nella batch
"Genera tutti" contemporaneamente alle lezioni didattiche. Il
discriminante a runtime è `lesson.is_assessment` + la chiave
`is_assessment` dentro il JSON di `content_raw`.

## 3. Schema del payload

`backend/app/schemas/course_lesson_content.py`:

- `AssessmentMCOption` — `{option_id ("A".."D"), text}`.
- `AssessmentMCQuestion` — `{question_id, question_type:"multiple_choice",
  text, options[2..6], correct_option_id}`.
- `AssessmentOpenQuestion` — `{question_id, question_type:"open", text,
  expected_answer}` (traccia di risposta attesa, per la correzione).
- `LessonAssessmentOutput` — `{lesson_id, lesson_title,
  is_assessment:true, multiple_choice_questions[], open_questions[]}`.
- `LessonAssessmentUpdateInput` — liste opzionali, per l'editing manuale.

Il numero di domande deriva dallo snapshot del corso:
`multiple_choice_questions_count` e `open_questions_count`.

## 4. Generazione (Fase 3)

La verifica si genera **in parallelo** alle lezioni didattiche, nello
stesso worker `course_lesson_content_worker`, che branch-a su
`lesson.is_assessment`:

- `course_lesson_content_service.build_assessment_user_prompt(course,
  lesson)` — prompt che deriva gli argomenti del modulo da
  `learning_objectives` + `mandatory_topics` delle lezioni sorelle
  didattiche (struttura Fase 2), più i conteggi domande.
- `openai_lesson_content_service.generate_lesson_assessment(...)` —
  chiamata AI con un system prompt dedicato: output = verifica
  competenze; **vietato citare lezioni specifiche** (le domande sono
  autoconsistenti); MC con una sola opzione corretta; aperte con
  `expected_answer`; lingua del corso.
- `course_lesson_content_service.materialize_lesson_assessment(...)` —
  validazioni: `question_id` unici, ogni MC ha `correct_option_id` che
  referenzia un'opzione esistente, esattamente una corretta. Scrive
  `content_raw`, `content_status='ready'`.

La verifica **partecipa** allo stato Fase 3 del corso
(`_recompute_course_content_status` la include).

## 5. Esclusione da Fasi 4/5/6 e PDF

La lezione di verifica non ha contenuto didattico: è **esclusa** da
slide (Fase 4), discorso (Fase 5), video (Fasi 6/6b) e da tutti gli
export PDF.

> **Punto critico**: `_recompute_course_slides_status` e
> `_recompute_course_speech_status` devono **escludere** le lezioni
> `is_assessment` dal calcolo, altrimenti il loro `slides_status='empty'`
> permanente bloccherebbe per sempre `slides_approved`/`speech_approved`.

I worker e le API delle Fasi 4/5/6 saltano le lezioni `is_assessment`
(non eleggibili); le rotte per-lezione rispondono `409`
(`lesson_is_assessment_not_eligible`).

## 6. Editing manuale

- `PATCH /orgs/{org}/courses/{cid}/lessons/{lid}/assessment` — body
  `LessonAssessmentUpdateInput`; guard `lesson.is_assessment` (altrimenti
  `409 lesson_not_assessment`); richiede status `ready`/`approved`.
- `course_lesson_content_crud.update_lesson_assessment(...)` — applica le
  modifiche con le stesse validazioni MC (rifiuta MC con 0 o 2 corrette).

## 7. Frontend

- **Scheda Contenuti** (`CourseLessonContentView.tsx`): per le righe
  `is_assessment` mostra un badge "Verifica", nasconde la UI di export
  PDF, apre `LessonAssessmentEditDialog` per la modifica, e nel corpo
  espanso rende `LessonAssessmentView` (domande MC con opzione corretta
  evidenziata + domande aperte con risposta attesa). È disponibile un
  **export CSV** delle domande (generato lato client).
- **Scheda Struttura lezioni**: la lezione-verifica ha un badge
  "Verifica competenze" (analogo al badge "Introduttiva").
- `LessonAssessmentView.tsx` / `LessonAssessmentEditDialog.tsx` — vista
  ed editor dedicati.

## Note

- La feature vale dalla generazione architettura in poi: i corsi con
  architettura già generata prima della feature hanno l'ultima lezione
  normale (serve rigenerare l'architettura per averla come verifica).
- `is_assessment` default `false` → zero impatto sui dati esistenti.
- La piattaforma genera solo i contenuti della verifica, non gestisce la
  fruizione da parte degli studenti.
