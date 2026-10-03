"""Prestazioni L1 — W4: ticker di avanzamento realistici.

I ticker degli 8 worker con `_progress_ticker` passano da
`progress_ticker.run_progress_ticker`: la curva copre `CURVE_SHARE` dello
span in `duration_sec`, poi il valore sale di almeno un punto ogni
`MAX_STALL_SEC` fino a `end_pct - 1`; le scritture sono `UPDATE`
condizionate senza leggere la riga. Qui il clock è finto (nessuna attesa
reale) e il DB è quello dei test.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.services import (
    course_architecture_worker,
    course_lesson_content_worker,
    course_lesson_pdf_worker,
    course_lesson_slides_pdf_worker,
    course_lesson_slides_worker,
    course_lesson_speech_pdf_worker,
    course_lesson_speech_worker,
    course_lesson_structure_worker,
    progress_ticker,
)
from tests.course_builders import build_course

# ---------------------------------------------------------------------------
# Funzioni pure
# ---------------------------------------------------------------------------


def _simulate(
    *, start: int, end: int, duration: float, tick: float, horizon: float
) -> list[tuple[float, int]]:
    """`(istante, valore)` di ogni salita, come la produce il ticker."""
    last, last_bump, t = start, 0.0, 0.0
    out: list[tuple[float, int]] = []
    while last < end - 1 and t < horizon:
        t += tick
        target = progress_ticker.next_target(
            elapsed=t,
            last=last,
            last_bump=last_bump,
            tick_sec=tick,
            start_pct=start,
            end_pct=end,
            duration_sec=duration,
        )
        if target > last:
            out.append((t, target))
            last, last_bump = target, t
    return out


@pytest.mark.parametrize(
    ("start", "end", "duration", "tick"),
    [
        (15, 85, 150.0, 3.0),  # dispense
        (15, 85, 100.0, 2.0),  # struttura
        (15, 85, 110.0, 2.0),  # architettura
        (15, 85, 60.0, 3.0),  # slide, discorso
        (10, 85, 20.0, 2.0),  # PDF dispensa e slide
        (10, 85, 15.0, 2.0),  # PDF discorso
    ],
)
def test_progress_never_stalls_more_than_ten_seconds_until_the_cap(
    start: int, end: int, duration: float, tick: float
) -> None:
    steps = _simulate(start=start, end=end, duration=duration, tick=tick, horizon=3600.0)
    values = [v for _t, v in steps]
    times = [0.0] + [t for t, _v in steps]
    assert values == sorted(set(values))  # strettamente crescente
    assert values[-1] == end - 1  # tetto: il valore finale lo scrive il worker
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert max(gaps) <= progress_ticker.MAX_STALL_SEC, gaps


def test_the_curve_covers_the_share_of_the_span_in_duration() -> None:
    """Dispense (15→85, 150 s): a p50 (100 s) siamo a 68, alla fine della
    curva a 74, poi la coda porta a 84."""
    curve = progress_ticker.curve_target
    assert curve(0.0, start_pct=15, end_pct=85, duration_sec=150.0) == 15
    assert curve(100.0, start_pct=15, end_pct=85, duration_sec=150.0) == 67
    assert curve(150.0, start_pct=15, end_pct=85, duration_sec=150.0) == 74
    assert curve(900.0, start_pct=15, end_pct=85, duration_sec=150.0) == 74
    steps = _simulate(start=15, end=85, duration=150.0, tick=3.0, horizon=3600.0)
    reached_cap = steps[-1][0]
    assert 200.0 < reached_cap <= 150.0 + 10 * progress_ticker.MAX_STALL_SEC


def test_next_target_never_goes_down_nor_over_the_cap() -> None:
    kwargs: dict[str, Any] = {"tick_sec": 2.0, "start_pct": 10, "end_pct": 85}
    # Un valore più alto già scritto (dal worker) resta: la curva non scende.
    assert (
        progress_ticker.next_target(
            elapsed=4.0, last=60, last_bump=4.0, duration_sec=20.0, **kwargs
        )
        == 60
    )
    assert (
        progress_ticker.next_target(
            elapsed=500.0, last=84, last_bump=0.0, duration_sec=20.0, **kwargs
        )
        == 84
    )


# ---------------------------------------------------------------------------
# Ticker reali sul DB di test, con clock finto
# ---------------------------------------------------------------------------


@dataclass
class _Case:
    module: ModuleType
    model: Any
    status_attr: str
    active: str
    progress_attr: str
    start: int
    duration: float


_CASES = {
    "architecture": _Case(
        course_architecture_worker,
        Course,
        "status",
        "architecture_pending",
        "architecture_progress",
        15,
        110.0,
    ),
    "structure": _Case(
        course_lesson_structure_worker,
        CourseModule,
        "lessons_structure_status",
        "processing",
        "lessons_structure_progress",
        15,
        100.0,
    ),
    "content": _Case(
        course_lesson_content_worker,
        CourseLesson,
        "content_status",
        "processing",
        "content_progress",
        15,
        150.0,
    ),
    "slides": _Case(
        course_lesson_slides_worker,
        CourseLesson,
        "slides_status",
        "processing",
        "slides_progress",
        15,
        60.0,
    ),
    "speech": _Case(
        course_lesson_speech_worker,
        CourseLesson,
        "speech_status",
        "processing",
        "speech_progress",
        15,
        60.0,
    ),
    "pdf": _Case(
        course_lesson_pdf_worker, CourseLesson, "pdf_status", "processing", "pdf_progress", 10, 20.0
    ),
    "slides_pdf": _Case(
        course_lesson_slides_pdf_worker,
        CourseLesson,
        "slides_pdf_status",
        "processing",
        "slides_pdf_progress",
        10,
        20.0,
    ),
    "speech_pdf": _Case(
        course_lesson_speech_pdf_worker,
        CourseLesson,
        "speech_pdf_status",
        "processing",
        "speech_pdf_progress",
        10,
        15.0,
    ),
}


class _FakeClock:
    """Clock finto per il ticker: `sleep` avanza il tempo senza attendere e
    lascia girare il loop; `hooks` sono azioni da eseguire a un istante."""

    def __init__(self) -> None:
        self.now = 0.0
        self.hooks: list[tuple[float, Any]] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, sec: float) -> None:
        self.now += sec
        await asyncio.sleep(0)
        due = [h for h in self.hooks if h[0] <= self.now]
        self.hooks = [h for h in self.hooks if h[0] > self.now]
        for _at, action in due:
            await action()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(progress_ticker, "_monotonic", fake.monotonic)
    monkeypatch.setattr(progress_ticker, "_sleep", fake.sleep)
    return fake


@pytest.fixture
def factory(_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> async_sessionmaker:
    session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    for case in _CASES.values():
        monkeypatch.setattr(case.module, "async_session_factory", session_factory)
    return session_factory


class _Sql:
    """Statement SELECT/UPDATE eseguiti sull'engine, con l'istante finto."""

    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.statements: list[tuple[float, str, Any]] = []

    def __call__(self, conn: Any, cursor: Any, statement: str, params: Any, *a: Any) -> None:
        head = statement.lstrip().upper()
        if head.startswith(("SELECT", "UPDATE")):
            self.statements.append((self.clock.now, statement, params))


@pytest.fixture
def sql(_engine: AsyncEngine, clock: _FakeClock) -> Iterator[_Sql]:
    rec = _Sql(clock)
    event.listen(_engine.sync_engine, "before_cursor_execute", rec)
    yield rec
    event.remove(_engine.sync_engine, "before_cursor_execute", rec)


# Righe portate in uno status attivo da `_row_id`, con lo status di partenza:
# il DB dei test è di sessione e i worker di altri test prenderebbero in
# carico una riga lasciata `processing`/`architecture_pending`.
_ACTIVE_ROWS: list[tuple[AsyncEngine, Any, uuid.UUID, str, Any]] = []


@pytest.fixture(autouse=True)
async def _restore_active_rows() -> AsyncIterator[None]:
    """A fine test riporta allo status di partenza le righe di `_row_id`."""
    yield
    rows = list(_ACTIVE_ROWS)
    _ACTIVE_ROWS.clear()
    for engine, model, row_id, attr, original in rows:
        async with async_sessionmaker(engine)() as s:
            await s.execute(
                update(model).where(model.id == row_id).values({getattr(model, attr): original})
            )
            await s.commit()


async def _row_id(db: AsyncSession, case: _Case) -> uuid.UUID:
    """Una riga nello status attivo, con il progress di partenza (ripristinata
    a fine test da `_restore_active_rows`)."""
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    if case.model is Course:
        row_id = course_id
    elif case.model is CourseModule:
        row_id = await db.scalar(select(CourseModule.id).where(CourseModule.course_id == course_id))
    else:
        row_id = await db.scalar(select(CourseLesson.id).where(CourseLesson.course_id == course_id))
    status_col = getattr(case.model, case.status_attr)
    original = await db.scalar(select(status_col).where(case.model.id == row_id))
    assert row_id is not None and isinstance(db.bind, AsyncEngine)
    _ACTIVE_ROWS.append((db.bind, case.model, row_id, case.status_attr, original))
    await db.execute(
        update(case.model)
        .where(case.model.id == row_id)
        .values(
            {
                getattr(case.model, case.status_attr): case.active,
                getattr(case.model, case.progress_attr): case.start,
            }
        )
    )
    await db.commit()
    assert row_id is not None
    return row_id


async def _read(factory: async_sessionmaker, case: _Case, row_id: uuid.UUID, attr: str) -> Any:
    async with factory() as s:
        return await s.scalar(select(getattr(case.model, attr)).where(case.model.id == row_id))


@pytest.mark.parametrize("name", list(_CASES))
async def test_ticker_climbs_to_the_cap_with_conditional_updates_only(
    name: str,
    seeded_db: AsyncSession,
    factory: async_sessionmaker,
    clock: _FakeClock,
    sql: _Sql,
) -> None:
    case = _CASES[name]
    row_id = await _row_id(seeded_db, case)
    before = await _read(factory, case, row_id, "updated_at")
    sql.statements.clear()

    await case.module._progress_ticker(
        row_id, start_pct=case.start, end_pct=85, duration_sec=case.duration
    )
    statements = list(sql.statements)  # prima delle letture di verifica

    assert await _read(factory, case, row_id, case.progress_attr) == 84
    # `updated_at` (onupdate della colonna) avanza come con l'ORM di prima.
    assert await _read(factory, case, row_id, "updated_at") > before
    table = case.model.__tablename__
    assert statements, "nessuna scrittura"
    for _t, statement, _params in statements:
        # Nessuna lettura della riga: solo UPDATE condizionate.
        assert statement.lstrip().upper().startswith(f"UPDATE {table.upper()} SET"), statement
        assert f"{table}.{case.status_attr} =" in statement, statement
        assert f"{table}.{case.progress_attr} <" in statement, statement
    times = [0.0] + [t for t, _s, _p in statements]
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert max(gaps) <= progress_ticker.MAX_STALL_SEC, gaps


async def test_ticker_stops_when_the_status_changes(
    seeded_db: AsyncSession, factory: async_sessionmaker, clock: _FakeClock, sql: _Sql
) -> None:
    case = _CASES["content"]
    row_id = await _row_id(seeded_db, case)

    async def finish() -> None:
        async with factory() as s:
            await s.execute(
                update(CourseLesson)
                .where(CourseLesson.id == row_id)
                .values(content_status="ready", content_progress=100)
            )
            await s.commit()

    clock.hooks.append((30.0, finish))
    sql.statements.clear()

    await case.module._progress_ticker(row_id, start_pct=15, end_pct=85, duration_sec=150.0)
    statements = list(sql.statements)  # prima delle letture di verifica

    # Il job è finito a 30 s: il ticker esce al primo tick dopo, senza
    # toccare il valore scritto dal worker.
    assert clock.now <= 30.0 + 3.0
    assert await _read(factory, case, row_id, "content_progress") == 100
    selects = [s for _t, s, _p in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1
    assert selects[0].lstrip().startswith("SELECT course_lesson.content_status \nFROM")


async def test_ticker_does_not_lower_a_value_written_by_the_worker(
    seeded_db: AsyncSession, factory: async_sessionmaker, clock: _FakeClock
) -> None:
    case = _CASES["content"]
    row_id = await _row_id(seeded_db, case)
    seen: list[int] = []

    async def worker_writes() -> None:
        async with factory() as s:
            await s.execute(
                update(CourseLesson).where(CourseLesson.id == row_id).values(content_progress=80)
            )
            await s.commit()

    async def observe() -> None:
        seen.append(await _read(factory, case, row_id, "content_progress"))

    clock.hooks.append((12.0, worker_writes))
    clock.hooks.extend((t, observe) for t in range(13, 400, 3))

    await case.module._progress_ticker(row_id, start_pct=15, end_pct=85, duration_sec=150.0)

    assert seen[0] == 80 and seen == sorted(seen)
    assert await _read(factory, case, row_id, "content_progress") == 84


async def test_ticker_returns_when_the_row_is_gone(
    factory: async_sessionmaker, clock: _FakeClock, sql: _Sql
) -> None:
    await course_lesson_pdf_worker._progress_ticker(
        uuid.uuid4(), start_pct=10, end_pct=85, duration_sec=20.0
    )
    assert clock.now == 2.0  # un solo tick
    kinds = [s.lstrip().split()[0].upper() for _t, s, _p in sql.statements]
    assert kinds == ["UPDATE", "SELECT"]


async def test_cancelling_the_ticker_ends_it_quietly(
    seeded_db: AsyncSession, factory: async_sessionmaker
) -> None:
    """Il worker cancella il task a fine chiamata: nessuna eccezione."""
    case = _CASES["slides"]
    row_id = await _row_id(seeded_db, case)
    task = asyncio.create_task(
        case.module._progress_ticker(row_id, start_pct=15, end_pct=85, duration_sec=60.0)
    )
    await asyncio.sleep(0)
    task.cancel()
    assert await task is None
