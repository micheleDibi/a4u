"""Test puri (senza DB, senza Chromium) per `scripts/revalidate_mermaid_assets.py`.

Coprono il gate statico D8, l'estrazione degli asset dai JSONB, il conteggio
degli asset non citati e la valutazione con un renderer finto.
"""

from __future__ import annotations

import pytest

from app.services import figure_theme as theme
from scripts.revalidate_mermaid_assets import (
    AssetResult,
    MermaidAsset,
    collect_mermaid_assets,
    declared_type,
    evaluate_assets,
    first_meaningful_line,
    result_rows,
    static_gate,
    summary_rows,
    uncited_asset_ids,
)

# ---------------------------------------------------------------------------
# Gate statico
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", list(theme.MERMAID_D8_SAMPLES.values()))
def test_static_gate_accepts_d8_samples(code: str):
    assert static_gate(code) == ""


def test_static_gate_accepts_legacy_aliases_and_skips_comments_and_frontmatter():
    assert static_gate("%% commento\ngraph TD\n  A --> B") == ""
    assert static_gate("stateDiagram\n  [*] --> A") == ""
    assert static_gate("---\ntitle: Schema\n---\nflowchart LR\n  A --> B") == ""
    assert first_meaningful_line("\n\n%% c\n  flowchart LR\n A") == "flowchart LR"
    assert declared_type("---\ntitle: x\n---\n\nsequenceDiagram\n A->>B: c") == "sequenceDiagram"


@pytest.mark.parametrize("kind", theme.MERMAID_EXCLUDED_TYPES)
def test_static_gate_rejects_excluded_types(kind: str):
    assert static_gate(f"{kind}\n  x") == f"mermaid_type_not_allowed:{kind}"


def test_static_gate_rejects_unknown_empty_init_and_html():
    assert static_gate("") == "mermaid_empty"
    assert static_gate("   \n%% solo commento") == "mermaid_type_not_allowed:?"
    assert static_gate("requirementDiagram\n x") == "mermaid_type_not_allowed:requirementDiagram"
    assert (
        static_gate("%%{init: {'theme': 'dark'}}%%\nflowchart LR\n A --> B")
        == "mermaid_init_directive"
    )
    assert static_gate("flowchart LR\n  A[<b>x</b>] --> B") == "mermaid_html_in_label"
    assert (
        static_gate('flowchart LR\n  A@{ img: "http://x/y.png" } --> B')
        == "mermaid_external_resource"
    )
    # `<br>` è la sintassi di a capo di Mermaid, non HTML: nessun falso
    # positivo nel report L5 (REG-1).
    assert static_gate("flowchart LR\n  A[Riga 1<br>Riga 2] --> B") == ""
    # Frecce e annotazioni con `<` non sono tag HTML.
    assert static_gate("classDiagram\n  class A {\n    <<interface>>\n  }\n  A <|-- B") == ""
    assert static_gate("sequenceDiagram\n  A->>B: x\n  B-->>A: y") == ""


# ---------------------------------------------------------------------------
# Estrazione e asset non citati
# ---------------------------------------------------------------------------


def _rec(content_raw=None, slides_raw=None):
    return {
        "course_id": "c1",
        "course_title": "Analisi",
        "lesson_code": "M1.L1",
        "content_raw": content_raw,
        "slides_raw": slides_raw,
    }


def test_collect_mermaid_assets_from_both_sources_in_order():
    rec = _rec(
        content_raw={
            "visual_assets": [
                {"asset_id": "F1", "format": "mermaid", "content": "flowchart LR\n A --> B"},
                {"asset_id": "F2", "format": "image", "content": "x.png"},
                "spazzatura",
            ]
        },
        slides_raw={
            "new_assets": [
                {"asset_id": "S1", "format": "mermaid", "content": 'pie\n "a" : 1'},
            ]
        },
    )
    assets = collect_mermaid_assets(rec)
    assert [(a.source, a.asset_id) for a in assets] == [
        ("content_raw", "F1"),
        ("slides_raw", "S1"),
    ]
    assert assets[0].course == "Analisi" and assets[0].lesson_code == "M1.L1"
    assert collect_mermaid_assets(_rec()) == []


