# Backend 07 — `app/services/`

Logica di dominio. I services ricevono `AsyncSession` (dal dep `get_db`),
parametri dominio, e ritornano modelli ORM o tuple. Mai aprono sessioni
proprie. Ogni operazione mutating chiama `core.audit.write_audit`.

---

## `app/services/__init__.py`

Vuoto.

---

## `app/services/file_service.py`

**Scopo**: validare e salvare upload immagini/audio sul filesystem locale.
Ri-encoding tramite Pillow per le immagini (strip EXIF). Subdir accettano
path nidificati come stringa (es. `"avatars/<user_id>"`).

### Costanti

- `ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}`.
- `ALLOWED_EXT_BY_FORMAT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}`.
- `ALLOWED_AUDIO_MIME = {"audio/webm", "audio/ogg", "audio/mpeg",
  "audio/mp4", "audio/wav", "audio/x-wav", "audio/m4a", "audio/x-m4a",
  "audio/aac"}`.
- `log = get_logger("app.files")`.

> `subdir` è una stringa: oltre ai valori "piatti" (`organizations`,
> `templates`) accetta forme nested come `f"avatars/{user_id}"`. Il path
> finale è sempre validato da `_ensure_within`.

### Funzioni

#### `_ensure_within(root: Path, target: Path) -> None`

Solleva `ValidationAppError(code="invalid_path")` se `target` (resolved) non
è dentro `root`. Difesa contro path traversal.

#### `save_upload_image(upload, *, subdir, filename_stem=None, max_dimension=4096) -> str`

`async`. Validazione + ri-encoding + salvataggio.

1. Verifica `Content-Type` ∈ `ALLOWED_MIME_TYPES` (se presente).
2. `await upload.read()`. Verifica:
   - non vuoto → `code=empty_file`;
   - dimensione ≤ `UPLOAD_MAX_MB` → `code=file_too_large`.
3. `Image.open(BytesIO(raw))`:
   - `ImageOps.exif_transpose` rispetta orientamento e strip EXIF;
   - se formato non riconosciuto, fallback PNG (alpha) o JPEG;
   - `thumbnail` se `max(w,h) > max_dimension`;
   - per JPEG converte in `RGB` e usa `quality=85`;
   - salva in `BytesIO` con `optimize=True`.
4. Crea `target_dir = upload_root / subdir` (anche se nested,
   `mkdir(parents=True)`).
5. `filename = f"{filename_stem or uuid4().hex}{ext}"`.
6. `_ensure_within(upload_root, target_path)`.
7. `target_path.write_bytes(payload)`.
8. Log `file_saved` con subdir, filename, size.
9. Restituisce `f"/uploads/{subdir}/{filename}"` (path pubblico).

