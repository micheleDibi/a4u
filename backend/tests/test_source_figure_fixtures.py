"""Le fixture delle figure di fonte corrispondono alla verità di terreno.

`build.py` e `manifest.json` restano allineati, e i documenti generati
contengono davvero ciò che il manifest dichiara (pagine, raster, didascalie,
immagini del DOCX).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pdfplumber
import pytest

from tests.fixtures.source_figures.build import (
    DOCX_NAME,
    GROUND_TRUTH,
    PDF_NAME,
    PPTX_NAME,
    build_all,
)

_DIR = Path(__file__).parent / "fixtures" / "source_figures"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("fixtures")
    build_all(directory)
    return directory


def test_manifest_matches_the_generator() -> None:
    manifest = json.loads((_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == json.loads(json.dumps(GROUND_TRUTH))


def test_pdf_has_the_declared_pages_rasters_and_captions(built: Path) -> None:
    truth = GROUND_TRUTH["pdf"]
    with pdfplumber.open(str(built / PDF_NAME)) as pdf:
        assert len(pdf.pages) == truth["pages"]
        assert pdf.metadata.get("Title") == "Vibrometria laser"
        photo = next(f for f in truth["figures"] if f["id"] == "bench_photo")
        images = pdf.pages[photo["page"] - 1].images
        assert any(
            abs(im["x0"] - photo["bbox"][0]) < 1 and abs(im["bottom"] - photo["bbox"][3]) < 1
            for im in images
        )
        for figure in truth["figures"]:
            text = pdf.pages[figure["page"] - 1].extract_text() or ""
            if figure["caption"]:
                assert figure["caption"] in " ".join(text.split())
        for page in pdf.pages:
            assert any(abs(im["x0"] - 40) < 1 for im in page.images), "logo su ogni pagina"


def test_docx_has_two_body_pictures_and_a_header_logo(built: Path) -> None:
    with zipfile.ZipFile(built / DOCX_NAME) as zf:
        body = zf.read("word/document.xml").decode("utf-8")
        headers = [n for n in zf.namelist() if n.startswith("word/header")]
        assert body.count("<a:blip ") == 2
        assert any(b"<a:blip " in zf.read(h) for h in headers if h.endswith(".xml"))


def test_pptx_has_three_slides_and_two_pictures(built: Path) -> None:
    with zipfile.ZipFile(built / PPTX_NAME) as zf:
        names = zf.namelist()
        assert names[0] == "[Content_Types].xml"
        assert sum(n.startswith("ppt/slides/slide") for n in names) == 3
        assert sum(n.startswith("ppt/media/") for n in names) == 2
        assert b"Docente di Prova" in zf.read("docProps/core.xml")
