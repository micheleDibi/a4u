"""Prestazioni L1 — W3: avatar e video non bloccano l'event loop.

Contratto `docs/contracts/perf-l1-conventions.md` §1: storage, soundfile,
decode audio e pulizia delle cartelle di lavoro girano in un thread. Ogni
caso rende lento apposta il passo sincrono (`time.sleep`) e una sonda sullo
stesso loop deve restare sotto 100 ms; il risultato deve essere quello di
prima.
"""

from __future__ import annotations

import asyncio
import base64
import io
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from app.services import (
    avatar_clip_worker,
    avatar_service,
    course_lesson_video_worker,
    lesson_audio_cache,
    remote_storage,
    runpod_tts_client,
)
from app.services import course_lesson_video_worker as cvw
from app.services.minimax_service import TaskStatus
from tests.test_perf_l1_offloop_pdf import MAX_LAG_SEC, SLOW_SEC, _run_with_probe


def _lag_msg(lag: float) -> str:
    return f"loop fermo per {lag * 1000:.0f} ms"


class _SlowStorage:
    """Storage finto: ogni operazione dorme `SLOW_SEC` e viene registrata."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.files: dict[str, bytes] = {}

    def delete(self, key: str) -> None:
        time.sleep(SLOW_SEC)
        self.calls.append(("delete", key))

    def delete_prefix(self, key: str) -> None:
        time.sleep(SLOW_SEC)
        self.calls.append(("delete_prefix", key))

    def upload_bytes(self, key: str, data: bytes) -> None:
        time.sleep(SLOW_SEC)
        self.calls.append(("upload", key))
        self.files[key] = data


@pytest.fixture
def slow_storage(monkeypatch: pytest.MonkeyPatch) -> _SlowStorage:
    fake = _SlowStorage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeDb:
    """Sessione finta: `execute` restituisce le clip date, il resto registra."""

    def __init__(self, clips: list[Any]) -> None:
        self.clips = clips
        self.deleted: list[Any] = []

    async def execute(self, *args: Any, **kwargs: Any) -> _FakeResult:
        return _FakeResult(self.clips)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        return None


async def _no_audit(*args: Any, **kwargs: Any) -> None:
    return None


def _key(path: str) -> str:
    return remote_storage.uploads_key(path)


# ---------------------------------------------------------------------------
# avatar_service: delete sullo storage
# ---------------------------------------------------------------------------


def _clips(user_id: uuid.UUID) -> list[Any]:
    return [
        SimpleNamespace(video_path=f"/uploads/avatars/{user_id}/clips/a.mp4"),
        SimpleNamespace(video_path=None),
        SimpleNamespace(video_path=f"/uploads/avatars/{user_id}/clips/b.mp4"),
    ]


async def test_delete_my_avatar_deletes_files_off_loop(
    monkeypatch: pytest.MonkeyPatch, slow_storage: _SlowStorage
) -> None:
    monkeypatch.setattr(avatar_service, "write_audit", _no_audit)
    user_id = uuid.uuid4()
    clips = _clips(user_id)
    avatar = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        image_path=f"/uploads/avatars/{user_id}/image.png",
        audio_path=f"/uploads/avatars/{user_id}/audio.wav",
    )
    db = _FakeDb(clips)

    result, lag = await _run_with_probe(
        avatar_service.delete_my_avatar(db, avatar=avatar, actor_id=user_id)  # type: ignore[arg-type]
    )

    assert result is None
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    # Stesse operazioni e stesso ordine di prima.
    assert slow_storage.calls == [
        ("delete", _key(clips[0].video_path)),
        ("delete", _key(clips[2].video_path)),
        ("delete", _key(avatar.image_path)),
        ("delete", _key(avatar.audio_path)),
        ("delete_prefix", _key(f"/uploads/avatars/{user_id}/clips")),
        ("delete_prefix", _key(f"/uploads/avatars/{user_id}")),
    ]
    assert db.deleted == [avatar]


async def test_reset_clips_deletes_videos_off_loop(slow_storage: _SlowStorage) -> None:
    user_id = uuid.uuid4()
    clips = _clips(user_id)
    db = _FakeDb(clips)
    avatar = SimpleNamespace(id=uuid.uuid4())

    result, lag = await _run_with_probe(avatar_service._reset_clips(db, avatar))  # type: ignore[arg-type]

    assert result is None
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    assert slow_storage.calls == [
        ("delete", _key(clips[0].video_path)),
        ("delete", _key(clips[2].video_path)),
    ]
    assert db.deleted == clips


async def test_upsert_deletes_replaced_files_off_loop(
    monkeypatch: pytest.MonkeyPatch, slow_storage: _SlowStorage
) -> None:
    user_id = uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        image_path=f"/uploads/avatars/{user_id}/image.png",
        audio_path=f"/uploads/avatars/{user_id}/audio.wav",
        audio_lang="it",
        clips_status="ready",
    )

    async def get_avatar(db: Any, uid: uuid.UUID) -> Any:
        return existing

    async def save_image(upload: Any, **kwargs: Any) -> str:
        return f"/uploads/avatars/{user_id}/image.jpg"

    async def save_audio(upload: Any, **kwargs: Any) -> str:
        return f"/uploads/avatars/{user_id}/audio.mp3"

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(avatar_service, "get_my_avatar", get_avatar)
    monkeypatch.setattr(avatar_service.file_service, "save_upload_image", save_image)
    monkeypatch.setattr(avatar_service.file_service, "save_upload_audio", save_audio)
    monkeypatch.setattr(avatar_service, "_reset_clips", noop)
    monkeypatch.setattr(avatar_service, "_create_pending_clips", noop)
    monkeypatch.setattr(avatar_service, "write_audit", _no_audit)
    old_image, old_audio = existing.image_path, existing.audio_path

    result, lag = await _run_with_probe(
        avatar_service.upsert_my_avatar(
            _FakeDb([]),  # type: ignore[arg-type]
            user_id=user_id,
            image_upload=object(),  # type: ignore[arg-type]
            audio_upload=object(),  # type: ignore[arg-type]
            audio_lang=None,
            actor_id=user_id,
        )
    )

    assert result is existing
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    assert slow_storage.calls == [("delete", _key(old_image)), ("delete", _key(old_audio))]
    assert existing.image_path.endswith("image.jpg")
    assert existing.audio_path.endswith("audio.mp3")


# ---------------------------------------------------------------------------
# avatar_clip_worker: cartella temporanea e upload della clip
# ---------------------------------------------------------------------------


async def test_clip_poll_uploads_and_cleans_up_off_loop(
    monkeypatch: pytest.MonkeyPatch, slow_storage: _SlowStorage
) -> None:
    original_rmtree = shutil.rmtree
    removed: list[str] = []

    def slow_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        time.sleep(SLOW_SEC)
        removed.append(str(path))
        original_rmtree(path, *args, **kwargs)

    async def status(task_id: str) -> TaskStatus:
        return TaskStatus(status="success", file_id="f1", raw={})

    async def download(file_id: str) -> bytes:
        return b"clip-mp4"

    async def wide(path: Path) -> tuple[int, int]:
        return (1280, 720)

    async def fake_ffmpeg(args: list[str]) -> tuple[int, bytes, bytes]:
        Path(args[-1]).write_bytes(b"squared-" + Path(args[args.index("-i") + 1]).read_bytes())
        return 0, b"", b""

    monkeypatch.setattr(avatar_clip_worker.shutil, "rmtree", slow_rmtree)
    monkeypatch.setattr(avatar_clip_worker.minimax_service, "query_task_status", status)
    monkeypatch.setattr(avatar_clip_worker.minimax_service, "download_file", download)
    monkeypatch.setattr(avatar_clip_worker, "_probe_video_dims", wide)
    monkeypatch.setattr(avatar_clip_worker, "_run_cmd", fake_ffmpeg)
    monkeypatch.setattr(avatar_clip_worker, "write_audit", _no_audit)
    user_id = uuid.uuid4()
    clip = SimpleNamespace(id=uuid.uuid4(), minimax_task_id="t1", status="processing")
    avatar = SimpleNamespace(id=uuid.uuid4(), user_id=user_id)

    result, lag = await _run_with_probe(
        avatar_clip_worker._poll_processing(None, clip, avatar)  # type: ignore[arg-type]
    )

    assert result is None
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    path = f"/uploads/avatars/{user_id}/clips/{clip.id}.mp4"
    assert clip.video_path == path and clip.status == "ready"
    # Clip ritagliata (ffmpeg finto) caricata, cartella temporanea rimossa.
    assert slow_storage.files == {_key(path): b"squared-clip-mp4"}
    assert len(removed) == 1 and not Path(removed[0]).exists()


# ---------------------------------------------------------------------------
# course_lesson_video_worker: cache audio (soundfile) e cartella di lavoro
# ---------------------------------------------------------------------------


def _speech() -> dict[str, Any]:
    return {
        "speech_segments": [
            {"segment_id": "g1", "text": "Primo."},
            {"segment_id": "g2", "text": "Secondo."},
        ]
    }


def _audio() -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, 2400, dtype=np.float32)
    return {"g1": 0.25 * np.sin(40 * t), "g2": 0.5 * np.cos(30 * t)}


async def test_tts_phase_audio_cache_runs_off_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def no_cancel(lesson_id: uuid.UUID) -> bool:
        return False

    async def no_progress(*args: Any, **kwargs: Any) -> None:
        return None

    synth_calls: list[str] = []

    async def synth(**kwargs: Any) -> tuple[dict[str, np.ndarray], int]:
        synth_calls.append(kwargs["voice_sample_url"])
        return _audio(), runpod_tts_client.SAMPLE_RATE

    monkeypatch.setattr(cvw, "_check_cancelled", no_cancel)
    monkeypatch.setattr(cvw, "_set_progress", no_progress)
    monkeypatch.setattr(cvw.runpod_tts_client, "synthesize_lesson_audio", synth)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(lesson_audio_cache, "_cache_dir", lambda course_id, lesson_id: cache_dir)
    for name in ("compute_cache_key", "load", "save"):
        original = getattr(lesson_audio_cache, name)

        def slow(*args: Any, _fn: Any = original, **kwargs: Any) -> Any:
            time.sleep(SLOW_SEC)
            return _fn(*args, **kwargs)

        monkeypatch.setattr(lesson_audio_cache, name, slow)
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voce")
    args: dict[str, Any] = {
        "course_id": uuid.uuid4(),
        "speech_raw": _speech(),
        "voice_sample_path": voice,
        "voice_sample_url": "https://storage/voice.wav",
        "language_code": "it",
    }
    lesson_id = uuid.uuid4()

    # Primo giro: cache vuota → sintesi e salvataggio dei WAV.
    first, lag1 = await _run_with_probe(cvw._run_tts_phase(lesson_id, **args))
    # Secondo giro: stessa chiave → audio ricaricato dalla cache.
    second, lag2 = await _run_with_probe(cvw._run_tts_phase(lesson_id, **args))

    assert lag1 < MAX_LAG_SEC, _lag_msg(lag1)
    assert lag2 < MAX_LAG_SEC, _lag_msg(lag2)
    assert synth_calls == ["https://storage/voice.wav"]  # una sola sintesi
    audio, rate, _secs = second
    assert rate == runpod_tts_client.SAMPLE_RATE and second[2] == 0.0
    expected = _audio()
    assert set(audio) == set(expected)
    for sid, arr in expected.items():
        np.testing.assert_allclose(audio[sid], arr, atol=1e-4)  # PCM_16
    assert set(first[0]) == set(expected)


async def test_video_worker_cleans_work_dir_off_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_process_one` fino alla fase TTS (annullata): la `finally` rimuove la
    cartella di lavoro in un thread."""
    lesson_id, course_id = uuid.uuid4(), uuid.uuid4()
    lesson = SimpleNamespace(
        id=lesson_id,
        course_id=course_id,
        video_status="pending",
        speech_status="approved",
        slides_status="approved",
        video_attempts=0,
        speech_raw={},
    )
    course = SimpleNamespace(
        id=course_id, assignee_user_id=uuid.uuid4(), video_language_code=None, language_code="it"
    )

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, *args: Any) -> Any:
            return lesson

        async def commit(self) -> None:
            return None

    async def value(v: Any) -> Any:
        return v

    class _Storage:
        def exists(self, key: str) -> bool:
            return True

        def download_to(self, key: str, dest: Path) -> None:
            Path(dest).write_bytes(b"voce")

    svc = cvw.course_lesson_video_service
    monkeypatch.setattr(cvw, "async_session_factory", _Session)
    monkeypatch.setattr(svc, "load_course_full", lambda db, course_id: value(course))
    monkeypatch.setattr(svc, "get_lesson_or_404", lambda course, lesson_id: value(lesson))
    monkeypatch.setattr(
        svc,
        "resolve_assignee_avatar",
        lambda db, assignee_user_id: value(SimpleNamespace(audio_path="/uploads/v.wav")),
    )
    monkeypatch.setattr(
        svc, "resolve_voice_sample_ref", lambda db, assignee_user_id: value("/uploads/v.wav")
    )
    monkeypatch.setattr(svc, "_recompute_course_video_status", lambda db, cid: value(None))
    monkeypatch.setattr(svc, "video_relative_path", lambda **kw: "lesson_videos/x.mp4")
    monkeypatch.setattr(svc, "video_absolute_path", lambda rel: tmp_path / "videos" / "x.mp4")
    monkeypatch.setattr(cvw.runpod_tts_client, "is_configured", lambda: True)
    monkeypatch.setattr(cvw.lesson_video_compose_service, "parse_speech_raw", lambda raw: {})
    monkeypatch.setattr(remote_storage, "get_storage", lambda: _Storage())

    async def cancelled_tts(*args: Any, **kwargs: Any) -> Any:
        raise asyncio.CancelledError("Generazione annullata")

    monkeypatch.setattr(cvw, "_run_tts_phase", cancelled_tts)
    original_rmtree = shutil.rmtree
    removed: list[str] = []

    def slow_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        time.sleep(SLOW_SEC)
        removed.append(str(path))
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", slow_rmtree)

    result, lag = await _run_with_probe(course_lesson_video_worker._process_one(lesson_id))

    assert result is None
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    work_dir = tmp_path / "videos" / f".tmp_work_{lesson_id}"
    assert removed == [str(work_dir)] and not work_dir.exists()


