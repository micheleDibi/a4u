"""Ri-ritaglio sul posto delle figure di fonte già estratte (doc 18 §22).

Decisione V2 (giuria Q6): le figure estratte col ritaglio v1 passano al
ritaglio v2 SENZA cambiare UUID, anche quelle già collocate nelle lezioni:
la riga «Fonte» resta, cambiano i byte e gli ingressi della risoluzione;
le lezioni migliorano alla prossima esportazione, senza rigenerazione.
Niente Vision: la descrizione resta quella del ritaglio v1.

- Richiesta a lease (`course_document.figures_recrop_requested_at`): lo
  script la accoda; il worker delle figure prende un documento quando la
  richiesta è scaduta, la sposta avanti di `RECROP_LEASE` e lavora sotto
  `HEAVY_JOB_LOCK` con il figlio senza rilevatore (comando `recrop`); a
  lavoro finito torna NULL. Un documento in estrazione non si ri-ritaglia.
- Verifica d'identità: il figlio riproduce il ritaglio v1 ai dpi salvati e
  ne confronta il phash con quello salvato; se differisce (file cambiato,
  bbox di un'altra versione) la figura resta v1.
- UPDATE condizionale (stesso UUID, `crop_version = 1` e stesso file):
  una riga cambiata nel frattempo non si tocca.
- Reversibile: `recrop_previous` conserva percorsi e metadati v1;
  `revert` li ripristina e cancella i file v2. I file v1 restano nello
  storage finché `purge_replaced` (comando manuale, dopo
  `RETENTION_DAYS`) non li cancella: da lì il ripristino non è più
  possibile.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.logging import get_logger
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.services import document_figures_service, remote_storage
from app.services.document_figures import CROP_VERSION
from app.services.document_figures import storage as figure_storage
from app.services.document_figures.runner import ChildSession, ExtractionChildError
from app.services.source_figure_resolution import ResolutionInputs, selection_class

log = get_logger("app.document_figures_recrop")

# Lease lungo quanto il lavoro più lungo (un documento a lotti): un worker
# morto la lascia riprendere dopo, uno vivo non se la vede portare via.
RECROP_LEASE = timedelta(hours=3)
# Figure per giro del figlio: HEAVY_JOB_LOCK si prende e si rilascia per
# lotto, così le estrazioni e la letteratura non aspettano un documento
# intero.
RECROP_BATCH = 40
RECROP_ATTEMPTS_MAX = 3
# Distanza massima fra il phash del ritaglio v1 riprodotto e quello salvato.
IDENTITY_MAX_DISTANCE = 4
RETENTION_DAYS = 14
_BASE_SECONDS = 120.0
_SECONDS_PER_FIGURE = 20.0
_RECROP_MIMES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
_EXTRACTION_ACTIVE = ("pending", "processing")
# Campi del ritaglio v1 conservati per il ripristino.
PREVIOUS_FIELDS: tuple[str, ...] = (
    "storage_path",
    "preview_path",
    "mime_type",
    "width",
    "height",
    "dpi",
    "byte_size",
    "is_vector",
    "phash",
    "native_ppi",
    "natural_width_mm",
    "crop_mode",
    "crop_version",
)


# Campi dell'evento del figlio che servono alla classe di risoluzione.
_INPUT_KEYS = ("width", "height", "dpi", "native_ppi", "natural_width_mm", "is_vector")


def _now() -> datetime:
    return datetime.now(UTC)


def eligible_clause() -> ColumnElement[bool]:
    """Figure v1 di un documento del corso, con file e phash: le pronte e
    le superate ancora collocate in una lezione (U1)."""
    return and_(
        CourseDocumentFigure.document_id.is_not(None),
        CourseDocumentFigure.source_kind == "uploaded",
        CourseDocumentFigure.crop_version == 1,
        CourseDocumentFigure.storage_path.is_not(None),
        CourseDocumentFigure.phash.is_not(None),
        or_(
            CourseDocumentFigure.status == "ready",
            CourseDocumentFigure.reject_reason == "superseded",
        ),
    )


async def eligible_rows(db: AsyncSession, document_id: uuid.UUID) -> list[CourseDocumentFigure]:
    rows = await db.execute(
        select(CourseDocumentFigure)
        .where(CourseDocumentFigure.document_id == document_id, eligible_clause())
        .order_by(CourseDocumentFigure.locator)
    )
    return list(rows.scalars().all())


def child_item(row: CourseDocumentFigure) -> dict[str, Any] | None:
    """Voce per il figlio; None se mancano gli ingressi (PDF senza bbox o
    dpi: il ritaglio v1 non si può riprodurre)."""
    item: dict[str, Any] = {
        "key": row.id.hex,
        "locator": row.locator,
        "phash": row.phash,
    }
    if row.page is not None and isinstance(row.bbox, dict):
        if not row.dpi:
            return None
        item.update(page=row.page, bbox=row.bbox, dpi=row.dpi)
    return item


async def busy_documents(db: AsyncSession, course_id: uuid.UUID | None = None) -> int:
    """Documenti con un'estrazione o un ri-ritaglio in corso: lo script
    rifiuta di partire finché sono > 0."""
    query = select(CourseDocument.id).where(
        or_(
            CourseDocument.figures_status.in_(_EXTRACTION_ACTIVE),
            CourseDocument.figures_recrop_requested_at.is_not(None),
        )
    )
    if course_id is not None:
        query = query.where(CourseDocument.course_id == course_id)
    return len((await db.execute(query)).scalars().all())


async def request(db: AsyncSession, document_ids: list[uuid.UUID]) -> int:
    """Accoda il ri-ritaglio (richiesta subito scaduta, il worker la prende)."""
    if not document_ids:
        return 0
    now = _now()
    await db.execute(
        update(CourseDocument)
        .where(CourseDocument.id.in_(document_ids))
        .values(
            figures_recrop_requested_at=now, figures_recrop_stats={"requested_at": now.isoformat()}
        )
    )
    await db.commit()
    return len(document_ids)


async def _take(db: AsyncSession, doc: CourseDocument, now: datetime) -> CourseDocument | None:
    """Sposta avanti il lease e conta il tentativo; oltre il tetto chiude la
    richiesta (un crash del processo non la fa riprendere all'infinito)."""
    stats = dict(doc.figures_recrop_stats or {})
    attempts = int(stats.get("attempts") or 0)
    if attempts >= RECROP_ATTEMPTS_MAX:
        stats.update(error="attempts_exhausted", finished_at=now.isoformat())
        doc.figures_recrop_stats = stats
        doc.figures_recrop_requested_at = None
        await db.commit()
        log.warning("document_figures_recrop_exhausted", doc_id=str(doc.id))
        return None
    stats["attempts"] = attempts + 1
    doc.figures_recrop_stats = stats
    doc.figures_recrop_requested_at = now + RECROP_LEASE
    await db.commit()
    return doc


async def claim(db: AsyncSession, document_id: uuid.UUID) -> CourseDocument | None:
    """Claim di un documento preciso (ri-ritaglio `--inline` dello script):
    stesso lease del worker, così nessun altro lo prende nel frattempo."""
    now = _now()
    doc = (
        await db.execute(
            select(CourseDocument)
            .where(
                CourseDocument.id == document_id,
                CourseDocument.figures_recrop_requested_at.is_not(None),
                CourseDocument.figures_recrop_requested_at <= now,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if doc is None:
        return None
    return await _take(db, doc, now)


async def claim_next(db: AsyncSession) -> CourseDocument | None:
    """Un documento con la richiesta scaduta e senza estrazione in corso;
    la richiesta si sposta avanti di un lease (un worker morto la lascia
    riprendere dopo)."""
    now = _now()
    doc = (
        await db.execute(
            select(CourseDocument)
            .where(
                CourseDocument.figures_recrop_requested_at.is_not(None),
                CourseDocument.figures_recrop_requested_at <= now,
                or_(
                    CourseDocument.figures_status.is_(None),
                    CourseDocument.figures_status.not_in(_EXTRACTION_ACTIVE),
                ),
            )
            .order_by(CourseDocument.figures_recrop_requested_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if doc is None:
        return None
    return await _take(db, doc, now)


@dataclass
class RecropResult:
    """Esito del figlio per un documento (nessuna scrittura).

    `snapshots`: i valori v1 di ogni riga fotografati PRIMA del figlio
    (id, locator e `PREVIOUS_FIELDS`): l'UPDATE condizionale confronta con
    questi, non con l'oggetto ORM, che dopo un commit si rilegge dal DB e
    nasconderebbe una modifica concorrente."""

    snapshots: list[dict[str, Any]]
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    workdir: Path | None = None


async def run_child(
    doc: CourseDocument, rows: list[CourseDocumentFigure], workdir: Path, config: Any
) -> RecropResult:
    """Scarica il documento in `workdir` e fa girare il figlio `recrop`."""
    result = RecropResult(
        snapshots=[{"id": r.id, "locator": r.locator, **previous_of(r)} for r in rows],
        workdir=workdir,
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        item = child_item(row)
        if item is None:
            result.skipped[row.id.hex] = "missing_inputs"
        else:
            items.append(item)
    if not items:
        return result
    source = workdir / f"source{Path(doc.file_path).suffix.lower() or '.bin'}"
    if not source.exists():  # i lotti successivi riusano il file scaricato
        await asyncio.to_thread(
            remote_storage.get_storage().download_to,
            remote_storage.uploads_key(doc.file_path),
            source,
        )
    session = ChildSession(config, workdir)
    try:
        events = await session.recrop(
            source_name=source.name,
            mime=doc.mime_type,
            figures=items,
            identity_max_distance=IDENTITY_MAX_DISTANCE,
            timeout_seconds=_BASE_SECONDS + _SECONDS_PER_FIGURE * len(items),
        )
    finally:
        await session.close()
    result.events = {str(e.get("key")): e for e in events}
    return result


def previous_of(row: CourseDocumentFigure) -> dict[str, Any]:
    return {name: getattr(row, name) for name in PREVIOUS_FIELDS}


async def apply_result(
    db: AsyncSession, doc: CourseDocument, result: RecropResult
) -> dict[str, Any]:
    """Carica i ritagli v2 e aggiorna le righe (UPDATE condizionale)."""
    assert result.workdir is not None
    # Id letti subito: dopo un commit l'oggetto scade e rileggerlo in
    # AsyncSession solleverebbe MissingGreenlet.
    course_id, doc_id = doc.course_id, doc.id
    stats: dict[str, Any] = {
        "figures": len(result.snapshots),
        "recropped": 0,
        "identity_mismatch": 0,
        "errors": 0,
        "changed": 0,
        "kept_unusable": 0,
        "missing_inputs": len(result.skipped),
    }
    for snap in result.snapshots:
        figure_id: uuid.UUID = snap["id"]
        event = result.events.get(figure_id.hex)
        if event is None:
            continue
        if event.get("error"):
            stats["errors"] += 1
            log.warning(
                "document_figure_recrop_error",
                figure_id=str(figure_id),
                code=event.get("error"),
                message=event.get("message"),
            )
            continue
        if not (event.get("identity") or {}).get("ok"):
            stats["identity_mismatch"] += 1
            continue
        # Una figura che col ritaglio v2 sarebbe sotto il minimo resta v1:
        # già collocata, uscirebbe di pochi millimetri (etichette vettoriali
        # comprese); il badge dell'editor la segnala al docente.
        inputs = ResolutionInputs.from_mapping(
            {key: event.get(key) for key in _INPUT_KEYS}, source_kind="uploaded"
        )
        if selection_class(inputs) == "unusable":
            stats["kept_unusable"] += 1
            continue
        data = await asyncio.to_thread((result.workdir / Path(str(event["file"])).name).read_bytes)
        preview = await asyncio.to_thread(
            (result.workdir / Path(str(event["preview_file"])).name).read_bytes
        )
        sha12 = hashlib.sha256(data).hexdigest()[:12]
        ext = "jpg" if event.get("mime") == "image/jpeg" else "png"
        locator = str(snap["locator"])[:60]
        # `-v2`: mai lo stesso nome del ritaglio v1 (il revert cancella i v2).
        storage_path = figure_storage.figure_path(course_id, doc_id, f"{locator}-{sha12}-v2.{ext}")
        preview_path = figure_storage.figure_path(
            course_id, doc_id, f"{locator}-{sha12}-v2-preview.jpg"
        )
        await asyncio.to_thread(figure_storage.upload, storage_path, data)
        await asyncio.to_thread(figure_storage.upload, preview_path, preview)
        done = await db.execute(
            update(CourseDocumentFigure)
            .where(
                CourseDocumentFigure.id == figure_id,
                CourseDocumentFigure.crop_version == 1,
                CourseDocumentFigure.storage_path == snap["storage_path"],
            )
            .values(
                storage_path=storage_path,
                preview_path=preview_path,
                mime_type=event.get("mime"),
                width=event.get("width"),
                height=event.get("height"),
                dpi=event.get("dpi"),
                byte_size=len(data),
                is_vector=event.get("is_vector"),
                phash=event.get("phash"),
                native_ppi=event.get("native_ppi") or None,
                natural_width_mm=event.get("natural_width_mm") or None,
                crop_mode=event.get("crop_mode"),
                crop_version=CROP_VERSION,
                recropped_at=_now(),
                recrop_previous={name: snap[name] for name in PREVIOUS_FIELDS},
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        if getattr(done, "rowcount", 0) == 1:
            stats["recropped"] += 1
        else:
            # Riga cambiata dopo la fotografia: resta com'è. I file v2 si
            # tolgono solo se la riga non li usa (un altro ri-ritaglio dello
            # stesso documento produce gli stessi nomi, deterministici).
            stats["changed"] += 1
            current = (
                await db.execute(
                    select(
                        CourseDocumentFigure.storage_path, CourseDocumentFigure.preview_path
                    ).where(CourseDocumentFigure.id == figure_id)
                )
            ).first()
            in_use = set(current or ())
            await document_figures_service.delete_files(
                [p for p in (storage_path, preview_path) if p not in in_use]
            )
    return stats


async def _finish(db: AsyncSession, doc_id: uuid.UUID, stats: dict[str, Any]) -> None:
    doc = await db.get(CourseDocument, doc_id, populate_existing=True)
    if doc is None:
        return
    merged = {**dict(doc.figures_recrop_stats or {}), **stats, "finished_at": _now().isoformat()}
    doc.figures_recrop_stats = merged
    doc.figures_recrop_requested_at = None
    await db.commit()


async def process(
    db: AsyncSession, doc: CourseDocument, config: Any, *, lock: asyncio.Lock | None = None
) -> dict[str, Any]:
    """Un giro completo del ri-ritaglio di un documento (worker o `--inline`),
    a lotti di `RECROP_BATCH` figure; `lock` (HEAVY_JOB_LOCK nel worker) si
    tiene solo mentre gira il figlio di un lotto. Un lotto fallito lascia la
    richiesta in coda: al giro dopo restano solo le figure ancora v1."""
    doc_id = doc.id
    attempts = int((doc.figures_recrop_stats or {}).get("attempts") or 0)
    stats: dict[str, Any]
    if doc.mime_type not in _RECROP_MIMES:
        stats = {"error": "unsupported_format"}
        await _finish(db, doc_id, stats)
        return stats
    rows = await eligible_rows(db, doc_id)
    if not rows:
        stats = {"figures": 0}
        await _finish(db, doc_id, stats)
        return stats
    started = time.monotonic()
    workdir = Path(tempfile.mkdtemp(prefix="a4u-recrop-"))
    totals: dict[str, Any] = {}
    try:
        for first in range(0, len(rows), RECROP_BATCH):
            batch = rows[first : first + RECROP_BATCH]
            step = "child"
            try:
                if lock is not None:
                    async with lock:
                        result = await run_child(doc, batch, workdir, config)
                else:
                    result = await run_child(doc, batch, workdir, config)
                step = "apply"
                batch_stats = await apply_result(db, doc, result)
            except remote_storage.StorageFileNotFound:
                stats = {"error": "source_missing"}
                await _finish(db, doc_id, stats)
                return stats
            except Exception as exc:
                # Figlio, storage o DB: conta come tentativo (niente giri
                # infiniti a ogni lease); la richiesta resta in coda.
                await db.rollback()
                code = exc.code if isinstance(exc, ExtractionChildError) else f"{step}_failed"
                log.warning(
                    "document_figures_recrop_failed",
                    doc_id=str(doc_id),
                    code=code,
                    error=str(exc)[:300],
                )
                if attempts >= RECROP_ATTEMPTS_MAX:
                    stats = {"error": "attempts_exhausted", "last_error": code, **totals}
                    await _finish(db, doc_id, stats)
                    return stats
                return {"error": code, "retry": True, **totals}
            for key, value in batch_stats.items():
                totals[key] = int(totals.get(key) or 0) + int(value)
        totals["seconds"] = round(time.monotonic() - started, 1)
        await _finish(db, doc_id, totals)
        log.info("document_figures_recropped", doc_id=str(doc_id), **totals)
        return totals
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, True)


async def revert(
    db: AsyncSession, *, course_id: uuid.UUID, document_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Ripristina il ritaglio v1 delle figure ri-ritagliate (i file v1 sono
    ancora nello storage finché non si esegue `purge_replaced`)."""
    query = select(CourseDocumentFigure).where(
        CourseDocumentFigure.course_id == course_id,
        CourseDocumentFigure.recrop_previous.is_not(None),
    )
    if document_id is not None:
        query = query.where(CourseDocumentFigure.document_id == document_id)
    rows = list((await db.execute(query)).scalars().all())
    stale: list[str] = []
    reverted = 0
    for row in rows:
        previous = dict(row.recrop_previous or {})
        # Solo percorsi del corso della riga (G7): mai file di un altro corso.
        if not previous.get("storage_path") or not all(
            figure_storage.belongs_to_course(previous.get(key), row.course_id)
            for key in ("storage_path", "preview_path")
            if previous.get(key)
        ):
            continue
        stale.extend(p for p in (row.storage_path, row.preview_path) if p)
        for name in PREVIOUS_FIELDS:
            if name in previous:
                setattr(row, name, previous[name])
        row.crop_version = int(previous.get("crop_version") or 1)
        row.recrop_previous = None
        row.recropped_at = None
        reverted += 1
    await db.commit()
    await document_figures_service.delete_files(stale)
    return {"reverted": reverted}


async def purge_replaced(
    db: AsyncSession, *, course_id: uuid.UUID | None, older_than: timedelta, apply: bool
) -> dict[str, int]:
    """Cancella i file v1 dei ri-ritagli più vecchi di `older_than` (da lì
    il ripristino non è più possibile). Senza `apply`: solo il conteggio."""
    cutoff = _now() - older_than
    query = select(CourseDocumentFigure).where(
        CourseDocumentFigure.recrop_previous.is_not(None),
        CourseDocumentFigure.recropped_at.is_not(None),
        CourseDocumentFigure.recropped_at < cutoff,
    )
    if course_id is not None:
        query = query.where(CourseDocumentFigure.course_id == course_id)
    rows = list((await db.execute(query)).scalars().all())
    paths = [
        path
        for row in rows
        for path in (
            (row.recrop_previous or {}).get("storage_path"),
            (row.recrop_previous or {}).get("preview_path"),
        )
        if path
        and path not in (row.storage_path, row.preview_path)
        and figure_storage.belongs_to_course(path, row.course_id)
    ]
    if apply:
        for row in rows:
            row.recrop_previous = None
        await db.commit()
        await document_figures_service.delete_files(paths)
    return {"figures": len(rows), "files": len(paths)}
