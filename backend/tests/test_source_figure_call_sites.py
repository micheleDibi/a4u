"""I tre punti di chiamata del resolver delle figure di fonte (G1, WP4).

Dispensa (`materialize_lesson_pdf`), PDF slide (`materialize_lesson_slides_pdf`)
e frame video (`render_slides_to_png`) risolvono le figure di fonte del
corso e passano la mappa al render. Se uno dei tre smettesse di farlo, ogni
figura di fonte diventerebbe un segnaposto su quella superficie senza che
nessun test di resa se ne accorga: qui il resolver e il render sono
sostituiti da sonde e il flusso si ferma al render.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import course_lesson_content_service as content_svc
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import lesson_slides_video_render_service as video
from app.services.source_figure_service import ResolvedSourceFigure
from tests.course_builders import build_course

FIG = str(uuid.UUID("0f3c2a52-1111-4a4a-9a9a-222233334444"))
SENTINEL = {"SRC-a": ResolvedSourceFigure(False, "not_found")}


class _StopError(Exception):
    pass


async def _course(db: AsyncSession) -> tuple[Any, Any]:
    course_id, _org, _user = await build_course(
        db, modules=1, lessons_per_module=1, content_status="ready", slides_status="ready"
    )
    course = await content_svc.load_course_full(db, course_id=course_id)
    assert course is not None
    lesson = course.modules[0].lessons[0]
    lesson.content_raw = {
        "introduction": "Schema [FIG:SRC-a].",
        "sections": [],
        "summary": "Sintesi.",
        "visual_assets": [
            {"asset_id": "SRC-a", "format": "source_figure", "content": FIG, "caption": "S"}
        ],
    }
    lesson.slides_raw = {
        "lesson_id": lesson.lesson_code,
        "total_slides": 1,
        "slides": [
            {
                "slide_id": "s1",
                "slide_number": 1,
                "type": "diagram",
                "title": "Schema",
                "references_assets": ["SRC-a"],
            }
        ],
    }
    await db.commit()
    return course, lesson


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    seen: dict[str, list[Any]] = {"resolve": [], "render": []}

    async def resolve(db: Any, *, course_id: uuid.UUID, assets: Any, **kw: Any) -> Any:
        seen["resolve"].append((course_id, [a.get("asset_id") for a in assets]))
        return SENTINEL

    def render(*args: Any, **kwargs: Any) -> str:
        seen["render"].append(kwargs.get("source_figures"))
        raise _StopError

    for module in (pdf, slides_pdf, video):
        monkeypatch.setattr(module, "resolve_source_figures", resolve)
    monkeypatch.setattr(pdf, "render_lesson_html", render)
    monkeypatch.setattr(slides_pdf, "render_slides_html", render)
    return seen


async def test_lesson_pdf_passes_the_resolved_map(
    seeded_db: AsyncSession, probes: dict[str, list[Any]]
) -> None:
    course, lesson = await _course(seeded_db)
    with pytest.raises(_StopError):
        await pdf.materialize_lesson_pdf(seeded_db, course=course, lesson=lesson)
    assert probes["resolve"] == [(course.id, ["SRC-a"])]
    assert probes["render"] == [SENTINEL]


async def test_slides_pdf_passes_the_resolved_map(
    seeded_db: AsyncSession, probes: dict[str, list[Any]]
) -> None:
    course, lesson = await _course(seeded_db)
    with pytest.raises(_StopError):
        await slides_pdf.materialize_lesson_slides_pdf(seeded_db, course=course, lesson=lesson)
    assert probes["resolve"] == [(course.id, ["SRC-a"])]
    assert probes["render"] == [SENTINEL]


async def test_video_frames_pass_the_resolved_map(
    seeded_db: AsyncSession, probes: dict[str, list[Any]], tmp_path: Path
) -> None:
    course, lesson = await _course(seeded_db)
    with pytest.raises(_StopError):
        await video.render_slides_to_png(
            seeded_db, course=course, lesson=lesson, output_dir=tmp_path
        )
    assert probes["resolve"] == [(course.id, ["SRC-a"])]
    assert probes["render"] == [SENTINEL]