def test_uncited_asset_ids_uses_body_only_and_is_case_insensitive_on_ids():
    content = {
        "introduction": "Vedi [FIG:a1].",
        "sections": [{"content": "E anche [FIG:B2] qui."}],
        "summary": "Fine.",
        "key_takeaways": ["Citazione ignorata: [FIG:c3]"],
        "visual_assets": [
            {"asset_id": "A1"},
            {"asset_id": "b2"},
            {"asset_id": "c3"},
            {"asset_id": "d4"},
        ],
    }
    assert uncited_asset_ids(content) == ["c3", "d4"]
    assert uncited_asset_ids(None) == []
    # `[fig:x]` minuscolo non è un tag (mirror di `_ASSET_REF_RE`).
    assert uncited_asset_ids({"introduction": "[fig:z9]", "visual_assets": [{"asset_id": "z9"}]})


# ---------------------------------------------------------------------------
# Valutazione con renderer finto e report
# ---------------------------------------------------------------------------


def _asset(asset_id: str, content: str) -> MermaidAsset:
    return MermaidAsset("Analisi", "c1", "M1.L1", "content_raw", asset_id, content)


def test_evaluate_assets_static_then_render_with_fake_batch():
    calls: list[list[str]] = []

    def fake_batch(codes: list[str]) -> list[str | None]:
        calls.append(codes)
        out: list[str | None] = []
        for c in codes:
            if c.startswith("pie"):
                out.append(None)
            elif c.startswith("gantt"):
                out.append("<svg><foreignObject/><foreignObject/></svg>")
            else:
                out.append("<svg><text>ok</text></svg>")
        return out

    assets = [
        _asset("ok", "```mermaid\nflowchart LR\n A --> B\n```"),
        _asset("journey", "journey\n title x"),
        _asset("fail", 'pie\n "a" : 1'),
        _asset("fo", "gantt\n title x"),
    ]
    results = evaluate_assets(assets, render=True, batch_size=2, render_batch=fake_batch)
    by_id = {r.asset.asset_id: r for r in results}
    assert by_id["ok"].ok and by_id["ok"].sanitized_changed and by_id["ok"].svg_bytes > 0
    assert by_id["journey"].error == "mermaid_type_not_allowed:journey"
    assert by_id["fail"].error == "render_failed"
    assert by_id["fo"].error == "foreign_object:2" and by_id["fo"].foreign_objects == 2
    # Solo i tre asset che passano il gate arrivano al render, in batch da 2.
    assert [len(c) for c in calls] == [2, 1]
    assert calls[0][0] == "flowchart LR\n A --> B"

    # Senza render: gate statico soltanto, nessuna chiamata.
    calls.clear()
    results = evaluate_assets(assets, render=False, render_batch=fake_batch)
    assert calls == [] and [r.ok for r in results] == [True, False, True, True]


def test_report_rows_and_summary():
    results = [
        AssetResult(_asset("a", "flowchart LR\n A"), ok=True, error=""),
        AssetResult(_asset("b", "journey"), ok=False, error="mermaid_type_not_allowed:journey"),
        AssetResult(_asset("c", "flowchart LR\n A"), ok=False, error="foreign_object:1"),
    ]
    rows = result_rows(results, show_ok=False)
    assert [r["asset_id"] for r in rows] == ["b", "c"]
    assert rows[0]["tipo"] == "journey" and rows[0]["esito"] == "da correggere"
    assert len(result_rows(results, show_ok=True)) == 3

    summary = summary_rows(
        results, lessons=5, lessons_with_uncited=2, uncited_assets=3, rendered=True
    )
    values = {r["voce"]: r["valore"] for r in summary}
    assert values["asset mermaid totali"] == 3
    assert values["ok"] == 1 and values["da correggere"] == 2
    assert values["  di cui foreign_object"] == 1
    assert values["  di cui mermaid_type_not_allowed"] == 1
    assert values["lezioni con asset non citati (A12)"] == 2
    assert values["asset non citati totali"] == 3
