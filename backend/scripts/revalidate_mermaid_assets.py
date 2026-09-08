"""Rivalidazione con Mermaid 11 degli asset Mermaid già in DB — DRY-RUN, sola lettura.

Livello L5 della «regressione zero» del passaggio da Mermaid 10.9.4 a 11.x
(pin `settings.mermaid_cdn_version`): per ogni asset `format == "mermaid"`
in `content_raw.visual_assets` (Fase 3) e `slides_raw.new_assets` (Fase 4)
lo script:

1. applica `_sanitize_mermaid_code` (la stessa pulizia del pre-render);
2. esegue il gate statico D8 (tipo dichiarato tra
   `figure_theme.MERMAID_ALLOWED_TYPES`, niente `%%{init ...}%%`, niente
   tag HTML nelle label) — lo stesso criterio che il registro dei renderer
   applica alla rigenerazione e alla modifica del singolo asset;
3. renderizza il sorgente con la pagina Playwright di produzione
   (`mermaid_prerender._prerender_mermaid_to_svg_batch_sync`, Mermaid
   11.x da CDN, `htmlLabels: false` top-level) e conta i `<foreignObject>`
   nell'SVG: un diagramma che non parsa o che ne emette almeno uno finirà
   nel PDF come fallback `<pre>` e va corretto.

Il report elenca totale / ok / da correggere (con corso, lesson_code,
sorgente, asset_id, errore) e il numero di lezioni con asset visivi non
citati nel corpo della dispensa (`[FIG:id]` in introduction, sections,
summary): con la numerazione «Figura N.» questi asset vengono resi in coda
alla dispensa (A12), oggi sono invisibili.

Nessuna scrittura sul DB. Modello: `scripts/measure_register.py`.

Uso (dalla cartella `backend/`, Postgres raggiungibile)
-------------------------------------------------------

    python -m scripts.revalidate_mermaid_assets
    python -m scripts.revalidate_mermaid_assets --course "Analisi" --show-ok
    python -m scripts.revalidate_mermaid_assets --sample 40 --format csv
    python -m scripts.revalidate_mermaid_assets --skip-render   # solo gate statico

Sul server (`dc` = alias docker compose di produzione):

    dc exec -T backend python -m scripts.revalidate_mermaid_assets --format csv > mermaid.csv

`--skip-render` non richiede Chromium né rete. Senza, serve Playwright con
Chromium e l'accesso a cdn.jsdelivr.net: se la pagina non si carica ogni
asset del batch risulta «render_failed» (come accadrebbe nell'export).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import re
import sys
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.figure_render_service import (
    MERMAID_GATE_TYPE,
    mermaid_declared_type,
    mermaid_first_meaningful_line,
    mermaid_static_gate,
)
from app.services.mermaid_prerender import _sanitize_mermaid_code

SCRIPT_VERSION = "1"

# Stessa forma di `_ASSET_REF_RE` del PDF (case-sensitive su `FIG`); gli id
# sono confrontati in minuscolo, come fa il renderer.
_FIG_REF_RE = re.compile(r"\[FIG:([^\]\n]+)\]")
# ---------------------------------------------------------------------------
# Gate statico D8 — delegato al registro dei renderer (unico punto del gate)
# ---------------------------------------------------------------------------


def first_meaningful_line(code: str) -> str:
    """Prima riga utile: salta righe vuote, commenti `%%` e il frontmatter
    YAML `---...---` iniziale."""
    return mermaid_first_meaningful_line(code)


def declared_type(code: str) -> str:
    """Tipo dichiarato (prima parola della prima riga utile, `graph TD` → `graph`)."""
    return mermaid_declared_type(code)


def static_gate(code: str) -> str:
    """Ritorna `""` se il sorgente passa il gate statico D8 di
    `figure_render_service.mermaid_static_gate` (lo stesso di
    `MermaidRenderer.validate`), altrimenti un codice d'errore leggibile
    (`mermaid_empty`, `mermaid_type_not_allowed:<tipo>`,
    `mermaid_init_directive`, `mermaid_html_in_label`,
    `mermaid_external_resource`). `<br>` NON è un tag HTML per il gate: è
    la sintassi di a capo di Mermaid e passa."""
    outcome, detail = mermaid_static_gate(code)
    if outcome == MERMAID_GATE_TYPE:
        return f"{outcome}:{detail}"
    return outcome


# ---------------------------------------------------------------------------
# Estrazione dagli JSONB
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class MermaidAsset:
    course: str
    course_id: str
    lesson_code: str
    source: str  # "content_raw" | "slides_raw"
    asset_id: str
    content: str


def collect_mermaid_assets(rec: Any) -> list[MermaidAsset]:
    """Asset `format == "mermaid"` di una lezione: `content_raw.visual_assets`
    (Fase 3) e `slides_raw.new_assets` (Fase 4), nell'ordine degli array."""
    out: list[MermaidAsset] = []
    sources = (
        ("content_raw", _as_list(_as_dict(rec["content_raw"]).get("visual_assets"))),
        ("slides_raw", _as_list(_as_dict(rec["slides_raw"]).get("new_assets"))),
    )
    for source, assets in sources:
        for raw in assets:
            asset = _as_dict(raw)
            if asset.get("format") != "mermaid":
                continue
            out.append(
                MermaidAsset(
                    course=_str(rec["course_title"]),
                    course_id=str(rec["course_id"]),
                    lesson_code=_str(rec["lesson_code"]),
                    source=source,
                    asset_id=_str(asset.get("asset_id")),
                    content=_str(asset.get("content")),
                )
            )
    return out


