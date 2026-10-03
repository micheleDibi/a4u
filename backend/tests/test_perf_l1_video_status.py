"""Prestazioni L1, task B3: stato del video e del video con avatar senza caricare il corso intero.

- `GET …/lessons-video/status`, `…/lessons/{id}/video/status`,
  `…/lessons-avatar-video/status`, `…/lessons/{id}/avatar-video/status` rispondono come prima
  (stesso DTO costruito dal corso completo di `course_service.get_course`), ma leggono solo le
  colonne video delle lezioni: nessuna SELECT con colonne `*_raw`;
- autorizzazione invariata (404 per il membro non assegnatario, 404 per la lezione assente);
- `_video_assignee_context`: l'esito di `storage.exists` del campione vocale resta in cache
  per `VOICE_SAMPLE_EXISTS_TTL_S` secondi (3 poll → 1 sola chiamata), e la chiamata gira in
  un thread (una coroutine sonda sullo stesso loop non resta ferma).
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import courses as courses_api
from app.core.permissions import R
from app.models.avatar import Avatar
from app.models.avatar_clip import AvatarClip
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import (
    course_lesson_avatar_video_service,
    course_lesson_video_service,
    course_service,
    remote_storage,
)
from tests.course_builders import build_course
from tests.test_admin_user_management import _bearer
from tests.test_permissions import _setup_user_membership

_RAW_COLUMNS = ("content_raw", "slides_raw", "speech_raw", "lessons_structure_raw")


class _Storage:
    """Storage finto: conta le `exists` e può rallentarle (`time.sleep`, come l'SFTP)."""

    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self.found = True  # esito delle `exists`
        self.exists_calls: list[str] = []
        self.entered = threading.Event()

    def exists(self, key: str) -> bool:
        self.exists_calls.append(key)
        self.entered.set()
        if self.delay_s:
            time.sleep(self.delay_s)
        return self.found


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    fake = _Storage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    monkeypatch.setattr(courses_api, "_voice_sample_exists_cache", {})
    return fake


@pytest_asyncio.fixture(autouse=True)
async def _drop_courses_created_by_test(_engine) -> AsyncIterator[None]:
    """Isolamento: il DB dei test è di sessione. A fine test cancella i corsi creati dal test
    (moduli, lezioni e documenti vanno in cascata), così nessuna riga in stato attivo
    (`pending`/`processing`) resta ai worker dei test successivi."""
    async with _engine.connect() as conn:
        started = (await conn.execute(text("SELECT clock_timestamp()"))).scalar_one()
    yield
    async with _engine.begin() as conn:
        await conn.execute(delete(Course).where(Course.created_at >= started))


async def _setup(db: AsyncSession, *, role_code: str = R.MANAGER) -> dict[str, Any]:
    """Corso 2×3 (l'ultima lezione è una verifica) assegnato all'utente, con stati video vari,
    `content_raw` pesante, avatar con campione vocale e una clip pronta."""
    user, org, _m = await _setup_user_membership(db, role_code=role_code)
    course_id, _o, _u = await build_course(
        db,
        modules=2,
        lessons_per_module=3,
        with_assessment=True,
        content_status="approved",
        slides_status="approved",
        speech_status="approved",
    )
    course = await db.get(Course, course_id)
    assert course is not None
    course.organization_id = org.id
    course.assignee_user_id = user.id
    lessons = (
        (
            await db.execute(
                select(CourseLesson)
                .where(CourseLesson.course_id == course_id)
                .order_by(CourseLesson.lesson_code)
            )
        )
        .scalars()
        .all()
    )
    t0 = datetime.now(UTC) - timedelta(days=1)
    for lesson in lessons:
        lesson.content_raw = {"blob": "x" * 5000}
    ready, running, failed = lessons[0], lessons[1], lessons[3]
    ready.video_status = "ready"
    ready.video_path = f"lesson_videos/{course_id}/{ready.id}.mp4"
    ready.video_progress = 100
    ready.video_attempts = 1
    ready.video_generated_at = t0
    ready.video_tokens = {"tts_seconds": 12}
    ready.speech_modified_at = t0 + timedelta(hours=1)  # video non aggiornato
    ready.avatar_video_status = "ready"
    ready.avatar_video_path = f"lesson_avatar_videos/{course_id}/{ready.id}.mp4"
    ready.avatar_video_generated_at = t0 - timedelta(hours=1)  # più vecchio del video
    running.video_status = "processing"
    running.video_progress = 40
    running.video_progress_phase = "tts"
    running.avatar_video_status = "pending"
    failed.video_status = "failed"
    failed.video_error = "boom"
    failed.video_attempts = 2
    avatar = Avatar(
        user_id=user.id, image_path="/uploads/avatars/a.png", audio_path="/uploads/avatars/a.wav"
    )
    db.add(avatar)
    await db.flush()
    db.add(
        AvatarClip(
            avatar_id=avatar.id,
            position=1,
            prompt_text="Saluta",
            status="ready",
            video_path="/uploads/avatars/clip1.mp4",
        )
    )
    await db.commit()
    return {
        "user": user,
        "org": org,
        "course_id": course_id,
        "ready_id": ready.id,
        "running_id": running.id,
        "base": f"/api/v1/orgs/{org.id}/courses/{course_id}",
    }


async def _expected(_engine, ctx: dict[str, Any]) -> dict[str, Any]:
    """Risposte «di prima»: stessi builder applicati al corso completo di `get_course`."""
    async with async_sessionmaker(_engine, expire_on_commit=False)() as session:
        user = ctx["user"]
        course = await course_service.get_course(
            session,
            organization_id=ctx["org"].id,
            course_id=ctx["course_id"],
            current_user=user,
            granted_permissions={"course:view_all"},
        )
        avatar = await course_lesson_video_service.resolve_assignee_avatar(
            session, assignee_user_id=user.id
        )
        clips_ready = course_lesson_avatar_video_service.avatar_is_ready(avatar)
        lesson = await course_lesson_video_service.get_lesson_or_404(
            course=course, lesson_id=ctx["ready_id"]
        )
        return {
            "video_batch": course_lesson_video_service.build_batch_out(
                course, voice_sample_available=True
            ).model_dump(mode="json"),
            "video_one": course_lesson_video_service.build_status_out(
                lesson, voice_sample_available=True
            ).model_dump(mode="json"),
            "avatar_batch": course_lesson_avatar_video_service.build_batch_out(
                course, avatar_clips_ready=clips_ready
            ).model_dump(mode="json"),
            "avatar_one": course_lesson_avatar_video_service.build_status_out(
                lesson, avatar_clips_ready=clips_ready
            ).model_dump(mode="json"),
        }


def _urls(ctx: dict[str, Any]) -> dict[str, str]:
    base, lesson_id = ctx["base"], ctx["ready_id"]
    return {
        "video_batch": f"{base}/lessons-video/status",
        "video_one": f"{base}/lessons/{lesson_id}/video/status",
        "avatar_batch": f"{base}/lessons-avatar-video/status",
        "avatar_one": f"{base}/lessons/{lesson_id}/avatar-video/status",
    }


async def test_responses_unchanged_and_no_raw_selects(
    client: AsyncClient, db: AsyncSession, _engine, storage: _Storage
) -> None:
    ctx = await _setup(db)
    expected = await _expected(_engine, ctx)
    headers = _bearer(ctx["user"].id)

    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement)

    event.listen(_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        got = {}
        for name, url in _urls(ctx).items():
            resp = await client.get(url, headers=headers)
            assert resp.status_code == 200, (name, resp.text)
            got[name] = resp.json()
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", _capture)

    assert got == expected
    # Il fixture copre i casi che contano (non solo liste vuote).
    batch = got["video_batch"]
    assert batch["total"] == 5  # la verifica è esclusa
    assert (batch["ready_count"], batch["processing_count"], batch["failed_count"]) == (1, 1, 1)
    assert batch["aggregate_progress"] == 40
    assert got["video_one"]["is_stale"] is True
    assert got["video_one"]["tokens"] == {"tts_seconds": 12}
    assert got["avatar_one"]["is_stale"] is True
    assert got["avatar_batch"]["avatar_clips_ready"] is True

    selects = [s.lower() for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert any("course_lesson" in s for s in selects)
    for stmt in selects:
        for raw in _RAW_COLUMNS:
            assert raw not in stmt, (raw, stmt)


async def test_authorization_and_missing_lesson(
    client: AsyncClient, db: AsyncSession, storage: _Storage
) -> None:
    ctx = await _setup(db)
    member, _org, _m = await _setup_user_membership(db, role_code=R.MEMBER)
    # Membro di un'altra organizzazione → 403; membro della stessa org non assegnatario → 404.
    for url in _urls(ctx).values():
        resp = await client.get(url, headers=_bearer(member.id))
        assert resp.status_code == 403, url

    other = await _setup(db, role_code=R.MEMBER)
    course = await db.get(Course, other["course_id"])
    assert course is not None
    course.assignee_user_id = ctx["user"].id
    await db.commit()
    for url in _urls(other).values():
        resp = await client.get(url, headers=_bearer(other["user"].id))
        assert resp.status_code == 404, url
        assert resp.json()["code"] == "course_not_found"

    headers = _bearer(ctx["user"].id)
    for path in ("video/status", "avatar-video/status"):
        resp = await client.get(f"{ctx['base']}/lessons/{uuid.uuid4()}/{path}", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "lesson_not_found"


async def test_voice_sample_exists_cached_with_ttl(
    client: AsyncClient, db: AsyncSession, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _setup(db)
    headers = _bearer(ctx["user"].id)
    urls = _urls(ctx)
    clock = [1000.0]
    monkeypatch.setattr(courses_api, "_cache_clock", lambda: clock[0])

    for url in (urls["video_batch"], urls["video_one"], urls["video_batch"]):
        resp = await client.get(url, headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        for item in body.get("items", [body]):
            assert item["voice_sample_available"] is True
        clock[0] += 10  # tre poll entro 60 s
    assert storage.exists_calls == [remote_storage.uploads_key("/uploads/avatars/a.wav")]

    clock[0] += courses_api.VOICE_SAMPLE_EXISTS_TTL_S  # scaduta
    resp = await client.get(urls["video_batch"], headers=headers)
    assert resp.status_code == 200
    assert len(storage.exists_calls) == 2
    assert len(courses_api._voice_sample_exists_cache) == 1


async def test_missing_voice_sample_cached_briefly(
    client: AsyncClient, db: AsyncSession, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Esito negativo in cache solo `VOICE_SAMPLE_MISSING_TTL_S` (10 s): appena il campione
    viene caricato, la UI se ne accorge in fretta."""
    assert courses_api.VOICE_SAMPLE_MISSING_TTL_S == 10.0
    assert courses_api.VOICE_SAMPLE_EXISTS_TTL_S == 60.0
    ctx = await _setup(db)
    headers = _bearer(ctx["user"].id)
    url = _urls(ctx)["video_one"]
    clock = [1000.0]
    monkeypatch.setattr(courses_api, "_cache_clock", lambda: clock[0])

    async def available() -> bool:
        resp = await client.get(url, headers=headers)
        assert resp.status_code == 200
        return resp.json()["voice_sample_available"]

    storage.found = False
    assert await available() is False
    clock[0] += 9  # ancora in cache
    assert await available() is False
    assert len(storage.exists_calls) == 1

    storage.found = True  # il docente carica il campione
    clock[0] += 1  # 10 s dopo la verifica: negativo scaduto
    assert await available() is True
    assert len(storage.exists_calls) == 2
    clock[0] += 50  # il positivo resta valido 60 s
    assert await available() is True
    assert len(storage.exists_calls) == 2


async def test_exists_runs_off_loop(
    client: AsyncClient, db: AsyncSession, storage: _Storage
) -> None:
    ctx = await _setup(db)
    url = _urls(ctx)["video_batch"]
    headers = _bearer(ctx["user"].id)
    # Prima request a vuoto: la prima chiamata a un'app appena creata paga ~300 ms di
    # inizializzazione sincrona (una tantum), che qui non interessa.
    assert (await client.get(url, headers=headers)).status_code == 200
    courses_api._voice_sample_exists_cache.clear()
    storage.exists_calls.clear()
    storage.entered.clear()
    storage.delay_s = 0.3
    worst_ms: list[float] = []

    async def probe() -> None:
        """Ritardo massimo di brevi sleep sul loop finché la `exists` lenta (0,3 s) è in
        corso: se girasse sul loop, un giro della sonda durerebbe ~300 ms."""
        worst = 0.0
        deadline = time.perf_counter() + 5
        while not storage.entered.is_set() and time.perf_counter() < deadline:
            started = time.perf_counter()
            await asyncio.sleep(0.005)
            worst = max(worst, time.perf_counter() - started)
        started = time.perf_counter()
        await asyncio.sleep(0.01)  # la `exists` sta ancora dormendo nel thread
        worst = max(worst, time.perf_counter() - started)
        worst_ms.append(worst * 1000)

    resp, _ = await asyncio.gather(client.get(url, headers=headers), probe())
    assert resp.status_code == 200
    assert len(storage.exists_calls) == 1
    assert worst_ms and worst_ms[0] < 100, worst_ms
