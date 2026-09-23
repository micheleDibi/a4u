"""Estrazione delle figure di fonte (G12) sul PDF e sul DOCX di prova.

Il sottoprocesso gira come in produzione. Il motore euristico gira sempre
(CI); Docling richiede l'extra `figures` e il modello di layout: senza, il
test è saltato con `[dep:docling]`, e fallisce se `A4U_REQUIRED_DEPS`
contiene `docling` (stage `test` del Dockerfile).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.services.document_figures.geometry import BBox
from tests.dep_guard import require_module, required_deps
from tests.fixtures.source_figures.build import DOCX_NAME, PDF_NAME, build_all
from tests.source_figure_child import run_child

_MANIFEST = json.loads(
    (Path(__file__).parent / "fixtures" / "source_figures" / "manifest.json").read_text(
        encoding="utf-8"
    )
)
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(scope="module")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("source_figures")
    build_all(directory)
    return directory


def _workdir(tmp_path: Path, fixtures_dir: Path, name: str) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    shutil.copyfile(fixtures_dir / name, work / name)
    return work


def _bbox(event: dict[str, Any]) -> BBox:
    b = event["bbox"]
    return BBox(b["l"], b["t"], b["r"], b["b"])


def _check_pdf_events(events: list[dict[str, Any]], work: Path) -> None:
    assert events[0] == {"event": "ready", "pages": 5}
    figures = [e for e in events if e["event"] == "figure"]
    accepted = [e for e in figures if e["reject_reason"] is None]
    truth = _MANIFEST["pdf"]["figures"]
    assert len(accepted) == len(truth), [(e["locator"], e["bbox"]) for e in accepted]
    for expected in truth:
        gt = BBox(*expected["bbox"])
        match = [e for e in accepted if e["page"] == expected["page"]]
        assert len(match) == 1, expected["id"]
        found = match[0]
        box = _bbox(found)
        assert box.iou(gt) >= 0.6, (expected["id"], found["bbox"])
        # Ritaglio completo: il bbox (più il margine del ritaglio) copre il disegno.
        assert box.expand(4).coverage_of(gt) >= 0.9, (expected["id"], found["bbox"])
        assert found["caption"] == expected["caption"], expected["id"]
        assert found["source_label"] == expected["label"], expected["id"]
        assert found["is_vector"] is expected["is_vector"], expected["id"]
        assert (work / found["file"]).is_file() and (work / found["preview_file"]).is_file()
        assert found["byte_size"] == (work / found["file"]).stat().st_size
        assert len(found["phash"]) == 16
        if expected["is_vector"]:
            assert found["mime"] == "image/png" and found["dpi"] >= 300
        else:
            assert found["mime"] == "image/jpeg" and 150 <= found["dpi"] <= 300
        context = found["context_excerpt"] or ""
        assert "Università di Prova" not in context
    schema = next(e for e in accepted if e["page"] == 1)
    assert "fotorivelatore" in (schema["context_excerpt"] or "")
    # Distrattori: nessuna figura accettata sopra logo, icona o tabella.
    for distractor in _MANIFEST["pdf"]["distractors"]:
        box = BBox(*distractor["bbox"])
        pages = distractor.get("pages") or [distractor["page"]]
        for e in accepted:
            if e["page"] in pages:
                assert _bbox(e).coverage_of(box) < 0.5, (distractor["id"], e["locator"])
    done = [e["page"] for e in events if e["event"] == "page_done"]
    assert done == [1, 2, 3, 4, 5]


def test_heuristic_engine_on_the_fixture_pdf(tmp_path: Path, fixtures_dir: Path) -> None:
    work = _workdir(tmp_path, fixtures_dir, PDF_NAME)
    code, events, stderr = run_child(
        work, source=PDF_NAME, mime=PDF_MIME, engine="heuristic", blocks=[[1, 3], [4, 5]]
    )
    assert code == 0, stderr[-2000:]
    _check_pdf_events(events, work)
    assert [e["pages"] for e in events if e["event"] == "block_done"] == [[1, 3], [4, 5]]


def _docling_artifacts() -> str:
    require_module("docling", "docling")
    path = os.environ.get("FIGURE_DOCLING_ARTIFACTS_PATH", "/opt/docling-models")
    if not Path(path).is_dir():
        if "docling" in required_deps():
            pytest.fail(f"[dep:docling] modelli di layout assenti in {path}")
        pytest.skip(f"[dep:docling] modelli di layout assenti in {path}")
    return path


def test_docling_engine_on_the_fixture_pdf(tmp_path: Path, fixtures_dir: Path) -> None:
    artifacts = _docling_artifacts()
    work = _workdir(tmp_path, fixtures_dir, PDF_NAME)
    code, events, stderr = run_child(
        work,
        source=PDF_NAME,
        mime=PDF_MIME,
        engine="docling",
        blocks=[[1, 5]],
        artifacts_path=artifacts,
    )
    assert code == 0, stderr[-2000:]
    _check_pdf_events(events, work)


def test_docling_probe(tmp_path: Path) -> None:
    artifacts = _docling_artifacts()
    code, events, stderr = run_child(
        tmp_path,
        source="",
        mime="",
        engine="docling",
        blocks=[],
        artifacts_path=artifacts,
        probe=True,
    )
    assert code == 0, stderr[-2000:]
    assert events[0]["event"] == "probe_ok" and events[0]["torch"]


def test_docling_missing_is_engine_unavailable(tmp_path: Path, fixtures_dir: Path) -> None:
    try:
        import docling  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("Docling installato: il caso «motore assente» non si riproduce qui")
    work = _workdir(tmp_path, fixtures_dir, PDF_NAME)
    code, events, _ = run_child(
        work, source=PDF_NAME, mime=PDF_MIME, engine="docling", blocks=[[1, 1]]
    )
    assert code == 2
    assert events == [events[0]] and events[0]["event"] == "error"
    assert events[0]["code"] == "engine_unavailable"


def test_docx_images_with_caption(tmp_path: Path, fixtures_dir: Path) -> None:
    work = _workdir(tmp_path, fixtures_dir, DOCX_NAME)
    code, events, stderr = run_child(
        work, source=DOCX_NAME, mime=DOCX_MIME, engine="docling", blocks=[[1, 1]]
    )
    assert code == 0, stderr[-2000:]
    assert events[0] == {"event": "ready", "pages": 1}
    figures = [e for e in events if e["event"] == "figure"]
    truth = _MANIFEST["docx"]["figures"]
    # Il logo della testata non è nel corpo del documento.
    assert [e["locator"] for e in figures] == ["d0001", "d0002"]
    for found, expected in zip(figures, truth, strict=True):
        assert found["reject_reason"] is None
        assert found["caption"] == expected["caption"]
        assert found["source_label"] == expected["label"]
        assert found["page"] is None and found["bbox"] is None
        assert (work / found["file"]).is_file()
    assert figures[1]["mime"] == "image/jpeg"
    assert "vibrometro" in (figures[0]["context_excerpt"] or "")


def test_corrupt_pdf_is_reported(tmp_path: Path) -> None:
    (tmp_path / "rotto.pdf").write_bytes(b"%PDF-1.4\nquesto non e' un pdf")
    code, events, _ = run_child(
        tmp_path, source="rotto.pdf", mime=PDF_MIME, engine="heuristic", blocks=[[1, 1]]
    )
    assert code == 2
    assert events[-1]["event"] == "error" and events[-1]["code"] == "corrupt"


def test_encrypted_pdf_is_reported(tmp_path: Path, fixtures_dir: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(str(fixtures_dir / PDF_NAME)).pages:
        writer.add_page(page)
    writer.encrypt(user_password="segreta", owner_password="segreta")
    with open(tmp_path / "cifrato.pdf", "wb") as handle:
        writer.write(handle)
    code, events, _ = run_child(
        tmp_path, source="cifrato.pdf", mime=PDF_MIME, engine="heuristic", blocks=[[1, 1]]
    )
    assert code == 2
    assert events[-1]["event"] == "error" and events[-1]["code"] == "encrypted"


def test_unsupported_mime_is_reported(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("testo")
    code, events, _ = run_child(
        tmp_path, source="note.txt", mime="text/plain", engine="heuristic", blocks=[]
    )
    assert code == 2
    assert events[-1]["code"] == "unsupported_format"


def test_child_stdout_is_protocol_only(tmp_path: Path, fixtures_dir: Path) -> None:
    """Ogni riga dello stdout è un evento JSON: le stampe delle librerie
    vanno su stderr."""
    work = _workdir(tmp_path, fixtures_dir, PDF_NAME)
    code, events, _ = run_child(
        work, source=PDF_NAME, mime=PDF_MIME, engine="heuristic", blocks=[[2, 2]]
    )
    assert code == 0
    assert all("event" in e for e in events)
