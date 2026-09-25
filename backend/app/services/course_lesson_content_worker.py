"""Background worker per la generazione dei contenuti delle lezioni
(Fase 3 — §6).

Pattern speculare al worker di Fase 2 ma scoped a livello LEZIONE
(non modulo): dispatcha le lezioni `pending` IN PARALLELO con cap di
concorrenza configurabile (default 3, output 5x più grande di Fase 2).

Stato per lezione su `course_lesson.content_status`:
    empty → pending → processing → ready → approved
                                  ↘ failed (solo dopo N retry esauriti)

**Auto-retry trasparente** (`course_lesson_content_auto_retry_max`,
default 5): se la generazione fallisce in modo recuperabile (errore
OpenAI transiente, validazione §6.4, glossary gate, materializzazione),
il worker NON transita a `failed` — riporta lo status a `pending` e il
ticker successivo (4s) ritenta. La UI vede solo "in elaborazione"
finché passa, mai il messaggio di errore. Solo dopo `auto_retry_max`
attempts esauriti la lezione transita a `failed` (terminale, l'utente
può ricliccare manualmente). Errori non recuperabili
(`OpenAINotConfiguredError` — config issue) vanno a `failed` subito.

Pre-step automatico: al primo task della Fase 3 il worker controlla
`course.glossary_status` — se `empty`/`failed`, chiama sync inline
`course_glossary_service.ensure_glossary_ready` (~10-20s). I task
successivi trovano il glossario già pronto.

Il progresso (0-100%) e la fase corrente sono persistiti su
`course_lesson.content_progress` / `content_progress_phase`. La UI
fa polling su `GET /courses/{id}` mentre almeno una lezione è in
`pending|processing` (o `course.glossary_status='processing'`) e mostra
una progress bar live + aggregate progress in header.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.course_phase_order import lesson_structure_is_ready
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_content import (
    KEY_TAKEAWAYS_MIN,
    SOURCE_FIGURE_FORMAT,
    LessonContentOutput,
)
from app.services import (
    asset_validation_service,
    course_glossary_service,
    course_lesson_content_service,
    document_citation_guard,
    openai_lesson_content_service,
    source_figure_catalog,
    source_figure_fusion,
)
from app.services.heavy_job_lock import HEAVY_JOB_LOCK
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_lesson_content.worker")


# ---------------------------------------------------------------------------
# State del worker (lesson-scope)
# ---------------------------------------------------------------------------

_inflight: set[uuid.UUID] = set()
_inflight_lock = asyncio.Lock()

_semaphore: asyncio.Semaphore | None = None
_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_active_tasks: set[asyncio.Task] = set()

# Lezioni il cui ultimo tentativo è fallito per una figura `tikz` rimasta
# invalida dopo il suo unico fix (`tikz_unresolved`): valore =
# `content_attempts` del tentativo fallito. Il tentativo SUCCESSIVO non
# offre `tikz` (né nello schema né nel messaggio user). In memoria: un
# riavvio lo perde (limite dichiarato, al più un tentativo `tikz` in più
# entro `course_lesson_content_auto_retry_max`).
_TIKZ_WITHHELD: dict[uuid.UUID, int] = {}


# ---------------------------------------------------------------------------
# Auto-retry helper
# ---------------------------------------------------------------------------


def _apply_failure(
    lesson: CourseLesson,
    *,
    error: str,
    phase: str,
    recoverable: bool,
    auto_retry_max: int,
) -> bool:
    """Decide se ritentare automaticamente o passare a `failed`.

    Scrive sui campi della lezione SENZA committare. Il caller fa il
    commit + audit log dopo aver visto il return value.

    Returns:
        True se è stato schedulato un auto-retry (status='pending').
        False se è transizione terminale (status='failed').
    """
    attempts = lesson.content_attempts or 0
    if recoverable and attempts < auto_retry_max:
        lesson.content_status = "pending"
        lesson.content_error = None
        lesson.content_progress = 0
        lesson.content_progress_phase = None
        log.info(
            "lesson_content_auto_retry",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            phase=phase,
            attempts=attempts,
            max_retry=auto_retry_max,
            error=error[:200],
        )
        return True
    lesson.content_status = "failed"
    lesson.content_error = error[:500]
    lesson.content_progress = 0
    lesson.content_progress_phase = None
    log.warning(
        "lesson_content_failed_terminal",
        lesson_id=str(lesson.id),
        lesson_code=lesson.lesson_code,
        phase=phase,
        attempts=attempts,
        error=error[:200],
    )
    return False


# ---------------------------------------------------------------------------
# Punti chiave degradati dalla dedup
# ---------------------------------------------------------------------------


def _warn_on_degraded_key_takeaways(lesson: CourseLesson, output: object) -> None:
    """Warning quando la dedup dello schema (D18) porta i punti chiave sotto
    `KEY_TAKEAWAYS_MIN`.

    `LessonContentOutput` conta il minimo sull'elenco grezzo del modello:
    una lista validata più corta è quindi effetto della sola dedup. La
    lezione resta valida e non viene rigenerata (un difetto cosmetico non
    vale una generazione intera); il warning rende visibile il degrado. Le
    verifiche delle competenze non hanno punti chiave e sono ignorate.
    """
    if not isinstance(output, LessonContentOutput):
        return
    count = len(output.key_takeaways)
    if count < KEY_TAKEAWAYS_MIN:
        log.warning(
            "lesson_content_key_takeaways_below_min",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            key_takeaways=count,
            minimum=KEY_TAKEAWAYS_MIN,
        )


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


async def _set_progress(lesson_id: uuid.UUID, *, pct: int, phase: str | None) -> None:
    """Aggiorna `content_progress` + phase su una sessione propria."""
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseLesson, lesson_id)
        if row is None:
            return
        row.content_progress = max(0, min(100, pct))
        row.content_progress_phase = phase
        await tdb.commit()


async def _progress_ticker(
    lesson_id: uuid.UUID,
    *,
    start_pct: int,
    end_pct: int,
    duration_sec: float,
) -> None:
    """Incrementa gradualmente `content_progress` da `start_pct` verso
    `end_pct` su `duration_sec` secondi (ease-out).

    Si ferma se cancellato o se lo status non è più `processing`.
    """
    started = time.monotonic()
    span = max(1, end_pct - start_pct)
    try:
        while True:
            await asyncio.sleep(3.0)
            elapsed = time.monotonic() - started
            ratio = min(1.0, elapsed / duration_sec)
            eased = 1 - (1 - ratio) ** 2
            target = start_pct + int(span * eased)
            target = min(end_pct, target)
            async with async_session_factory() as tdb:
                row = await tdb.get(CourseLesson, lesson_id)
                if row is None or row.content_status != "processing":
                    return
                if row.content_progress < target:
                    row.content_progress = target
                    await tdb.commit()
            if target >= end_pct:
                return
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Process one lesson (con sessione DB propria)
# ---------------------------------------------------------------------------


async def _process_one(lesson_id: uuid.UUID) -> None:
    """Genera il contenuto di Fase 3 per una singola lezione.

    Sessione DB propria, niente contention con altre task.
    """
    async with async_session_factory() as db:
        bare = await db.get(CourseLesson, lesson_id)
        if bare is None:
            log.warning("lesson_content_lesson_not_found", lesson_id=str(lesson_id))
            return
        if bare.content_status != "pending":
            log.info(
                "lesson_content_skip_not_pending",
                lesson_id=str(lesson_id),
                status=bare.content_status,
            )
            return
        course_id = bare.course_id

        course_full = await course_lesson_content_service.load_course_full(db, course_id=course_id)
        if course_full is None:
            log.warning(
                "lesson_content_course_not_found",
                lesson_id=str(lesson_id),
                course_id=str(course_id),
            )
            return

        try:
            lesson = await course_lesson_content_service.get_lesson_or_404(
                db, course=course_full, lesson_id=lesson_id
            )
        except Exception:
            return

        # Pre-check per-unità (difesa in profondità del gate API): il
        # modulo della lezione deve avere struttura approvata e la
        # lezione i dati di Fase 2. Failure immediata non recuperabile
        # (il task è stato accodato fuori contesto, es. race con una
        # rigenerazione della struttura).
        if not lesson_structure_is_ready(course_full, lesson):
            settings = get_settings()
            _apply_failure(
                lesson,
                error=(
                    "Impossibile generare la dispensa: la struttura del "
                    "modulo della lezione deve essere approvata (e la "
                    "lezione deve avere obiettivi/sezioni di Fase 2). "
                    "Genera e approva prima la struttura."
                ),
                phase="precheck_structure",
                recoverable=False,
                auto_retry_max=settings.course_lesson_content_auto_retry_max,
            )
            course_lesson_content_service._recompute_course_content_status(course_full)
            await write_audit(
                db,
                action="course.lesson.content.failed",
                actor_user_id=None,
                organization_id=course_full.organization_id,
                target_type="course_lesson",
                target_id=str(lesson.id),
                metadata={
                    "course_id": str(course_full.id),
                    "lesson_code": lesson.lesson_code,
                    "phase": "precheck_structure",
                    "error": "lesson_structure_not_ready",
                    "attempts": lesson.content_attempts,
                },
            )
            await db.commit()
            return

        # Transizione → processing
        lesson.content_attempts = (lesson.content_attempts or 0) + 1
        lesson.content_status = "processing"
        lesson.content_error = None
        lesson.content_progress = 5
        lesson.content_progress_phase = "preparing_prompt"
        await db.commit()

        # Glossary gate: se non disponibile, genera sync inline. La
        # lezione-verifica non usa il glossario → salta il gate.
        if not lesson.is_assessment and course_full.glossary_status not in (
            "ready",
            "approved",
        ):
            try:
                course_full = await course_glossary_service.ensure_glossary_ready(
                    db, course=course_full, actor_id=None
                )
                # Ricarica la lezione dalla nuova istanza ricaricata.
                lesson = await course_lesson_content_service.get_lesson_or_404(
                    db, course=course_full, lesson_id=lesson_id
                )
            except Exception as exc:
                settings = get_settings()
                terminal = not _apply_failure(
                    lesson,
                    error=f"Glossario non disponibile: {exc}",
                    phase="glossary_gate",
                    recoverable=True,
                    auto_retry_max=settings.course_lesson_content_auto_retry_max,
                )
                if terminal:
                    course_lesson_content_service._recompute_course_content_status(course_full)
                    await write_audit(
                        db,
                        action="course.lesson.content.failed",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "phase": "glossary_gate",
                            "error": str(exc)[:500],
                            "attempts": lesson.content_attempts,
                        },
                    )
                await db.commit()
                return

        regen = course_lesson_content_service.is_regeneration_for_lesson(lesson)
        # Catalogo delle figure di fonte (budget (b), IN AGGIUNTA alle figure
        # generate): un errore qui non blocca la lezione, che resta senza.
        catalog: source_figure_catalog.CatalogResult | None = None
        if not lesson.is_assessment:
            try:
                async with db.begin_nested():
                    catalog = await source_figure_catalog.build_catalog(db, course_full, lesson)
            except Exception as exc:
                log.warning(
                    "lesson_content_source_catalog_failed",
                    lesson_id=str(lesson.id),
                    error=str(exc)[:300],
                )
        # Formati offerti a questo tentativo: gli stessi per schema e
        # messaggio user (`tikz` solo se proposto, non appena fallito e non
        # durante un'estrazione Docling, che occupa la sandbox TeX: la
        # figura resterebbe senza validazione e costerebbe una
        # rigenerazione).
        withhold_tikz = (
            _TIKZ_WITHHELD.pop(lesson.id, None) == (lesson.content_attempts or 0) - 1
            or HEAVY_JOB_LOCK.locked()
        )
        visual_formats = openai_lesson_content_service.phase3_visual_formats(
            course_full.language_code, withhold_tikz=withhold_tikz
        )
        if lesson.is_assessment:
            user_prompt = course_lesson_content_service.build_assessment_user_prompt(
                course_full, lesson
            )
        else:
            user_prompt = course_lesson_content_service.build_user_prompt(
                course_full,
                lesson,
                figure_catalog=catalog.catalog if catalog else None,
                source_figures_max=catalog.budget if catalog else 0,
                visual_formats=visual_formats,
            )

        # Aggiorna progresso → calling_openai e avvia ticker
        lesson.content_progress = 15
        lesson.content_progress_phase = "calling_openai"
        await db.commit()

        # Ticker ease-out più lento di Fase 2: lezione completa ~60-120s.
        ticker_task = asyncio.create_task(
            _progress_ticker(lesson.id, start_pct=15, end_pct=85, duration_sec=90.0)
        )

        try:
            try:
                if lesson.is_assessment:
                    (
                        content_output,
                        usage,
                    ) = await openai_lesson_content_service.generate_lesson_assessment(
                        user_prompt=user_prompt,
                        language_code=course_full.language_code,
                        is_regeneration=regen,
                    )
                else:
                    style = course_lesson_content_service.didactic_style_labels(course_full)
                    (
                        content_output,
                        usage,
                    ) = await openai_lesson_content_service.generate_lesson_content(
                        user_prompt=user_prompt,
                        language_code=course_full.language_code,
                        is_regeneration=regen,
                        ruolo_docente=style["ruolo_docente"],
                        stile_insegnamento=style["stile_insegnamento"],
                        livello_eqf=style["livello_eqf"],
                        # Vincolo strutturale sui riferimenti agli
                        # obiettivi: stessi codici mostrati nel prompt.
                        objective_ids=course_lesson_content_service.objective_ids_for_lesson(
                            lesson
                        ),
                        source_figure_refs=list(catalog.catalog.refs) if catalog else (),
                        visual_formats=visual_formats,
                    )
            except OpenAINotConfiguredError:
                # NON recuperabile (config issue) → terminal subito.
                settings = get_settings()
                _apply_failure(
                    lesson,
                    error=(
                        "OpenAI non configurato: l'amministratore deve impostare "
                        "OPENAI_API_KEY nel file .env del backend."
                    ),
                    phase="openai_call",
                    recoverable=False,
                    auto_retry_max=settings.course_lesson_content_auto_retry_max,
                )
                course_lesson_content_service._recompute_course_content_status(course_full)
                await write_audit(
                    db,
                    action="course.lesson.content.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "lesson_code": lesson.lesson_code,
                        "phase": "openai_call",
                        "error": "openai_not_configured",
                        "attempts": lesson.content_attempts,
                    },
                )
                await db.commit()
                return
            except openai_lesson_content_service.OpenAILessonContentError as exc:
                settings = get_settings()
                terminal = not _apply_failure(
                    lesson,
                    error=str(exc),
                    phase="openai_call",
                    recoverable=True,
                    auto_retry_max=settings.course_lesson_content_auto_retry_max,
                )
                if terminal:
                    course_lesson_content_service._recompute_course_content_status(course_full)
                    await write_audit(
                        db,
                        action="course.lesson.content.failed",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "phase": "openai_call",
                            "error": str(exc)[:500],
                            "attempts": lesson.content_attempts,
                        },
                    )
                await db.commit()
                return
        finally:
            ticker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await ticker_task

        # Cancel-check: se nel frattempo l'utente ha annullato la
        # generazione, lo status DB è stato spostato a `failed`. Scarta
        # il risultato OpenAI (lo abbiamo pagato ma non lo materializziamo).
        await db.refresh(lesson, ["content_status"])
        if lesson.content_status != "processing":
            log.info(
                "lesson_content_cancelled_post_openai",
                lesson_id=str(lesson.id),
                lesson_code=lesson.lesson_code,
                current_status=lesson.content_status,
            )
            return

        # Validazione + auto-fix degli asset "fragili" (formule LaTeX e
        # figure) PRIMA di materializzare: ripara con AI ogni asset invalido
        # cosi' che nessuno raggiunga `ready` rotto, poi revisione figura ↔
        # testo e localizzazione. Solo per le lezioni di contenuto
        # (l'assessment ha schema MC/open). Collocazione rispetto ai tre
        # stadi: qui, prima del filtro delle fonti riservate (che tocca solo
        # `references`) e di `materialize_lesson_content`, che riceve
        # l'usage con le chiamate degli asset (`content_tokens.assets`).
        fusion: source_figure_fusion.FusionReport | None = None
        if isinstance(content_output, LessonContentOutput):
            # Figure di fonte scelte → asset `source_figure` (prima della
            # validazione, che le salta: non sono in RENDERABLE_FORMATS).
            fusion = source_figure_fusion.fuse_source_figures(
                content_output,
                catalog.catalog.refs if catalog else {},
                max_items=catalog.budget if catalog else 0,
            )
            if fusion.as_json():
                log.info(
                    "lesson_content_source_figures_fused",
                    lesson_id=str(lesson.id),
                    lesson_code=lesson.lesson_code,
                    **fusion.as_json(),
                )
        if not lesson.is_assessment:
            lesson.content_progress = 88
            lesson.content_progress_phase = "validating_assets"
            await db.commit()
            try:
                (
                    content_output,
                    assets_usage,
                ) = await asset_validation_service.validate_and_fix_content_assets(
                    content_output, language_code=course_full.language_code
                )
            # Cattura ampia (incl. AssetFixUnresolvedError): qualunque errore
            # in validazione/fix e' recuperabile -> auto-retry rigenera la
            # lezione. Cosi' un asset rotto non raggiunge mai `ready` e
            # l'utente non vede errori intermedi.
            except Exception as exc:
                # Il costo gia' speso nei tentativi di fix non si
                # materializza (la lezione viene rigenerata): resta almeno
                # visibile nei log, come quello scartato dal cancel-check.
                if getattr(exc, "code", None) == "tikz_unresolved":
                    _TIKZ_WITHHELD[lesson.id] = lesson.content_attempts or 0
                    log.warning(
                        "lesson_content_tikz_unresolved",
                        lesson_id=str(lesson.id),
                        lesson_code=lesson.lesson_code,
                        attempts=lesson.content_attempts,
                        error=str(exc)[:300],
                    )
                spent = getattr(exc, "assets_usage", [])
                if spent:
                    log.warning(
                        "lesson_content_assets_cost_discarded",
                        lesson_id=str(lesson.id),
                        lesson_code=lesson.lesson_code,
                        reason="asset_validation_failed",
                        calls=len(spent),
                        assets_cost_usd=asset_validation_service.assets_cost_usd(spent),
                    )
                settings = get_settings()
                terminal = not _apply_failure(
                    lesson,
                    error=f"Asset non validabili: {exc}",
                    phase="asset_validation",
                    recoverable=True,
                    auto_retry_max=settings.course_lesson_content_auto_retry_max,
                )
                if terminal:
                    course_lesson_content_service._recompute_course_content_status(course_full)
                    await write_audit(
                        db,
                        action="course.lesson.content.failed",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "phase": "asset_validation",
                            "error": str(exc)[:500],
                            "attempts": lesson.content_attempts,
                        },
                    )
                await db.commit()
                return
            # Costo degli asset (fix, revisione, localizzazione) accanto a
            # quello della chiamata di Fase 3: `cost_usd` resta il suo.
            usage = asset_validation_service.merge_assets_usage(usage, assets_usage)

            # Secondo cancel-check: fix, revisione figura ↔ testo e
            # localizzazione possono durare minuti (fino a
            # `figure_review_max_attempts` chiamate per figura più la resa),
            # e un annullamento arrivato nel frattempo non va sovrascritto da
            # `ready` né dal progress di materializzazione. Come dopo la
            # chiamata di Fase 3, il risultato (e il suo usage) è scartato:
            # niente riga in `content_tokens` per una lezione annullata, ma
            # il costo già speso resta nel log dell'annullamento.
            await db.refresh(lesson, ["content_status"])
            if lesson.content_status != "processing":
                log.info(
                    "lesson_content_cancelled_post_assets",
                    lesson_id=str(lesson.id),
                    lesson_code=lesson.lesson_code,
                    current_status=lesson.content_status,
                    cost_usd=usage.get("cost_usd"),
                    assets_calls=len(assets_usage),
                    assets_cost_usd=usage.get("assets_cost_usd"),
                )
                return

        # Revisore delle ridondanze (PROMPT 19): segnala soltanto, ogni
        # errore vale «nessun avviso»; costo in `content_tokens.assets`.
        # Gira PRIMA del ricontrollo TOCTOU e del terzo cancel-check: la sua
        # attesa di rete (fino a `figure_redundancy_timeout_seconds`) non
        # deve lasciare passare né un cambio di politica né un annullamento.
        figure_review: dict | None = None
        if isinstance(content_output, LessonContentOutput) and fusion is not None and fusion.added:
            try:
                infos = await source_figure_catalog.figure_infos(
                    db,
                    course_full.id,
                    {
                        a.asset_id: uuid.UUID(a.content)
                        for a in content_output.visual_assets
                        if a.format == SOURCE_FIGURE_FORMAT
                    },
                )
                (
                    figure_review,
                    redundancy_usage,
                ) = await asset_validation_service.review_source_figure_redundancy(
                    content_output, infos, language_code=course_full.language_code
                )
            except Exception as exc:
                figure_review, redundancy_usage = None, []
                log.warning(
                    "lesson_content_figure_redundancy_failed",
                    lesson_id=str(lesson.id),
                    lesson_code=lesson.lesson_code,
                    error=str(exc)[:300],
                )
            if redundancy_usage:
                usage = asset_validation_service.merge_assets_usage(
                    usage, [*(usage.get("assets") or []), *redundancy_usage]
                )

        # Ricontrollo delle figure di fonte scelte (TOCTOU): politica del
        # documento, esclusione o licenza (politica dell'organizzazione
        # RILETTA adesso) cambiate durante la generazione. Se il ricontrollo
        # stesso fallisce, le figure di fonte escono tutte (scelta prudente:
        # mai una figura non ricontrollata).
        if (
            catalog is not None
            and fusion is not None
            and fusion.added
            and isinstance(content_output, LessonContentOutput)
        ):
            fused = {
                a.asset_id: uuid.UUID(a.content)
                for a in content_output.visual_assets
                if a.format == SOURCE_FIGURE_FORMAT
            }
            # Motivo per figura: politica (not_selectable_at_materialize) prima
            # del tetto di riuso (reuse_cap), che le lezioni generate in
            # parallelo possono aver raggiunto nel frattempo.
            reason_by: dict[uuid.UUID, str] = {}
            try:
                current_policy = await source_figure_catalog.license_policy_for(db, course_full)
                for fid in await source_figure_catalog.not_selectable(
                    db, course_full, fused.values(), license_policy=current_policy
                ):
                    reason_by[fid] = "not_selectable_at_materialize"
                for fid in await source_figure_catalog.over_reuse_cap(
                    db, course_full, fused.values(), lesson_id=lesson.id
                ):
                    reason_by.setdefault(fid, "reuse_cap")
            except Exception as exc:
                log.warning(
                    "lesson_content_source_figures_recheck_failed",
                    lesson_id=str(lesson.id),
                    lesson_code=lesson.lesson_code,
                    error=str(exc)[:300],
                )
                reason_by = dict.fromkeys(fused.values(), "recheck_failed")
            if reason_by:
                dropped = {aid for aid, fid in fused.items() if fid in reason_by}
                source_figure_fusion.remove_source_figures(content_output, dropped)
                figure_review = _drop_from_review(figure_review, dropped)
                for reason in sorted(set(reason_by.values())):
                    metadata: dict[str, Any] = {
                        "course_id": str(course_full.id),
                        "lesson_code": lesson.lesson_code,
                        "dropped": sorted(
                            aid for aid, fid in fused.items() if reason_by.get(fid) == reason
                        ),
                        "reason": reason,
                    }
                    if reason == "reuse_cap":
                        metadata["cap"] = source_figure_catalog.reuse_cap()
                    await write_audit(
                        db,
                        action="course.lesson.content.source_figures_dropped",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata=metadata,
                    )
                fusion.added = [a for a in fusion.added if a not in dropped]
        if catalog is not None:
            usage["source_figures"] = {
                "catalog": len(catalog.catalog.candidates),
                "relevant": catalog.catalog.relevant_count,
                "budget": catalog.budget,
                "license_policy": catalog.license_policy,
                **(fusion.as_json() if fusion else {}),
            }

        # Terzo cancel-check: il revisore delle ridondanze e il ricontrollo
        # stanno dopo il secondo; un annullamento arrivato nel frattempo non
        # va sovrascritto da `ready`.
        await db.refresh(lesson, ["content_status"])
        if lesson.content_status != "processing":
            log.info(
                "lesson_content_cancelled_pre_materialize",
                lesson_id=str(lesson.id),
                lesson_code=lesson.lesson_code,
                current_status=lesson.content_status,
                cost_usd=usage.get("cost_usd"),
            )
            return

        # Visibilità delle fonti: filtro hard delle references che
        # matchano documenti a fonte riservata (PRIMA di model_dump()
        # così content_raw persiste filtrato) + scan SOFT della prosa
        # (mai mutazione: solo warning + audit).
        if not lesson.is_assessment:
            reserved_index = document_citation_guard.build_identity_index(
                list(course_full.documents)
            )
            if reserved_index:
                kept_refs, dropped_refs = document_citation_guard.filter_reference_items(
                    [r.model_dump() for r in content_output.references],
                    reserved_index,
                )
                if dropped_refs:
                    content_output.references = (
                        [type(content_output.references[0])(**r) for r in kept_refs]
                        if kept_refs
                        else []
                    )
                    await write_audit(
                        db,
                        action="course.lesson.content.references_filtered",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "dropped": [r.get("citation") for r in dropped_refs],
                        },
                    )
                # Anche didascalie e testi alternativi delle figure: il nome
                # di un documento riservato non deve comparire nemmeno lì.
                figure_texts: list[str] = []
                if isinstance(content_output, LessonContentOutput):
                    figure_texts = [
                        f"{asset.caption or ''}\n{asset.alt_text or ''}"
                        for asset in content_output.visual_assets
                    ]
                prose = "\n".join(
                    [content_output.introduction or ""]
                    + [s.content or "" for s in content_output.sections]
                    + [content_output.summary or ""]
                    + figure_texts
                )
                leaks = document_citation_guard.scan_text_for_leaks(prose, reserved_index)
                if leaks:
                    log.warning(
                        "lesson_content_reserved_leak_in_prose",
                        lesson_id=str(lesson.id),
                        lesson_code=lesson.lesson_code,
                        documents=leaks,
                    )
                    await write_audit(
                        db,
                        action="course.lesson.content.reserved_leak",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "documents": leaks,
                        },
                    )

        # Materializzazione + validazioni §6.4
        lesson.content_progress = 90
        lesson.content_progress_phase = "materializing"
        await db.commit()

        try:
            if lesson.is_assessment:
                await course_lesson_content_service.materialize_lesson_assessment(
                    db,
                    course=course_full,
                    lesson=lesson,
                    output=content_output,
                    raw=content_output.model_dump(),
                    usage=usage,
                )
            else:
                await course_lesson_content_service.materialize_lesson_content(
                    db,
                    course=course_full,
                    lesson=lesson,
                    output=content_output,
                    raw=content_output.model_dump(),
                    usage=usage,
                    figure_review=figure_review,
                )
        except Exception as exc:
            settings = get_settings()
            terminal = not _apply_failure(
                lesson,
                error=f"Materializzazione fallita: {exc}",
                phase="materialize",
                recoverable=True,
                auto_retry_max=settings.course_lesson_content_auto_retry_max,
            )
            if terminal:
                course_lesson_content_service._recompute_course_content_status(course_full)
                await write_audit(
                    db,
                    action="course.lesson.content.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "lesson_code": lesson.lesson_code,
                        "phase": "materialize",
                        "error": str(exc)[:500],
                        "attempts": lesson.content_attempts,
                    },
                )
            await db.commit()
            return

        _warn_on_degraded_key_takeaways(lesson, content_output)

        if lesson.is_assessment:
            phase_metadata: dict = {
                "is_assessment": True,
                "mc_questions": len(content_output.multiple_choice_questions),
                "open_questions": len(content_output.open_questions),
            }
        else:
            phase_metadata = {
                "sections": len(content_output.sections),
                "visual_assets": len(content_output.visual_assets),
                "tables": len(content_output.tables),
                "equations": len(content_output.equations),
                "examples": len(content_output.examples),
                "estimated_word_count": content_output.estimated_word_count,
            }
        await write_audit(
            db,
            action="course.lesson.content.generated",
            actor_user_id=None,
            organization_id=course_full.organization_id,
            target_type="course_lesson",
            target_id=str(lesson.id),
            metadata={
                "course_id": str(course_full.id),
                "lesson_code": lesson.lesson_code,
                **phase_metadata,
                "tokens_total": usage.get("total"),
                "tokens_prompt": usage.get("prompt"),
                "tokens_completion": usage.get("completion"),
                "model": usage.get("model"),
                "attempts": lesson.content_attempts,
                "regeneration": regen,
            },
        )
        await db.commit()
        log.info(
            "lesson_content_generated",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            is_assessment=lesson.is_assessment,
            tokens=usage.get("total"),
        )


# ---------------------------------------------------------------------------
# Bound task (semaforo + inflight tracking)
# ---------------------------------------------------------------------------


async def _bound_process(lesson_id: uuid.UUID) -> None:
    """Wrap `_process_one` con cap concorrenza.

    NB: il task viene aggiunto a `_inflight` da `_tick` PRIMA del dispatch
    (non qui) per evitare race con i tick successivi mentre la task è in
    coda dietro al semaforo. Qui ci occupiamo solo del discard finale.
    """
    assert _semaphore is not None
    try:
        async with _semaphore:
            await _process_one(lesson_id)
    except Exception as exc:  # pragma: no cover
        log.error(
            "lesson_content_worker_unexpected",
            lesson_id=str(lesson_id),
            error=str(exc),
            exc_info=True,
        )
    finally:
        async with _inflight_lock:
            _inflight.discard(lesson_id)


# ---------------------------------------------------------------------------
# Tick: discovery + dispatch
# ---------------------------------------------------------------------------


def _drop_from_review(review: dict | None, dropped: set[str]) -> dict | None:
    """Verdetto del revisore senza le figure tolte dal ricontrollo (e senza
    le coppie verso di esse)."""
    if not review or not dropped:
        return review
    figures = {
        aid: {
            **verdict,
            "pairs": [p for p in verdict.get("pairs") or [] if p.get("other") not in dropped],
        }
        for aid, verdict in (review.get("figures") or {}).items()
        if aid not in dropped and isinstance(verdict, dict)
    }
    return {**review, "figures": figures} if figures else None


def _pending_lessons_query() -> Select[tuple[uuid.UUID]]:
    """Lezioni `pending`. Una lezione di contenuto aspetta (senza sleep: la
    riprende un tick successivo) finché nel suo corso c'è un'estrazione di
    figure di fonte in coda o in corso, richiesta da meno di
    `FIGURE_WAIT_MAX_MINUTES`: così il catalogo include le figure appena
    richieste. Con la letteratura aperta accesa (WP5) aspetta anche la
    verifica dei buchi della lezione (`figures_gap_status` in coda o in
    corso, stesso tetto dalla richiesta). Oltre il tetto si parte comunque."""
    settings = get_settings()
    query = select(CourseLesson.id).where(CourseLesson.content_status == "pending")
    if not settings.figure_source_enabled:
        return query
    cutoff = datetime.now(UTC) - timedelta(minutes=int(settings.figure_wait_max_minutes))
    if settings.figure_extraction_enabled:
        extracting = (
            select(CourseDocument.id)
            .where(
                CourseDocument.course_id == CourseLesson.course_id,
                CourseDocument.figures_status.in_(("pending", "processing")),
                CourseDocument.figures_requested_at > cutoff,
            )
            .exists()
        )
        query = query.where(or_(CourseLesson.is_assessment.is_(True), ~extracting))
    if settings.figure_literature_enabled:
        # Esplicito sui NULL: NOT su un confronto con NULL escluderebbe la riga.
        query = query.where(
            or_(
                CourseLesson.is_assessment.is_(True),
                CourseLesson.figures_gap_status.is_(None),
                CourseLesson.figures_gap_status.not_in(("pending", "processing")),
                CourseLesson.figures_gap_requested_at.is_(None),
                CourseLesson.figures_gap_requested_at <= cutoff,
            )
        )
    return query


async def _request_figure_gaps(db: AsyncSession) -> None:
    """Letteratura aperta (WP5): le lezioni ordinarie in coda mai
    verificate entrano nella coda dei buchi (`figures_gap_status=pending`),
    PRIMA della query delle lezioni pronte, così nessuna parte senza."""
    settings = get_settings()
    if not (settings.figure_source_enabled and settings.figure_literature_enabled):
        return
    await db.execute(
        update(CourseLesson)
        .where(
            CourseLesson.content_status == "pending",
            CourseLesson.is_assessment.is_(False),
            CourseLesson.figures_gap_status.is_(None),
        )
        .values(figures_gap_status="pending", figures_gap_requested_at=datetime.now(UTC))
    )
    await db.commit()


async def _tick() -> None:
    """Cerca lezioni `pending` non già in flight e le dispatcha come task
    paralleli (fire-and-forget). Il `_tick` ritorna subito.
    """
    async with async_session_factory() as db:
        try:
            await _request_figure_gaps(db)
            res = await db.execute(_pending_lessons_query())
            lesson_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning("lesson_content_worker_tick_failed", error=str(exc))
            return

    if not lesson_ids:
        return

    # Dedup + claim ATOMICO: filtriamo le lezioni nuove e le marchiamo come
    # inflight nello stesso lock. Senza il claim qui, le task accodate dietro
    # il semaforo non risultano in `_inflight` finché non lo acquisiscono →
    # il tick successivo (4s dopo) le ridispatcherebbe duplicate, generando
    # uno storm di `lesson_content_skip_not_pending` quando finalmente
    # eseguono e trovano `status=processing` settato dal vincitore.
    async with _inflight_lock:
        new_ids = [lid for lid in lesson_ids if lid not in _inflight]
        for lid in new_ids:
            _inflight.add(lid)

    for lid in new_ids:
        task = asyncio.create_task(
            _bound_process(lid),
            name=f"lesson_content_lesson_{lid}",
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


# ---------------------------------------------------------------------------
# Run loop + lifecycle
# ---------------------------------------------------------------------------


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_lesson_content_poll_interval_seconds))
    log.info(
        "course_lesson_content_worker_started",
        interval=interval,
        max_concurrency=settings.course_lesson_content_max_concurrency,
    )
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
    log.info("course_lesson_content_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _worker_task is not None and not _worker_task.done():
        return
    settings = get_settings()
    _stop_event = asyncio.Event()
    _semaphore = asyncio.Semaphore(max(1, int(settings.course_lesson_content_max_concurrency)))
    _worker_task = asyncio.create_task(_run_loop(), name="course_lesson_content_worker")


async def stop_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=15)
        except TimeoutError:
            _worker_task.cancel()
            await asyncio.gather(_worker_task, return_exceptions=True)
    if _active_tasks:
        log.info(
            "lesson_content_worker_waiting_inflight",
            count=len(_active_tasks),
        )
        await asyncio.gather(*list(_active_tasks), return_exceptions=True)
    _worker_task = None
    _stop_event = None
    _semaphore = None
    _inflight.clear()
    _active_tasks.clear()
