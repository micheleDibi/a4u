"""PROMPT 3 con e senza catalogo delle figure di fonte (I1, parte pura).

Senza catalogo lo schema è identico a prima (stessa costante o stesso
deepcopy) e `visual_assets` non cambia mai; con il catalogo compare solo
`source_figures`, obbligatorio, con l'enum degli id. La riga statica del
system prompt resta entro la guardia della variante peggiore (M6).
"""

from __future__ import annotations

import copy

from app.services import openai_lesson_content_service as content
from app.services.figure_render_service import RENDERABLE_FORMATS

MAX_SYSTEM_P3 = 31_500


def test_schema_without_catalog_is_unchanged() -> None:
    assert content.build_lesson_content_json_schema() is content.LESSON_CONTENT_JSON_SCHEMA
    with_ids = content.build_lesson_content_json_schema(
        objective_ids=["O1"], visual_formats=["mermaid", "dot"]
    )
    assert "source_figures" not in with_ids["schema"]["properties"]
    assert "source_figures" not in with_ids["schema"]["required"]


def test_schema_with_catalog_adds_only_source_figures() -> None:
    base = content.build_lesson_content_json_schema(
        objective_ids=["O1"], visual_formats=list(RENDERABLE_FORMATS)
    )
    schema = content.build_lesson_content_json_schema(
        objective_ids=["O1"],
        visual_formats=list(RENDERABLE_FORMATS),
        source_figure_refs=["SRC-aaaaaaaa", "SRC-bbbbbbbb"],
    )
    props = schema["schema"]["properties"]
    item = props["source_figures"]["items"]
    assert item["properties"]["figure"]["enum"] == ["SRC-aaaaaaaa", "SRC-bbbbbbbb"]
    assert item["required"] == ["figure", "caption", "alt_text"]
    assert item["additionalProperties"] is False
    assert schema["schema"]["required"][-1] == "source_figures"
    stripped = copy.deepcopy(schema)
    del stripped["schema"]["properties"]["source_figures"]
    stripped["schema"]["required"].remove("source_figures")
    assert stripped == base, "a parte source_figures lo schema è identico"
    assert props["visual_assets"] == base["schema"]["properties"]["visual_assets"]
    # La costante condivisa non è stata toccata.
    assert "source_figures" not in content.LESSON_CONTENT_JSON_SCHEMA["schema"]["properties"]


def test_system_prompt_line_and_worst_variant_guard() -> None:
    prompt = content._system_prompt("it")
    assert "FIGURE DI FONTE" in prompt and "`source_figures`" in prompt
    worst = max(
        len(
            content._system_prompt(
                lang,
                ruolo_docente="Ruoli di supporto e Tutoraggio",
                stile_insegnamento=stile,
                livello_eqf="Diploma di licenza conclusiva del I ciclo di istruzione",
                grounding_enabled=grounding,
                visual_formats=formats,
            )
            + content.REGENERATION_SUFFIX
        )
        for lang in ("it", "zh-cn")
        for stile in ("Collaborativo", "", "(non specificato)")
        for grounding in (True, False)
        for formats in (RENDERABLE_FORMATS, ("mermaid",), ())
    )
    assert worst <= MAX_SYSTEM_P3, worst
