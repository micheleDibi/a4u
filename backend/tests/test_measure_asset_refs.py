"""Test puri (senza DB) per `scripts/measure_asset_refs.py`.

L'import `from scripts.measure_asset_refs import ...` funziona come in
`test_measure_register.py` (rootdir=backend, `scripts/` namespace package).
Lo script è diagnostico: i test pinnano che le sue definizioni restino
allineate a quelle di produzione (regex dei tag, id dichiarati per kind,
famiglia del teorema, ordine di accodamento D3) e che tabelle, equazioni
ed esempi dichiarati non siano più contati come «tag senza asset».
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import figure_numbering
from app.services.course_lesson_pdf_service import _asset_ids_by_kind
from scripts import measure_asset_refs as mar

CONTENT: dict = {
    "introduction": "Come in [FIG:A], vedi [TAB:t1].\n\n[FIG:A]",
    "sections": [
        {
            "title": "S",
            "content": "- [EQ:e1]\n\n[EX:x1]\n\nVedi [TAB:ghost] e [EX:e1].",
        }
    ],
    "summary": "Fine [FIG:A].",
    "key_takeaways": ["Vedi [FIG:A]"],
    "references": [{"citation": "[TAB:t1]"}],
    "visual_assets": [
        {"asset_id": "A", "format": "mermaid", "content": "flowchart LR\n a --> b"},
        {"asset_id": "B", "format": "dot", "content": "digraph { a -> b }"},
    ],
    "tables": [
        {"table_id": "t1", "markdown": "|a|\n|-|\n|1|"},
        {"table_id": "t2", "markdown": "|b|\n|-|\n|2|"},
    ],
    "equations": [
        {"equation_id": "e1", "latex": "x"},
        {"equation_id": "L1", "kind": "lemma", "latex": "y", "statement": "Enunciato."},
        {"equation_id": "p1", "latex": "z", "proof": [{"text": "passo"}]},
        {"equation_id": "q1", "latex": "w", "proof": [{"text": "  "}]},
    ],
    "examples": [
        {"example_id": "x1", "title": "T", "content": "c [FIG:B]"},
        {"example_id": "e1", "title": "U", "content": "d"},
    ],
}


def test_tag_regex_matches_production() -> None:
    assert mar.TAG_RE.pattern == figure_numbering.ASSET_REF_RE.pattern


def test_declared_assets_mirror_pdf_ids_by_kind() -> None:
    declared = mar.declared_assets(CONTENT)
    expected = [
        (kind, mar.norm_id(asset_id))
        for kind, ids in _asset_ids_by_kind(CONTENT).items()
        for asset_id in ids
    ]
    # Stesse coppie, stesso ordine (FIG → TAB → EQ → EX, poi ordine dell'array).
    assert list(declared) == expected
    assert [kind for kind, _ in declared] == sorted(
        (kind for kind, _ in declared), key=figure_numbering.ASSET_KINDS.index
    )
    # Lo stesso id in kind diversi non collide.
    assert ("EQ", "e1") in declared and ("EX", "e1") in declared
    assert declared[("EQ", "e1")] is not declared[("EX", "e1")]


def test_declared_assets_skips_empty_ids_and_non_dicts() -> None:
    content = {
        "visual_assets": [{"asset_id": ""}, "x", {"asset_id": " A "}, {"asset_id": "a"}],
        "tables": None,
    }
    declared = mar.declared_assets(content)
    assert list(declared) == [("FIG", "a")]
    assert declared[("FIG", "a")] == {"asset_id": " A "}  # vince il primo


@pytest.mark.parametrize("eq", CONTENT["equations"])
def test_equation_family_mirrors_predicate(eq: dict) -> None:
    expected = "teorema" if figure_numbering.equation_label_family(eq) == "THM" else "equazione"
    assert mar.equation_family(eq) == expected


def test_asset_family_for_each_kind() -> None:
    declared = mar.declared_assets(CONTENT)
    assert mar.asset_family(("FIG", "a"), declared) == "mermaid"
    assert mar.asset_family(("TAB", "t1"), declared) == "tabella"
    assert mar.asset_family(("EQ", "e1"), declared) == "equazione"
    assert mar.asset_family(("EQ", "l1"), declared) == "teorema"
    assert mar.asset_family(("EX", "x1"), declared) == "esempio"
    assert mar.asset_family(("TAB", "ghost"), declared) == "ASSENTE"
    assert mar.asset_family(("FIG", "t1"), declared) == "ASSENTE"  # kind diverso


def _write_export(tmp_path: Path) -> Path:
    lesson2 = {
        "introduction": "Solo testo",
        "sections": [],
        "summary": "",
        "visual_assets": [{"asset_id": "V", "format": "vegalite", "content": '{"mark": "bar"}'}],
        "tables": [{"table_id": "t9", "markdown": "|c|\n|-|\n|3|"}],
    }
    rows = [
        {"id": "1", "lesson_code": "M1.L1", "course_title": "Corso", "content_raw": CONTENT},
        {"id": "2", "lesson_code": "M1.L2", "content_raw": json.dumps(lesson2)},
    ]
    path = tmp_path / "export.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_report_declared_tables_equations_examples_are_not_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert mar.main([str(_write_export(tmp_path))]) == 0
    out = capsys.readouterr().out
    lesson1, rest = out.split("## Lezione M1.L2")
    # Dichiarati: famiglia al posto di «ASSENTE».
    assert "| t1 | TAB | tabella |" in lesson1
    assert "| e1 | EQ | equazione |" in lesson1
    assert "| x1 | EX | esempio |" in lesson1
    assert "| a | FIG | mermaid |" in lesson1
    # Stesso id in kind diversi: due righe distinte.
    assert "| e1 | EX | esempio |" in lesson1
    # Solo il tag non dichiarato è «senza asset».
    assert "| ghost | TAB | ASSENTE |" in lesson1
    assert "tag senza asset (blocco missing-asset): TAB:ghost\n" in lesson1
    assert "ASSENTE" not in lesson1.replace("| ghost | TAB | ASSENTE |", "")
    # Mai citati nel corpo, nell'ordine di accodamento D3 (B è citato solo
    # nella coda: il PDF lo accoda).
    assert "(accodati FIG → TAB → EQ → EX, A12/D3): FIG:b, TAB:t2, EQ:l1, EQ:p1, EQ:q1\n" in lesson1
    lesson2 = rest.split("## (b) Sintesi per lezione")[0]
    assert "(accodati FIG → TAB → EQ → EX, A12/D3): FIG:v, TAB:t9\n" in lesson2
    assert "tag senza asset (blocco missing-asset): -\n" in lesson2


def test_report_summary_counts_all_kinds_and_structure_lists_only_figures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert mar.main([str(_write_export(tmp_path))]) == 0
    out = capsys.readouterr().out
    summary = out.split("## (b) Sintesi per lezione")[1].split("## (b) Aggregato")[0]
    # M1.L1: 11 tag (FIG 5, TAB 3, EQ 1, EX 2), 7 coppie (kind, id) distinte,
    # 10 asset dichiarati (2+2+4+2), 8 in linea, 3 ancore, 3 in coda, 2 id
    # ripetuti, 5 mai citati nel corpo, 1 tag orfano. M1.L2: 2 dichiarati,
    # 2 mai citati (figura e tabella), nessun tag.
    assert "| M1.L1 | Corso | 11 | 7 | 10 | 5 | 3 | 1 | 2 | 8 | 3 | 3 | 2 | 5 | 1 |" in summary
    assert "| M1.L2 | - | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 |" in summary
    structure = out.split("## (d) Struttura delle figure")[1]
    rows = [ln for ln in structure.splitlines() if ln.startswith("| M1.")]
    assert [r.split(" | ")[1:3] for r in rows] == [
        ["a", "mermaid"],
        ["b", "dot"],
        ["v", "vegalite"],
    ]


def test_figures_option_renders_and_reports_the_thresholds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--figures` rende con il registro di produzione (Chromium per
    Mermaid, `dot`, vl-convert), riporta gli incroci (`hashTable` = 1) e la
    percentuale oltre ogni soglia di `graph_rules` con la regola di
    calibrazione."""
    from tests.test_frontend_figure_templates import DOT, MERMAID

    hash_table = next(t.code for t in DOT if t.id == "hashTable")
    dense = "flowchart TD\n" + "\n".join(f"  N{i} --> N{i + 1}" for i in range(39))
    content = {
        "introduction": "x",
        "sections": [],
        "summary": "",
        "visual_assets": [
            {"asset_id": "M", "format": "mermaid", "content": MERMAID[0].code},
            {"asset_id": "D", "format": "dot", "content": hash_table},
            {"asset_id": "G", "format": "mermaid", "content": dense},
            {"asset_id": "V", "format": "vegalite", "content": '{"title": "Titolo"}'},
            {"asset_id": "F", "format": "function", "content": "{}"},
        ],
    }
    path = tmp_path / "export.json"
    path.write_text(json.dumps([{"id": "9", "lesson_code": "M9.L9", "content_raw": content}]))
    assert mar.main([str(path), "--figures"]) == 0
    out = capsys.readouterr().out
    figures = out.split("## (e) Figure rese")[1].split("## (e) Soglie")[0]
    rows = {ln.split(" | ")[1]: ln.split(" | ") for ln in figures.splitlines() if "M9.L9" in ln}
    assert set(rows) == {"M", "D", "G", "V"}  # `function` non è una figura da misurare
    assert rows["D"][4:7] == ["si", "5", "4"] and rows["D"][11] == "1"
    assert rows["M"][11] == "0" and rows["G"][5] == "40"
    assert rows["V"][11] == "-"  # Vega-Lite: nessun arco, nessun incrocio
    thresholds = out.split("## (e) Soglie")[1]
    assert "| nodi | 30 | 3 | 5 | 5 | 33.0 | 40 | 33.3 % | alzare la soglia |" in thresholds
    assert "| incroci | 4 | 3 | 0 | 0 | 0.8 | 1 | 0.0 % | ok |" in thresholds
    assert "incroci non misurati: 0 su 3 grafi resi" in thresholds


