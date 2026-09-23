"""Figure di fonte nelle superfici di resa (G1, G7, G8; WP4).

- partial: con `attribution` vuota il markup è byte-identico a prima; con
  la riga, `<span class="figure-source">` in coda alla didascalia;
- dispensa (HTML e PDF WeasyPrint veri): ogni `<img class="source-figure">`
  ha la sua riga «Fonte» nella stessa figura; la pagina del PDF con il
  ritaglio contiene la riga esatta; controprova CSS (`.figure-source`
  nascosta) → la riga sparisce dal PDF, quindi l'oracolo la vede davvero;
- slide (split sì/no, cioè PDF e frame video): la riga sta nella fascia
  `.slide-attribution` della pagina che mostra la figura, mai nella
  didascalia; le altre pagine non hanno fascia;
- una figura non risolta (o senza riga) è il segnaposto numerato
  «Figura non disponibile.»: niente immagine, niente UUID, niente riga;
- geometria: la fascia sta sotto il body, sopra il footer e fuori dalla
  riserva dell'avatar (85 mm a destra);
- resolver (DB): figura del corso → immagine e riga; UUID di un altro corso
  o inventato → mai risolto e nessuna lettura dal file (G7); figura di un
  documento diventato content_only → ancora resa (U1, non retroattivo);
  figura staccata → resa con l'attribuzione congelata; file sparito →
  segnaposto.
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import remote_storage
from app.services.figure_markup import render_figure_html
from app.services.slide_geometry import SlideGeometry
from app.services.source_figure_service import ResolvedSourceFigure, resolve_source_figures
from tests.course_builders import build_course, build_course_document
from tests.source_figure_builders import build_document_figure
from tests.test_lesson_pdf_figures import SVG_A, _pdf_text_and_warnings, _weasyprint

LINE = "Fonte: Rossi, «Vibrometria laser», Dispense di misure, 2021, fig. 2.1, p. 3 (CC BY 4.0)"
FIG_UUID = uuid.UUID("0f3c2a52-1111-4a4a-9a9a-222233334444")


def _png(color: str = "navy") -> bytes:
    image = Image.new("RGB", (320, 200), "white")
    ImageDraw.Draw(image).rectangle([20, 20, 300, 180], outline=color, width=6)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _resolved(line: str = LINE) -> ResolvedSourceFigure:
    import base64

    data_url = "data:image/png;base64," + base64.b64encode(_png()).decode("ascii")
    return ResolvedSourceFigure(
        True,
        figure_id=FIG_UUID,
        data_url=data_url,
        mime_type="image/png",
        width=320,
        height=200,
        attribution_text=line,
    )


def _content_raw() -> dict[str, Any]:
    return {
        "introduction": "Introduzione con [FIG:gen-1].",
        "sections": [
            {
                "section_id": "S1",
                "title": "Il vibrometro",
                "content": "Lo schema è in [FIG:fig-src-1].\n\nPoi [FIG:fig-src-2].",
            }
        ],
        "summary": "Sintesi.",
        "key_takeaways": [],
        "visual_assets": [
            {
                "asset_id": "gen-1",
                "format": "dot",
                "content": "digraph{a->b}",
                "caption": "Catena di misura.",
                "alt_text": "catena",
            },
            {
                "asset_id": "fig-src-1",
                "format": "source_figure",
                "content": str(FIG_UUID),
                "caption": "Schema del vibrometro laser Doppler.",
                "alt_text": "schema del vibrometro",
            },
            {
                "asset_id": "fig-src-2",
                "format": "source_figure",
                "content": str(uuid.uuid4()),
                "caption": "Figura che non si risolve.",
                "alt_text": "mancante",
            },
        ],
    }


def _course() -> Course:
    return Course(title="Misure", language_code="it", cfu=6)


def _lesson(slides: bool = False) -> CourseLesson:
    lesson = CourseLesson(lesson_code="M1.L2", title="Vibrometria", content_raw=_content_raw())
    if slides:
        lesson.slides_raw = {
            "slides": [
                {
                    "slide_id": "s1",
                    "type": "concept",
                    "title": "Principio",
                    "bullets": ["Effetto Doppler", "Interferometro"],
                    "references_assets": ["fig-src-1"],
                },
                {
                    "slide_id": "s2",
                    "type": "concept",
                    "title": "Catena",
                    "body": "La catena di misura.",
                    "references_assets": ["gen-1"],
                },
                {
                    "slide_id": "s3",
                    "type": "concept",
                    "title": "Mancante",
                    "body": "Figura non risolta.",
                    "references_assets": ["fig-src-2"],
                },
            ]
        }
    return lesson


def _figures(html: str) -> dict[str, str]:
    """{asset_id → markup della <figure>} (una figura per asset)."""
    out: dict[str, str] = {}
    for match in re.finditer(
        r'<figure class="visual[^"]*" data-asset-id="([^"]*)".*?</figure>', html, re.S
    ):
        out[match.group(1)] = match.group(0)
    return out


# --- partial ---------------------------------------------------------------------


def test_partial_is_byte_identical_without_attribution() -> None:
    kwargs: dict[str, Any] = {
        "body_html": None,
        "caption": "Schema.",
        "alt_text": "schema",
        "asset_id": "a",
        "fmt": "dot",
        "number": 2,
        "labels": None,
        "variant": "lesson",
        "fallback_source": "digraph{}",
    }
    assert render_figure_html(**kwargs) == render_figure_html(**kwargs, attribution="")
    with_line = render_figure_html(**kwargs, attribution="Fonte: <script>x</script>")
    assert '<span class="figure-source">Fonte: &lt;script&gt;x&lt;/script&gt;</span>' in with_line
    assert with_line.index("figure-source") > with_line.index("figure-label")


# --- dispensa ------------------------------------------------------------------------


def _lesson_html(**extra: Any) -> str:
    return pdf.render_lesson_html(
        course=_course(),
        lesson=_lesson(),
        organization=None,
        pdf_template=None,
        visual_svg_map={"gen-1": SVG_A},
        source_figures={"fig-src-1": _resolved()},
        **extra,
    )


def test_lesson_html_every_source_image_has_its_line() -> None:
    html = _lesson_html()
    figures = _figures(html)
    shown = figures["fig-src-1"]
    assert '<img class="source-figure" src="data:image/png;base64,' in shown
    assert 'alt="schema del vibrometro"' in shown
    assert f'<span class="figure-source">{LINE}</span>' in shown.replace("&#34;", '"')
    assert "Figura 2." in shown  # numerata come le altre (prima citazione)
    # Ogni immagine di fonte ha la sua riga, nella stessa figura.
    for markup in figures.values():
        assert markup.count('class="source-figure"') == markup.count('class="figure-source"')
    # Non risolta: segnaposto numerato, niente immagine, niente riga, niente UUID.
    missing = figures["fig-src-2"]
    assert "Figura non disponibile." in missing and "Figura 3." in missing
    assert 'source-figure"' not in missing and "figure-source" not in missing
    content = _content_raw()["visual_assets"][2]["content"]
    assert content not in html
    # Le figure generate non cambiano.
    assert "figure-source" not in figures["gen-1"]


def test_resolved_figure_without_line_is_a_placeholder() -> None:
    html = pdf.render_lesson_html(
        course=_course(),
        lesson=_lesson(),
        organization=None,
        pdf_template=None,
        visual_svg_map={"gen-1": SVG_A},
        source_figures={"fig-src-1": _resolved(line="  ")},
    )
    shown = _figures(html)["fig-src-1"]
    assert "Figura non disponibile." in shown and "data:image/png" not in shown


def test_lesson_pdf_page_with_the_crop_carries_the_exact_line() -> None:
    weasyprint = _weasyprint()
    html = _lesson_html()
    _data, text, _warnings = _pdf_text_and_warnings(weasyprint, html)
    flat = " ".join(text.split())
    assert "Rossi, «Vibrometria laser», Dispense di misure, 2021, fig. 2.1, p. 3" in flat
    # Controprova: nascondendo la riga via CSS l'oracolo non la trova più.
    hidden = html.replace("</head>", "<style>.figure-source{display:none}</style></head>", 1)
    _data, text_hidden, _warnings = _pdf_text_and_warnings(weasyprint, hidden)
    assert "Vibrometria laser»" not in " ".join(text_hidden.split())


# --- slide e frame video ----------------------------------------------------------------


def _slides_html(enable_split: bool) -> str:
    return slides_pdf.render_slides_html(
        course=_course(),
        lesson=_lesson(slides=True),
        organization=None,
        slide_template=None,
        visual_svg_map={"gen-1": SVG_A},
        enable_split=enable_split,
        source_figures={"fig-src-1": _resolved()},
    )


def _pages(html: str) -> list[str]:
    return html.split('<div class="slide">')[1:]


@pytest.mark.parametrize("enable_split", [True, False], ids=["pdf-split", "video-frame"])
def test_slide_band_is_on_the_page_that_shows_the_figure(enable_split: bool) -> None:
    pages = _pages(_slides_html(enable_split))
    with_image = [p for p in pages if 'class="source-figure"' in p]
    assert len(with_image) == 1
    page = with_image[0]
    band = re.search(r'<div class="slide-attribution">(.*?)</div>', page, re.S)
    assert band is not None
    assert f'<span class="source-line">{LINE}</span>' in band.group(1)
    # Mai nella didascalia della figura.
    caption = re.search(r"<figcaption.*?</figcaption>", page, re.S)
    assert caption is not None and "Fonte:" not in caption.group(0)
    # Le altre pagine (bullet, figura generata, segnaposto) non hanno fascia.
    for other in pages:
        if other is not page:
            assert "slide-attribution" not in other
    missing = [p for p in pages if "Figura non disponibile." in p]
    assert len(missing) == 1 and "data:image/png" not in missing[0]


def test_band_geometry_is_outside_body_footer_and_avatar() -> None:
    geometry = SlideGeometry()
    x0, y0, x1, y1 = geometry.attribution_box_mm
    assert x0 == geometry.body_left_mm
    # Fuori dalla riserva dell'avatar (85 mm a destra, come il footer).
    assert x1 <= geometry.page_w_mm - 85.0
    # Sotto il body e sopra il footer (bottom 10 mm + circa 12 mm di contenuto).
    assert y0 >= geometry.body_bottom_y_mm
    assert y1 <= geometry.page_h_mm - 10.0 - 12.0
    # Due righe a 8 pt con interlinea 1,3 entrano nell'altezza della fascia.
    assert 2 * geometry.attribution_pt * 1.3 * 25.4 / 72 <= geometry.attribution_max_h_mm


def test_slide_pdf_carries_the_line_on_the_figure_page() -> None:
    weasyprint = _weasyprint()
    _data, text, _warnings = _pdf_text_and_warnings(weasyprint, _slides_html(True))
    assert "«Vibrometria laser», Dispense di misure" in " ".join(text.split())


# --- resolver (DB) --------------------------------------------------------------------


class _Storage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.reads: list[str] = []

    def download_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        if key not in self.files:
            raise remote_storage.StorageFileNotFound(key)
        return self.files[key]


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    fake = _Storage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


async def test_resolver_course_scope_and_non_retroactive(
    seeded_db: AsyncSession, storage: _Storage
) -> None:
    db = seeded_db
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    other_course, _org2, _user2 = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="Dispense_misure.pdf")
    doc.bibliography = {"title": "Vibrometria laser", "authors": ["Mario Rossi"], "year": 2021}
    doc.bibliography_source = "user"
    doc.license = "cc_by"
    foreign_doc = build_course_document(other_course, filename="altro.pdf")
    db.add_all([doc, foreign_doc])
    await db.flush()
    good = build_document_figure(course_id, doc.id, license="cc_by")
    not_ready = build_document_figure(course_id, doc.id, license="cc_by", locator="p0002-f01")
    not_ready.status = "extracted"
    gone = build_document_figure(course_id, doc.id, license="cc_by", locator="p0003-f01")
    foreign = build_document_figure(other_course, foreign_doc.id, license="cc_by")
    db.add_all([good, not_ready, gone, foreign])
    await db.commit()
    for fig in (good, foreign):
        storage.files[remote_storage.uploads_key(str(fig.storage_path))] = _png()

    def asset(aid: str, content: str) -> dict[str, Any]:
        return {"asset_id": aid, "format": "source_figure", "content": content}

    assets = [
        asset("ok", str(good.id)),
        asset("foreign", str(foreign.id)),
        asset("invented", str(uuid.uuid4())),
        asset("garbage", "../../etc/passwd"),
        asset("not-ready", str(not_ready.id)),
        asset("gone", str(gone.id)),
        {"asset_id": "gen", "format": "dot", "content": "digraph{}"},
    ]
    out = await resolve_source_figures(db, course_id=course_id, assets=assets, language="it")
    assert set(out) == {"ok", "foreign", "invented", "garbage", "not-ready", "gone"}
    ok = out["ok"]
    assert ok.renderable and ok.data_url.startswith("data:image/png;base64,")
    assert ok.attribution_text.startswith("Fonte: Mario Rossi, «Vibrometria laser»")
    assert "CC BY" in ok.attribution_text
    assert (out["foreign"].renderable, out["foreign"].reason) == (False, "not_found")
    assert (out["invented"].renderable, out["invented"].reason) == (False, "not_found")
    assert (out["garbage"].renderable, out["garbage"].reason) == (False, "invalid_reference")
    assert (out["not-ready"].renderable, out["not-ready"].reason) == (False, "not_ready")
    assert (out["gone"].renderable, out["gone"].reason) == (False, "file_missing")
    # G7: il file della figura dell'altro corso non viene mai letto.
    assert remote_storage.uploads_key(str(foreign.storage_path)) not in storage.reads

    # U1: il documento diventa content_only → la figura collocata resta.
    doc.citation_policy = "content_only"
    await db.commit()
    again = await resolve_source_figures(db, course_id=course_id, assets=assets[:1], language="it")
    assert again["ok"].renderable and again["ok"].attribution_text == ok.attribution_text
    # Senza byte (payload API): stessa riga, nessuna lettura.
    reads = len(storage.reads)
    light = await resolve_source_figures(
        db, course_id=course_id, assets=assets[:1], language="it", with_bytes=False
    )
    assert light["ok"].renderable and light["ok"].data_url == ""
    assert light["ok"].attribution_text == ok.attribution_text
    assert len(storage.reads) == reads


async def test_resolver_detached_figure_keeps_its_frozen_attribution(
    seeded_db: AsyncSession, storage: _Storage
) -> None:
    from app.services import document_figures_service

    db = seeded_db
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="Dispense_misure.pdf")
    doc.bibliography = {"title": "Vibrometria laser", "authors": ["Mario Rossi"]}
    doc.bibliography_source = "user"
    db.add(doc)
    await db.flush()
    fig = build_document_figure(course_id, doc.id, license="unknown")
    db.add(fig)
    await db.commit()
    storage.files[remote_storage.uploads_key(str(fig.storage_path))] = _png()
    assets = [{"asset_id": "fig-src-1", "format": "source_figure", "content": str(fig.id)}]
    before = await resolve_source_figures(db, course_id=course_id, assets=assets, language="it")
    lesson = await db.get(CourseLesson, (await _first_lesson_id(db, course_id)))
    assert lesson is not None
    lesson.content_raw = {"visual_assets": assets}
    await db.commit()
    await document_figures_service.prepare_document_deletion(db, doc)
    await db.delete(doc)
    await db.commit()
    after = await resolve_source_figures(db, course_id=course_id, assets=assets, language="it")
    assert after["fig-src-1"].renderable
    assert after["fig-src-1"].attribution_text == before["fig-src-1"].attribution_text


async def _first_lesson_id(db: AsyncSession, course_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy import select

    lesson_id = (
        (await db.execute(select(CourseLesson.id).where(CourseLesson.course_id == course_id)))
        .scalars()
        .first()
    )
    assert lesson_id is not None
    return lesson_id


def test_render_modules_never_read_storage_paths_from_the_asset() -> None:
    """Il PATH del file non passa mai dall'asset: `content` è un UUID e
    l'immagine arriva solo dal resolver (niente `lesson_assets/...`)."""
    source = Path(pdf.__file__).read_text(encoding="utf-8")
    branch = source[source.index("elif fmt == SOURCE_FIGURE_FORMAT:") :]
    branch = branch[: branch.index('elif fmt == "image":')]
    assert "_resolve_template_asset_url" not in branch and "content" not in branch