`filename_stem` è opzionale; serve quando si vuole nomenclatura
deterministica (es. `image` per l'avatar utente, dove ogni utente ha la
propria cartella e quindi non c'è collisione).

In caso di immagine non valida: `UnidentifiedImageError → ValidationAppError(code="invalid_image")`.

#### `save_upload_audio(upload, *, subdir, filename_stem=None) -> str`

`async`. Validazione e salvataggio della traccia audio dell'avatar.

1. Verifica `Content-Type` ∈ `ALLOWED_AUDIO_MIME`.
2. `await upload.read()`. Verifica non vuoto e dimensione ≤
   `AVATAR_AUDIO_MAX_MB` → altrimenti `code=file_too_large`.
3. Estensione derivata dal MIME (mai dal client).
4. Salva sotto `upload_root / subdir / filename`. `_ensure_within` come
   sopra.
5. Restituisce il path pubblico `/uploads/<subdir>/<filename>`.

Non c'è ri-encoding: il file audio è scritto raw.

#### `save_document_from_bytes(payload: bytes, *, subdir, mime_type, filename_stem=None) -> tuple[str, str, int]`

`async`. Sibling di `save_upload_document` che riceve **bytes** invece di
un `UploadFile` (`file_service.py:314-360`). Usato dall'import paper
scientifici per persistere il PDF scaricato da URL esterno (OpenAlex)
oppure il `.md` di metadati generato per i paper non-OA. Stessa
validazione di `save_upload_document`:

1. `_validate_subdir(subdir)`.
2. MIME ∈ whitelist `ALLOWED_DOCUMENT_MIME_TYPES` (PDF/DOC/DOCX/TXT/MD/RTF)
   → altrimenti `code=invalid_document_mime`.
3. `payload` non vuoto → `code=empty_file`.
4. Dimensione ≤ `course_document_max_mb` → altrimenti
   `code=document_too_large`.
5. Estensione derivata dal MIME, salva sotto `upload_root / subdir /
   {stem}{ext}` con `_ensure_within`.

Ritorna `(public_path, stored_filename, size_bytes)` come
`save_upload_document`. Nessuna trascodifica: bytes scritti raw.

> La whitelist include `text/markdown → .md`, abilitante per l'import
> dei metadati paper (vedi `course_service.add_document_from_bytes` e
> [Courses 16](../courses/16-paper-search.md)).

#### `delete_upload(path: str | None) -> None`

`async`. Cancella un file su filesystem.
- Se `path` è None o non inizia con `/uploads/` → no-op.
- Calcola path assoluto, valida con `_ensure_within`.
- Se esiste, `unlink()`. Errori di OS sono loggati ma non rilanciati.

---

## `app/services/storage_service.py`

**Scopo**: wrapper sottile sull'I/O file (org/avatar/template). Resta
l'**autorità** sulle convenzioni di path/subdir e sulla validazione del
filename, ma l'I/O effettivo è delegato a `remote_storage` (vedi sotto), che
instrada verso il backend attivo (`local` / `ovh_ftp` / `ovh_sftp`). Tutti i
metodi sono sincroni e non aprono sessioni DB.

### Costanti

- `_ALLOWED_ROOTS = {"organizations", "avatars", "templates"}` — whitelist
  del primo segmento di `subdir`.

### Funzioni

- `save_bytes(*, subdir: str, filename: str, data: bytes) -> str`: valida
  `subdir` (`_validate_subdir`) e `filename` (no `/`, no `.`/`..`), poi scrive
  via `remote_storage.get_storage().upload_bytes(uploads_key(...), data)`.
  Ritorna il path pubblico relativo `/uploads/{subdir}/{filename}`.
- `read_bytes(path: str) -> bytes`: richiede un path `/uploads/...`; legge i
  bytes via `remote_storage` (`StorageFileNotFound` → `ValidationAppError(
  code="file_not_found")`).
- `delete(path: str | None) -> None`: cancella un singolo file (no-op se
  `path` è `None` o non inizia con `/uploads/`; errori loggati ma non
  rilanciati).
- `delete_directory(subdir: str) -> None`: rimuove ricorsivamente una
  sotto-cartella di upload via `remote_storage.delete_prefix` (usato dal
  delete avatar per pulire `avatars/<user_id>/`). Idempotente.
- `public_url(path: str) -> str`: delega a `remote_storage.public_url(
  uploads_key(path))` → URL pubblico raggiungibile dall'esterno
  (MiniMax/RunPod): assoluto OVH con un backend remoto, `PUBLIC_BASE_URL` +
  path in `local`.

---

## `app/services/remote_storage.py`

**Scopo**: astrazione dello storage file. Tutto l'I/O persistente dei file
dell'app (upload utente/media, PDF generati) passa da qui: il DB salva un
*path logico opaco*, questo modulo lo mappa a una **key** namespaced
(`uploads/<rel>` o `generated_pdfs/<rel>`) e la risolve sul backend attivo,
selezionato da `settings.storage_backend`:

- `local` → filesystem locale (`LocalStorage`, dev/fallback, comportamento
  storico byte-per-byte).
- `ovh_ftp` → server OVH via FTP/FTPS (`OvhFtpStorage`; `ftplib.FTP_TLS` con
  riuso della sessione TLS sul canale dati, connessione per-operazione, MKD
  ricorsivo, upload atomico `STOR` su temp + `RNTO`, retry/backoff sugli errori
  transitori).
- `ovh_sftp` → server OVH via SFTP (`OvhSftpStorage`; Paramiko/SSH su porta
  `ovh_sftp_port`, stessa semantica di `OvhFtpStorage` ma su canale cifrato,
  rename atomico via `posix_rename` con fallback delete+rename). Richiede
  `paramiko`.

Le scritture/cancellazioni vanno sul backend primario; le letture lato server
usano il canale del backend (FTP `RETR` / SFTP), le letture browser/servizi
esterni l'URL pubblico.

### Protocollo + eccezioni

- `Storage` (`typing.Protocol`): `upload_bytes`, `upload_file`,
  `download_bytes`, `download_to`, `delete`, `delete_prefix`, `exists`,
  `size`. Implementato da `LocalStorage`, `OvhFtpStorage`, `OvhSftpStorage` e
  `FallbackStorage`.
- `StorageError` (base) e `StorageFileNotFound`.

### Key helpers

- `uploads_key(db_path: str) -> str`: key per un file di upload, tollerante a
  prefissi incoerenti (`/uploads/courses/...` con prefisso vs
  `lesson_videos/...` senza) → `uploads/<rel>`.
- `pdf_key(rel: str) -> str`: key per un PDF generato → `generated_pdfs/<rel>`.

### URL pubblici

- `media_url(key: str) -> str`: URL che il browser deve usare. Backend remoto
  (`ovh_ftp`/`ovh_sftp`) → URL assoluto `{ovh_public_base_url}/{key}`;
  `local` → path relativo same-origin (`/uploads/...`).
- `public_url(key: str) -> str`: URL assoluto per servizi esterni
  (MiniMax/RunPod). Con backend remoto coincide con `media_url`; in `local`
  antepone `public_base_url` al path servito.

### Fallback (cutover)

- `FallbackStorage(primary, fallback)`: scrive/cancella solo sul primario; in
  lettura ripiega sul fallback se il file non è (ancora) presente sul primario
  (logga `storage_fallback_read`). Usato durante il cutover quando
  `storage_local_fallback=true`.

### Factory

- `get_storage() -> Storage`: ritorna il backend attivo in base a
  `storage_backend`. Con un backend remoto e `storage_local_fallback=true`
  avvolge il primario in un `FallbackStorage(primary, LocalStorage())`.
  **Non cache-ato**: le istanze sono leggere (nessuna connessione persistente)
  e i test possono cambiare backend cambiando le env.
- `build_remote_backend(protocol: str | None = None) -> Storage`: costruisce il
  backend remoto (`ftp`|`sftp`) a prescindere da `storage_backend` — usato
  dagli script di spike/migrazione mentre l'app è ancora `local`.

Runbook di migrazione: [Storage file: migrazione su server OVH (FTP/SFTP)](../storage-ovh-migration.md).
Variabili di configurazione: [04 — Configuration](../04-configuration.md).

---

## `app/services/minimax_service.py`

**Scopo**: client del provider MiniMax (modello `MiniMax-Hailuo-02`).
Wrapping di `httpx.AsyncClient` con header `Authorization: Bearer
<MINIMAX_API_KEY>`. Le chiamate sono `async` e non toccano DB.

### Funzioni

- `start_video_generation(*, image_url: str, prompt: str, last_frame_image: str | None = None, duration: int | None = None) -> str`:
  POST `{MINIMAX_BASE_URL}/v1/video_generation`
  (`minimax_service.py:76-125`). Il body include sempre `model`
  (`settings.minimax_video_model`), `prompt` (troncato a 1990 char),
  `first_frame_image=image_url`, `duration` (param o
  `MINIMAX_CLIP_DURATION`) e `resolution=MINIMAX_CLIP_RESOLUTION`.
  `last_frame_image` è **opzionale**: viene aggiunto al body **solo se
  valorizzato** (`if last_frame_image: body["last_frame_image"] = ...`,
  `minimax_service.py:100-101`). Quando presente, attiva la modalità
  **FLF** (First-and-Last-Frame) di `MiniMax-Hailuo-02`: il modello
  interpola un video da `first_frame_image` a `last_frame_image`.
  L'API decide FLF dalla presenza del campo nel body — niente flag
  esplicito. Ritorna `task_id`.

  > Il caller `avatar_clip_worker._start_pending`
  > (`avatar_clip_worker.py:129-136`) passa **la stessa URL avatar**
  > come `image_url` e `last_frame_image`, così ogni clip torna alla posa
  > iniziale → diventa loopabile su sé stessa e interscambiabile con
  > qualsiasi altra clip dello stesso pool (MuseTalk pesca a caso dal
  > pool: con clip loopabili le giunzioni sono fluide). Vedi
  > [Courses 13 — Avatar video](../courses/13-avatar-video.md).
- `query_task_status(task_id: str) -> TaskStatus`: GET sullo status del
  task (`minimax_service.py:128-158`). Ritorna il dataclass frozen
  `TaskStatus(status, file_id, raw)` (`minimax_service.py:48-52`):
  - `status`: valore **normalizzato** lowercase tra `preparing` |
    `processing` | `success` | `failed` | `unknown`
    (`minimax_service.py:144-155`). Mappa i valori RAW di MiniMax
    (`Preparing`/`Queueing`/`Processing`/`Success`/`Fail`) su quelli
    normalizzati.
  - `file_id`: `str | None`, popolato quando `status == "success"`.
  - `raw`: il `dict` JSON grezzo della risposta MiniMax.
- `download_file(file_id: str) -> bytes`: chiama l'endpoint
  `/v1/files/retrieve` per ottenere il `download_url` del `.mp4`, poi lo
  scarica con una GET semplice (`plain.get(download_url)`,
  `minimax_service.py:189-200`) e ne ritorna i bytes.

Errori HTTP/timeout sono propagati come eccezioni; il chiamante (worker)
li intercetta e marca la clip `failed` con `error_message`.

---

## `app/services/avatar_clip_worker.py`

**Scopo**: worker singleton in-process che processa le clip
`pending`/`processing`. Lanciato dal `lifespan` di `app/main.py`.

### Esporta

- `start(app: FastAPI) -> None`: crea un `asyncio.Task` e lo memorizza in
  `app.state.avatar_clip_worker_task`. Idempotente: se già attivo, no-op.
- `stop(app: FastAPI) -> None`: cancella il task e `await` il join.

### Loop principale

```python
while not stop_event.is_set():
    async with AsyncSessionLocal() as db:
        await _tick(db)
    await asyncio.sleep(MINIMAX_POLL_INTERVAL_SECONDS)
```

`_tick`:

1. Se `MINIMAX_API_KEY` è vuoto → return (clip restano `pending`).
2. Carica clip in `pending` senza `minimax_task_id`: per ognuna chiama
   `start_video_generation(image_url=..., prompt=...,
   last_frame_image=image_url)` — la stessa URL avatar come first e last
   frame attiva la modalità FLF di Hailuo-02 (clip loopabili). Salva
   `minimax_task_id`, marca `status=processing`, `started_at=now`.
3. Carica clip in `processing` con `minimax_task_id`: chiama
   `query_task_status`. Se `Success`, scarica il file via
   `download_file`, lo scrive con `storage_service.save_bytes` sotto
   `avatars/<user_id>/clip_<position>.mp4`, marca `status=ready`,
   `completed_at`, `video_path`. Se `Fail`, marca `status=failed` con
   `error_message`.
4. Aggiorna `avatars.clips_status` aggregato (`ready` se tutte 5 ready,
   `partial` se mix ready/failed, `failed` se tutte failed,
   `processing` se almeno una in processing, altrimenti `pending`).
5. Commit. Eccezioni dentro il tick sono loggate e non interrompono il
   loop.

### Recovery al restart

Tutto lo stato è in DB: al riavvio, le clip in `processing` con
`minimax_task_id` continuano dal polling, quelle `pending` ripartono.

---

## `app/services/runpod_tts_client.py`

**Scopo**: client HTTP del servizio TTS XTTS-v2 su **RunPod Serverless
(GPU)**. Sostituisce il vecchio `xtts_voice_clone_service` in-process: la
sintesi vocale gira ora su GPU remota (handler nella cartella `XTTS/` del
repo), questo modulo è un client puro — nessuna dipendenza torch/coqui.

### Costanti / eccezioni

- `SAMPLE_RATE = 24000`.
- `RunpodTtsError` (base, recuperabile), `RunpodNotConfiguredError`,
  `RunpodJobFailedError`, `RunpodTimeoutError`.

### Funzioni

- `is_configured() -> bool`: True se `RUNPOD_API_KEY` e
  `RUNPOD_TTS_ENDPOINT_ID` sono entrambi presenti.
- `synthesize_lesson_audio(*, speech_raw, voice_sample_path,
  language_code, on_segment_progress=None) -> tuple[dict[str, np.ndarray],
  int]`: sintetizza l'audio di **tutti** i segment con un solo job.
  Ritorna `({segment_id: ndarray float32 mono}, 24000)`.

Dettagli del contratto:

- **Un job per video** (non uno per segment). Il payload contiene
  `language_code`, `voice_sample_url`, `segments` (lista
  `{segment_id, text}` non vuoti).
- Il **campione vocale viaggia come URL pubblico** (`/uploads/...`), non
  base64 inline: un audio di pochi MB in base64 sfora il limite di
  payload di `/run`. Il worker GPU lo scarica via HTTP.
- L'handler restituisce l'audio **per chunk** (FLAC base64, con
  `chunk_index` per segment): il client li raggruppa per `segment_id`,
  li ordina e li concatena.
- Consuma lo **stream incrementale** `/stream/{job_id}` per il progress
  per-segment; a job `COMPLETED` continua a drenare lo stream finché una
  risposta torna senza nuovi item (altrimenti perde i chunk finali
  bufferizzati). Se `/stream` fallisce, fallback a `/status` polling.
- **Controllo di completezza**: ogni `segment_id` richiesto DEVE avere
  audio, altrimenti `RunpodJobFailedError` (un audio incompleto darebbe
  un video monco — il worker fa auto-retry).

---

## `app/services/lesson_audio_cache.py`

**Scopo**: cache su disco dell'audio TTS delle lezioni, per evitare di
richiamare RunPod (costo GPU + attesa) quando la parte audio di un video
non è cambiata.

L'audio sintetizzato viene salvato come un WAV per segment
(`seg_NNN.wav`, PCM_16 @ 24000 Hz) + un `manifest.json`, sotto
`{upload_root}/lesson_audio/{course_id}/{lesson_id}/`.

### Funzioni

- `compute_cache_key(*, speech_raw, voice_sample_path, language_code)
  -> str`: hash SHA-256 di testo dei segment (ordinati per id), lingua e
  contenuto del campione vocale. Se uno qualunque cambia la chiave cambia
  → la cache si invalida da sola.
- `load(course_id, lesson_id, *, cache_key) -> dict[str, np.ndarray] |
  None`: ricarica l'audio se il manifest combacia; `None` se
  assente/diverso/incompleto/corrotto.
- `save(course_id, lesson_id, *, cache_key, audio_per_segment) -> None`:
  sovrascrive la cache della lezione (un WAV per segment + manifest).

---

## `app/services/lesson_slides_video_render_service.py`

**Scopo**: render Playwright delle slide come PNG per il video MP4.
Riusa **al 100%** la pipeline del PDF slide (Fase 4): stesso template
(`lesson_slides_pdf.html.j2`), stesso pre-render delle figure → SVG
(`render_figure_map` del registro `figure_render_service`: Mermaid,
Vega-Lite, DOT, `function`, tutte in `<img data:svg>` con la cornice
«Figura.» senza numero), stessa risoluzione di asset (formule LaTeX →
SVG MathJax con fallback MathML, immagini caricate). Sostituisce il
vecchio `lesson_slides_png_service.py` (template custom, eliminato).

### Costanti

- `VIDEO_WIDTH = 1980`, `VIDEO_HEIGHT = 1400` — A4 landscape 297:210.

### Funzione

- `render_slides_to_png(db, *, course, lesson, output_dir,
  public_base_url=None) -> tuple[list[Path], list[str]]`: genera l'HTML
  PDF (con `enable_split=False`: 1 slide JSON → 1 frame video), inietta
  un override CSS che neutralizza i page-break PDF e scala ogni `.slide`
  per riempire il viewport 1980×1400, e screenshotta ogni slide.
  Ritorna `(png_paths, slide_id_order)` parallele, 1:1 con
  `slides_raw.slides[].slide_id`.

Lo screenshot Playwright gira in un loop dedicato
(`ProactorEventLoop` su Windows per `subprocess_exec`), invocato via
`asyncio.to_thread`.

---

## `app/services/lesson_video_compose_service.py`

**Scopo**: composizione del MP4 finale (slide PNG + audio TTS) via
ffmpeg.

### Errori

- `VideoComposeError` — errore di composizione, recuperabile.

### Funzioni

- `compose_lesson_video(*, lesson_speech_raw, png_paths, slide_id_order,
  audio_per_segment, audio_sample_rate, output_path, on_progress=None)
  -> dict`: per ogni slide concatena i WAV TTS dei suoi `segment_ids`
  (da `speech.slide_to_segments_map`; slide senza segment → 2 s di
  silenzio), produce un MP4 per slide (`ffmpeg -loop 1` immagine + audio,
  `-tune stillimage`) e fa il concat finale via demuxer `concat`
  (`-c copy`, niente re-encode). Ritorna metadata `audio_duration_s`,
  `video_duration_s`, `encode_duration_ms`, `num_segments_encoded`,
  `file_size_bytes`.
- `compose_lesson_video_sync(**kwargs) -> dict`: wrapper sync che crea un
  `ProactorEventLoop` dedicato (su Windows serve per `subprocess_exec`);
  il worker lo invoca via `asyncio.to_thread`.
- `parse_speech_raw(raw) -> dict`: normalizza `speech_raw` (dict o
  stringa JSON).

---

## `app/services/course_lesson_video_service.py`

**Scopo**: API pubblica + helper per la Fase 6 (generazione video MP4).
Usato dalle rotte; il rendering vero è nel worker.

### Funzioni

- `request_lesson_video` / `request_all_lessons_video`: enqueue
  (`video_status='pending'`); validano le pre-condizioni (speech+slides
  `approved`, voice sample presente, lezione non `is_assessment`) e
  sollevano `ConflictError` con `code` specifico
  (`speech_not_approved`, `slides_not_approved`, `voice_sample_missing`,
  `lesson_is_assessment_not_eligible`, ...).
- `cancel_lesson_video` / `cancel_all_lesson_videos`: `pending`/
  `processing` → `cancelled` (idempotente).
- `load_course_full`, `get_lesson_or_404`, `resolve_assignee_avatar`,
  `resolve_voice_sample_path`: helper di caricamento (la voce è
  `Avatar.audio_path` dell'assegnatario del corso).
- `video_relative_path` / `video_absolute_path` / `video_public_url`:
  helper di path (`lesson_videos/{course_id}/{lesson_id}.mp4`).
- `build_status_out` / `build_batch_out` / `is_lesson_eligible`:
  costruiscono i DTO (`LessonVideoStatusOut`, `LessonVideoBatchOut`) con
  `is_stale`, `eligible_count`, `aggregate_progress`.
- `save_video_metadata`: persistenza finale post-encoding (non commita).

---

## `app/services/course_lesson_video_worker.py`

**Scopo**: worker async della Fase 6, lanciato in `app.main.lifespan`.
Pattern speculare ai worker delle Fasi 2-5: semaphore + set `_inflight`
+ claim atomico via status.

3 fasi con cancel-check tra una e l'altra:

1. **TTS** (0→60%): audio dei segment dalla cache su disco se possibile,
   altrimenti via `runpod_tts_client` (streaming → progress per-segment),
   poi salvato in cache.
2. **Slide PNG** (60→80%): `lesson_slides_video_render_service`.
3. **Encoding** (80→100%): `lesson_video_compose_service`.

Caratteristiche:

- Cap di concorrenza `course_lesson_video_max_concurrency` (default 1).
- **Auto-retry trasparente**: errori recuperabili (timeout/errore
  RunPod, errore ffmpeg) con `attempts < course_lesson_video_auto_retry_max`
  (default 3) → status riportato a `pending`; pre-condizioni mancanti →
  `failed` terminale.
- **Nota tecnica**: il callback di progress dell'encoding è invocato dal
  thread di compose; il worker cattura il loop principale (proprietario
  del pool asyncpg) con `asyncio.get_running_loop()` e usa
  `run_coroutine_threadsafe`, altrimenti l'update DB finirebbe su un loop
  sbagliato (`Future attached to a different loop`).

---

## `app/services/course_lesson_avatar_video_service.py`

**Scopo**: API pubblica + helper per la Fase 6b ("Video con Avatar").
Riusa `load_course_full`, `get_lesson_or_404`, `resolve_assignee_avatar`
da `course_lesson_video_service`.

### Funzioni

- `request_lesson_avatar_video` / `request_all_lessons_avatar_video`:
  enqueue con validazione pre-condizioni (`lesson_video_not_ready`,
  `avatar_clips_not_ready`, `lesson_is_assessment_not_eligible`, ...).
- `cancel_lesson_avatar_video` / `cancel_all_lesson_avatar_videos`:
  `pending`/`processing` → `cancelled`.
- `count_ready_clips` / `avatar_is_ready`: eleggibilità dell'avatar
  (≥ 1 clip MiniMax `ready` con file).
- `avatar_clips_dir(user_id)` / `avatar_musetalk_clips_dir(user_id,
  resolution)`: path delle clip originali / ridimensionate per MuseTalk.
- `avatar_video_relative_path` / `avatar_video_absolute_path` /
  `avatar_video_public_url`: helper di path
  (`lesson_avatar_videos/{course_id}/{lesson_id}.mp4`).
- `build_status_out` / `build_batch_out` / `is_lesson_eligible`: DTO
  (`LessonAvatarVideoStatusOut`, `LessonAvatarVideoBatchOut`).
- `save_avatar_video_metadata`: persistenza finale post-overlay.

---

## `app/services/course_lesson_avatar_video_worker.py`

**Scopo**: worker async della Fase 6b, lanciato in `app.main.lifespan`.
Pattern speculare a `course_lesson_video_worker` (semaphore, claim
atomico, auto-retry, cancel-check). Cap di concorrenza 1.

3 fasi con cancel-check:

1. **Preparazione** (1→8%): `_prepare_musetalk_clips` ridimensiona le
   clip dell'avatar a `avatar_video_clip_resolution` (default 640;
   le clip MiniMax 1080×1080 farebbero sforare il tetto di 60 min del
   job RunPod) — una clip è riconvertita solo se cambia il sorgente, così
   l'hash del set resta stabile e il preprocessing MuseTalk resta in
   cache; `_extract_audio` estrae la traccia audio dal video MP4 della
   lezione (WAV mono 16 kHz, con `aresample=async=1` per **non
   compattare** i gap di timeline — altrimenti l'avatar si
   desincronizza).
2. **Lip-sync MuseTalk** (10→85%): `_run_musetalk_subprocess` lancia il
   client vendored come subprocess isolato (vedi sotto).
3. **Overlay** (86→100%): `_overlay_avatar` sovrappone con ffmpeg
   l'avatar (quadrato in basso a destra, lato `avatar_video_overlay_scale`
   della larghezza, margine `avatar_video_overlay_margin`) al video della
   lezione, `-c:a copy` conserva la traccia audio.

Caratteristiche:

- Un **timeout RunPod (`TIMED_OUT`)** è errore **terminale** (si
  ripeterebbe identico); gli errori transitori vanno in auto-retry
  (`course_lesson_avatar_video_auto_retry_max`, default 3).
- Diagnostica `avatar_video_av_diag`: dopo il lip-sync il worker logga
  fps/durata di audio estratto, video assemblato, output MuseTalk e
  video lezione (osservabilità sul drift A/V).

---

## `app/services/course_duplication_paths.py`

**Single-source-of-truth dichiarativa** dei path JSON da tradurre per
ogni struttura JSONB del corso. Esporta costanti come
`CONTENT_RAW_TRANSLATE_PATHS`, `SLIDES_RAW_TRANSLATE_PATHS`,
`SPEECH_RAW_TRANSLATE_PATHS`, `ARCHITECTURE_TRANSLATE_PATHS`,
`GLOSSARY_TRANSLATE_PATHS`, `DOCUMENT_SUMMARY_TRANSLATE_PATHS`,
`ASSESSMENT_RAW_TRANSLATE_PATHS`, `LESSON_JSONB_TRANSLATE_PATHS`,
`MODULE_TRANSLATE_FIELDS`, `LESSON_TRANSLATE_FIELDS`,
`COURSE_METADATA_TRANSLATE_FIELDS`. Sintassi path:
- `"a"` → object field string
- `"a.b"` → nested object
- `"a[]"` → array di stringhe
- `"a[].b"` → field di oggetti in array

Tutto ciò che NON è dichiarato resta AS-IS (UUID, `content` delle figure
— codice Mermaid, spec Vega-Lite, sorgente DOT, spec `function` —,
LaTeX, format Literal, ID, numeri, booleani). I testi interni alle
figure non sono tradotti dalla duplicazione (TODO tracciato in
[Courses 15](../courses/15-course-duplication.md) e
[Courses 17](../courses/17-visual-figures.md)).

Vedi [Courses 15 — Duplicazione corso](../courses/15-course-duplication.md)
per la tabella completa di cosa è tradotto vs cosa è preservato.

---

## `app/services/course_duplication_service.py`

**Scopo**: orchestrazione della **duplicazione corso in altra lingua**.

API pubblica:
- `request_course_duplication(db, source_course, target_language_code,
  actor_id) → CourseDuplicationJob` — crea job `pending`. Valida:
  lingua diversa, lingua attiva in DB, no job già attivo per stessa
  coppia (controllo applicativo + unique parziale DB).
- `cancel_duplication(db, job, actor_id) → CourseDuplicationJob` —
  idempotente. Mette `pending|processing` → `failed` con error
  "Annullata dall'utente".
