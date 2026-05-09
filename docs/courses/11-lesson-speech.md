# 11 — Lesson speech (Fase 5)

Generazione AI del **discorso temporizzato** che il docente leggerà (e
che un futuro TTS pronuncerà nel video) per ciascuna lezione, suddiviso
in segmenti sincronizzati alle slide. Pipeline parallela (cap=3 di
default) con auto-retry trasparente. Riferimento spec: §8 + §9.5 di
`prompt_generazione_corsi.md`.

## Cosa fa

Per ogni lezione con `slides_status ∈ {ready, approved}`, una chiamata
OpenAI produce il parlato suddiviso in segmenti, ciascuno ancorato a un
`slide_id` e con `estimated_duration_seconds`. Vincoli forti:

- somma `estimated_duration_seconds` ≈ `lesson_duration_minutes × 60`
  (tolleranza ±5%)
- ogni slide di Fase 4 ha **almeno un segmento**
- testo TTS-friendly (no markdown, no abbreviazioni note, no LaTeX)
- velocità di parlato: **130 wpm IT / 150 wpm EN** (default 130)

Il discorso è **prosa naturale**, non una lista di bullet. Le
transizioni tra slide sono esplicite ("Passiamo ora a vedere...").

## Stato per-lezione

`course_lesson.speech_status` ∈
`empty → pending → processing → ready → approved | failed`.

Auto-retry trasparente prima di `failed`: 5 tentativi
(`COURSE_LESSON_SPEECH_AUTO_RETRY_MAX`). Cause frequenti di retry:
durata fuori range, TTS-safety violazione (l'AI mette occasionalmente
`\frac` o `*` nel testo), LaTeX inline.

`course.status` (`speech_pending` / `speech_ready` / `speech_approved`)
è derivato dagli stati per-lezione (`_recompute_course_speech_status`).

## Pre-condizione

`lesson.slides_status ∈ {ready, approved}` AND `lesson.slides_raw`
valorizzato. Se la pre-condizione manca, il worker fa fail terminale
**non recuperabile** con messaggio "Genera prima le slide".

A monte servono anche `content_raw` (Fase 3, sempre presente se Fase 4
è ready/approved): il prompt include sia il testo della lezione sia
le slide come contesto.

## Convenzioni words-per-minute

Il modulo `openai_lesson_speech_service.py` espone:

```python
WORDS_PER_MINUTE: dict[str, int] = {
    "it": 130,
    "en": 150,
    "default": 130,
}

def words_per_minute(language_code: str) -> int:
    """Ritorna i wpm per il `language_code` (case-insensitive, primi 2 char).
    Fallback a `WORDS_PER_MINUTE['default']`."""
```

Riusato sia nel system prompt sia nella validazione (`materialize_lesson_speech` regola §8.5 punto 4) sia nell'editor frontend (`LessonSpeechEditDialog` per il bottone "Auto" durata).

## Flusso di generazione

```
[utente] POST /lessons/{id}/speech/generate (con hint opzionale)
  └─► course_lesson_speech_service.request_lesson_speech_generation
       ├─► validate course.status ∈ {slides_ready, slides_approved, speech_*}
       ├─► validate lesson.slides_status ∈ {ready, approved}
       ├─► lesson.speech_status = "pending"
       ├─► lesson.speech_regeneration_hint = hint
       ├─► reset speech_pdf_status='empty' (PDF discorso obsoleto)
       └─► audit course.lesson.speech.generate.requested

[worker] course_lesson_speech_worker._tick (ogni 4s)
  └─► SELECT lessons WHERE speech_status='pending'
      ├─► claim atomico in _inflight
      └─► fire-and-forget _bound_process(lesson_id)

[worker task] _process_one
  ├─► reload lesson + course (eager load completo)
  ├─► pre-check slides_status (terminal fail se non ready/approved)
  ├─► lesson.speech_status = "processing", attempts++
  ├─► build_user_prompt(course, lesson) = §8.3 + §9.5 se rigenerazione
  │    (include content_raw + slides_raw + bibliografia + hint)
  ├─► progress ticker (background) ease-out 15→85%
  ├─► openai_lesson_speech_service.generate_lesson_speech(...)
  │    ├─► system prompt §8.2 + REGENERATION_SUFFIX se rigenerazione
  │    ├─► response_format json_schema strict (§8.4)
  │    └─► return (LessonSpeechOutput, usage)
  ├─► cancel-check (utente potrebbe aver cancellato)
  ├─► materialize_lesson_speech (8 validazioni §8.5 — vedi sotto)
  ├─► lesson.speech_raw = output
  ├─► lesson.speech_tokens = usage
  ├─► lesson.speech_status = "ready", progress = 100
  ├─► _recompute_course_speech_status(course)
  └─► audit course.lesson.speech.generated
```

