# 13 — Avatar video (Fase 6b §9b)

Generazione del **"Video con Avatar"**: prende il video MP4 già
generato della lezione (Fase 6) e ci **sovrappone in basso a destra un
avatar parlante** con lip-sync, sincronizzato sull'audio della lezione.
Il lip-sync è prodotto da **MuseTalk** su un endpoint RunPod Serverless
GPU dedicato.

> Spec di riferimento: §9b. È una scheda separata da "Video" (Fase 6).

## 1. Pipeline

```
video_status='ready' (il video lezione esiste)  ─┐
Avatar assegnatario con ≥1 clip MiniMax pronta   ─┼─► avatar_video_status:
MuseTalk/R2 configurati                          ─┘    empty→pending→processing→ready
                                                              │
                                                              ▼
                                  /uploads/lesson_avatar_videos/{course}/{lesson}.mp4
```

Worker `course_lesson_avatar_video_worker`, 3 fasi con cancel-check:

| Fase | Progress | Cosa fa |
|---|---|---|
| Preparazione | 1 → 9 % | Ridimensiona le clip avatar a 640×640; estrae la traccia audio dal video MP4 della lezione. |
| Lip-sync | 10 → 85 % | Lancia il client MuseTalk come subprocess: assembla un video dalle clip, lo invia a RunPod, scarica il video di avatar parlante. |
| Overlay | 86 → 100 % | Sovrappone con ffmpeg l'avatar (quadrato, in basso a destra) al video della lezione, conservandone la traccia audio. |

Output: `/uploads/lesson_avatar_videos/{course_id}/{lesson_id}.mp4`.

**Sincronizzazione garantita per costruzione**: l'audio passato a
MuseTalk è esattamente quello estratto dal video della lezione, e il
video finale conserva quello stesso audio. Avatar e slide condividono
la medesima timeline.

## 2. Pre-condizioni

Verificate dal worker dopo il claim:

- `lesson.video_status == 'ready'` e `video_path` valorizzato — il video
  MP4 della lezione (Fase 6) **deve esistere**: l'avatar ci si sovrappone.
- L'avatar dell'assegnatario del corso deve avere **≥ 1 clip MiniMax
  pronta** (`AvatarClip.status == 'ready'`). Attenzione: cambiare
  l'immagine dell'avatar **resetta tutte le clip a `pending`** e quindi
  ri-blocca questa pre-condizione finché non vengono rigenerate (vedi §9b).
- Credenziali MuseTalk/R2 configurate (vedi §8).
- La lezione non deve essere `is_assessment`.

> **Generazione clip avatar (FLF).** Le clip del pool non vengono
> prodotte da questo worker, ma dal worker dedicato
> `avatar_clip_worker` via MiniMax. Dal default `MINIMAX_VIDEO_MODEL =
> **MiniMax-Hailuo-02**` (`backend/app/core/config.py:56`), la clip è
> generata in modalità **FLF (First-and-Last-Frame)**: il worker passa
> `last_frame_image = first_frame_image` (la **stessa URL avatar**) a
> `start_video_generation` (`backend/app/services/avatar_clip_worker.py:130-136`,
> `backend/app/services/minimax_service.py:80,100-101`), così il modello
> interpola dal frame iniziale allo stesso frame finale → ogni clip
> **torna alla posa di partenza** ed è quindi loopabile su sé stessa e
> interscambiabile con qualsiasi altra clip del pool (giunzioni fluide
> quando MuseTalk le concatena). Sostituisce l'approccio del precedente
> `MiniMax-Hailuo-2.3`, che si affidava al solo prompt *"seamless looping
> animation"* (I2V puro, niente FLF). Dettaglio in
> [04 — Configuration](../04-configuration.md) e
> [backend/07 — Services](../backend/07-services.md).

A monte l'API rifiuta con `409` + `code` (`lesson_video_not_ready`,
`avatar_clips_not_ready`, ...).

## 3. Il client MuseTalk (vendored)

`backend/app/musetalk_client/` è una **copia verbatim** del client
`scripts/client/` del progetto esterno **MuseTalk-API**:

```
app/musetalk_client/
├── README.md                           # "vendored, NON modificare"
└── scripts/
    ├── __init__.py
    └── client/
        ├── __init__.py
        ├── synth_random_lipsync.py      # entry-point CLI
        ├── runpod_client.py             # R2 (boto3) + RunPod API (requests)
        ├── video_assembler.py           # probe/sample/concat/trim ffmpeg
        └── clip_manifest.py             # cache preprocessing per set di clip
```

