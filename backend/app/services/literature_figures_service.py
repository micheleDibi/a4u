"""Buchi di figure di fonte e letteratura aperta (WP5).

Prima della Fase 3 ogni lezione ordinaria passa da una verifica (job del
worker `course_lesson_figures_gap_worker`; la Fase 3 aspetta con un filtro
SQL e un tetto, come per le estrazioni):

1. figure di fonte PERTINENTI già nel catalogo del corso per la lezione
   (stesse regole del catalogo del PROMPT 3: `relevant` di
   `lesson_figure_selection`, modo select, filtri di qualità). Se sono
   almeno `FIGURE_SOURCE_MIN_PER_LESSON` → `done`, nessuna chiamata;
2. documenti con un'estrazione in coda o in corso, richiesta da meno di
   `FIGURE_WAIT_MAX_MINUTES` → `skipped` (`documents_extracting`): le loro
   figure arrivano a breve. Un documento con estrazione MAI richiesta non
   blocca: la letteratura parte (la precedenza delle figure dei documenti
   estratti dopo, alla rigenerazione, arriva con l'assegnazione del piano
   delle figure, WP6, e la riapertura delle verifiche con WP7);
3. altrimenti la letteratura aperta integra fino al budget (b): termini di
   ricerca in inglese (PROMPT 20), poi Wikimedia Commons (licenze libere,
   rendering PNG di Commons) e, con `OPENALEX_API_KEY` e l'estrazione
   accesa, i PDF open access di OpenAlex con licenza CC o pubblico dominio
   (ritagli estratti con lo stesso processo figlio dei documenti, sotto
   `HEAVY_JOB_LOCK`). Ogni candidata passa dai limiti di dimensione
   (`image_limits`), dal controllo dei duplicati (phash) e dalla verifica
   Vision di pertinenza (PROMPT 20); le tenute diventano righe del catalogo
   del corso SENZA documento (`source_kind` wikimedia/openalex,
   attribuzione e licenza della fonte congelate, `external_id` unico per
   corso). Nessun `CourseDocument`: riassunti, selezione dei documenti,
   testo e riferimenti delle lezioni non cambiano.

Tetti: candidate valutate per lezione, figure esterne per corso, tempo
totale. Il costo delle chiamate AI va in `course_lesson.figures_gap_usage`
(dashboard admin, fase `figures_gap`). Errori recuperabili (rete, 429,
5xx) → `GapRetryError`: il worker rimette la lezione in coda fino al tetto.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import (
    FIGURE_ATTRIBUTION_IDENTIFYING_KEYS,
    CourseDocumentFigure,
)
from app.models.course_lesson import CourseLesson
from app.services import image_limits, openalex_client, source_figure_catalog, wikimedia_client
from app.services import openai_figure_relevance_service as relevance
from app.services.document_figures import CROP_VERSION, cropper
from app.services.document_figures import storage as figure_storage
from app.services.document_figures.phash import hamming, phash
from app.services.document_figures_service import EXTRACTABLE_MIMES
from app.services.figure_attribution import figure_number_from_label
from app.services.figure_need_matching import NeedKey, match, tokens
from app.services.lesson_document_selection import build_query_profile, terms
from app.services.openai_client import OpenAINotConfiguredError
from app.services.openai_figure_describe_service import depicts_current, depicts_payload
from app.services.remote_storage import StorageError
from app.services.safe_http import SafeFetchError
from app.services.source_caption import third_party_credit
from app.services.source_figure_policy import OPEN_LICENSES
from app.services.source_figure_resolution import ResolutionInputs, selection_class

log = get_logger("app.literature_figures")

GAP_ACTIVE = ("pending", "processing")
EXTERNAL_KINDS = ("wikimedia", "openalex")
DUPLICATE_DISTANCE = 4
EXTRACTION_VERSION = 1
# Ritagli di un PDF OpenAlex valutati dalla Vision (i più vicini alla
# lezione per didascalia) e lavori OpenAlex per ricerca.
OPENALEX_FIGURES_PER_WORK = 3
OPENALEX_WORKS_PER_QUERY = 5
WIKIMEDIA_FILES_PER_QUERY = 10
# Costo della copia del PDF ospitata da OpenAlex (listino della API key).
OPENALEX_COPY_USD = 0.01


class GapRetryError(Exception):
    """Errore recuperabile (rete, 429, 5xx): la lezione torna in coda. Porta
    l'usage già pagato e l'esito parziale, da salvare comunque."""

    def __init__(
        self,
        message: str,
        usage: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.stats = dict(stats or {})


def _now() -> datetime:
    return datetime.now(UTC)


def merge_usage(previous: dict[str, Any] | None, usage: dict[str, Any] | None) -> dict[str, Any]:
    """Somma cumulativa dell'usage (stessa forma di `vision_usage`)."""
    from app.services.course_document_figures_worker import merge_usage as merge

    return merge(previous, usage) if usage else dict(previous or {})


def lesson_context(course: Course, lesson: CourseLesson) -> relevance.LessonContext:
    topics: list[str] = []
    for topic in lesson.mandatory_topics or []:
        if isinstance(topic, dict):
            label = topic.get("topic") or topic.get("title")
            if isinstance(label, str) and label.strip():
                topics.append(label.strip())
    objectives = [str(o).strip() for o in lesson.learning_objectives or [] if str(o).strip()]
    return relevance.LessonContext(
        title=lesson.title or "",
        topics=tuple(topics[:8]),
        objectives=tuple(objectives[:6]),
        language_code=(course.language_code or "it").lower(),
    )


async def documents_extracting(db: AsyncSession, course_id: uuid.UUID) -> int:
    """Documenti non esclusi estraibili con un'estrazione in coda o in corso,
    richiesta da meno di `FIGURE_WAIT_MAX_MINUTES` (stesso tetto dell'attesa
    della Fase 3). L'estrazione mai richiesta (NULL) non conta: non è in
    arrivo. Con l'estrazione spenta: 0."""
    settings = get_settings()
    if not settings.figure_extraction_enabled:
        return 0
    cutoff = _now() - timedelta(minutes=int(settings.figure_wait_max_minutes))
    count = await db.scalar(
        select(func.count(CourseDocument.id)).where(
            CourseDocument.course_id == course_id,
            CourseDocument.citation_policy != "excluded",
            CourseDocument.mime_type.in_(tuple(EXTRACTABLE_MIMES)),
            CourseDocument.figures_status.in_(GAP_ACTIVE),
            CourseDocument.figures_requested_at > cutoff,
        )
    )
    return int(count or 0)


