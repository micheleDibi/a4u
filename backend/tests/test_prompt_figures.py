"""WP3 — le quattro famiglie di figure nei prompt di Fase 3/4/5.

Test puri (nessun DB, nessuna rete): il blocco «FORMATI DELLE FIGURE» di
P3 elenca i tipi Mermaid ammessi ed esclusi (D8), le regole D5, lo schema
compatto di `FunctionFigureSpec` (D9) e tre esempi minimi che devono
superare i validatori reali del registro; P4 rinvia a Fase 3 senza
graffe; P5 vieta la lettura a voce delle sorgenti. Il testo dei prompt è
statico (A19): nessun test dipende da `available_formats()`.
"""

from __future__ import annotations

import shutil
import typing

import pytest

from app.schemas import figure_function as ff
from app.services import openai_lesson_content_service as content
from app.services import openai_lesson_slides_service as slides
from app.services import openai_lesson_speech_service as speech
from app.services.figure_compute.function_parse import FUNCTIONS
from app.services.figure_render_service import REGISTRY
from app.services.figure_theme import MERMAID_D8_TYPES, MERMAID_EXCLUDED_TYPES

FORMATS = ("mermaid", "vegalite", "dot", "function")


def _p3(**kwargs: object) -> str:
    return content._system_prompt("it", **kwargs)  # type: ignore[arg-type]


def _flat(text: str) -> str:
    """Testo con gli a capo dell'impaginazione collassati in spazi: le
    asserzioni sulle frasi non dipendono dalla larghezza delle righe."""
    return " ".join(text.split())


def _figure_block(prompt: str) -> str:
    return prompt[prompt.index("FORMATI DELLE FIGURE") : prompt.index("EQUAZIONI — ENUNCIATO")]


# ---------------------------------------------------------------------------
# P3 — tabella D8, tipi Mermaid, regole D5/D3
# ---------------------------------------------------------------------------


def test_p3_figure_block_names_the_four_formats_and_maps_content_to_them():
    block = _figure_block(_p3())
    for fmt in FORMATS:
        assert f"→ `{fmt}`" in block, fmt
    # Il blocco sta fra i requisiti degli asset e le equazioni, prima di
    # LINGUA (ordine verificato da test_prompt_register).
    prompt = _p3()
    assert prompt.index("REQUISITI — ASSET VISIVI") < prompt.index("FORMATI DELLE FIGURE")
    assert prompt.index("FORMATI DELLE FIGURE") < prompt.index("LINGUA — REGOLA TASSATIVA")


def test_p3_lists_mermaid_allowed_and_excluded_types():
    block = _figure_block(_p3())
    allowed = block[block.index("Tipi ammessi:") : block.index("Esclusi:")]
    excluded = block[block.index("Esclusi:") : block.index("Label in testo semplice")]
    for name in MERMAID_D8_TYPES:
        assert name in allowed, name
    for name in MERMAID_EXCLUDED_TYPES:
        assert name in excluded, name
    assert "journey" in excluded and "journey" not in allowed
    assert "%%{init}%%" in block


def test_p3_vegalite_rules_and_style():
    block = _flat(_figure_block(_p3()))
    for text in (
        "4000 caratteri",
        "≤ 200 righe",
        "`data.url`",
        "`data.name`",
        '`mark: "image"`',
        "`config`",
        "`params`",
        "`selection`",
        "`tooltip`",
        '`"clip": true`',
        "`scale.domain`",
        "una `title`",
        "Dati illustrativi, non sperimentali",
        "`axis.title` con l'unità di misura",
        "legenda solo con più serie",
        "NON si tracciano in Vega-Lite: usa `function`",
    ):
        assert text in block, text


def test_p3_dot_rules():
    block = _flat(_figure_block(_p3()))
    dot = block[block.index("DOT (Graphviz)") : block.index("FUNCTION (")]
    for text in ("`graph`, `digraph` o `strict`", "label brevi", "nessun colore", "`image`"):
        assert text in dot, text


def test_p3_caption_rule_forbids_figure_prefix():
    prompt = _p3()
    divieti = _flat(prompt[prompt.index("DIVIETI ASSOLUTI") : prompt.index("CASO SPECIALE")])
    assert '"Figura 1"/"Fig. 1"' in divieti
    assert "il numero lo mette il renderer" in divieti


def test_p3_lingua_covers_the_new_formats():
    prompt = _p3()
    lingua = _flat(prompt[prompt.index("LINGUA — REGOLA TASSATIVA") :])
    for text in (
        "`axis.title`",
        "`legend.title`",
        "`label` dei sorgenti DOT",
        "`expressions[].label`",
        "`annotations[].label`",
        "di Vega-Lite (chiavi JSON, `field`, `type`, espressioni `datum.*`)",
        "di DOT (ID dei nodi, `->`/`--`",
        "di `function` (chiavi JSON, `expr`, `kind`, `show`)",
    ):
        assert text in lingua, text


