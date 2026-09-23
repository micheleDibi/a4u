"""Provenienza dei documenti all'ingresso (WP1b).

L'import dalla ricerca paper salva origine e bibliografia OpenAlex
(`bibliography_source='openalex'`), da cui la riga «Fonte» delle figure di
fonte; il caricamento normale resta `origin='upload'` senza bibliografia.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.schemas.document_bibliography import DocumentBibliography
from app.schemas.paper_search import PaperOut
from app.services import course_service, file_service, paper_import_service
from tests.course_builders import build_course


@pytest.fixture
async def db(seeded_db: AsyncSession) -> AsyncSession:
    return seeded_db


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_save(payload: bytes, *, subdir: str, mime_type: str, **_: Any):
        ext = "pdf" if mime_type == "application/pdf" else "md"
        return f"/uploads/{subdir}/fake.{ext}", f"fake.{ext}", len(payload)

    monkeypatch.setattr(file_service, "save_document_from_bytes", fake_save)


def _paper(**overrides: Any) -> PaperOut:
    data: dict[str, Any] = {
        "id": "https://openalex.org/W123",
        "doi": "10.1000/ldv",
        "title": "Laser Doppler vibrometry for structural testing",
        "abstract": "Abstract.",
        "authors": ["Mario Rossi", "  ", "Anna Bianchi"],
        "year": 2020,
        "journal": "Journal of Sound and Vibration",
        "citations": 10,
        "is_oa": True,
        "oa_pdf_url": None,
        "doi_url": "https://doi.org/10.1000/ldv",
        "work_type": None,
        "keywords": [],
        "relevance_score": None,
    }
    data.update(overrides)
    return PaperOut(**data)


async def _course(db: AsyncSession) -> tuple[Course, Any]:
    course_id, _org, user = await build_course(db, modules=1, lessons_per_module=1)
    course = await db.get(Course, course_id)
    assert course is not None
    return course, user


_EXPECTED_BIBLIOGRAPHY = {
    "title": "Laser Doppler vibrometry for structural testing",
    "authors": ["Mario Rossi", "Anna Bianchi"],
    "year": 2020,
    "container": "Journal of Sound and Vibration",
    "doi": "10.1000/ldv",
    "url": "https://doi.org/10.1000/ldv",
    "openalex_id": "https://openalex.org/W123",
}


async def test_metadata_import_records_openalex_bibliography(db: AsyncSession) -> None:
    course, user = await _course(db)
    result = await paper_import_service.import_paper(
        db, course=course, paper=_paper(), actor_id=user.id
    )
    doc = result.document
    assert result.mode == "metadata"
    assert doc.origin == "paper_metadata"
    assert doc.bibliography_source == "openalex"
    assert doc.bibliography == _EXPECTED_BIBLIOGRAPHY
    assert doc.license is None and doc.is_own_work is False


async def test_pdf_import_records_openalex_bibliography(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_download(url: str, *, max_bytes: int) -> bytes:
        return b"%PDF-1.4 fake"

    async def fake_get_work(work_id: str) -> Any:
        # WP5: l'URL del PDF e i metadati si rileggono dal server OpenAlex.
        from app.services.openalex_client import _to_work

        return _to_work(
            {
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1000/ldv",
                "title": "Laser Doppler vibrometry for structural testing",
                "authorships": [
                    {"author": {"display_name": "Mario Rossi"}},
                    {"author": {"display_name": "Anna Bianchi"}},
                ],
                "publication_year": 2020,
                "primary_location": {"source": {"display_name": "Journal of Sound and Vibration"}},
                "open_access": {"is_oa": True},
                "best_oa_location": {"pdf_url": "https://x.org/a.pdf"},
            }
        )

    monkeypatch.setattr(paper_import_service, "download_pdf", fake_download)
    monkeypatch.setattr(paper_import_service, "get_work", fake_get_work)
    course, user = await _course(db)
    result = await paper_import_service.import_paper(
        db, course=course, paper=_paper(oa_pdf_url="https://x.org/a.pdf"), actor_id=user.id
    )
    doc = result.document
    assert result.mode == "pdf"
    assert doc.origin == "paper_import"
    assert doc.bibliography == _EXPECTED_BIBLIOGRAPHY


async def test_implausible_year_and_empty_fields_are_dropped(db: AsyncSession) -> None:
    course, user = await _course(db)
    paper = _paper(year=20201, journal="", doi=None, doi_url=None, authors=[])
    doc = (
        await paper_import_service.import_paper(db, course=course, paper=paper, actor_id=user.id)
    ).document
    assert doc.bibliography == {
        "title": "Laser Doppler vibrometry for structural testing",
        "authors": [],
        "openalex_id": "https://openalex.org/W123",
    }


async def test_plain_bytes_document_has_no_bibliography(db: AsyncSession) -> None:
    course, user = await _course(db)
    doc = await course_service.add_document_from_bytes(
        db,
        course=course,
        payload=b"# testo",
        filename_original="note.md",
        mime_type="text/markdown",
        actor_id=user.id,
    )
    assert doc.origin == "upload"
    assert doc.bibliography is None and doc.bibliography_source is None


async def test_bibliography_and_source_go_together(db: AsyncSession) -> None:
    course, user = await _course(db)
    with pytest.raises(ValueError):
        await course_service.add_document_from_bytes(
            db,
            course=course,
            payload=b"# testo",
            filename_original="note.md",
            mime_type="text/markdown",
            actor_id=user.id,
            bibliography=DocumentBibliography(title="T"),
        )


async def test_client_supplied_metadata_is_normalised(db: AsyncSession) -> None:
    """I metadati arrivano dal client (`PaperOut`): spazi compressi prima del
    taglio, url solo http(s). Nessun 500 per un autore pieno di spazi."""
    course, user = await _course(db)
    paper = _paper(
        authors=[" " * 200 + "x", "Anna   Bianchi"],
        doi_url="javascript:alert(1)",
        title="  Titolo   con   spazi  ",
    )
    doc = (
        await paper_import_service.import_paper(db, course=course, paper=paper, actor_id=user.id)
    ).document
    assert doc.bibliography is not None
    assert doc.bibliography["authors"] == ["x", "Anna Bianchi"]
    assert doc.bibliography["title"] == "Titolo con spazi"
    assert "url" not in doc.bibliography
