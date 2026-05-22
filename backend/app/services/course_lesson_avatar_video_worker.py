"""Background worker per il "Video con Avatar" delle lezioni (§9b).

Pattern speculare a `course_lesson_video_worker`:
- semaphore + `_inflight` set + claim atomico via status
- auto-retry trasparente (`course_lesson_avatar_video_auto_retry_max`)
- cancel-check tra fasi (e durante il lip-sync, sui poll RunPod)

Pre-condizioni runtime (verificate dopo il claim):
- `lesson.video_status == 'ready'` (il video MP4 della lezione esiste);
- l'avatar dell'assegnatario del corso ha ≥ 1 clip MiniMax pronta;
- credenziali MuseTalk/R2 configurate.

Pipeline (3 fasi con cancel-check tra una e l'altra):
1. Preparazione (1→8%): estrazione della traccia audio dal video MP4
   della lezione (sync garantita: il lip-sync userà esattamente quell'audio).
2. Lip-sync MuseTalk (10→85%): subprocess isolato del client vendored
   `app/musetalk_client/` (`synth_random_lipsync`) — campiona le clip
   MiniMax dell'avatar, le invia a RunPod insieme all'audio, scarica il
   video di avatar parlante. Il subprocess non viene mai modificato.
3. Overlay ffmpeg (86→100%): sovrappone l'avatar (quadrato, in basso a
   destra) al video della lezione, conservandone la traccia audio.

Output: `/uploads/lesson_avatar_videos/{course_id}/{lesson_id}.mp4`.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_avatar_video_service as svc

log = get_logger("app.course_lesson_avatar_video.worker")

# Client MuseTalk vendored (`app/musetalk_client/`). Risolto relativamente
# a questo modulo: `parents[1]` è la dir `app/`. Il subprocess gira con
# `cwd` su questa cartella, così `import scripts.client...` si risolve.
_MUSETALK_CLIENT_DIR = Path(__file__).resolve().parents[1] / "musetalk_client"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_inflight: set[uuid.UUID] = set()
_inflight_lock = asyncio.Lock()

_semaphore: asyncio.Semaphore | None = None
_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_active_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Auto-retry helper
# ---------------------------------------------------------------------------


def _apply_failure(
    lesson: CourseLesson,
    *,
    error: str,
    phase: str,
    recoverable: bool,
    auto_retry_max: int,
) -> bool:
    """Auto-retry come negli altri worker. True se schedulato retry,
    False se transizione terminale a `failed`."""
    attempts = lesson.avatar_video_attempts or 0
    if recoverable and attempts < auto_retry_max:
        lesson.avatar_video_status = "pending"
        lesson.avatar_video_error = None
        lesson.avatar_video_progress = 0
        lesson.avatar_video_progress_phase = None
        log.info(
            "avatar_video_auto_retry",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            phase=phase,
            attempts=attempts,
            max_retry=auto_retry_max,
            error=error[:200],
        )
        return True
    lesson.avatar_video_status = "failed"
    lesson.avatar_video_error = error[:500]
    lesson.avatar_video_progress = 0
    lesson.avatar_video_progress_phase = None
    log.warning(
        "avatar_video_failed_terminal",
        lesson_id=str(lesson.id),
        lesson_code=lesson.lesson_code,
        phase=phase,
        attempts=attempts,
        error=error[:200],
    )
    return False


# ---------------------------------------------------------------------------
# Progress / cancel helpers
# ---------------------------------------------------------------------------


async def _set_progress(
    lesson_id: uuid.UUID, *, pct: int, phase: str | None
) -> None:
    """Aggiorna `avatar_video_progress` + phase su sessione propria."""
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseLesson, lesson_id)
        if row is None:
            return
        row.avatar_video_progress = max(0, min(100, pct))
        row.avatar_video_progress_phase = phase
        await tdb.commit()


async def _check_cancelled(lesson_id: uuid.UUID) -> bool:
    """True se nel frattempo lo status è uscito da `processing` (cancel)."""
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseLesson, lesson_id)
        if row is None:
            return True
        return row.avatar_video_status != "processing"


# ---------------------------------------------------------------------------
# Subprocess / ffmpeg helpers
# ---------------------------------------------------------------------------


async def _run_cmd(args: list[str]) -> tuple[int, bytes, bytes]:
    """Esegue un comando in subprocess async → (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out, err


async def _probe_video_dims(path: Path) -> tuple[int, int] | None:
    """Larghezza×altezza del primo stream video via ffprobe, o None."""
    settings = get_settings()
    ffprobe = settings.ffmpeg_binary.replace("ffmpeg", "ffprobe")
    ret, out, _err = await _run_cmd(
        [
            ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            str(path),
        ]
    )
    if ret != 0:
        return None
    try:
        w_str, h_str = out.decode("utf-8", "replace").strip().split("x")
        return int(w_str), int(h_str)
    except Exception:  # pragma: no cover
        return None


async def _probe_duration(path: Path) -> float | None:
    """Durata in secondi del file via ffprobe, o None."""
    settings = get_settings()
    ffprobe = settings.ffmpeg_binary.replace("ffmpeg", "ffprobe")
    ret, out, _err = await _run_cmd(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    if ret != 0:
        return None
    try:
        return float(out.decode("utf-8", "replace").strip())
    except Exception:  # pragma: no cover
        return None


async def _extract_audio(video_path: Path, audio_out: Path) -> None:
    """Estrae la traccia audio dal video MP4 della lezione in un WAV mono
    16 kHz. È esattamente l'audio su cui MuseTalk farà il lip-sync, quindi
    l'avatar resta sincronizzato con lo scorrere delle slide. 16 kHz mono
    è il formato che Whisper (interno a MuseTalk) usa comunque: nessuna
    perdita per il lip-sync e upload R2 più leggero."""
    settings = get_settings()
    ret, _out, err = await _run_cmd(
        [
            settings.ffmpeg_binary,
            "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(audio_out),
        ]
    )
    if ret != 0 or not audio_out.is_file() or audio_out.stat().st_size == 0:
        raise RuntimeError(
            "Estrazione audio dal video della lezione fallita: "
            + err.decode("utf-8", "replace")[-400:]
        )


async def _overlay_avatar(
    *,
    base_video: Path,
    avatar_video: Path,
    output_path: Path,
) -> None:
    """Sovrappone il video di avatar lip-sync al video della lezione.

    L'avatar è un quadrato ancorato in basso a destra, con lato pari a
    `avatar_video_overlay_scale` della larghezza del video della lezione.
    La traccia audio del video della lezione viene conservata invariata
    (`-c:a copy`): l'audio resta uno solo, già sincronizzato.
    """
    settings = get_settings()
    dims = await _probe_video_dims(base_video)
    if dims is None:
        raise RuntimeError(
            "Impossibile leggere le dimensioni del video della lezione."
        )
    base_w, _base_h = dims
    side = max(16, int(base_w * settings.avatar_video_overlay_scale))
    # libx264 richiede dimensioni pari.
    if side % 2 == 1:
        side += 1
    margin = max(0, int(settings.avatar_video_overlay_margin))

    # [1:v] = avatar lip-sync → scalato e ritagliato a un quadrato esatto
    # side×side (robusto anche se la clip non fosse perfettamente 1:1).
    # [0:v] = video della lezione; overlay in basso a destra con margine.
    filter_complex = (
        f"[1:v]scale={side}:{side}:force_original_aspect_ratio=increase,"
        f"crop={side}:{side},setsar=1[ov];"
        f"[0:v][ov]overlay=W-w-{margin}:H-h-{margin}[outv]"
    )
    ret, _out, err = await _run_cmd(
        [
            settings.ffmpeg_binary,
            "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(base_video),
            "-i", str(avatar_video),
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "0:a:0?",
            "-c:v", settings.video_video_codec,
            "-preset", settings.video_preset,
            "-crf", str(settings.video_crf),
            "-pix_fmt", settings.video_pixel_format,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
    )
    if ret != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(
            "Overlay ffmpeg dell'avatar fallito: "
            + err.decode("utf-8", "replace")[-400:]
        )


def _musetalk_config_error(settings: Any) -> str | None:
    """None se le credenziali MuseTalk/R2 ci sono tutte, altrimenti un
    messaggio che elenca cosa manca."""
    missing: list[str] = []
    if not settings.runpod_api_key:
        missing.append("RUNPOD_API_KEY")
    if not settings.runpod_musetalk_endpoint_id:
        missing.append("RUNPOD_MUSETALK_ENDPOINT_ID")
    if not settings.r2_endpoint:
        missing.append("R2_ENDPOINT")
    if not settings.r2_bucket:
        missing.append("R2_BUCKET")
    if not settings.r2_access_key_id:
        missing.append("R2_ACCESS_KEY_ID")
    if not settings.r2_secret_access_key:
        missing.append("R2_SECRET_ACCESS_KEY")
    if missing:
        return (
            "Servizio MuseTalk non configurato: mancano "
            + ", ".join(missing)
            + "."
        )
    return None


def _musetalk_env(settings: Any) -> dict[str, str]:
    """Environment del subprocess MuseTalk: l'environment del backend +
    le credenziali RunPod/R2. Le var esplicite hanno la precedenza su
    qualunque `.env` che il client provasse a caricare da solo."""
    env = os.environ.copy()
    env["RUNPOD_API_KEY"] = settings.runpod_api_key or ""
    env["RUNPOD_ENDPOINT_ID"] = settings.runpod_musetalk_endpoint_id or ""
    env["R2_ENDPOINT"] = settings.r2_endpoint or ""
    env["R2_BUCKET"] = settings.r2_bucket or ""
    env["R2_ACCESS_KEY_ID"] = settings.r2_access_key_id or ""
    env["R2_SECRET_ACCESS_KEY"] = settings.r2_secret_access_key or ""
    # Output non bufferizzato: necessario per leggere il progress live.
    env["PYTHONUNBUFFERED"] = "1"
    return env


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Killa il subprocess MuseTalk se ancora vivo e ne attende l'uscita."""
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:  # pragma: no cover
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=15)
    except asyncio.TimeoutError:  # pragma: no cover
        pass


async def _on_musetalk_line(
    lesson_id: uuid.UUID, line: str, parsed: dict[str, Any]
) -> None:
    """Mappa una riga di stdout di `synth_random_lipsync` a un progress.

    Il client stampa milestone con tag `[build ]`, `[probe ]`, `[upload]`,
    `[submit]`, `[poll  ]`, `[dload ]`. La fase di polling RunPod è la più
    lunga e imprevedibile: si avanza in modo asintotico sui minuti
    trascorsi, restando sotto l'82%.
    """
    pct: int | None = None
    if line.startswith("[build ]") and "done" in line:
        pct = 22
    elif line.startswith("[probe ]") and "ready" in line:
        pct = 34
    elif line.startswith("[upload]"):
        pct = 38
    elif line.startswith("[submit]"):
        pct = 42
        m = re.search(r"job_id=(\S+)", line)
        if m:
            parsed["runpod_job_id"] = m.group(1)
    elif line.startswith("[poll"):
        m = re.search(r"elapsed=([\d.]+)\s*min", line)
        if m:
            pct = min(82, 44 + int(float(m.group(1)) * 4))
    elif line.startswith("[dload"):
        pct = 84
    if pct is not None:
        await _set_progress(lesson_id, pct=pct, phase="lipsync")


async def _run_musetalk_subprocess(
    lesson_id: uuid.UUID,
    *,
    clips_dir: Path,
    audio_path: Path,
    output_path: Path,
    intermediate_dir: Path,
    manifest_cache_dir: Path,
    extra_margin: int,
    left_cheek_width: int,
    right_cheek_width: int,
    seed: int,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, Any]:
    """Lancia `python -m scripts.client.synth_random_lipsync` come
    subprocess isolato (il client vendored non viene mai modificato) e
    ne segue lo stdout per il progress. Ritorna i metadata parsati.

    Solleva `RuntimeError` su fallimento, `asyncio.CancelledError` se la
    generazione viene annullata durante il polling RunPod.
    """
    cmd = [
        sys.executable,
        "-m", "scripts.client.synth_random_lipsync",
        "--clips-dir", str(clips_dir),
        "--audio", str(audio_path),
        "--output", str(output_path),
        "--intermediate-dir", str(intermediate_dir),
        "--manifest-cache-dir", str(manifest_cache_dir),
        "--extra-margin", str(extra_margin),
        "--left-cheek-width", str(left_cheek_width),
        "--right-cheek-width", str(right_cheek_width),
        "--seed", str(seed),
    ]
    log.info(
        "musetalk_subprocess_start",
        lesson_id=str(lesson_id),
        clips_dir=str(clips_dir),
        cwd=str(_MUSETALK_CLIENT_DIR),
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(_MUSETALK_CLIENT_DIR),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        # stderr unito a stdout: una sola pipe da leggere, niente deadlock.
        stderr=asyncio.subprocess.STDOUT,
    )
    tail: deque[str] = deque(maxlen=40)
    parsed: dict[str, Any] = {}
    cancelled = False

    async def _consume() -> None:
        nonlocal cancelled
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            tail.append(line)
            log.info(
                "musetalk_stdout", lesson_id=str(lesson_id), line=line[:300]
            )
            await _on_musetalk_line(lesson_id, line, parsed)
            # Cancel-check ad ogni milestone del client (build/probe/
            # upload/submit/poll/dload): annullamento reattivo.
            if await _check_cancelled(lesson_id):
                cancelled = True
                return

    try:
        await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        await _terminate(proc)
        raise RuntimeError(
            f"MuseTalk: timeout dopo {timeout_s}s.\n" + "\n".join(tail)
        ) from exc
    except BaseException:
        await _terminate(proc)
        raise

    if cancelled:
        await _terminate(proc)
        raise asyncio.CancelledError("Generazione annullata")

    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(
            f"MuseTalk: il subprocess è uscito con codice {rc}.\n"
            + "\n".join(tail)
        )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(
            "MuseTalk: subprocess completato ma il video di output è "
            "mancante o vuoto.\n" + "\n".join(tail)
        )
    log.info(
        "musetalk_subprocess_done",
        lesson_id=str(lesson_id),
        output_size=output_path.stat().st_size,
        runpod_job_id=parsed.get("runpod_job_id"),
    )
    return parsed


# ---------------------------------------------------------------------------
# Preparazione clip per MuseTalk (downscale)
# ---------------------------------------------------------------------------


async def _prepare_musetalk_clips(
    lesson_id: uuid.UUID,
    *,
    source_dir: Path,
    target_dir: Path,
    resolution: int,
) -> Path:
    """Ridimensiona le clip dell'avatar a `resolution`×`resolution` in
    `target_dir`, e ritorna `target_dir` (la `--clips-dir` da passare a
    MuseTalk).

    Le clip MiniMax sono 1080×1080: a quella risoluzione il lip-sync su
    RunPod sfora il tetto di 60 min (blending + encode + RAM scalano con
    l'area del frame). A `resolution` (default 640) i tempi rientrano —
    l'avatar nel video finale è solo ~475px, nessuna perdita visibile.

    Una clip viene riconvertita solo se la copia in `target_dir` manca o è
    più vecchia del sorgente: finché le clip dell'avatar non cambiano i
    file mantengono mtime/dimensione stabili, quindi l'hash del set di clip
    di MuseTalk resta stabile → il preprocessing resta in cache.
    """
    settings = get_settings()
    target_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted(source_dir.glob("*.mp4"))
    source_names = {s.name for s in sources}

    # Pulizia: rimuovi le clip ridimensionate il cui sorgente non c'è più.
    for stale in target_dir.glob("*.mp4"):
        if stale.name not in source_names:
            stale.unlink(missing_ok=True)

    rebuilt = 0
    for src in sources:
        dst = target_dir / src.name
        if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
            continue  # già aggiornata → riuso (hash cache stabile)
        # `force_original_aspect_ratio=increase` + crop: quadrato esatto
        # senza distorsione, robusto anche se una clip non fosse 1:1.
        ret, _out, err = await _run_cmd(
            [
                settings.ffmpeg_binary,
                "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src),
                "-vf",
                f"scale={resolution}:{resolution}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={resolution}:{resolution}",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-an",
                str(dst),
            ]
        )
        if ret != 0 or not dst.is_file() or dst.stat().st_size == 0:
            dst.unlink(missing_ok=True)
            raise RuntimeError(
                f"Ridimensionamento clip avatar fallito ({src.name}): "
                + err.decode("utf-8", "replace")[-300:]
            )
        rebuilt += 1

    if not any(target_dir.glob("*.mp4")):
        raise RuntimeError(
            "Nessuna clip avatar disponibile dopo il ridimensionamento."
        )
    log.info(
        "musetalk_clips_prepared",
        lesson_id=str(lesson_id),
        resolution=resolution,
        total=len(sources),
        rebuilt=rebuilt,
    )
    return target_dir


# ---------------------------------------------------------------------------
# Process one lesson
# ---------------------------------------------------------------------------


async def _process_one(lesson_id: uuid.UUID) -> None:
    """Genera il video con avatar per una singola lezione."""
    settings = get_settings()
    async with async_session_factory() as db:
        bare = await db.get(CourseLesson, lesson_id)
        if bare is None:
            log.warning("avatar_video_lesson_not_found", lesson_id=str(lesson_id))
            return
        if bare.avatar_video_status != "pending":
            log.info(
                "avatar_video_skip_not_pending",
                lesson_id=str(lesson_id),
                status=bare.avatar_video_status,
            )
            return
        course_id = bare.course_id
        course = await svc.load_course_full(db, course_id=course_id)
        if course is None:
            log.warning(
                "avatar_video_course_not_found",
                lesson_id=str(lesson_id),
                course_id=str(course_id),
            )
            return
        try:
            lesson = await svc.get_lesson_or_404(
                course=course, lesson_id=lesson_id
            )
        except Exception:
            return

        retry_max = settings.course_lesson_avatar_video_auto_retry_max
        organization_id = course.organization_id

        # === Pre-condition checks ====================================
        if lesson.is_assessment:
            _apply_failure(
                lesson,
                error="Le lezioni di verifica non generano video.",
                phase="precheck",
                recoverable=False,
                auto_retry_max=retry_max,
            )
            await db.commit()
            return
        if lesson.video_status != "ready" or not lesson.video_path:
            _apply_failure(
                lesson,
                error=(
                    "Il video MP4 della lezione non è ancora pronto: "
                    "genera prima il video nella scheda «Video»."
                ),
                phase="precheck",
                recoverable=False,
                auto_retry_max=retry_max,
            )
            await db.commit()
            return

        base_video_path = (settings.upload_root / lesson.video_path).resolve()
        try:
            base_video_path.relative_to(settings.upload_root.resolve())
        except ValueError:
            base_video_path = Path("/nonexistent")
        if not base_video_path.is_file():
            _apply_failure(
                lesson,
                error="Il file del video della lezione non è stato trovato.",
                phase="precheck",
                recoverable=False,
                auto_retry_max=retry_max,
            )
            await db.commit()
            return

        # Avatar dell'assegnatario + clip MiniMax.
        avatar = None
        if course.assignee_user_id is not None:
            avatar = await svc.resolve_assignee_avatar(
                db, assignee_user_id=course.assignee_user_id
            )
        if not svc.avatar_is_ready(avatar):
            _apply_failure(
                lesson,
                error=(
                    "L'avatar dell'assegnatario del corso non ha clip "
                    "pronte: genera prima le clip dell'avatar."
                ),
                phase="precheck",
                recoverable=False,
                auto_retry_max=retry_max,
            )
            await db.commit()
            return
        assert avatar is not None

        clips_dir = svc.avatar_clips_dir(avatar.user_id)
        if not clips_dir.is_dir() or not any(clips_dir.glob("*.mp4")):
            _apply_failure(
                lesson,
                error="Nessun file clip trovato per l'avatar dell'assegnatario.",
                phase="precheck",
                recoverable=False,
                auto_retry_max=retry_max,
            )
            await db.commit()
            return

        config_error = _musetalk_config_error(settings)
        if config_error is not None:
            _apply_failure(
                lesson,
                error=config_error,
                phase="precheck",
                recoverable=False,
                auto_retry_max=retry_max,
            )
            await db.commit()
            return

        # Snapshot dei valori che servono fuori dalla sessione DB.
        avatar_user_id = avatar.user_id
        musetalk_extra_margin = avatar.musetalk_extra_margin
        musetalk_left = avatar.musetalk_left_cheek_width
        musetalk_right = avatar.musetalk_right_cheek_width
        num_ready_clips = svc.count_ready_clips(avatar)

        # === Claim → processing ======================================
        lesson.avatar_video_attempts = (lesson.avatar_video_attempts or 0) + 1
        lesson.avatar_video_status = "processing"
        lesson.avatar_video_error = None
        lesson.avatar_video_progress = 1
        lesson.avatar_video_progress_phase = "preparing"
        await db.commit()

    # === Esecuzione fuori dalla sessione DB iniziale =================
    output_rel = svc.avatar_video_relative_path(
        course_id=course_id, lesson_id=lesson_id
    )
    output_path = svc.avatar_video_absolute_path(output_rel)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / f".tmp_avatar_work_{lesson_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    # Cache del preprocessing MuseTalk per set di clip: persiste fra
    # lezioni e rigenerazioni (puntiamo --clips-dir alla dir reale → hash
    # stabile). Una recompute è solo un costo, mai un errore.
    manifest_cache_dir = settings.upload_root / "musetalk_manifests"
    manifest_cache_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    try:
        # --- Phase 0: ridimensiona le clip dell'avatar per MuseTalk --
        if await _check_cancelled(lesson_id):
            log.info("avatar_video_cancelled_pre_clips", lesson_id=str(lesson_id))
            return
        await _set_progress(lesson_id, pct=2, phase="preparing")
        musetalk_clips_dir = await _prepare_musetalk_clips(
            lesson_id,
            source_dir=clips_dir,
            target_dir=svc.avatar_musetalk_clips_dir(
                avatar_user_id, settings.avatar_video_clip_resolution
            ),
            resolution=settings.avatar_video_clip_resolution,
        )

        # --- Phase 1: estrazione audio dal video della lezione -------
        if await _check_cancelled(lesson_id):
            log.info("avatar_video_cancelled_pre_audio", lesson_id=str(lesson_id))
            return
        await _set_progress(lesson_id, pct=6, phase="preparing")
        audio_path = work_dir / "lesson_audio.wav"
        await _extract_audio(base_video_path, audio_path)
        audio_duration = await _probe_duration(audio_path)
        await _set_progress(lesson_id, pct=8, phase="preparing")

        if await _check_cancelled(lesson_id):
            log.info("avatar_video_cancelled_post_audio", lesson_id=str(lesson_id))
            return

        # --- Phase 2: lip-sync MuseTalk (subprocess) -----------------
        await _set_progress(lesson_id, pct=10, phase="lipsync")
        lipsync_path = work_dir / "lipsync.mp4"
        lipsync_t0 = time.monotonic()
        parsed = await _run_musetalk_subprocess(
            lesson_id,
            clips_dir=musetalk_clips_dir,
            audio_path=audio_path,
            output_path=lipsync_path,
            intermediate_dir=work_dir / "intermediate",
            manifest_cache_dir=manifest_cache_dir,
            extra_margin=musetalk_extra_margin,
            left_cheek_width=musetalk_left,
            right_cheek_width=musetalk_right,
            # Seed deterministico per-lezione: il campionamento delle clip
            # è stabile fra rigenerazioni.
            seed=lesson_id.int & 0x7FFFFFFF,
            env=_musetalk_env(settings),
            timeout_s=settings.course_lesson_avatar_video_timeout_seconds,
        )
        lipsync_seconds = time.monotonic() - lipsync_t0
        await _set_progress(lesson_id, pct=85, phase="lipsync")

        if await _check_cancelled(lesson_id):
            log.info(
                "avatar_video_cancelled_post_lipsync", lesson_id=str(lesson_id)
            )
            return

        # --- Phase 3: overlay sul video della lezione ----------------
        await _set_progress(lesson_id, pct=87, phase="overlay")
        overlay_t0 = time.monotonic()
        await _overlay_avatar(
            base_video=base_video_path,
            avatar_video=lipsync_path,
            output_path=output_path,
        )
        overlay_ms = int((time.monotonic() - overlay_t0) * 1000)
        await _set_progress(lesson_id, pct=99, phase="overlay")

        if await _check_cancelled(lesson_id):
            log.info(
                "avatar_video_cancelled_post_overlay", lesson_id=str(lesson_id)
            )
            return

        # --- Save metadata -------------------------------------------
        tokens: dict[str, Any] = {
            "audio_duration_s": audio_duration,
            "lipsync_duration_s": round(lipsync_seconds, 1),
            "overlay_duration_ms": overlay_ms,
            "total_duration_s": round(time.monotonic() - started, 1),
            "runpod_job_id": parsed.get("runpod_job_id"),
            "num_ready_clips": num_ready_clips,
            "overlay_scale": settings.avatar_video_overlay_scale,
            "file_size_bytes": output_path.stat().st_size,
        }

        async with async_session_factory() as db:
            lesson_db = await db.get(CourseLesson, lesson_id)
            if lesson_db is None or lesson_db.avatar_video_status != "processing":
                # Annullato durante il save: niente da fare.
                return
            svc.save_avatar_video_metadata(
                lesson_db, video_rel_path=output_rel, tokens=tokens
            )
            await write_audit(
                db,
                action="course.lesson.avatar_video.generated",
                actor_user_id=None,
                organization_id=organization_id,
                target_type="course_lesson",
                target_id=str(lesson_db.id),
                metadata={
                    "course_id": str(course_id),
                    "lesson_code": lesson_db.lesson_code,
                    "avatar_video_path": output_rel,
                    **tokens,
                },
            )
            await db.commit()

        log.info(
            "avatar_video_generated",
            lesson_id=str(lesson_id),
            avatar_video_path=output_rel,
            file_size=tokens["file_size_bytes"],
            total_duration_s=tokens["total_duration_s"],
        )

    except asyncio.CancelledError:
        log.info("avatar_video_cancelled", lesson_id=str(lesson_id))
        # Lo status è già `cancelled` (impostato dall'API): niente fix.
        return
    except Exception as exc:  # noqa: BLE001
        async with async_session_factory() as db:
            lesson_db = await db.get(CourseLesson, lesson_id)
            if lesson_db is None:
                return
            # Un timeout RunPod (`TIMED_OUT`) si ripeterebbe identico:
            # niente retry. Gli errori transitori restano recuperabili.
            recoverable = "TIMED_OUT" not in str(exc)
            terminal = not _apply_failure(
                lesson_db,
                error=str(exc),
                phase=lesson_db.avatar_video_progress_phase or "unknown",
                recoverable=recoverable,
                auto_retry_max=settings.course_lesson_avatar_video_auto_retry_max,
            )
            if terminal:
                await write_audit(
                    db,
                    action="course.lesson.avatar_video.failed",
                    actor_user_id=None,
                    organization_id=organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson_id),
                    metadata={
                        "course_id": str(course_id),
                        "lesson_code": lesson_db.lesson_code,
                        "error": str(exc)[:500],
                        "attempts": lesson_db.avatar_video_attempts,
                    },
                )
            await db.commit()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Bound + tick + run loop
# ---------------------------------------------------------------------------


async def _bound_process(lesson_id: uuid.UUID) -> None:
    """Wrap `_process_one` con cap concorrenza."""
    assert _semaphore is not None
    try:
        async with _semaphore:
            await _process_one(lesson_id)
    except Exception as exc:  # pragma: no cover
        log.error(
            "avatar_video_worker_unexpected",
            lesson_id=str(lesson_id),
            error=str(exc),
            exc_info=True,
        )
    finally:
        async with _inflight_lock:
            _inflight.discard(lesson_id)


async def _tick() -> None:
    """Discovery + dispatch lezioni pending."""
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(CourseLesson.id).where(
                    CourseLesson.avatar_video_status == "pending"
                )
            )
            lesson_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning("avatar_video_worker_tick_failed", error=str(exc))
            return

    if not lesson_ids:
        return

    async with _inflight_lock:
        new_ids = [lid for lid in lesson_ids if lid not in _inflight]
        for lid in new_ids:
            _inflight.add(lid)

    for lid in new_ids:
        task = asyncio.create_task(
            _bound_process(lid),
            name=f"avatar_video_lesson_{lid}",
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(
        2, int(settings.course_lesson_avatar_video_poll_interval_seconds)
    )
    log.info(
        "course_lesson_avatar_video_worker_started",
        interval=interval,
        max_concurrency=settings.course_lesson_avatar_video_max_concurrency,
    )
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("course_lesson_avatar_video_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _worker_task is not None and not _worker_task.done():
        return
    settings = get_settings()
    _stop_event = asyncio.Event()
    _semaphore = asyncio.Semaphore(
        max(1, int(settings.course_lesson_avatar_video_max_concurrency))
    )
    _worker_task = asyncio.create_task(
        _run_loop(), name="course_lesson_avatar_video_worker"
    )


async def stop_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=15)
        except asyncio.TimeoutError:
            _worker_task.cancel()
            await asyncio.gather(_worker_task, return_exceptions=True)
    if _active_tasks:
        log.info(
            "avatar_video_worker_waiting_inflight",
            count=len(_active_tasks),
        )
        await asyncio.gather(*list(_active_tasks), return_exceptions=True)
    _worker_task = None
    _stop_event = None
    _semaphore = None
    _inflight.clear()
    _active_tasks.clear()
