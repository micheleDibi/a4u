"""Worker e runner dell'estrazione delle figure di fonte (G5, G10, G12).

- runner: ambiente del figlio senza segreti, SIGILL → `engine_unavailable`,
  uscita muta → `crashed`, timeout di blocco con kill del gruppo di processi
  (anche il nipote muore);
- worker: estrazione completa del PDF di prova con il motore euristico e
  uno storage remoto simulato (scarica e carica solo il padre), checkpoint
  e ripresa, politiche del documento, file mancante, crash ripetuto sullo
  stesso blocco (saltato, copertura parziale), rinvio per memoria senza
  consumare tentativi, `FIGURE_EXTRACTION_ENABLED=false` → nessun lavoro;
- isolamento dal riassunto: i campi `summary_*` restano identici.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.services import course_document_figures_worker as worker
from app.services import remote_storage
from app.services.document_figures import runner
from app.services.document_figures import storage as figure_storage
from app.services.openai_client import OpenAINotConfiguredError
from app.services.openai_figure_describe_service import (
    DescribeInput,
    FigureDescription,
    OpenAIFigureDescribeError,
)
from tests.course_builders import build_course, build_course_document
from tests.fixtures.source_figures.build import GROUND_TRUTH, PDF_NAME, build_all

_SECRETS = {
    "OPENAI_API_KEY": "sk-segreto-openai",
    "JWT_SECRET": "segreto-jwt-" + "x" * 40,
    "DATABASE_URL": "postgresql+asyncpg://utente:password-segreta@db/a4u",
    "OVH_SFTP_PASSWORD": "password-sftp-segreta",
    "RUNPOD_API_KEY": "rp-segreto",
}
_ALLOWED_ENV = {
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "TORCH_NUM_THREADS",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "HF_HUB_DISABLE_TELEMETRY",
    "MPLBACKEND",
    "DOCLING_ARTIFACTS_PATH",
}


# --- runner -------------------------------------------------------------------


@pytest.fixture
def fake_child(monkeypatch: pytest.MonkeyPatch):
    """Sostituisce il modulo del figlio con `tests.fake_figure_child`; il
    modo si sceglie chiamando la funzione restituita."""
    original_env = runner.child_env
    state = {"mode": "ok"}

    def env_with_mode(workdir: Path, **kwargs: Any) -> dict[str, str]:
        env = original_env(workdir, **kwargs)
        env["A4U_FAKE_CHILD_MODE"] = state["mode"]
        return env

    monkeypatch.setattr(runner, "CHILD_MODULE", "tests.fake_figure_child")
    monkeypatch.setattr(runner, "child_env", env_with_mode)

    def set_mode(mode: str) -> None:
        state["mode"] = mode

    return set_mode


def _config(**overrides: Any) -> runner.ChildConfig:
    base: dict[str, Any] = {
        "engine": "heuristic",
        "artifacts_path": None,
        "block_timeout_seconds": 60.0,
        "probe_timeout_seconds": 60.0,
    }
    base.update(overrides)
    return runner.ChildConfig(**base)


def test_child_env_is_an_allowlist_without_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in _SECRETS.items():
        monkeypatch.setenv(name, value)
    env = runner.child_env(tmp_path, threads=2, artifacts_path="/opt/docling-models")
    assert set(env) <= _ALLOWED_ENV
    assert env["HOME"] == env["TMPDIR"] == str(tmp_path)
    assert env["OMP_NUM_THREADS"] == "2" and env["HF_HUB_OFFLINE"] == "1"
    joined = "\n".join(env.values())
    for value in _SECRETS.values():
        assert value not in joined


async def test_spawned_child_receives_only_the_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_child: Any
) -> None:
    for name, value in _SECRETS.items():
        monkeypatch.setenv(name, value)
    fake_child("env")
    session = runner.ChildSession(_config(), tmp_path)
    await session._spawn()
    await session._send({"source": "x.pdf", "mime": "application/pdf"})
    event = await session._read_event(time.monotonic() + 30)
    await session.close()
    received = event["env"]
    assert (
        set(received)
        - {"A4U_FAKE_CHILD_MODE", "PWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
        <= _ALLOWED_ENV
    )
    for value in _SECRETS.values():
        assert value not in "\n".join(received.values())


async def test_sigill_is_engine_unavailable(tmp_path: Path, fake_child: Any) -> None:
    fake_child("sigill")
    session = runner.ChildSession(_config(), tmp_path)
    with pytest.raises(runner.ExtractionChildError) as info:
        await session.start(source_name="x.pdf", mime="application/pdf")
    await session.close()
    assert info.value.code == "engine_unavailable"


async def test_silent_exit_is_a_crash(tmp_path: Path, fake_child: Any) -> None:
    fake_child("exit")
    session = runner.ChildSession(_config(), tmp_path)
    with pytest.raises(runner.ExtractionChildError) as info:
        await session.start(source_name="x.pdf", mime="application/pdf")
    await session.close()
    assert info.value.code == "crashed"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_block_timeout_kills_the_whole_process_group(tmp_path: Path, fake_child: Any) -> None:
    fake_child("hang")
    session = runner.ChildSession(_config(block_timeout_seconds=1.0), tmp_path)
    await session.start(source_name="x.pdf", mime="application/pdf")
    grandchild = int((tmp_path / "grandchild.pid").read_text())
    assert _alive(grandchild)
    with pytest.raises(runner.ExtractionChildError) as info:
        await session.run_block(1, 2)
    assert info.value.code == "timeout"
    await session.close()
    for _ in range(50):
        if not _alive(grandchild):
            break
        await asyncio.sleep(0.1)
    assert not _alive(grandchild), "il nipote del figlio è sopravvissuto al kill del gruppo"


def test_child_python_path_is_absolute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`''` nel sys.path del padre (uvicorn) vale la sua cwd: il figlio gira
    in un'altra cartella e deve comunque trovare il pacchetto `app`."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.path", ["", "relativo-inesistente", str(tmp_path)])
    entries = runner.child_python_path().split(os.pathsep)
    assert entries == [str(tmp_path.resolve())]


# --- worker -------------------------------------------------------------------


class FakeStorage:
    """Storage remoto simulato: il worker scarica e carica solo dal padre."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.downloads: list[str] = []

    def upload_bytes(self, key: str, data: bytes) -> None:
        self.files[key] = data

    def download_to(self, key: str, dest_path: Path) -> None:
        self.downloads.append(key)
        if key not in self.files:
            raise remote_storage.StorageFileNotFound(key)
        Path(dest_path).write_bytes(self.files[key])

    def download_bytes(self, key: str) -> bytes:
        return self.files[key]

    def delete(self, key: str) -> None:
        self.files.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self.files if k.startswith(prefix)]:
            del self.files[key]