async def pertinent_figures(db: AsyncSession, course: Course, lesson: CourseLesson) -> int:
    """Figure di fonte pertinenti alla lezione già nel catalogo del corso."""
    result = await source_figure_catalog.build_catalog(db, course, lesson)
    if result is None:
        return 0
    return int(result.catalog.stats.get("relevant") or 0)


async def external_figures(db: AsyncSession, course_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count(CourseDocumentFigure.id)).where(
            CourseDocumentFigure.course_id == course_id,
            CourseDocumentFigure.source_kind.in_(EXTERNAL_KINDS),
        )
    )
    return int(count or 0)


@dataclass
class _NeedSearch:
    """Un fabbisogno scoperto cercato nella letteratura aperta."""

    need: dict[str, Any]
    key: NeedKey
    cap: int
    evaluated: int = 0
    found: uuid.UUID | None = None
    kept_other: int = 0

    @property
    def need_id(self) -> str:
        return str(self.need.get("need_id") or "")


# Stima minima del costo di una Vision del PROMPT 20 (gpt-4.1-mini, 768 px),
# finché il giro non ne ha misurata una più cara.
VISION_ESTIMATE_USD = 0.0012


@dataclass
class _Run:
    # Solo valori semplici: dopo un rollback gli oggetti ORM scadono e
    # rileggerli in AsyncSession solleverebbe MissingGreenlet.
    course_id: uuid.UUID
    profile: dict[str, float]
    context: relevance.LessonContext
    target: int
    max_candidates: int
    deadline: float
    hashes: list[str]
    known_ids: set[str]
    usage: dict[str, Any] | None = None
    kept: int = 0
    evaluated: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    # Editori che hanno rifiutato il download (401/403): nello stesso giro si
    # passa subito alla copia di OpenAlex.
    blocked_hosts: set[str] = field(default_factory=set)
    # Copia di OpenAlex non più tentata (chiave rifiutata o credito del
    # giorno finito, o tetto dei PDF a pagamento).
    content_off: bool = False
    # Piano delle figure (WP7): fabbisogno cercato ora, tetti della verifica.
    current: _NeedSearch | None = None
    lesson_id: uuid.UUID | None = None
    max_cost_usd: float | None = None
    paid_pdfs: int = 0
    max_paid_pdfs: int | None = None
    # Costo già speso nei tentativi precedenti della STESSA verifica (i
    # retry): il tetto in dollari vale per verifica, non per tentativo.
    spent_before_usd: float = 0.0
    # Costo della Vision più cara vista nel giro: stima della prossima, per
    # fermarsi PRIMA di superare il tetto.
    vision_estimate_usd: float = VISION_ESTIMATE_USD
    # Copie OpenAlex già pagate nel giro (per lavoro) e lavori già estratti
    # per il fabbisogno in corso: la seconda ricerca non ripaga né riestrae.
    paid_copies: dict[str, bytes] = field(default_factory=dict)
    seen_works: set[tuple[str, str]] = field(default_factory=set)
    cost_capped: bool = False

    @property
    def cost_usd(self) -> float:
        return self.spent_before_usd + float((self.usage or {}).get("cost_usd") or 0.0)

    @property
    def exhausted(self) -> bool:
        """Fine della verifica (tetti della lezione)."""
        return (
            self.kept >= self.target
            or self.evaluated >= self.max_candidates
            or time.monotonic() > self.deadline
            or (self.max_cost_usd is not None and self.cost_usd >= self.max_cost_usd)
            or self.cost_capped
        )

    def vision_over_cap(self) -> bool:
        """La prossima Vision porterebbe la verifica oltre il tetto."""
        return (
            self.max_cost_usd is not None
            and self.cost_usd + self.vision_estimate_usd > self.max_cost_usd
        )

    @property
    def done(self) -> bool:
        """Fine della ricerca in corso: la verifica, o il fabbisogno cercato
        (trovato o al suo tetto di candidate)."""
        if self.exhausted:
            return True
        current = self.current
        return current is not None and (
            current.found is not None or current.evaluated >= current.cap
        )

    def count(self, key: str) -> None:
        self.stats[key] = int(self.stats.get(key) or 0) + 1


# Quota massima di figure dei documenti senza `depicts` corrente oltre la
# quale la verifica per fabbisogno non cerca nella letteratura.
DEPICTS_MISSING_MAX_SHARE = 0.2


async def _depicts_missing(db: AsyncSession, course_id: uuid.UUID) -> tuple[int, int]:
    """(figure dei documenti pronte senza `depicts` corrente, figure pronte)."""
    rows = await db.execute(
        select(CourseDocumentFigure.depicts).where(
            CourseDocumentFigure.course_id == course_id,
            CourseDocumentFigure.status == "ready",
            CourseDocumentFigure.source_kind == "uploaded",
            CourseDocumentFigure.excluded_by_user.is_(False),
        )
    )
    values = list(rows.scalars().all())
    return sum(1 for v in values if not depicts_current(v)), len(values)


def _spent(run: _Run) -> float:
    """Costo della verifica fino a qui (tentativi precedenti compresi)."""
    return round(run.cost_usd, 6)


async def _course_hashes(db: AsyncSession, course_id: uuid.UUID) -> list[str]:
    rows = await db.execute(
        select(CourseDocumentFigure.phash).where(
            CourseDocumentFigure.course_id == course_id,
            CourseDocumentFigure.phash.is_not(None),
            CourseDocumentFigure.status == "ready",
        )
    )
    return [h for h in rows.scalars().all() if h]


async def _course_external_ids(db: AsyncSession, course_id: uuid.UUID) -> set[str]:
    rows = await db.execute(
        select(CourseDocumentFigure.external_id).where(
            CourseDocumentFigure.course_id == course_id,
            CourseDocumentFigure.external_id.is_not(None),
        )
    )
    return {i for i in rows.scalars().all() if i}


