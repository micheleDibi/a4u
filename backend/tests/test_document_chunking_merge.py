from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from app.services import document_extraction_service as extraction
from app.services.document_chunking_service import (
    compute_fingerprint,
    plan_chunks,
)
from app.services.document_summary_merge_service import (
    FACTS_RENDER_MAX_CHARS,
    merge_chunk_facts,
    render_merged_facts,
)

pytestmark = pytest.mark.asyncio


def _spans_single(text: str):
    return [(0, len(text), None)]


# ---------------------------------------------------------------------------
# plan_chunks
# ---------------------------------------------------------------------------


async def test_plan_chunks_single_under_threshold():
    text = "parola " * 500  # ~3.5k char
    chunks = plan_chunks(
        text.strip(), _spans_single(text), target_chars=24_000,
        overlap_chars=1_500,
    )
    assert len(chunks) == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(text.strip())


async def test_plan_chunks_deterministic_with_overlap_and_tail_merge():
    text = ("Frase di prova numero uno. " * 200 + "\n\n") * 40  # ~220k
    spans = _spans_single(text)
    a = plan_chunks(text, spans, target_chars=24_000, overlap_chars=1_500)
    b = plan_chunks(text, spans, target_chars=24_000, overlap_chars=1_500)
    assert [(c.char_start, c.char_end) for c in a] == [
        (c.char_start, c.char_end) for c in b
    ]
    assert len(a) >= 8
    # Overlap: ogni chunk (dal secondo) inizia prima della fine del precedente.
    for prev, cur in pairwise(a):
        assert cur.char_start < prev.char_end
        assert prev.char_end - cur.char_start <= 1_500
    # Copertura totale: ultimo chunk arriva alla fine, nessuna coda persa.
    assert a[-1].char_end == len(text)
    # Tail-merge: l'ultimo chunk non è una briciola né oltre il 125%.
    last_len = a[-1].char_end - a[-1].char_start
    assert last_len <= int(24_000 * 1.25) + 1


async def test_plan_chunks_prefers_page_boundaries():
    page = "x" * 5_000
    pages = [page] * 10
    text = "\n\n".join(pages)
    spans = []
    cursor = 0
    for i, p in enumerate(pages):
        spans.append((cursor, cursor + len(p), i + 1))
        cursor += len(p) + 2
    chunks = plan_chunks(text, spans, target_chars=10_000, overlap_chars=500)
    page_ends = {end for _s, end, _p in spans}
    # Il primo taglio cade su un confine pagina (entro la finestra ±15%).
    assert chunks[0].char_end in page_ends
    assert chunks[0].page_start == 1
    assert chunks[0].page_end is not None


async def test_plan_chunks_scale_on_large_input():
    text = "a" * 1_250_000
    chunks = plan_chunks(
        text, _spans_single(text), target_chars=24_000, overlap_chars=1_500
    )
    # ~1.25M / (24k - 1.5k overlap) ≈ 55 chunk.
    assert 45 <= len(chunks) <= 70
    assert chunks[-1].char_end == len(text)


async def test_fingerprint_sensitivity():
    base = compute_fingerprint(
        b"contenuto", target_chars=24_000, overlap_chars=1_500,
        singleshot_max_chars=100_000, hard_cap_chars=2_000_000,
        model="gpt-4o-mini",
    )
    assert base != compute_fingerprint(
        b"contenuto-diverso", target_chars=24_000, overlap_chars=1_500,
        singleshot_max_chars=100_000, hard_cap_chars=2_000_000,
        model="gpt-4o-mini",
    )
    assert base != compute_fingerprint(
        b"contenuto", target_chars=20_000, overlap_chars=1_500,
        singleshot_max_chars=100_000, hard_cap_chars=2_000_000,
        model="gpt-4o-mini",
    )
    assert base != compute_fingerprint(
        b"contenuto", target_chars=24_000, overlap_chars=1_500,
        singleshot_max_chars=100_000, hard_cap_chars=2_000_000,
        model="gpt-4o",
    )


# ---------------------------------------------------------------------------
# merge_chunk_facts / render_merged_facts
# ---------------------------------------------------------------------------


def _chunk(abstract: str, **lists):
    base = {
        "chunk_abstract": abstract,
        "detected_language": "it",
        "outline_items": [],
        "key_concepts": [],
        "definitions": [],
        "examples_or_cases": [],
        "formulas_or_rules": [],
        "authors_and_references": [],
        "candidate_tags": [],
    }
    base.update(lists)
    return base