- `list_duplications_for_course(db, course_id) → list[Job]` — qualsiasi
  stato, source o target.

Helper interni chiamati dal worker:
- `_clone_course_structure(db, source, target_language_code, job)` —
  clona shell del target con metadati, taxonomy, configurazione AI.
  Documenti copiati fisicamente su filesystem (`shutil.copy2` con nuovo
  `filename_stored = uuid.uuid4().hex + ext`). I JSONB testuali
  (`architecture_raw`, `content_raw`, `slides_raw`, `speech_raw`,
  `glossary_raw`, `documents.summary`) sono copiati AS-IS via
  `_deepcopy_json` — saranno tradotti in-place dalle fasi successive.
  Video, avatar video e PDF resettati a `empty` con path `None`.
- `_translate_jsonb_inplace(data, paths, source_lang_*, target_lang_*)`
  — estrae le foglie testuali secondo `paths`, fa un singolo
  `translate_batch` (efficienza token), e riapplica in-place. Usa
  `sqlalchemy.orm.attributes.flag_modified` per marcare JSONB dirty.
- 7 funzioni granulari: `_translate_course_metadata`,
  `_translate_architecture`, `_translate_lesson` (con `phase ∈
  meta|content|slides|speech`), `_translate_glossary` (azzera anche
  `terms[].translation` perché era già una traduzione contestuale),
  `_translate_document_summaries` (aggiorna anche
  `summary.detected_language` al nuovo codice).
- `_finalize(db, source, target)` — allinea
  `target.status = source.status` via `advance_course_status`
  (monotonia: video/avatar restano `empty`).

---

## `app/services/course_duplication_worker.py`

**Scopo**: worker async dedicato, lanciato in `app.main.lifespan`.
Pattern speculare a `course_lesson_content_worker` ma scoped a livello
JOB (non lezione). Cap globale 1
(`course_duplication_max_concurrent_jobs`); dentro al job le lezioni
sono tradotte in parallelo cap 3 per fase
(`course_duplication_lesson_translate_concurrency`).

Pipeline `_process_one(job_id)` in 8 fasi: `loading_source` (2%) →
`cloning_structure` (5%→8%) → `translating_architecture` (12%→20%) →
`translating_lesson_metadata` (22%→28%) → `translating_content`
(30%→50%) → `translating_slides` (55%→70%) → `translating_speech`
(75%→85%) → `translating_glossary_documents` (88%→95%) → `finalizing`
(95%→100%). `_check_cancelled` fra le fasi per uscire pulitamente.

Auto-retry trasparente (`course_duplication_auto_retry_max=5`):
`OpenAIError` transient → job torna a `pending` con `attempts+1`. Cap
esaurito → `failed`. `OpenAINotConfiguredError` è terminale subito.

Audit log: `course.duplicate.request`, `course.duplicate.completed`,
`course.duplicate.failed`, `course.duplicate.cancelled`.

---

## `app/musetalk_client/` — client MuseTalk vendored

**Non è codice a4u da modificare.** È una **copia verbatim** del client
`scripts/client/` del progetto esterno **MuseTalk-API**:

```
app/musetalk_client/
├── README.md                       # "vendored, NON modificare"
└── scripts/
    ├── __init__.py
    └── client/
        ├── __init__.py
        ├── synth_random_lipsync.py  # entry-point CLI
        ├── runpod_client.py         # R2 (boto3) + RunPod API (requests)
        ├── video_assembler.py       # probe/sample/concat/trim ffmpeg
        └── clip_manifest.py         # cache preprocessing per set di clip
```

Principi dell'integrazione:

- I file non vengono **mai modificati a mano**: per aggiornarli si
  ri-copia dal progetto sorgente.
- Il client gira come **subprocess isolato** —
  `python -m scripts.client.synth_random_lipsync` con `cwd` sulla
  cartella vendored (così `import scripts.client...` si risolve) — e
  legge la configurazione solo da variabili d'ambiente, che
  `course_lesson_avatar_video_worker` gli passa esplicitamente
  (credenziali RunPod/R2).
- Dipendenze del client: `boto3` (R2, S3-compatible) e `requests` (HTTP
  RunPod), dichiarate in `backend/pyproject.toml`.

> Il vecchio TTS in-process `xtts_voice_clone_service` (torch + coqui nel
> container) è stato **rimosso**: la sintesi vocale gira ora su RunPod
> GPU serverless via `runpod_tts_client`.

---

## `app/services/auth_service.py`

**Scopo**: login, refresh con rotation, revoca refresh token. Lockout/audit/
rate per fallimenti.

### Costanti

- `log = get_logger("app.auth")`.

### Helper privati

#### `_now() -> datetime`

`datetime.now(tz=UTC)`.

#### `_record_login_attempt(db, *, email, ip, success) -> None`

Inserisce una riga in `login_attempts`. Email lowercased.

### Funzioni pubbliche

#### `login(db, *, email, password, ip, user_agent) -> tuple[User, str, str]`

`async`. Esegue il login; restituisce `(user, access_token, refresh_raw)`.

1. Normalizza email lowercase.
2. Carica user con `func.lower(User.email) == email_norm` (CITEXT lo gestisce
   già, ma resta idempotente).
3. Se utente lockato (`locked_until > now`): `RateLimitedError(code=
   "account_locked")`.
4. Se utente assente, inattivo o password errata:
   - registra tentativo fallito;
   - incrementa `failed_login_count`;
   - se raggiunge `LOGIN_LOCKOUT_THRESHOLD`:
     - setta `locked_until = now + LOGIN_LOCKOUT_MINUTES`;
     - resetta `failed_login_count`;
     - audit `auth.login.locked`.
   - audit `auth.login.failure`;
   - `await db.commit()` (cruciale: deve persistere anche se la richiesta
     fallisce);
   - `AuthenticationError(code="invalid_credentials")`.
5. Reset `failed_login_count = 0`, `locked_until = None`,
   `last_login_at = now`.
6. Registra tentativo OK.
7. Crea access token + refresh token. Salva `RefreshToken(id=jti,
   token_hash=hash_secret(raw), expires_at, user_agent, ip)`.
8. Audit `auth.login.success`.
9. Restituisce.

#### `rotate_refresh(db, *, refresh_token, ip, user_agent) -> tuple[User, str, str]`

`async`. Implementa rotation + reuse-detection.

1. `decode_token(refresh_token, expected_type="refresh")`.
2. Estrae `user_id`, `jti` (UUID).
3. `db.get(RefreshToken, jti)`:
   - `None` o `user_id` mismatch → `AuthenticationError(code="token_unknown")`.
4. Verifica `token_hash == hash_secret(refresh_token)` → mismatch =
   `token_invalid`.
5. Se `revoked_at != None` → **reuse detection**:
   - revoca tutti i refresh dell'utente non ancora revocati;
   - audit `auth.refresh.reuse_detected`;
   - `AuthenticationError(code="token_reused")`.
6. Se `expires_at <= now` → `code="token_expired"`.
7. Verifica `User` ancora attivo.
8. Marca il vecchio refresh `revoked_at = now`.
9. Genera nuovo (access, refresh, expires_at).
10. Salva nuovo `RefreshToken`. Setta `replaced_by_id` sul vecchio.
11. Audit `auth.refresh.success`.
12. Restituisce.

#### `revoke_refresh_token(db, *, refresh_token, ip) -> None`

`async`. Logout. Se token mancante o invalido, no-op silenzioso. Altrimenti
marca `revoked_at = now` e audit `auth.logout`.

---

## `app/services/org_service.py`

**Scopo**: CRUD organizzazioni con soft-delete e audit.

### Funzioni

#### `list_organizations(db, *, page, page_size, q=None) -> tuple[list[Organization], int]`

`async`. Lista paginata con filtro `q` opzionale (LIKE su `name`). Filtra
`deleted_at IS NULL`. Ritorna `(items, total)`.

#### `get_organization(db, org_id) -> Organization`

`async`. Carica per id; 404 `NotFoundError(code="organization_not_found")`
se assente o soft-deleted.

#### `create_organization(db, *, payload, logo_path, actor_id) -> Organization`

`async`. Crea `Organization(**payload, logo_path, created_by_user_id)`.
Subito dopo `db.add(org)` e prima del commit, crea anche un record
`OrganizationCourseSettings(organization_id=org.id)` con i default
business (`modules_per_cfu=1`, `lessons_per_module=8`,
`lesson_duration_minutes=15`, `assessment_lesson_enabled=true`,
`multiple_choice_questions_count=30`, `open_questions_count=6`), così
ogni nuova org nasce già con la sua configurazione corsi. Audit
`organization.create` con metadata `{name, email}`.

#### `update_organization(db, *, org, payload, actor_id, new_logo_path=None) -> Organization`

`async`. Aggiorna campi anagrafica. Se `new_logo_path` è valorizzato (anche
`None` esplicito), aggiorna il logo. Audit `organization.update`.

#### `soft_delete_organization(db, *, org, actor_id) -> None`

`async`. Setta `org.deleted_at = now`. Audit `organization.delete`.

---

## `app/services/organization_course_settings_service.py`

**Scopo**: lettura/aggiornamento idempotente dei parametri di
configurazione dei corsi per una organizzazione (1:1 con `Organization`).

### Funzioni

#### `get_or_create_settings(db, organization_id: UUID) -> OrganizationCourseSettings`

`async`. Cerca la riga per `organization_id`. Se assente (org creata
prima della migrazione `0007`) la crea **lazy** con i default business
e la restituisce. Garantisce quindi che ogni org abbia sempre una
configurazione consultabile.

#### `update_settings(db, settings, payload, *, actor_id) -> OrganizationCourseSettings`

`async`. Applica i campi di `OrganizationCourseSettingsUpdate` alla
riga, esegue `db.flush()` e `db.refresh(settings)` per riallinearsi al
DB. Scrive audit `organization.course_settings.update` con metadata
`{"changes": <diff>}`.

---

## `app/services/membership_service.py`

**Scopo**: iscrizioni, change role, transfer creator. Vincoli rank/creator
unico.

### Funzioni

#### `get_role_by_code(db, code) -> OrganizationRole`

`async`. Carica per codice. 404 se assente.

#### `enroll_user(db, *, user_id, organization_id, role_code, actor_id) -> Membership`

`async`. Iscrive un utente esistente come membro.

1. Carica `User` (404 altrimenti).
2. Carica ruolo target.
3. Verifica che l'utente non sia già membro (`uq_memberships_user_organization`).
   Se sì → `ConflictError(code="already_member")`.