Principi dell'integrazione:

- **NON viene modificato nulla**: i 4 file sono identici all'originale
  (verifica SHA-256 al momento della copia). Per aggiornarli si ri-copia
  dal progetto sorgente, non si edita a mano.
- a4u resta **disaccoppiata** da MuseTalk-API: il client gira come
  **subprocess isolato** — `python -m scripts.client.synth_random_lipsync`
  con `cwd` sulla cartella vendored (così `import scripts.client...` si
  risolve) — e legge la configurazione solo da variabili d'ambiente, che
  il worker gli passa esplicitamente.
- Dipendenze del client: `boto3` (R2, S3-compatible) e `requests` (HTTP
  RunPod), dichiarate in `backend/pyproject.toml`.

Cosa fa `synth_random_lipsync`: campiona N clip brevi dell'avatar, le
concatena (con ripetizione, seed deterministico) fino a coprire la
durata dell'audio, calcola un manifest di preprocessing su RunPod
(bbox + latenti + parsing, messo in cache su R2), e infine sottomette
il job di lip-sync.

## 4. Data model

Migration **`0029_avatar_video.py`**.

8 colonne `avatar_video_*` su `course_lesson` — gemelle delle `video_*`
di Fase 6:

| Colonna | Tipo |
|---|---|
| `avatar_video_status` | VARCHAR(40), `empty\|pending\|processing\|ready\|failed\|cancelled` |
| `avatar_video_progress` | SMALLINT 0-100 |
| `avatar_video_progress_phase` | VARCHAR(50), `preparing\|lipsync\|overlay` |
| `avatar_video_path` | VARCHAR(500) |
| `avatar_video_attempts` | SMALLINT |
| `avatar_video_error` | TEXT |
| `avatar_video_generated_at` | TIMESTAMPTZ |
| `avatar_video_tokens` | JSONB |

3 colonne `musetalk_*` su `avatars` — parametri MuseTalk per-avatar,
con default = i valori del comando MuseTalk testato manualmente:

| Colonna | Default | Flag CLI |
|---|---|---|
| `musetalk_extra_margin` | 15 | `--extra-margin` |
| `musetalk_left_cheek_width` | 110 | `--left-cheek-width` |
| `musetalk_right_cheek_width` | 110 | `--right-cheek-width` |

Sono modificabili dalla pagina "Mio Avatar" (sezione avanzata).

Index `ix_course_lesson_course_avatar_video_status` su
`(course_id, avatar_video_status)`.

## 5. Il worker

`backend/app/services/course_lesson_avatar_video_worker.py` — pattern
speculare a `course_lesson_video_worker` (semaphore, claim atomico,
auto-retry, cancel-check). Cap di concorrenza 1 (un job GPU per volta).

### Fase preparazione

- **`_prepare_musetalk_clips`** — ridimensiona le clip dell'avatar a
  `avatar_video_clip_resolution`×`...` (default **640**) in
  `{upload_root}/avatars/{user_id}/clips_musetalk_{res}/`. Le clip
  MiniMax sono 1080×1080 (generate da `MiniMax-Hailuo-02` in modalità
  FLF, loopabili — vedi §2): a quella risoluzione il lip-sync su RunPod
  sfora il tetto di 60 min (blending + encode + RAM scalano con l'area
  del frame). Una clip è riconvertita **solo se cambia il sorgente** →
  mtime/dimensione stabili → l'hash del set di clip di MuseTalk resta
  stabile → il preprocessing resta in cache fra lezioni.
- **`_extract_audio`** — estrae la traccia audio dal video MP4 della
  lezione in un WAV mono 16 kHz. Usa **`aresample=async=1`**: la traccia
  audio della lezione può avere gap di timeline (pause fra i segment);
  senza questo filtro ffmpeg li **compatterebbe** e l'avatar verrebbe
  sincronizzato su una timeline più corta dell'audio finale → drift
  cumulativo. Con `async=1` i gap restano (come silenzio) e l'avatar
  combacia con l'audio del video finale.

### Fase lip-sync

