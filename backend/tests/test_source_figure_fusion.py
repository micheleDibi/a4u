"""Fusione delle figure di fonte scelte dal PROMPT 3 (WP3).

Scelte valide → asset `source_figure` (UUID della figura), budget (b),
scarti (sconosciute, doppie, non citate, oltre budget), tag orfani tolti,
asset generati `SRC-…` rinominati, coda di fonte tolta dalle didascalie,
`source_figures` mai in `content_raw`, il modello non può generare asset
`source_figure`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.course_lesson_content import LessonContentOutput
from app.services.figure_mix import compute_figure_mix
from app.services.source_figure_fusion import (
    clean_caption,
    fuse_source_figures,
    remove_source_figures,
)

A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
C = uuid.UUID("cccccccc-0000-0000-0000-000000000003")
REFS = {"SRC-aaaaaaaa": A, "SRC-bbbbbbbb": B, "SRC-cccccccc": C}


def _output(**overrides: Any) -> LessonContentOutput:
    data: dict[str, Any] = {
        "lesson_id": "M1.L1",
        "lesson_title": "Vibrometria",
        "is_introductory": False,
        "estimated_word_count": 1000,
        "introduction": "Introduzione.",
        "sections": [
            {
                "section_id": "S1",
                "title": "Principio",
                "content": "Lo schema.\n\n[FIG:SRC-bbbbbbbb]\n\nPoi il grafico.\n\n[FIG:fig1]\n\n"
                "E lo strumento.\n\n[FIG:SRC-aaaaaaaa]\n\nFine.",
            },
            {
                "section_id": "S2",
                "title": "Misura",
                "content": "Testo.\n\n[FIG:SRC-cccccccc]\n\nAltro.\n\n[FIG:SRC-deadbeef]\n\nFine.",
            },
        ],
        "summary": "Sintesi.",
        "key_takeaways": ["uno", "due", "tre"],
        "visual_assets": [
            {
                "asset_id": "fig1",
                "format": "mermaid",
                "content": "flowchart LR\nA-->B",
                "caption": "Flusso",
                "alt_text": "flusso",
            }
        ],
        "coverage_check": {"objectives_covered": [], "topics_covered": []},
        "source_figures": [
            {"figure": "SRC-aaaaaaaa", "caption": "Schema del vibrometro.", "alt_text": "schema"},
            {"figure": "SRC-bbbbbbbb", "caption": "Banco. Fonte: Rossi 2020", "alt_text": "b"},
            {"figure": "SRC-cccccccc", "caption": "Risposta", "alt_text": "c"},
            {"figure": "SRC-99999999", "caption": "inventata", "alt_text": ""},
            {"figure": "SRC-aaaaaaaa", "caption": "doppia", "alt_text": ""},
        ],
    }
    data.update(overrides)
    return LessonContentOutput.model_validate(data)


def _source_assets(output: LessonContentOutput) -> list[Any]:
    return [a for a in output.visual_assets if a.format == "source_figure"]


def test_fusion_adds_cited_choices_in_citation_order_within_budget() -> None:
    output = _output()
    report = fuse_source_figures(output, REFS, max_items=2)
    assert report.added == ["SRC-bbbbbbbb", "SRC-aaaaaaaa"]
    assert report.dropped_over_budget == ["SRC-cccccccc"]
    assert report.dropped_unknown == ["SRC-99999999"]
    assert report.dropped_duplicate == ["SRC-aaaaaaaa"]
    assets = _source_assets(output)
    assert [(a.asset_id, a.content) for a in assets] == [
        ("SRC-bbbbbbbb", str(B)),
        ("SRC-aaaaaaaa", str(A)),
    ]
    assert assets[0].caption == "Banco." and report.captions_trimmed == ["SRC-bbbbbbbb"]
    # Il tag della figura oltre budget e quello mai dichiarato spariscono,
    # le figure generate restano intatte.
    s2 = output.sections[1].content
    assert "SRC-cccccccc" not in s2 and "SRC-deadbeef" not in s2
    assert s2 == "Testo.\n\nAltro.\n\nFine."
    assert report.removed_tags == ["src-cccccccc", "src-deadbeef"]
    assert [a.asset_id for a in output.visual_assets if a.format != "source_figure"] == ["fig1"]
    assert output.source_figures == []


def test_uncited_choice_is_dropped() -> None:
    output = _output(
        sections=[{"section_id": "S1", "title": "T", "content": "Nessun tag qui."}],
        source_figures=[{"figure": "SRC-aaaaaaaa", "caption": "x", "alt_text": ""}],
    )
    report = fuse_source_figures(output, REFS, max_items=4)
    assert report.dropped_uncited == ["SRC-aaaaaaaa"] and not _source_assets(output)


def test_without_catalog_nothing_is_added_and_orphan_tags_go() -> None:
    output = _output(source_figures=[])
    report = fuse_source_figures(output, {}, max_items=0)
    assert report.added == [] and not _source_assets(output)
    assert "[FIG:SRC-" not in output.sections[0].content
    assert "[FIG:fig1]" in output.sections[0].content


def test_generated_asset_with_src_id_is_renamed() -> None:
    output = _output(
        visual_assets=[
            {
                "asset_id": "SRC-aaaaaaaa",
                "format": "mermaid",
                "content": "flowchart LR\nA-->B",
                "caption": "Flusso",
                "alt_text": "flusso",
            }
        ],
        sections=[
            {"section_id": "S1", "title": "T", "content": "Testo.\n\n[FIG:SRC-aaaaaaaa]\n\nFine."}
        ],
        source_figures=[],
    )
    report = fuse_source_figures(output, {}, max_items=0)
    assert report.renamed_generated == {"src-aaaaaaaa": "fig-src-1"}
    assert output.visual_assets[0].asset_id == "fig-src-1"
    assert "[FIG:fig-src-1]" in output.sections[0].content


def test_source_figures_never_reach_content_raw() -> None:
    output = _output()
    assert "source_figures" not in output.model_dump()
    fuse_source_figures(output, REFS, max_items=4)
    dumped = output.model_dump()
    assert "source_figures" not in dumped
    assert {"asset_id", "format", "content", "caption", "alt_text"} == set(
        dumped["visual_assets"][-1]
    )


def test_model_cannot_generate_source_figure_assets() -> None:
    with pytest.raises(ValidationError):
        _output(
            visual_assets=[
                {
                    "asset_id": "x",
                    "format": "source_figure",
                    "content": str(A),
                    "caption": "",
                    "alt_text": "",
                }
            ]
        )


def test_remove_source_figures_drops_asset_and_tag() -> None:
    output = _output()
    fuse_source_figures(output, REFS, max_items=4)
    remove_source_figures(output, {"SRC-aaaaaaaa"})
    assert "SRC-aaaaaaaa" not in [a.asset_id for a in output.visual_assets]
    assert "[FIG:SRC-aaaaaaaa]" not in output.sections[0].content
    assert "[FIG:SRC-bbbbbbbb]" in output.sections[0].content


def test_figure_mix_counts_sources_apart() -> None:
    output = _output()
    fuse_source_figures(output, REFS, max_items=4)
    mix = compute_figure_mix(output.visual_assets)
    assert mix.total == 1 and mix.formats == {"mermaid": 1}
    assert mix.sources == {"source_figure": 3}


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        (
            "Schema della fonte luminosa e del rivelatore.",
            "Schema della fonte luminosa e del rivelatore.",
        ),
        ("Schema del vibrometro. Fonte: Rossi, 2020", "Schema del vibrometro."),
        ("Schema (source: Wikipedia)", "Schema"),
        ("Grafico; tratto da Bianchi 2019", "Grafico"),
        ("Foto del banco © Ateneo", "Foto del banco"),
        ("Schema del banco di misura (Rossi et al., 2019)", "Schema del banco di misura"),
        ("Diagramma del sensore. Quelle: Müller 2018", "Diagramma del sensore."),
        ("传感器示意图。出典\uff1a山田 2019", "传感器示意图。"),
        ("Schema del circuito (c) ACME", "Schema del circuito"),
        # Solo fonte: resta com'è (meglio una fonte ripetuta che niente).
        ("Adapted from Smith (2020)", "Adapted from Smith (2020)"),
        # Falsi positivi della prima versione: il testo legittimo resta.
        (
            "Andamento della tensione nel tratto da A a B.",
            "Andamento della tensione nel tratto da A a B.",
        ),
        ("Energy sources: solar and wind", "Energy sources: solar and wind"),
        ("Resources: CPU and memory", "Resources: CPU and memory"),
        (
            "Carico adattato da un trasformatore di impedenza",
            "Carico adattato da un trasformatore di impedenza",
        ),
        (
            "Confronto delle fonti: rumore termico e shot",
            "Confronto delle fonti: rumore termico e shot",
        ),
        ("Curva di risposta (1 kHz, 2019)", "Curva di risposta (1 kHz, 2019)"),
    ],
)
def test_clean_caption(caption: str, expected: str) -> None:
    assert clean_caption(caption)[0] == expected


def test_dropping_tags_leaves_untouched_fields_identical() -> None:
    """Un campo senza tag tolti torna identico (anche le righe vuote
    multiple, per esempio in un blocco di codice); dove una riga di solo tag
    sparisce, le righe vuote intorno si riducono a una."""
    from app.services.source_figure_fusion import _drop_tags

    code = "```python\nimport os\n\n\ndef f():\n    pass\n```"
    assert _drop_tags(code, {"src-aa"}) == code
    text = "Intro.\n\n[FIG:SRC-aa]\n\nTesto.\n\n\nAltro."
    assert _drop_tags(text, {"src-aa"}) == "Intro.\n\nTesto.\n\n\nAltro."
    assert _drop_tags("Vedi [FIG:SRC-aa] qui.", {"src-aa"}) == "Vedi  qui."


def test_alt_text_loses_the_source_tail_too() -> None:
    output = _output()
    output.source_figures[0].alt_text = "Schema del vibrometro. Fonte: Rossi, 2020"
    fuse_source_figures(output, REFS, max_items=4)
    alts = [a.alt_text for a in output.visual_assets if a.format == "source_figure"]
    assert "Schema del vibrometro." in alts
    assert not any("Rossi" in alt for alt in alts)
