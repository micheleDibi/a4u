"""Endpoint del formato `tikz` (WP6.5, W6-T5).

Senza TeX (renderer con la compilazione sostituita, lexer vero):
- spento di default: anteprima e vista 409 `figure_format_unavailable`,
  `formats` senza `tikz`;
- anteprima (COURSE_EDIT): 422 `tikz_source_invalid` con `loc=["content"]`
  dal lexer; SVG, avvisi geometrici e hash; quota per utente consumata
  solo dalle compilazioni (gli hit della cache non contano) → 429; sandbox
  occupata → 409 `tikz_busy`; 403 senza COURSE_EDIT;
- vista (COURSE_VIEW): solo un sorgente identico a un asset `tikz` salvato
  in una lezione del corso, altrimenti 404 `tikz_asset_not_found`.

Con TeX (container `test`): l'anteprima vera compila e misura.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import R
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.services import figure_render_service as frs
from app.services import tikz_api_service, tikz_compile_service
from app.services.figure_render_service import RenderedFigure, SvgMetrics
from tests.dep_guard import require_binary
from tests.test_admin_user_management import _bearer
from tests.test_function_figure_service import _course_for
from tests.test_tikz_validator import CHAIN

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h1"/></svg>'


class _FakeTikz(frs.TikzRenderer):
    def __init__(self) -> None:
        self.compiled: list[str] = []
        self.busy = False

    def available(self) -> bool:
        return True

    def _render_or_raise(self, sanitized: str) -> RenderedFigure:
        if self.busy:
            raise tikz_compile_service.TikzBusyError("compilazione TikZ occupata")
        self.compiled.append(sanitized)
        metrics = SvgMetrics(
            font_px_min=11.0,
            font_px_median=11.0,
            text_count=1,
            source="parsed",
            defects=("labels_overlap: «A» su «B»",),
        )
        return RenderedFigure(svg=SVG, metrics=metrics)


@pytest.fixture(autouse=True)
def _clean() -> Any:
    tikz_api_service.reset_quota()
    frs.clear_svg_cache()
    yield
    tikz_api_service.reset_quota()
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeTikz:
    renderer = _FakeTikz()
    monkeypatch.setitem(frs.REGISTRY, "tikz", renderer)
    formats = ("mermaid", "vegalite", "dot", "function", "tikz")
    monkeypatch.setattr(frs, "available_formats", lambda: formats)
    monkeypatch.setattr(tikz_api_service, "available_formats", lambda: formats)
    patched = frs.get_settings().model_copy(update={"figure_tikz_preview_per_minute": 2})
    monkeypatch.setattr(tikz_api_service, "get_settings", lambda: patched)
    return renderer


def _base(org_id: uuid.UUID, course_id: uuid.UUID) -> str:
    return f"/api/v1/orgs/{org_id}/courses/{course_id}/lesson-assets"


async def _lesson_with(db: AsyncSession, course_id: uuid.UUID, content: str) -> None:
    module = CourseModule(
        course_id=course_id,
        position=1,
        module_code="M1",
        title="Modulo 1",
        lessons_structure_status="approved",
    )
    db.add(module)
    await db.flush()
    db.add(
        CourseLesson(
            module_id=module.id,
            course_id=course_id,
            position=1,
            lesson_code="M1.L1",
            title="Catene di misura",
            summary="Sommario.",
            learning_objectives=[],
            mandatory_topics=[],
            prerequisites=[],
            section_outline=[],
            content_status="ready",
            content_raw={
                "introduction": "Vedi [FIG:t1].",
                "sections": [],
                "summary": "",
                "visual_assets": [
                    {"asset_id": "t1", "format": "tikz", "content": content, "caption": "C."},
                    {"asset_id": "m1", "format": "mermaid", "content": "flowchart LR\n a-->b"},
                ],
            },
        )
    )
    await db.commit()


async def test_off_by_default(client: Any, seeded_db: AsyncSession) -> None:
    user_id, org_id, course_id = await _course_for(seeded_db, role_code=R.MANAGER)
    headers = _bearer(user_id)
    res = await client.post(
        f"{_base(org_id, course_id)}/render-tikz", json={"content": CHAIN}, headers=headers
    )
    assert res.status_code == 409 and res.json()["code"] == "figure_format_unavailable"
    res = await client.post(
        f"{_base(org_id, course_id)}/tikz-view",
        json={"asset_id": "t1", "content": CHAIN},
        headers=headers,
    )
    assert res.status_code == 409
    res = await client.get(f"{_base(org_id, course_id)}/formats", headers=headers)
    assert res.status_code == 200 and "tikz" not in res.json()["formats"]


async def test_preview_lexer_warnings_quota_and_busy(
    client: Any, seeded_db: AsyncSession, fake: _FakeTikz
) -> None:
    user_id, org_id, course_id = await _course_for(seeded_db, role_code=R.MANAGER)
    url, headers = f"{_base(org_id, course_id)}/render-tikz", _bearer(user_id)

    bad = r"\begin{tikzpicture}\input{/etc/passwd}\end{tikzpicture}"
    res = await client.post(url, json={"content": bad}, headers=headers)
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "tikz_source_invalid"
    (error,) = body["meta"]["errors"]
    assert error["loc"] == ["content"] and error["msg"].startswith("tikz_source_invalid")
    assert fake.compiled == []

    for _ in range(4):  # un solo sorgente: una compilazione, poi cache
        res = await client.post(url, json={"content": CHAIN}, headers=headers)
        assert res.status_code == 200, res.text
    body = res.json()
    assert body["svg"] == SVG and body["font_px_min"] == 11.0
    assert body["warnings"] == ["labels_overlap: «A» su «B»"]
    assert len(body["content_hash"]) == 64 and len(fake.compiled) == 1

    second = CHAIN.replace("ADC", "Convertitore")
    assert (await client.post(url, json={"content": second}, headers=headers)).status_code == 200
    third = CHAIN.replace("ADC", "Registratore")
    res = await client.post(url, json={"content": third}, headers=headers)
    assert res.status_code == 429 and res.json()["code"] == "tikz_preview_rate_limited"

    tikz_api_service.reset_quota()
    fake.busy = True
    res = await client.post(url, json={"content": third}, headers=headers)
    assert res.status_code == 409 and res.json()["code"] == "tikz_busy"


async def test_preview_needs_course_edit(
    client: Any, seeded_db: AsyncSession, fake: _FakeTikz
) -> None:
    user_id, org_id, course_id = await _course_for(seeded_db, role_code=R.MEMBER)
    res = await client.post(
        f"{_base(org_id, course_id)}/render-tikz",
        json={"content": CHAIN},
        headers=_bearer(user_id),
    )
    assert res.status_code == 403 and fake.compiled == []


async def test_view_renders_only_saved_sources(
    client: Any, seeded_db: AsyncSession, fake: _FakeTikz
) -> None:
    user_id, org_id, course_id = await _course_for(seeded_db, role_code=R.MEMBER)
    await _lesson_with(seeded_db, course_id, CHAIN)
    url, headers = f"{_base(org_id, course_id)}/tikz-view", _bearer(user_id)

    res = await client.post(url, json={"asset_id": "t1", "content": CHAIN}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {"svg": SVG}
    for payload in (
        {"asset_id": "t1", "content": CHAIN + "\n% altro"},  # sorgente diverso
        {"asset_id": "t2", "content": CHAIN},  # id assente
        {"asset_id": "m1", "content": "flowchart LR\n a-->b"},  # non tikz
    ):
        res = await client.post(url, json=payload, headers=headers)
        assert res.status_code == 404, payload
        assert res.json()["code"] == "tikz_asset_not_found"
    assert len(fake.compiled) == 1

    # Un altro corso non vede la figura.
    other_user, other_org, other_course = await _course_for(seeded_db, role_code=R.MANAGER)
    res = await client.post(
        f"{_base(other_org, other_course)}/tikz-view",
        json={"asset_id": "t1", "content": CHAIN},
        headers=_bearer(other_user),
    )
    assert res.status_code == 404
    res = await client.get(f"{_base(org_id, course_id)}/formats", headers=headers)
    assert "tikz" in res.json()["formats"]


def test_real_preview_compiles_with_tex(monkeypatch: pytest.MonkeyPatch) -> None:
    require_binary("tex", "xelatex", "kpsewhich", "pdftocairo")
    patched = frs.get_settings().model_copy(update={"figure_tikz_enabled": True})
    monkeypatch.setattr(frs, "get_settings", lambda: patched)
    monkeypatch.setattr(tikz_compile_service, "get_settings", lambda: patched)
    frs.available_formats.cache_clear()
    tikz_compile_service.available.cache_clear()
    calls: list[int] = []
    try:
        renderer = frs.REGISTRY["tikz"]
        assert isinstance(renderer, frs.TikzRenderer)
        first = renderer.preview(CHAIN, before_compile=lambda: calls.append(1))
        again = renderer.preview(CHAIN, before_compile=lambda: calls.append(1))
        assert first.svg.startswith("<svg") and first.font_px_min and not first.cached
        assert again.cached and calls == [1]
    finally:
        tikz_compile_service.available.cache_clear()
