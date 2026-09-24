"""Estrazione delle figure di fonte dei documenti già caricati — idempotente.

Nessun backfill automatico: i documenti caricati prima della funzione hanno
`figures_status` NULL e restano così finché qualcuno non chiede
l'estrazione (bottone «Estrai figure» o questo script). Senza `--apply` lo
script elenca soltanto che cosa farebbe (dry-run, nessuna scrittura).

- default: documenti mai richiesti (`figures_status` NULL), saltati
  perché l'estrazione era spenta (`skipped/extraction_disabled`) o fonti
  riservate saltate con la regola precedente (`skipped/policy_content_only`:
  ora si estraggono come materiale del docente) → in coda (`pending`), o
  `skipped` con il motivo se non estraibili (documento escluso, formato,
  estrazione spenta); il worker li prende uno alla volta;
- `--retry-failed`: rimette in coda anche i documenti `failed`;
- `--gc-detached`: figure staccate (documento cancellato) che nessuna
  lezione usa più → cancellate con i file.

Uso (dalla cartella `backend/`, Postgres raggiungibile):

    python -m scripts.extract_document_figures                      # dry-run
    python -m scripts.extract_document_figures --course <uuid> --apply
    python -m scripts.extract_document_figures --gc-detached --apply

Sul server (`dc` = alias docker compose di produzione):

    dc exec -T backend python -m scripts.extract_document_figures --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import select


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--course", type=uuid.UUID, help="Solo i documenti di questo corso.")
    parser.add_argument("--apply", action="store_true", help="Scrive (default: dry-run).")
    parser.add_argument(
        "--retry-failed", action="store_true", help="Rimette in coda anche i `failed`."
    )
    parser.add_argument(
        "--gc-detached",
        action="store_true",
        help="Pulisce le figure staccate non più usate invece di accodare.",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    from app.db.session import async_session_factory, engine
    from app.models.course import Course
    from app.models.course_document import CourseDocument
    from app.services import document_figures_service as service

    try:
        async with async_session_factory() as db:
            if args.gc_detached:
                orphans = await service.gc_detached(db, course_id=args.course, apply=args.apply)
                verb = "cancellate" if args.apply else "da cancellare"
                for row in orphans:
                    print(f"{row.course_id}  {row.id}  {row.storage_path}")
                print(f"figure staccate non usate {verb}: {len(orphans)}")
                return 0

            def eligible(doc: CourseDocument) -> bool:
                if doc.figures_status is None:
                    return True
                if doc.figures_status == "skipped" and doc.figures_error_code in (
                    "extraction_disabled",
                    "policy_content_only",
                ):
                    return True
                return bool(args.retry_failed) and doc.figures_status == "failed"

            query = select(CourseDocument).order_by(CourseDocument.created_at.asc())
            if args.course is not None:
                query = query.where(CourseDocument.course_id == args.course)
            docs = [d for d in (await db.execute(query)).scalars().all() if eligible(d)]
            queued = skipped = 0
            for doc in docs:
                code = service.skip_code(doc)
                action = f"skipped ({code})" if code else "in coda"
                print(f"{doc.course_id}  {doc.id}  {doc.filename_original[:60]}  → {action}")
                if code:
                    skipped += 1
                else:
                    queued += 1
                if args.apply:
                    course = await db.get(Course, doc.course_id)
                    assert course is not None
                    if doc.figures_status == "failed":
                        doc.figures_status = None
                    await service.request_extraction(db, course=course, doc=doc, actor_id=None)
            mode = "applicato" if args.apply else "dry-run"
            print(f"[{mode}] documenti: {len(docs)}; in coda: {queued}; saltati: {skipped}")
            return 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
