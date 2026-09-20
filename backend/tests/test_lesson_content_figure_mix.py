"""Mix dei formati e dei tipi di figura — misura, non gate.

Oracolo che mancava prima: sull'export reale del docente (18 settembre
2026, quattro lezioni di quattro corsi diversi) delle 14 figure generate
dal modello 13 erano `mermaid` flowchart e una `function`; zero
`vegalite`, zero `dot`, e dentro Mermaid nessun sequence, state, class,
er, mindmap, timeline. Per saperlo il docente ha dovuto riaprire le
lezioni a una a una: in produzione non restava traccia del mix.

Qui si prova che (a) il conteggio per formato e per tipo Mermaid è
quello giusto, (b) la materializzazione lo emette come
`lesson_content_figure_mix` senza bloccare nulla, (c) il warning di
monocultura scatta su tre flowchart e NON su due flowchart più un
grafico di funzione, (d) la sezione (c) di `measure_asset_refs` riporta
lo stesso mix su un export della stessa forma di quello reale (che resta
fuori dal repo).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import structlog

from app.services import course_lesson_content_service as content_svc
from app.services.figure_mix import (
    MONOCULTURE_MIN_FIGURES,
    UNKNOWN_MERMAID_TYPE,
    compute_figure_mix,
)
from scripts import measure_asset_refs as mar
from tests.course_builders import build_course, build_lesson_content_output, find_lesson

USAGE = {"total": 1, "prompt": 1, "completion": 0, "model": "gpt-5.5"}

FLOW = "flowchart LR\n  A[Passo uno] --> B[Passo due]"
SEQUENCE = "sequenceDiagram\n  Alice->>Bob: richiesta\n  Bob-->>Alice: risposta"
STATE = "stateDiagram-v2\n  [*] --> Attesa\n  Attesa --> Pronto"
DOT = 'digraph G { rankdir=LR; A [label="q0"]; B [label="q1"]; A -> B; }'
VEGALITE = json.dumps(
    {
        "data": {"values": [{"anno": "2024", "valore": 3}, {"anno": "2025", "valore": 5}]},
        "mark": {"type": "bar", "clip": True},
        "encoding": {
            "x": {"field": "anno", "type": "nominal"},
            "y": {"field": "valore", "type": "quantitative"},
        },
    }
)
FUNCTION = json.dumps(
    {
        "kind": "function_study",
        "expressions": [{"expr": "sin(1/x)", "label": "f"}],
        "variable": "x",
        "domain": [-1, 1],
        "show": ["discontinuities"],
    }
)


def _asset(asset_id: str, fmt: str, content: str) -> dict[str, Any]:
    return {"asset_id": asset_id, "format": fmt, "content": content, "caption": "Didascalia"}


# ---------------------------------------------------------------------------
# `compute_figure_mix` — funzione pura
# ---------------------------------------------------------------------------


def test_mix_counts_every_format_and_every_mermaid_type() -> None:
    mix = compute_figure_mix(
        [
            _asset("f1", "mermaid", FLOW),
            _asset("f2", "mermaid", SEQUENCE),
            _asset("f3", "vegalite", VEGALITE),
            _asset("f4", "dot", DOT),
            _asset("f5", "function", FUNCTION),
        ]
    )
    assert mix.total == 5
    assert mix.formats == {"mermaid": 2, "dot": 1, "function": 1, "vegalite": 1}
    assert mix.mermaid_types == {"flowchart": 1, "sequenceDiagram": 1}
    assert mix.monoculture is False
    assert mix.single_format is None


def test_mix_orders_by_count_then_name_so_two_equal_mixes_log_the_same_row() -> None:
    """L'ordine è parte del contratto: due lezioni con lo stesso mix devono
    produrre la stessa riga di log, altrimenti un diff fra due misure è
    rumore."""
    assets = [
        _asset("a", "mermaid", STATE),
        _asset("b", "mermaid", FLOW),
        _asset("c", "mermaid", FLOW),
        _asset("d", "dot", DOT),
    ]
    mix = compute_figure_mix(assets)
    assert list(mix.formats) == ["mermaid", "dot"]
    assert list(mix.mermaid_types) == ["flowchart", "stateDiagram-v2"]
    assert compute_figure_mix(list(reversed(assets))).mermaid_types == mix.mermaid_types


def test_mix_reads_pydantic_assets_and_raw_dicts_the_same_way() -> None:
    """La materializzazione ha i modelli Pydantic, lo script i dizionari di
    `content_raw`: la misura deve essere la stessa."""
    raw = [_asset("f1", "mermaid", FLOW), _asset("f2", "dot", DOT)]
    output = build_lesson_content_output(visual_assets=raw)
    assert compute_figure_mix(output.visual_assets) == compute_figure_mix(raw)


def test_mix_marks_an_unrecognised_mermaid_source_without_raising() -> None:
    """Un sorgente senza corpo (solo commenti) finisce sotto il tipo ignoto;
    uno che apre con un token qualsiasi è contato sotto quel token, come fa
    già `graph_rules`. In nessuno dei due casi la misura solleva: è
    diagnostica, non validazione."""
    assert compute_figure_mix([_asset("f1", "mermaid", "%% solo un commento")]).mermaid_types == {
        UNKNOWN_MERMAID_TYPE: 1
    }
    assert compute_figure_mix([_asset("f1", "mermaid", "nonEsiste A --> B")]).mermaid_types == {
        "nonEsiste": 1
    }


def test_mix_ignores_assets_without_a_format() -> None:
    mix = compute_figure_mix([{"asset_id": "f1", "content": FLOW}, _asset("f2", "dot", DOT)])
    assert mix.formats == {"dot": 1} and mix.total == 1


@pytest.mark.parametrize(
    ("assets", "expected"),
    [
        # Tre flowchart: un solo formato, un solo tipo → monocultura.
        ([("mermaid", FLOW)] * 3, True),
        # Due flowchart e un grafico di funzione: due formati → no.
        ([("mermaid", FLOW), ("mermaid", FLOW), ("function", FUNCTION)], False),
        # Due flowchart soli: sotto la soglia, una lezione corta non è una
        # monocultura.
        ([("mermaid", FLOW)] * 2, False),
        # Tre Mermaid di tre tipi diversi: un formato, tre tipi → no.
        ([("mermaid", FLOW), ("mermaid", SEQUENCE), ("mermaid", STATE)], False),
        # Tre Vega-Lite: un solo formato, nessun tipo Mermaid → monocultura.
        ([("vegalite", VEGALITE)] * 3, True),
    ],
)
def test_monoculture_predicate(assets: list[tuple[str, str]], expected: bool) -> None:
    mix = compute_figure_mix(
        [_asset(f"f{i}", fmt, src) for i, (fmt, src) in enumerate(assets)],
    )
    assert mix.monoculture is expected


def test_monoculture_threshold_is_the_documented_one() -> None:
    assert MONOCULTURE_MIN_FIGURES == 3


def test_the_single_mermaid_type_is_none_when_there_is_no_monoculture() -> None:
    """`single_mermaid_type` rispondeva «flowchart» anche a una lezione con
    quattro formati diversi, mentre `single_format` rispondeva `None` e
    `monoculture` era falsa. Oggi è letta solo dentro `if mix.monoculture`,
    ma il nome invita a usarla fuori: lì direbbe il contrario della
    misura."""
    vario = compute_figure_mix(
        [
            _asset("f1", "mermaid", FLOW),
            _asset("f2", "vegalite", VEGALITE),
            _asset("f3", "dot", DOT),
            _asset("f4", "function", FUNCTION),
        ]
    )
    assert vario.monoculture is False
    assert vario.single_format is None
    assert vario.single_mermaid_type is None
    # Dentro la monocultura il valore è quello di prima: il tipo unico.
    mono = compute_figure_mix([_asset(f"f{i}", "mermaid", FLOW) for i in range(3)])
    assert (mono.single_format, mono.single_mermaid_type) == ("mermaid", "flowchart")
    # Formato unico non-Mermaid: nessun tipo da dichiarare.
    dot_only = compute_figure_mix([_asset(f"f{i}", "dot", DOT) for i in range(3)])
    assert (dot_only.single_format, dot_only.single_mermaid_type) == ("dot", None)


# ---------------------------------------------------------------------------
# Materializzazione — log strutturato
# ---------------------------------------------------------------------------


async def _materialize(db: Any, assets: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    course = await content_svc.load_course_full(db, course_id=course_id)
    lesson = find_lesson(course, "M1.L1")
    lesson.learning_objectives = ["Comprendere l'argomento"]
    lesson.mandatory_topics = [{"topic_id": "T1", "topic": "Argomento", "rationale": "x"}]
    await db.flush()
    tags = "\n\n".join(f"[FIG:{a['asset_id']}]" for a in assets)
    output = build_lesson_content_output(
        visual_assets=assets,
        introduction=f"Introduzione.\n\n{tags}\n",
    )
    with structlog.testing.capture_logs() as logs:
        await content_svc.materialize_lesson_content(
            db, course=course, lesson=lesson, output=output, raw=output.model_dump(), usage=USAGE
        )
    return lesson, logs


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == name]


async def test_materialization_logs_the_mix_of_four_formats(seeded_db: Any) -> None:
    lesson, logs = await _materialize(
        seeded_db,
        [
            _asset("f1", "mermaid", FLOW),
            _asset("f2", "mermaid", SEQUENCE),
            _asset("f3", "vegalite", VEGALITE),
            _asset("f4", "dot", DOT),
            _asset("f5", "function", FUNCTION),
        ],
    )
    assert lesson.content_status == "ready"
    (event,) = _events(logs, "lesson_content_figure_mix")
    assert event["lesson_code"] == "M1.L1"
    assert event["figures"] == 5
    assert event["formats"] == {"mermaid": 2, "dot": 1, "function": 1, "vegalite": 1}
    assert event["mermaid_types"] == {"flowchart": 1, "sequenceDiagram": 1}
    assert _events(logs, "lesson_content_figure_monoculture") == []


async def test_three_flowcharts_warn_and_the_lesson_is_still_materialized(
    seeded_db: Any,
) -> None:
    """Il warning è diagnostico: la lezione resta `ready`. È la forma
    misurata in produzione su tre corsi su quattro."""
    lesson, logs = await _materialize(
        seeded_db,
        [_asset(f"f{i}", "mermaid", FLOW) for i in range(3)],
    )
    assert lesson.content_status == "ready"
    (mix_event,) = _events(logs, "lesson_content_figure_mix")
    assert mix_event["formats"] == {"mermaid": 3}
    assert mix_event["mermaid_types"] == {"flowchart": 3}
    (warning,) = _events(logs, "lesson_content_figure_monoculture")
    assert warning["log_level"] == "warning"
    assert (warning["figures"], warning["format"], warning["mermaid_type"]) == (
        3,
        "mermaid",
        "flowchart",
    )


async def test_two_flowcharts_and_a_function_graph_do_not_warn(seeded_db: Any) -> None:
    _lesson, logs = await _materialize(
        seeded_db,
        [
            _asset("f1", "mermaid", FLOW),
            _asset("f2", "mermaid", FLOW),
            _asset("f3", "function", FUNCTION),
        ],
    )
    (mix_event,) = _events(logs, "lesson_content_figure_mix")
    assert mix_event["formats"] == {"mermaid": 2, "function": 1}
    assert _events(logs, "lesson_content_figure_monoculture") == []


async def test_a_lesson_without_figures_logs_an_empty_mix(seeded_db: Any) -> None:
    _lesson, logs = await _materialize(seeded_db, [])
    (event,) = _events(logs, "lesson_content_figure_mix")
    assert (event["figures"], event["formats"], event["mermaid_types"]) == (0, {}, {})
    assert _events(logs, "lesson_content_figure_monoculture") == []


# ---------------------------------------------------------------------------
# `scripts/measure_asset_refs.py` — sezione (c)
# ---------------------------------------------------------------------------

# Stessa FORMA dell'export reale del docente (che resta fuori dal repo) e
# stesso mix misurato il 18 settembre 2026 sulle figure generate dal
# modello: M2.L2 un flowchart più una `function`, le altre tre lezioni
# quattro flowchart ciascuna. Totale 14 figure, 13 flowchart.
EXPORT_FIXTURE: list[dict[str, Any]] = [
    {
        "id": "1",
        "lesson_code": "M2.L2",
        "course_title": "Analisi",
        "content_raw": {
            "visual_assets": [
                _asset("fig_avvicinamenti", "mermaid", FLOW),
                _asset("fig_confronti", "function", FUNCTION),
            ]
        },
    },
    *(
        {
            "id": str(n + 2),
            "lesson_code": code,
            "course_title": title,
            # `content_raw` come STRINGA JSON: l'export di pgAdmin lo dà
            # così, e `load_rows` deve scioglierlo prima della misura.
            "content_raw": json.dumps(
                {"visual_assets": [_asset(f"fig_{i}", "mermaid", FLOW) for i in range(4)]}
            ),
            # Solo M4.L1 porta le slide, con la stessa perdita misurata
            # sull'export reale: quattro figure, tre referenziate. Le
            # altre due lezioni non hanno `slides_raw`, come succede in
            # un export preso a Fase 3 finita.
            **(
                {
                    "slides_raw": {
                        "slides": [
                            {"slide_id": f"s{i}", "references_assets": [f"fig_{i}"]}
                            for i in range(3)
                        ]
                    }
                }
                if code == "M4.L1"
                else {}
            ),
        }
        for n, (code, title) in enumerate(
            [("M4.L1", "Mercati"), ("M4.L3", "Misure"), ("M12.L7", "Campi")]
        )
    ),
]


def _write_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "export.json"
    path.write_text(json.dumps(EXPORT_FIXTURE), encoding="utf-8")
    return path


def test_script_section_c_reports_the_mix_per_lesson_and_aggregated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert mar.main([str(_write_fixture(tmp_path))]) == 0
    out = capsys.readouterr().out
    section = out.split("## (c) Mix dei formati e dei tipi, per lezione")[1]
    per_lesson = section.split("## (c) Mix aggregato — formati")[0]
    # A parità di conteggio l'ordine è alfabetico (contratto di `_sorted`).
    m2 = "| M2.L2 | Analisi | 2 | 2 | function 1, mermaid 1 | flowchart 1 | no | - |"
    assert m2 in per_lesson
    assert "| M4.L1 | Mercati | 4 | 1 | mermaid 4 | flowchart 4 | SI | 1 |" in per_lesson
    assert "| M12.L7 | Campi | 4 | 1 | mermaid 4 | flowchart 4 | SI | - |" in per_lesson

    formati = section.split("## (c) Mix aggregato — formati")[1].split("## (c) Mix aggregato")[0]
    assert "| mermaid | 13 | 93% |" in formati
    assert "| function | 1 | 7% |" in formati
    # Il difetto in una riga: zero Vega-Lite e zero DOT non compaiono.
    assert "vegalite" not in formati and "dot" not in formati

    tipi = section.split("## (c) Mix aggregato — tipi Mermaid")[1]
    assert "| flowchart | 13 | 100% |" in tipi
    assert "figure totali: 14; lezioni in monocultura" in tipi
    assert "un solo formato e un solo tipo): 3 su 4" in tipi


def test_script_section_c_counts_the_figures_that_never_reached_a_slide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """La colonna «senza slide» è la stessa misura del warning
    `lesson_slides_unreferenced_assets`, dal lato dell'export: sul file
    reale del docente M4.L1 ha quattro figure e solo tre referenziate, e
    quella quarta non arriva né alle slide né al video. Senza `slides_raw`
    la colonna resta «-»: la lezione non ha ancora una Fase 4, non ha
    perso nulla."""
    assert mar.main([str(_write_fixture(tmp_path))]) == 0
    out = capsys.readouterr().out
    per_lesson = out.split("## (c) Mix dei formati e dei tipi, per lezione")[1].split(
        "## (c) Mix aggregato — formati"
    )[0]
    assert "| senza slide |" in per_lesson
    assert "senza slide in M4.L1: fig_3" in per_lesson
    # Una sola lezione dell'export ha slide: le altre non sono elencate.
    assert per_lesson.count("senza slide in ") == 1
    # La funzione dice «niente da misurare» e «niente perso» in due modi
    # diversi: `None` contro lista vuota.
    content = {"visual_assets": [_asset("fig_1", "mermaid", FLOW)], "tables": []}
    assert mar.assets_without_slide(content, None) is None
    assert mar.assets_without_slide(content, {"slides": []}) == ["fig_1"]
    assert mar.assets_without_slide(content, {"slides": [{"references_assets": [" FIG_1 "]}]}) == []


def test_script_section_c_sits_before_the_structure_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """La sezione (c) sta fra l'aggregato (b) e la struttura (d): le due
    tabelle non si confondono quando si legge l'output a occhio."""
    assert mar.main([str(_write_fixture(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert out.index("## (b) Aggregato") < out.index("## (c) Mix dei formati")
    assert out.index("## (c) Mix aggregato — tipi Mermaid") < out.index(
        "## (d) Struttura delle figure"
    )


def test_script_and_materialization_share_one_measure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lo script non ha una sua copia dei contatori: chiama la funzione di
    produzione. Se un giorno divergessero, la misura sull'export del
    docente e i log direbbero due cose diverse sullo stesso corso."""
    assert mar.compute_figure_mix is compute_figure_mix
    assert mar.main([str(_write_fixture(tmp_path))]) == 0
    out = capsys.readouterr().out
    totals = compute_figure_mix(
        [
            a
            for row in mar.load_rows(_write_fixture(tmp_path))
            for a in (row.get("content_raw") or {}).get("visual_assets", [])
        ]
    )
    assert totals.formats == {"mermaid": 13, "function": 1}
    assert f"figure totali: {totals.total};" in out