# ---------------------------------------------------------------------------
# runpod_tts_client: decode FLAC fuori dal loop
# ---------------------------------------------------------------------------


def _flac_b64(arr: np.ndarray) -> str:
    buf = io.BytesIO()
    sf.write(buf, arr, runpod_tts_client.SAMPLE_RATE, format="FLAC")
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def test_tts_client_decodes_audio_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = _audio()
    half = len(audio["g1"]) // 2
    outputs = [
        {"segment_id": "g1", "chunk_index": 1, "audio_b64": _flac_b64(audio["g1"][half:])},
        {"segment_id": "g2", "audio_b64": _flac_b64(audio["g2"])},
        {"segment_id": "g1", "chunk_index": 0, "audio_b64": _flac_b64(audio["g1"][:half])},
    ]

    async def submit(*args: Any, **kwargs: Any) -> str:
        return "job-1"

    async def collect(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return outputs

    original = runpod_tts_client._decode_segment_audio

    def slow_decode(audio_b64: str) -> np.ndarray:
        time.sleep(SLOW_SEC)
        return original(audio_b64)

    monkeypatch.setattr(runpod_tts_client, "_endpoint_base", lambda: ("https://rp", "k"))
    monkeypatch.setattr(runpod_tts_client, "_submit_job", submit)
    monkeypatch.setattr(runpod_tts_client, "_collect_outputs", collect)
    monkeypatch.setattr(runpod_tts_client, "_decode_segment_audio", slow_decode)

    result, lag = await _run_with_probe(
        runpod_tts_client.synthesize_lesson_audio(
            speech_raw=_speech(), voice_sample_url="https://storage/v.wav", language_code="it"
        )
    )

    assert not isinstance(result, Exception), result
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    got, rate = result
    assert rate == runpod_tts_client.SAMPLE_RATE
    # Chunk riordinati e concatenati come prima; FLAC è senza perdita a 16 bit.
    for sid, arr in audio.items():
        np.testing.assert_allclose(got[sid], arr, atol=1e-4)


def test_assemble_raises_on_worker_error() -> None:
    """Un output con `error` fa fallire il job come prima (ora nel thread)."""
    with pytest.raises(runpod_tts_client.RunpodJobFailedError, match="boom"):
        runpod_tts_client._assemble_segments_audio([{"error": "boom"}])