async def test_merge_dedup_exact_and_near():
    merged = merge_chunk_facts(
        [
            _chunk(
                "Primo blocco.",
                definitions=[
                    {"term": "Entropia", "definition": "Misura del disordine."},
                    {"term": "Entalpia", "definition": "Contenuto termico."},
                ],
            ),
            _chunk(
                "Secondo blocco.",
                definitions=[
                    # dedup esatta (case/accents-insensitive)
                    {"term": "entropia", "definition": "Misura del disordine di un sistema."},
                    # near-dup (typo)
                    {"term": "Entalpiaa", "definition": "Contenuto termico a pressione costante."},
                ],
            ),
        ]
    )
    terms = [e.data["term"] for e in merged.definitions]
    assert len(terms) == 2
    counts = {e.data["term"]: e.count for e in merged.definitions}
    assert all(c == 2 for c in counts.values())
    # A parità si tiene la versione più lunga.
    defs = {e.key: e.data["definition"] for e in merged.definitions}
    assert any("sistema" in d for d in defs.values())


async def test_merge_orders_by_first_occurrence_and_votes_language():
    merged = merge_chunk_facts(
        [
            _chunk(
                "A.",
                key_concepts=[{"name": "Zeta", "explanation": "z"}],
                outline_items=["Capitolo 1", "Capitolo 2"],
            ),
            _chunk(
                "B.",
                key_concepts=[{"name": "Alfa", "explanation": "a"}],
                outline_items=["Capitolo 2", "Capitolo 3"],
            ),
        ]
    )
    assert [e.data["name"] for e in merged.key_concepts] == ["Zeta", "Alfa"]
    assert merged.structure_outline == [
        "Capitolo 1", "Capitolo 2", "Capitolo 3",
    ]
    assert merged.detected_language == "it"
    assert [i for i, _p, _t in merged.abstracts] == [0, 1]


async def test_render_respects_global_budget():
    definitions = [
        {"term": f"Termine {i}", "definition": "x" * 400} for i in range(200)
    ]
    merged = merge_chunk_facts([_chunk("A.", definitions=definitions)])
    rendered = render_merged_facts(merged)
    assert len(rendered) <= FACTS_RENDER_MAX_CHARS + 5_000  # margine sezioni
    assert rendered.count("Termine") < 200  # trim avvenuto


# ---------------------------------------------------------------------------
# extract_segments / extract_text (equivalenza)
# ---------------------------------------------------------------------------


async def test_extract_text_equivalence_on_txt(tmp_path: Path):
    content = "  Riga uno.\n\nRiga due con testo.  "
    p = tmp_path / "doc.txt"
    p.write_text(content, encoding="utf-8")
    text, original = await extraction.extract_text(p, "text/plain")
    assert text == content.strip()
    assert original == len(content.strip())

    full, spans, original2, capped = await extraction.extract_segments(
        p, "text/plain", hard_cap_chars=1_000_000
    )
    assert full == text
    assert original2 == original
    assert capped is False
    assert spans == [(0, len(full), None)]


async def test_extract_segments_hard_cap(tmp_path: Path):
    p = tmp_path / "big.txt"
    p.write_text("a" * 5_000, encoding="utf-8")
    full, spans, original, capped = await extraction.extract_segments(
        p, "text/plain", hard_cap_chars=2_000
    )
    assert capped is True
    assert original == 5_000
    assert len(full) == 2_000
    assert spans == [(0, 2_000, None)]


async def test_extract_pdf_segments_with_empty_pages(tmp_path: Path):
    weasyprint = pytest.importorskip("weasyprint")
    html = (
        "<html><body>"
        "<div>Pagina uno con testo.</div>"
        "<div style='page-break-before: always'></div>"  # pagina vuota
        "<div style='page-break-before: always'>Pagina tre con testo.</div>"
        "</body></html>"
    )
    pdf_path = tmp_path / "doc.pdf"
    weasyprint.HTML(string=html).write_pdf(str(pdf_path))

    text, original = await extraction.extract_text(
        pdf_path, "application/pdf"
    )
    full, spans, original2, _ = await extraction.extract_segments(
        pdf_path, "application/pdf", hard_cap_chars=1_000_000
    )
    # Equivalenza byte-identica wrapper vs segmenti (incl. pagine vuote).
    assert full == text
    assert original2 == original
    # Solo le pagine non vuote producono span, con numerazione progressiva.
    assert len(spans) == 2
    assert [p for _s, _e, p in spans] == [1, 2]
    for start, end, _page in spans:
        assert full[start:end].strip()