@dataclass(frozen=True)
class CropMeta:
    """Come si è ottenuto il file di una candidata: ingressi della
    risoluzione effettiva (doc 18 §22), salvati sulla riga. Prima si
    perdevano dpi, `is_vector` e bbox dei ritagli OpenAlex (D9)."""

    dpi: int | None = None
    is_vector: bool | None = None
    bbox: dict[str, Any] | None = None
    native_ppi: float | None = None
    natural_width_mm: float | None = None
    crop_mode: str | None = None
    crop_version: int = 1

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> CropMeta:
        """Dall'evento `figure` del processo figlio (ritaglio di un PDF)."""

        def positive(key: str) -> float | None:
            try:
                value = float(event.get(key) or 0)
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None

        dpi = positive("dpi")
        bbox = event.get("bbox")
        return cls(
            dpi=int(dpi) if dpi else None,
            is_vector=event.get("is_vector") if isinstance(event.get("is_vector"), bool) else None,
            bbox=bbox if isinstance(bbox, dict) else None,
            native_ppi=positive("native_ppi"),
            natural_width_mm=positive("natural_width_mm"),
            crop_mode=event.get("crop_mode") or None,
            crop_version=int(event.get("crop_version") or 1),
        )


def _unusable(inputs: ResolutionInputs) -> bool:
    """Figura sotto la soglia minima di risoluzione (con la regola attiva):
    la si scarta prima di pagare download o Vision."""
    return bool(get_settings().figure_resolution_rules_enabled) and (
        selection_class(inputs) == "unusable"
    )


def _safe_locator(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in value.lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:60] or "x"