4. Se `role_code == creator` e l'org ha già un creator → `ConflictError(
   code="creator_exists")`. (Use case: non si crea un secondo creator
   tramite enroll.)
5. Crea `Membership(user_id, organization_id, role_id, joined_by_user_id=actor_id)`.
6. Audit `membership.create` con `metadata.role`.
7. Restituisce.

#### `change_role(db, *, membership, new_role_code, actor_user, actor_membership) -> Membership`

`async`. Cambia ruolo di un membro applicando i vincoli rank.

1. Carica nuovo ruolo. Se `creator` → `PermissionDeniedError(code=
   "creator_via_transfer")` (usare l'endpoint dedicato).
2. Se attore non è platform admin:
   - richiede `actor_membership` (404 altrimenti);
   - se attore **non** è creator:
     - `ROLE_RANK[new_role.code] >= ROLE_RANK[actor_role.code]` (cioè non
       puoi promuovere a un ruolo superiore al tuo);
     - non puoi modificare un membro con rank inferiore al tuo (= ruolo
       superiore al tuo).
3. Aggiorna `membership.role_id`. Audit `membership.role_change`.
4. Restituisce.

#### `remove_membership(db, *, membership, actor_user) -> None`

`async`. Rimuove un membro.
- Se il target è `creator` → `ConflictError(code="cannot_remove_creator")`
  (richiede transfer prima).
- `db.delete(membership)`. Audit `membership.remove`.

#### `transfer_creator(db, *, organization_id, actor_user, actor_membership, target_user_id) -> tuple[Membership, Membership]`

`async`. Atomico nella stessa sessione.

1. Carica ruoli `creator` e `org_admin`.
2. Verifica `actor_membership.role_id == creator.id` (chi chiama deve essere
   il creator dell'org). Altrimenti `PermissionDeniedError(code=
   "not_creator")`. *Eccezione*: nel router, se l'attore è platform admin
   e non è membro, il router carica come `actor_membership` la membership
   del creator corrente.
3. Verifica `target_user_id != actor_user.id` (no self-transfer).
4. Carica membership del target (404 se non membro).
5. Scambia: `actor.role_id = org_admin.id`, `target.role_id = creator.id`.
6. Audit `organization.transfer_creator`.
7. Restituisce `(actor_membership_aggiornata, target_membership_aggiornata)`.

---

## `app/services/invitation_service.py`

**Scopo**: creazione/accettazione inviti.

### Costanti

- `INVITATION_TTL_DAYS = 7`.

### Funzioni

#### `_now() -> datetime`

#### `create_invitation(db, *, organization_id, email, role_code, actor_id) -> tuple[Invitation, str]`

`async`. Crea l'invito.

1. Se `role_code == creator` → `ValidationAppError(code=
   "cannot_invite_creator")`.
2. Carica ruolo target (404 altrimenti).
3. `raw_token = generate_url_safe_token()`.
4. Crea `Invitation(token_hash=hash_secret(raw_token), expires_at=now+7gg)`.
5. Audit `invitation.create` con `email`, `role`.
6. Restituisce `(invitation, raw_token)`. Il chiamante usa `raw_token` per
   costruire `accept_url`.

#### `accept_invitation(db, *, token, full_name, password, ip, user_agent) -> tuple[User, Membership]`

`async`. Accetta un invito.

1. Carica per `token_hash = hash_secret(token)`. 404 altrimenti.
2. 409 se `accepted_at`/`revoked_at` settati o `expires_at <= now`.
3. Cerca utente con quell'email.
   - Se assente: richiede `full_name` + `password` validi (valida con
     `is_password_strong`); altrimenti `ValidationAppError`.
   - Crea `User(...)`.
4. Se l'utente è già membro dell'org → marca invitation accepted_at e
   restituisce existing.
5. Crea `Membership(role_id=invitation.role_id, joined_by_user_id=
   invitation.created_by_user_id)`.
6. Setta `invitation.accepted_at = now`.
7. Audit `invitation.accept`.
8. Restituisce `(user, membership)`.

---

## `app/services/permission_service.py`

**Scopo**: modificare default globali, override per organizzazione, override
per membership.

### Funzioni

#### `_ensure_codes_exist(db, codes) -> dict[str, UUID]`

`async`. Verifica che tutti i codici siano in `ALL_PERMISSION_CODES`. Se
no, `ValidationAppError(code="unknown_permissions",
meta={"unknown": [...]})`. Restituisce `{code: permission_id}`.

#### `update_role_default_permissions(db, *, payload: RolePermissionDefaultUpdate, actor_id) -> None`

`async`. Modifica `role_permissions` (livello globale).

1. Carica ruolo per codice. 404 altrimenti.
2. Se ruolo è `creator`, verifica che `payload.permissions` contenga
   `CREATOR_REQUIRED_PERMISSIONS`. Altrimenti `ConflictError(code=
   "creator_required_permissions")`.
3. `_ensure_codes_exist(payload.permissions)`.
4. DELETE righe di `role_permissions` per quel ruolo.
5. INSERT nuove righe per ogni `permission_id`.
6. Audit `permission.role_defaults.update`.

#### `upsert_org_role_permissions(db, *, organization_id, role_code, overrides, actor_id) -> None`

`async`. Modifica `organization_role_permissions` per l'org+ruolo.

1. Carica ruolo. 404 altrimenti.
2. Se `creator` e overrides revocano `CREATOR_REQUIRED_PERMISSIONS` →
   `ConflictError`.
3. `_ensure_codes_exist(overrides.codes)`.
4. DELETE righe esistenti per `(org, role)`.
5. INSERT nuove con `granted` come specificato.
6. Audit `permission.org_role.update`.

#### `upsert_membership_permissions(db, *, membership, overrides, actor_id) -> None`

`async`. Modifica `membership_permission_overrides` per il singolo membro.

1. Se il membership è del creator, vincolo come sopra.
2. `_ensure_codes_exist`.
3. DELETE/INSERT analoghi.
4. Audit `permission.membership.update`.

#### `get_role_default_permissions(db, *, role_code) -> list[str]`

`async`. Restituisce i codici default per il ruolo (legge `role_permissions`).

#### `list_organization_role_overrides(db, *, organization_id, role_code) -> list[PermissionOverrideEntry]`

`async`. Restituisce gli override per (org, ruolo) come lista di `(code, granted)`.

#### `list_membership_overrides(db, *, membership_id) -> list[PermissionOverrideEntry]`

`async`. Restituisce gli override per il membership.

---

## `app/services/template_service.py`

**Scopo**: list/get/create/delete template slide e PDF. L'**update** è
implementato direttamente nei router perché coinvolge multipart con
flag di rimozione (più chiaro inline). Restano qui list/get/create/delete.

### Funzioni

#### `list_slide_templates(db, organization_id) -> list[SlideTemplate]`

`async`. Ordina per nome.

#### `list_pdf_templates(db, organization_id) -> list[PdfTemplate]`

`async`. Idem.

#### `get_slide_template(db, organization_id, template_id) -> SlideTemplate`

`async`. 404 se assente o di altra org.

#### `get_pdf_template(db, organization_id, template_id) -> PdfTemplate`

`async`. Idem.

#### `create_slide_template(db, *, organization_id, payload, background_image_path, logo_left_path, logo_right_path, actor_id) -> SlideTemplate`

`async`. Crea, audit `template.slide.create` con metadata `name`.

#### `delete_slide_template(db, *, tpl, actor_id) -> None`

`async`. Cancella i 3 file (background + 2 loghi) via `delete_upload`,
poi `db.delete(tpl)`. Audit `template.slide.delete`.

#### `create_pdf_template(...)` / `delete_pdf_template(...)`

Analoghi per PDF.

---

## `app/services/avatar_service.py`

**Scopo**: gestione dell'avatar utente (1:1 con `User`). L'avatar è
globale (cross-org), quindi le funzioni operano su `user_id` e non su
`organization_id`. Carica/salva immagine + audio, avvia generazione
delle 5 clip.

### Funzioni

#### `get_avatar_for_user(db, *, user_id) -> Avatar | None`

`async`. Carica l'avatar dell'utente con eager-load delle `clips`
ordinate per `position`. Ritorna `None` se assente.

#### `upsert_my_avatar(db, *, user_id, image, audio, audio_lang, actor_id) -> Avatar`

`async`. Crea l'avatar se non esiste, altrimenti aggiorna i campi
forniti. La firma non accetta più `audio_text` (rimosso).

1. Carica avatar esistente (o ne crea uno nuovo `Avatar(user_id=...)`).
2. Se `image` è fornito: salva via `file_service.save_upload_image(
   image, subdir=f"avatars/{user_id}", filename_stem="image")`. Se
   l'immagine cambia, cancella le clip vecchie (e i loro video) e
   ricrea 5 righe `pending`.
3. Se `audio` è fornito: salva via `file_service.save_upload_audio(
   audio, subdir=f"avatars/{user_id}", filename_stem="audio")`.
   Aggiorna `audio_lang` se fornito.
4. Setta `clips_status="pending"` se sono state ricreate le clip.
5. Audit `avatar.create` o `avatar.update`.
6. Restituisce l'avatar con `clips`.

#### `regenerate_clips(db, *, user_id, actor_id) -> Avatar`

`async`. Cancella tutte le righe `avatar_clips` esistenti (e i video su
disco) e le ricrea in `pending` partendo dai prompt admin attivi
(snapshot `prompt_text`). Setta `clips_status="pending"`. Audit
`avatar.clips.regenerate`. **202**.

#### `delete_avatar_for_user(db, *, user_id, actor_id) -> None`

`async`. Carica l'avatar dell'utente. Cancella la riga (CASCADE rimuove
le clip) e poi rimuove ricorsivamente la cartella
`avatars/<user_id>/` via `storage_service.delete_directory`. Audit
`avatar.delete`.

---

## `app/services/avatar_config_service.py`

**Scopo**: CRUD + reorder dei `avatar_clip_prompts` e CRUD dei
`avatar_voice_scripts` (entrambi config admin di piattaforma).

### Funzioni — prompt clip

#### `list_prompts(db) -> list[AvatarClipPrompt]`

`async`. Ordina per `position`.

#### `create_prompt(db, *, payload, actor_id) -> AvatarClipPrompt`

`async`. Assegna `position = max(position) + 1`. Audit
`avatar_config.prompt.create`.

#### `update_prompt(db, *, prompt_id, payload, actor_id) -> AvatarClipPrompt`

`async`. 404 se non esiste. Aggiorna i campi forniti. Audit
`avatar_config.prompt.update`.

#### `delete_prompt(db, *, prompt_id, actor_id) -> None`

`async`. 404 se non esiste. Cancella la riga (FK `avatar_clips.prompt_id`
SET NULL preserva le clip storiche). Audit
`avatar_config.prompt.delete`.

#### `reorder_prompts(db, *, ordered_ids, actor_id) -> list[AvatarClipPrompt]`

`async`. Validazione: `ordered_ids` deve coincidere col set di id
esistenti. Riassegna `position` in base all'ordine. Audit
`avatar_config.prompt.reorder`.

### Funzioni — voice scripts

#### `list_voice_scripts(db) -> list[AvatarVoiceScript]`

`async`. Restituisce tutte le righe di `avatar_voice_scripts` ordinate
per `language_code`.

#### `get_voice_script(db, *, language_code) -> AvatarVoiceScript | None`

`async`. Carica per chiave esatta. Nessun fallback.

#### `get_voice_script_with_fallback(db, *, language_code: str | None) -> AvatarVoiceScript | None`

`async`. Risolve lo script da mostrare all'utente con la cascata:
1. lingua richiesta (`language_code` valorizzato),
2. lingua di default piattaforma (es. `it`),
3. qualsiasi script disponibile (prima riga in ordine alfabetico),
4. `None` se la tabella è vuota.

#### `upsert_voice_script(db, *, language_code, text, actor_id) -> AvatarVoiceScript`

`async`. Crea la riga se assente, altrimenti aggiorna il `text`. Audit
`avatar.config.voice_script.upsert`.

#### `delete_voice_script(db, *, language_code, actor_id) -> None`

`async`. 404 se assente. Cancella la riga. Audit
`avatar.config.voice_script.delete`.

---

## `app/services/openai_translate_service.py`

**Scopo**: client OpenAI per la traduzione automatica i18n. Wrapping di
`httpx.AsyncClient` con header `Authorization: Bearer <OPENAI_API_KEY>`
e `base_url` configurabile. Tutte le chiamate sono `async` e non toccano
il DB.

### Eccezioni

- `class OpenAITranslateError(Exception)` con attributi `status: int |
  None`, `message: str`, `payload: Any`. Errore base.
- `class OpenAINotConfiguredError(OpenAITranslateError)` — sollevata
  quando `OPENAI_API_KEY` è vuota.

### Helper privati

#### `_client(timeout: float = 120.0) -> httpx.AsyncClient`

Crea il client `httpx.AsyncClient` con `Authorization: Bearer ...` e
`base_url=settings.openai_base_url`. Se la key è vuota, solleva
`OpenAINotConfiguredError`.

#### `_system_prompt(source_lang, target_lang_code, target_lang_name) -> str`

Costruisce il system prompt strict con queste regole:

- preservare i placeholder i18next (`{{name}}`, `{{count}}`, ecc.) e i
  segnaposto HTML;
- mantenere identiche le **keys** del JSON, tradurre solo i **values**;
- tono UI professionale, naturale e idiomatico, non letterale;
- mantenere intatti termini tecnici (PDF, JSON, API, MiniMax, ecc.) e
  simboli;
- brand `a4u` lowercase;
- output **only** JSON object.

### Funzione pubblica

#### `translate_batch(*, items, source_lang_code, source_lang_name, target_lang_code, target_lang_name) -> dict[str, str]`

`async`. Invoca `POST /chat/completions` con `model=settings.openai_model`,
`response_format={"type": "json_object"}`, `temperature=0.2`. Skip delle
chiavi con value vuoto o non-stringa. Ritorna il dict `{key: translated}`.

---

## `app/services/i18n_service.py`

**Scopo**: gestione delle traduzioni i18n a DB e completamento via
OpenAI delle voci mancanti.

### Costanti

- `DEFAULT_LANG_CODE = "it"`.

### Helper privati

#### `_missing_or_fallback_keys(*, reference, target) -> dict[str, str]`

Restituisce solo le chiavi **mancanti o vuote** nel `target`
(rispetto al `reference`). **Non** include le chiavi il cui valore
coincide col reference: i valori identici sono spesso traduzioni
legittime — brand name, prestiti linguistici, stringhe tecniche.

#### `_chunk(items, size) -> list[dict[str, str]]`

Spezza il dict di chiavi in liste di sotto-dict di dimensione `size`
(usato per costruire i batch da inviare a OpenAI).

### Funzioni pubbliche

#### `count_untranslated_per_language(db, *, default_code='it') -> dict[str, int]`

`async`. Singola query che raggruppa in memoria per ogni lingua il
numero di chiavi mancanti o vuote rispetto al default. Usata da
`GET /admin/i18n/languages` per popolare `untranslated_count` di tutti
gli elementi della lista.

#### `count_untranslated_for_language(db, *, code, default_code='it') -> int`

`async`. Versione singola lingua, usata dagli endpoint puntuali.

#### `auto_translate_missing(db, *, language, actor_id, default_code='it', default_name='Italian') -> dict`

`async`. Pipeline completa di completamento via OpenAI:

1. Identifica le chiavi mancanti via `_missing_or_fallback_keys`.
2. Le batcha in chunk da `settings.openai_translate_batch_size`
   (default 80).
3. Per ogni batch chiama
   `openai_translate_service.translate_batch()`. Una
   `OpenAINotConfiguredError` interrompe immediatamente con
   `ValidationAppError(code="openai_not_configured")` (fail-fast: non ha
   senso ritentare i batch successivi). Altri `OpenAITranslateError`
   vengono raccolti in `errors` ma non interrompono il flusso.
4. Esegue upsert dei risultati via la `upsert_translations` esistente.
5. Audit `i18n.translations.auto_translate` con metadata
   `{requested, translated, upserted, skipped, errors[:5]}`.
6. Ritorna `{requested, translated, skipped, errors}`.

---

## Servizi del dominio Corsi

I services del dominio Corso sono documentati nella sezione dedicata
(sono ~25 file, troppo per inlinarli qui senza disorientare). Mappatura
sintetica:

### Foundation + Pre-processing (Iterazioni A-B)

| Service | Documentato in | Scopo |
|---|---|---|
| `course_service.py` | [Courses 01](../courses/01-data-model.md), [Courses 03](../courses/03-architecture-generation.md) | CRUD corso, list, dettaglio eager-loaded, upload documenti. In `update_course` il `payload.status` accetta solo `published`/`archived`; da terminale, un valore non terminale = riattivazione via `normalize_course_status_from_data` (valore richiesto ignorato, in audit come `requested`); altrimenti `409 invalid_status_transition`. Espone anche `add_document_from_bytes` (sibling di `add_document` che crea un `CourseDocument` da bytes invece che da `UploadFile`, usato dall'import paper — `course_service.py:746-804`) |
| `course_taxonomy_service.py` | [Courses 01](../courses/01-data-model.md) | CRUD term tassonomie + auto-create on demand |
| `course_document_worker.py` | [Courses 02](../courses/02-document-preprocessing.md) | Worker async pre-processing documenti (lifespan) |
| `document_extraction_service.py` | [Courses 02](../courses/02-document-preprocessing.md) | Estrazione testo da PDF/DOCX/DOC/RTF/TXT/MD via `asyncio.to_thread` |
| `openai_summarize_service.py` | [Courses 02](../courses/02-document-preprocessing.md) | Wrapper OpenAI summarize (Appendice A) con `response_format: json_schema` |
| `openai_client.py` | [Courses 02](../courses/02-document-preprocessing.md) | Modulo condiviso: `OpenAIError`, `OpenAINotConfiguredError`, `get_client()`, `apply_reasoning_effort()` |

### Ricerca paper scientifici (doc 16)

Ricerca, arricchimento, riassunto AI e import di paper accademici dentro
la tab Documenti di un corso. 3 endpoint sotto
`/orgs/{org_id}/courses/{course_id}/papers/` (`POST /search`,
`POST /ai-summary`, `POST /import`), permission `course:edit`. Deep-dive
completo in [Courses 16](../courses/16-paper-search.md); endpoint
riassunti anche in [Courses 05](../courses/05-api-reference.md).

| Service | Documentato in | Scopo |
|---|---|---|
| `openalex_client.py` | [Courses 16](../courses/16-paper-search.md) | Client OpenAlex (sorgente **primaria**): `search_works` con cursor pagination su `/works`, `download_pdf` del binario OA. `mailto:` nello User-Agent → polite pool |
| `openalex_search_service.py` | [Courses 16](../courses/16-paper-search.md) | Orchestrazione search di lista + `_clamp_relevance` (relevance_score sigmoid-like in `[0,1]`). Nessun enrichment in lista (evita ~40 chiamate per ricerca) |
| `semantic_scholar_client.py` | [Courses 16](../courses/16-paper-search.md) | Client S2 (secondario): `get_paper_by_doi` via `/graph/v1/paper/DOI:{doi}` → `tldr.text` + fallback `openAccessPdf.url`. Non bloccante (ritorna `None` su 404/error/timeout) |
| `crossref_client.py` | [Courses 16](../courses/16-paper-search.md) | Client Crossref (secondario): `get_work_by_doi` via `/works/{doi}` → abstract ripulito dai tag JATS, `subjects[]`, `references_count`. Non bloccante |
| `paper_enrichment_service.py` | [Courses 16](../courses/16-paper-search.md) | Merge **on-demand** OpenAlex + S2 + Crossref (`asyncio.gather(return_exceptions=True)` se DOI presente). Priorità abstract OpenAlex>Crossref>S2, oa_pdf_url OpenAlex>S2; tldr solo S2; subjects/references_count solo Crossref |
| `openai_paper_summary_service.py` | [Courses 16](../courses/16-paper-search.md) | Wrapper OpenAI riassunto AI del paper (`gpt-4o-mini`, `temperature=0.3`, `json_schema` strict, 4 sezioni). Prompt nella lingua del **corso**. Ritorna `(output, usage)`; l'endpoint scarta `usage`, niente persistenza DB |
| `paper_import_service.py` | [Courses 16](../courses/16-paper-search.md) | `import_paper`: se `oa_pdf_url` → `download_pdf` → `.pdf` (`application/pdf`, mode `pdf`); su errore download/validazione fallback graceful a `.md` di metadati (`text/markdown`, mode `metadata`). Entrambi via `course_service.add_document_from_bytes` → `summary_status='pending'` |

> L'import non genera summary: salva il `CourseDocument` con
> `summary_status='pending'` e la pipeline esistente
> `course_document_worker` prende in carico extract_text + summarize AI
> (vedi [Courses 02](../courses/02-document-preprocessing.md)).

### Fase 1 — Architettura

| Service | Documentato in | Scopo |
|---|---|---|
| `course_architecture_service.py` | [Courses 03](../courses/03-architecture-generation.md) | Trigger generazione architettura, approve, rigenerazione |
| `course_architecture_crud.py` | [Courses 04](../courses/04-manual-editing.md) | CRUD manuale moduli/lezioni con renumber + AI generate-lessons. `_ensure_editable` **data-based** (niente `EDITABLE_STATUSES`): 409 su `{draft, architecture_pending, published, archived}` o generazioni in volo per-modulo/per-lezione. `regenerate_module_lessons` resetta `lessons_structure_status='empty'` + `approved_at=None` |
| `course_architecture_worker.py` | [Courses 03](../courses/03-architecture-generation.md) | Worker async Fase 1 architettura (lifespan + ticker progress) |
| `openai_architecture_service.py` | [Courses 03](../courses/03-architecture-generation.md) | Wrapper OpenAI architettura (Fase 1) — gpt-5.5 |
| `openai_module_lessons_service.py` | [Courses 04](../courses/04-manual-editing.md) | Wrapper OpenAI lezioni di un modulo singolo (sync, ~20-30s) |

### Fase 2 — Struttura lezioni

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_structure_service.py` | [Courses 07](../courses/07-lesson-structure.md) | Orchestrazione + materializzazione + approve. Gate **per-modulo**: corso non terminale + rank ≥ `architecture_approved` + nessuna lezione del modulo con dispense (`module_has_content`); generate-all filtra gli eleggibili; approve-all tollerante (ignora gli `empty`) |
| `course_lesson_structure_crud.py` | [Courses 07](../courses/07-lesson-structure.md) | Edit manuale dei 4 campi Fase 2 |
| `course_lesson_structure_worker.py` | [Courses 07](../courses/07-lesson-structure.md) | Worker parallelo (cap=5 default) per modulo |
| `openai_lesson_structure_service.py` | [Courses 07](../courses/07-lesson-structure.md) | Wrapper OpenAI Fase 2 con JSON schema strict |

