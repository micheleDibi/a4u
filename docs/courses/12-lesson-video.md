# 12 — Lesson video (Fase 6 §9)

Generazione del **video MP4 della lezione**: le slide della Fase 4
diventano fotogrammi, il discorso della Fase 5 diventa audio parlato
(voce clonata dell'avatar assegnatario) e ffmpeg compone il filmato
finale. È la prima fase non-AI della pipeline (nessuna chiamata OpenAI):
orchestra **TTS su GPU RunPod + rendering Playwright + encoding ffmpeg**.

> Spec di riferimento: §9 di `prompt_generazione_corsi.md`.

## 1. Pipeline

```
speech_status='approved' ─┐
slides_status='approved' ─┼─► video_status: empty → pending → processing → ready
Avatar.audio_path        ─┘                                         │
RUNPOD_TTS configurato                                              ▼
                                              /uploads/lesson_videos/{course}/{lesson}.mp4
```

Il worker `course_lesson_video_worker` esegue 3 fasi, con cancel-check
tra una e l'altra:

| Fase | Progress | Cosa fa |
|---|---|---|
| 1. TTS | 0 → 60 % | Sintesi vocale di ogni `LessonSpeechSegment` su RunPod GPU (voce clonata da `Avatar.audio_path`). Streaming incrementale → progress per-segment. Risultato in cache su disco. |
| 2. Slide PNG | 60 → 80 % | Render Playwright delle slide a 1980×1400 (riusa il template PDF slide). |
| 3. Encoding | 80 → 100 % | Per-slide MP4 (`-loop 1` immagine + audio) e concat finale, via ffmpeg. |

Output: `/uploads/lesson_videos/{course_id}/{lesson_id}.mp4` — 1980×1400
H.264 + AAC, servito da `StaticFiles` con HTTP Range nativo. Il path è
stabile: una rigenerazione sovrascrive lo stesso file.

## 2. Pre-condizioni

Verificate dopo il claim atomico, nel worker (`_process_one`):

- `lesson.speech_status == 'approved'` — il discorso deve essere approvato.
- `lesson.slides_status == 'approved'` — le slide devono essere approvate.
- L'**avatar dell'assegnatario del corso** (`course.assignee_user_id` →
  `Avatar.user_id`) deve avere un `audio_path` (campione vocale) presente
  su filesystem.
- Servizio TTS RunPod configurato (`RUNPOD_API_KEY` +
  `RUNPOD_TTS_ENDPOINT_ID`).

Le **lezioni di verifica** (`is_assessment=True`) non sono mai eleggibili
(vedi [14 — Assessment lesson](14-assessment-lesson.md)).

Se una pre-condizione manca, il worker porta `video_status='failed'` con
errore non recuperabile (no auto-retry); l'API la rifiuta a monte con
`409` e un `code` specifico (`speech_not_approved`, `slides_not_approved`,
`voice_sample_missing`).

## 3. Data model

Migration **`0025_lesson_video.py`** — 8 colonne `video_*` su
`course_lesson`:

| Colonna | Tipo | Note |
|---|---|---|
| `video_status` | VARCHAR(40) | `empty\|pending\|processing\|ready\|failed\|cancelled` (CHECK) |
| `video_progress` | SMALLINT | 0-100 (CHECK) |
| `video_progress_phase` | VARCHAR(50) | `preparing\|tts\|rendering_slides\|encoding` |
| `video_path` | VARCHAR(500) | path relativo sotto `upload_root` |
| `video_attempts` | SMALLINT | contatore tentativi (auto-retry) |
| `video_error` | TEXT | messaggio d'errore terminale |
| `video_generated_at` | TIMESTAMPTZ | timestamp ultima generazione |
| `video_tokens` | JSONB | metadata run (vedi sotto) |

Index `ix_course_lesson_course_video_status` su
`(course_id, video_status)` per le query batch.

`video_tokens` raccoglie la telemetria della run: `audio_duration_s`,
`video_duration_s`, `encode_duration_ms`, `tts_duration_ms`, `device`,
`model_xtts`, `num_segments`, `num_slides`, `file_size_bytes`.

Migration **`0026_avatar_tts_latents_and_course_video_language.py`**
aggiunge inoltre `course.video_language_code` (VARCHAR nullable):
override per-corso della lingua usata dal TTS. NULL → fallback su
`course.language_code`. La stessa migration introduceva una cache di
latenti XTTS sull'avatar, poi **rimossa** dalla
`0027_drop_avatar_tts_latents.py` quando il TTS è migrato su RunPod.

## 4. Il TTS su RunPod GPU

Il TTS XTTS-v2 **non gira nel backend**: il backend non ha
torch/coqui. La sintesi gira su un endpoint **RunPod Serverless (GPU)**
il cui handler è nella cartella `XTTS/` del repo (immagine Docker
dedicata). Il backend è solo un **client HTTP**.

`backend/app/services/runpod_tts_client.py`:

- `is_configured() -> bool` — True se `RUNPOD_API_KEY` e
  `RUNPOD_TTS_ENDPOINT_ID` sono entrambi presenti.
- `synthesize_lesson_audio(*, speech_raw, voice_sample_path,
  language_code, on_segment_progress) -> tuple[dict[str, np.ndarray], int]`
  — sintetizza l'audio di **tutti** i segment con un solo job. Ritorna
  `({segment_id: ndarray float32 mono}, 24000)`.

Dettagli del contratto:

- **Un job per video**, non uno per segment. Il payload contiene
  `language_code`, `voice_sample_url`, `segments` (lista
  `{segment_id, text}` non vuoti).
- Il **campione vocale viaggia come URL pubblico** (`/uploads/...`), non
  come base64 inline: un audio di pochi MB in base64 sfora il limite di
  payload di `/run` di RunPod. Il worker GPU lo scarica via HTTP.
- L'handler RunPod restituisce l'audio **per chunk** (FLAC base64, con
  `chunk_index` per segment): il client li raggruppa per `segment_id`,
  li ordina e li concatena. Questo evita di perdere segment quando
  l'audio di una lezione lunga è troppo grande per un singolo blob.
- Il client consuma lo **stream incrementale** `/stream/{job_id}`
  (progress per-segment); a job `COMPLETED` continua a drenare lo stream
  finché una risposta torna senza nuovi item — altrimenti perderebbe i
  chunk finali ancora bufferizzati. Se `/stream` fallisce, fallback a
  `/status` polling.
- **Controllo di completezza**: ogni `segment_id` richiesto DEVE avere
  audio. Un audio incompleto darebbe un video monco/desincronizzato →
  il client solleva `RunpodJobFailedError` (il worker fa auto-retry).

Errori: `RunpodNotConfiguredError`, `RunpodJobFailedError`,
`RunpodTimeoutError`, `RunpodTtsError` (base, recuperabile).

### 4.6 Chunking CJK-aware (worker XTTS GPU)

Il backend **non** spezza il testo: invia ogni segment come
`{segment_id, text}` (vedi sopra). È il **worker GPU XTTS**
(`XTTS/handler.py`) a suddividere ogni segment in chunk sintetizzabili,
con regole dipendenti dalla lingua. Il punto d'ingresso è
`split_into_chunks(text, max_chars, *, join)`, invocato per ogni segment
da `_synthesize_segment_chunks`.

Perché serve: il tokenizer XTTS-v2 ha un **limite di caratteri per
frase per lingua** (`VoiceBpeTokenizer.check_input_length`); oltre il
limite l'audio viene troncato. Per le lingue CJK il limite è molto più
basso che per le europee — `ja=71`, `zh-cn=82`, `ko=95` contro `it=213`,
`en=250` (tabella `_XTTS_CHAR_LIMITS` in `handler.py`). Il cap effettivo
di ogni chunk è `min(MAX_CHARS=180, limite_lingua)`.

Lo split procede a tre stadi:

1. **Split forte sui terminatori di frase**, sia ASCII (`. ! ? :`
   seguiti da spazio, così `3.14` non viene spezzato) sia **CJK**
   (`。．！？…`, anche senza spazio dopo) — regex `_STRONG_SPLIT_RE`.
2. Frasi ancora troppo lunghe: **split debole sulle virgole** ASCII e
   CJK (`、，`, `_SOFT_SPLIT_RE`) con **packing greedy** entro `max_chars`,
   accumulando i pezzi finché ci stanno.
3. **Rete di sicurezza** (`_hard_slice`): qualunque chunk ancora oltre il
   limite (tipico del giapponese privo di punteggiatura) viene tagliato a
   conteggio caratteri. Senza questo, un chunk troppo grande verrebbe
   scartato dallo stream RunPod → "nessun audio prodotto" per quel
   segment.

Il separatore di packing `join` è uno spazio per le lingue europee e la
**stringa vuota per ja/zh/ko** (`_NO_SPACE_LANGS`), che non separano le
parole con spazi. Ogni chunk subisce inoltre `rstrip` della punteggiatura
finale (`_CHUNK_TRIM_CHARS`) per evitare il "punto" pronunciato dal
normalizer XTTS. Per testo solo-ASCII l'output è identico alla versione
pre-CJK.

Le lingue CJK richiedono **dipendenze G2P/tokenizer dedicate**, extra
opzionali di `coqui-tts` non installate di default (`XTTS/requirements.txt`):
giapponese → `cutlet` (romanizzazione) + `unidic-lite` (dizionario MeCab
per fugashi); cinese → `jieba` (segmentazione) + `pypinyin`; coreano →
`hangul_romanize`. Senza, un job in quelle lingue fallisce a runtime con
`ModuleNotFoundError`.

Lato backend `tts_languages.py` (`normalize_language_code`) fa **solo
normalizzazione del language code** — lowercase, drop del country code,
`zh*` → `zh-cn`, fallback `it` — e validazione contro
`XTTS_SUPPORTED_LANGUAGES` (le 16 lingue); non tocca mai il testo dei
segment. Lo stesso `normalize_language_code` è duplicato in `handler.py`
lato GPU.

## 5. Cache dell'audio TTS su disco

`backend/app/services/lesson_audio_cache.py` — evita di richiamare
RunPod (costo GPU + attesa) quando si rigenera un video la cui parte
audio non è cambiata.

- L'audio sintetizzato viene salvato come un WAV per segment
  (`seg_NNN.wav`, PCM_16 @ 24000 Hz) + un `manifest.json`, sotto
  `{upload_root}/lesson_audio/{course_id}/{lesson_id}/`.
- `compute_cache_key(*, speech_raw, voice_sample_path, language_code)` —
  hash SHA-256 di: testo dei segment (ordinati per id), lingua, contenuto
  del campione vocale. Se uno qualunque cambia, la chiave cambia → cache
  invalidata da sola.
- `load(course_id, lesson_id, *, cache_key)` — ricarica l'audio se il
  manifest combacia; `None` se assente/diverso/incompleto/corrotto.
- `save(course_id, lesson_id, *, cache_key, audio_per_segment)` —
  sovrascrive la cache della lezione.

Il worker, in Fase 1, prova prima la cache: se hit, salta del tutto
RunPod e va dritto al 60 %.

## 6. Rendering delle slide (Playwright)

`backend/app/services/lesson_slides_video_render_service.py` —
`render_slides_to_png(db, *, course, lesson, output_dir, public_base_url)
-> (png_paths, slide_id_order)`.

Riusa **al 100 %** la pipeline del PDF slide (Fase 4): stesso template
(`lesson_slides_pdf.html.j2`), stesso pre-render Mermaid → SVG, stessa
risoluzione di asset (LaTeX → MathML, immagini caricate). La differenza
è il viewport: Playwright apre **1980×1400** (proporzione A4 landscape
297:210 = 99:70) e scala ogni `.slide` per riempire esattamente il
frame — niente bande bianche, niente distorsione. `enable_split=False`:
1 slide JSON → 1 frame video (mapping audio↔frame banale).

Output: una PNG 1980×1400 per slide, in ordine 1:1 con
`slides_raw.slides[].slide_id`.

## 7. Composizione ffmpeg

`backend/app/services/lesson_video_compose_service.py` —
`compose_lesson_video(*, lesson_speech_raw, png_paths, slide_id_order,
audio_per_segment, audio_sample_rate, output_path, on_progress)`.

1. Per ogni slide, dai suoi `segment_ids` (da
   `speech.slide_to_segments_map`) concatena i WAV TTS dei segment in un
   unico WAV. Slide senza segment → 2 s di silenzio (fallback, non far
   sparire la slide).
2. Per ogni slide: `ffmpeg -loop 1 -i slide.png -i audio.wav -shortest
   -tune stillimage -c:v libx264 -c:a aac` → `seg_NNN.mp4`. La durata
   del segmento = durata del suo audio.
3. Concat finale via demuxer `concat` (`-c copy`, niente re-encode).

Tutte le operazioni sono subprocess async. Il wrapper sync
`compose_lesson_video_sync` crea un `ProactorEventLoop` dedicato (su
Windows serve per `subprocess_exec`); il worker lo invoca via
`asyncio.to_thread`. Ritorna metadata: `audio_duration_s`,
`video_duration_s`, `encode_duration_ms`, `num_segments_encoded`,
`file_size_bytes`.

## 8. Public API e worker

`backend/app/services/course_lesson_video_service.py` — API pubblica
(usata dalle rotte):

- `request_lesson_video` / `request_all_lessons_video` — enqueue
  (`video_status='pending'`); validano le pre-condizioni e sollevano
  `ConflictError` con `code` specifico.
- `cancel_lesson_video` / `cancel_all_lesson_videos` — `pending`/
  `processing` → `cancelled`.
- `load_course_full`, `get_lesson_or_404`, `resolve_assignee_avatar`,
  `resolve_voice_sample_path` — helper di caricamento.
- `build_status_out` / `build_batch_out` — costruiscono i DTO
  (`LessonVideoStatusOut`, `LessonVideoBatchOut`) con `is_stale`,
  `eligible_count`, `aggregate_progress`.
- `save_video_metadata` — persistenza finale post-encoding.

`backend/app/services/course_lesson_video_worker.py` — worker async
(loop singolo lanciato in `app.main.lifespan`):

- Pattern speculare ai worker delle Fasi 2-5: semaphore + set
  `_inflight` + claim atomico via status.
- Cap di concorrenza `course_lesson_video_max_concurrency` (default 1 —
  un job GPU per volta).
- **Auto-retry trasparente**: errori recuperabili (timeout/errore
  RunPod, errore ffmpeg) con `attempts < course_lesson_video_auto_retry_max`
  (default 3) → status riportato a `pending`; pre-condizioni mancanti →
  `failed` terminale.
- Cancel-check tra le fasi.
- **Nota tecnica**: il callback di progress dell'encoding viene
  invocato dal thread di compose; il worker cattura il loop principale
  (proprietario del pool asyncpg) con `asyncio.get_running_loop()` e usa
  `run_coroutine_threadsafe`, altrimenti l'update DB finirebbe su un
  loop sbagliato (`Future attached to a different loop`).

## 9. API REST

Sotto `/orgs/{org_id}/courses` (vedi [05 — API reference](05-api-reference.md)):

| Metodo | Path | Permesso |
|---|---|---|
| POST | `/{course_id}/lessons/{lesson_id}/video/generate` | `course:generate` |
| POST | `/{course_id}/lessons-video/generate-batch` | `course:generate` |
| POST | `/{course_id}/lessons/{lesson_id}/video/cancel` | `course:generate` |
| POST | `/{course_id}/lessons-video/cancel-batch` | `course:generate` |
| GET | `/{course_id}/lessons/{lesson_id}/video/status` | `course:view` |
| GET | `/{course_id}/lessons-video/status` | `course:view` |

`generate`/`generate-batch` rispondono `202 Accepted`. Lo status è
polling-friendly: il FE rinfresca ogni 2 s mentre c'è almeno un job in
flight.

## 10. Frontend

- `frontend/src/api/courses.ts` → namespace `coursesApi.lessonVideo`
  (6 metodi).
- `frontend/src/hooks/useLessonVideo.ts` — `useCourseVideoStatus`,
  `useLessonVideoStatus` + 4 mutation hook; refetch ogni 2 s se ci sono
  job in flight.
- `frontend/src/pages/org/courses/components/CourseLessonVideoView.tsx`
  — scheda **"Video"** del `CourseEditorPage`: selettore lingua TTS,
  banner pre-requisiti, progress aggregato + ETA, card per lezione con
  player HTML5 e bottoni Genera/Rigenera/Annulla/Scarica.
- La lingua TTS è limitata alle **16 lingue supportate da XTTS-v2**
  (`XTTS_SUPPORTED_LANGUAGES` in `courses.ts`); se la lingua del corso
  non è supportata, l'override è obbligatorio.

## 11. Configurazione

Vedi [04 — Configuration](../04-configuration.md). Variabili rilevanti:

- RunPod TTS: `RUNPOD_API_KEY`, `RUNPOD_TTS_ENDPOINT_ID`,
  `RUNPOD_BASE_URL`, `RUNPOD_TTS_TIMEOUT_SECONDS`,
  `RUNPOD_TTS_POLL_INTERVAL_SECONDS`.
- Worker: `COURSE_LESSON_VIDEO_POLL_INTERVAL_SECONDS`,
  `COURSE_LESSON_VIDEO_MAX_CONCURRENCY`,
  `COURSE_LESSON_VIDEO_AUTO_RETRY_MAX`.
- Encoding ffmpeg: `VIDEO_FRAMERATE` (30), `VIDEO_AUDIO_BITRATE`,
  `VIDEO_AUDIO_SAMPLE_RATE`, `VIDEO_VIDEO_CODEC` (libx264), `VIDEO_CRF`,
  `VIDEO_PRESET` (veryfast), `VIDEO_PIXEL_FORMAT`, `LESSON_VIDEO_MAX_MB`,
  `FFMPEG_BINARY`.

## 12. Migrazioni

| Migration | Cosa |
|---|---|
| `0025_lesson_video.py` | 8 colonne `video_*` + 2 CHECK + index batch |
| `0026_avatar_tts_latents_and_course_video_language.py` | `course.video_language_code` (+ cache latenti XTTS, poi rimossa) |
| `0027_drop_avatar_tts_latents.py` | rimuove la cache latenti XTTS (TTS migrato su RunPod) |

## Note / edge case

- Il TTS originariamente girava **in-process** (`xtts_voice_clone_service`,
  torch+coqui nel container). È stato migrato su RunPod GPU serverless
  per togliere ~5 GB di dipendenze dal backend e usare una GPU vera. Il
  vecchio service non esiste più.
- Server CPU senza NVENC: l'encoding usa `libx264 -preset veryfast
  -tune stillimage` — per slide statiche dà qualità identica a `medium`
  ma 3-5× più veloce.
- Il video con avatar parlante sovrapposto è una feature separata:
  vedi [13 — Avatar video](13-avatar-video.md).
