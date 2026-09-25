"""Ri-ritaglio v2 delle figure di fonte già estratte (doc 18 §22, decisione V2).

Stesso UUID, anche per le figure già collocate: la riga «Fonte» resta,
cambiano i byte; niente Vision. Senza opzioni lo script elenca soltanto
che cosa farebbe (dry-run).

- `--measure`: ri-ritaglia in una cartella temporanea SENZA scrivere nulla
  e riporta identità, modi del ritaglio, byte, deriva del phash e la
  matrice delle classi A → B → C (A: ritaglio v1 con la regola di stampa
  v1; B: ritaglio v1 con la regola nuova; C: ritaglio v2 con la regola
  nuova), più le lezioni da riesportare; `--report file.json` salva il
  dettaglio;
- `--apply`: accoda il ri-ritaglio al worker delle figure (richiede la
  regola di stampa nuova e il worker acceso);
- `--inline`: esegue il ri-ritaglio subito, in questo processo;
- `--revert`: ripristina il ritaglio v1 (finché i file v1 esistono);
- `--purge-replaced --older-than N`: cancella i file v1 dei ri-ritagli più
  vecchi di N giorni (default 14; da lì `--revert` non è più possibile);
  senza `--apply` solo il conteggio.

`--apply` e `--inline` rifiutano di partire se nel perimetro ci sono
estrazioni in coda o in corso, o ri-ritagli già accodati.

Uso (dalla cartella `backend/`, Postgres raggiungibile):

    python -m scripts.rerender_document_figures --course <uuid>             # dry-run
    python -m scripts.rerender_document_figures --course <uuid> --measure --report r.json
    python -m scripts.rerender_document_figures --course <uuid> --apply
    python -m scripts.rerender_document_figures --course <uuid> --revert
    python -m scripts.rerender_document_figures --purge-replaced --older-than 14 --apply

Sul server (`dc` = alias docker compose di produzione):

    dc exec -T backend python -m scripts.rerender_document_figures --course <uuid> --measure
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import sys
import tempfile
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--course", type=uuid.UUID, help="Corso (obbligatorio tranne che per il purge)."
    )
    parser.add_argument("--document", type=uuid.UUID, help="Solo questo documento del corso.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--measure", action="store_true", help="Misura senza scrivere.")
    modes.add_argument("--inline", action="store_true", help="Ri-ritaglia subito, qui.")
    modes.add_argument("--revert", action="store_true", help="Ripristina il ritaglio v1.")
    modes.add_argument(
        "--purge-replaced", action="store_true", help="Cancella i file v1 dei ri-ritagli vecchi."
    )
    parser.add_argument("--apply", action="store_true", help="Scrive (accoda, o esegue il purge).")
    parser.add_argument("--older-than", type=int, default=14, help="Giorni (purge).")
    parser.add_argument("--report", type=Path, help="Dettaglio della misura in JSON.")
    return parser


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def _classes(row: Any, event: dict[str, Any] | None) -> dict[str, Any]:
    """Classi A (v1/v1), B (v1/nuova), C (v2/nuova) e ppi di stampa."""
    from app.services.source_figure_resolution import (
        REFERENCE_COLUMN_MM,
        ResolutionInputs,
        class_for_ppi,
        plan_print,
    )
    from app.services.source_figure_service import display_width_mm

    out: dict[str, Any] = {}
    width_v1 = display_width_mm(row.width, row.dpi)
    if width_v1 and row.width:
        printed = min(REFERENCE_COLUMN_MM, width_v1)
        out["a_ppi"] = round(row.width / (printed / 25.4), 1)
        out["a"] = class_for_ppi(out["a_ppi"])
    plan_b = plan_print(ResolutionInputs.from_figure(row))
    if plan_b is not None:
        out["b"], out["b_ppi"] = plan_b.resolution_class, round(plan_b.ppi, 1)
    if event is not None and not event.get("error"):
        data = {
            "width": event.get("width"),
            "height": event.get("height"),
            "dpi": event.get("dpi"),
            "native_ppi": event.get("native_ppi"),
            "natural_width_mm": event.get("natural_width_mm"),
            "is_vector": event.get("is_vector"),
            "bbox": row.bbox,
        }
        plan_c = plan_print(ResolutionInputs.from_mapping(data, source_kind=row.source_kind))
        if plan_c is not None:
            out["c"], out["c_ppi"] = plan_c.resolution_class, round(plan_c.ppi, 1)
    return out


async def _measure(db: Any, docs: list[Any], course_id: uuid.UUID) -> dict[str, Any]:
    from app.services import course_document_figures_worker as figures_worker
    from app.services import document_figures_recrop_service as recrop
    from app.services.document_figures.phash import hamming
    from app.services.document_figures_service import figure_lesson_uses

    config = figures_worker._config()
    uses = await figure_lesson_uses(db, course_id)
    figures: list[dict[str, Any]] = []
    for doc in docs:
        rows = await recrop.eligible_rows(db, doc.id)
        if not rows:
            continue
        workdir = Path(tempfile.mkdtemp(prefix="a4u-recrop-measure-"))
        try:
            try:
                result = await recrop.run_child(doc, rows, workdir, config)
            except Exception as exc:
                print(f"  {doc.filename_original[:50]}: errore {type(exc).__name__}: {exc}")
                continue
            for row in rows:
                event = result.events.get(row.id.hex)
                item: dict[str, Any] = {
                    "figure_id": str(row.id),
                    "document": doc.filename_original,
                    "locator": row.locator,
                    "lessons": uses.get(row.id, []),
                    "v1": {
                        "width": row.width,
                        "dpi": row.dpi,
                        "mime": row.mime_type,
                        "bytes": row.byte_size,
                    },
                    "skipped": result.skipped.get(row.id.hex),
                }
                if event is not None:
                    new_bytes = workdir / Path(str(event.get("file") or "x")).name
                    item["identity"] = event.get("identity")
                    item["error"] = event.get("error")
                    item["v2"] = {
                        key: event.get(key)
                        for key in (
                            "width",
                            "dpi",
                            "mime",
                            "crop_mode",
                            "aligned",
                            "native_ppi",
                            "natural_width_mm",
                            "source_lossy",
                        )
                    }
                    item["v2"]["bytes"] = new_bytes.stat().st_size if new_bytes.is_file() else None
                    if event.get("phash") and row.phash:
                        item["phash_drift"] = hamming(str(event["phash"]), str(row.phash))
                item["classes"] = _classes(row, event)
                figures.append(item)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return {"figures": figures, "summary": _summary(figures)}


def _summary(figures: list[dict[str, Any]]) -> dict[str, Any]:
    done = [f for f in figures if f.get("v2") and not f.get("error")]
    identity_ok = [f for f in done if (f.get("identity") or {}).get("ok")]
    modes = Counter(f["v2"]["crop_mode"] for f in done)
    rasters = [f for f in done if f["v2"]["crop_mode"] in ("raster_native", "mixed")]
    ratios = [
        f["v2"]["bytes"] / f["v1"]["bytes"]
        for f in done
        if f["v2"].get("bytes") and f["v1"].get("bytes")
    ]
    drift = [float(f["phash_drift"]) for f in done if f.get("phash_drift") is not None]
    matrix = Counter(
        f"{f['classes'].get('a', '-')}→{f['classes'].get('b', '-')}→{f['classes'].get('c', '-')}"
        for f in figures
    )
    lessons = sorted({code for f in identity_ok for code in f.get("lessons") or []})
    return {
        "figures": len(figures),
        "rerendered": len(done),
        "identity_ok": len(identity_ok),
        "identity_share": round(len(identity_ok) / len(done), 3) if done else None,
        "modes": dict(modes),
        "raster_native_share": (
            round(
                sum(1 for f in rasters if f["v2"]["crop_mode"] == "raster_native") / len(rasters), 3
            )
            if rasters
            else None
        ),
        "aligned_share": (
            round(sum(1 for f in rasters if f["v2"]["aligned"]) / len(rasters), 3)
            if rasters
            else None
        ),
        "jpeg_v1": sum(1 for f in figures if f["v1"]["mime"] == "image/jpeg"),
        "jpeg_v2": sum(1 for f in done if f["v2"]["mime"] == "image/jpeg"),
        "bytes_ratio_median": round(statistics.median(ratios), 2) if ratios else None,
        "bytes_ratio_p95": round(_percentile(ratios, 0.95) or 0, 2) if ratios else None,
        "bytes_ratio_max": round(max(ratios), 2) if ratios else None,
        "bytes_v2_max": max((f["v2"]["bytes"] or 0 for f in done), default=None),
        "phash_drift_p95": _percentile(drift, 0.95),
        "phash_drift_max": max(drift) if drift else None,
        "classes_a_b_c": dict(matrix.most_common()),
        "classes_c": dict(Counter(f["classes"].get("c", "-") for f in done)),
        "lessons_to_reexport": lessons,
    }


async def run(args: argparse.Namespace) -> int:
    from app.core.config import get_settings
    from app.db.session import async_session_factory, engine
    from app.models.course_document import CourseDocument
    from app.services import course_document_figures_worker as figures_worker
    from app.services import document_figures_recrop_service as recrop

    settings = get_settings()
    if args.purge_replaced:
        async with async_session_factory() as db:
            out = await recrop.purge_replaced(
                db,
                course_id=args.course,
                older_than=timedelta(days=max(0, args.older_than)),
                apply=args.apply,
            )
        verb = "cancellati" if args.apply else "da cancellare"
        print(f"figure ri-ritagliate da più di {args.older_than} giorni: {out['figures']}")
        print(f"file v1 {verb}: {out['files']}")
        await engine.dispose()
        return 0
    if args.course is None:
        print("--course è obbligatorio", file=sys.stderr)
        return 2
    try:
        async with async_session_factory() as db:
            if args.revert:
                # Con un ri-ritaglio accodato o in corso il ripristino sarebbe
                # parziale o annullato subito dopo dal worker.
                busy = await recrop.busy_documents(db, args.course)
                if busy:
                    print(
                        f"rifiutato: {busy} documenti con estrazione o ri-ritaglio in corso",
                        file=sys.stderr,
                    )
                    return 2
                out = await recrop.revert(db, course_id=args.course, document_id=args.document)
                print(f"figure ripristinate al ritaglio v1: {out['reverted']}")
                return 0
            query = (
                select(CourseDocument)
                .where(CourseDocument.course_id == args.course)
                .order_by(CourseDocument.created_at.asc())
            )
            if args.document is not None:
                query = query.where(CourseDocument.id == args.document)
            docs = list((await db.execute(query)).scalars().all())
            counts = {doc.id: len(await recrop.eligible_rows(db, doc.id)) for doc in docs}
            targets = [doc for doc in docs if counts[doc.id]]
            print(f"documenti con figure v1 da ri-ritagliare: {len(targets)}")
            print(f"figure v1 da ri-ritagliare: {sum(counts.values())}")
            if args.measure:
                report = await _measure(db, targets, args.course)
                print(json.dumps(report["summary"], indent=1, ensure_ascii=False))
                if args.report is not None:
                    args.report.write_text(
                        json.dumps(report, indent=1, ensure_ascii=False, default=str),
                        encoding="utf-8",
                    )
                    print(f"dettaglio: {args.report}")
                return 0
            if not (args.apply or args.inline):
                for doc in targets:
                    print(f"  {doc.id}  {counts[doc.id]:4d}  {doc.filename_original[:60]}")
                print("dry-run: nessuna scrittura (--measure, --apply o --inline)")
                return 0
            if not settings.figure_resolution_rules_enabled:
                print(
                    "rifiutato: FIGURE_RESOLUTION_RULES_ENABLED=false (il ritaglio v2 si "
                    "applica solo con la regola di stampa nuova)",
                    file=sys.stderr,
                )
                return 2
            if not settings.figure_extraction_native_crop_enabled:
                print(
                    "rifiutato: FIGURE_EXTRACTION_NATIVE_CROP_ENABLED=false",
                    file=sys.stderr,
                )
                return 2
            busy = await recrop.busy_documents(db, args.course)
            if busy:
                print(
                    f"rifiutato: {busy} documenti con estrazione o ri-ritaglio in corso",
                    file=sys.stderr,
                )
                return 2
            if args.apply:
                if not settings.figure_extraction_enabled:
                    print(
                        "rifiutato: worker delle figure spento (FIGURE_EXTRACTION_ENABLED=false); "
                        "usare --inline",
                        file=sys.stderr,
                    )
                    return 2
                queued = await recrop.request(db, [doc.id for doc in targets])
                print(f"documenti accodati al worker: {queued}")
                return 0
            config = figures_worker._config()
            total: Counter[str] = Counter()
            for doc in targets:
                # Anche --inline passa dal lease: `busy_documents` lo vede e
                # né il worker né un altro script prendono lo stesso documento.
                await recrop.request(db, [doc.id])
                claimed = await recrop.claim(db, doc.id)
                if claimed is None:
                    print(f"  {doc.filename_original[:50]}: già in lavorazione, saltato")
                    continue
                stats = await recrop.process(db, claimed, config)
                total.update({k: int(v) for k, v in stats.items() if isinstance(v, int)})
                print(f"  {doc.filename_original[:50]}: {stats}")
            print(f"totale: {dict(total)}")
            return 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