### Fase 3 — Contenuto + Glossario

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_content_service.py` | [Courses 08](../courses/08-lesson-content.md) | Orchestrazione + 10 validazioni §6.4 (riferimenti a obiettivi/temi riconciliati, `coverage_check` derivato, hard fail solo sulla copertura) + materializzazione + approve. Gate **per-unità** (niente allow-set su `course.status`): corso non terminale + `ensure_lesson_structure_ready` (modulo `approved` + `section_outline` presente, assessment esenti); generate-all/missing filtrano in silenzio; approve-all tollerante (ignora le `empty`). Branch su `is_assessment`: `build_assessment_user_prompt`, `materialize_lesson_assessment` per le lezioni di verifica |
| `course_lesson_content_crud.py` | [Courses 08](../courses/08-lesson-content.md) | Edit manuale `content_raw` + sync ref per asset rinominati + `update_lesson_assessment` (verifica delle competenze) |
| `course_lesson_content_worker.py` | [Courses 08](../courses/08-lesson-content.md) | Worker parallelo (cap=3 default) per lezione + auto-trigger glossario + pre-check struttura (`phase="precheck_structure"`, failed non recuperabile). Genera anche le lezioni-verifica `is_assessment` |
| `course_glossary_service.py` | [Courses 08](../courses/08-lesson-content.md) | Glossario corso (§10.1) — sync + ensure_glossary_ready. Gate: rank ≥ `architecture_approved` AND status ≠ `archived` (`published` ammesso) |
| `openai_lesson_content_service.py` | [Courses 08](../courses/08-lesson-content.md) | Wrapper OpenAI Fase 3 + addendum §9.3 in rigenerazione + `generate_lesson_assessment` (verifica) + `build_lesson_content_json_schema` (enum dei codici obiettivo per chiamata) |
| `lesson_coverage_resolver.py` | [Courses 08](../courses/08-lesson-content.md) | Modulo puro: risolve i riferimenti di contabilità di Fase 3 (`O1`/testo/varianti tipografiche → obiettivo canonico, `topic_id` case-insensitive). Cascata deterministica, nessun fuzzy |
| `openai_glossary_service.py` | [Courses 08](../courses/08-lesson-content.md) | Wrapper OpenAI glossario (10-30 termini) |
| `asset_validation_service.py` | [Courses 08 § Validazione asset](../courses/08-lesson-content.md#validazione-asset-latexmermaid--auto-fix-ai-a-generazione) | Validazione + auto-fix degli asset «fragili» a generazione (Fase 3 `validate_and_fix_content_assets`, Fase 4 `validate_and_fix_slides_assets`): formule LaTeX con `latex2mathml` E KaTeX (batch Playwright con Mermaid 11, pin `mermaid_cdn_version`); figure con `format` in `RENDERABLE_FORMATS` validate dal renderer del registro (`_validate_slots` con rimappatura `js_pos`: nel batch JS entrano solo `latex` e `mermaid`, gli altri kind in `to_thread` con `validate(deep=True)`, mai pass-through; `AssetCheck.fixable=False` per un formato non disponibile → `AssetFixUnresolvedError` senza fix AI); step deterministico (caratteri di controllo) → fix AI fino a `asset_fix_max_attempts`; rete di sicurezza i18n (`_LocField` per ogni kind con `extract_translatable`/`apply_translations` del renderer, rivalidazione offline dopo la traduzione). Scheda dettagliata più sotto |
| `openai_asset_fix_service.py` | [PROMPTS 12](../PROMPTS.md) | `fix_asset(kind, source, error, …)`: correzione della sola sintassi di un asset invalido; `AssetKind = Literal["latex","mermaid","vegalite","dot","function"]`, prompt per kind × IT/EN (`_SYSTEM_PROMPTS`), kind ignoto → `ValueError` (A20); `_ERROR_CAP = 1600`, contesto ≤ 600, `max_tokens` 4.000 (A16); output JSON `AssetFixOut{fixed_content, notes}`; usage di `openai_pricing.build_usage_dict` (con `cost_usd` e `duration_ms`, D16), consegnato anche con l'eccezione quando una risposta 200 è pagata ma inutilizzabile |
| `openai_figure_review_service.py` | [PROMPTS 17](../PROMPTS.md), [Courses 08 § Pipeline di validazione di Fase 3](../courses/08-lesson-content.md#pipeline-di-validazione-di-fase-3-fix--revisione--localizzazione) | Revisore figura ↔ testo (D15). Scheda dettagliata più sotto |
| `openai_asset_localize_service.py` | [Courses 08](../courses/08-lesson-content.md) | `localize_texts(items, language_code)`: ritraduce i campi testuali degli asset rimasti in un'altra lingua (rete di sicurezza per script non latini, kill-switch `asset_localize_enabled`), preservando LaTeX, sintassi Mermaid, chiavi/`field`/`datum.*` di Vega-Lite, id e `->`/`--` di DOT, placeholder `[FIG:..]`; `response_format=json_object`; usage di `openai_pricing.build_usage_dict` (D16) |
| `openai_image_to_mermaid_service.py` | [Courses 05 § lesson-assets](../courses/05-api-reference.md#lesson-assets-upload-immagini--imagemermaid--anteprima-function), [PROMPTS 11](../PROMPTS.md) | `convert_image_to_mermaid(image_bytes, …)`: Vision (`openai_image_to_mermaid_model`, default `gpt-4o`) → codice Mermaid 11 dei soli tipi `MERMAID_ALLOWED_TYPES` (label in testo semplice, niente `%%{init}%%`), `UNRECOGNIZED` → `OpenAIImageToMermaidError`; `_extract_mermaid_code` ripulisce i fence, `_is_valid_mermaid_keyword` controlla il tipo dichiarato |

> Verifica delle competenze (`is_assessment`): vedi
> [Courses 14 — Assessment lesson](../courses/14-assessment-lesson.md).
> La verifica riusa l'intero ciclo `content_*` della Fase 3 (stessi
> worker e service, branch su `lesson.is_assessment`).

### §7 — PDF lezione testo

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_pdf_service.py` | [Courses 09](../courses/09-pdf-export.md) | Render HTML + pre-render delle figure via `figure_render_service.render_figure_map` (`_prerender_visual_assets_for_lesson`, alias storico `_prerender_mermaid_for_lesson`) + MathJax + WeasyPrint + materialize; numerazione «Figura N.» (`figure_numbering`) calcolata sul corpo markdown prima della sostituzione degli asset, orfane accodate dopo la sintesi; ogni figura passa dal partial unico (`figure_markup.render_figure_html`); math: grammatica unica (`_install_math_grammar`: dollarmath con opzioni pinnate, rule inline `math_bsdelim` per `\(..\)`/`\[..\]`, core rule anti-currency `math_currency_guard`, quattro token resi da `_render_math_token`) condivisa da `_md_renderer` (commonmark) e `_md_inline_renderer` (preset `zero` + `markupsafe`, `render_markdown_inline` per didascalie, titoli, label, punti chiave e citazioni, D9) e dal collector per parse `_collect_math_from_content`/`_iter_math_sources` (parità con il renderer per uguaglianza: `_prepare_lesson_body` prepara una volta sola il corpo con i rimandi riscritti nella lingua del corso, il collector sostituisce le ancore con un HTML block segnaposto e passa la coda da `cite_asset_refs` come il renderer); nessun pre-processing testuale (L11); `_neutralize_blank_lines` negli asset iniettati; `MathSvgMap` con `requested`/`misses`, fallback MathML loggato (`math_render_fallback` per formula, `lesson_pdf_math_fallbacks` per lezione in dispensa, slide e video); pagina MathJax `build_mathjax_renderer_html` con pin `settings.mathjax_cdn_version` e guardia di rete; re-esporta i nomi storici di `mermaid_prerender` |
| `course_lesson_pdf_worker.py` | [Courses 09](../courses/09-pdf-export.md) | Worker parallelo (cap=2 default) + cancel-check post-render |

### Fase 4 — Slide + PDF slide

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_slides_service.py` | [Courses 10](../courses/10-lesson-slides.md) | Orchestrazione + 8 validazioni §7.4 + materializzazione + approve + reset PDF su rigenerazione. Gate **per-lezione**: corso non terminale + `content_status='approved'` (bulk eligible/missing su `approved`) |
| `course_lesson_slides_crud.py` | [Courses 10](../courses/10-lesson-slides.md) | Edit manuale `slides_raw` + validazione allentata |
| `course_lesson_slides_worker.py` | [Courses 10](../courses/10-lesson-slides.md) | Worker parallelo (cap=3 default) + auto-retry trasparente + atomic claim. Accetta ancora content `ready\|approved` (transitorio, stretta a `approved` a code svuotate) |
| `openai_lesson_slides_service.py` | [Courses 10](../courses/10-lesson-slides.md) | Wrapper OpenAI Fase 4 con JSON schema + REGENERATION_SUFFIX §9.4 |
| `course_lesson_slides_pdf_service.py` | [Courses 09](../courses/09-pdf-export.md) | Render PDF slide A4 portrait + slide split + figure in `<img data:svg>` (tutti i formati, `_prerender_mermaid_for_slides` → `render_figure_map` fondendo asset di Fase 3 e `new_assets`; `svg_to_data_uri` re-esportata da `svg_normalize`) con etichetta «Figura.» senza numero + slide_template; math delle slide raccolto su `_math_content_for_slides` (equazioni, tabelle, esempi e figure delle Dispense + `new_*` di Fase 4, didascalie comprese) e mappa SVG passata anche alla figura |
| `course_lesson_slides_pdf_worker.py` | [Courses 09](../courses/09-pdf-export.md) | Worker parallelo (cap=2, riusa env `course_lesson_pdf_*`) |

### Fase 5 — Discorso + PDF discorso

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_speech_service.py` | [Courses 11](../courses/11-lesson-speech.md) | Orchestrazione + 8 validazioni §8.5 (incl. TTS-safety) + materializzazione + approve. Gate **per-lezione** (speculare a Fase 4): corso non terminale + `slides_status='approved'` (bulk eligible/missing su `approved`) |
| `course_lesson_speech_crud.py` | [Courses 11](../courses/11-lesson-speech.md) | Edit manuale `speech_raw` + auto-ricalcolo durata + TTS-safety |
| `course_lesson_speech_worker.py` | [Courses 11](../courses/11-lesson-speech.md) | Worker parallelo (cap=3 default) + pre-check slides `ready\|approved` (transitorio, stretta a `approved` a code svuotate) |
| `openai_lesson_speech_service.py` | [Courses 11](../courses/11-lesson-speech.md) | Wrapper OpenAI Fase 5 + JSON schema + REGENERATION_SUFFIX §9.5 + `WORDS_PER_MINUTE` (130 IT / 150 EN) |
| `course_lesson_speech_pdf_service.py` | [Courses 09](../courses/09-pdf-export.md) | Render PDF discorso A4 portrait per-slide grouping + format_timeline cumulativa |
| `course_lesson_speech_pdf_worker.py` | [Courses 09](../courses/09-pdf-export.md) | Worker parallelo (cap=2, riusa env `course_lesson_pdf_*`) |

### Fase 6 — Video MP4 della lezione

I service di Fase 6 sono documentati per intero più sopra in questo file
(`runpod_tts_client`, `lesson_audio_cache`,
`lesson_slides_video_render_service`, `lesson_video_compose_service`,
`course_lesson_video_service`, `course_lesson_video_worker`) e in
[Courses 12 — Lesson video](../courses/12-lesson-video.md). È la prima
fase non-AI della pipeline: nessuna chiamata OpenAI, orchestra TTS su
RunPod GPU + rendering Playwright + encoding ffmpeg.

