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