@pytest.fixture(scope="module")
def fixture_pdf(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    directory = tmp_path_factory.mktemp("worker_fixtures")
    build_all(directory)
    return (directory / PDF_NAME).read_bytes()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


@pytest.fixture
def db(seeded_db: AsyncSession) -> AsyncSession:
    return seeded_db


_BASE_OVERRIDES: dict[str, Any] = {
    "figure_extraction_enabled": True,
    "figure_extraction_engine": "heuristic",
    "figure_extraction_block_pages": 2,
    "figure_extraction_pages_per_child": 2,
}


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch):
    """Impostazioni del worker per il test; `settings(**campi)` le cambia."""

    def apply(**updates: Any) -> None:
        patched = get_settings().model_copy(update={**_BASE_OVERRIDES, **updates})
        monkeypatch.setattr(worker, "get_settings", lambda: patched)

    apply()
    monkeypatch.setattr(worker, "mem_available_mb", lambda: None)
    worker._reset_probe_for_tests()
    return apply


class FakeVision:
    """Vision finta: nessuna chiamata reale dai test; registra le chiamate
    e può fallire su richiesta (`fail` = eccezione da sollevare)."""

    def __init__(self) -> None:
        self.calls: list[DescribeInput] = []
        self.fail: BaseException | None = None
        self.fail_after: int | None = None

    async def __call__(self, item: DescribeInput) -> tuple[FigureDescription, dict[str, Any]]:
        self.calls.append(item)
        if self.fail is not None and (self.fail_after is None or len(self.calls) > self.fail_after):
            raise self.fail
        out = FigureDescription(
            kind="schematic",
            description="Schema di un vibrometro laser Doppler con cella di Bragg.",
            keywords_course=["vibrometro", "laser", "cella di Bragg"],
            keywords_en=["vibrometer", "laser", "Bragg cell"],
            quality_score=4,
            legibility="good",
            is_useful_for_teaching=True,
            reason="ok",
        )
        usage = {
            "model": "gpt-4.1-mini",
            "prompt": 1000,
            "completion": 120,
            "total": 1120,
            "cost_usd": 0.0006,
        }
        return out, usage


