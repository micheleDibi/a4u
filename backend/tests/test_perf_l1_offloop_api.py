"""Prestazioni L1, task B4: «Scarica tutto» e ricodifica delle immagini fuori dall'event loop.

- Bundle PDF di corso e di modulo (merged e zip): download dallo storage, `pypdf` e `zipfile`
  girano in un thread. Con uno storage finto lento (`time.sleep(0.3)` per file) una
  coroutine sonda sullo stesso loop non resta mai ferma più di 100 ms;
- il risultato non cambia: PDF unito valido (pagine e segnalibri in ordine modulo → lezione),
  zip con gli stessi nomi e cartelle, stessi errori (409 non pronti, 404 file mancante);
- il corso dei bundle si carica senza colonne `*_raw`;
- `file_service.save_upload_image`: stesso output byte per byte dell'algoritmo precedente e
  Pillow in un thread (sonda < 100 ms con `exif_transpose` rallentato).
"""

from __future__ import annotations

import asyncio
import io
import struct
import time
import zipfile
import zlib
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers, UploadFile

from app.core.errors import ValidationAppError
from app.core.permissions import R
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import (
    course_lesson_pdf_service,
    course_lesson_slides_pdf_service,
    course_lesson_speech_pdf_service,
    file_service,
    remote_storage,
)
from tests.course_builders import build_course
from tests.test_admin_user_management import _bearer
from tests.test_permissions import _setup_user_membership

_PATH_ATTR = {"content": "pdf_path", "slides": "slides_pdf_path", "speech": "speech_pdf_path"}
_STATUS_ATTR = {
    "content": "pdf_status",
    "slides": "slides_pdf_status",
    "speech": "speech_pdf_status",
}
_URL_PART = {
    "content": "lessons-pdf",
    "slides": "lessons-slides-pdf",
    "speech": "lessons-speech-pdf",
}
_FILENAME = {
    "content": course_lesson_pdf_service.pdf_filename_for_download,
    "slides": course_lesson_slides_pdf_service.slides_pdf_filename_for_download,
    "speech": course_lesson_speech_pdf_service.speech_pdf_filename_for_download,
}