def test_video_frame_band_is_drawn_outside_the_avatar(tmp_path: Path) -> None:
    """Frame video VERI (Chromium, come `render_slides_to_png`): la riga
    «Fonte» si vede nella fascia della slide con la figura, non nelle altre,
    e la zona dell'avatar (a destra, sotto il body) resta identica fra un
    frame con la fascia e uno senza."""
    from tests.dep_guard import require_module

    require_module("playwright", "playwright")
    from app.services import lesson_slides_video_render_service as video

    html = _slides_html(False).replace("</head>", video._VIDEO_OVERRIDE_CSS + "</head>", 1)
    try:
        frames = video._screenshot_slides_sync(html, tmp_path)
    except Exception as exc:  # Chromium di Playwright non installato
        pytest.skip(f"[dep:chromium] {exc}")
    assert len(frames) == 3
    images = [Image.open(f).convert("L") for f in frames]
    px_per_mm = images[0].width / 297.0
    geometry = SlideGeometry()

    def box(x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return tuple(round(v * px_per_mm) for v in (x0, y0, x1, y1))  # type: ignore[return-value]

    def dark(image: Image.Image, area: tuple[int, int, int, int]) -> int:
        return sum(1 for value in image.crop(area).getdata() if value < 160)

    band = box(*geometry.attribution_box_mm)
    assert dark(images[0], band) > 200, "riga «Fonte» assente dal frame della figura"
    assert dark(images[1], band) == 0 and dark(images[2], band) == 0
    # Zona dell'avatar: a destra della riserva, sotto il body.
    avatar = box(
        geometry.page_w_mm - 85.0, geometry.body_bottom_y_mm + 1, 297.0, geometry.page_h_mm
    )
    assert list(images[0].crop(avatar).getdata()) == list(images[1].crop(avatar).getdata())
