"""Piano delle figure di fonte: fabbisogni per lezione (WP4).

I fabbisogni (PROMPT 22, `openai_figure_needs_service`) si calcolano in
modo PIGRO, solo quando si chiede la Fase 3 di una lezione (le tre
`request_*` di `course_lesson_content_service`): mai all'avvio, in una
migrazione o in un tick. Valgono finché l'IMPRONTA dell'input (struttura
di Fase 2 della lezione, titoli delle lezioni sorelle, versione del
prompt, tetto dei fabbisogni) non cambia: una richiesta con la stessa
impronta non ricalcola nulla.

Stato in `course_lesson`:
- `figure_needs_status`: NULL (mai chiesti), `pending`, `processing`,
  `ready`, `failed`, `skipped` (verifiche e lezioni senza scaletta);
- `figure_needs`: `{v, fingerprint, model, needs, dropped}`;
- `figure_needs_requested_at`: quando è stata chiesta la Fase 3 (conta il
  tetto dell'attesa);
- `figure_needs_usage`: costo cumulativo (dashboard admin).

Attesa della Fase 3 (`waiting_clause`): una lezione in coda aspetta finché
una lezione in coda dello stesso corso ha i fabbisogni in coda o in
calcolo, al più `FIGURE_WAIT_MAX_MINUTES` dalla PROPRIA richiesta. Così
l'assegnazione delle figure vede la domanda di tutte le lezioni chieste
insieme. Se i fabbisogni mancano o sono vecchi al momento della
generazione, `ensure_lesson_needs` li calcola inline (una chiamata).

Interruttore: `FIGURE_PLAN_ENABLED` (con `FIGURE_SOURCE_ENABLED`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, true
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import openai_figure_needs_service as needs_svc

log = get_logger("app.figure_plan")

NEEDS_ACTIVE = ("pending", "processing")


def _now() -> datetime:
    return datetime.now(UTC)


def plan_active() -> bool:
    settings = get_settings()
    return bool(settings.figure_source_enabled and settings.figure_plan_enabled)


def max_needs(lesson: CourseLesson) -> int:
    settings = get_settings()
    if lesson.is_introductory:
        return max(0, int(settings.figure_needs_max_per_intro_lesson))
    return max(0, int(settings.figure_needs_max_per_lesson))


def _objectives(lesson: CourseLesson) -> tuple[str, ...]:
    out: list[str] = []
    for item in lesson.learning_objectives or []:
        text = item.get("text") or item.get("objective") if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return tuple(out)


def _topics(lesson: CourseLesson) -> tuple[str, ...]:
    out: list[str] = []
    for item in lesson.mandatory_topics or []:
        if isinstance(item, dict):
            # `topic` in Fase 2; `title` in alcuni dati di prova (D15).
            text = item.get("topic") or item.get("title")
            if isinstance(text, str) and text.strip():
                out.append(text.strip())
    return tuple(out)


def _outline(lesson: CourseLesson) -> tuple[tuple[str, str, str], ...]:
    out: list[tuple[str, str, str]] = []
    for item in lesson.section_outline or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("section_id") or "").strip()
        if sid:
            out.append((sid, str(item.get("title") or ""), str(item.get("purpose") or "")))
    return tuple(out)


def needs_input(course: Course, lesson: CourseLesson) -> needs_svc.NeedsInput | None:
    """Input del PROMPT 22; None per le verifiche e le lezioni senza scaletta."""
    if lesson.is_assessment:
        return None
    outline = _outline(lesson)
    if not outline:
        return None
    siblings: list[str] = []
    for module in course.modules or []:
        if any(item.id == lesson.id for item in module.lessons or []):
            siblings = [
                f"{item.lesson_code} {item.title}"
                for item in module.lessons or []
                if item.id != lesson.id and not item.is_assessment
            ]
            break
    return needs_svc.NeedsInput(
        lesson_code=str(lesson.lesson_code or ""),
        title=str(lesson.title or ""),
        language_code=str(course.language_code or "it"),
        is_introductory=bool(lesson.is_introductory),
        objectives=_objectives(lesson),
        topics=_topics(lesson),
        outline=outline,
        sibling_titles=tuple(siblings),
    )


def fingerprint(item: needs_svc.NeedsInput, max_total: int) -> str:
    payload = {
        **item.fingerprint_payload(),
        "v": needs_svc.PROMPT_VERSION,
        "max": max_total,
        "max_must": needs_svc.MAX_MUST,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def current_needs(lesson: CourseLesson, fp: str | None = None) -> list[dict[str, Any]] | None:
    """Fabbisogni validi (stato `ready` e, se data, stessa impronta)."""
    data = lesson.figure_needs if isinstance(lesson.figure_needs, dict) else None
    if lesson.figure_needs_status != "ready" or data is None:
        return None
    if fp is not None and data.get("fingerprint") != fp:
        return None
    needs = data.get("needs")
    return [n for n in needs if isinstance(n, dict)] if isinstance(needs, list) else None


def request_needs(course: Course, lesson: CourseLesson) -> bool:
    """Richiesta esplicita di Fase 3 per la lezione: annota l'ora (tetto
    dell'attesa) e mette in coda i fabbisogni se mancano o se l'impronta è
    cambiata. Ritorna True se li ha messi in coda."""
    if not plan_active():
        return False
    lesson.figure_needs_requested_at = _now()
    item = needs_input(course, lesson)
    if item is None:
        lesson.figure_needs_status = "skipped"
        return False
    fp = fingerprint(item, max_needs(lesson))
    if current_needs(lesson, fp) is not None:
        return False
    lesson.figure_needs_status = "pending"
    lesson.figure_needs_attempts = 0
    return True


def waiting_clause() -> ColumnElement[bool]:
    """Condizione per cui una lezione in coda PUÒ partire: nessuna lezione in
    coda dello stesso corso ha i fabbisogni in coda o in calcolo, oppure è
    passato il tetto dalla propria richiesta. Vera con il piano spento."""
    if not plan_active():
        return true()
    cutoff = _now() - timedelta(minutes=int(get_settings().figure_wait_max_minutes))
    sister = aliased(CourseLesson)
    busy = (
        select(sister.id)
        .where(
            sister.course_id == CourseLesson.course_id,
            sister.content_status == "pending",
            sister.figure_needs_status.in_(NEEDS_ACTIVE),
        )
        .exists()
    )
    return or_(
        CourseLesson.is_assessment.is_(True),
        CourseLesson.figure_needs_requested_at.is_(None),
        CourseLesson.figure_needs_requested_at <= cutoff,
        ~busy,
    )


def store_needs(
    lesson: CourseLesson,
    *,
    result: needs_svc.NeedsResult,
    fp: str,
    model: str,
    usage: dict[str, Any] | None,
) -> None:
    lesson.figure_needs = {
        "v": needs_svc.PROMPT_VERSION,
        "fingerprint": fp,
        "model": model,
        "needs": result.needs,
        "dropped": result.dropped,
    }
    lesson.figure_needs_status = "ready"
    lesson.figure_needs_checked_at = _now()
    lesson.figure_needs_usage = merge_usage(lesson.figure_needs_usage, usage)


def merge_usage(previous: dict[str, Any] | None, usage: dict[str, Any] | None) -> Any:
    """Somma cumulativa dell'usage delle chiamate del PROMPT 22."""
    if not usage:
        return previous
    if not previous:
        return {**usage, "calls": int(usage.get("calls") or 1)}
    merged = dict(previous)
    for key in ("prompt", "completion", "total", "cached_tokens", "reasoning_tokens"):
        merged[key] = int(merged.get(key) or 0) + int(usage.get(key) or 0)
    merged["calls"] = int(merged.get("calls") or 0) + int(usage.get("calls") or 1)
    if usage.get("cost_usd") is not None:
        merged["cost_usd"] = round(
            float(merged.get("cost_usd") or 0.0) + float(usage["cost_usd"]), 8
        )
    merged["model"] = usage.get("model") or merged.get("model")
    return merged


