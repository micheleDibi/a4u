"""Composizione MP4 finale (slide PNG + audio TTS) via ffmpeg.

Pipeline:
1. Per ogni slide, prendiamo i suoi `segment_ids` dal
   `speech.slide_to_segments_map`, concateniamo i WAV TTS dei segment in
   `audio_slide_NNN.wav`.
2. Per ogni slide, ffmpeg `-loop 1 -i slide_NNN.png -i audio_slide.wav
   -shortest -tune stillimage -c:v libx264 -c:a aac` → `seg_NNN.mp4`.
3. Concat finale: ffmpeg `-f concat -safe 0 -i list.txt -c copy` → out.mp4.

Tutte le operazioni sono async (subprocess via `asyncio.create_subprocess_exec`)
così il worker resta responsivo per gli updates di progress.

Pre-condizione (assicurata dal worker): `audio_per_segment` deve
contenere tutti i `segment_id` referenziati in `slide_to_segments_map`.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.lesson_video_compose")


# ---------------------------------------------------------------------------
# Errori
# ---------------------------------------------------------------------------


class VideoComposeError(Exception):
    """Errore durante la composizione video. Recuperabile (retry worker)."""


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


async def _run_ffmpeg(args: list[str]) -> tuple[int, bytes, bytes]:
    """Esegue ffmpeg in subprocess async. Restituisce (returncode, stdout, stderr).

    Su Windows, asyncio.create_subprocess_exec richiede ProactorEventLoop;
    questo modulo viene chiamato da un thread con loop dedicato dal
    worker (analogo a Playwright in slides_pdf service), quindi è già
    compatibile. Su Linux/Mac niente vincoli.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout, stderr