# ---------------------------------------------------------------------------
# P3 — schema compatto di FunctionFigureSpec (D9)
# ---------------------------------------------------------------------------


def _literal_values(alias: object) -> tuple[str, ...]:
    return typing.get_args(alias)


def test_p3_function_schema_lists_every_field_and_enum():
    schema = content._FUNCTION_SPEC_COMPACT
    assert schema in _p3()
    for name in ff.FunctionFigureSpec.model_fields:
        assert f'"{name}"' in schema, name
    for name in ff.ExpressionSpec.model_fields:
        assert f'"{name}"' in schema, name
    nested = (
        set(ff.TangentAnnotation.model_fields)
        | set(ff.AreaAnnotation.model_fields)
        | set(ff.PointAnnotation.model_fields)
        | set(ff.ParameterSpec.model_fields)
        | set(ff.SamplingSpec.model_fields)
    )
    for name in nested:
        assert f'"{name}"' in schema, name
    for value in _literal_values(ff.FunctionKind):
        assert value in schema, value
    for value in _literal_values(ff.ShowItem):
        assert f'"{value}"' in schema, value
    for kind in ("tangent", "area", "point"):
        assert f'"kind":"{kind}"' in schema or f'"{kind}"' in schema, kind
    prose = _flat(_figure_block(_p3()))
    for fn in FUNCTIONS:
        assert fn in prose, fn
    for text in ("`**` (mai `^`)", "`2*x` (mai `2x`)", "NON scrivere numeri calcolati"):
        assert text in prose, text


# ---------------------------------------------------------------------------
# P3 — gli esempi passano i validatori del registro
# ---------------------------------------------------------------------------


def test_p3_examples_are_in_the_prompt_and_within_budget():
    prompt = _p3()
    assert len(content._VEGALITE_EXAMPLE) <= 300
    assert len(content._DOT_EXAMPLE) <= 150
    for example in (content._VEGALITE_EXAMPLE, content._DOT_EXAMPLE, content._FUNCTION_EXAMPLE):
        assert example in prompt
    # Esempio Vega-Lite: barre con dati inline, clip e scale.domain.
    assert '"values":[' in content._VEGALITE_EXAMPLE
    assert '"type":"bar","clip":true' in content._VEGALITE_EXAMPLE
    assert '"scale":{"domain":[' in content._VEGALITE_EXAMPLE
    assert '"axis":{"title":"Precipitazioni (mm)"}' in content._VEGALITE_EXAMPLE


def test_p3_vegalite_example_passes_schema_and_d5_rules():
    pytest.importorskip("jsonschema")
    pytest.importorskip("altair")
    ok, error = REGISTRY["vegalite"].validate(content._VEGALITE_EXAMPLE)
    assert ok, error


@pytest.mark.skipif(shutil.which("dot") is None, reason="binario graphviz `dot` assente")
def test_p3_dot_example_passes_the_static_gate():
    ok, error = REGISTRY["dot"].validate(content._DOT_EXAMPLE)
    assert ok, error


def test_p3_function_example_is_a_valid_spec():
    spec, issues = ff.parse_function_spec(content._FUNCTION_EXAMPLE)
    assert spec is not None, issues
    assert spec.kind == "function_study"
    assert "zeros" in spec.show and "formula" in spec.show
    assert spec.annotations and spec.annotations[0].kind == "point"


# ---------------------------------------------------------------------------
# P4 e P5 — rinvii testuali senza graffe, divieto di lettura
# ---------------------------------------------------------------------------


def test_p4_refers_to_phase3_formats_without_braces_or_image_prompt():
    for prompt in (
        slides._system_prompt("it"),
        slides._system_prompt("it", minuti_per_lezione=45, livello_eqf="EQF 6"),
    ):
        assert "{" not in prompt and "}" not in prompt
        assert "image_prompt" not in prompt
        flat = _flat(prompt)
        assert "figure Mermaid, Vega-Lite, DOT o `function` e immagini" in flat
        assert "STESSI formati, regole e limiti di Fase 3" in flat
        for fmt in ("`mermaid`", "`vegalite`", "`dot`"):
            assert fmt in flat, fmt
        assert "UNA SLIDE DEDICATA PER OGNI ASSET VISIVO" in prompt
        assert "VINCOLI DI VALIDAZIONE" in prompt
        lingua = _flat(prompt[prompt.index("LINGUA — REGOLA TASSATIVA") :])
        assert "`axis.title`" in lingua and "`label` dei sorgenti DOT" in lingua
        assert "di DOT (ID dei nodi, `->`/`--`" in lingua


def test_p5_forbids_reading_sources_aloud():
    prompt = _flat(speech._system_prompt("it", minuti_per_lezione=45))
    assert "MAI leggere codice Mermaid, spec JSON Vega-Lite/function, sorgente DOT" in prompt