`_run_musetalk_subprocess` lancia
`python -m scripts.client.synth_random_lipsync` con `cwd` sul client
vendored e l'environment popolato dalle credenziali RunPod/R2. Argomenti
passati: `--clips-dir` (la dir 640×640), `--audio`, `--output`,
`--intermediate-dir`, `--manifest-cache-dir`
(`{upload_root}/musetalk_manifests/`, persistente), i tre `musetalk_*`
dell'avatar, `--seed` (derivato dall'id lezione → riproducibile),
`--keep-intermediate`.

Il worker legge lo stdout del subprocess per il progress (tag
`[build]`, `[probe]`, `[submit]`, `[poll]`, `[dload]`) e fa cancel-check
ad ogni milestone.

### Fase overlay

`_overlay_avatar` — ffmpeg sovrappone il video di avatar al video della
lezione: l'avatar è un quadrato di lato `avatar_video_overlay_scale`
(default 0,24 = 24 %) della larghezza del video, ancorato in basso a
destra con margine `avatar_video_overlay_margin` (24 px). `-c:a copy`
conserva intatta la traccia audio della lezione.

### Gestione errori

- Un **timeout RunPod (`TIMED_OUT`)** è errore **terminale**, non
  recuperabile: si ripeterebbe identico, e tre retry da 1 h sarebbero
  3 h di GPU sprecate.
- Errori transitori → auto-retry (`course_lesson_avatar_video_auto_retry_max`,
  default 3).
- Diagnostica `avatar_video_av_diag`: dopo il lip-sync il worker logga
  fps/durata di audio estratto, video assemblato, output MuseTalk e
  video lezione — osservabilità per problemi di sincronizzazione.

## 6. Public API (service)

`backend/app/services/course_lesson_avatar_video_service.py`:

- `request_lesson_avatar_video` / `request_all_lessons_avatar_video` —
  enqueue con validazione pre-condizioni.
- `cancel_lesson_avatar_video` / `cancel_all_lesson_avatar_videos`.
- `avatar_clips_dir(user_id)` / `avatar_musetalk_clips_dir(user_id, res)`
  — path delle clip originali / ridimensionate.
- `count_ready_clips` / `avatar_is_ready` — eleggibilità dell'avatar.
- `build_status_out` / `build_batch_out` — DTO
  (`LessonAvatarVideoStatusOut`, `LessonAvatarVideoBatchOut`).
- `save_avatar_video_metadata` — persistenza finale.

Riusa `load_course_full`, `get_lesson_or_404`, `resolve_assignee_avatar`
da `course_lesson_video_service`.

## 7. API REST

| Metodo | Path | Permesso |
|---|---|---|
| POST | `/{course_id}/lessons/{lesson_id}/avatar-video/generate` | `course:generate` |
| POST | `/{course_id}/lessons-avatar-video/generate-batch` | `course:generate` |
| POST | `/{course_id}/lessons/{lesson_id}/avatar-video/cancel` | `course:generate` |
| POST | `/{course_id}/lessons-avatar-video/cancel-batch` | `course:generate` |
| GET | `/{course_id}/lessons/{lesson_id}/avatar-video/status` | `course:view` |
| GET | `/{course_id}/lessons-avatar-video/status` | `course:view` |

`PATCH /me/avatar/musetalk-params` — aggiorna i tre `musetalk_*`
dell'avatar dell'utente corrente.

## 8. RunPod MuseTalk + Cloudflare R2

- **RunPod**: stesso account del TTS (`RUNPOD_API_KEY` riusato), endpoint
  Serverless **dedicato** a MuseTalk (`RUNPOD_MUSETALK_ENDPOINT_ID`).