async def probe_audio_duration(wav_path: Path) -> float:
    """Durata in secondi di un WAV. Niente ffprobe — usa soundfile."""
    try:
        info = sf.info(str(wav_path))
        return float(info.frames) / float(info.samplerate)
    except Exception as exc:
        raise VideoComposeError(
            f"Impossibile leggere durata WAV {wav_path.name}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compose_lesson_video(
    *,
    lesson_speech_raw: dict[str, Any],
    png_paths: list[Path],
    slide_id_order: list[str],
    audio_per_segment: dict[str, np.ndarray],
    audio_sample_rate: int,
    output_path: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Compone il video MP4 finale.

    Args:
        lesson_speech_raw: dict serializzato di `LessonSpeechOutput`
            (`speech_raw`). Da qui leggiamo `slide_to_segments_map`.
        png_paths: lista ordinata di PNG, una per slide. Stesso ordine di
            `slide_id_order`.
        slide_id_order: `slide_id` corrispondente a ciascun PNG.
        audio_per_segment: dict `segment_id → ndarray float32 mono`
            generato dal worker via XTTS.
        audio_sample_rate: sample rate WAV in input (24000 nativo XTTS).
        output_path: path file MP4 finale.
        on_progress: callback(done_slides, total_slides) per UI.

    Returns:
        Dict metadata: {audio_duration_s, video_duration_s,
        encode_duration_ms, num_segments, file_size_bytes}.
    """
    if len(png_paths) != len(slide_id_order):
        raise VideoComposeError(
            f"PNG count ({len(png_paths)}) != slide_id_order count "
            f"({len(slide_id_order)})."
        )
    if not png_paths:
        raise VideoComposeError("Nessuna slide da comporre.")

    settings = get_settings()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Work dir per file intermedi: pulito sempre, anche su failure.
    work_dir = output_path.parent / f".tmp_{output_path.stem}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        slide_to_segments_map = lesson_speech_raw.get(
            "slide_to_segments_map"
        ) or []
        segments_by_slide: dict[str, list[str]] = {}
        for entry in slide_to_segments_map:
            if isinstance(entry, dict):
                sid = str(entry.get("slide_id") or "")
                seg_ids = [
                    str(s) for s in entry.get("segment_ids") or []
                ]
                if sid:
                    segments_by_slide[sid] = seg_ids

        total = len(png_paths)
        segment_mp4s: list[Path] = []
        encode_started = asyncio.get_event_loop().time()
        audio_total_seconds = 0.0

        for idx, (png_path, slide_id) in enumerate(
            zip(png_paths, slide_id_order, strict=True)
        ):
            seg_ids = segments_by_slide.get(slide_id, [])
            if not seg_ids:
                log.warning(
                    "video_compose_slide_without_segments",
                    slide_id=slide_id,
                    idx=idx,
                )
                # Fallback: 2s di silenzio per non far sparire la slide.
                audio_concat = np.zeros(
                    int(audio_sample_rate * 2.0), dtype=np.float32
                )
            else:
                pieces: list[np.ndarray] = []
                for sid in seg_ids:
                    a = audio_per_segment.get(sid)
                    if a is None or len(a) == 0:
                        log.warning(
                            "video_compose_missing_segment_audio",
                            segment_id=sid,
                            slide_id=slide_id,
                        )
                        continue
                    pieces.append(a.astype(np.float32, copy=False))
                if not pieces:
                    audio_concat = np.zeros(
                        int(audio_sample_rate * 2.0), dtype=np.float32
                    )
                else:
                    audio_concat = np.concatenate(pieces)

            audio_wav = work_dir / f"audio_{idx + 1:03d}.wav"
            sf.write(
                str(audio_wav),
                audio_concat,
                audio_sample_rate,
                subtype="PCM_16",
            )
            audio_seconds = await probe_audio_duration(audio_wav)
            audio_total_seconds += audio_seconds

            seg_mp4 = work_dir / f"seg_{idx + 1:03d}.mp4"
            ret, _stdout, stderr = await _run_ffmpeg(
                [
                    settings.ffmpeg_binary,
                    "-y",
                    "-loop",
                    "1",
                    "-framerate",
                    str(settings.video_framerate),
                    "-i",
                    str(png_path),
                    "-i",
                    str(audio_wav),
                    "-c:v",
                    settings.video_video_codec,
                    "-tune",
                    "stillimage",
                    "-preset",
                    settings.video_preset,
                    "-crf",
                    str(settings.video_crf),
                    "-pix_fmt",
                    settings.video_pixel_format,
                    "-r",
                    str(settings.video_framerate),
                    "-c:a",
                    "aac",
                    "-b:a",
                    settings.video_audio_bitrate,
                    "-ar",
                    str(settings.video_audio_sample_rate),
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(seg_mp4),
                ]
            )
            if ret != 0:
                err_tail = stderr.decode("utf-8", errors="replace")[-1500:]
                raise VideoComposeError(
                    f"ffmpeg fallito su segmento {idx + 1}/{total}: {err_tail}"
                )
            segment_mp4s.append(seg_mp4)
            if on_progress is not None:
                try:
                    on_progress(idx + 1, total)
                except Exception:  # pragma: no cover
                    pass

        # Concat finale via demuxer concat (no re-encode).
        list_file = work_dir / "concat.txt"
        list_file.write_text(
            "\n".join(
                f"file '{p.as_posix()}'" for p in segment_mp4s
            ),
            encoding="utf-8",
        )
        ret, _stdout, stderr = await _run_ffmpeg(
            [
                settings.ffmpeg_binary,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        if ret != 0:
            err_tail = stderr.decode("utf-8", errors="replace")[-1500:]
            raise VideoComposeError(
                f"ffmpeg concat finale fallito: {err_tail}"
            )

        encode_duration_ms = int(
            (asyncio.get_event_loop().time() - encode_started) * 1000
        )
        file_size = output_path.stat().st_size
        # video_duration ≈ audio_total (gli MP4 hanno -shortest, quindi
        # il video dura quanto il suo audio).
        log.info(
            "video_compose_done",
            output=str(output_path),
            segments=len(segment_mp4s),
            audio_seconds=round(audio_total_seconds, 2),
            file_size_bytes=file_size,
            encode_ms=encode_duration_ms,
        )
        return {
            "audio_duration_s": round(audio_total_seconds, 2),
            "video_duration_s": round(audio_total_seconds, 2),
            "encode_duration_ms": encode_duration_ms,
            "num_segments_encoded": len(segment_mp4s),
            "file_size_bytes": file_size,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Sync wrapper per worker (Windows: nuovo ProactorEventLoop in thread)
# ---------------------------------------------------------------------------


def compose_lesson_video_sync(
    **kwargs: Any,
) -> dict[str, Any]:
    """Wrapper sync che crea un loop dedicato (ProactorEventLoop su Win
    per supporto subprocess) e chiama `compose_lesson_video`. Eseguibile
    via `asyncio.to_thread` dal worker."""
    import sys

    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(compose_lesson_video(**kwargs))
    finally:
        try:
            loop.close()
        except Exception:  # pragma: no cover
            pass


def parse_speech_raw(raw: Any) -> dict[str, Any]:
    """Normalizza `speech_raw` (può essere dict serializzato o stringa JSON)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:  # pragma: no cover
            return {}
    return {}