## Le 8 validazioni §8.5

Implementate in `course_lesson_speech_service.materialize_lesson_speech`:

1. **`output.lesson_id == lesson.lesson_code`** (sanity check ID).
2. **Tutti i `slide_id` esistono** in `lesson.slides_raw["slides"]`.
3. **Ogni slide di Fase 4 ha almeno un segmento associato**: l'insieme dei `referenced_slide_ids` (dai `speech_segments[*].slide_id`) deve coincidere con `valid_slide_ids` di Fase 4.
4. **`segment_id` univoci** a livello di lezione.
5. **`sum(estimated_duration_seconds) ∈ [target × 0.95, target × 1.05]`** dove `target = course.lesson_duration_minutes × 60`. Hard error fuori range.
6. **Word count coerente con duration**: `est_word_count ≈ est_duration × wpm/60` con tolleranza ±15%. Soft warning (l'AI può aver stimato male — non blocca).
7. **`slide_to_segments_map` coerente con `speech_segments`**:
   - ogni `segment_id` listato esiste in `speech_segments`
   - nessun segmento è orfano
   - per ogni slide, `slide_total_duration_seconds == sum(durate dei suoi segmenti)`
8. **TTS-safety** (`_validate_tts_safety`):
   - **Caratteri proibiti**: `*`, `_`, `` ` ``, `#`, `\`, `$`
   - **Abbreviazioni proibite** (case-insensitive con word boundary): `es.`, `etc.`, `ca.`, `p.es.`, `i.e.`, `e.g.`
   - **Pattern LaTeX**: `\frac`, `\sum`, `\int`, `\cdot`, `\alpha`, `\beta`, `\gamma`, `\delta`, `\sqrt`, `\infty`, `\partial`, `\nabla`, `\leq`, `\geq`, `\approx`, ecc. (lista completa nel sorgente)
   
   Hard error con elenco delle violazioni e il `segment_id` colpito.

## OpenAI service — `openai_lesson_speech_service.py`

System prompt (§8.2) tradotto fedelmente con regole su:

- **TTS-friendly**: no abbreviazioni, espandere acronimi alla prima occorrenza, scrivere cifre (TTS le pronuncia bene), no LaTeX (descrivere a voce), no markdown
- **Struttura**: per ogni slide ≥1 segmento, transizioni esplicite tra slide
- **Dimensionamento**: `sum(durations) = minuti_per_lezione × 60` ±5%, slide titolo/agenda 15-30s, concept densa 120-180s
- **Contenuto**: prosa naturale, registro adatto a `ruolo_docente` + livello EQF, lezione introduttiva con tono di benvenuto

Regeneration suffix (§9.5):
> ATTENZIONE: stai RIGENERANDO il discorso di una lezione. Considera la
> versione precedente e il feedback del docente.
> Le slide (Fase 4) sono invariate. Mantieni gli stessi slide_id.
> Se il feedback NON tocca la durata, mantienila uguale a
> `minuti_per_lezione * 60`. Mantieni le regole TTS-friendly.

Settings env-driven:

| Env | Default | Significato |
|---|---|---|
| `OPENAI_LESSON_SPEECH_MODEL` | `gpt-5.5` | Modello reasoning per Fase 5 |
| `OPENAI_LESSON_SPEECH_MAX_TOKENS` | `16000` | `max_completion_tokens` (output prosa ~6-12k + reasoning) |
| `OPENAI_LESSON_SPEECH_REASONING_EFFORT` | `medium` | `minimal/low/medium/high` |
| `COURSE_LESSON_SPEECH_POLL_INTERVAL_SECONDS` | `4` | Tick worker |
| `COURSE_LESSON_SPEECH_MAX_CONCURRENCY` | `3` | Lezioni in parallelo |
| `COURSE_LESSON_SPEECH_AUTO_RETRY_MAX` | `5` | Tentativi prima di fail terminale |

## Schema output (§8.4)

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
      "text": "Benvenuti, in questa lezione esploreremo gli algoritmi di clustering...",
      "estimated_duration_seconds": 25,
      "delivery_notes": "Tono caloroso, pausa breve dopo benvenuti."
    },
    {
      "segment_id": "SEG002",
      "slide_id": "S02",
      "text": "Passiamo ora a vedere la struttura di k-means...",
      "estimated_duration_seconds": 90,
      "delivery_notes": ""
    }
  ],
  "slide_to_segments_map": [
    {
      "slide_id": "S01",
      "segment_ids": ["SEG001"],
      "slide_total_duration_seconds": 25
    },
    {
      "slide_id": "S02",
      "segment_ids": ["SEG002"],
      "slide_total_duration_seconds": 90
    }
  ]
}
```

## CRUD manuale — `course_lesson_speech_crud.py`

Edit del `speech_raw` finché la lezione è in `ready`/`approved`. Edit
non degrada lo stato. Hard fail per:

- `segment_id` duplicati o vuoti
- `slide_id` orfani (non presenti in `slides_raw`)
- testo segmento vuoto
- `estimated_duration_seconds` < 1
- TTS-safety violazione (regola §8.5 punto 5/8 sempre attiva)
- somma durate fuori da [target × 0.95, target × 1.05]
- almeno una slide senza segmento associato
- `slide_to_segments_map` inconsistente

**Auto-ricalcolo durata**: nell'editor frontend, il bottone "Auto"
ricalcola `estimated_duration_seconds` da `word_count(text) × 60 / wpm`
con `wpm` derivato da `course.language_code`. Lato backend il
ricalcolo automatico avviene in `update_lesson_speech` solo quando il
caller lascia `estimated_total_word_count` obsoleto (lo derivia da
`text` dei segmenti e applica wpm).

`PATCH /lessons/{id}/speech` setta `speech_modified_at = now()` per
stale-detection del PDF discorso downstream.

## Frontend — `CourseLessonSpeechView.tsx`

Tab "Discorso" (ottavo tab del wizard). Visibile in `mode === "edit"` da
`course.status` ∈ `{slides_ready, slides_approved, speech_pending,
speech_ready, speech_approved, ...}`.

Componenti:
- **Header**: aggregate progress + ETA, CTA batch (Genera tutto /
  Rigenera / Genera mancanti / Approva tutto / Annulla, + Esporta PDF)
- **Module card** per ciascun modulo
- **Lesson row** espandibile con primary CTA + kebab + stale alert
- **Expanded**: `<LessonSpeechView speech={speech_raw} slides={slides_raw} />`
  raggruppato per slide con timeline cumulativa `[mm:ss — mm:ss]` e
  delivery notes (vedi sotto)

### `LessonSpeechView.tsx` — viewer read-only

Lista verticale **raggruppata per slide** (mirror del PDF discorso). Per
ciascuna entry di `slide_to_segments_map`:

- Header: slide_number + titolo slide (lookup da `slides_raw`) + durata totale slide
- Lista segmenti in ordine:
  - Range temporale cumulativo `[mm:ss — mm:ss]` calcolato lato FE sommando le durate precedenti
  - Durata segmento `Ns`
  - Testo segmento (paragrafo, font serif per leggibilità)
  - Note al docente (italic, accent, se non vuote)

Helper `formatMmSs(seconds: number): string` per conversione `123 → "02:03"`.

### `LessonSpeechEditDialog.tsx` — editor manuale

Layout: lista slide (lookup da `slidesRaw`). Per ciascuna slide, lista
dei segmenti che la coprono. Per ciascun segmento:

- `segment_id` (read-only chip)
- Selettore `slide_id` (popolato dalle slide di Fase 4)
- Textarea `text` con **warning chip TTS-safety inline** se rileva
  pattern proibiti (regex client-side duplicata dal BE per UX immediata)
- Input `estimated_duration_seconds` (number) con bottone **"Auto"** che
  ricalcola da `word_count(text) × 60 / wpm`
- Textarea `delivery_notes` (1 riga, opzionale)
- Bottone "Rimuovi"
- Bottone "Aggiungi segmento" sotto ciascuna slide

Footer dialog: warning verde se `sum(durations) ∈ [target × 0.95, target × 1.05]`, warning ambra altrimenti con valori `actual / low / high / target`.

Submit ricostruisce automaticamente `slide_to_segments_map` e i totali derivati (`estimated_total_duration_seconds`, `estimated_total_word_count`) prima di chiamare l'API.

## File rilevanti

```
backend/app/services/openai_lesson_speech_service.py    # OpenAI call + JSON schema + REGENERATION_SUFFIX + WORDS_PER_MINUTE
backend/app/services/course_lesson_speech_worker.py     # worker async + auto-retry + atomic claim _inflight
backend/app/services/course_lesson_speech_service.py    # orchestrazione + materialize + 8 validazioni §8.5 + validate_tts_safety
backend/app/services/course_lesson_speech_crud.py       # PATCH manuale + validazioni complete
backend/app/schemas/course_lesson_speech.py             # LessonSpeechOutput + LessonSpeechSegment + LessonSlideSegmentsMapEntry
backend/app/api/v1/courses.py                           # 7 endpoint Fase 5 (generate / generate-all / generate-missing / cancel-all / approve / approve-all / patch)
frontend/src/api/courses.ts                             # coursesApi.lessonSpeech + tipi
frontend/src/pages/org/courses/components/
  ├── CourseLessonSpeechView.tsx                        # vista batch + per-lezione + integrazione PDF
  ├── LessonSpeechView.tsx                              # render read-only raggruppato per slide con timeline
  ├── LessonSpeechEditDialog.tsx                        # editor manuale + auto-durata + TTS-safety inline
  └── LessonSpeechGenerateDialog.tsx                    # dialog generate/regenerate (4 modes)
