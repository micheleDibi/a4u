"""Ri-ritaglio sul posto delle figure già estratte (doc 18 §22, V2, Q6).

- figlio (`recrop`, senza rilevatore): il ritaglio v1 riprodotto ai dpi
  salvati ha lo stesso phash (identità), poi il ritaglio v2; un phash
  diverso dà identità negata; anche per DOCX;
- servizio (DB e storage simulato): stesso UUID, anche per le figure
  collocate, nessuna Vision, ingressi della risoluzione e `recrop_previous`;
  file v1 conservati; `revert` ripristina e cancella i v2; identità
  negata → la figura resta v1; riga cambiata nel frattempo → nessuna
  sovrascrittura e file v2 cancellati; lease del claim e documenti in
  estrazione esclusi; `_tick` del worker; `purge_replaced` dopo la
  ritenzione.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.services import course_document_figures_worker as worker
from app.services import document_figures_recrop_service as recrop
from app.services import document_figures_service, remote_storage
from app.services.document_figures.child import DOCX_MIME, PDF_MIME
from tests.fixtures.source_figures.build import DOCX_NAME, PDF_NAME, build_all
from tests.source_figure_child import run_child, run_recrop
from tests.test_source_figure_worker import (
    FakeStorage,
    FakeVision,
    _figures,
    _queued_document,
    _run,
    park_queued_documents,
)

# --- figlio ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("recrop_fixtures")
    build_all(directory)
    return directory


def _copy(tmp_path: Path, built: Path, name: str) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    shutil.copyfile(built / name, work / name)
    return work


def _items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": e["locator"],
            "locator": e["locator"],
            "page": e["page"],
            "bbox": e["bbox"],
            "dpi": e["dpi"],
            "phash": e["phash"],
        }
        for e in events
        if e["event"] == "figure" and e["reject_reason"] is None
    ]


def test_child_recrop_reproduces_v1_then_renders_v2(tmp_path: Path, built: Path) -> None:
    work = _copy(tmp_path, built, PDF_NAME)
    code, events, stderr = run_child(
        work, source=PDF_NAME, mime=PDF_MIME, engine="heuristic", blocks=[[1, 5]]
    )
    assert code == 0, stderr[-2000:]
    items = _items(events)
    assert items
    tampered = {**items[0], "key": "tampered", "phash": "f" * 16}
    code, out, stderr = run_recrop(work, source=PDF_NAME, mime=PDF_MIME, figures=[*items, tampered])
    assert code == 0, stderr[-2000:]
    assert out[0]["event"] == "ready" and out[-1]["event"] == "recrop_done"
    by_key = {e["key"]: e for e in out if e["event"] == "recrop"}
    for item in items:
        event = by_key[item["key"]]
        assert event["identity"] == {"distance": 0, "ok": True}, event
        assert event["crop_version"] == 2 and event["crop_mode"] in (
            "raster_native",
            "mixed",
            "vector",
        )
        assert (work / event["file"]).is_file() and event["natural_width_mm"] > 0
    assert by_key["tampered"]["identity"]["ok"] is False


def test_child_recrop_of_office_images(tmp_path: Path, built: Path) -> None:
    work = _copy(tmp_path, built, DOCX_NAME)
    code, events, stderr = run_child(
        work, source=DOCX_NAME, mime=DOCX_MIME, engine="heuristic", blocks=[[1, 1]]
    )
    assert code == 0, stderr[-2000:]
    figures = [e for e in events if e["event"] == "figure" and e["reject_reason"] is None]
    items = [{"key": e["locator"], "locator": e["locator"], "phash": e["phash"]} for e in figures]
    code, out, stderr = run_recrop(work, source=DOCX_NAME, mime=DOCX_MIME, figures=items)
    assert code == 0, stderr[-2000:]
    recropped = [e for e in out if e["event"] == "recrop"]
    assert len(recropped) == len(items)
    for event in recropped:
        assert event["identity"]["ok"] and event["crop_mode"] == "office"


# --- servizio ------------------------------------------------------------------------


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


@pytest.fixture(scope="module")
def fixture_pdf(built: Path) -> bytes:
    return (built / PDF_NAME).read_bytes()


@pytest.fixture(autouse=True)
def vision(monkeypatch: pytest.MonkeyPatch) -> FakeVision:
    fake = FakeVision()
    monkeypatch.setattr(worker, "describe_figure", fake)
    return fake


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    def apply(**updates: Any) -> Any:
        patched = get_settings().model_copy(
            update={
                "figure_extraction_enabled": True,
                "figure_extraction_engine": "heuristic",
                "figure_extraction_block_pages": 5,
                "figure_extraction_pages_per_child": 5,
                **updates,
            }
        )
        monkeypatch.setattr(worker, "get_settings", lambda: patched)
        monkeypatch.setattr(document_figures_service, "get_settings", lambda: patched)
        return patched

    apply()
    monkeypatch.setattr(worker, "mem_available_mb", lambda: None)
    worker._reset_probe_for_tests()
    return apply


@pytest.fixture
async def db(seeded_db: AsyncSession) -> AsyncSession:
    await park_queued_documents(seeded_db)
    # Il claim del ri-ritaglio è globale come quello dell'estrazione.
    await seeded_db.execute(
        update(CourseDocument)
        .where(CourseDocument.figures_recrop_requested_at.is_not(None))
        .values(figures_recrop_requested_at=None)
    )
    await seeded_db.commit()
    return seeded_db


async def _v1_document(db: AsyncSession, storage: FakeStorage, pdf: bytes, settings: Any) -> Any:
    """Documento estratto col ritaglio v1 (come le figure di oggi in
    produzione)."""
    settings(figure_extraction_native_crop_enabled=False)
    doc = await _queued_document(db, storage, pdf)
    doc = await _run(db, doc.id)
    assert doc.figures_status == "ready", (doc.figures_error_code, doc.figures_error)
    settings(figure_extraction_native_crop_enabled=True)
    return doc


def _config() -> Any:
    return worker._config()


async def test_recrop_in_place_keeps_the_uuid_and_is_reversible(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any, vision: FakeVision
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    before = {r.id: r for r in await _figures(db, doc.id) if r.status == "ready"}
    assert before and all(r.crop_version == 1 for r in before.values())
    v1 = {r.id: (r.storage_path, r.preview_path, r.phash, r.description) for r in before.values()}
    placed = next(iter(before))
    # Una figura collocata in una lezione: si ri-ritaglia anche lei (V2).
    lesson = CourseLesson(
        module_id=None,
        course_id=doc.course_id,
        position=9,
        lesson_code="M9.L9",
        title="Lezione",
        summary="S.",
        learning_objectives=[],
        mandatory_topics=[],
        prerequisites=[],
        section_outline=[],
        content_status="ready",
        content_raw={
            "visual_assets": [
                {"asset_id": "SRC-a", "format": "source_figure", "content": str(placed)}
            ]
        },
    )
    calls = len(vision.calls)
    await recrop.request(db, [doc.id])
    claimed = await recrop.claim_next(db)
    assert claimed is not None and claimed.id == doc.id
    stats = await recrop.process(db, claimed, _config())
    assert stats["recropped"] == len(before), stats
    assert len(vision.calls) == calls  # nessuna Vision
    after = {r.id: r for r in await _figures(db, doc.id) if r.status == "ready"}
    assert set(after) == set(before)
    for fid, row in after.items():
        old_path, _old_preview, old_phash, description = v1[fid]
        assert row.crop_version == 2 and row.crop_mode and row.recropped_at is not None
        assert row.recrop_previous["storage_path"] == old_path
        assert row.recrop_previous["phash"] == old_phash
        assert row.description == description
        assert row.storage_path != old_path and row.storage_path.endswith("-v2.png")
        # File v1 conservati (ripristino possibile), v2 caricati.
        assert remote_storage.uploads_key(old_path) in storage.files
        assert remote_storage.uploads_key(row.storage_path) in storage.files
    fresh = await db.get(CourseDocument, doc.id, populate_existing=True)
    assert fresh is not None and fresh.figures_recrop_requested_at is None
    assert fresh.figures_recrop_stats["recropped"] == len(before)
    del lesson

    # Idempotente: un secondo giro non trova figure v1.
    await recrop.request(db, [doc.id])
    again = await recrop.claim_next(db)
    assert again is not None
    assert (await recrop.process(db, again, _config())) == {"figures": 0}

    v2_paths = [r.storage_path for r in after.values()]
    out = await recrop.revert(db, course_id=doc.course_id)
    assert out == {"reverted": len(before)}
    reverted = {r.id: r for r in await _figures(db, doc.id) if r.status == "ready"}
    for fid, row in reverted.items():
        assert (row.storage_path, row.preview_path, row.phash) == v1[fid][:3]
        assert row.crop_version == 1 and row.recrop_previous is None
    assert all(remote_storage.uploads_key(p) not in storage.files for p in v2_paths)


async def test_identity_mismatch_leaves_the_figure_v1(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    rows = [r for r in await _figures(db, doc.id) if r.status == "ready"]
    rows[0].phash = "f" * 16
    await db.commit()
    await recrop.request(db, [doc.id])
    claimed = await recrop.claim_next(db)
    assert claimed is not None
    stats = await recrop.process(db, claimed, _config())
    assert stats["identity_mismatch"] == 1
    assert stats["recropped"] == len(rows) - 1
    fresh = {r.id: r for r in await _figures(db, doc.id)}
    assert fresh[rows[0].id].crop_version == 1


async def test_a_row_changed_meanwhile_is_not_overwritten(
    db: AsyncSession,
    storage: FakeStorage,
    fixture_pdf: bytes,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    # Bersaglio deterministico (primo locator) e percorso cambiato qualunque
    # sia l'estensione (il fixture v1 ha anche un JPEG).
    ready = sorted(
        (r for r in await _figures(db, doc.id) if r.status == "ready"), key=lambda r: r.locator
    )
    target = ready[0]
    stem, ext = str(target.storage_path).rsplit(".", 1)
    changed_path = f"{stem}-x.{ext}"
    original = recrop.run_child

    async def child_then_change(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        await db.execute(
            update(type(target))
            .where(type(target).id == target.id)
            .values(storage_path=changed_path)
        )
        await db.commit()
        return result

    monkeypatch.setattr(recrop, "run_child", child_then_change)
    await recrop.request(db, [doc.id])
    claimed = await recrop.claim_next(db)
    assert claimed is not None
    uploads = set(storage.files)
    stats = await recrop.process(db, claimed, _config())
    assert stats["changed"] == 1
    fresh = next(r for r in await _figures(db, doc.id) if r.id == target.id)
    assert fresh.crop_version == 1 and fresh.recrop_previous is None
    assert fresh.storage_path == changed_path
    # I file v2 della riga cambiata non restano orfani nello storage.
    new_files = set(storage.files) - uploads
    assert all(target.locator not in key for key in new_files)


async def test_claim_uses_a_lease_and_skips_documents_in_extraction(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    busy = await _queued_document(db, storage, fixture_pdf, course_id=doc.course_id)
    await recrop.request(db, [doc.id, busy.id])
    assert await recrop.busy_documents(db, doc.course_id) == 2
    claimed = await recrop.claim_next(db)
    assert claimed is not None and claimed.id == doc.id
    assert claimed.figures_recrop_stats["attempts"] == 1
    # Lease: non si riprende subito; il documento in estrazione mai.
    assert await recrop.claim_next(db) is None
    await db.execute(
        update(CourseDocument)
        .where(CourseDocument.id == doc.id)
        .values(figures_recrop_requested_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    again = await recrop.claim_next(db)
    assert again is not None and again.id == doc.id
    assert again.figures_recrop_stats["attempts"] == 2


async def test_worker_tick_recrops_when_no_extraction_is_queued(
    db: AsyncSession,
    storage: FakeStorage,
    fixture_pdf: bytes,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    _engine: Any,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    doc = await _v1_document(db, storage, fixture_pdf, settings)
    monkeypatch.setattr(
        worker, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    await recrop.request(db, [doc.id])
    await worker._tick()
    rows = [r for r in await _figures(db, doc.id) if r.status == "ready"]
    assert rows and all(r.crop_version == 2 for r in rows)


async def test_purge_replaced_after_the_retention(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    await recrop.request(db, [doc.id])
    claimed = await recrop.claim_next(db)
    assert claimed is not None
    await recrop.process(db, claimed, _config())
    rows = [r for r in await _figures(db, doc.id) if r.status == "ready"]
    old_paths = [r.recrop_previous["storage_path"] for r in rows]
    retention = timedelta(days=recrop.RETENTION_DAYS)
    # Dentro la ritenzione: niente da cancellare.
    early = await recrop.purge_replaced(
        db, course_id=doc.course_id, older_than=retention, apply=True
    )
    assert early == {"figures": 0, "files": 0}
    await db.execute(
        update(type(rows[0]))
        .where(type(rows[0]).document_id == doc.id)
        .values(recropped_at=datetime.now(UTC) - retention - timedelta(days=1))
    )
    await db.commit()
    dry = await recrop.purge_replaced(
        db, course_id=doc.course_id, older_than=retention, apply=False
    )
    assert dry == {"figures": len(rows), "files": 2 * len(rows)}
    assert all(remote_storage.uploads_key(p) in storage.files for p in old_paths)
    done = await recrop.purge_replaced(
        db, course_id=doc.course_id, older_than=retention, apply=True
    )
    assert done == dry
    assert all(remote_storage.uploads_key(p) not in storage.files for p in old_paths)
    # Dopo il purge il ripristino non è più possibile.
    assert await recrop.revert(db, course_id=doc.course_id) == {"reverted": 0}


def test_eligible_rows_need_the_v1_inputs() -> None:
    from types import SimpleNamespace

    base = {
        "id": uuid.uuid4(),
        "locator": "p0001-f01",
        "phash": "0" * 16,
        "page": 1,
        "bbox": {"l": 1, "t": 1, "r": 50, "b": 50, "page_w": 595, "page_h": 842},
        "dpi": 150,
    }
    assert recrop.child_item(SimpleNamespace(**base)) is not None  # type: ignore[arg-type]
    assert recrop.child_item(SimpleNamespace(**{**base, "dpi": None})) is None  # type: ignore[arg-type]
    office = recrop.child_item(SimpleNamespace(**{**base, "page": None, "bbox": None}))  # type: ignore[arg-type]
    assert office is not None and "bbox" not in office


# --- script --------------------------------------------------------------------------


class _Engine:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch, _engine: Any, settings: Any):
    """`scripts.rerender_document_figures.run` sul DB dei test; `script(args,
    **setting)` lo esegue con le impostazioni date."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.core.config as config_module
    import app.db.session as session_module
    from scripts import rerender_document_figures as module

    monkeypatch.setattr(
        session_module, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    monkeypatch.setattr(session_module, "engine", _Engine())

    async def run(argv: list[str], **updates: Any) -> int:
        patched = settings(**updates)
        monkeypatch.setattr(config_module, "get_settings", lambda: patched)
        return await module.run(module.build_parser().parse_args(argv))

    return run


async def test_script_dry_run_measure_and_refusals(
    db: AsyncSession,
    storage: FakeStorage,
    fixture_pdf: bytes,
    settings: Any,
    script: Any,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    doc = await _v1_document(db, storage, fixture_pdf, settings)
    course = str(doc.course_id)
    ready = [r for r in await _figures(db, doc.id) if r.status == "ready"]
    assert await script(["--course", course]) == 0
    out = capsys.readouterr().out
    assert f"figure v1 da ri-ritagliare: {len(ready)}" in out and "dry-run" in out

    report = tmp_path / "misura.json"
    assert await script(["--course", course, "--measure", "--report", str(report)]) == 0
    summary = json.loads(report.read_text(encoding="utf-8"))["summary"]
    assert summary["rerendered"] == len(ready) and summary["identity_share"] == 1.0
    assert summary["jpeg_v2"] == 0 and summary["classes_c"]
    assert all("→" in key for key in summary["classes_a_b_c"])
    # La misura non scrive nulla.
    assert all(r.crop_version == 1 for r in await _figures(db, doc.id) if r.status == "ready")

    code = await script(["--course", course, "--apply"], figure_resolution_rules_enabled=False)
    assert code == 2 and "FIGURE_RESOLUTION_RULES_ENABLED" in capsys.readouterr().err
    code = await script(["--course", course, "--apply"], figure_extraction_enabled=False)
    assert code == 2 and "--inline" in capsys.readouterr().err
    busy = await _queued_document(db, storage, fixture_pdf, course_id=doc.course_id)
    assert await script(["--course", course, "--apply"]) == 2
    assert "in corso" in capsys.readouterr().err
    await db.execute(
        update(CourseDocument).where(CourseDocument.id == busy.id).values(figures_status="ready")
    )
    await db.commit()

    assert await script(["--course", course, "--apply"]) == 0
    queued = await db.get(CourseDocument, doc.id, populate_existing=True)
    assert queued is not None and queued.figures_recrop_requested_at is not None
    # Con un ri-ritaglio già accodato anche --inline rifiuta.
    assert await script(["--course", course, "--inline"]) == 2


async def test_script_inline_revert_and_purge(
    db: AsyncSession,
    storage: FakeStorage,
    fixture_pdf: bytes,
    settings: Any,
    script: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    course = str(doc.course_id)
    assert await script(["--course", course, "--inline"]) == 0
    rows = [r for r in await _figures(db, doc.id) if r.status == "ready"]
    assert rows and all(r.crop_version == 2 for r in rows)
    assert await script(["--purge-replaced", "--older-than", "14", "--course", course]) == 0
    assert "file v1 da cancellare: 0" in capsys.readouterr().out
    assert await script(["--course", course, "--revert"]) == 0
    assert f"ripristinate al ritaglio v1: {len(rows)}" in capsys.readouterr().out
    rows = [r for r in await _figures(db, doc.id) if r.status == "ready"]
    assert all(r.crop_version == 1 for r in rows)


# --- rilievi della verifica WP2 -----------------------------------------------------


async def test_a_second_recrop_of_the_same_snapshot_keeps_the_files_in_use(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any, tmp_path: Path
) -> None:
    """Due giri sullo stesso documento producono gli stessi nomi v2: quello
    che perde l'UPDATE condizionale non cancella i file della riga."""
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    rows = await recrop.eligible_rows(db, doc.id)
    workdir = tmp_path / "recrop"
    workdir.mkdir()
    result = await recrop.run_child(doc, rows, workdir, _config())
    first = await recrop.apply_result(db, doc, result)
    assert first["recropped"] == len(rows)
    second = await recrop.apply_result(db, doc, result)
    assert second["changed"] == len(rows) and second["recropped"] == 0
    for row in await _figures(db, doc.id):
        if row.status == "ready":
            assert remote_storage.uploads_key(row.storage_path) in storage.files
            assert remote_storage.uploads_key(row.preview_path) in storage.files


@pytest.mark.parametrize(
    "switch", ["figure_extraction_native_crop_enabled", "figure_resolution_rules_enabled"]
)
async def test_switches_off_leave_the_recrop_queue_untouched(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any, switch: str
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    await recrop.request(db, [doc.id])
    settings(**{switch: False})
    await worker._recrop_tick(db)
    fresh = await db.get(CourseDocument, doc.id, populate_existing=True)
    assert fresh is not None and fresh.figures_recrop_requested_at is not None
    assert "attempts" not in (fresh.figures_recrop_stats or {})
    assert all(r.crop_version == 1 for r in await _figures(db, doc.id))


async def test_a_storage_error_while_applying_counts_as_an_attempt(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    doc_id = doc.id  # dopo il rollback del servizio l'oggetto è scaduto
    await recrop.request(db, [doc_id])
    storage.fail_upload_after = storage.uploads  # il prossimo caricamento fallisce
    for attempt in range(1, recrop.RECROP_ATTEMPTS_MAX + 1):
        await db.execute(
            update(CourseDocument)
            .where(CourseDocument.id == doc_id)
            .values(figures_recrop_requested_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
        claimed = await recrop.claim(db, doc_id)
        assert claimed is not None
        out = await recrop.process(db, claimed, _config())
        if attempt < recrop.RECROP_ATTEMPTS_MAX:
            assert out == {"error": "apply_failed", "retry": True}
    assert out["error"] == "attempts_exhausted"
    fresh = await db.get(CourseDocument, doc_id, populate_existing=True)
    assert fresh is not None and fresh.figures_recrop_requested_at is None


async def test_revert_writes_sql_null(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any
) -> None:
    from sqlalchemy import func, select

    from app.models.course_document_figure import CourseDocumentFigure

    doc = await _v1_document(db, storage, fixture_pdf, settings)
    await recrop.request(db, [doc.id])
    claimed = await recrop.claim(db, doc.id)
    assert claimed is not None
    await recrop.process(db, claimed, _config())
    await recrop.revert(db, course_id=doc.course_id)
    left = await db.scalar(
        select(func.count(CourseDocumentFigure.id)).where(
            CourseDocumentFigure.document_id == doc.id,
            CourseDocumentFigure.recrop_previous.is_not(None),
        )
    )
    assert left == 0


def test_figure_file_paths_include_the_v1_files_of_the_same_course() -> None:
    from types import SimpleNamespace

    course = uuid.uuid4()
    doc = uuid.uuid4()
    base = f"/uploads/courses/{course}/document_figures/{doc}"
    row = SimpleNamespace(
        course_id=course,
        storage_path=f"{base}/p0001-f01-aaaa-v2.png",
        preview_path=f"{base}/p0001-f01-aaaa-v2-preview.jpg",
        recrop_previous={
            "storage_path": f"{base}/p0001-f01-bbbb.jpg",
            "preview_path": f"/uploads/courses/{uuid.uuid4()}/document_figures/{doc}/x.jpg",
        },
    )
    paths = document_figures_service.figure_file_paths(row)  # type: ignore[arg-type]
    assert paths == [row.storage_path, row.preview_path, f"{base}/p0001-f01-bbbb.jpg"]


async def test_script_refuses_with_the_native_crop_switch_off(
    db: AsyncSession,
    storage: FakeStorage,
    fixture_pdf: bytes,
    settings: Any,
    script: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    for mode in ("--apply", "--inline"):
        code = await script(
            ["--course", str(doc.course_id), mode], figure_extraction_native_crop_enabled=False
        )
        assert code == 2
        assert "FIGURE_EXTRACTION_NATIVE_CROP_ENABLED" in capsys.readouterr().err


async def test_a_figure_that_v2_would_make_unusable_stays_v1(
    db: AsyncSession, storage: FakeStorage, tmp_path: Path
) -> None:
    """Rilievo della verifica WP2: una figura mista col raster sotto il
    minimo non si sostituisce (in dispensa uscirebbe di pochi millimetri)."""
    import io as _io

    from PIL import Image as _Image

    from tests.course_builders import build_course, build_course_document
    from tests.source_figure_builders import build_document_figure

    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="ldv.pdf")
    db.add(doc)
    await db.flush()
    rows = [
        build_document_figure(course_id, doc.id, license="cc_by", locator=f"p0001-f0{i}")
        for i in (1, 2)
    ]
    db.add_all(rows)
    await db.commit()
    buf = _io.BytesIO()
    _Image.new("RGB", (120, 90), "white").save(buf, format="PNG")
    (tmp_path / "r.png").write_bytes(buf.getvalue())
    (tmp_path / "r-preview.jpg").write_bytes(b"\xff\xd8")
    base = {
        "identity": {"ok": True, "distance": 0},
        "file": "r.png",
        "preview_file": "r-preview.jpg",
        "mime": "image/png",
        "height": 90,
        "is_vector": False,
        "phash": "0" * 16,
        "crop_mode": "mixed",
        "natural_width_mm": 50.8,
    }
    events = {
        # 120 px a 360 dpi di un raster a 60 ppi: 20 px d'informazione.
        rows[0].id.hex: {
            **base,
            "key": rows[0].id.hex,
            "width": 120,
            "dpi": 360,
            "native_ppi": 60.0,
        },
        rows[1].id.hex: {
            **base,
            "key": rows[1].id.hex,
            "width": 1200,
            "dpi": 600,
            "native_ppi": 600.0,
        },
    }
    result = recrop.RecropResult(
        snapshots=[{"id": r.id, "locator": r.locator, **recrop.previous_of(r)} for r in rows],
        events=events,
        workdir=tmp_path,
    )
    stats = await recrop.apply_result(db, doc, result)
    assert stats["kept_unusable"] == 1 and stats["recropped"] == 1
    fresh = {r.id: r for r in await _figures(db, doc.id)}
    assert fresh[rows[0].id].crop_version == 1 and fresh[rows[1].id].crop_version == 2


async def test_a_crashing_request_is_closed_after_the_attempts(
    db: AsyncSession, storage: FakeStorage, fixture_pdf: bytes, settings: Any
) -> None:
    """Un processo che muore a ogni giro (niente eccezione da contare) non
    fa riprendere la richiesta all'infinito: il claim la chiude."""
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    await recrop.request(db, [doc.id])
    await db.execute(
        update(CourseDocument)
        .where(CourseDocument.id == doc.id)
        .values(figures_recrop_stats={"attempts": recrop.RECROP_ATTEMPTS_MAX})
    )
    await db.commit()
    assert await recrop.claim_next(db) is None
    fresh = await db.get(CourseDocument, doc.id, populate_existing=True)
    assert fresh is not None and fresh.figures_recrop_requested_at is None
    assert fresh.figures_recrop_stats["error"] == "attempts_exhausted"


async def test_script_refuses_to_revert_while_a_recrop_is_queued(
    db: AsyncSession,
    storage: FakeStorage,
    fixture_pdf: bytes,
    settings: Any,
    script: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    doc = await _v1_document(db, storage, fixture_pdf, settings)
    await recrop.request(db, [doc.id])
    assert await script(["--course", str(doc.course_id), "--revert"]) == 2
    assert "in corso" in capsys.readouterr().err


def test_child_recrops_a_superseded_office_row(tmp_path: Path, built: Path) -> None:
    work = _copy(tmp_path, built, DOCX_NAME)
    code, events, stderr = run_child(
        work, source=DOCX_NAME, mime=DOCX_MIME, engine="heuristic", blocks=[[1, 1]]
    )
    assert code == 0, stderr[-2000:]
    first = next(e for e in events if e["event"] == "figure" and e["reject_reason"] is None)
    item = {
        "key": "old",
        "locator": f"old-0123456789ab-{first['locator']}",
        "phash": first["phash"],
    }
    code, out, stderr = run_recrop(work, source=DOCX_NAME, mime=DOCX_MIME, figures=[item])
    assert code == 0, stderr[-2000:]
    (event,) = [e for e in out if e["event"] == "recrop"]
    assert event.get("error") is None and event["identity"]["ok"], event
