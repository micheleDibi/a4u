"""Bibliografia deterministica dai file (`document_figures.metadata`).

Titoli e autori implausibili scartati, DOI riconosciuto nel testo,
metadati del PDF e `core.xml` di DOCX letti senza il modello.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.document_figures import metadata
from tests.fixtures.source_figures.build import DOCX_NAME, PDF_NAME, build_all


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("metadata")
    build_all(directory)
    return directory


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Vibrometria laser", "Vibrometria laser"),
        ("  Misure   meccaniche e termiche ", "Misure meccaniche e termiche"),
        ("Microsoft Word - dispensa.docx", None),
        ("dispensa_finale.pdf", None),
        ("Untitled", None),
        ("Presentazione standard", None),
        ("abc", None),
        ("12345 67890", None),
        (None, None),
        (42, None),
    ],
)
def test_plausible_title(raw: object, expected: str | None) -> None:
    assert metadata.plausible_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mario Rossi; Anna Bianchi", ["Mario Rossi", "Anna Bianchi"]),
        ("Mario Rossi and Anna Bianchi", ["Mario Rossi", "Anna Bianchi"]),
        ("mrossi", []),
        ("utente@ateneo.it", []),
        ("Docente di Prova", ["Docente di Prova"]),
        (["Mario Rossi", "x"], ["Mario Rossi"]),
        (None, []),
    ],
)
def test_plausible_authors(raw: object, expected: list[str]) -> None:
    assert metadata.plausible_authors(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DOI: 10.1016/j.jsv.2020.115432.", "10.1016/j.jsv.2020.115432"),
        ("https://doi.org/10.3390/S21041234)", "10.3390/s21041234"),
        ("nessun identificativo qui", None),
        (None, None),
    ],
)
def test_find_doi(text: str | None, expected: str | None) -> None:
    assert metadata.find_doi(text) == expected


def test_pdf_metadata_of_the_fixture(built: Path) -> None:
    bib = metadata.pdf_bibliography(built / PDF_NAME)
    assert bib is not None
    assert bib.as_json() == {"title": "Vibrometria laser", "authors": ["Docente di Prova"]}
    assert "vibrometro" in metadata.pdf_first_pages_text(built / PDF_NAME).lower()


def test_docx_core_properties(built: Path) -> None:
    bib = metadata.office_bibliography(built / DOCX_NAME)
    assert bib is not None
    assert bib.title == "Appunti di vibrometria"
    assert bib.authors == ["Docente di Prova"]


def test_unreadable_files_give_no_bibliography(tmp_path: Path) -> None:
    (tmp_path / "rotto.pdf").write_bytes(b"non un pdf")
    (tmp_path / "rotto.docx").write_bytes(b"non uno zip")
    assert metadata.pdf_bibliography(tmp_path / "rotto.pdf") is None
    assert metadata.office_bibliography(tmp_path / "rotto.docx") is None
    assert metadata.pdf_first_pages_text(tmp_path / "rotto.pdf") == ""