def body_markdown(content_raw: Any) -> str:
    """Corpo della dispensa in cui il renderer sostituisce i tag `[FIG:id]`:
    introduction, sections[].content, summary (non key_takeaways/references)."""
    content = _as_dict(content_raw)
    parts = [_str(content.get("introduction"))]
    parts.extend(_str(_as_dict(s).get("content")) for s in _as_list(content.get("sections")))
    parts.append(_str(content.get("summary")))
    return "\n\n".join(p for p in parts if p)


def uncited_asset_ids(content_raw: Any) -> list[str]:
    """Id dei `visual_assets` (qualunque formato) mai citati con `[FIG:id]`
    nel corpo della dispensa: con la numerazione delle figure vengono resi
    in coda (A12)."""
    content = _as_dict(content_raw)
    cited = {m.group(1).strip().lower() for m in _FIG_REF_RE.finditer(body_markdown(content))}
    out: list[str] = []
    for raw in _as_list(content.get("visual_assets")):
        asset_id = _str(_as_dict(raw).get("asset_id")).strip()
        if asset_id and asset_id.lower() not in cited:
            out.append(asset_id)
    return out


# ---------------------------------------------------------------------------
# Valutazione: gate statico + render Mermaid 11
# ---------------------------------------------------------------------------


@dataclass
class AssetResult:
    asset: MermaidAsset
    ok: bool
    error: str
    foreign_objects: int = 0
    svg_bytes: int = 0
    sanitized_changed: bool = False

    @property
    def declared(self) -> str:
        return declared_type(_sanitize_mermaid_code(self.asset.content))


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), max(1, size)):
        yield items[i : i + max(1, size)]


def evaluate_assets(
    assets: Sequence[MermaidAsset],
    *,
    render: bool,
    batch_size: int = 50,
    render_batch: Any = None,
) -> list[AssetResult]:
    """Gate statico su tutti gli asset; render Mermaid 11 (una sessione
    Playwright per `batch_size` asset) solo su quelli che lo superano.
    `render_batch` (test) sostituisce `_prerender_mermaid_to_svg_batch_sync`."""
    results: list[AssetResult] = []
    to_render: list[int] = []
    for asset in assets:
        code = _sanitize_mermaid_code(asset.content)
        err = static_gate(code)
        results.append(
            AssetResult(
                asset=asset,
                ok=not err,
                error=err,
                sanitized_changed=code != asset.content.strip(),
            )
        )
        if not err:
            to_render.append(len(results) - 1)

    if not render or not to_render:
        return results

    if render_batch is None:
        from app.services.mermaid_prerender import _prerender_mermaid_to_svg_batch_sync

        render_batch = _prerender_mermaid_to_svg_batch_sync

    for chunk in _chunks(to_render, batch_size):
        codes = [_sanitize_mermaid_code(results[i].asset.content) for i in chunk]
        svgs = render_batch(codes)
        for idx, svg in zip(chunk, svgs, strict=True):
            res = results[idx]
            if not svg:
                res.ok = False
                res.error = "render_failed"
                continue
            res.svg_bytes = len(svg.encode("utf-8"))
            res.foreign_objects = svg.count("<foreignObject")
            if res.foreign_objects:
                res.ok = False
                res.error = f"foreign_object:{res.foreign_objects}"
    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def result_rows(results: Sequence[AssetResult], *, show_ok: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in results:
        if r.ok and not show_ok:
            continue
        rows.append(
            {
                "corso": r.asset.course,
                "lezione": r.asset.lesson_code,
                "sorgente": r.asset.source,
                "asset_id": r.asset.asset_id,
                "tipo": r.declared,
                "esito": "ok" if r.ok else "da correggere",
                "errore": r.error,
                "foreignObject": r.foreign_objects,
                "svg_bytes": r.svg_bytes,
                "sanitizzato": "sì" if r.sanitized_changed else "no",
            }
        )
    return rows


def summary_rows(
    results: Sequence[AssetResult],
    *,
    lessons: int,
    lessons_with_uncited: int,
    uncited_assets: int,
    rendered: bool,
) -> list[dict[str, Any]]:
    errors: dict[str, int] = {}
    for r in results:
        if not r.ok:
            key = r.error.split(":", 1)[0]
            errors[key] = errors.get(key, 0) + 1
    rows = [
        {"voce": "lezioni analizzate", "valore": lessons},
        {"voce": "asset mermaid totali", "valore": len(results)},
        {"voce": "ok", "valore": sum(1 for r in results if r.ok)},
        {"voce": "da correggere", "valore": sum(1 for r in results if not r.ok)},
    ]
    rows.extend({"voce": f"  di cui {k}", "valore": v} for k, v in sorted(errors.items()))
    rows.append({"voce": "render Mermaid 11 eseguito", "valore": "sì" if rendered else "no"})
    rows.append({"voce": "lezioni con asset non citati (A12)", "valore": lessons_with_uncited})
    rows.append({"voce": "asset non citati totali", "valore": uncited_assets})
    return rows


def render_markdown_table(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return "_(nessuna riga)_"
    columns = list(rows[0].keys())
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in columns) + " |"
        for row in rows
    )
    return "\n".join(lines)