```

## Forward-compat: pipeline TTS+video

Lo schema `speech_segments` è esattamente quello richiesto dal futuro
pipeline TTS+video. Quando aggiungeremo l'audio:

- `text` → input al modello TTS (es. ElevenLabs, Azure Speech)
- `slide_to_segments_map[i].slide_total_duration_seconds` → durata di
  permanenza della slide nel video
- la durata reale del TTS può differire da `estimated_duration_seconds`
  (la stima del modello è approssimata) → usare la durata reale come
  ground truth per il timing del video
- considerare fade ~0.3s tra slide brevi per fluidità visiva

## Errori comuni

Vedi tabella completa in [05 — API reference](05-api-reference.md). Più
frequenti:

- `lesson_speech_duration_out_of_range` — durata totale fuori ±5%. Con `regeneration_hint` chiedere all'AI di adattare.
- `lesson_speech_tts_unsafe` — l'AI ha messo `\frac` o `*` nel testo. Auto-retry risolve di solito.
- `lesson_speech_uncovered_slides` — qualche slide non ha segmenti. Edit manuale per aggiungerli, o rigenerare.
- `lesson_slides_not_ready_for_speech` — Fase 4 non completa, tornare alle slide.
- `OpenAILessonSpeechError` con finish_reason=length — output troncato per lezioni lunghe (90 min ≈ 11700 parole IT). Alzare `OPENAI_LESSON_SPEECH_MAX_TOKENS`.
