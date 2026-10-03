"""`GET /orgs/{org_id}/courses/{course_id}/status`: stato leggero per il polling (Prestazioni L1).

Contratto: `docs/contracts/perf-l1-course-status.md`.
- forma esatta (chiavi per livello), nessuna chiave `*_raw`/`*_tokens`, valori coerenti con
  il dettaglio, ordinamento di moduli, lezioni (per `position`) e documenti (`created_at, id`);
- autorizzazione ed errori identici al dettaglio `GET …/courses/{id}` (stessa regola condivisa
  `_can_view_course`: il dettaglio non cambia comportamento);
- `ETag` = sha256 del corpo, `If-None-Match` (anche debole, in lista o `*`) → 304 senza corpo;
  `Cache-Control: no-cache`;
- corso da 96 lezioni con `content_raw` da ~60 KB → corpo ≤ 200 KB e nessuna SELECT con
  colonne `*_raw`/`*_tokens`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import R
from app.core.security import hash_password
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.user import User
from tests.course_builders import build_course, build_course_document
from tests.test_admin_user_management import _bearer
from tests.test_permissions import _setup_user_membership

_PHASES = ("content", "slides", "speech")
_EXPORTS = ("pdf", "slides_pdf", "speech_pdf")

COURSE_KEYS = {
    "course_id",
    "status",
    "updated_at",
    "architecture_progress",
    "architecture_progress_phase",
    "architecture_error",
    "architecture_attempts",
    "architecture_generated_at",
    "glossary_status",
    "glossary_generated_at",
    "glossary_error",
    "documents",
    "modules",
}
DOCUMENT_KEYS = {
    "id",
    "summary_status",
    "summary_generated_at",
    "summary_error",
    "summary_attempts",
    "summary_coverage",
    "summary_chunks_total",
    "summary_chunks_done",
    "figures_status",
    "figures_error_code",
    "figures_count",
    "figures_coverage",
    "figures_pages_total",
    "figures_pages_done",
    "figures_progress",
    "figures_requested_at",
}
MODULE_KEYS = {
    "id",
    "lessons_structure_status",
    "lessons_structure_progress",
    "lessons_structure_progress_phase",
    "lessons_structure_error",
    "lessons_structure_attempts",
    "lessons_structure_generated_at",
    "lessons_structure_approved_at",
    "architecture_modified_at",
    "lessons",
}
LESSON_KEYS = (
    {"id", "lesson_structure_modified_at", "video_status", "avatar_video_status"}
    | {
        f"{p}_{s}"
        for p in _PHASES
        for s in (
            "status",
            "progress",
            "progress_phase",
            "error",
            "attempts",
            "generated_at",
            "approved_at",
            "modified_at",
        )
    }
    | {
        f"{q}_{s}"
        for q in _EXPORTS
        for s in ("status", "progress", "progress_phase", "error", "attempts", "generated_at")
    }
)

# Colonne JSONB grandi che la query di stato non deve mai leggere.
_HEAVY_COLUMNS = (
    "content_raw",
    "slides_raw",
    "speech_raw",
    "architecture_raw",
    "glossary_raw",
    "lessons_structure_raw",
    "_tokens",
)


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


async def _setup(
    db: AsyncSession,
    *,
    role_code: str = R.MANAGER,
    modules: int = 2,
    lessons_per_module: int = 2,
    assign_to_user: bool = True,
) -> dict[str, Any]:
    """Utente con ruolo `role_code` nell'org e corso di quell'org (assegnato a lui o no)."""
    user, org, _m = await _setup_user_membership(db, role_code=role_code)
    course_id, _o, other_user = await build_course(
        db, modules=modules, lessons_per_module=lessons_per_module, content_status="ready"
    )
    course = await db.get(Course, course_id)
    assert course is not None
    course.organization_id = org.id
    course.assignee_user_id = user.id if assign_to_user else other_user.id
    await db.commit()
    return {
        "user": user,
        "org": org,
        "course_id": course_id,
        "base": f"/api/v1/orgs/{org.id}/courses/{course_id}",
    }


async def _get_both(client: AsyncClient, base: str, user_id: uuid.UUID) -> tuple[Any, Any]:
    headers = _bearer(user_id)
    detail = await client.get(base, headers=headers)
    status = await client.get(f"{base}/status", headers=headers)
    return detail, status


def _assert_same_outcome(detail: Any, status: Any, expected: int) -> None:
    assert detail.status_code == expected, detail.text
    assert status.status_code == expected, status.text
    if expected >= 400:
        assert status.json()["code"] == detail.json()["code"]


# --- Forma e valori ---------------------------------------------------------------------


async def test_status_shape_matches_contract_and_detail(client: AsyncClient, db: AsyncSession):
    ctx = await _setup(db)
    course_id = ctx["course_id"]
    t0 = datetime.now(UTC)
    # Documenti inseriti in ordine inverso rispetto a `created_at`: conta `created_at, id`.
    late = build_course_document(course_id, filename="tardi.pdf", created_at=t0)
    early = build_course_document(
        course_id, filename="presto.pdf", created_at=t0 - timedelta(hours=1)
    )
    late.figures_status = "processing"
    late.figures_progress = {"stage": "pages", "done": 2}
    db.add_all([late, early])
    # Moduli con `position` invertita rispetto all'inserimento.
    mods = (
        (
            await db.execute(
                select(CourseModule)
                .where(CourseModule.course_id == course_id)
                .order_by(CourseModule.position)
            )
        )
        .scalars()
        .all()
    )
    mods[0].position = 99
    await db.flush()
    mods[1].position = 1
    lesson = (
        await db.execute(
            select(CourseLesson).where(
                CourseLesson.module_id == mods[1].id, CourseLesson.position == 2
            )
        )
    ).scalar_one()
    lesson.content_progress = 42
    lesson.content_progress_phase = "writing"
    lesson.slides_status = "pending"
    lesson.content_raw = {"blob": "x" * 1000}
    lesson.content_tokens = {"input": 1}
    await db.commit()

    detail, resp = await _get_both(client, ctx["base"], ctx["user"].id)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["cache-control"] == "no-cache"
    body = resp.json()

    assert set(body) == COURSE_KEYS
    assert body["course_id"] == str(course_id)
    assert [d["id"] for d in body["documents"]] == [str(early.id), str(late.id)]
    assert [m["id"] for m in body["modules"]] == [str(mods[1].id), str(mods[0].id)]
    for doc in body["documents"]:
        assert set(doc) == DOCUMENT_KEYS
    for module in body["modules"]:
        assert set(module) == MODULE_KEYS
        assert len(module["lessons"]) == 2
        for les in module["lessons"]:
            assert set(les) == LESSON_KEYS

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert not key.endswith(("_raw", "_tokens")), key
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)

    first = body["modules"][0]["lessons"][1]
    assert first["id"] == str(lesson.id)
    assert first["content_progress"] == 42
    assert first["content_progress_phase"] == "writing"
    assert first["slides_status"] == "pending"
    assert first["video_status"] == lesson.video_status
    assert first["avatar_video_status"] == lesson.avatar_video_status
    assert body["documents"][1]["figures_progress"] == {"stage": "pages", "done": 2}

    # Ogni campo comune ha lo stesso valore del dettaglio (stessi tipi e default).
    full = detail.json()
    for key in COURSE_KEYS - {"course_id", "documents", "modules"}:
        assert body[key] == full[key], key
    docs_full = {d["id"]: d for d in full["documents"]}
    for doc in body["documents"]:
        for key in DOCUMENT_KEYS:
            assert doc[key] == docs_full[doc["id"]][key], key
    mods_full = {m["id"]: m for m in full["modules"]}
    for module in body["modules"]:
        m_full = mods_full[module["id"]]
        for key in MODULE_KEYS - {"lessons"}:
            assert module[key] == m_full[key], key
        assert [x["id"] for x in module["lessons"]] == [x["id"] for x in m_full["lessons"]]
        for les, les_full in zip(module["lessons"], m_full["lessons"], strict=True):
            for key in LESSON_KEYS & set(les_full):
                assert les[key] == les_full[key], key


async def test_lessons_follow_modules_like_detail(client: AsyncClient, db: AsyncSession):
    """Con `course_id` incoerente la lezione segue il suo modulo, come `CourseModule.lessons`
    nel dettaglio: /status e dettaglio hanno le stesse lezioni (niente reload a vuoto)."""
    ctx = await _setup(db)
    other_id, _o, _u = await build_course(db, modules=1, lessons_per_module=1)
    # Lezione di un modulo di questo corso che punta a un altro corso, e lezione di un modulo
    # dell'altro corso che punta a questo (lette entrambe prima di modificarle).
    stray = (
        await db.execute(
            select(CourseLesson).where(
                CourseLesson.course_id == ctx["course_id"], CourseLesson.lesson_code == "M1.L2"
            )
        )
    ).scalar_one()
    intruder = (
        await db.execute(select(CourseLesson).where(CourseLesson.course_id == other_id))
    ).scalar_one()
    stray.course_id = other_id
    intruder.course_id = ctx["course_id"]
    intruder.lesson_code = "X1.L1"  # unicità (course_id, lesson_code)
    await db.commit()

    detail, status = await _get_both(client, ctx["base"], ctx["user"].id)
    assert detail.status_code == status.status_code == 200
    by_module = {m["id"]: [x["id"] for x in m["lessons"]] for m in status.json()["modules"]}
    assert by_module == {m["id"]: [x["id"] for x in m["lessons"]] for m in detail.json()["modules"]}
    all_ids = {lesson_id for module_ids in by_module.values() for lesson_id in module_ids}
    assert str(stray.id) in all_ids
    assert str(intruder.id) not in all_ids


# --- Autorizzazione: identica al dettaglio ----------------------------------------------


async def test_authorization_matches_detail(client: AsyncClient, db: AsyncSession):
    # Manager (course:view_all) su un corso non suo → 200.
    ctx = await _setup(db, role_code=R.MANAGER, assign_to_user=False)
    _assert_same_outcome(*await _get_both(client, ctx["base"], ctx["user"].id), 200)

    # Member assegnatario → 200; member non assegnatario (niente view_all) → 404.
    mine = await _setup(db, role_code=R.MEMBER, assign_to_user=True)
    _assert_same_outcome(*await _get_both(client, mine["base"], mine["user"].id), 200)
    other = await _setup(db, role_code=R.MEMBER, assign_to_user=False)
    detail, status = await _get_both(client, other["base"], other["user"].id)
    _assert_same_outcome(detail, status, 404)
    assert status.json()["code"] == "course_not_found"

    # Corso inesistente o di un'altra organizzazione → 404.
    missing = f"/api/v1/orgs/{ctx['org'].id}/courses/{uuid.uuid4()}"
    _assert_same_outcome(*await _get_both(client, missing, ctx["user"].id), 404)
    foreign = f"/api/v1/orgs/{ctx['org'].id}/courses/{mine['course_id']}"
    _assert_same_outcome(*await _get_both(client, foreign, ctx["user"].id), 404)

    # Utente che non è membro dell'organizzazione → 403.
    _assert_same_outcome(*await _get_both(client, ctx["base"], mine["user"].id), 403)

    # Platform admin: vede tutto; org inesistente → 404 come il dettaglio.
    admin = User(
        email=f"pa-{uuid.uuid4().hex[:8]}@x.it",
        password_hash=hash_password("Password123!"),
        full_name="Admin",
        is_active=True,
        is_platform_admin=True,
    )
    db.add(admin)
    await db.commit()
    _assert_same_outcome(*await _get_both(client, other["base"], admin.id), 200)
    no_org = f"/api/v1/orgs/{uuid.uuid4()}/courses/{ctx['course_id']}"
    detail, status = await _get_both(client, no_org, admin.id)
    _assert_same_outcome(detail, status, 404)
    assert status.json()["code"] == "organization_not_found"

    # Senza autenticazione → 401 come il dettaglio.
    anon_detail = await client.get(ctx["base"])
    anon_status = await client.get(f"{ctx['base']}/status")
    assert anon_status.status_code == anon_detail.status_code == 401


# --- ETag / 304 -------------------------------------------------------------------------


async def test_etag_and_if_none_match(client: AsyncClient, db: AsyncSession):
    ctx = await _setup(db)
    url = f"{ctx['base']}/status"
    headers = _bearer(ctx["user"].id)

    first = await client.get(url, headers=headers)
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert etag == f'"{hashlib.sha256(first.content).hexdigest()}"'

    for value in (etag, f"W/{etag}", "*", f'"abc", {etag}', f'W/"zzz" , W/{etag}'):
        resp = await client.get(url, headers={**headers, "If-None-Match": value})
        assert resp.status_code == 304, value
        assert resp.content == b""
        assert resp.headers["etag"] == etag
        assert resp.headers["cache-control"] == "no-cache"

    stale = await client.get(url, headers={**headers, "If-None-Match": '"abc"'})
    assert stale.status_code == 200
    assert stale.headers["etag"] == etag

    # Cambia solo un avanzamento: nuovo corpo, nuovo ETag, il vecchio non vale più.
    await db.execute(
        update(CourseLesson)
        .where(CourseLesson.course_id == ctx["course_id"])
        .values(content_progress=7)
    )
    await db.commit()
    changed = await client.get(url, headers={**headers, "If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag


# --- Corso grande: dimensione e nessuna colonna pesante -----------------------------------


async def test_large_course_small_body_and_no_heavy_selects(
    client: AsyncClient, db: AsyncSession, _engine
):
    ctx = await _setup(db, modules=8, lessons_per_module=12)
    blob = {"sections": [{"text": "lorem ipsum " * 5000}]}  # ~60 KB per lezione
    await db.execute(
        update(CourseLesson)
        .where(CourseLesson.course_id == ctx["course_id"])
        .values(content_raw=blob, slides_raw=blob, speech_raw=blob)
    )
    await db.commit()

    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement)

    event.listen(_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        resp = await client.get(f"{ctx['base']}/status", headers=_bearer(ctx["user"].id))
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", _capture)

    assert resp.status_code == 200
    body = resp.json()
    assert sum(len(m["lessons"]) for m in body["modules"]) == 96
    assert len(resp.content) <= 200 * 1024, len(resp.content)

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert any("course_lesson" in s for s in selects)
    for stmt in selects:
        lowered = stmt.lower()
        for heavy in _HEAVY_COLUMNS:
            assert heavy not in lowered, (heavy, stmt)