def render_csv(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Accesso al DB (sola lettura)
# ---------------------------------------------------------------------------


def build_statement(args: argparse.Namespace) -> Any:
    """SELECT esplicito delle sole colonne necessarie; nessuna esecuzione."""
    from sqlalchemy import or_, select

    import app.models  # noqa: F401  registra tutti i mapper (relazioni per stringa)
    from app.models.course import Course
    from app.models.course_lesson import CourseLesson

    stmt = (
        select(
            Course.id.label("course_id"),
            Course.title.label("course_title"),
            CourseLesson.lesson_code,
            CourseLesson.content_raw,
            CourseLesson.slides_raw,
        )
        .join(Course, Course.id == CourseLesson.course_id)
        .where(or_(CourseLesson.content_raw.is_not(None), CourseLesson.slides_raw.is_not(None)))
        .order_by(Course.title, Course.id, CourseLesson.lesson_code)
    )
    if args.course:
        try:
            stmt = stmt.where(Course.id == uuid.UUID(args.course))
        except ValueError:
            stmt = stmt.where(Course.title.ilike(f"%{args.course}%"))
    if args.lesson:
        stmt = stmt.where(CourseLesson.lesson_code == args.lesson)
    return stmt


async def load_records(args: argparse.Namespace) -> list[Any]:
    from app.db.session import async_session_factory, engine

    stmt = build_statement(args)
    try:
        async with async_session_factory() as session:
            return list((await session.execute(stmt)).mappings().all())
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.revalidate_mermaid_assets",
        description=(
            "Rivalida con Mermaid 11 gli asset Mermaid in DB (dry-run, sola lettura). "
            f"SCRIPT_VERSION={SCRIPT_VERSION}"
        ),
    )
    parser.add_argument("--course", help="Filtro corso: parte del titolo (ILIKE) oppure UUID.")
    parser.add_argument("--lesson", help="Filtro lesson_code esatto, es. M1.L1.")
    parser.add_argument(
        "--sample", type=int, default=0, metavar="N", help="Analizza solo i primi N asset."
    )
    parser.add_argument(
        "--skip-render",
        action="store_true",
        help="Solo gate statico D8 (niente Chromium, niente rete).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Asset per sessione Playwright (default 50).",
    )
    parser.add_argument("--format", choices=("table", "csv"), default="table")
    parser.add_argument("--show-ok", action="store_true", help="Elenca anche gli asset validi.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = asyncio.run(load_records(args))

    assets: list[MermaidAsset] = []
    lessons_with_uncited = 0
    uncited_total = 0
    for rec in records:
        assets.extend(collect_mermaid_assets(rec))
        uncited = uncited_asset_ids(rec["content_raw"])
        if uncited:
            lessons_with_uncited += 1
            uncited_total += len(uncited)
    if args.sample > 0:
        assets = assets[: args.sample]

    results = evaluate_assets(assets, render=not args.skip_render, batch_size=args.batch_size)
    detail = result_rows(results, show_ok=args.show_ok)
    summary = summary_rows(
        results,
        lessons=len(records),
        lessons_with_uncited=lessons_with_uncited,
        uncited_assets=uncited_total,
        rendered=not args.skip_render,
    )

    if args.format == "csv":
        sys.stdout.write(render_csv(detail))
        print(render_markdown_table(summary), file=sys.stderr)
        return 0

    print(
        f"# revalidate_mermaid_assets — SCRIPT_VERSION={SCRIPT_VERSION} — "
        f"lezioni: {len(records)} — asset mermaid: {len(results)}"
    )
    print("_Dry-run, sola lettura: nessuna modifica al DB._")
    print()
    print(render_markdown_table(summary))
    print()
    print("## Asset" + ("" if args.show_ok else " da correggere"))
    print()
    print(render_markdown_table(detail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