async def ensure_lesson_needs(course: Course, lesson: CourseLesson) -> list[dict[str, Any]] | None:
    """Fabbisogni validi della lezione, calcolati inline se mancano o sono
    vecchi (ripiego della Fase 3: una chiamata). None con il piano spento,
    per le lezioni senza scaletta o se la chiamata fallisce (la Fase 3
    procede senza piano). Non fa commit: lo fa il chiamante."""
    if not plan_active():
        return None
    item = needs_input(course, lesson)
    if item is None:
        return None
    max_total = max_needs(lesson)
    fp = fingerprint(item, max_total)
    ready = current_needs(lesson, fp)
    if ready is not None:
        return ready
    try:
        result, usage = await needs_svc.generate_needs(item, max_total=max_total)
    except needs_svc.OpenAIFigureNeedsError as exc:
        lesson.figure_needs_usage = merge_usage(lesson.figure_needs_usage, exc.usage)
        log.warning("figure_needs_inline_failed", lesson_id=str(lesson.id), error=str(exc)[:300])
        return None
    store_needs(
        lesson,
        result=result,
        fp=fp,
        model=str(get_settings().openai_figure_needs_model),
        usage=usage,
    )
    log.info(
        "figure_needs_inline", lesson_id=str(lesson.id), needs=len(result.needs), **result.dropped
    )
    return result.needs