- **Cloudflare R2** (S3-compatible): storage di transito per
  video/audio/output del job. Credenziali: `R2_ENDPOINT`, `R2_BUCKET`,
  `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.

Queste variabili vengono passate al subprocess MuseTalk come environment.
Se mancano, il worker fallisce con un errore di pre-condizione esplicito.

## 9. Frontend

- `frontend/src/api/courses.ts` → namespace `coursesApi.lessonAvatarVideo`.
- `frontend/src/hooks/useLessonAvatarVideo.ts` — `useCourseAvatarVideoStatus`
  + 4 mutation hook.
- `frontend/src/pages/org/courses/components/CourseLessonAvatarVideoView.tsx`
  — scheda **"Video con avatar"** del `CourseEditorPage`.
- `frontend/src/pages/me/MyAvatarPage.tsx` — pagina "Mio Avatar": form
  immagine/audio, griglia delle clip, sezione avanzata per i tre
  parametri MuseTalk per-avatar.
- `frontend/src/components/avatar/AvatarClipCard.tsx` — card della singola
  clip nel pool (player video se pronta, spinner/errore altrimenti).

## 9b. Clip avatar — label e reset al cambio immagine

### Label "Clip N" (non il prompt)

Ogni clip del pool è generata da un prompt admin (`AvatarClipPrompt`,
salvato in `AvatarClip.prompt_text`), ma la UI **non mostra mai il
prompt**: la card `AvatarClipCard` etichetta la clip come **"Clip N"**
(chiave i18n `myAvatar.clipLabel` = `"Clip {{n}}"`, con
`n = position + 1`) e ripete l'indice come badge `#{position+1}` sul
player. Il `prompt_text` resta interno (serve solo al worker MiniMax come
testo di generazione) e non è esposto all'utente: le clip sono
intercambiabili (vedi §2, FLF), quindi per l'utente contano come pool
numerato, non come prompt distinti.

### Reset di TUTTE le clip al cambio dell'immagine avatar

Quando l'utente salva l'avatar con una **nuova immagine** (o alla prima
creazione), `avatar_service.upsert_my_avatar` rigenera l'intero pool da
zero (`backend/app/services/avatar_service.py:124-130`):

1. `_reset_clips` — cancella **tutte** le `AvatarClip` esistenti, sia i
   record DB sia i file video locali sotto `/uploads/avatars/{user_id}/clips/`.
2. `_create_pending_clips` — ricrea una clip `status='pending'` per ogni
   prompt admin attivo.
3. `avatar.clips_status = 'pending'`.

Le clip vanno quindi **rigenerate da capo** dal worker MiniMax
(`avatar_clip_worker`): solo cambiare l'immagine, non l'audio, fa
scattare il reset (`image_changed`; un nuovo audio da solo lascia il pool
intatto). La UI avvisa in anticipo: la barra di salvataggio di
`MyAvatarPage` mostra `myAvatar.saveWarn` ("Modificare l'immagine
rigenererà tutti i clip.") quando `imageWillChange` è vero.

**Effetto sul «Video con Avatar».** Finché le clip non tornano pronte la
generazione avatar_video è **bloccata**: la pre-condizione `≥ 1 clip
ready` (`avatar_is_ready` / `count_ready_clips`, vedi §2 e §6) non è più
soddisfatta subito dopo il reset, e l'API risponde `409
avatar_clips_not_ready` finché il worker MiniMax non ha rigenerato almeno
una clip del nuovo pool. In pratica: cambiare l'immagine dell'avatar
invalida ogni «Video con Avatar» rigenerabile finché le clip non sono di
nuovo `ready`.

## 10. Configurazione

Vedi [04 — Configuration](../04-configuration.md):

- `RUNPOD_MUSETALK_ENDPOINT_ID`, `R2_ENDPOINT`, `R2_BUCKET`,
  `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
- `COURSE_LESSON_AVATAR_VIDEO_POLL_INTERVAL_SECONDS`,
  `COURSE_LESSON_AVATAR_VIDEO_MAX_CONCURRENCY`,
  `COURSE_LESSON_AVATAR_VIDEO_AUTO_RETRY_MAX`,
  `COURSE_LESSON_AVATAR_VIDEO_TIMEOUT_SECONDS`.
- `AVATAR_VIDEO_CLIP_RESOLUTION` (640), `AVATAR_VIDEO_OVERLAY_SCALE`
  (0.24), `AVATAR_VIDEO_OVERLAY_MARGIN` (24).

## Note / limiti noti

- `synth_random_lipsync` fissa l'`executionTimeout` del job RunPod a
  60 min. A 640×640 una lezione con audio fino a ~40-45 min rientra;
  audio molto più lunghi potrebbero sforare.
- La cancellazione durante il lip-sync ferma il subprocess locale, ma
  il job RunPod prosegue da remoto fino al termine (l'annullamento
  remoto richiederebbe di modificare il client MuseTalk).
- Il downscale delle clip a 640 e la preservazione dei gap audio sono
  due fix critici: senza il primo il job va in timeout, senza il secondo
  l'avatar si desincronizza progressivamente dall'audio.
