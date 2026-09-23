"""Ciclo di vita delle figure di fonte dei documenti (WP2d, G8 e G10).

- Richiesta di estrazione via API: 202, idempotente, permesso
  `course:generate`, campi `figures_*` e di provenienza nell'output;
  documenti non estraibili → `skipped` con il motivo.
- Cambio di politica verso `citable`: si torna in coda solo se il documento
  era stato saltato per la politica.
- Cancellazione del documento (U1, non retroattiva): figure usate staccate
  con l'attribuzione congelata IDENTICA a prima, figure non usate cancellate
  con i file; `--gc-detached` idempotente.
- Nessun backfill: lo script senza `--apply` non scrive nulla.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.permissions import R
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.services import course_service, document_figures_service, remote_storage
from app.services.figure_attribution import figure_attribution_line
from tests.course_builders import build_course, build_course_document
from tests.source_figure_builders import build_document_figure
from tests.test_admin_user_management import _bearer
from tests.test_permissions import _setup_user_membership

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class _Storage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    def delete_prefix(self, prefix: str) -> None:
        self.deleted.append(prefix + "/")


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    fake = _Storage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def extraction_enabled(monkeypatch: pytest.MonkeyPatch):
    def apply(enabled: bool) -> None:
        patched = get_settings().model_copy(update={"figure_extraction_enabled": enabled})
        monkeypatch.setattr(document_figures_service, "get_settings", lambda: patched)

    apply(True)
    return apply


# --- API ------------------------------------------------------------------------


async def _course_with_member(db: AsyncSession, role_code: str) -> tuple[uuid.UUID, Course]:
    user, org, _m = await _setup_user_membership(db, role_code=role_code)
    course_id, _o, _u = await build_course(db, modules=1, lessons_per_module=1)
    course = await db.get(Course, course_id)
    assert course is not None
    course.organization_id = org.id
    course.assignee_user_id = user.id
    await db.commit()
    return user.id, course


def _url(course: Course, doc_id: uuid.UUID | None = None) -> str:
    base = f"/api/v1/orgs/{course.organization_id}/courses/{course.id}/documents"
    return f"{base}/{doc_id}/figures/extract" if doc_id else f"{base}/figures/extract"


async def test_extract_endpoint_queues_and_is_idempotent(
    client: Any, seeded_db: AsyncSession
) -> None:
    user_id, course = await _course_with_member(seeded_db, R.MANAGER)
    doc = build_course_document(course.id, filename="dispensa.pdf")
    seeded_db.add(doc)
    await seeded_db.commit()

    res = await client.post(_url(course, doc.id), headers=_bearer(user_id))
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["figures_status"] == "pending" and body["figures_requested_at"]
    assert body["origin"] == "upload" and body["is_own_work"] is False
    assert {"figures_count", "figures_pages_done", "bibliography_source"} <= set(body)

    again = await client.post(_url(course, doc.id), headers=_bearer(user_id))
    assert again.status_code == 202
    assert again.json()["figures_requested_at"] == body["figures_requested_at"]


async def test_extract_endpoint_requires_generate_permission(
    client: Any, seeded_db: AsyncSession
) -> None:
    user_id, course = await _course_with_member(seeded_db, R.MEMBER)
    doc = build_course_document(course.id, filename="dispensa.pdf")
    seeded_db.add(doc)
    await seeded_db.commit()
    res = await client.post(_url(course, doc.id), headers=_bearer(user_id))
    assert res.status_code == 403
    fresh = await seeded_db.get(CourseDocument, doc.id, populate_existing=True)
    assert fresh is not None and fresh.figures_status is None


async def test_extract_all_marks_unextractable_documents_skipped(
    client: Any, seeded_db: AsyncSession
) -> None:
    user_id, course = await _course_with_member(seeded_db, R.MANAGER)
    docs = {
        "ok.pdf": build_course_document(course.id, filename="ok.pdf"),
        "note.docx": build_course_document(course.id, filename="note.docx"),
        "riservato.pdf": build_course_document(
            course.id, filename="riservato.pdf", policy="content_only"
        ),
        "escluso.pdf": build_course_document(course.id, filename="escluso.pdf", policy="excluded"),
        "testo.md": build_course_document(course.id, filename="testo.md"),
    }
    docs["note.docx"].mime_type = DOCX
    docs["testo.md"].mime_type = "text/markdown"
    seeded_db.add_all(docs.values())
    await seeded_db.commit()
    res = await client.post(_url(course), headers=_bearer(user_id))
    assert res.status_code == 202, res.text
    by_name = {d["filename_original"]: d for d in res.json()}
    assert by_name["ok.pdf"]["figures_status"] == "pending"
    assert by_name["note.docx"]["figures_status"] == "pending"
    assert (
        by_name["riservato.pdf"]["figures_status"],
        by_name["riservato.pdf"]["figures_error_code"],
    ) == (
        "skipped",
        "policy_content_only",
    )
    assert by_name["escluso.pdf"]["figures_error_code"] == "policy_excluded"
    assert by_name["testo.md"]["figures_error_code"] == "unsupported_format"


async def test_extraction_disabled_is_reported(
    seeded_db: AsyncSession, extraction_enabled: Any
) -> None:
    extraction_enabled(False)
    course_id, _org, user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    course = await seeded_db.get(Course, course_id)
    assert course is not None
    doc = build_course_document(course_id, filename="a.pdf")
    seeded_db.add(doc)
    await seeded_db.commit()
    doc = await document_figures_service.request_extraction(
        seeded_db, course=course, doc=doc, actor_id=user.id
    )
    assert (doc.figures_status, doc.figures_error_code) == ("skipped", "extraction_disabled")


# --- cambio di politica ---------------------------------------------------------


async def test_back_to_citable_requeues_only_policy_skips(seeded_db: AsyncSession) -> None:
    course_id, _org, user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    course = await seeded_db.get(Course, course_id)
    assert course is not None
    skipped = build_course_document(course_id, filename="a.pdf", policy="content_only")
    failed = build_course_document(course_id, filename="b.pdf", policy="content_only")
    skipped.figures_status, skipped.figures_error_code = "skipped", "policy_content_only"
    failed.figures_status, failed.figures_error_code = "failed", "corrupt"
    seeded_db.add_all([skipped, failed])
    await seeded_db.commit()
    for doc in (skipped, failed):
        await course_service.update_document_citation_policy(
            seeded_db, course=course, doc=doc, citation_policy="citable", actor_id=user.id
        )
    assert skipped.figures_status == "pending" and skipped.figures_requested_at is not None
    assert (failed.figures_status, failed.figures_error_code) == ("failed", "corrupt")


# --- cancellazione del documento (U1) ---------------------------------------------


async def _lesson_using(
    db: AsyncSession, course_id: uuid.UUID, figure_ids: list[uuid.UUID]
) -> None:
    lesson = (
        (await db.execute(select(CourseLesson).where(CourseLesson.course_id == course_id)))
        .scalars()
        .first()
    )
    assert lesson is not None
    lesson.content_raw = {
        "introduction": "Intro",
        "visual_assets": [
            {"id": f"fig-{i}", "format": "source_figure", "content": str(fid)}
            for i, fid in enumerate(figure_ids)
        ]
        + [{"id": "m1", "format": "mermaid", "content": "flowchart LR\nA-->B"}],
    }
    await db.commit()


async def test_deleting_a_document_detaches_used_figures_with_identical_attribution(
    seeded_db: AsyncSession, storage: _Storage
) -> None:
    course_id, _org, user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    course = await seeded_db.get(Course, course_id)
    assert course is not None
    doc = build_course_document(course_id, filename="rossi_2020_ldv_a1b2c3.pdf")
    doc.origin = "paper_import"
    doc.bibliography = {
        "title": "Laser Doppler vibrometry",
        "authors": ["Mario Rossi"],
        "year": 2020,
    }
    doc.bibliography_source = "openalex"
    seeded_db.add(doc)
    await seeded_db.flush()
    used = build_document_figure(course_id, doc.id, license="cc_by", page=3)
    unused = build_document_figure(course_id, doc.id, license="cc_by", page=4)
    seeded_db.add_all([used, unused])
    await seeded_db.commit()
    await _lesson_using(seeded_db, course_id, [used.id])
    lines_before = {
        (lang, mode): figure_attribution_line(used, doc, language=lang, mode=mode)
        for lang in ("it", "en")
        for mode in ("written", "spoken")
    }
    unused_paths = [unused.storage_path, unused.preview_path]

    await course_service.delete_document(seeded_db, course=course, doc=doc, actor_id=user.id)
    await seeded_db.commit()

    remaining = list(
        (
            await seeded_db.execute(
                select(CourseDocumentFigure)
                .where(CourseDocumentFigure.course_id == course_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert [r.id for r in remaining] == [used.id]
    kept = remaining[0]
    assert kept.document_id is None and kept.detached_at is not None
    assert kept.storage_path == used.storage_path
    for (lang, mode), line in lines_before.items():
        assert figure_attribution_line(kept, None, language=lang, mode=mode) == line
    assert all(remote_storage.uploads_key(p) in storage.deleted for p in unused_paths if p), (
        storage.deleted
    )
    assert remote_storage.uploads_key(str(kept.storage_path)) not in storage.deleted


async def test_gc_detached_is_idempotent(seeded_db: AsyncSession, storage: _Storage) -> None:
    course_id, _org, _user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    orphan = build_document_figure(
        course_id,
        None,
        license="unknown",
        detached_at=datetime.now(UTC),
        attribution={"fallback_name": "dispensa"},
    )
    still_used = build_document_figure(
        course_id,
        None,
        license="unknown",
        detached_at=datetime.now(UTC),
        attribution={"fallback_name": "dispensa"},
    )
    seeded_db.add_all([orphan, still_used])
    await seeded_db.commit()
    await _lesson_using(seeded_db, course_id, [still_used.id])

    dry = await document_figures_service.gc_detached(seeded_db, course_id=course_id, apply=False)
    assert [r.id for r in dry] == [orphan.id]
    assert await seeded_db.get(CourseDocumentFigure, orphan.id) is not None
    done = await document_figures_service.gc_detached(seeded_db, course_id=course_id, apply=True)
    assert [r.id for r in done] == [orphan.id]
    again = await document_figures_service.gc_detached(seeded_db, course_id=course_id, apply=True)
    assert again == []
    seeded_db.expunge_all()
    assert await seeded_db.get(CourseDocumentFigure, orphan.id) is None
    assert await seeded_db.get(CourseDocumentFigure, still_used.id) is not None


async def test_deleting_the_course_removes_its_figure_folder(
    seeded_db: AsyncSession, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import file_service

    async def no_disk(path: str) -> None:
        return None

    monkeypatch.setattr(file_service, "delete_upload", no_disk)
    course_id, _org, user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    course = await course_service._refresh_full(seeded_db, course_id)
    await course_service.delete_course(seeded_db, course=course, actor_id=user.id)
    assert f"uploads/courses/{course_id}/document_figures/" in storage.deleted


def test_source_figure_ids_reads_only_source_figure_assets() -> None:
    fid = uuid.uuid4()
    content = {
        "visual_assets": [
            {"format": "source_figure", "content": str(fid)},
            {"format": "source_figure", "content": "non-un-uuid"},
            {"format": "mermaid", "content": str(uuid.uuid4())},
            "spazzatura",
        ]
    }
    assert document_figures_service.source_figure_ids(content) == {fid}
    assert document_figures_service.source_figure_ids(None) == set()


# --- script (nessun backfill) ------------------------------------------------------


async def test_script_dry_run_writes_nothing_and_apply_queues(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import extract_document_figures as script

    course_id, _org, _user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="vecchio.pdf")
    # Chiesto quando l'estrazione era spenta: si rimette in coda anche lui.
    disabled = build_course_document(course_id, filename="chiesto_a_estrazione_spenta.pdf")
    disabled.figures_status = "skipped"
    disabled.figures_error_code = "extraction_disabled"
    seeded_db.add_all([doc, disabled])
    await seeded_db.commit()

    async def run(*argv: str) -> None:
        # Stessa sessione del test al posto di quella dello script.
        class _Factory:
            def __call__(self) -> Any:
                return self

            async def __aenter__(self) -> AsyncSession:
                return seeded_db

            async def __aexit__(self, *exc: object) -> None:
                return None

        class _Engine:
            async def dispose(self) -> None:
                return None

        import app.db.session as session_mod

        monkeypatch.setattr(session_mod, "async_session_factory", _Factory())
        monkeypatch.setattr(session_mod, "engine", _Engine())
        await script.run(script.build_parser().parse_args(list(argv)))

    await run("--course", str(course_id))
    fresh = await seeded_db.get(CourseDocument, doc.id, populate_existing=True)
    assert fresh is not None and fresh.figures_status is None
    assert "dry-run" in capsys.readouterr().out
    await run("--course", str(course_id), "--apply")
    for doc_id in (doc.id, disabled.id):
        fresh = await seeded_db.get(CourseDocument, doc_id, populate_existing=True)
        assert fresh is not None and fresh.figures_status == "pending"


def test_every_figures_error_code_and_status_has_ui_text() -> None:
    """Il frontend mostra il motivo di ogni `figures_error_code`: nessun
    codice del modello può restare senza testo in it/en."""
    import json
    from pathlib import Path

    from app.models.course_document import FIGURES_ERROR_CODES, FIGURES_STATUSES

    locales = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"
    for lang in ("it", "en"):
        figures = json.loads((locales / f"{lang}.json").read_text(encoding="utf-8"))["courses"][
            "docs"
        ]["figures"]
        assert set(FIGURES_ERROR_CODES) <= set(figures["errors"]), lang
        statuses = {k.split("_")[0] for k in figures["status"]}
        assert set(FIGURES_STATUSES) <= statuses, lang