class _Storage:
    """Storage finto: PDF in memoria, `download_bytes` rallentabile (`time.sleep`, come SFTP);
    `upload_bytes` registra i caricamenti."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.uploads: dict[str, bytes] = {}
        self.delay_s = 0.0

    def download_bytes(self, key: str) -> bytes:
        if self.delay_s:
            time.sleep(self.delay_s)
        if key not in self.files:
            raise remote_storage.StorageFileNotFound(key)
        return self.files[key]

    def upload_bytes(self, key: str, data: bytes) -> None:
        if self.delay_s:
            time.sleep(self.delay_s)
        self.uploads[key] = data


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    fake = _Storage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


@pytest_asyncio.fixture(autouse=True)
async def _drop_courses_created_by_test(_engine) -> AsyncIterator[None]:
    """Isolamento: il DB dei test è di sessione. A fine test cancella i corsi creati dal test
    (moduli, lezioni e documenti vanno in cascata), così nessuna riga in stato attivo
    (`pending`/`processing`) resta ai worker dei test successivi."""
    async with _engine.connect() as conn:
        started = (await conn.execute(text("SELECT clock_timestamp()"))).scalar_one()
    yield
    async with _engine.begin() as conn:
        await conn.execute(delete(Course).where(Course.created_at >= started))


def _pdf(width: int) -> bytes:
    """PDF di una pagina; la larghezza identifica la lezione nel PDF unito."""
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


async def _setup(db: AsyncSession, storage: _Storage) -> dict[str, Any]:
    """Corso 2×2 + verifica finale, tutti i PDF pronti (tranne la verifica, che non ne ha).
    `widths[kind]` = larghezze delle pagine in ordine modulo → lezione."""
    user, org, _m = await _setup_user_membership(db, role_code=R.MANAGER)
    course_id, _o, _u = await build_course(db, modules=2, lessons_per_module=2)
    course = await db.get(Course, course_id)
    assert course is not None
    course.organization_id = org.id
    course.assignee_user_id = user.id
    # Una verifica in fondo all'ultimo modulo: senza PDF, esclusa dai bundle.
    last = (
        await db.execute(
            select(CourseLesson).where(
                CourseLesson.course_id == course_id, CourseLesson.lesson_code == "M2.L2"
            )
        )
    ).scalar_one()
    last.is_assessment = True
    lessons = (
        (
            await db.execute(
                select(CourseLesson)
                .where(CourseLesson.course_id == course_id)
                .order_by(CourseLesson.lesson_code)
            )
        )
        .scalars()
        .all()
    )
    widths: dict[str, list[int]] = {k: [] for k in _PATH_ATTR}
    for idx, lesson in enumerate(lessons):
        lesson.content_raw = {"blob": "x" * 5000}
        if lesson.is_assessment:
            continue
        for k_idx, kind in enumerate(_PATH_ATTR):
            rel = f"{course_id}/{lesson.id}-{kind}.pdf"
            setattr(lesson, _PATH_ATTR[kind], rel)
            setattr(lesson, _STATUS_ATTR[kind], "ready")
            width = 100 + 10 * idx + k_idx
            storage.files[remote_storage.pdf_key(rel)] = _pdf(width)
            widths[kind].append(width)
    await db.commit()
    m1 = next(lesson.module_id for lesson in lessons if lesson.lesson_code == "M1.L1")
    return {
        "user": user,
        "course": course,
        "lessons": [lesson for lesson in lessons if not lesson.is_assessment],
        "widths": widths,
        "module_1": m1,
        "base": f"/api/v1/orgs/{org.id}/courses/{course_id}",
    }


def _outline_titles(reader: PdfReader) -> list[str]:
    return [item.title for item in reader.outline]


@pytest.mark.parametrize("kind", ["content", "slides", "speech"])
async def test_course_bundles_unchanged(
    client: AsyncClient, db: AsyncSession, storage: _Storage, kind: str
) -> None:
    ctx = await _setup(db, storage)
    headers = _bearer(ctx["user"].id)
    course = ctx["course"]

    merged = await client.get(
        f"{ctx['base']}/{_URL_PART[kind]}/download-all-merged", headers=headers
    )
    assert merged.status_code == 200, merged.text
    assert merged.headers["content-type"] == "application/pdf"
    assert "attachment;" in merged.headers["content-disposition"]
    reader = PdfReader(io.BytesIO(merged.content))
    assert [int(p.mediabox.width) for p in reader.pages] == ctx["widths"][kind]
    assert _outline_titles(reader) == [
        f"{lesson.lesson_code.split('.')[0]} — {lesson.title}" for lesson in ctx["lessons"]
    ]

    zipped = await client.get(f"{ctx['base']}/{_URL_PART[kind]}/download-all-zip", headers=headers)
    assert zipped.status_code == 200, zipped.text
    assert zipped.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as zf:
        names = zf.namelist()
        assert names == [
            f"{lesson.lesson_code.split('.')[0]} Modulo {lesson.lesson_code[1]}/"
            f"{_FILENAME[kind](course.title, lesson)}"
            for lesson in ctx["lessons"]
        ]
        first = PdfReader(io.BytesIO(zf.read(names[0])))
        assert int(first.pages[0].mediabox.width) == ctx["widths"][kind][0]


async def test_module_bundles_unchanged(
    client: AsyncClient, db: AsyncSession, storage: _Storage
) -> None:
    ctx = await _setup(db, storage)
    headers = _bearer(ctx["user"].id)
    base = f"{ctx['base']}/modules/{ctx['module_1']}/lessons-pdf"
    m1 = [lesson for lesson in ctx["lessons"] if lesson.lesson_code.startswith("M1.")]

    merged = await client.get(f"{base}/download-merged", headers=headers)
    assert merged.status_code == 200, merged.text
    reader = PdfReader(io.BytesIO(merged.content))
    assert [int(p.mediabox.width) for p in reader.pages] == ctx["widths"]["content"][:2]
    assert _outline_titles(reader) == [lesson.title for lesson in m1]

    zipped = await client.get(f"{base}/download-zip", headers=headers)
    assert zipped.status_code == 200, zipped.text
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as zf:
        assert zf.namelist() == [
            course_lesson_pdf_service.pdf_filename_for_download(ctx["course"].title, lesson)
            for lesson in m1
        ]


async def test_bundle_errors_unchanged(
    client: AsyncClient, db: AsyncSession, storage: _Storage
) -> None:
    ctx = await _setup(db, storage)
    headers = _bearer(ctx["user"].id)
    # File mancante sullo storage: l'errore nasce nel thread e arriva identico.
    missing = ctx["lessons"][1]
    del storage.files[remote_storage.pdf_key(missing.pdf_path)]
    resp = await client.get(f"{ctx['base']}/lessons-pdf/download-all-merged", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "module_pdf_file_missing"
    assert missing.lesson_code in resp.json()["message"]
    resp = await client.get(
        f"{ctx['base']}/modules/{ctx['module_1']}/lessons-pdf/download-zip", headers=headers
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "module_pdf_file_missing"

    # PDF non pronti: 409 con le lezioni mancanti, prima di toccare lo storage.
    missing.slides_pdf_status = "pending"
    await db.commit()
    resp = await client.get(f"{ctx['base']}/lessons-slides-pdf/download-all-zip", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["code"] == "course_pdfs_not_ready"
    assert resp.json()["meta"]["missing_lessons"] == [str(missing.id)]


async def test_bundle_loader_skips_raw_columns(
    client: AsyncClient, db: AsyncSession, storage: _Storage, _engine
) -> None:
    ctx = await _setup(db, storage)
    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement.lower())

    event.listen(_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        resp = await client.get(
            f"{ctx['base']}/lessons-speech-pdf/download-all-merged", headers=_bearer(ctx["user"].id)
        )
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", _capture)
    assert resp.status_code == 200
    selects = [s for s in statements if s.lstrip().startswith("select")]
    assert any("course_lesson" in s for s in selects)
    for stmt in selects:
        for raw in ("content_raw", "slides_raw", "speech_raw", "architecture_raw"):
            assert raw not in stmt, (raw, stmt)


async def _probe_while(work: Any) -> tuple[Any, float]:
    """Esegue `work` e, in parallelo sullo stesso loop, una sonda che misura il ritardo
    massimo di brevi sleep finché `work` non finisce. Ritorna (risultato, ritardo in ms)."""
    done = asyncio.Event()
    worst = 0.0

    async def probe() -> None:
        nonlocal worst
        while not done.is_set():
            started = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - started - 0.01)

    async def run() -> Any:
        try:
            return await work
        finally:
            done.set()

    result, _ = await asyncio.gather(run(), probe())
    return result, worst * 1000


@pytest.mark.parametrize("suffix", ["download-all-merged", "download-all-zip"])
async def test_course_bundle_does_not_block_loop(
    client: AsyncClient, db: AsyncSession, storage: _Storage, suffix: str
) -> None:
    ctx = await _setup(db, storage)
    headers = _bearer(ctx["user"].id)
    url = f"{ctx['base']}/lessons-pdf/{suffix}"
    # Prima request a vuoto: la prima chiamata a un'app appena creata paga ~300 ms di
    # inizializzazione sincrona (una tantum), che qui non interessa.
    assert (await client.get(url, headers=headers)).status_code == 200

    storage.delay_s = 0.3  # 3 lezioni → ~0,9 s di storage bloccante
    started = time.perf_counter()
    resp, lag_ms = await _probe_while(client.get(url, headers=headers))
    assert resp.status_code == 200
    assert time.perf_counter() - started >= 0.9  # lo storage lento è stato davvero usato
    assert lag_ms < 100, lag_ms


async def test_module_bundle_does_not_block_loop(
    client: AsyncClient, db: AsyncSession, storage: _Storage
) -> None:
    ctx = await _setup(db, storage)
    headers = _bearer(ctx["user"].id)
    url = f"{ctx['base']}/modules/{ctx['module_1']}/lessons-slides-pdf/download-merged"
    assert (await client.get(url, headers=headers)).status_code == 200
    storage.delay_s = 0.3
    resp, lag_ms = await _probe_while(client.get(url, headers=headers))
    assert resp.status_code == 200
    assert lag_ms < 100, lag_ms


# --- Upload immagine ------------------------------------------------------------------


def _reference_reencode(
    raw: bytes, *, max_dimension: int, square: bool, preserve_format: bool
) -> tuple[bytes, str]:
    """L'algoritmo di `save_upload_image` com'era prima di B4 (copia fedele)."""
    with Image.open(BytesIO(raw)) as img:
        source_format = (img.format or "").upper()
        img = ImageOps.exif_transpose(img)
        if square:
            w, h = img.size
            if w != h:
                side = min(w, h)
                left, top = (w - side) // 2, (h - side) // 2
                img = img.crop((left, top, left + side, top + side))
        fmt = (img.format or "").upper()
        if preserve_format and source_format in file_service.ALLOWED_IMAGE_EXT_BY_FORMAT:
            fmt = source_format
        if fmt not in file_service.ALLOWED_IMAGE_EXT_BY_FORMAT:
            fmt = "PNG" if img.mode in ("RGBA", "LA") else "JPEG"
        ext = file_service.ALLOWED_IMAGE_EXT_BY_FORMAT[fmt]
        if max(img.size) > max_dimension:
            img.thumbnail((max_dimension, max_dimension))
        buffer = BytesIO()
        save_kwargs: dict = {"optimize": True}
        if fmt == "JPEG":
            if img.mode != "RGB":
                img = img.convert("RGB")
            save_kwargs["quality"] = 85
        img.save(buffer, format=fmt, **save_kwargs)
        return buffer.getvalue(), ext


def _image(fmt: str, size: tuple[int, int], mode: str = "RGB", orientation: int = 0) -> bytes:
    img = Image.new(mode, size, "navy")
    for x in range(0, size[0], 7):  # un po' di contenuto, non un colore piatto
        img.putpixel((x, x % size[1]), (200, 30, 30, 255)[: len(mode)])
    buf = BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        img.save(buf, format=fmt, exif=exif)
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


def _upload(data: bytes, mime: str) -> UploadFile:
    return UploadFile(file=BytesIO(data), filename="x", headers=Headers({"content-type": mime}))


_IMAGE_CASES = [
    # (dati, mime, kwargs)
    (_image("PNG", (300, 200), "RGBA"), "image/png", {}),
    (_image("PNG", (600, 300)), "image/png", {"preserve_format": True, "max_dimension": 256}),
    (_image("JPEG", (320, 200), orientation=6), "image/jpeg", {"square": True}),
    (_image("WEBP", (120, 90)), "image/webp", {"max_dimension": 64}),
]


@pytest.mark.parametrize(("data", "mime", "kwargs"), _IMAGE_CASES)
async def test_upload_image_output_unchanged(
    storage: _Storage, data: bytes, mime: str, kwargs: dict[str, Any]
) -> None:
    public = await file_service.save_upload_image(
        _upload(data, mime), subdir="lesson_assets/test", filename_stem="img", **kwargs
    )
    expected, ext = _reference_reencode(
        data,
        max_dimension=kwargs.get("max_dimension", 4096),
        square=kwargs.get("square", False),
        preserve_format=kwargs.get("preserve_format", False),
    )
    assert public == f"/uploads/lesson_assets/test/img{ext}"
    assert storage.uploads == {remote_storage.uploads_key(public): expected}


async def test_upload_invalid_image_still_rejected(storage: _Storage) -> None:
    with pytest.raises(ValidationAppError) as exc:
        await file_service.save_upload_image(
            _upload(b"not an image at all", "image/png"), subdir="lesson_assets/test"
        )
    assert exc.value.code == "invalid_image"
    assert storage.uploads == {}


def _png_header_only(width: int, height: int) -> bytes:
    """PNG di pochi byte che dichiara `width`×`height` pixel, con un IDAT non valido: aprirlo
    legge solo l'intestazione, decodificarlo fallirebbe (quindi un `image_too_large` prova che
    il controllo precede la decodifica)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # RGB a 8 bit
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", b"not zlib data")
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    "size",
    [
        (10_000, 6_000),  # 60 MP: oltre il tetto, sotto il controllo «bomba» di Pillow
        (20_000, 10_000),  # 200 MP: Pillow solleva già DecompressionBombError in `Image.open`
    ],
)
async def test_upload_image_too_many_pixels_rejected_before_decoding(
    storage: _Storage, monkeypatch: pytest.MonkeyPatch, size: tuple[int, int]
) -> None:
    def no_decode(self: Image.Image, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("decodifica dei pixel non attesa")

    monkeypatch.setattr(Image.Image, "load", no_decode)
    data = _png_header_only(*size)
    assert len(data) < 100
    assert size[0] * size[1] > file_service.UPLOAD_IMAGE_MAX_PIXELS
    with pytest.raises(ValidationAppError) as exc:
        await file_service.save_upload_image(_upload(data, "image/png"), subdir="lesson_assets/t")
    assert exc.value.code == "image_too_large"
    assert storage.uploads == {}


async def test_upload_image_pixel_cap_value() -> None:
    # 48 MP (foto da smartphone, 8064×6048) passano; il tetto resta sotto i ~89 MP del
    # «warning» di Pillow.
    assert 8064 * 6048 <= file_service.UPLOAD_IMAGE_MAX_PIXELS < Image.MAX_IMAGE_PIXELS


async def test_upload_image_pillow_off_loop(
    storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ImageOps.exif_transpose

    def slow_transpose(img: Image.Image) -> Image.Image:
        time.sleep(0.3)  # Pillow lento (immagine grande)
        return original(img)

    monkeypatch.setattr(file_service.ImageOps, "exif_transpose", slow_transpose)
    started = time.perf_counter()
    public, lag_ms = await _probe_while(
        file_service.save_upload_image(
            _upload(_image("PNG", (50, 40)), "image/png"), subdir="lesson_assets/test"
        ),
    )
    assert public.startswith("/uploads/lesson_assets/test/")
    assert time.perf_counter() - started >= 0.3
    assert lag_ms < 100, lag_ms
