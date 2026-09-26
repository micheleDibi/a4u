"""Completamento delle figure di una lezione (piano delle figure, doc 18 §24).

Una figura adatta a un fabbisogno scoperto può arrivare DOPO la generazione
della dispensa: la verifica dei buchi la trova nella letteratura aperta, o
l'estrazione di un documento la aggiunge al catalogo. Questo modulo:

- trova, per i fabbisogni scoperti della lezione, la figura del corso che li
  copre meglio (stesso abbinamento, tetto di riuso e budget del piano;
  prima i must);
- la inserisce nella sezione del fabbisogno, dopo la figura del membro
  precedente della sequenza, preceduta dalla frase che la introduce
  (PROMPT 23);
- lo fa da solo (`fill_lesson`) solo nelle lezioni pronte e non approvate,
  col contenuto non superato da modifiche a monte, sotto il lock di corso;
  aggiorna `content_generated_at` (PDF e slide risultano da riesportare) e
  la fotografia dell'assegnazione;
- prepara l'inserimento per «Inserisci» dell'editor (`draft_insertion`),
  che modifica solo la bozza: salva il docente.

Nessuna figura entra se il docente ha detto «Non serve» (fabbisogni attivi).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import figure_plan_service as plan
from app.services import source_figure_assignment_service as assignment
from app.services import source_figure_catalog as catalog
from app.services.document_figures_service import source_figure_ids
from app.services.figure_need_matching import FigureIndex
from app.services.figure_needs_view import figure_needs_view
from app.services.lesson_figure_selection import figure_ref
from app.services.openai_figure_intro_service import IntroInput, intro_or_fallback
from app.services.source_figure_plan import (
    add_intro,
    figure_caption,
    insert_figure_block,
    sequence_neighbors,
)

log = get_logger("app.source_figure_fill")

_OPEN = ("uncovered", "missing")


@dataclass(frozen=True)
class FillCandidate:
    need_id: str
    section_id: str
    figure_id: uuid.UUID
    relation: str
    tier: int
    caption: str
    description: str
    subject: str


def _now() -> datetime:
    return datetime.now(UTC)


def _open_needs(lesson: CourseLesson) -> list[dict[str, Any]]:
    """Voci della vista ancora da coprire (una figura non citata non conta:
    è già nella lezione)."""
    return [
        v
        for v in figure_needs_view(lesson) or []
        if v["status"] in _OPEN and v.get("reason") != "not_cited"
    ]


async def candidates(
    db: AsyncSession, course: Course, lesson: CourseLesson, *, within_budget: bool
) -> list[FillCandidate]:
    """La figura migliore per ciascun fabbisogno scoperto (must prima),
    distinta per fabbisogno; con `within_budget` al più i posti rimasti nel
    budget del piano."""
    if not plan.plan_active() or lesson.is_assessment:
        return []
    open_needs = _open_needs(lesson)
    if not open_needs:
        return []
    ready = {str(n.get("need_id")): n for n in assignment._ready_needs(course, lesson)}
    policy = await catalog.license_policy_for(db, course)
    figures = await catalog.selectable_figures(
        db, course, license_policy=policy, lesson_id=lesson.id
    )
    in_lesson = source_figure_ids(lesson.content_raw)
    pool = [f for f in figures if f.id not in in_lesson]
    by_id = {f.id: f for f in pool}
    index = FigureIndex(pool, legacy=bool(get_settings().figure_plan_legacy_match_enabled))
    ordered = sorted(
        open_needs, key=lambda v: (0 if v.get("priority") == "must" else 1, v.get("label", ""))
    )
    taken: set[uuid.UUID] = set()
    out: list[FillCandidate] = []
    for entry in ordered:
        need = ready.get(str(entry["need_id"]))
        if need is None:
            continue
        options = [
            (fid, found) for fid, found in index.covering(need) if fid not in taken and fid in by_id
        ]
        # A parità di livello, prima le figure non a bassa risoluzione.
        options.sort(
            key=lambda fm: (
                -fm[1].tier,
                catalog.resolution_class(by_id[fm[0]]) == "low",
                -fm[1].score,
            )
        )
        if not options:
            continue
        fid, found = options[0]
        taken.add(fid)
        fig = by_id[fid]
        out.append(
            FillCandidate(
                need_id=str(entry["need_id"]),
                section_id=str(entry.get("section_id") or ""),
                figure_id=fid,
                relation=found.relation,
                tier=found.tier,
                caption=figure_caption(fig) or str(entry.get("subject") or ""),
                description=str(getattr(fig, "description", "") or ""),
                subject=str(entry.get("subject") or ""),
            )
        )
    if within_budget:
        musts = sum(1 for n in ready.values() if n.get("priority") == "must")
        room = assignment.plan_budget(course, lesson, musts) - len(in_lesson)
        out = out[: max(0, room)]
    return out


def _unique_ref(fid: uuid.UUID, taken: set[str]) -> str:
    length = 8
    ref = figure_ref(fid)
    while ref.lower() in taken and length < 32:
        length += 4
        ref = figure_ref(fid, length)
    return ref


def _section(content_raw: dict[str, Any], section_id: str) -> dict[str, Any] | None:
    for section in content_raw.get("sections") or []:
        if isinstance(section, dict) and str(section.get("section_id") or "") == section_id:
            return section
    return None


def _placed_assets(lesson: CourseLesson) -> dict[str, str]:
    """need_id → asset_id delle voci già nella lezione (per l'ordine delle
    sequenze)."""
    return {
        str(v["need_id"]): str(v["asset_id"])
        for v in figure_needs_view(lesson) or []
        if v.get("asset_id") and v["status"] in ("placed", "misplaced")
    }


async def _place(
    lesson: CourseLesson,
    course: Course,
    section_text: str,
    section_title: str,
    cand: FillCandidate,
    ref: str,
    asset_of: dict[str, str],
) -> tuple[str, dict[str, Any] | None]:
    """Testo della sezione con frase e figura; usage del PROMPT 23."""
    needs = (lesson.figure_needs or {}).get("needs") or []
    need: dict[str, Any] = next((n for n in needs if str(n.get("need_id")) == cand.need_id), {})
    before, after = sequence_neighbors(need, needs, asset_of)
    text = insert_figure_block(section_text, f"[FIG:{ref}]", before=before, after=after)
    context = text[: text.find(f"[FIG:{ref}]")]
    sentence, usage = await intro_or_fallback(
        IntroInput(
            section_title=section_title,
            context=context,
            description=cand.description or cand.caption,
            subject=cand.subject,
            language_code=course.language_code or "it",
        ),
        cand.caption,
    )
    return add_intro(text, ref, sentence), usage


def _asset(ref: str, cand: FillCandidate) -> dict[str, Any]:
    caption = cand.caption[:600]
    return {
        "asset_id": ref,
        "format": "source_figure",
        "content": str(cand.figure_id),
        "caption": caption,
        "alt_text": caption[:400],
    }


def _content_stale(course: Course, lesson: CourseLesson) -> bool:
    """Contenuto superato da modifiche a monte (struttura o architettura):
    il completamento non lo tocca, sarà rigenerato."""
    generated = lesson.content_generated_at
    if generated is None:
        return True
    if lesson.lesson_structure_modified_at and lesson.lesson_structure_modified_at > generated:
        return True
    for module in course.modules or []:
        if module.id == lesson.module_id:
            modified = module.architecture_modified_at
            return bool(modified and modified > generated)
    return False


def _settle_filled(lesson: CourseLesson, placed: list[tuple[FillCandidate, str]]) -> None:
    """Fotografia dell'assegnazione con le figure inserite (legame e
    collocazione), così la vista le conta come collocate."""
    data = dict(lesson.figure_assignment) if isinstance(lesson.figure_assignment, dict) else {}
    if data.get("state") != "settled":
        data = {"state": "settled", "offers": {}, "bound": {}, "placed": []}
    bound = dict(data.get("bound") or {})
    placement = dict(data.get("placement") or {})
    needs = dict(placement.get("needs") or {})
    placed_ids = list(data.get("placed") or [])
    for cand, ref in placed:
        bound[cand.need_id] = str(cand.figure_id)
        needs[cand.need_id] = {
            "status": "auto_placed",
            "asset_id": ref,
            "section": cand.section_id,
            "filled": True,
        }
        placed_ids.append(str(cand.figure_id))
    placement["needs"] = needs
    data.update(
        bound=bound,
        placement=placement,
        placed=sorted(set(placed_ids)),
        missed=sorted(n for n in (data.get("offers") or {}) if n not in bound),
        filled_at=_now().isoformat(),
    )
    lesson.figure_assignment = data


async def fill_lesson(
    db: AsyncSession, course: Course, lesson: CourseLesson, *, trigger: str
) -> list[str]:
    """Completamento automatico: inserisce nella lezione le figure adatte ai
    fabbisogni scoperti, entro il budget del piano. Solo lezioni `ready`
    (mai approvate, in generazione o superate), sotto il lock di corso.
    Non fa commit (lo fa il chiamante). Ritorna gli asset inseriti."""
    settings = get_settings()
    if not (settings.figure_auto_fill_enabled and plan.plan_active()):
        return []
    if lesson.is_assessment or lesson.figure_needs_status != "ready":
        return []
    if not await assignment.course_lock(db, course.id):
        log.info("figure_fill_lock_busy", lesson_id=str(lesson.id))
        return []
    # Sotto il lock: stato e contenuto riletti (una generazione o un
    # salvataggio del docente possono essere arrivati nel frattempo).
    await db.refresh(lesson)
    if lesson.content_status != "ready" or not isinstance(lesson.content_raw, dict):
        return []
    if _content_stale(course, lesson):
        return []
    found = await candidates(db, course, lesson, within_budget=True)
    if not found:
        return []
    raw = dict(lesson.content_raw)
    raw["sections"] = [dict(s) for s in raw.get("sections") or [] if isinstance(s, dict)]
    assets = [dict(a) for a in raw.get("visual_assets") or [] if isinstance(a, dict)]
    taken = {str(a.get("asset_id") or "").lower() for a in assets}
    asset_of = _placed_assets(lesson)
    placed: list[tuple[FillCandidate, str]] = []
    usage_total = lesson.figure_needs_usage
    for cand in found:
        section = _section(raw, cand.section_id)
        if section is None:
            continue
        ref = _unique_ref(cand.figure_id, taken)
        text, usage = await _place(
            lesson,
            course,
            str(section.get("content") or ""),
            str(section.get("title") or ""),
            cand,
            ref,
            asset_of,
        )
        section["content"] = text
        assets.append(_asset(ref, cand))
        taken.add(ref.lower())
        asset_of[cand.need_id] = ref
        placed.append((cand, ref))
        usage_total = plan.merge_usage(usage_total, usage)
    if not placed:
        return []
    raw["visual_assets"] = assets
    lesson.content_raw = raw
    flag_modified(lesson, "content_raw")
    lesson.figure_needs_usage = usage_total
    # Aggiornamento del sistema: il timestamp del worker (mai il
    # `content_modified_at` dei CRUD manuali); PDF e slide risultano da
    # riesportare.
    lesson.content_generated_at = _now()
    _settle_filled(lesson, placed)
    await write_audit(
        db,
        action="course.lesson.content.figures_filled",
        actor_user_id=None,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "trigger": trigger,
            "inserted": [
                {"need_id": c.need_id, "asset_id": ref, "figure_id": str(c.figure_id)}
                for c, ref in placed
            ],
        },
    )
    log.info(
        "figure_fill_applied",
        lesson_id=str(lesson.id),
        trigger=trigger,
        inserted=len(placed),
    )
    return [ref for _c, ref in placed]


async def fill_course(
    db: AsyncSession, course: Course, *, trigger: str, lesson_ids: set[uuid.UUID] | None = None
) -> int:
    """Completamento delle lezioni del corso (tutte o `lesson_ids`), una
    alla volta con un commit ciascuna. Un errore su una lezione non ferma
    le altre. Ritorna le figure inserite."""
    total = 0
    targets = [
        lesson
        for module in course.modules or []
        for lesson in module.lessons or []
        if lesson_ids is None or lesson.id in lesson_ids
    ]
    for lesson in targets:
        if lesson.content_status != "ready" or lesson.figure_needs_status != "ready":
            continue
        lesson_id = lesson.id
        try:
            inserted = await fill_lesson(db, course, lesson, trigger=trigger)
            await db.commit()
            total += len(inserted)
        except Exception as exc:
            await db.rollback()
            log.warning("figure_fill_failed", lesson_id=str(lesson_id), error=str(exc)[:300])
    return total


async def draft_insertion(
    db: AsyncSession,
    course: Course,
    lesson: CourseLesson,
    *,
    need_id: str,
    figure_id: uuid.UUID,
    section_text: str,
    taken_ids: list[str],
) -> dict[str, Any] | None:
    """«Inserisci» dell'editor: testo della sezione (della bozza) con frase e
    figura, e l'asset da aggiungere. Non salva il contenuto (lo fa il
    docente); il costo del PROMPT 23 si somma a `figure_needs_usage`. None
    se la figura non è una candidata del fabbisogno."""
    found = await candidates(db, course, lesson, within_budget=False)
    cand = next((c for c in found if c.need_id == need_id and c.figure_id == figure_id), None)
    if cand is None:
        return None
    section = _section(
        lesson.content_raw if isinstance(lesson.content_raw, dict) else {}, cand.section_id
    )
    title = str((section or {}).get("title") or "")
    ref = _unique_ref(figure_id, {t.lower() for t in taken_ids})
    text, usage = await _place(
        lesson, course, section_text, title, cand, ref, _placed_assets(lesson)
    )
    lesson.figure_needs_usage = plan.merge_usage(lesson.figure_needs_usage, usage)
    return {"section_id": cand.section_id, "section_text": text, "asset": _asset(ref, cand)}