### Fase 6b — Video con Avatar (lip-sync MuseTalk)

Service documentati più sopra (`course_lesson_avatar_video_service`,
`course_lesson_avatar_video_worker`) + il pacchetto vendored
`app/musetalk_client/`, e in
[Courses 13 — Avatar video](../courses/13-avatar-video.md). Sovrappone
un avatar parlante (lip-sync MuseTalk su RunPod GPU) al video MP4 già
generato della lezione.

### Figure accademiche (doc 17) — registro dei renderer, tema, numerazione

Moduli del branch `feat/academic-figures` (progettazione e decisioni in
[Courses 17](../courses/17-visual-figures.md)). Tutti i moduli «leaf»
(`figure_theme`, `svg_normalize`, `figure_numbering`, `figure_compute/*`)
non importano `app.core.config` né SQLAlchemy: sono importabili dal
processo figlio `spawn` e dai test puri.

| Service | Documentato in | Scopo |
|---|---|---|
| `figure_theme.py` | [Courses 17 § 5](../courses/17-visual-figures.md) | Tema accademico unico (D3) e testi delle didascalie: `THEME_VERSION` (entra nella chiave di cache degli SVG), `FONT_STACK`/`FONT_ALLOWED`, palette di Okabe-Ito (`PALETTE`, `PALETTE_LABEL`), `MERMAID_ALLOWED_TYPES` (15 tipi D8 + alias `graph`/`stateDiagram`) / `MERMAID_EXCLUDED_TYPES` / `MERMAID_D8_SAMPLES`, `mermaid_config` e `mermaid_initialize_js(*, use_max_width, security_level)` (`htmlLabels: false` top-level, `theme: neutral` con ogni variabile derivata fissata), `VEGALITE_THEME_CONFIG`, `DOT_DEFAULTS` + `dot_defaults_prelude`, `MATPLOTLIB_RC`, `FIGURE_I18N` it/en con chiavi `courses.figures.*` speculari ai locale del frontend, `figure_labels(language)` (fallback it, A4), `ASSET_LABEL_ROOTS` + `asset_label_key(kind, form)` / `asset_label(labels, kind, number, *, kind_word)` / `asset_ref(...)` (D5: etichetta del blocco «Tabella 3.» e forma non numerata «Tabella.» per slide e frame video; rimando in linea senza punto «Figura 2»; `THM` usa la parola del kind e senza numero resta la sola parola; `ValueError` su `asset_ref` senza numero, che sarebbe un errore del risolutore, non un caso di render), `latex_to_unicode`, `format_number`, `function_caption(computed, language)`. Copia frontend `lib/figureTheme.ts` da mantenere allineata (test di parità) |
| `mermaid_prerender.py` | [Courses 09](../courses/09-pdf-export.md) | Pre-render Mermaid → SVG estratto da `course_lesson_pdf_service` (che re-esporta i nomi storici): `build_mermaid_renderer_html(version=…)` con pin `settings.mermaid_cdn_version`, `_prerender_mermaid_batch_{async,sync}` (una sessione Playwright per lezione; per figura UNA `page.evaluate` di `window.__renderMermaidMeasured(id, code)` → `MermaidPrerender(svg, metrics)` con il corpo dei testi misurato da `MEASURE_SVG_FONT_PX_JS`, D10; misura fallita → `metrics=None` e `mermaid_font_measure_failed`, SVG accettato), `_prerender_mermaid_to_svg_batch_{async,sync}` (nome storico, proiezione `.svg` usata dai test del sanitizer e dei `<foreignObject>`), `window.__renderMermaid` resta la funzione a stringa, `_strip_mermaid_max_width`, `_sanitize_mermaid_code` (righe `mermaid`/`all`/fence). Geometria (D14): la stessa `page.evaluate` chiama anche `window.__measureSvg` (`MEASURE_SVG_GEOMETRY_JS`, gemello JS di `figure_geometry` con tetti propri e passo locale, opzioni da `geometry_measure_options()`) e l'esito diventa un `GeometryReport` con `_geometry_from_page`; l'SVG non è mai riscritto. Guardia di rete condivisa dalle quattro pagine headless: `allows_prerender_url(url, *, allowed_prefixes)` (`PRERENDER_ALLOWED_PREFIX` = `https://cdn.jsdelivr.net/`, più gli schemi inerti `about:`/`data:`/`blob:`), `media_url_prefixes()` (solo l'host pubblico dei media con lo storage OVH, mai `public_base_url`) e `block_external_requests(page, *, allowed_prefixes=())` — `page.route("**/*")` con `route.abort()` e `prerender_request_blocked`, più `page.route_web_socket` che chiude ogni WebSocket (`prerender_websocket_blocked`), e la pagina riportata su `about:blank` prima del `set_content` del chiamante |
| `svg_normalize.py` | [Courses 17 § 7](../courses/17-visual-figures.md) | `normalize_svg(svg, *, max_bytes) -> NormalizedSvg(svg, width_px, height_px)` per gli SVG di vl-convert, `dot` e matplotlib (Mermaid non passa da qui): prologo rimosso, scansione che RIFIUTA (`SvgRejectedError`) `<script>`, `<foreignObject>`, `<iframe>`, `<image>`, SMIL, e nei tag `href` esterni, `on*=`, `javascript:`, `url()` non-frammento (anche in `<style>`); radice riscritta con `width`/`height` in px, `viewBox`, `preserveAspectRatio`, `xmlns`. `svg_to_data_uri` (base64) unica per dispensa e slide. Due letture pure per la banda di leggibilità (D10), valide anche per Mermaid perché non toccano l'SVG: `svg_intrinsic_box(svg) -> SvgBox \| None` (viewBox, dimensione intrinseca in px, `px_per_unit`: 4/3 per DOT e matplotlib) e `svg_base_font_px(svg) -> SvgMetrics` (corpo minimo dei `<text>`/`<tspan>` con testo proprio, cascata CSS minima: attributi, `style` in linea, regole del `<style>` per id/classe/discendenza, eredità, `em`/`%` sul padre; `source` `parsed`/`root_rule`/`unresolved`/`no_text`; fuori grammatica o dipendente dal contesto — `rem`, `var()`, `calc()`, parole chiave, `em`/`%` senza antenati — → `unresolved`) |
| `figure_compute/isolated.py` | [Courses 17 § 3.5](../courses/17-visual-figures.md) | `run_isolated(fn_path, payload, *, timeout)`: processo figlio `spawn` + `Pipe`, `poll(remaining)` prima di `recv` (nessuno stallo con risultati grandi), deadline monotona, `kill()` allo scadere → `FigureTimeoutError`; figlio morto o errore di avvio → `FigureComputeError` (A13, A18) |
| `figure_compute/vegalite_rules.py` | [Courses 17 § 3.2](../courses/17-visual-figures.md) | `check_vegalite_rules(spec) -> list[str]`: regole D5 (`data.url`/`data.name`/`lookup.from.data`, `mark image`, `selection\|params\|tooltip\|interactive\|config\|usermeta`, `encoding.href`, `values` ≤ 200, `sequence` ≤ 5.000 con `step > 0`, `clip: true` su line/area/point/trail, `scale.domain` sugli assi quantitativi, `axis.format`, una sola `title`) con ricorsione ≤ 4 su `layer\|hconcat\|vconcat\|concat\|spec`; euristica del criterio 10 (H1 funzioni, H2 `datum` a denominatore, H3 potenza) con ereditarietà dei campi `sequence` e degli alias `calculate` → `vegalite_use_function_format`; `nesting_depth`/`MAX_NESTING = 32` |
| `figure_compute/vegalite_render.py` | [Courses 17 § 3.2](../courses/17-visual-figures.md) | Bersagli del figlio: `render_svg(payload)` (solleva con il messaggio di vl-convert) e `render_svg_batch(payload)` (`None` per la spec che fallisce); `$schema` e `config` arrivano dal registro; `allowed_base_urls=[]` |
| `figure_compute/function_parse.py` | [Courses 17 § 4.2](../courses/17-visual-figures.md) | Passo 1 delle espressioni `function`, senza sympy: `check_expression(src, *, free_symbols) -> ParsedExpr` (AST con nodi ammessi, `FUNCTIONS` whitelist, `pi`/`E`, `Pow` con esponente ≤ 12, ≤ 80 nodi, profondità ≤ 12; `ExprError(loc_suffix, msg, type)` con messaggi per il docente: «scrivi 2*x», «usa ** per la potenza», «simbolo non dichiarato: y») |
| `figure_compute/function_numeric.py` | [Courses 17 § 4.3](../courses/17-visual-figures.md) | Calcolo numerico in thread (numpy): `compile_numpy` (valutatore dell'AST, niente `eval`/`lambdify`), `sample`, `split_branches` + `classify_cuts` (poli, salti, `edge_pole`), `find_zeros` (bisezione, plateau), `find_critical`/`find_inflection`, `oblique_or_horizontal` (regressione sulle code), `tail_confirmed`, verifiche locali (`is_zero_at`, `is_stationary_at`); `MAX_NOTABLE_POINTS = 12` per categoria |
| `figure_compute/function_symbolic.py` | [Courses 17 § 4.3](../courses/17-visual-figures.md) | `analyze_symbolic(payload)` SOLO nel figlio: `parse_sympy` con `global_dict` ristretto (passo 2), `solveset`/`solve`, `singularities`, `limit` sulle code, `integrate`, `diff`; `exact_form` (`nsimplify` + `latex` bounded: `MAX_EXACT_LATEX = 80`, `MAX_FORMULA_LATEX = 160`) |
| `figure_compute/function_plot.py` | [Courses 17 § 4.4, § 23](../courses/17-visual-figures.md) | Disegno matplotlib (API a oggetti, `rc_context(MATPLOTLIB_RC)` + `svg.hashsalt` per asset, serializzato da `_DRAW_LOCK`): assi con frecce, rami, asintoti, punti notevoli con coordinate esatte, formula come `TextPath` (A14: `to_mathtext`, `expr_to_mathtext`, `fit_size`, `MIN_MATH_SIZE_PT`), `contour` per le curve di livello, `fill_between` per le aree; mai `<image>`. Impaginazione (§ 23): `draw()` → `DrawResult(svg, warnings, boxes)` con il riquadro (`TextBox`, punti della figura) di OGNI testo — è l'oracolo dei test — per una di due vie: `_Canvas.place` lo colloca e lo registra (`kind` `label`, `formula`, `legend`, `axis`), `_Canvas.register` registra quello che colloca matplotlib, misurato sull'artista con `_Canvas.artist_box` (`get_window_extent`): `_register_tick_labels` per le etichette dei tick (`kind` `tick`) e `_label_levels` per quelle dei contorni (`kind` `contour`, tolte quando non hanno posto). Formule e legenda in una banda sopra gli assi (`_plan_band`, che usa prima il vuoto di `TOP_PAD_PT` e poi alza la figura; tetto aritmetico `MAX_BAND_CONTENT_PT` / `MAX_FIG_H_PT`: mai più alta che larga), etichette dei punti collocate fra più candidati senza collisioni, compresi quelli riportati dentro il riquadro degli assi (`_label_candidates`, `_place_label`, avvertenza `labels_crowded`); `render_svg()` resta `(svg, warnings)` |
| `figure_function_service.py` | [Courses 17 § 4](../courses/17-visual-figures.md) | Motore del formato `function`: `render_function_sync(spec, language=…)` = numerico → simbolico nel figlio (`SYMBOLIC_TARGET`, timeout `figure_function_timeout_seconds`) → riconciliazione (`_Reconciler`: esatti accettati solo con riscontro numerico) → disegno → `normalize_svg` → `function_caption`; `FunctionRenderResult(svg, computed, latex, warnings, computed_caption, content_hash)`; cache LRU dei risultati (`cached_result`, `clear_result_cache`); `extract_translatable`/`apply_translations` sulle label; `FunctionRenderError` |
| `figure_render_service.py` | [Courses 17 § 2-3](../courses/17-visual-figures.md) | Registro D2: `FigureRenderer` (Protocol), `MermaidRenderer` (gate statico `mermaid_static_gate`: tipo D8, `%%{init`, frontmatter, HTML nelle label; batch via `mermaid_prerender`), `VegaLiteRenderer` (≤ 4.000 char, chiavi duplicate, annidamento ≤ 32, `Draft7Validator` sullo schema v6 di altair senza `import altair`, regole D5, render in `run_isolated` con tema), `DotRenderer` (tokenizzatore degli attributi vietati `image\|shapefile\|imagepath\|fontpath\|stylesheet\|URL\|href\|target` e composti, ≤ 600 archi, `dot -Tsvg` senza shell con timeout), `FunctionRenderer`; il gate editoriale di `figure_compute.graph_rules` gira in `MermaidRenderer.validate` dopo il gate statico e in `DotRenderer.validate`; `DotRenderer.measure(svg, *, work_left) -> GeometryReport` è il metodo FACOLTATIVO del protocollo, letto con `getattr(renderer, "measure", None)` (`:386`) e non richiesto agli altri renderer; `REGISTRY`, `RENDERABLE_FORMATS`, `available_formats()` (kill-switch ∧ dipendenza, `lru_cache`), `RenderedFigure(svg, metrics)` (D10: metriche del testo accanto all'SVG; `from_svg` le legge con `svg_base_font_px`), `render_figure_map(assets, *, language)` (unico punto di `to_thread` + `wait_for` + semaforo, una chiamata batch per formato: `render_figure_batch` se il renderer lo espone, cioè `MermaidRenderer` → `mermaid_prerender._prerender_mermaid_batch_sync`, altrimenti `render_svg_batch` avvolto in `RenderedFigure.from_svg`, nessun metodo nuovo nel Protocol; cache LRU + negativa 60 s, mai solleva) e la proiezione `render_svg_map(assets, *, language)` (`{asset_id: svg}`, nessuna logica propria), `validate_visual_assets_or_raise` (gate del PATCH, A15, `ValidationAppError` con `meta.errors`), `render_chain_variants(assets, figures, *, box_mm, variant, language, lesson_code)` (D15: per le sole figure Mermaid che in quel box escono sotto la banda e il cui sorgente SANIFICATO — `MermaidRenderer.sanitize`, lo stesso che il renderer disegna — è una catena orizzontale rende ANCHE la variante verticale, in un batch unico, e la appende in `RenderedFigure.chain_variant`; chiave di cache propria `(mermaid, sha256 del sorgente variante, THEME_VERSION)`, condivisa fra dispensa e slide; non sceglie e non solleva), `render_function` (endpoint) e `function_computed_caption` |
| `figure_numbering.py` | [Courses 17 § 6.1](../courses/17-visual-figures.md) | Modulo puro, dal branch dei rimandi esteso ai quattro kind (D3): `ASSET_REF_RE` (`FIG\|TAB\|EQ\|EX`) e la proiezione `FIG_REF_RE`, `cited_asset_ids` / `cited_figure_ids`, `append_uncited_asset_refs(markdown, ids_by_kind)` (orfani in coda nell'ordine `FIG → TAB → EQ → EX`, A12; id che non fanno round-trip nel token scartati, COR-4) e la proiezione `append_uncited_figure_refs`, `compute_asset_numbers(markdown, ids_by_kind) -> {(KIND, id_lower): N}` (contatore indipendente per kind, prima citazione; da applicare DOPO l'accodamento e PRIMA di `asset_ref_normalize`) con la proiezione `compute_figure_numbers` (stessi risultati di sempre, fixture storica intatta), `proof_steps` e `equation_label_family(eq) -> "EQ" \| "THM"` (`statement` non vuoto o un passo di `proof` non vuoto: il campo `kind` da solo non decide), `strip_figure_prefix` (cifra e separatore obbligatori, solo a render). Copia frontend `lib/figureNumbering.ts`, fixture condivisa `tests/fixtures/figure_numbering_cases.json` |
| `asset_ref_normalize.py` | [Courses 17 § 6.1](../courses/17-visual-figures.md), [Courses 09](../courses/09-pdf-export.md) | Modulo puro (D1, D2, D4), specchiato da `frontend/src/lib/assetRefNormalize.ts` con la fixture condivisa `tests/fixtures/asset_ref_normalize_cases.json`. Un tag `[KIND:id]` è **gestito** quando il kind è in `numbers` e l'id normalizzato (`.strip().lower()`) vi ha un numero; un tag non gestito resta byte-identico in ogni passo. `normalize_asset_refs(markdown, *, numbers, reference)`: citazione in linea → rimando `reference(kind, id_lower, n)`, ridotto al **solo numero** quando la parola dell'etichetta precede già il tag sulla stessa riga a meno di spazi e su parola intera (guardia «parola-etichetta»: «La figura [FIG:x]» → «La figura 1»; la parola è quella che `reference` stessa mette prima del numero, quindi la chiave i18n del rimando, e il plurale non corrisponde); riga fatta del solo tag (`ANCHOR_LINE_RE`) = **ancora**, tenuta la prima per chiave e rimosse le successive con la riga vuota adiacente; chiave con citazioni ma senza ancora → UNA ancora su riga propria dopo il **blocco** della prima citazione (righe contigue non vuote; fence e `$$…$$` chiusi come unità opache, una lista presa intera per non spezzare la numerazione); dentro fence, code span e math i tag sono citazioni, mai ancore. `cite_asset_refs(text, …)`: sola sostituzione, per la coda che non rende blocchi (punti chiave e riferimenti, C9). I numeri sono dati calcolati prima sul corpo non normalizzato, il testo fuori dai tag è byte-identico e la funzione è idempotente; regex con classi esplicite (mai `\s`/`\d`) e `.lower()` senza `IGNORECASE`, per avere lo stesso esito in Python e in JS |
| `figure_geometry.py` | [Courses 17 § 12](../courses/17-visual-figures.md) | Modulo puro (D14, WP5): misura l'SVG già reso e non lo modifica mai; gemello Python di `mermaid_prerender.MEASURE_SVG_GEOMETRY_JS` (`window.__measureSvg`), con parità provata in Chromium sui diciotto modelli DOT. `measure_svg(svg, *, dot_defects, max_segments, max_work, work_left, step) -> GeometryReport(crossings, crossing_pairs, edges, segments, defects, skipped, work, spent)` e `measure_dot_svg` (incroci + difetti). Incroci arco × arco fra tracciati di archi **diversi** (`EDGE_CLASS_PREFIXES`/`EDGE_CLASS_NAMES`/`EDGE_GROUP_CLASSES`; involucri `<a>` di Graphviz attraversati con `is_anchor_wrapper`), campionamento a `SAMPLE_STEP` 2 unità della radice, estremi esclusi a `ENDPOINT_TOLERANCE`, punti fusi a `CLUSTER_RADIUS`, archi ellittici convertiti in cubiche; punti ciechi dichiarati (griglia di quadrant e gantt, link del sankey, curve del radar, xychart: `crossings = 0`). Quattro difetti di lettura DOT (`DEFECT_TEXT_OUTSIDE_CANVAS`, `DEFECT_TEXT_OUTSIDE_OWNER`, `DEFECT_LABELS_OVERLAP`, `DEFECT_EDGE_CROSSES_LABEL`) con il riquadro del testo stimato per famiglia di font (`font_kind`, `estimate_text_width`). Costo limitato per costruzione: il lavoro è contato PRIMA di eseguirlo (celle delle impronte, coppie candidate, controlli del raggruppamento) e oltre i tetti (`MAX_MEASURE_SEGMENTS`, `MAX_MEASURE_WORK`, i gemelli `MAX_BATCH_*` e `MAX_BROWSER_*`) la misura è saltata con `skipped` in {`figure_segment_cap`, `batch_segment_cap`, `figure_work_cap`, `batch_work_cap`, `geometry_out_of_range`} e la figura intatta; griglia dal viewBox con passo adattivo e al più `MAX_GRID_CELLS` 250.000 celle, coordinate non finite senza eccezioni. Gli incroci oltre `graph_rules.MAX_EDGE_CROSSINGS` sono **diagnostici**, mai un rifiuto |
| `figure_compute/chain_layout.py` | [Courses 17 § 21](../courses/17-visual-figures.md), [Courses 09](../courses/09-pdf-export.md) | Direzione delle catene lineari (D15), puro e leaf come `graph_rules`, specchiato da `lib/chainLayout.ts` con la fixture `tests/fixtures/chain_layout_cases.json`: `vertical_chain_variant(source) -> str | None` ritorna il sorgente Mermaid con il SOLO token di direzione cambiato (`LR` → `TB`, `RL` → `BT`) quando è una catena lineare dichiarata in orizzontale, byte per byte identico altrove. Riconoscimento conservativo (nel dubbio `None`, e la figura resta com'è): intestazione `flowchart|graph LR|RL` come prima riga utile, con il `;` finale facoltativo come nel corpo (frontmatter YAML e commenti `%%` prima ammessi, direttiva `%%{…}%%` no), nessun `subgraph`/`end`/`direction`, archi solo semplici (`-->`, `---`), grado entrante e uscente al più 1, nessun cappio, un solo componente connesso, almeno `MIN_CHAIN_NODES` = 3 nodi; `is_linear_chain(nodes, edges)` è l'invariante, esposto per il test; `_SPACE_CLASS` è l'insieme degli spazi comune a Python e JavaScript (`str.strip()` toglie anche U+001C-U+001F e U+0085, `String.trim()` anche U+FEFF: senza la classe esplicita i due lati sceglievano direzioni diverse). Non rende e non sceglie: la scelta la fa la MISURA (`render_chain_variants` + `_figure_width_style`) |
| `figure_compute/graph_rules.py` | [Courses 17 § 12](../courses/17-visual-figures.md) | Gate editoriale dei grafi (D13, D14), puro, gemello formale di `vegalite_rules`: `check_graph_rules(kind, source, *, metrics) -> list[str]` (vuoto = conforme) sui formati con archi (`GRAPH_FORMATS` = mermaid, dot), con `mermaid_source_metrics` / `dot_source_metrics` / `graph_source_metrics` → `GraphSourceMetrics` (conteggi per tipo sul sorgente: archi DOT espansi sui sottografi come Graphviz e `strict` rispettato; parola più lunga dove Mermaid 11 va a capo da solo), `format_graph_violations` e `crossings_violation`. Messaggi `graph_too_dense: <cosa> <n> > <max> — <che cosa ridurre>`, distinti dai tetti di RISORSA (`figure_render_service.DOT_MAX_EDGES`, `settings.figure_dot_max_chars`, `VEGALITE_MAX_CHARS`, `VISUAL_ASSET_CONTENT_MAX_CHARS`) e con la clausola di semplificazione per il fix AI; agganciati in `MermaidRenderer.validate` dopo il gate statico e in `DotRenderer.validate`, con tipo dedicato nel 422 del PATCH. Costanti calibrate il 16 settembre 2026 sui 57 modelli degli editor con margine ≥ 1,4× sul massimo osservato: `MAX_GRAPH_NODES` 30, `MAX_GRAPH_EDGES` 45, `MAX_LABEL_CHARS` 64, `MAX_TITLE_CHARS` 110, `MAX_MERMAID_SOURCE_CHARS` 3.000, `MAX_GRAPH_LINES` 120, `MAX_EDGE_CROSSINGS` 4, **confermate il 18 settembre 2026** su 20 grafi reali (`scripts/measure_asset_refs.py --figures` sull'export di quattro lezioni: nessun p90 oltre il 60 % della soglia, quindi nessuna ricalibrazione); `MAX_MERMAID_MEASURED_CHARS` 12.000 ferma la lettura al tetto A1. `MAX_EDGE_CROSSINGS` è DIAGNOSTICA per entrambi i formati: un grafo a strati completi ne ha per costruzione (percettrone 3-4-2: 16) e il fix AI non può togliere archi, quindi un rifiuto rigenererebbe la lezione fino all'esaurimento dei tentativi |
| `figure_markup.py` | [Courses 17 § 6.3](../courses/17-visual-figures.md) | Quarto `Environment` Jinja (`templates/partials/`, `autoescape=True`) e `render_figure_html(*, body_html, caption, alt_text, asset_id, fmt, number, labels, variant, fallback_source, extra_caption, cite, caption_renderer, box)`: partial unico `figure.html.j2` per dispensa (`lesson`) e slide/video (`slide`, «Figura.»), guardia anti-doppia coda, nessuna riga vuota nell'output; `FigureBox(w_mm, h_mm)` (D12, al decimo, > 0) emesso come `style="--figure-w: …; --figure-h: …"` sul `<figure>` dopo `aria-label`, facoltativo (la dispensa non lo passa); `caption_text` (una riga + `strip_figure_prefix`) è l'unico pre-trattamento della didascalia, condiviso con il collector del math del PDF, `cite` (rimando testuale degli asset, D4) è applicato subito dopo, quindi la didascalia visibile e l'accessible name dicono la stessa cosa (`alt_text` resta testo d'autore, mai citato), e `caption_renderer` (math inline, D9) riceve il testo già pre-trattato e ritorna `Markup` (senza, output byte-identico) |
| `figure_scale.py` | [Courses 17 § 12](../courses/17-visual-figures.md), [Courses 09](../courses/09-pdf-export.md) | Modulo leaf (D10, D11), specchiato da `lib/figureFormats.ts` (`fitFigureWidthMm`) con la fixture `tests/fixtures/figure_scale_cases.json`: `READABILITY_BANDS_PT` (dispensa e web 8-11 pt, slide e video 10-14 pt), `fit_figure_width_mm(vb_w, vb_h, base_font_px, box_w_mm, box_h_mm, variant, intrinsic_w_px) -> FigureFit(width_mm, scale, text_pt, in_band)` (fluidi: riempiono il box e scendono al tetto della banda; `<img>` intrinseci: da scala 1 salgono solo al fondo della banda; mai oltre il box, larghezza per difetto al centesimo; banda irraggiungibile → larghezza del box e `in_band=False`; senza testo scala naturale), `SvgMetrics`, `resolve_base_font_px` (metriche risolte oppure `FALLBACK_BASE_FONT_PX` per formato con `source="constant"`: Mermaid 14, Vega-Lite 11, DOT 40/3, `function` 12 px), `FigureFitEntry` (voce del `fit_report`, input del gate D13; `direction_flipped` dice se la catena è stata ribaltata, D15), `FigureBoxMm`, `LESSON_REFERENCE_BOX_MM` (168 × 242 mm: il box di RIFERIMENTO della dispensa, usato SOLO per decidere la direzione di una catena quando il box vero non è noto — è il mirror che il frontend usa nella vista — mai come larghezza di resa), `format_mm`. Box del fit: dispensa `figure_box_w_mm` × `figure_box_h_mm` da `course_lesson_pdf_service._compute_template_margins_cm` (170 × 242 mm sul template di default, 168 per Mermaid col padding del wrapper), slide il `FigureBox` della pagina (`slide_geometry.image_box`). La larghezza va come ultimo attributo `style="width:Wmm"` sul corpo della figura (`<div class="mermaid-svg">` o `<img>`), mai dentro l'SVG |
| `slide_geometry.py` | [Courses 09 § Rendering delle figure nelle slide](../courses/09-pdf-export.md) | Modulo puro (D12): `SlideGeometry` mirror delle costanti CSS di `lesson_slides_pdf.html.j2` (riga citata per campo, pinnata da un test a regex), `estimate_lines` (limite superiore per classi di carattere per titoli, prosa, bullet e didascalie, calibrato `real ≤ stima ≤ real + 1` sui `LineBox` di WeasyPrint; il `<pre>` di fallback non passa di qui: dal settimo giro è in `white-space: pre`, una riga sorgente è una riga resa e le righe lunghe sono tagliate a destra, e lo stimatore mono con le tabelle di larghezza per lingua e per script è stato rimosso), `page_figure_budget(title, body, bullets, n_blocks)` (budget per blocco della pagina resa, pavimento 25 mm con `clamped`; da WP4 titolo, prosa e bullet sono testo o pezzi con `ProseMath`: `prose_extent` misura le formule della prosa, in linea come parola larga quanto l'SVG più la crescita di riga, a blocco con la loro altezza, e `svg_inline_box` legge le dimensioni della radice MathJax), `image_box(budget, caption_text)` (box dell'immagine meno la didascalia reale, al decimo per difetto), `fallback_source_rows(source, language)` (righe rese ESATTE, una per riga sorgente con a capo su `\n`, CRLF, CR, U+2028 e U+2029 e NUL scartati, e altezza stimata: 1,3 em per riga dell'insieme base con lingua neutra (`_mono_lang`: profili neutra/altra, vi neutra senza greco, cirillico, ∏, ∑ e ∫), `fallback_tall_line_budget` 1,70 em per le altre, 1,83 em con mn-cn e con il mongolo tradizionale, 1,76 con tcy, `fallback_ideo_line_budget` 2,46 em quando il primo carattere con script reale è un ideogramma, un kana o un Hangul (`_ideographic_lead`, ottavo giro: Pango allinea le altre run sulla baseline ideografica della prima e in WeasyPrint la riga arriva a 2,293 em; limite analitico 2,4555 su tutte le coppie di font del container)), `truncate_fallback_source(source, box, language)` (righe del `<pre>` che entrano in altezza, marcatore «…», a capo riscritti come `\n` così WeasyPrint e Chromium rendono le stesse righe); consumato da `course_lesson_slides_pdf_service.render_slides_html` (due passi: split, poi budget per pagina) e da `_render_visual_asset_block(figure_budget=)` |
| `scripts/revalidate_mermaid_assets.py` | [Courses 09 § Settings comuni](../courses/09-pdf-export.md) | Dry-run L5 (A11): gate statico D8 + render Mermaid 11 + conteggio `<foreignObject>` sugli asset Mermaid in DB, lezioni con asset non citati (A12); `--skip-render`, `--course`, `--lesson`, `--sample`, `--format csv`, `--show-ok` |
| `scripts/check_prompts_md.py` | [Backend 11 — Tests](11-tests.md) | Verifica meccanica di `docs/PROMPTS.md`: i blocchi ```text dei PROMPT 3, 4, 5, 6, 11, 12 e 17 (con le varianti verbatim, `_SYSTEM_*_IT` e `_SYSTEM_*_EN`) confrontati con i `_system_prompt(...)` reali resi con i segnaposto documentati; exit 1 con diff se divergono |

#### `app/services/asset_validation_service.py` — scheda

**Scopo**: nessun asset «fragile» raggiunge `ready` rotto. Entrata:
`validate_and_fix_content_assets(output: LessonContentOutput, *, language_code)
-> (output, assets_usage)` (Fase 3, progress `validating_assets` a 88%; ordine
fix → revisione → localizzazione) e
`validate_and_fix_slides_assets(output: LessonSlidesOutput, …) -> output`
(Fase 4, fix → localizzazione, usage solo loggato in `slides_assets_usage`).

- **Slot** (`_Slot(id, kind, current, …)`): `_collect_content_slots` /
  `_collect_slides_slots` raccolgono `equations[].latex`, `proof[].latex`, il
  math inline `$..$`/`$$..$$` dei campi testo (`_InlineField`) e ogni
  `visual_assets[]` / `new_assets[]` con `format in RENDERABLE_FORMATS`
  (`kind = asset.format`). `_sanitize` toglie fence (anche ```` ```vega-lite ````)
  e caratteri di controllo senza alterare i byte utili.
- **`_validate_slots`**: batch Playwright (`_validate_js_batch`, pagina
  `_validator_html()` con KaTeX 0.16.9 e Mermaid `settings.mermaid_cdn_version`,
  `mermaid_initialize_js(use_max_width=False)`) per i soli kind `latex` e
  `mermaid`, risultati riallineati con `js_pos`; `latex` anche con
  `validate_latex_mathml` (`latex2mathml`, gate offline); `mermaid` prima
  con il gate statico del registro, poi il parse JS (pass-through se la
  CDN è irraggiungibile); `vegalite`/`dot`/`function` con
  `REGISTRY[kind].validate(deep=True)` in `to_thread` — mai pass-through;
  formato assente da `available_formats()` → `AssetCheck(fixable=False)`.
- **`_validate_and_fix`**: giro 0 sugli originali (validi → byte-identici);
  step deterministico (`_clean_latex_source`) sugli invalidi; loop di fix
  AI (`openai_asset_fix_service.fix_asset`, kind dello slot) fino a
  `asset_fix_max_attempts`; `_raise_if_unfixable` alza subito
  `AssetFixUnresolvedError` (recuperabile: auto-retry dell'intera lezione)
  per i check non riparabili; contatori di log per kind.
- **Localizzazione (D7)**: `_collect_*_loc_fields` → `_LocField` per
  caption/alt_text/enunciati/celle e per i campi testuali estratti dai
  renderer (`extract_translatable`: `title`/`axis.title`/`text` di
  Vega-Lite, `label|xlabel|headlabel|taillabel` di DOT, `label` delle
  espressioni e annotazioni di `function`, label Mermaid);
  `_needs_localization` (script non latino, `asset_localize_enabled`) →
  `openai_asset_localize_service.localize_texts` → `apply_translations` e
  rivalidazione offline dei kind strutturali.
- **Revisione figura ↔ testo (D15)**: `_review_figures` (fra fix e
  localizzazione; kill-switch `figure_review_enabled`, al più
  `figure_review_max_attempts` chiamate per figura, saltata senza
  `openai_api_key`). `_review_context` (primo blocco che cita la figura
  con `FIG_REF_RE`: introduzione, sezioni, sintesi, poi esempi e tabelle,
  lo stesso corpus dei warning di Fase 3; altrimenti il corpo), `_figure_measure` (nodi/archi di
  `graph_rules`, incroci/difetti della resa, corpo del testo sul box
  `_REVIEW_FIT_BOX_MM` 170 × 242 mm), `render_figure_map` sugli originali;
  per giro `_review_round` (chiamate concorrenti sotto `_review_semaphore`,
  `figure_review_max_parallel` per loop) → `_review_guard`
  (`missing_source`, `unchanged`, `placeholder`, `type_changed`,
  `density_increased`, `nodes_removed` e `nodes_isolated` da
  `GraphSourceMetrics.node_ids`/`linked_ids`; per i formati senza archi
  `_DATA_GUARDS`: `_vegalite_guard` su `vegalite_data_metrics`
  (`rows_removed`, `sequence_removed`, `fields_removed`) e
  `_function_guard` su `parse_function_spec` (`expressions_changed`,
  `domain_reduced`), così nemmeno Vega-Lite e `function` possono perdere i
  dati) → `_judge_candidates`
  (`_validate_slots` sugli slot `asset:<id>#review`, poi una
  `render_figure_map` con originale e riscrittura dei grafi) → `review_acceptance(fmt, original, candidate)`
  (pubblica: riscrittura resa e misurata, incroci non superiori, nessun
  codice di difetto nuovo; Vega-Lite e `function` decisi dalla guardia dei
  dati e dalla validazione). Le rese della revisione sono speculative
  (`render_figure_map(..., cache_failures=False)`): niente cache negativa
  sulle chiavi degli originali.
  Commit delle riscritture accettate a fine revisione; log
  `figure_review_verdict`, `figure_review_rejected`,
  `figure_review_measured`, `figure_review_applied`; nessuna eccezione
  esce: `_ask_review` cattura ogni errore della singola chiamata
  (`figure_review_call_failed`, le chiamate sorelle del `gather`
  finiscono e restano contate), il resto diventa `figure_review_failed`.
- **Usage (D16)**: `_validate_and_fix` e `_localize_fields` accettano
  `usage_sink`; ogni chiamata AI aggiunge
  `{phase, asset_id, **build_usage_dict}` (`phase` fra `fix`, `review`,
  `localize`; la localizzazione ha `asset_id=None` e `fields`), anche
  quando è pagata senza risultato usabile (l'usage arriva con
  `OpenAIError.usage`). `assets_cost_usd(assets)` somma i `cost_usd` noti e
  `merge_assets_usage(usage, assets)` (pubbliche, usate dal worker di Fase
  3) aggiunge `assets` e `assets_cost_usd` senza toccare `cost_usd`;
  `AssetFixUnresolvedError.assets_usage` porta le chiamate già pagate di un
  tentativo che finisce in rigenerazione.
- `validate_assets_for_test` resta l'hook dei test.

#### `app/services/openai_figure_review_service.py` — scheda

**Scopo**: verdetto AI sulla coerenza fra una figura già valida e il testo
che la cita (D15, PROMPT 17). Speculare a `openai_asset_fix_service`:
httpx con `get_client`, `response_format` json_schema strict
`FIGURE_REVIEW_JSON_SCHEMA` (`verdict` ∈ {`coerente`, `correggi`},
`reason`, `source` stringa o `null`), nessuna persistenza.

- `review_figure(*, fmt, source, caption, alt_text, context, measure,
  language_code, feedback="") -> (FigureReviewOut, usage)`: modello
  `openai_figure_review_model` (default `gpt-4o-mini`),
  `max_completion_tokens` `openai_figure_review_max_tokens` (4.000),
  reasoning `openai_figure_review_reasoning_effort` con
  `apply_reasoning_effort`, timeout 90 s; usage di `build_usage_dict`.
  Errori: `OpenAIFigureReviewError` (HTTP, corpo non JSON, formato,
  JSON del contenuto, schema), `OpenAINotConfiguredError`; un `usage`
  che non è un oggetto vale vuoto. L'usage è costruito prima di leggere il
  verdetto: una risposta 200 inutilizzabile (JSON troncato dal tetto dei
  token, schema fuori contratto) lo consegna al chiamante con l'eccezione
  (`OpenAIError.usage`), che lo contabilizza lo stesso.
- `FigureReviewOut` (`extra="ignore"`, ogni campo mancante vale
  `coerente`), `FigureMeasure` (`nodes`, `edges`, `rendered`,
  `crossings`, `defects`, `text_pt`, `in_band`; `measured` = resa e
  incroci contati), `ReviewContext` (`title`, `text`, `cited`).
- `build_user_message(...)`: etichette in italiano; tetti dichiarati
  `SECTION_MAX_CHARS` 24.000 (sezione citante), `BODY_MAX_CHARS` 12.000
  (corpo per una figura non citata), 600 per didascalia, testo
  alternativo e motivo del rifiuto precedente; troncamento marcato con
  «[…]».
- `_SYSTEM_PROMPTS = {"it": _SYSTEM_REVIEW_IT, "en": _SYSTEM_REVIEW_EN}`:
  `coerente` predefinito e in caso di dubbio, nessuna riscrittura di una
  figura che corrisponde al testo, tutti i nodi conservati con i loro
  id (il testo di un nodo si corregge con l'etichetta), densità ridotta
  solo sugli archi senza isolare nodi, incroci solo in diminuzione, righe
  di `data.values` e campi dell'encoding conservati in Vega-Lite,
  espressioni e dominio conservati in `function`, nessun contenuto assente
  dal
  testo, registro accademico, niente placeholder né `graph [...]` in DOT.

### Pattern condivisi (tutti i worker AI)

- **Lifecycle**: registrati in `app/main.py` lifespan; ognuno espone `start_worker()` (idempotente) + `async stop_worker()` (gracefully attende task in flight con timeout 15s).
- **Concorrenza**: `asyncio.Semaphore(N)` con N da env `COURSE_LESSON_*_MAX_CONCURRENCY`. Cap separati per ogni fase (5 struttura, 3 content/slides/speech, 2 PDF, 1 video e 1 avatar-video — un job GPU per volta).
- I worker delle **Fasi 6 e 6b** (`course_lesson_video_worker`, `course_lesson_avatar_video_worker`) condividono lo stesso scheletro (semaphore + `_inflight` + claim atomico + auto-retry + cancel-check tra fasi) pur non chiamando OpenAI; il loro `_apply_failure` ha `auto_retry_max` default 3.
- **Atomic claim** (anti-double-dispatch): `_inflight: set[UUID]` + `_inflight_lock: asyncio.Lock` con claim **PRIMA** del semaforo (pattern fix `87fbf70`). Evita che task in coda dietro al semaforo vengano ri-dispatched dal tick successivo.
- **Auto-retry trasparente**: helper `_apply_failure(lesson, *, error, recoverable, auto_retry_max)`. Errori recuperabili (rate-limit OpenAI, validazione, materializzazione) tornano a `pending` finché `attempts < auto_retry_max` (default 5). La UI vede solo "in elaborazione" finché passa.
- **Cancel-check post-OpenAI**: dopo la chiamata OpenAI/render PDF, refresh dello status dal DB; se `!= 'processing'` (utente ha cancellato), scarta il risultato senza scrivere. Il worker di Fase 3 ripete il controllo dopo la validazione degli asset, che con la revisione figura ↔ testo può durare minuti (`lesson_content_cancelled_post_assets`).
- **JSON schema strict**: tutte le chiamate OpenAI usano `response_format: {type: 'json_schema', json_schema: {strict: true, schema: {...}}}`. Validazione Pydantic post-call per ulteriore safety.
- **Audit log** per ogni azione mutating: `course.created`, `course.document.summary.ready`, `course.architecture.generated`, `course.lesson.content.generated`, `course.lesson.slides.generated`, `course.lesson.speech.generated`, `course.lesson.{slides,speech}_pdf.generated`, ecc.
- **Stale-detection setter**: i CRUD manuali settano `*_modified_at = now()` per la cascata staleness; i worker AI non lo toccano.

---

## `app/services/admin_metrics_service.py`

**Scopo**: aggregazioni platform-wide per `GET /admin/metrics` (dashboard
pannello admin).

Cache in-memory TTL 60s (`_cache: tuple[float, AdminMetricsOut] | None`
+ `asyncio.Lock` anti-thundering-herd al primo hit dopo scadenza).
Niente persistenza: dashboard informativa, una staleness < 60s è
accettabile e risparmia decine di query a ogni refresh del browser.

### Funzioni pubbliche

- `get_admin_metrics(db) -> AdminMetricsOut` — entry-point con cache.
- `invalidate_cache()` — forza refresh al prossimo `get` (per test).

### Aggregazioni interne (helper `_users` / `_orgs` / `_courses` / `_lessons` / `_cost` / `_login_activity`)

- **Users**: COUNT totali / `is_active=true` / `last_login_at >= now()-30d`.
- **Orgs**: COUNT `WHERE deleted_at IS NULL`.
- **Courses**: COUNT + GROUP BY `status` (22 valori, restituiti raw — il
  frontend li raggruppa nel widget `CoursePipelineDetail`).
- **Lessons**: COUNT + GROUP BY su ciascuno dei 5 `*_status`
  (content / slides / speech / video / avatar_video).
- **Cost**: `SUM((tokens->>'cost_usd')::float)` sulle 5 fasi che usano lo
  schema arricchito (`Course.architecture_tokens` /
  `CourseModule.lessons_structure_tokens` /
  `CourseLesson.content_tokens` / `.slides_tokens` / `.speech_tokens`).
  Glossary escluso (schema vecchio senza `cost_usd`). Totale + ultimi 7
  giorni + ultimi 30 giorni, filtrato per `*_generated_at` con
  `func.case((generated_at >= cutoff, cost), else_=None)`. Per `content`
  `_sum_cost(..., extra_keys=("assets_cost_usd",))` somma per riga il
  `cost_usd` della chiamata di Fase 3 e il costo delle chiamate degli
  asset (D16), ciascuno con `coalesce(…, 0)`: il costo degli asset conta
  anche se `cost_usd` manca (verificato da `tests/test_figure_review.py`).
- **Login activity 7g**: bucket per-giorno UTC con zero-fill su 7 entry.

---

## `app/services/org_metrics_service.py`

**Scopo**: aggregazioni org-scoped per `GET /orgs/{org_id}/metrics`
(dashboard organizzazione).

Niente cache (il traffico è già scoped per-org/utente). **Niente costi
AI nel payload**: il service non calcola nemmeno `SUM(cost_usd)` per non
sprecare query — scelta di prodotto (vedi memoria
`feedback_no_api_costs_in_org_views`).

### Funzione pubblica

- `compute_org_metrics(db, *, org_id) -> OrgMetricsOut`.

### Aggregazioni interne

- **Courses**: COUNT WHERE `organization_id` + GROUP BY `status`
  (restituito raw, il FE lo raggruppa via `CoursePipelineDetail`).
- **Lessons**: COUNT + GROUP BY `*_status` con `JOIN Course ON
  course_id WHERE Course.organization_id`. Riutilizza lo stesso pattern
  di `admin_metrics_service._by_lesson_status`.
- **Members**: COUNT membership + `pending_invitations`
  (`accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`).