async def _consider(
    db: AsyncSession,
    run: _Run,
    *,
    image_bytes: bytes,
    source_kind: str,
    locator: str,
    external_id: str,
    source_title: str | None,
    source_text: str | None,
    license: str,
    license_url: str | None,
    attribution: dict[str, Any],
    source_url: str | None,
    caption: str | None,
    page: int | None = None,
    source_label: str | None = None,
    crop: CropMeta | None = None,
) -> bool:
    """Valuta una candidata e, se pertinente, la salva nel catalogo."""
    crop = crop or CropMeta()
    settings = get_settings()
    # Prima della Vision (che costa): solo licenze aperte (come Wikimedia e
    # la politica open_only) e un'attribuzione che nomini la fonte.
    if license not in OPEN_LICENSES:
        run.count("license_not_open")
        return False
    if not any(attribution.get(key) for key in FIGURE_ATTRIBUTION_IDENTIFYING_KEYS):
        run.count("attribution_missing")
        return False
    try:
        safe = await asyncio.to_thread(
            image_limits.load_image,
            image_bytes,
            max_pixels=int(settings.figure_literature_max_image_pixels),
            crop_v2=bool(settings.figure_extraction_native_crop_enabled),
        )
    except image_limits.ImageLimitError as exc:
        run.count(f"rejected_{exc.code}")
        return False
    if await asyncio.to_thread(cropper.is_blank, safe.image):
        # Vuota o uniforme: la Vision la promuoveva fidandosi del titolo.
        run.count("rejected_blank")
        return False
    digest = await asyncio.to_thread(phash, safe.image)
    if any(hamming(digest, other) <= DUPLICATE_DISTANCE for other in run.hashes):
        run.count("duplicates")
        return False
    # Risoluzione sui pixel VERI scaricati o ritagliati, prima della Vision.
    inputs = ResolutionInputs.from_mapping(
        {
            "width": safe.width,
            "height": safe.height,
            "dpi": crop.dpi,
            "native_ppi": crop.native_ppi,
            "natural_width_mm": crop.natural_width_mm,
            "is_vector": crop.is_vector,
            "bbox": crop.bbox,
        },
        source_kind=source_kind,
    )
    if _unusable(inputs):
        run.count("rejected_resolution")
        return False
    if run.vision_over_cap():
        # Tetto in dollari della verifica: ci si ferma prima della spesa.
        run.count("cost_cap")
        run.cost_capped = True
        return False
    run.evaluated += 1
    current = run.current
    if current is not None:
        current.evaluated += 1
    before = run.cost_usd
    try:
        # Senza piano la chiamata resta quella di prima (nessun fabbisogno).
        extra: dict[str, Any] = {"need": current.need} if current is not None else {}
        verdict, usage = await relevance.assess_candidate(
            safe.data, run.context, source_title=source_title, source_text=source_text, **extra
        )
    except relevance.OpenAIFigureRelevanceError as exc:
        run.usage = merge_usage(run.usage, exc.usage)
        if exc.status is None or exc.status == 429 or exc.status >= 500:
            raise GapRetryError(str(exc)) from exc
        run.count("vision_errors")
        return False
    run.usage = merge_usage(run.usage, usage)
    run.vision_estimate_usd = max(run.vision_estimate_usd, run.cost_usd - before)
    if not relevance.text_language_allowed(verdict.text_language, run.context.language_code):
        run.count("rejected_language")
        return False
    if not (
        verdict.relevant
        and verdict.is_useful_for_teaching
        and verdict.quality_score >= int(settings.figure_min_quality_score)
        and verdict.kind not in source_figure_catalog.EXCLUDED_KINDS
    ):
        run.count("not_relevant")
        return False
    # Piano delle figure: la figura copre il fabbisogno cercato? (stesso
    # abbinamento dell'assegnazione, su `depicts` della Vision). Se sì le si
    # lega il fabbisogno (`found_for_*`): l'assegnazione le riserva il posto.
    depicts = depicts_payload(verdict.depicts)
    covers = False
    if current is not None:
        probe = _Probe(
            kind=verdict.kind,
            depicts=depicts,
            description=verdict.description,
            source_caption=caption,
            keywords={"course": verdict.keywords_course, "en": verdict.keywords_en},
        )
        covers = match(current.key, probe).covers
        if not covers:
            run.count("kept_not_covering")
    # Nome non ricavabile dall'esterno (U5): lo sha di un rendering pubblico
    # di Commons sarebbe calcolabile, il suffisso casuale no.
    stem = f"{locator}-{uuid.uuid4().hex[:8]}-{hashlib.sha256(safe.data).hexdigest()[:12]}"
    ext = "jpg" if safe.mime == "image/jpeg" else "png"
    course_id = run.course_id
    storage_path = figure_storage.figure_path(course_id, None, f"{stem}.{ext}")
    preview_path = figure_storage.figure_path(course_id, None, f"{stem}-preview.jpg")
    preview = await asyncio.to_thread(cropper.preview, safe.image)
    try:
        await asyncio.to_thread(figure_storage.upload, storage_path, safe.data)
        await asyncio.to_thread(figure_storage.upload, preview_path, preview)
    except (StorageError, OSError) as exc:
        raise GapRetryError(f"storage: {exc}") from exc
    now = _now()
    row_id = uuid.uuid4()
    db.add(
        CourseDocumentFigure(
            id=row_id,
            found_for_lesson_id=run.lesson_id if covers else None,
            found_for_need_id=(current.need_id[:40] if current is not None else None)
            if covers
            else None,
            course_id=course_id,
            document_id=None,
            source_kind=source_kind,
            locator=locator,
            extraction_version=EXTRACTION_VERSION,
            engine=source_kind,
            page=page if page and page >= 1 else None,
            source_label=(source_label or None) and str(source_label)[:60],
            source_caption=caption,
            storage_path=storage_path,
            preview_path=preview_path,
            mime_type=safe.mime,
            width=safe.width,
            height=safe.height,
            byte_size=len(safe.data),
            phash=digest,
            bbox=crop.bbox,
            dpi=crop.dpi,
            is_vector=crop.is_vector,
            native_ppi=crop.native_ppi,
            natural_width_mm=crop.natural_width_mm,
            crop_mode=crop.crop_mode,
            crop_version=crop.crop_version,
            kind=verdict.kind,
            description=verdict.description,
            keywords={"course": verdict.keywords_course, "en": verdict.keywords_en},
            quality_score=verdict.quality_score,
            legibility=verdict.legibility,
            is_useful_for_teaching=verdict.is_useful_for_teaching,
            depicts=depicts,
            described_at=now,
            describe_model=str(settings.openai_figure_relevance_model)[:80],
            status="ready",
            license=license,
            license_source=source_kind,
            license_url=(license_url or None) and license_url[:500],
            attribution=attribution,
            external_id=external_id[:200],
            source_url=(source_url or None) and source_url[:1000],
            retrieved_at=now,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        await asyncio.to_thread(figure_storage.delete, storage_path)
        await asyncio.to_thread(figure_storage.delete, preview_path)
        if "uq_course_document_figure_external" in str(exc):
            # Stessa figura esterna salvata nel frattempo: niente doppioni.
            run.count("duplicates")
        else:
            log.error("figures_gap_row_rejected", error=str(exc)[:300])
            run.count("db_rejected")
        return False
    run.hashes.append(digest)
    run.known_ids.add(external_id)
    run.kept += 1
    if current is not None:
        if covers:
            current.found = row_id
        else:
            current.kept_other += 1
    run.stats.setdefault("kept_by_source", {})
    run.stats["kept_by_source"][source_kind] = run.stats["kept_by_source"].get(source_kind, 0) + 1
    return True


async def _from_wikimedia(
    db: AsyncSession,
    run: _Run,
    queries: list[str],
    cache: dict[str, list[wikimedia_client.CommonsFile]] | None = None,
) -> None:
    for query in queries:
        if run.done:
            return
        try:
            if cache is not None and query in cache:
                files = cache[query]
            else:
                files = await wikimedia_client.search_files(
                    query, limit=WIKIMEDIA_FILES_PER_QUERY, language=run.context.language_code
                )
                if cache is not None:
                    cache[query] = files
        except SafeFetchError as exc:
            if exc.recoverable:
                raise GapRetryError(str(exc)) from exc
            run.count("wikimedia_errors")
            continue
        requested = int(get_settings().figure_literature_image_width)
        for item in files:
            if run.done:
                return
            if item.external_id in run.known_ids:
                continue
            if run.current is not None and not _variant_in(
                run.current.key, f"{item.title} {item.object_name or ''} {item.description or ''}"
            ):
                # Una ricerca per gruppo di varianti: la figura di un'altra
                # variante non si scarica per questo fabbisogno.
                run.count("filtered_variant")
                continue
            expected = item.expected_width_px(requested)
            if expected is not None and item.original_width and item.original_height:
                probe = ResolutionInputs.from_mapping(
                    {
                        "width": expected,
                        "height": max(1, expected * item.original_height // item.original_width),
                        "is_vector": item.is_vector,
                    },
                    source_kind="wikimedia",
                )
                if _unusable(probe):
                    # Troppo piccola per qualunque stampa: niente download.
                    run.known_ids.add(item.external_id)
                    run.count("rejected_resolution")
                    continue
            try:
                got = await wikimedia_client.download_image(item)
            except SafeFetchError as exc:
                if exc.code == "rate_limited":
                    raise GapRetryError(str(exc)) from exc
                run.count("download_errors")
                continue
            run.known_ids.add(item.external_id)
            await _consider(
                db,
                run,
                image_bytes=got.content,
                source_kind="wikimedia",
                locator=_safe_locator(f"wm-{item.page_id}"),
                external_id=item.external_id,
                source_title=item.object_name or item.title,
                source_text=item.description,
                license=item.license,
                license_url=item.license_url,
                attribution=item.attribution(),
                source_url=item.description_url,
                caption=item.description,
                crop=CropMeta(
                    is_vector=item.is_vector,
                    crop_mode="external",
                    crop_version=(
                        CROP_VERSION if get_settings().figure_extraction_native_crop_enabled else 1
                    ),
                ),
            )


class HeavyJobBusyError(Exception):
    """Un'estrazione dei documenti tiene il lock oltre la scadenza."""


async def extract_pdf_figures(
    pdf: bytes,
    *,
    max_pages: int,
    wait_seconds: float,
    select: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Ritagli di un PDF di terzi con lo stesso processo figlio dei documenti
    (motore di `FIGURE_EXTRACTION_ENGINE`, sotto `HEAVY_JOB_LOCK`, atteso al
    più `wait_seconds`). Il PDF lo apre SOLO il figlio (pagine comprese: il
    tetto `max_pages` si controlla sul numero che restituisce). `select`
    sceglie fra gli eventi dei ritagli; solo per quelli si leggono i byte
    (`data`)."""
    from app.services import course_document_figures_worker as figures_worker
    from app.services.document_figures.runner import ChildSession
    from app.services.heavy_job_lock import HEAVY_JOB_LOCK

    config = figures_worker._config()
    workdir = Path(tempfile.mkdtemp(prefix="a4u-literature-"))
    try:
        source = workdir / "source.pdf"
        await asyncio.to_thread(source.write_bytes, pdf)
        try:
            await asyncio.wait_for(HEAVY_JOB_LOCK.acquire(), timeout=max(0.1, wait_seconds))
        except TimeoutError as exc:
            raise HeavyJobBusyError("estrazione dei documenti in corso") from exc
        try:
            if figures_worker._memory_low():
                raise GapRetryError("memoria disponibile sotto la soglia")
            await figures_worker._ensure_engine(config, workdir)
            session = ChildSession(config, workdir)
            events: list[dict[str, Any]] = []
            try:
                pages = await session.start(source_name=source.name, mime="application/pdf")
                if pages > max_pages:
                    raise image_limits.ImageLimitError(
                        "too_many_pages", f"{pages} pagine oltre {max_pages}"
                    )
                block = max(1, int(get_settings().figure_extraction_block_pages))
                first = 1
                while first <= pages:
                    end = min(first + block - 1, pages)
                    result = await session.run_block(first, end)
                    events.extend(
                        e for e in result.figures if not e.get("reject_reason") and e.get("file")
                    )
                    first = end + 1
            finally:
                await session.close()
        finally:
            HEAVY_JOB_LOCK.release()
        chosen = select(events)
        return [
            {
                **event,
                "data": await asyncio.to_thread(
                    (workdir / Path(str(event["file"])).name).read_bytes
                ),
            }
            for event in chosen
        ]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _caption_score(profile: dict[str, float], caption: str | None) -> float:
    return sum(profile.get(term, 0.0) for term in terms(caption))


def _ranking_profile(run: _Run, queries: list[str], title: str | None) -> dict[str, float]:
    """Profilo per le didascalie dei PDF, per lo più in inglese: quello della
    lezione più i termini delle ricerche inglesi (PROMPT 20) e del titolo del
    lavoro."""
    profile = dict(run.profile)
    for text in (*queries, title or ""):
        for term in terms(text):
            profile[term] = max(profile.get(term, 0.0), 1.0)
    return profile


async def _work_pdf(
    run: _Run, work: openalex_client.OpenAlexWork, pdf_url: str | None, *, max_bytes: int
) -> bytes | None:
    """PDF del lavoro: prima dall'editore (gratis); se l'editore rifiuta i
    download automatici (403 di MDPI, Hindawi…) o il PDF non si scarica,
    dalla copia ospitata da OpenAlex (0,01 $ sulla API key). Mai scavalcare
    le protezioni dell'editore: si usa solo la copia di OpenAlex. None se
    non si ottiene nessun PDF."""
    host = (urlsplit(pdf_url).hostname or "").lower() if pdf_url else ""
    if pdf_url and host not in run.blocked_hosts:
        try:
            pdf = await openalex_client.download_pdf(pdf_url, max_bytes=max_bytes)
        except openalex_client.OpenAlexError as exc:
            if exc.status == 413:
                # La copia di OpenAlex è lo stesso file: troppo grande anche lei.
                run.count("rejected_too_large")
                return None
            run.count("publisher_errors")
            if exc.status in (401, 403) and host:
                run.blocked_hosts.add(host)
        else:
            run.count("downloads_publisher")
            return pdf
    if work.id in run.paid_copies:
        # Copia già pagata in questo giro (un altro fabbisogno o l'altra
        # ricerca): non si ripaga.
        return run.paid_copies[work.id]
    if run.content_off or not openalex_client.content_pdf_available(work):
        run.count("download_errors")
        return None
    if run.max_paid_pdfs is not None and run.paid_pdfs >= run.max_paid_pdfs:
        run.count("paid_pdf_cap")
        return None
    if run.max_cost_usd is not None and run.cost_usd + OPENALEX_COPY_USD > run.max_cost_usd:
        run.count("cost_cap")
        return None
    try:
        pdf = await openalex_client.download_content_pdf(work, max_bytes=max_bytes)
    except openalex_client.OpenAlexError as exc:
        run.count("download_errors")
        if exc.status == 413:
            run.count("rejected_too_large")
        elif exc.status in (401, 402, 403, 429):
            run.content_off = True
        return None
    run.count("downloads_openalex")
    # La copia ospitata costa 0,01 $ sulla API key: nel costo della verifica
    # (dashboard admin), senza contarla come chiamata AI.
    run.paid_pdfs += 1
    run.paid_copies[work.id] = pdf
    usage = dict(run.usage or {})
    usage["cost_usd"] = round(float(usage.get("cost_usd") or 0.0) + OPENALEX_COPY_USD, 8)
    usage["openalex_copies"] = int(usage.get("openalex_copies") or 0) + 1
    run.usage = usage
    return pdf


async def _from_openalex(
    db: AsyncSession, run: _Run, queries: list[str], *, title_abstract: bool = False
) -> None:
    from app.services.document_figures.runner import ExtractionChildError

    settings = get_settings()
    for query in queries:
        if run.done:
            return
        try:
            if title_abstract:
                works = await openalex_client.search_open_works(
                    query, per_page=OPENALEX_WORKS_PER_QUERY, title_abstract=True
                )
            else:
                works = await openalex_client.search_open_works(
                    query, per_page=OPENALEX_WORKS_PER_QUERY
                )
        except openalex_client.OpenAlexError as exc:
            if exc.status is None or exc.status == 429 or exc.status >= 500:
                raise GapRetryError(str(exc)) from exc
            run.count("openalex_errors")
            continue
        for work in works:
            if run.done:
                return
            license = openalex_client.oa_location_license(work)
            pdf_url = openalex_client.oa_best_pdf_url(work)
            if license not in OPEN_LICENSES or not (
                pdf_url or openalex_client.content_pdf_available(work)
            ):
                continue
            title = wikimedia_client.plain_text(work.title, limit=500)
            authors = [
                a for a in (wikimedia_client.plain_text(n, limit=200) for n in work.authors) if a
            ]
            if not (title or authors):
                run.count("attribution_missing")
                continue
            seen = (work.id, run.current.need_id if run.current is not None else "")
            if seen in run.seen_works:
                # Ritrovato dalla seconda ricerca per lo stesso fabbisogno:
                # stesse figure, già valutate.
                run.count("works_repeated")
                continue
            run.seen_works.add(seen)
            pdf = await _work_pdf(
                run,
                work,
                pdf_url,
                max_bytes=int(settings.figure_literature_max_pdf_mb) * 1024 * 1024,
            )
            if pdf is None:
                continue
            profile = _ranking_profile(run, queries, title)

            def choose(
                events: list[dict[str, Any]], profile: dict[str, float] = profile
            ) -> list[dict[str, Any]]:
                # Ritagli sotto la soglia di risoluzione: fuori prima di
                # leggerne i byte e prima della Vision.
                usable = [
                    e
                    for e in events
                    if not _unusable(ResolutionInputs.from_mapping(e, source_kind="openalex"))
                ]
                if len(usable) < len(events):
                    dropped = len(events) - len(usable)
                    run.stats["rejected_resolution"] = (
                        int(run.stats.get("rejected_resolution") or 0) + dropped
                    )
                events = usable
                scored = sorted(
                    (
                        (score, index, event)
                        for index, event in enumerate(events)
                        if (score := _caption_score(profile, event.get("caption"))) > 0
                    ),
                    key=lambda item: (-item[0], item[1]),
                )
                if scored:
                    return [event for _s, _i, event in scored[:OPENALEX_FIGURES_PER_WORK]]
                # Nessuna didascalia vicina alla lezione: una sola figura,
                # la prima, e decide la Vision.
                return events[:1]

            try:
                figures = await extract_pdf_figures(
                    pdf,
                    max_pages=int(settings.figure_literature_max_pdf_pages),
                    wait_seconds=max(0.0, run.deadline - time.monotonic()),
                    select=choose,
                )
            except image_limits.ImageLimitError as exc:
                run.count(f"rejected_{exc.code}")
                continue
            except HeavyJobBusyError:
                run.count("heavy_job_busy")
                return
            except ExtractionChildError as exc:
                run.count(f"extraction_{exc.code}")
                if exc.code == "engine_unavailable":
                    return
                continue
            work_key = work.id.rsplit("/", 1)[-1].lower()
            landing = openalex_client.oa_landing_url(work)
            doi_url = f"https://doi.org/{work.doi}" if work.doi else None
            for event in figures:
                if run.done:
                    return
                locator = str(event.get("locator") or "")
                external_id = f"{work.id}#{locator}"
                if external_id in run.known_ids:
                    continue
                run.known_ids.add(external_id)
                # Figura di terzi dentro il paper («Reprinted from…», «©»): la
                # licenza del paper non le si applica (Fase D).
                if third_party_credit(event.get("caption")):
                    run.stats["third_party"] = int(run.stats.get("third_party") or 0) + 1
                    continue
                figure_number = figure_number_from_label(event.get("source_label"))
                attribution: dict[str, Any] = {
                    "authors": authors[:20],
                    "title": title,
                    "container": wikimedia_client.plain_text(work.journal, limit=300),
                    "year": work.publication_year,
                    "figure_number": figure_number,
                    "page": event.get("page"),
                    "license": license,
                    "url": doi_url or landing,
                }
                await _consider(
                    db,
                    run,
                    image_bytes=event["data"],
                    source_kind="openalex",
                    locator=_safe_locator(f"oa-{work_key}-{locator}"),
                    external_id=external_id,
                    source_title=title,
                    source_text=event.get("caption"),
                    license=license,
                    license_url=None,
                    attribution={k: v for k, v in attribution.items() if v},
                    source_url=landing or doi_url,
                    caption=event.get("caption"),
                    page=event.get("page"),
                    source_label=event.get("source_label"),
                    crop=CropMeta.from_event(event),
                )


@dataclass(frozen=True)
class GapOutcome:
    status: str
    stats: dict[str, Any]
    usage: dict[str, Any] | None


# --- Piano delle figure: verifica per fabbisogno (WP7, doc 18 §23.5) --------------


@dataclass(frozen=True)
class _Probe:
    """Vista minima di una candidata per l'abbinamento col fabbisogno."""

    kind: str | None
    depicts: dict[str, Any]
    description: str | None
    source_caption: str | None
    keywords: dict[str, Any]
    id: uuid.UUID | None = None


def _variant_in(key: NeedKey, text: str) -> bool:
    """Il testo (titolo e descrizione di Commons) nomina la variante cercata?
    Sempre vero per un fabbisogno base."""
    if key.is_base or not key.variants:
        return True
    words = tokens(text)
    return any(variant <= words for variant in key.variants)


def need_queries(need: dict[str, Any]) -> list[str]:
    """Ricerche per un fabbisogno: «variante oggetto» in inglese, poi il primo
    termine di ricerca inglese diverso."""
    obj = str(need.get("object_en") or "").strip()
    variant = str(need.get("variant_en") or "").strip()
    first = " ".join(part for part in (variant, obj) if part)
    out = [first] if first else []
    for term in need.get("terms_en") or []:
        term = str(term).strip()
        if term and term.lower() not in (q.lower() for q in out):
            out.append(term)
            break
    return out


def _commons_query(need: dict[str, Any]) -> str:
    """Commons: una ricerca per gruppo di varianti (il solo oggetto, poi il
    filtro lessicale sulla variante); per un fabbisogno fuori gruppo anche la
    variante."""
    if need.get("sequence_group"):
        return str(need.get("object_en") or "").strip()
    queries = need_queries(need)
    return queries[0] if queries else ""


async def _course_with_lessons(db: AsyncSession, course_id: uuid.UUID) -> Course | None:
    from sqlalchemy.orm import selectinload

    from app.models.course_module import CourseModule

    return (
        await db.execute(
            select(Course)
            .where(Course.id == course_id)
            .options(selectinload(Course.modules).selectinload(CourseModule.lessons))
        )
    ).scalar_one_or_none()


async def _external_ready(db: AsyncSession, course_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count(CourseDocumentFigure.id)).where(
            CourseDocumentFigure.course_id == course_id,
            CourseDocumentFigure.source_kind.in_(EXTERNAL_KINDS),
            CourseDocumentFigure.status == "ready",
            CourseDocumentFigure.excluded_by_user.is_(False),
        )
    )
    return int(count or 0)


async def _ready_needs_in_course(db: AsyncSession, course: Course) -> int:
    from app.services import figure_plan_service as plan

    total = 0
    for module in course.modules or []:
        for lesson in module.lessons or []:
            needs = plan.current_needs(lesson)
            total += len(needs or [])
    return total


async def check_lesson_needs(
    db: AsyncSession,
    course: Course,
    lesson: CourseLesson,
    needs: list[dict[str, Any]],
    fp: str,
    *,
    spent_before_usd: float = 0.0,
) -> GapOutcome:
    """Verifica con il piano delle figure: si cercano SOLO i fabbisogni
    scoperti per l'assegnazione (nessuna figura li copre o sono tutte al
    tetto di riuso), per fabbisogno, fermandosi alla prima figura che lo
    copre (verifica della variante sulla risposta della Vision)."""
    from app.services import source_figure_assignment_service as assignment

    settings = get_settings()
    lesson_id = lesson.id
    stats: dict[str, Any] = {"mode": "needs", "needs_fp": fp, "needs_total": len(needs)}
    snap = await assignment.snapshot(db, course, lesson)
    open_reasons = ("no_candidate", "reuse_cap")
    uncovered = [
        n
        for n in needs
        if snap is not None
        and snap.assignment.unassigned.get((lesson_id, str(n.get("need_id")))) in open_reasons
    ]
    stats["needs_uncovered"] = len(uncovered)
    if not uncovered:
        return GapOutcome("done", {**stats, "reason": "covered", "needs": {}}, None)
    missing, total = await _depicts_missing(db, course.id)
    if total and missing > DEPICTS_MISSING_MAX_SHARE * total:
        # Figure dei documenti descritte prima della 0041: senza `depicts` non
        # coprono nessun fabbisogno e la ricerca pagherebbe la letteratura per
        # tutti (deploy sui corsi esistenti). Prima `redescribe_figure_depicts`.
        log.info(
            "figures_gap_depicts_missing",
            lesson_id=str(lesson_id),
            missing=missing,
            total=total,
        )
        return GapOutcome(
            "done",
            {**stats, "reason": "depicts_missing", "depicts_missing": missing, "needs": {}},
            None,
        )
    extracting = await documents_extracting(db, course.id)
    if extracting:
        return GapOutcome(
            "skipped", {**stats, "reason": "documents_extracting", "documents": extracting}, None
        )
    cap = max(
        int(settings.figure_literature_max_per_course), await _ready_needs_in_course(db, course)
    )
    room = cap - await _external_ready(db, course.id)
    if room <= 0:
        return GapOutcome("skipped", {**stats, "reason": "course_cap"}, None)
    run = _Run(
        course_id=course.id,
        profile=build_query_profile(lesson),
        context=lesson_context(course, lesson),
        target=room,
        max_candidates=max(0, int(settings.figure_literature_max_candidates_per_lesson)),
        deadline=time.monotonic() + float(settings.figure_literature_timeout_seconds),
        hashes=await _course_hashes(db, course.id),
        known_ids=await _course_external_ids(db, course.id),
        stats=dict(stats),
        lesson_id=lesson_id,
        max_cost_usd=float(settings.figure_literature_max_cost_usd_per_check),
        max_paid_pdfs=max(0, int(settings.figure_literature_max_paid_pdf_per_lesson)),
        spent_before_usd=spent_before_usd,
    )
    per_need = max(0, int(settings.figure_literature_max_candidates_per_need))
    ordered = sorted(uncovered, key=lambda n: 0 if n.get("priority") == "must" else 1)
    outcome: dict[str, Any] = {}
    commons_cache: dict[str, list[wikimedia_client.CommonsFile]] = {}
    openalex_on = bool((settings.openalex_api_key or "").strip()) and bool(
        settings.figure_extraction_enabled
    )
    try:
        for need in ordered:
            need_id = str(need.get("need_id") or "")
            if run.exhausted:
                outcome[need_id] = {"status": "not_searched"}
                continue
            search = _NeedSearch(
                need=need,
                key=NeedKey.from_need(need),
                cap=per_need if need.get("priority") == "must" else min(1, per_need),
            )
            run.current = search
            commons = _commons_query(need)
            if commons:
                await _from_wikimedia(db, run, [commons], cache=commons_cache)
            queries = need_queries(need)
            if openalex_on and not run.done and queries:
                await _from_openalex(db, run, queries[:1], title_abstract=True)
                if not run.done:
                    await _from_openalex(db, run, queries[:1])
            outcome[need_id] = {
                "status": "found" if search.found else "not_found",
                "evaluated": search.evaluated,
                **({"figure_id": str(search.found)} if search.found else {}),
                **({"kept_other": search.kept_other} if search.kept_other else {}),
            }
            run.current = None
    except OpenAINotConfiguredError:
        return GapOutcome(
            "skipped",
            {**run.stats, "reason": "openai_not_configured", "needs": outcome},
            run.usage,
        )
    except GapRetryError as exc:
        raise GapRetryError(
            str(exc), run.usage, {**run.stats, "needs": outcome, "spent_usd": _spent(run)}
        ) from exc
    except Exception as exc:
        log.warning("figures_gap_unexpected_error", lesson_id=str(lesson_id), error=str(exc))
        raise GapRetryError(
            f"errore inatteso: {exc}",
            run.usage,
            {**run.stats, "needs": outcome, "spent_usd": _spent(run)},
        ) from exc
    run.stats.update(
        kept=run.kept,
        evaluated=run.evaluated,
        found=sum(1 for v in outcome.values() if v.get("status") == "found"),
        cost_usd=round(run.cost_usd, 6),
        timed_out=time.monotonic() > run.deadline,
        needs=outcome,
    )
    return GapOutcome("done", run.stats, run.usage)


async def check_lesson(db: AsyncSession, lesson: CourseLesson) -> GapOutcome:
    """Verifica i buchi della lezione e integra dalla letteratura aperta.

    Solleva `GapRetryError` sugli errori recuperabili, con l'usage già
    pagato e l'esito parziale."""
    settings = get_settings()
    # Valori semplici subito: dopo un rollback l'oggetto ORM è scaduto.
    lesson_id = lesson.id
    # Retry della stessa verifica: il tetto in dollari conta anche la spesa
    # dei tentativi precedenti (salvata con l'errore).
    previous = lesson.figures_gap_stats if isinstance(lesson.figures_gap_stats, dict) else {}
    spent_before = (
        float(previous.get("spent_usd") or 0.0) if (lesson.figures_gap_attempts or 0) > 0 else 0.0
    )
    course = await db.get(Course, lesson.course_id)
    if course is None:
        return GapOutcome("skipped", {"reason": "course_missing"}, None)
    if lesson.is_assessment:
        return GapOutcome("skipped", {"reason": "assessment"}, None)
    budget = source_figure_catalog.budget_for(lesson)
    if not settings.figure_source_enabled or budget <= 0:
        return GapOutcome("skipped", {"reason": "no_budget"}, None)
    # Piano delle figure: con i fabbisogni pronti la verifica è per
    # fabbisogno (il vecchio criterio «abbastanza figure pertinenti» non
    # vede le varianti scoperte).
    from app.services import figure_plan_service as plan

    # Fabbisogni pronti ma calcolati su un input diverso (scaletta cambiata,
    # nuova versione del prompt): la verifica procede col criterio di prima
    # ma registra la loro impronta, così il tick non la riapre a ogni giro;
    # si riapre quando i fabbisogni vengono ricalcolati.
    stale_fp: str | None = None
    if plan.plan_active():
        full = await _course_with_lessons(db, course.id)
        target = next(
            (
                item
                for module in (full.modules if full else [])
                for item in module.lessons or []
                if item.id == lesson_id
            ),
            None,
        )
        item_input = plan.needs_input(full, target) if full and target else None
        if full is not None and target is not None and item_input is not None:
            fp = plan.fingerprint(item_input, plan.max_needs(target))
            ready = plan.current_needs(target, fp)
            if ready is not None:
                # «Non serve» e figure collegate dal docente: non si cercano.
                return await check_lesson_needs(
                    db,
                    full,
                    target,
                    plan.active_needs(target, ready),
                    fp,
                    spent_before_usd=spent_before,
                )
        if target is not None and target.figure_needs_status == "ready":
            stored = target.figure_needs if isinstance(target.figure_needs, dict) else {}
            stale_fp = str(stored.get("fingerprint") or "") or None
    pertinent = await pertinent_figures(db, course, lesson)
    stats: dict[str, Any] = {"pertinent_before": pertinent, "budget": budget}
    if stale_fp is not None:
        stats["needs_fp"] = stale_fp
    if pertinent >= int(settings.figure_source_min_per_lesson):
        return GapOutcome("done", {**stats, "reason": "enough"}, None)
    extracting = await documents_extracting(db, course.id)
    if extracting:
        log.info(
            "figures_gap_documents_extracting",
            lesson_id=str(lesson_id),
            documents=extracting,
        )
        return GapOutcome(
            "skipped", {**stats, "reason": "documents_extracting", "documents": extracting}, None
        )
    room = int(settings.figure_literature_max_per_course) - await external_figures(db, course.id)
    if room <= 0:
        return GapOutcome("skipped", {**stats, "reason": "course_cap"}, None)
    run = _Run(
        course_id=course.id,
        profile=build_query_profile(lesson),
        context=lesson_context(course, lesson),
        target=min(budget - pertinent, room),
        max_candidates=max(0, int(settings.figure_literature_max_candidates_per_lesson)),
        deadline=time.monotonic() + float(settings.figure_literature_timeout_seconds),
        hashes=await _course_hashes(db, course.id),
        known_ids=await _course_external_ids(db, course.id),
        stats=dict(stats),
        # Stessi tetti in dollari e di PDF a pagamento della verifica per
        # fabbisogno (il tetto di candidate per lezione vale per entrambe).
        max_cost_usd=float(settings.figure_literature_max_cost_usd_per_check),
        max_paid_pdfs=max(0, int(settings.figure_literature_max_paid_pdf_per_lesson)),
        spent_before_usd=spent_before,
    )
    if run.done:
        # Niente posto o nessuna candidata ammessa: nessuna chiamata AI.
        return GapOutcome("done", {**run.stats, "reason": "no_room", "kept": 0}, None)
    try:
        try:
            queries, usage = await relevance.search_terms(run.context)
        except relevance.OpenAIFigureRelevanceError as exc:
            run.usage = merge_usage(run.usage, exc.usage)
            raise GapRetryError(str(exc)) from exc
        run.usage = merge_usage(run.usage, usage)
        run.stats["queries"] = queries
        if queries:
            await _from_wikimedia(db, run, queries)
            if (
                not run.done
                and (settings.openalex_api_key or "").strip()
                and settings.figure_extraction_enabled
            ):
                await _from_openalex(db, run, queries)
    except OpenAINotConfiguredError:
        return GapOutcome("skipped", {**run.stats, "reason": "openai_not_configured"}, run.usage)
    except GapRetryError as exc:
        # L'usage già pagato si conserva anche se la lezione torna in coda.
        raise GapRetryError(str(exc), run.usage, {**run.stats, "spent_usd": _spent(run)}) from exc
    except Exception as exc:
        # Qualunque altro errore (figlio, storage, rete): stessa strada dei
        # recuperabili, con il costo già pagato (G9) e il tetto dei tentativi.
        log.warning("figures_gap_unexpected_error", lesson_id=str(lesson_id), error=str(exc))
        raise GapRetryError(
            f"errore inatteso: {exc}", run.usage, {**run.stats, "spent_usd": _spent(run)}
        ) from exc
    run.stats.update(
        kept=run.kept,
        evaluated=run.evaluated,
        timed_out=time.monotonic() > run.deadline,
    )
    return GapOutcome("done", run.stats, run.usage)
