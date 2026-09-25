"""Completa `depicts` sulle figure di fonte descritte prima della 0041 (doc 18 §23.2).

Una chiamata Vision (PROMPT 18) per figura, della quale si tiene SOLO
`depicts`: descrizione, parole chiave, tipo e qualità restano quelli di
prima, così il catalogo di Fase 3 non cambia. Il costo si somma in
`vision_usage` (dashboard admin, fase `document_figures`). Le copie di una
descrizione (`describe_source_id`) ricevono lo stesso `depicts` senza
chiamate. Senza `--apply` lo script conta soltanto e stima il costo.

- perimetro: figure `ready`, già descritte, con un file, senza `depicts`
  alla versione corrente; `--course` oppure `--all`;
- `--max-usd` è obbligatorio con `--apply`: lo script non avvia chiamate
  che porterebbero la spesa oltre il tetto;
- `--limit N`: al più N chiamate Vision (per le misure).

Uso (dalla cartella `backend/`, Postgres raggiungibile, chiave OpenAI):

    python -m scripts.redescribe_figure_depicts --course <uuid>                       # conteggio
    python -m scripts.redescribe_figure_depicts --course <uuid> --apply --max-usd 1

Sul server (`dc` = alias docker compose di produzione):

    dc exec -T backend python -m scripts.redescribe_figure_depicts \
        --course <uuid> --apply --max-usd 1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.course_document_figure import CourseDocumentFigure

# Stima per figura (misura M-D1: gpt-4.1-mini, 768 px, `detail=high`): serve al
# conteggio e al tetto finché non c'è una media misurata.
ESTIMATED_USD_PER_FIGURE = 0.0012


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--course", type=uuid.UUID, help="Solo le figure di questo corso.")
    scope.add_argument("--all", action="store_true", help="Tutte le figure dell'istanza.")
    parser.add_argument("--apply", action="store_true", help="Esegue le chiamate e scrive.")
    parser.add_argument("--max-usd", type=float, help="Tetto di spesa (obbligatorio con --apply).")
    parser.add_argument("--limit", type=int, help="Al più N chiamate Vision.")
    parser.add_argument("--concurrency", type=int, default=4, help="Chiamate in parallelo.")
    return parser


@dataclass
class Outcome:
    candidates: int = 0
    copied: int = 0
    described: int = 0
    propagated: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    stopped_by_budget: bool = False
    errors: list[str] = field(default_factory=list)


def _stale_depicts() -> Any:
    from app.models.course_document_figure import CourseDocumentFigure
    from app.services.openai_figure_describe_service import DEPICTS_VERSION

    return or_(
        CourseDocumentFigure.depicts.is_(None),
        CourseDocumentFigure.depicts["v"].astext.is_(None),
        CourseDocumentFigure.depicts["v"].astext != str(DEPICTS_VERSION),
    )


async def candidates(db: AsyncSession, course_id: uuid.UUID | None) -> list[CourseDocumentFigure]:
    """Figure da completare: prima le fonti delle descrizioni, poi le copie."""
    from app.models.course_document_figure import CourseDocumentFigure

    query = select(CourseDocumentFigure).where(
        CourseDocumentFigure.status == "ready",
        CourseDocumentFigure.described_at.is_not(None),
        CourseDocumentFigure.storage_path.is_not(None),
        _stale_depicts(),
    )
    if course_id is not None:
        query = query.where(CourseDocumentFigure.course_id == course_id)
    query = query.order_by(
        CourseDocumentFigure.describe_source_id.is_not(None),
        CourseDocumentFigure.course_id,
        CourseDocumentFigure.document_id,
        CourseDocumentFigure.page.asc().nulls_last(),
        CourseDocumentFigure.locator,
    )
    return list((await db.execute(query)).scalars().all())


async def _source_depicts(db: AsyncSession, row: CourseDocumentFigure) -> dict[str, Any] | None:
    from app.models.course_document_figure import CourseDocumentFigure
    from app.services.openai_figure_describe_service import depicts_current

    if row.describe_source_id is None:
        return None
    source = await db.get(CourseDocumentFigure, row.describe_source_id)
    if source is not None and depicts_current(source.depicts):
        return dict(source.depicts or {})
    return None


async def _describe_input(db: AsyncSession, row: CourseDocumentFigure) -> Any:
    from app.models.course import Course
    from app.models.course_document import CourseDocument
    from app.services.document_figures import storage as figure_storage
    from app.services.figure_attribution import attribution_source
    from app.services.openai_figure_describe_service import DescribeInput

    course = await db.get(Course, row.course_id)
    doc = await db.get(CourseDocument, row.document_id) if row.document_id else None
    src = attribution_source(row, doc)
    title = (src.title or src.fallback_name) if src is not None else None
    image = await asyncio.to_thread(figure_storage.read, str(row.storage_path))
    return DescribeInput(
        image=image,
        caption=row.source_caption,
        context=row.context_excerpt,
        document_title=title,
        language_code=course.language_code if course is not None else "it",
    )


async def _propagate(db: AsyncSession, source_id: uuid.UUID, depicts: dict[str, Any]) -> int:
    """Stesso `depicts` sulle copie della descrizione (qualunque stato)."""
    from app.models.course_document_figure import CourseDocumentFigure

    result = await db.execute(
        update(CourseDocumentFigure)
        .where(CourseDocumentFigure.describe_source_id == source_id, _stale_depicts())
        .values(depicts=depicts)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def redescribe(
    db: AsyncSession,
    *,
    course_id: uuid.UUID | None,
    max_usd: float,
    limit: int | None = None,
    concurrency: int = 4,
) -> Outcome:
    """Completa `depicts` entro `max_usd`. Commit a ogni lotto."""
    from app.services.course_document_figures_worker import merge_usage
    from app.services.openai_client import OpenAINotConfiguredError
    from app.services.openai_figure_describe_service import (
        OpenAIFigureDescribeError,
        depicts_current,
        depicts_payload,
        describe_figure,
    )

    rows = await candidates(db, course_id)
    out = Outcome(candidates=len(rows))
    # Una copia la cui fonte è fra le candidate riceve `depicts` dalla
    # propagazione quando la fonte è descritta: nessuna chiamata per lei.
    pending_sources = {row.id for row in rows}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    calls = 0
    batch_size = max(1, concurrency) * 2
    index = 0
    while index < len(rows):
        batch: list[tuple[CourseDocumentFigure, Any]] = []
        while index < len(rows) and len(batch) < batch_size:
            row = rows[index]
            await db.refresh(row, attribute_names=["depicts"])
            if depicts_current(row.depicts) or row.describe_source_id in pending_sources:
                index += 1  # completata (o da completare) dalla propagazione
                continue
            copied = await _source_depicts(db, row)
            if copied is not None:
                row.depicts = copied
                out.copied += 1
                index += 1
                continue
            mean = out.cost_usd / calls if calls else ESTIMATED_USD_PER_FIGURE
            if limit is not None and calls + len(batch) >= limit:
                break
            if out.cost_usd + mean * (len(batch) + 1) > max_usd:
                out.stopped_by_budget = True
                break
            batch.append((row, await _describe_input(db, row)))
            index += 1
        if not batch:
            await db.commit()
            break

        async def call(item: Any) -> Any:
            async with semaphore:
                return await describe_figure(item)

        results = await asyncio.gather(*(call(i) for _r, i in batch), return_exceptions=True)
        calls += len(batch)
        now = datetime.now(UTC)
        for (row, _item), result in zip(batch, results, strict=True):
            if isinstance(result, OpenAINotConfiguredError):
                await db.commit()
                raise result
            if isinstance(result, BaseException):
                out.failed += 1
                usage = getattr(result, "usage", None)
                if isinstance(usage, dict):
                    out.cost_usd += float(usage.get("cost_usd") or 0.0)
                    row.vision_usage = merge_usage(row.vision_usage, usage)
                    row.vision_usage_at = now
                if isinstance(result, OpenAIFigureDescribeError | OSError):
                    out.errors.append(f"{row.id}: {str(result)[:160]}")
                    continue
                raise result
            description, usage = result
            payload = depicts_payload(description.depicts)
            row.depicts = payload
            row.vision_usage = merge_usage(row.vision_usage, usage)
            row.vision_usage_at = now
            out.cost_usd += float(usage.get("cost_usd") or 0.0)
            out.described += 1
            out.propagated += await _propagate(db, row.id, payload)
        await db.commit()
        if out.stopped_by_budget or (limit is not None and calls >= limit):
            break
    return out


async def run(args: argparse.Namespace) -> int:
    from app.db.session import async_session_factory, engine

    course_id = None if args.all else args.course
    try:
        async with async_session_factory() as db:
            if not args.apply:
                rows = await candidates(db, course_id)
                sources = [r for r in rows if r.describe_source_id is None]
                print(f"figure senza depicts corrente: {len(rows)}")
                print(f"  fonti (una chiamata Vision ciascuna): {len(sources)}")
                print(f"  copie di una descrizione: {len(rows) - len(sources)}")
                estimate = len(sources) * ESTIMATED_USD_PER_FIGURE
                print(f"costo stimato: {estimate:.2f} $ (dry-run: nessuna scrittura)")
                return 0
            if args.max_usd is None or args.max_usd <= 0:
                print("--max-usd è obbligatorio con --apply", file=sys.stderr)
                return 2
            out = await redescribe(
                db,
                course_id=course_id,
                max_usd=float(args.max_usd),
                limit=args.limit,
                concurrency=args.concurrency,
            )
    finally:
        await engine.dispose()
    print(
        f"candidate {out.candidates} · descritte {out.described} · copiate {out.copied} · "
        f"propagate {out.propagated} · fallite {out.failed} · costo {out.cost_usd:.4f} $"
    )
    if out.stopped_by_budget:
        print("fermato dal tetto di spesa: rilanciare con un --max-usd più alto per finire")
    for error in out.errors[:20]:
        print(f"  errore {error}")
    return 1 if out.failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