@pytest.fixture(autouse=True)
def vision(monkeypatch: pytest.MonkeyPatch) -> FakeVision:
    fake = FakeVision()
    monkeypatch.setattr(worker, "describe_figure", fake)
    return fake


async def _queued_document(
    db: AsyncSession,
    storage: FakeStorage,
    payload: bytes | None,
    *,
    policy: str = "citable",
    mime: str = "application/pdf",
) -> CourseDocument:
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="vibrometria_dispensa.pdf", policy=policy)
    doc.mime_type = mime
    doc.figures_status = "pending"
    doc.figures_requested_at = datetime.now(UTC)
    db.add(doc)
    await db.commit()
    if payload is not None:
        storage.files[remote_storage.uploads_key(doc.file_path)] = payload
    return doc


async def _run(db: AsyncSession, doc_id: uuid.UUID) -> CourseDocument:
    claimed = await worker.claim_next(db)
    assert claimed is not None and claimed.id == doc_id
    assert claimed.figures_status == "processing"
    await worker.process_document(db, claimed)
    fresh = await db.get(CourseDocument, doc_id, populate_existing=True)
    assert fresh is not None
    return fresh


async def _figures(db: AsyncSession, doc_id: uuid.UUID) -> list[CourseDocumentFigure]:
    rows = await db.execute(
        select(CourseDocumentFigure)
        .where(CourseDocumentFigure.document_id == doc_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


def _summary_snapshot(doc: CourseDocument) -> dict[str, Any]:
    return {
        "summary_status": doc.summary_status,
        "summary": doc.summary,
        "summary_error": doc.summary_error,
        "summary_attempts": doc.summary_attempts,
    }


async def test_full_extraction_with_remote_storage(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes
) -> None:
    doc = await _queued_document(db, storage, fixture_pdf)
    before = _summary_snapshot(doc)
    doc = await _run(db, doc.id)

    assert doc.figures_status == "ready", (doc.figures_error_code, doc.figures_error)
    assert doc.figures_error_code is None
    assert doc.figures_count == len(GROUND_TRUTH["pdf"]["figures"])
    assert doc.figures_coverage == "full"
    assert doc.figures_pages_total == 5 and doc.figures_pages_done == 5
    assert doc.figures_engine == "heuristic" and doc.figures_fingerprint
    assert doc.figures_progress["stage"] == "done"
    assert doc.figures_progress["next_page"] == 6
    # Bibliografia deterministica dai metadati del PDF (mai dal modello).
    assert doc.bibliography_source == "pdf_metadata"
    assert doc.bibliography == {"title": "Vibrometria laser", "authors": ["Docente di Prova"]}
    assert _summary_snapshot(doc) == before

    rows = await _figures(db, doc.id)
    kept = sorted((r for r in rows if r.status == "ready"), key=lambda r: r.locator)
    assert [r.page for r in kept] == [1, 2, 3, 4]
    assert [r.source_label for r in kept] == ["Figura 2.1", "Figura 2.2", "Figura 2.3", None]
    for row in kept:
        assert row.license == "unknown" and row.license_source == "document"
        assert row.source_kind == "uploaded" and row.engine == "heuristic"
        assert figure_storage.belongs_to_course(row.storage_path, doc.course_id)
        assert figure_storage.belongs_to_course(row.preview_path, doc.course_id)
        assert remote_storage.uploads_key(row.storage_path) in storage.files
        assert remote_storage.uploads_key(row.preview_path) in storage.files
        assert row.byte_size == len(storage.files[remote_storage.uploads_key(row.storage_path)])
        # Vision (G9): descrizione applicata e costo contabilizzato sulla riga.
        assert row.kind == "schematic" and row.quality_score == 4
        assert row.keywords == {
            "course": ["vibrometro", "laser", "cella di Bragg"],
            "en": ["vibrometer", "laser", "Bragg cell"],
        }
        assert row.vision_usage["calls"] == 1 and row.vision_usage["cost_usd"] == 0.0006
        assert row.vision_usage_at is not None and row.describe_model == "gpt-4.1-mini"
    rejected = [r for r in rows if r.status == "rejected"]
    assert rejected and all(r.storage_path is None for r in rejected)
    assert {r.reject_reason for r in rejected} <= {"too_small", "header_footer", "repeated"}
    # Il padre ha scaricato il documento una volta sola.
    assert storage.downloads == [remote_storage.uploads_key(doc.file_path)]


async def test_rerun_is_idempotent(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes
) -> None:
    doc = await _queued_document(db, storage, fixture_pdf)
    doc = await _run(db, doc.id)
    first = {(r.locator, r.storage_path) for r in await _figures(db, doc.id)}
    doc.figures_status = "pending"
    await db.commit()
    doc = await _run(db, doc.id)
    assert doc.figures_status == "ready"
    assert {(r.locator, r.storage_path) for r in await _figures(db, doc.id)} == first


async def test_resume_from_checkpoint(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes
) -> None:
    doc = await _queued_document(db, storage, fixture_pdf)
    import hashlib

    doc.figures_fingerprint = worker.fingerprint(
        hashlib.sha256(fixture_pdf).hexdigest(), "heuristic"
    )
    doc.figures_progress = {"stage": "extracting", "next_page": 3}
    await db.commit()
    doc = await _run(db, doc.id)
    assert doc.figures_status == "ready"
    pages = {r.page for r in await _figures(db, doc.id)}
    assert pages == {3, 4, 5}


@pytest.mark.parametrize(
    ("policy", "code"), [("excluded", "policy_excluded"), ("content_only", "policy_content_only")]
)
async def test_policy_blocks_extraction(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, policy: str, code: str
) -> None:
    doc = await _queued_document(db, storage, fixture_pdf, policy=policy)
    doc = await _run(db, doc.id)
    assert (doc.figures_status, doc.figures_error_code) == ("skipped", code)
    assert await _figures(db, doc.id) == []
    assert storage.downloads == []


async def test_unsupported_format_is_skipped(db: AsyncSession, storage: FakeStorage) -> None:
    doc = await _queued_document(db, storage, b"testo", mime="text/markdown")
    doc = await _run(db, doc.id)
    assert (doc.figures_status, doc.figures_error_code) == ("skipped", "unsupported_format")


async def test_missing_source_file_fails(db: AsyncSession, storage: FakeStorage) -> None:
    doc = await _queued_document(db, storage, None)
    doc = await _run(db, doc.id)
    assert (doc.figures_status, doc.figures_error_code) == ("failed", "source_missing")


async def test_repeated_crash_on_a_block_skips_it(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, fake_child: Any
) -> None:
    fake_child("crash_block:3")
    doc = await _queued_document(db, storage, fixture_pdf)
    doc = await _run(db, doc.id)
    # Primo crash: si riprova più tardi dallo stesso blocco.
    assert (doc.figures_status, doc.figures_error_code) == ("pending", "crashed")
    assert doc.figures_next_attempt_at is not None
    assert doc.figures_progress["next_page"] == 3, doc.figures_progress
    assert doc.figures_progress["crashes"] == {"3-4": 1}, doc.figures_progress
    doc.figures_next_attempt_at = None
    await db.commit()
    doc = await _run(db, doc.id)
    # Secondo crash sullo stesso blocco: saltato, copertura parziale.
    assert doc.figures_status == "ready"
    assert doc.figures_coverage == "partial"
    assert doc.figures_error_code == "crashed_repeatedly"
    assert doc.figures_progress["skipped_blocks"] == [[3, 4]]
    assert doc.figures_stats["skipped_blocks"] == [[3, 4]]


async def test_low_memory_defers_without_consuming_attempts(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "mem_available_mb", lambda: 100)
    doc = await _queued_document(db, storage, fixture_pdf)
    doc = await _run(db, doc.id)
    assert doc.figures_status == "pending" and doc.figures_error_code is None
    assert doc.figures_attempts == 0
    assert doc.figures_next_attempt_at is not None
    # Oltre FIGURE_EXTRACTION_MAX_DEFER_MINUTES dalla richiesta: fallisce.
    doc.figures_requested_at = datetime.now(UTC) - timedelta(hours=5)
    doc.figures_next_attempt_at = None
    await db.commit()
    doc = await _run(db, doc.id)
    assert (doc.figures_status, doc.figures_error_code) == ("failed", "resources_unavailable")


async def test_docling_without_the_engine_fails_visibly(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any
) -> None:
    try:
        import docling  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("Docling installato: il caso «motore assente» non si riproduce qui")
    settings(figure_extraction_engine="docling")
    doc = await _queued_document(db, storage, fixture_pdf)
    doc = await _run(db, doc.id)
    assert (doc.figures_status, doc.figures_error_code) == ("failed", "engine_unavailable")
    assert await _figures(db, doc.id) == []


async def test_disabled_extraction_starts_no_worker(settings: Any) -> None:
    settings(figure_extraction_enabled=False)
    worker.start_worker()
    assert worker._worker_task is None


async def test_never_requested_documents_are_not_claimed(
    db: AsyncSession, storage: FakeStorage
) -> None:
    """Nessun backfill (G10): un documento con `figures_status` NULL non
    viene mai preso dal worker."""
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="vecchio.pdf")
    db.add(doc)
    await db.commit()
    assert doc.figures_status is None
    claimed = await worker.claim_next(db)
    assert claimed is None or claimed.id != doc.id
    fresh = await db.get(CourseDocument, doc.id, populate_existing=True)
    assert fresh is not None and fresh.figures_status is None


# --- descrizione Vision ---------------------------------------------------------


async def test_vision_receives_caption_context_title_and_language(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, vision: FakeVision
) -> None:
    doc = await _queued_document(db, storage, fixture_pdf)
    await _run(db, doc.id)
    assert len(vision.calls) == 4
    first = next(c for c in vision.calls if (c.caption or "").startswith("Figura 2.1."))
    assert first.context and "fotorivelatore" in first.context
    assert first.document_title == "Vibrometria laser"
    assert first.language_code == "it"
    assert first.image[:4] == b"\x89PNG"


async def test_describe_cap_rejects_the_rest(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any, vision: FakeVision
) -> None:
    settings(figure_describe_max_per_document=2)
    doc = await _queued_document(db, storage, fixture_pdf)
    doc = await _run(db, doc.id)
    rows = await _figures(db, doc.id)
    assert sum(r.status == "ready" for r in rows) == 2
    assert sum(r.reject_reason == "describe_capped" for r in rows) == 2
    assert doc.figures_count == 2 and len(vision.calls) == 2


async def test_descriptions_are_reused_across_courses_of_the_organization(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, vision: FakeVision
) -> None:
    first = await _queued_document(db, storage, fixture_pdf)
    await _run(db, first.id)
    assert len(vision.calls) == 4
    first_course = await db.get(Course, first.course_id)
    second = await _queued_document(db, storage, fixture_pdf)
    second_course = await db.get(Course, second.course_id)
    assert first_course is not None and second_course is not None
    second_course.organization_id = first_course.organization_id
    await db.commit()
    second = await _run(db, second.id)
    assert second.figures_status == "ready" and second.figures_count == 4
    assert len(vision.calls) == 4, "nessuna seconda chiamata Vision"
    reused = [r for r in await _figures(db, second.id) if r.status == "ready"]
    assert all(r.describe_source_id is not None for r in reused)
    assert all(r.vision_usage is None for r in reused)


async def test_descriptions_are_not_reused_across_organizations(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, vision: FakeVision
) -> None:
    first = await _queued_document(db, storage, fixture_pdf)
    await _run(db, first.id)
    second = await _queued_document(db, storage, fixture_pdf)
    await _run(db, second.id)
    assert len(vision.calls) == 8


async def test_missing_openai_key_is_vision_unavailable(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, vision: FakeVision
) -> None:
    vision.fail = OpenAINotConfiguredError()
    doc = await _queued_document(db, storage, fixture_pdf)
    doc = await _run(db, doc.id)
    assert (doc.figures_status, doc.figures_error_code) == ("pending", "vision_unavailable")
    assert doc.figures_next_attempt_at is not None
    rows = await _figures(db, doc.id)
    assert not any(r.status == "ready" for r in rows)
    # Nuovo tentativo con la Vision di nuovo disponibile: niente
    # ri-estrazione, solo le descrizioni.
    vision.fail = None
    vision.calls.clear()
    doc.figures_next_attempt_at = None
    await db.commit()
    doc = await _run(db, doc.id)
    assert doc.figures_status == "ready" and doc.figures_count == 4
    assert len(vision.calls) == 4


async def test_paid_but_unusable_answer_is_accounted(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, vision: FakeVision
) -> None:
    """Una risposta 200 inutilizzabile costa comunque: l'usage finisce in
    `vision_usage` anche se la figura resta non descritta (G9)."""
    vision.fail_after = 3
    vision.fail = OpenAIFigureDescribeError(
        status=200, message="json troncato", usage={"model": "gpt-4.1-mini", "cost_usd": 0.0004}
    )
    doc = await _queued_document(db, storage, fixture_pdf)
    doc = await _run(db, doc.id)
    assert doc.figures_status == "ready" and doc.figures_count == 3
    rows = await _figures(db, doc.id)
    undescribed = [r for r in rows if r.status == "extracted"]
    assert len(undescribed) == 1
    assert undescribed[0].vision_usage["cost_usd"] == 0.0004
    assert undescribed[0].vision_usage["calls"] == 1


async def test_admin_cost_includes_document_figures(db: AsyncSession) -> None:
    from app.services import admin_metrics_service as metrics

    now = datetime.now(UTC)

    async def figures_cost() -> float:
        cost = await metrics._cost(
            db, cutoff_7d=now - timedelta(days=7), cutoff_30d=now - timedelta(days=30)
        )
        return next(p.cost_usd for p in cost.by_phase if p.phase == "document_figures")

    before = await figures_cost()
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="costo.pdf")
    db.add(doc)
    await db.flush()
    from tests.source_figure_builders import build_document_figure

    figure = build_document_figure(course_id, doc.id, license="unknown")
    figure.vision_usage = {"calls": 2, "cost_usd": 0.25}
    figure.vision_usage_at = now
    db.add(figure)
    await db.commit()
    assert round(await figures_cost() - before, 6) == 0.25