class _GroupRecorder:
    """Renderer Mermaid finto: registra la dimensione di ogni batch e non
    misura gli incroci della figura `M1.L7`."""

    fmt = "mermaid"

    def __init__(self) -> None:
        self.sizes: list[int] = []

    def render_figure_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[object]:
        from app.services.figure_render_service import RenderedFigure
        from app.services.figure_scale import SvgMetrics

        self.sizes.append(len(contents))
        return [
            RenderedFigure(
                "<svg/>",
                SvgMetrics(10.0, 10.0, 1, "measured", crossings=None if "L7" in aid else 2),
            )
            for aid in asset_ids
        ]


def test_figures_option_measures_every_mermaid_figure_in_budget_safe_groups(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """V1-F3: l'intero export in un solo batch esauriva il tetto di
    segmenti della pagina e le ultime figure restavano senza incroci, in
    silenzio. Ora i Mermaid vanno a gruppi di `batch // figura` (3) e le
    misure mancanti sono contate sotto la tabella delle soglie."""
    from app.services import figure_geometry
    from app.services import figure_render_service as frs

    assert mar.measure_group_size() == (
        figure_geometry.MAX_BATCH_MEASURE_SEGMENTS // figure_geometry.MAX_MEASURE_SEGMENTS
    )
    assert mar.measure_group_size() == 3
    # DOT (giro 2, V2-F1): tetto di lavoro per batch, gruppi di 2 figure.
    assert mar.measure_group_size("dot") == (
        figure_geometry.MAX_BATCH_MEASURE_WORK // figure_geometry.MAX_MEASURE_WORK
    )
    assert mar.measure_group_size("dot") == 2
    assert mar.measure_group_size("vegalite") is None
    recorder = _GroupRecorder()
    monkeypatch.setitem(frs.REGISTRY, "mermaid", recorder)
    rows = [
        {
            "id": str(i),
            "lesson_code": f"M1.L{i}",
            "content_raw": {
                "visual_assets": [
                    {"asset_id": "f", "format": "mermaid", "content": "flowchart LR\n a --> b"}
                ]
            },
        }
        for i in range(8)
    ]
    path = tmp_path / "export.json"
    path.write_text(json.dumps(rows))
    assert mar.main([str(path), "--figures"]) == 0
    assert recorder.sizes == [3, 3, 2]
    out = capsys.readouterr().out
    thresholds = out.split("## (e) Soglie")[1]
    assert "| incroci | 4 | 7 | 2 | 2 | 2.0 | 2 | 0.0 % | ok |" in thresholds
    assert "incroci non misurati: 1 su 8 grafi resi" in thresholds
    monkeypatch.setattr(figure_geometry, "MAX_BATCH_MEASURE_SEGMENTS", 10)
    assert mar.measure_group_size() == 1
    dot_recorder = _GroupRecorder()
    monkeypatch.setitem(frs.REGISTRY, "dot", dot_recorder)
    for row in rows[:5]:
        row["content_raw"]["visual_assets"][0].update(format="dot", content="digraph { a -> b }")
    path.write_text(json.dumps(rows[:5]))
    assert mar.main([str(path), "--figures"]) == 0
    assert dot_recorder.sizes == [2, 2, 1]


def test_default_mode_uses_the_gate_counters() -> None:
    met = mar.asset_metrics({"format": "dot", "content": "digraph { a -> b -> c }"})
    assert (met["nodi"], met["archi"], met["incroci"]) == (3, 2, None)
    assert mar.asset_metrics({"format": "function", "content": "{}"})["nodi"] is None
