"""PPTX fra i documenti del corso (U3, WP2f).

Upload accettato solo se il contenuto è davvero una presentazione OOXML
(il MIME arriva dal client), testo delle slide per il riassunto, figure
estratte slide per slide con la riga «Fonte» che cita la slide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ValidationAppError
from app.models.course_document_figure import CourseDocumentFigure
from app.services import course_document_figures_worker as worker
from app.services import document_extraction_service, file_service, remote_storage
from app.services.figure_attribution import figure_attribution_line
from tests.course_builders import build_course, build_course_document
from tests.fixtures.source_figures.build import GROUND_TRUTH, PPTX_NAME, build_all
from tests.test_source_figure_worker import FakeStorage, FakeVision

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.fixture(scope="module")
def pptx_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    directory = tmp_path_factory.mktemp("pptx")
    build_all(directory)
    return (directory / PPTX_NAME).read_bytes()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


async def test_valid_pptx_upload_is_accepted(storage: FakeStorage, pptx_bytes: bytes) -> None:
    path, name, size = await file_service.save_document_from_bytes(
        pptx_bytes, subdir="courses/abc", mime_type=PPTX_MIME
    )
    assert name.endswith(".pptx") and size == len(pptx_bytes)
    assert remote_storage.uploads_key(path) in storage.files


@pytest.mark.parametrize(
    "payload",
    [b"%PDF-1.4 non una presentazione", b"PK\x03\x04 zip rotto", b"testo qualsiasi"],
)
async def test_fake_pptx_upload_is_refused(storage: FakeStorage, payload: bytes) -> None:
    with pytest.raises(ValidationAppError) as info:
        await file_service.save_document_from_bytes(
            payload, subdir="courses/abc", mime_type=PPTX_MIME
        )
    assert info.value.code == "invalid_document_content"
    assert storage.files == {}


async def test_docx_zip_declared_as_pptx_is_refused(storage: FakeStorage, tmp_path: Path) -> None:
    build_all(tmp_path)
    docx = (tmp_path / "vibrometria_appunti.docx").read_bytes()
    with pytest.raises(ValidationAppError):
        await file_service.save_document_from_bytes(docx, subdir="courses/abc", mime_type=PPTX_MIME)


async def test_slide_text_feeds_the_summary(tmp_path: Path, pptx_bytes: bytes) -> None:
    source = tmp_path / "lezione.pptx"
    source.write_bytes(pptx_bytes)
    text, _chars = await document_extraction_service.extract_text(source, PPTX_MIME)
    for needle in GROUND_TRUTH["pptx"]["text"]:
        assert needle in text


async def test_worker_extracts_pictures_per_slide(
    seeded_db: AsyncSession,
    storage: FakeStorage,
    pptx_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = get_settings().model_copy(
        update={"figure_extraction_enabled": True, "figure_extraction_engine": "heuristic"}
    )
    monkeypatch.setattr(worker, "get_settings", lambda: patched)
    monkeypatch.setattr(worker, "mem_available_mb", lambda: None)
    vision = FakeVision()
    monkeypatch.setattr(worker, "describe_figure", vision)

    course_id, _org, _user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="lezione_vibrometria.pptx")
    doc.mime_type = PPTX_MIME
    doc.file_path = f"/uploads/courses/{course_id}/lezione.pptx"
    doc.figures_status = "pending"
    seeded_db.add(doc)
    await seeded_db.commit()
    storage.files[remote_storage.uploads_key(doc.file_path)] = pptx_bytes

    claimed = await worker.claim_next(seeded_db)
    assert claimed is not None and claimed.id == doc.id
    await worker.process_document(seeded_db, claimed)
    assert claimed.figures_status == "ready", claimed.figures_error_code
    assert claimed.figures_pages_total == 3 and claimed.figures_count == 2
    assert claimed.bibliography == {
        "title": "Lezione di vibrometria",
        "authors": ["Docente di Prova"],
    }
    rows = list(
        (
            await seeded_db.execute(
                select(CourseDocumentFigure)
                .where(CourseDocumentFigure.document_id == doc.id)
                .order_by(CourseDocumentFigure.locator)
            )
        )
        .scalars()
        .all()
    )
    assert [(r.locator, r.page, r.status) for r in rows] == [
        ("s0002-f01", 2, "ready"),
        ("s0003-f01", 3, "ready"),
    ]
    line: Any = figure_attribution_line(rows[0], claimed, language="it")
    assert line == "Fonte: Docente di Prova, «Lezione di vibrometria», fig. 1, slide 2"
