"""Figure caricate nelle lezioni: il formato del file resta (doc 18 §23.7, D21).

Prima un PNG RGB diventava JPEG q85: la rotazione EXIF crea un'immagine senza
formato e il ramo di ripiego sceglieva JPEG. Con `preserve_format` il PNG resta
PNG (sempre ricodificato, metadati tolti); gli altri caricamenti (loghi,
sfondi) non cambiano.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image
from starlette.datastructures import Headers, UploadFile

from app.services import file_service, remote_storage


class _Store:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_bytes(self, key: str, data: bytes) -> None:
        self.files[key] = data


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    made = _Store()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: made)
    return made


def _upload(fmt: str, mode: str = "RGB") -> UploadFile:
    buf = io.BytesIO()
    Image.new(mode, (60, 40), "white").save(buf, fmt)
    buf.seek(0)
    mime = {"PNG": "image/png", "JPEG": "image/jpeg"}[fmt]
    return UploadFile(buf, filename=f"x.{fmt.lower()}", headers=Headers({"content-type": mime}))


async def test_a_png_stays_png_with_preserve_format(store: _Store) -> None:
    path = await file_service.save_upload_image(
        _upload("PNG"), subdir="lesson_assets/x", preserve_format=True
    )
    assert path.endswith(".png")
    data = store.files[remote_storage.uploads_key(path)]
    assert Image.open(io.BytesIO(data)).format == "PNG"


async def test_other_uploads_keep_the_previous_behaviour(store: _Store) -> None:
    path = await file_service.save_upload_image(_upload("PNG"), subdir="organizations")
    assert path.endswith(".jpg")


@pytest.mark.parametrize(("fmt", "ext"), [("JPEG", ".jpg"), ("PNG", ".png")])
async def test_preserve_format_never_passes_the_bytes_through(
    store: _Store, fmt: str, ext: str
) -> None:
    upload: Any = _upload(fmt)
    original = upload.file.getvalue()
    path = await file_service.save_upload_image(
        upload, subdir="lesson_assets/x", preserve_format=True
    )
    assert path.endswith(ext)
    assert store.files[remote_storage.uploads_key(path)] != original
