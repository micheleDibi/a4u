"""Endpoint del formato `tikz` (WP6.5): anteprima dell'editor, vista delle
figure salvate, formati offerti.

- anteprima (`POST …/lesson-assets/render-tikz`, COURSE_EDIT): compila il
  sorgente dell'editor nella sandbox; oltre al limite per IP del router c'è
  una quota per utente in memoria (`FIGURE_TIKZ_PREVIEW_PER_MINUTE`) che si
  consuma solo quando serve compilare (gli hit della cache sono gratis). I
  difetti geometrici sono avvisi, mai errori;
- vista (`POST …/lesson-assets/tikz-view`, COURSE_VIEW): rende SOLO un
  sorgente che esiste già, identico, come asset `tikz` di una lezione del
  corso (`content_raw.visual_assets`). Chi ha solo la vista non può far
  compilare un sorgente arbitrario;
- formati (`GET …/lesson-assets/formats`): `available_formats()`, per il
  menu «Aggiungi asset visivo».

Codici: 409 `figure_format_unavailable` e `tikz_busy` (sandbox occupata;
409 e non 503, che l'API non usa altrove), 422 `tikz_source_invalid`,
`tikz_compile_failed`, `tikz_render_timeout`, `tikz_render_failed`, 404
`tikz_asset_not_found`, 429 `tikz_preview_rate_limited`.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from functools import partial
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, RateLimitedError, ValidationAppError
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services.figure_render_service import (
    REGISTRY,
    RenderedFigure,
    TikzPreview,
    TikzPreviewError,
    TikzRenderer,
    available_formats,
    tikz_timeout_seconds,
)

_WINDOW_SECONDS = 60.0
_quota_lock = threading.Lock()
_quota: dict[uuid.UUID, deque[float]] = {}


def reset_quota() -> None:
    """Svuota la quota (test)."""
    with _quota_lock:
        _quota.clear()


def consume_preview_quota(user_id: uuid.UUID) -> None:
    """Una compilazione in più per l'utente nella finestra di 60 s; oltre
    `figure_tikz_preview_per_minute` → 429."""
    limit = max(1, int(get_settings().figure_tikz_preview_per_minute))
    now = time.monotonic()
    with _quota_lock:
        stamps = _quota.setdefault(user_id, deque())
        while stamps and now - stamps[0] >= _WINDOW_SECONDS:
            stamps.popleft()
        if len(stamps) >= limit:
            raise RateLimitedError(
                "Troppe anteprime TikZ: riprova fra un minuto.",
                code="tikz_preview_rate_limited",
            )
        stamps.append(now)


def _renderer() -> TikzRenderer:
    renderer = REGISTRY.get("tikz")
    if not isinstance(renderer, TikzRenderer) or "tikz" not in available_formats():
        raise ConflictError(
            "Il formato tikz non è disponibile su questo server.",
            code="figure_format_unavailable",
        )
    return renderer


def _raise(error: TikzPreviewError) -> NoReturn:
    if error.code in ("figure_format_unavailable", "tikz_busy"):
        raise ConflictError(error.message, code=error.code) from error
    raise ValidationAppError(
        "Figura TikZ non valida.",
        code=error.code,
        meta={"errors": [{"loc": ["content"], "msg": error.message, "type": error.code}]},
    ) from error


async def preview(content: str, *, user_id: uuid.UUID) -> TikzPreview:
    renderer = _renderer()
    timeout = tikz_timeout_seconds()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                renderer.preview,
                content,
                before_compile=partial(consume_preview_quota, user_id),
            ),
            timeout=timeout,
        )
    except TikzPreviewError as exc:
        _raise(exc)
    except TimeoutError as exc:
        raise ValidationAppError(
            f"Anteprima oltre {timeout:g} s.", code="tikz_render_timeout"
        ) from exc


async def _asset_exists(
    db: AsyncSession, *, course_id: uuid.UUID, asset_id: str, content: str
) -> bool:
    rows = await db.execute(
        select(CourseLesson.content_raw).where(
            CourseLesson.course_id == course_id,
            CourseLesson.content_raw["visual_assets"].contains(
                [{"asset_id": asset_id, "format": "tikz"}]
            ),
        )
    )
    for (raw,) in rows:
        assets = raw.get("visual_assets") if isinstance(raw, dict) else None
        for asset in assets if isinstance(assets, list) else []:
            if (
                isinstance(asset, dict)
                and asset.get("asset_id") == asset_id
                and asset.get("format") == "tikz"
                and asset.get("content") == content
            ):
                return True
    return False


async def view(db: AsyncSession, *, course: Course, asset_id: str, content: str) -> RenderedFigure:
    renderer = _renderer()
    if not await _asset_exists(db, course_id=course.id, asset_id=asset_id, content=content):
        raise NotFoundError("Figura TikZ non trovata nel corso.", code="tikz_asset_not_found")
    timeout = tikz_timeout_seconds()
    try:
        figure = await asyncio.wait_for(
            asyncio.to_thread(renderer.render_figure, content, asset_id=asset_id),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise ValidationAppError(f"Resa oltre {timeout:g} s.", code="tikz_render_timeout") from exc
    if figure is None:
        raise ValidationAppError("Resa della figura TikZ fallita.", code="tikz_render_failed")
    return figure
