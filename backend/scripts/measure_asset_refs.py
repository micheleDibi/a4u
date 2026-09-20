"""Misura dei riferimenti agli asset nelle dispense — strumento DIAGNOSTICO, non un gate.

Input: un file JSON esportato da pgAdmin, array di oggetti
`{id, lesson_code, course_title?, content_raw, slides_raw?}` (`content_raw`
può essere un oggetto JSON o una stringa JSON: entrambi accettati; un
oggetto con chiave `rows` o `data` è accettato come involucro).

Produce tabelle markdown:
  (b) posizione, ordine e conteggio dei tag `[KIND:id]` per lezione e in
      aggregato:
      asset dichiarati = visual_assets[].asset_id (FIG), tables[].table_id
              (TAB), equations[].equation_id (EQ), examples[].example_id
              (EX), come `course_lesson_pdf_service._asset_ids_by_kind`;
              un asset è identificato dalla coppia (kind, id): lo stesso id
              in kind diversi non collide;
      corpo = introduction + sections[].content + summary (cfr.
              `course_lesson_pdf_service._build_lesson_body_markdown`);
      coda  = key_takeaways[] + references[].citation + examples[].content
              + tables[].markdown + equations[].explanation/statement (nel
              PDF la coda riceve solo rimandi testuali, mai blocchi: i tag
              lì vanno contati a parte);
      posizione: «ancora» se la riga, tolti i tag, resta vuota; «in_linea»
              altrimenti. «sequenza posizioni»: per (kind, id), con la
              domanda «la prima occorrenza è una citazione in linea?» (C2);
      «asset mai citati»: dichiarati e mai citati nel CORPO (una citazione
              nella sola coda non conta), elencati nell'ordine in cui
              `figure_numbering.append_uncited_asset_refs` li accoda al
              corpo (FIG → TAB → EQ → EX, poi ordine dell'array; A12, D3);
      «tag senza asset»: coppie (kind, id) citate (corpo o coda) ma non
              dichiarate (blocco missing-asset nel PDF);
  (c) mix dei FORMATI (`mermaid`, `vegalite`, `dot`, `function`) e, dentro
      Mermaid, dei TIPI di diagramma, per lezione e in aggregato con la
      quota di ciascuno; segnala le lezioni in «monocultura» (almeno
      `figure_mix.MONOCULTURE_MIN_FIGURES` figure, un solo formato e un
      solo tipo). Stessa funzione della materializzazione
      (`app.services.figure_mix.compute_figure_mix`, che in produzione
      emette la riga `lesson_content_figure_mix`): la misura sull'export e
      quella nei log non possono divergere. Con `slides_raw` nell'export,
      la colonna «senza slide» conta le figure e le tabelle di Fase 3 che
      nessuna slide referenzia (stessa misura del warning
      `lesson_slides_unreferenced_assets`): una figura scartata dal deck
      sparisce anche dal video;
  (d) per ogni visual_asset di formato mermaid/dot/vegalite: nodi, archi,
      etichetta più lunga, righe e caratteri contati sul sorgente con gli
      stessi contatori del gate editoriale
      (`figure_compute.graph_rules.graph_source_metrics`; per Vega-Lite i
      record di `data.values`); gli incroci solo con `--figures`;
  (e) con `--figures`: ogni figura mermaid/dot/vegalite è RESA con il
      registro di produzione (`figure_render_service.REGISTRY`: Chromium e
      CDN per Mermaid, binario `dot`, vl-convert) e la tabella riporta
      nodi, archi, etichetta, titolo, righe, caratteri, incroci arco × arco
      (`figure_geometry`: nella pagina del pre-render per Mermaid, in Python
      per DOT) e difetti di lettura; segue la distribuzione per metrica sui
      grafi (Mermaid e DOT: min, mediana, p90, max) con la percentuale che
      supererebbe ciascuna soglia di `graph_rules` e l'esito della regola
      di calibrazione: se il p90 supera il 60 % della soglia, la soglia si
      alza (mai si boccia il contenuto). I Mermaid sono resi a gruppi di
      `MAX_BATCH_MEASURE_SEGMENTS // MAX_MEASURE_SEGMENTS` figure (una
      pagina per gruppo), i DOT a gruppi di `MAX_BATCH_MEASURE_WORK //
      MAX_MEASURE_WORK`: il tetto del batch non salta mai una misura, vale
      solo quello per figura. Le misure degli incroci mancanti
      (tetto per figura, misura fallita) sono contate in una riga
      «incroci non misurati» sotto la tabella delle soglie, perché la riga
      «incroci» ha allora un `n` minore delle altre.

Sola lettura: nessun accesso al DB, nessuna scrittura. Senza `--figures`
bastano la libreria standard e `graph_rules` (modulo puro); con
`--figures` serve l'ambiente del backend (`JWT_SECRET`, dipendenze delle
figure, `DYLD_FALLBACK_LIBRARY_PATH` su macOS per WeasyPrint).

Uso (dalla cartella `backend/`)
-------------------------------

    python -m scripts.measure_asset_refs lessons_export.json
    python -m scripts.measure_asset_refs lessons_export.json --per-occorrenza
    python -m scripts.measure_asset_refs lessons_export.json --figures
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.figure_compute import graph_rules
from app.services.figure_mix import MONOCULTURE_MIN_FIGURES, compute_figure_mix

SCRIPT_VERSION = "3"

# Allineato a `course_lesson_pdf_service._ASSET_REF_RE` (case-sensitive sul
# kind, il tag non attraversa la riga).
TAG_RE = re.compile(r"\[(FIG|TAB|EQ|EX):([^\]\n]+)\]")
KINDS = ("FIG", "TAB", "EQ", "EX")

# Campo e chiave dell'id per ogni kind, come
# `course_lesson_pdf_service._asset_ids_by_kind`; l'ordine dei kind è quello
# in cui `figure_numbering.append_uncited_asset_refs` accoda gli asset mai
# citati (D3).
DECLARED_FIELDS: dict[str, tuple[str, str]] = {
    "FIG": ("visual_assets", "asset_id"),
    "TAB": ("tables", "table_id"),
    "EQ": ("equations", "equation_id"),
    "EX": ("examples", "example_id"),
}

AssetKey = tuple[str, str]  # (KIND, id normalizzato)


def norm_id(value: object) -> str:
    """Come `figure_numbering._norm`."""
    return str(value or "").strip().lower()


def declared_assets(content: dict[str, Any]) -> dict[AssetKey, dict[str, Any]]:
    """`{(KIND, id_lower): item}` degli asset dichiarati, nell'ordine
    FIG → TAB → EQ → EX e, dentro il kind, dell'array; id vuoti ignorati,
    duplicati: vince il primo."""
    out: dict[AssetKey, dict[str, Any]] = {}
    for kind, (field, id_field) in DECLARED_FIELDS.items():
        for item in content.get(field) or []:
            if not isinstance(item, dict):
                continue
            key = (kind, norm_id(item.get(id_field)))
            if key[1]:
                out.setdefault(key, item)
    return out


def equation_family(eq: dict[str, Any]) -> str:
    """`teorema` se `statement` o almeno un passo di `proof` non è vuoto,
    altrimenti `equazione` (predicato di
    `figure_numbering.equation_label_family`)."""
    if str(eq.get("statement") or "").strip():
        return "teorema"
    for step in eq.get("proof") or []:
        if isinstance(step, dict) and (
            str(step.get("latex") or "").strip() or str(step.get("text") or "").strip()
        ):
            return "teorema"
    return "equazione"


def asset_family(key: AssetKey, declared: Mapping[AssetKey, dict[str, Any]]) -> str:
    """Colonna «asset» della tabella per id: formato della figura,
    `tabella`, `equazione` / `teorema`, `esempio`; `ASSENTE` se la coppia
    (kind, id) non è dichiarata."""
    item = declared.get(key)
    if item is None:
        return "ASSENTE"
    kind = key[0]
    if kind == "FIG":
        return str(item.get("format") or "?")
    if kind == "TAB":
        return "tabella"
    if kind == "EQ":
        return equation_family(item)
    return "esempio"


def format_keys(keys: Iterable[AssetKey]) -> str:
    """`FIG:a, TAB:t1` oppure `-` se vuoto."""
    return ", ".join(f"{kind}:{asset_id}" for kind, asset_id in keys) or "-"


@dataclass(frozen=True)
class Occurrence:
    """Una occorrenza di tag: zona (`corpo` | `coda`), campo (es.
    `sections[2].content`), riga 1-based e colonna 0-based dentro il campo,
    kind, id normalizzato, posizione (`in_linea` | `ancora`)."""

    zone: str
    field: str
    line: int
    col: int
    kind: str
    asset_id: str
    position: str


def body_parts(content: dict[str, Any]) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = [("introduction", content.get("introduction") or "")]
    for i, section in enumerate(content.get("sections") or []):
        if isinstance(section, dict):
            parts.append((f"sections[{i}].content", section.get("content") or ""))
    parts.append(("summary", content.get("summary") or ""))
    return parts


def tail_parts(content: dict[str, Any]) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    for i, item in enumerate(content.get("key_takeaways") or []):
        parts.append((f"key_takeaways[{i}]", item if isinstance(item, str) else ""))
    for i, ref in enumerate(content.get("references") or []):
        if isinstance(ref, dict):
            parts.append((f"references[{i}].citation", ref.get("citation") or ""))
    for i, ex in enumerate(content.get("examples") or []):
        if isinstance(ex, dict):
            parts.append((f"examples[{i}].content", ex.get("content") or ""))
    for i, tab in enumerate(content.get("tables") or []):
        if isinstance(tab, dict):
            parts.append((f"tables[{i}].markdown", tab.get("markdown") or ""))
    for i, eq in enumerate(content.get("equations") or []):
        if isinstance(eq, dict):
            parts.append((f"equations[{i}].explanation", eq.get("explanation") or ""))
            parts.append((f"equations[{i}].statement", eq.get("statement") or ""))
    return parts


def scan(parts: list[tuple[str, str]], zone: str) -> list[Occurrence]:
    out: list[Occurrence] = []
    for field, text in parts:
        for lineno, line in enumerate((text or "").split("\n"), start=1):
            stripped = line.strip()
            only_tags = bool(stripped) and not TAG_RE.sub("", stripped).strip()
            for m in TAG_RE.finditer(line):
                out.append(
                    Occurrence(
                        zone=zone,
                        field=field,
                        line=lineno,
                        col=m.start(),
                        kind=m.group(1),
                        asset_id=norm_id(m.group(2)),
                        position="ancora" if only_tags else "in_linea",
                    )
                )
    return out


def occurrences(content: dict[str, Any]) -> list[Occurrence]:
    return scan(body_parts(content), "corpo") + scan(tail_parts(content), "coda")


# ---------------------------------------------------------------------------
# (d) metriche strutturali dei sorgenti figura (euristiche testuali)
# ---------------------------------------------------------------------------

FIGURE_FORMATS = ("mermaid", "dot", "vegalite")


def _empty_metrics(kind: str) -> dict[str, Any]:
    return {"tipo": kind, "nodi": None, "archi": None, "label": None}


def metrics_graph(fmt: str, src: str) -> dict[str, Any]:
    """Contatori del gate editoriale (`graph_rules`) su un sorgente
    Mermaid o DOT."""
    met = graph_rules.graph_source_metrics(fmt, src.strip())
    if met is None:
        return _empty_metrics(fmt)
    return {
        "tipo": met.kind,
        "nodi": met.nodes,
        "archi": met.edges,
        "label": met.label_chars,
        "titolo": met.title_chars,
        "righe": met.lines,
    }


def _data_values(spec: dict[str, Any]) -> list[Any]:
    data = spec.get("data")
    values = data.get("values") if isinstance(data, dict) else None
    return values if isinstance(values, list) else []


def metrics_vegalite(src: str) -> dict[str, Any]:
    try:
        spec = json.loads(src)
    except (TypeError, ValueError):
        return _empty_metrics("vegalite")
    if not isinstance(spec, dict):
        return _empty_metrics("vegalite")
    layers = (
        spec.get("layer") or spec.get("hconcat") or spec.get("vconcat") or spec.get("concat") or []
    )
    values = _data_values(spec)
    if not values and isinstance(layers, list):
        for sub in layers:
            if isinstance(sub, dict):
                values = values or _data_values(sub)
    enc = spec.get("encoding") or {}
    return {
        "tipo": "vegalite",
        "nodi": len(values),  # punti dati
        "archi": len(layers) if isinstance(layers, list) else 0,  # layer/concat
        "label": len(enc) if isinstance(enc, dict) else None,  # canali di encoding
    }


def asset_metrics(asset: dict[str, Any]) -> dict[str, Any]:
    fmt = str(asset.get("format") or "")
    src = asset.get("content") or ""
    if not isinstance(src, str):
        src = json.dumps(src, ensure_ascii=False)
    if fmt in graph_rules.GRAPH_FORMATS:
        met = metrics_graph(fmt, src)
    elif fmt == "vegalite":
        met = metrics_vegalite(src)
    else:  # function, image, formati legacy: non sono grafi
        met = _empty_metrics(fmt or "?")
    met["formato"] = fmt
    met["len_sorgente"] = len(src)
    met["incroci"] = None  # solo con `--figures` (serve la figura resa)
    return met


# ---------------------------------------------------------------------------
# (c) mix dei formati e dei tipi
# ---------------------------------------------------------------------------


def format_counts(counts: Mapping[str, int]) -> str:
    """`flowchart 3, pie 1` oppure `-` se vuoto (conteggio decrescente, poi
    nome: è già l'ordine che `compute_figure_mix` restituisce)."""
    return ", ".join(f"{name} {n}" for name, n in counts.items()) or "-"


def share_rows(counts: Counter[str], total: int) -> list[list[Any]]:
    """Righe `nome | n | quota` ordinate per conteggio decrescente, poi nome."""
    return [
        [name, n, f"{n / total:.0%}" if total else "-"]
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def assets_without_slide(content: Mapping[str, Any], slides: Any) -> list[str] | None:
    """Figure e tabelle di Fase 3 che nessuna slide referenzia, `None` se
    la lezione non ha ancora `slides_raw`.

    Stessa misura del warning `lesson_slides_unreferenced_assets` e stessa
    chiave di confronto del CRUD (`norm_id`): un riferimento con un'altra
    grafia è lo stesso asset. Sull'export del 18 settembre 2026 una
    lezione con quattro figure ne perdeva una.
    """
    if not isinstance(slides, Mapping):
        return None
    items = slides.get("slides")
    if not isinstance(items, list):
        return None
    declared = {
        norm_id(a.get(key))
        for key, group in (("asset_id", "visual_assets"), ("table_id", "tables"))
        for a in (content.get(group) or [])
        if isinstance(a, dict)
    }
    declared.discard("")
    referenced = {
        norm_id(ref)
        for slide in items
        if isinstance(slide, dict)
        for ref in (slide.get("references_assets") or [])
    }
    return sorted(declared - referenced)


def print_figure_mix_report(rows: Sequence[dict[str, Any]]) -> None:
    """Sezione (c): quali FORMATI e, dentro `mermaid`, quali TIPI di
    diagramma il modello ha davvero prodotto, per lezione e in aggregato.

    È la misura che risponde alla monocultura vista il 18 settembre 2026
    sull'export reale: 13 flowchart su 14 figure generate, zero `vegalite`,
    zero `dot`. Stessa funzione della materializzazione
    (`app.services.figure_mix`), quindi la riga di log `lesson_content_
    figure_mix` in produzione e questa tabella contano allo stesso modo.
    """
    per_lesson: list[list[Any]] = []
    agg_formats: Counter[str] = Counter()
    agg_types: Counter[str] = Counter()
    mono = 0
    orphans: list[tuple[str, list[str]]] = []
    for row in rows:
        content = row.get("content_raw") or {}
        code = row.get("lesson_code") or str(row.get("id", ""))[:8]
        assets = [a for a in (content.get("visual_assets") or []) if isinstance(a, dict)]
        mix = compute_figure_mix(assets)
        agg_formats.update(mix.formats)
        agg_types.update(mix.mermaid_types)
        mono += int(mix.monoculture)
        senza_slide = assets_without_slide(content, row.get("slides_raw"))
        if senza_slide:
            orphans.append((code, senza_slide))
        per_lesson.append(
            [
                code,
                row.get("course_title", "-"),
                mix.total,
                len(mix.formats),
                format_counts(mix.formats),
                format_counts(mix.mermaid_types),
                "SI" if mix.monoculture else "no",
                "-" if senza_slide is None else len(senza_slide),
            ]
        )

    total = sum(agg_formats.values())
    print("\n## (c) Mix dei formati e dei tipi, per lezione")
    print(
        md_table(
            [
                "lezione",
                "corso",
                "figure",
                "formati distinti",
                "formati",
                "tipi mermaid",
                "monocultura",
                "senza slide",
            ],
            per_lesson,
        )
    )
    for code, ids in orphans:
        print(f"senza slide in {code}: {', '.join(ids)}")
    print("\n## (c) Mix aggregato — formati")
    print(md_table(["formato", "n", "quota"], share_rows(agg_formats, total)))
    mermaid_total = agg_formats.get("mermaid", 0)
    print("\n## (c) Mix aggregato — tipi Mermaid")
    print(md_table(["tipo", "n", "quota sui mermaid"], share_rows(agg_types, mermaid_total)))
    print(
        f"\nfigure totali: {total}; lezioni in monocultura "
        f"(>= {MONOCULTURE_MIN_FIGURES} figure, un solo formato e un solo tipo): "
        f"{mono} su {len(rows)}"
    )
    # `content_raw` non distingue la figura generata dal modello da quella
    # inserita a mano dall'editor: sull'export del 18 settembre 2026 dodici
    # campioni di catalogo (`A3`..`A14`) in una sola lezione facevano
    # sembrare vario un corpus che era 13 flowchart su 14 figure generate.
    print(
        "nota: il conteggio comprende gli asset inseriti a mano dagli editor "
        "(id tipo `A3`, `A7`); per il solo generato, filtra gli id del catalogo."
    )


# ---------------------------------------------------------------------------
# (e) figure rese con il registro di produzione (`--figures`)
# ---------------------------------------------------------------------------

# (metrica, colonna, soglia) su cui si applica la regola di calibrazione.
THRESHOLDS: tuple[tuple[str, str, int], ...] = (
    ("nodi", "nodes", graph_rules.MAX_GRAPH_NODES),
    ("archi", "edges", graph_rules.MAX_GRAPH_EDGES),
    ("etichetta (caratteri)", "label_chars", graph_rules.MAX_LABEL_CHARS),
    ("titolo (caratteri)", "title_chars", graph_rules.MAX_TITLE_CHARS),
    ("righe", "lines", graph_rules.MAX_GRAPH_LINES),
    ("sorgente Mermaid (caratteri)", "chars", graph_rules.MAX_MERMAID_SOURCE_CHARS),
    ("incroci", "crossings", graph_rules.MAX_EDGE_CROSSINGS),
)
CALIBRATION_SHARE = 0.6


@dataclass
class FigureRow:
    lesson: str
    asset_id: str
    fmt: str
    kind: str
    nodes: int | None
    edges: int | None
    label_chars: int | None
    title_chars: int | None
    lines: int
    chars: int
    rendered: bool = False
    crossings: int | None = None
    defects: tuple[str, ...] = ()


def _vegalite_row(lesson: str, asset_id: str, src: str) -> FigureRow:
    met = metrics_vegalite(src)
    try:
        spec = json.loads(src)
    except (TypeError, ValueError):
        spec = {}
    title = spec.get("title") if isinstance(spec, dict) else None
    return FigureRow(
        lesson=lesson,
        asset_id=asset_id,
        fmt="vegalite",
        kind="vegalite",
        nodes=met["nodi"],
        edges=None,
        label_chars=None,
        title_chars=len(title) if isinstance(title, str) else 0,
        lines=sum(1 for line in src.splitlines() if line.strip()),
        chars=len(src),
    )


def figure_rows(rows: Sequence[dict[str, Any]]) -> list[FigureRow]:
    """Una riga per visual_asset mermaid/dot/vegalite, con le misure del
    sorgente (nessun render)."""
    out: list[FigureRow] = []
    for row in rows:
        content = row.get("content_raw") or {}
        lesson = row.get("lesson_code") or str(row.get("id", ""))[:8]
        for asset in content.get("visual_assets") or []:
            if not isinstance(asset, dict) or asset.get("format") not in FIGURE_FORMATS:
                continue
            fmt = str(asset["format"])
            src = asset.get("content") or ""
            if not isinstance(src, str):
                src = json.dumps(src, ensure_ascii=False)
            asset_id = str(asset.get("asset_id") or "")
            if fmt == "vegalite":
                out.append(_vegalite_row(lesson, asset_id, src))
                continue
            met = graph_rules.graph_source_metrics(fmt, src.strip())
            if met is None:
                continue
            out.append(
                FigureRow(
                    lesson=lesson,
                    asset_id=asset_id,
                    fmt=fmt,
                    kind=met.kind,
                    nodes=met.nodes,
                    edges=met.edges,
                    label_chars=met.label_chars,
                    title_chars=met.title_chars,
                    lines=met.lines,
                    chars=met.chars,
                )
            )
    return out


def measure_group_size(fmt: str = "mermaid") -> int | None:
    """Figure per batch di misura: con al più `batch // figura` figure il
    residuo del batch non scende mai sotto il tetto di una figura, quindi
    nessuna misura è saltata per il tetto del batch (`batch_segment_cap`
    nella pagina Mermaid, `batch_work_cap` per DOT). `None` per i formati
    senza tetto di batch (un batch unico)."""
    from app.services import figure_geometry

    caps = {
        "mermaid": (
            figure_geometry.MAX_BATCH_MEASURE_SEGMENTS,
            figure_geometry.MAX_MEASURE_SEGMENTS,
        ),
        "dot": (figure_geometry.MAX_BATCH_MEASURE_WORK, figure_geometry.MAX_MEASURE_WORK),
    }
    if fmt not in caps:
        return None
    batch, per_figure = caps[fmt]
    return max(1, batch // per_figure)


def render_figures(rows: list[FigureRow], sources: dict[tuple[str, str], str]) -> None:
    """Rende le figure con il registro di produzione e riporta incroci e
    difetti nelle righe. Vega-Lite in un batch; Mermaid e DOT a gruppi di
    `measure_group_size(fmt)` figure, perché l'export ha un tetto di
    lavoro per batch e qui serve la misura di OGNI figura."""
    from app.services.figure_render_service import REGISTRY

    for fmt in FIGURE_FORMATS:
        todo = [r for r in rows if r.fmt == fmt]
        if not todo:
            continue
        renderer = REGISTRY[fmt]
        size = measure_group_size(fmt) or len(todo)
        for start in range(0, len(todo), size):
            group = todo[start : start + size]
            contents = [sources[(r.lesson, r.asset_id)] for r in group]
            ids = [f"{r.lesson}/{r.asset_id}" for r in group]
            batch = getattr(renderer, "render_figure_batch", None)
            if callable(batch):
                figures = batch(contents, asset_ids=ids)
                for row, fig in zip(group, figures, strict=True):
                    row.rendered = fig is not None
                    if fig is not None and fig.metrics is not None:
                        row.crossings = fig.metrics.crossings
                        row.defects = fig.metrics.defects
            else:
                svgs = renderer.render_svg_batch(contents, asset_ids=ids)
                for row, svg in zip(group, svgs, strict=True):
                    row.rendered = svg is not None


def unmeasured_crossings(rows: Sequence[FigureRow]) -> tuple[int, int]:
    """`(grafi resi senza incroci misurati, grafi resi)`: tetto di segmenti
    o di lavoro per figura o misura fallita (vedi i warning
    `figure_measure_skipped` e `mermaid_geometry_measure_failed`)."""
    rendered = [r for r in rows if r.fmt in graph_rules.GRAPH_FORMATS and r.rendered]
    return sum(1 for r in rendered if r.crossings is None), len(rendered)


def _percentile_90(values: Sequence[int]) -> float:
    if len(values) == 1:
        return float(values[0])
    return statistics.quantiles(values, n=10, method="inclusive")[8]


def threshold_rows(rows: Sequence[FigureRow]) -> list[list[Any]]:
    """Distribuzione per metrica sui grafi (Mermaid, DOT) e percentuale oltre
    ciascuna soglia, con l'esito della regola di calibrazione."""
    graphs = [r for r in rows if r.fmt in graph_rules.GRAPH_FORMATS]
    out: list[list[Any]] = []
    for label, attr, limit in THRESHOLDS:
        pool = [r for r in graphs if attr != "chars" or r.fmt == "mermaid"]
        values = [v for v in (getattr(r, attr) for r in pool) if isinstance(v, int)]
        if not values:
            out.append([label, limit, 0, "-", "-", "-", "-", "-", "-"])
            continue
        p90 = _percentile_90(values)
        over = sum(1 for v in values if v > limit)
        verdict = "alzare la soglia" if p90 > CALIBRATION_SHARE * limit else "ok"
        out.append(
            [
                label,
                limit,
                len(values),
                min(values),
                statistics.median(values),
                round(p90, 1),
                max(values),
                f"{100 * over / len(values):.1f} %",
                verdict,
            ]
        )
    return out


def print_figures_report(rows: Sequence[FigureRow]) -> None:
    print("\n## (e) Figure rese con il registro di produzione (--figures)")
    print(
        md_table(
            [
                "lezione",
                "asset",
                "formato",
                "tipo",
                "resa",
                "nodi",
                "archi",
                "etichetta",
                "titolo",
                "righe",
                "caratteri",
                "incroci",
                "difetti",
            ],
            [
                [
                    r.lesson,
                    r.asset_id,
                    r.fmt,
                    r.kind,
                    "si" if r.rendered else "no",
                    r.nodes,
                    r.edges,
                    r.label_chars,
                    r.title_chars,
                    r.lines,
                    r.chars,
                    r.crossings,
                    "; ".join(r.defects) or "-",
                ]
                for r in rows
            ],
        )
    )
    print(
        "\n## (e) Soglie di graph_rules sui grafi (Mermaid, DOT): regola di calibrazione "
        f"p90 > {int(CALIBRATION_SHARE * 100)} % della soglia → alzare la soglia"
    )
    print(
        md_table(
            ["metrica", "soglia", "n", "min", "mediana", "p90", "max", "oltre soglia", "esito"],
            threshold_rows(rows),
        )
    )
    missing, rendered = unmeasured_crossings(rows)
    print(
        f"\nincroci non misurati: {missing} su {rendered} grafi resi "
        "(tetto di segmenti o di lavoro per figura o misura fallita; "
        "esclusi dalla riga «incroci»)"
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def cell(value: Any) -> str:
    return "-" if value is None else str(value)


def md_table(header: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out.extend("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)
    return "\n".join(out)


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("data") or [data]
    rows: list[dict[str, Any]] = []
    for raw in data:
        row = dict(raw)
        for key in ("content_raw", "slides_raw"):
            val = row.get(key)
            if isinstance(val, str) and val.strip():
                try:
                    row[key] = json.loads(val)
                except ValueError:
                    row[key] = {}
        rows.append(row)
    return rows


def count_kind(occ: list[Occurrence], kind: str) -> int:
    return sum(1 for o in occ if o.kind == kind)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Misura dei riferimenti [KIND:id] e delle figure (diagnostico).",
    )
    parser.add_argument("export", type=Path, help="JSON esportato da pgAdmin (array di righe)")
    parser.add_argument(
        "--per-occorrenza",
        action="store_true",
        help="Stampa anche ogni singola occorrenza (zona, campo, riga, colonna).",
    )
    parser.add_argument(
        "--figures",
        action="store_true",
        help=(
            "Rende mermaid/dot/vegalite con il registro di produzione e riporta incroci, "
            "difetti e la percentuale oltre ciascuna soglia di graph_rules."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    rows = load_rows(args.export)
    print(f"measure_asset_refs v{SCRIPT_VERSION} — {len(rows)} lezioni\n")

    agg_kind: Counter[str] = Counter()
    agg_pos: Counter[str] = Counter()
    agg_zone: Counter[str] = Counter()
    agg_first: Counter[str] = Counter()
    agg_repeat = 0
    agg_ids = 0
    per_lesson: list[list[Any]] = []
    d_rows: list[list[Any]] = []

    for row in rows:
        content = row.get("content_raw") or {}
        code = row.get("lesson_code") or str(row.get("id", ""))[:8]
        occ = occurrences(content)
        by_key: dict[AssetKey, list[Occurrence]] = defaultdict(list)
        for o in occ:
            by_key[(o.kind, o.asset_id)].append(o)
        declared = declared_assets(content)
        cited_in_body = {(o.kind, o.asset_id) for o in occ if o.zone == "corpo"}
        repeated = [k for k, v in by_key.items() if len(v) > 1]
        # Mai citati nel corpo: il PDF li accoda (FIG → TAB → EQ → EX, A12/D3).
        uncited = [k for k in declared if k not in cited_in_body]
        # Citati ma non dichiarati: blocco missing-asset nel PDF.
        orphan = [k for k in by_key if k not in declared]

        agg_kind.update(o.kind for o in occ)
        agg_pos.update(o.position for o in occ)
        agg_zone.update(o.zone for o in occ)
        agg_first.update(v[0].position for v in by_key.values())
        agg_repeat += len(repeated)
        agg_ids += len(by_key)

        per_lesson.append(
            [
                code,
                row.get("course_title", "-"),
                len(occ),
                len(by_key),
                len(declared),
                *(count_kind(occ, kind) for kind in KINDS),
                sum(1 for o in occ if o.position == "in_linea"),
                sum(1 for o in occ if o.position == "ancora"),
                sum(1 for o in occ if o.zone == "coda"),
                len(repeated),
                len(uncited),
                len(orphan),
            ]
        )

        print(f"## Lezione {code} — {row.get('course_title', '')}")
        rows_id = []
        for key, occs_ in sorted(by_key.items(), key=lambda kv: kv[1][0].col):
            kind, asset_id = key
            seq = ",".join(o.position for o in occs_)
            rows_id.append(
                [
                    asset_id,
                    kind,
                    asset_family(key, declared),
                    len(occs_),
                    seq,
                    "si" if occs_[0].position == "in_linea" else "no",
                    "; ".join(f"{o.zone}:{o.field}" for o in occs_),
                ]
            )
        print(
            md_table(
                [
                    "id",
                    "kind",
                    "asset",
                    "n_occ",
                    "sequenza posizioni",
                    "prima occ. in linea?",
                    "campi",
                ],
                rows_id,
            )
        )
        print(
            "\nasset mai citati nel corpo (accodati FIG → TAB → EQ → EX, A12/D3): "
            + format_keys(uncited)
        )
        print(f"tag senza asset (blocco missing-asset): {format_keys(orphan)}\n")

        if args.per_occorrenza:
            print(
                md_table(
                    ["zona", "campo", "riga", "col", "kind", "id", "posizione"],
                    [[o.zone, o.field, o.line, o.col, o.kind, o.asset_id, o.position] for o in occ],
                )
            )
            print()

        for (kind, asset_id), asset in declared.items():
            if kind != "FIG":  # (d) riguarda solo i sorgenti delle figure
                continue
            met = asset_metrics(asset)
            d_rows.append(
                [
                    code,
                    asset_id,
                    met["formato"],
                    met["tipo"],
                    met["nodi"],
                    met["archi"],
                    met["label"],
                    met["incroci"],
                    met["len_sorgente"],
                    len(by_key.get(("FIG", asset_id), [])),
                ]
            )

    print("## (b) Sintesi per lezione")
    print(
        md_table(
            [
                "lezione",
                "corso",
                "tag",
                "id distinti",
                "asset dichiarati",
                *KINDS,
                "in linea",
                "ancore",
                "in coda",
                "id ripetuti",
                "asset non citati",
                "tag orfani",
            ],
            per_lesson,
        )
    )
    print("\n## (b) Aggregato")
    print(
        md_table(
            ["metrica", "valore"],
            [
                ["tag totali", sum(agg_kind.values())],
                *[[f"tag {k}", agg_kind.get(k, 0)] for k in KINDS],
                ["occorrenze in linea", agg_pos.get("in_linea", 0)],
                ["occorrenze su riga propria (ancora)", agg_pos.get("ancora", 0)],
                ["occorrenze nel corpo", agg_zone.get("corpo", 0)],
                ["occorrenze nella coda", agg_zone.get("coda", 0)],
                ["id distinti citati", agg_ids],
                ["id con prima occorrenza in linea", agg_first.get("in_linea", 0)],
                ["id con prima occorrenza come ancora", agg_first.get("ancora", 0)],
                ["id citati più di una volta", agg_repeat],
            ],
        )
    )
    print_figure_mix_report(rows)
    print("\n## (d) Struttura delle figure (incroci: solo con --figures)")
    print(
        md_table(
            [
                "lezione",
                "asset",
                "formato",
                "tipo",
                "nodi",
                "archi",
                "label",
                "incroci",
                "len sorgente",
                "n citazioni",
            ],
            d_rows,
        )
    )
    if args.figures:
        frows = figure_rows(rows)
        sources = {
            (row.get("lesson_code") or str(row.get("id", ""))[:8], str(a.get("asset_id") or "")): (
                a.get("content")
                if isinstance(a.get("content"), str)
                else json.dumps(a.get("content"), ensure_ascii=False)
            )
            for row in rows
            for a in ((row.get("content_raw") or {}).get("visual_assets") or [])
            if isinstance(a, dict)
        }
        render_figures(frows, sources)
        print_figures_report(frows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
