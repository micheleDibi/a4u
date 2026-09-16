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
  (d) per ogni visual_asset di formato mermaid/dot/vegalite: nodi, archi,
      label (parsing testuale semplice, euristico). Gli INCROCI non sono
      implementati: la colonna resta un segnaposto (`-`).

Sola lettura: nessun accesso al DB, nessuna scrittura. Solo stdlib.

Uso (dalla cartella `backend/`)
-------------------------------

    python -m scripts.measure_asset_refs lessons_export.json
    python -m scripts.measure_asset_refs lessons_export.json --per-occorrenza
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "1"

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

MM_EDGE = re.compile(r"-{2,3}>|-{3,}|-\.-+>|={2,}>|--[ox]|<--")
MM_EDGE_LABEL = re.compile(r"(-{2,3}|-\.-|={2,})\s*[^\-=>|\n]+?\s*(-{2,3}>|-\.->|={2,}>)")
MM_PIPE_LABEL = re.compile(r"\|[^|\n]*\|")
MM_SHAPE = re.compile(r"\[[^\]]+\]|\([^)]+\)|\{[^}]+\}")
MM_KEYWORDS = {"subgraph", "end", "style", "classdef", "class", "direction", "linkstyle"}
MM_OPAQUE_TYPES = (
    "statediagram-v2",
    "statediagram",
    "classdiagram",
    "erdiagram",
    "gantt",
    "mindmap",
    "timeline",
)
SEQ_KEYWORDS = ("participant", "actor", "note", "loop", "alt", "else", "end")


def _empty_metrics(kind: str) -> dict[str, Any]:
    return {"tipo": kind, "nodi": None, "archi": None, "label": None}


def metrics_mermaid(src: str) -> dict[str, Any]:
    lines = [
        ln.strip() for ln in src.splitlines() if ln.strip() and not ln.strip().startswith("%%")
    ]
    head = lines[0].lower() if lines else ""
    kind = head.split()[0] if head else "?"
    if kind in ("flowchart", "graph"):
        nodes: set[str] = set()
        edges = 0
        labels = 0
        for ln in lines[1:]:
            labels += len(MM_SHAPE.findall(ln)) + len(MM_PIPE_LABEL.findall(ln))
            clean = MM_PIPE_LABEL.sub(" ", MM_EDGE_LABEL.sub(r"\2", ln))
            edges += len(MM_EDGE.findall(clean))
            bare = MM_SHAPE.sub("", clean)
            for seg in MM_EDGE.split(bare):
                toks = seg.strip().split()
                if toks and toks[0].lower() not in MM_KEYWORDS:
                    nodes.add(toks[0])
        return {"tipo": kind, "nodi": len(nodes), "archi": edges, "label": labels}
    if kind == "sequencediagram":
        actors = [ln for ln in lines[1:] if ln.lower().startswith(("participant", "actor"))]
        msgs = [
            ln
            for ln in lines[1:]
            if re.search(r"-{1,2}>>?|-{1,2}x|--?\)", ln) and not ln.lower().startswith(SEQ_KEYWORDS)
        ]
        return {
            "tipo": "sequenceDiagram",
            "nodi": len(actors),
            "archi": len(msgs),
            "label": len(msgs),
        }
    if kind == "pie":
        slices = [ln for ln in lines[1:] if ":" in ln]
        return {"tipo": "pie", "nodi": len(slices), "archi": 0, "label": len(slices)}
    if kind in MM_OPAQUE_TYPES:
        return {
            "tipo": kind,
            "nodi": None,
            "archi": len(MM_EDGE.findall(src)) or None,
            "label": None,
        }
    return _empty_metrics(kind)


DOT_KEYWORDS = {
    "digraph",
    "graph",
    "subgraph",
    "node",
    "edge",
    "rankdir",
    "strict",
    "label",
    "shape",
}
DOT_STMT_PREFIXES = ("digraph", "graph", "subgraph", "}", "{", "node", "edge", "rankdir")


def metrics_dot(src: str) -> dict[str, Any]:
    body = re.sub(r"//.*|/\*.*?\*/|#.*", "", src, flags=re.S)
    edges = len(re.findall(r"->|--", body))
    labels = len(re.findall(r'label\s*=\s*("(?:[^"\\]|\\.)*"|[\w.]+)', body))
    nodes: set[str] = set()
    for stmt in re.split(r"[;\n]", body):
        s = stmt.strip()
        if not s or s.startswith(DOT_STMT_PREFIXES):
            continue
        s = re.sub(r"\[[^\]]*\]", "", s)
        for tok in re.findall(r'"(?:[^"\\]|\\.)*"|[A-Za-z_][\w]*', s):
            name = tok.strip('"')
            if name and name.lower() not in DOT_KEYWORDS:
                nodes.add(name)
    return {"tipo": "dot", "nodi": len(nodes), "archi": edges, "label": labels}


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
    if fmt == "mermaid":
        met = metrics_mermaid(src)
    elif fmt == "dot":
        met = metrics_dot(src)
    elif fmt == "vegalite":
        met = metrics_vegalite(src)
    else:  # function, image, formati legacy: non sono grafi
        met = _empty_metrics(fmt or "?")
    met["formato"] = fmt
    met["len_sorgente"] = len(src)
    met["incroci"] = None  # SEGNAPOSTO: non implementato (serve il layout, non il testo)
    return met


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
    print("\n## (d) Struttura delle figure (incroci: NON implementato)")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
