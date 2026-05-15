from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.core.errors import ValidationAppError
from app.core.logging import get_logger

log = get_logger("app.files")

ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
ALLOWED_IMAGE_EXT_BY_FORMAT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}

# MIME accettati per audio: copertura per upload tipici e per i file emessi
# da MediaRecorder (Chromium → audio/webm; Safari → audio/mp4).
ALLOWED_AUDIO_MIME_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/aac",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/x-m4a",
}
ALLOWED_AUDIO_EXT_BY_MIME = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-m4a": ".m4a",
}

# Whitelist dei subdir consentiti sotto upload_root. Path-like accettati,
# es. "avatars", "avatars/{user_id}", "avatars/{user_id}/clips".
_ALLOWED_SUBDIR_ROOTS = {
    "organizations",
    "avatars",
    "templates",
    "courses",
    "lesson_assets",
    "lesson_videos",
}

# Documenti di riferimento dei corsi: tipi accettati per il pre-processing
# di Appendice A (riassunto strutturato). Il file viene salvato as-is, no
# trascodifica.
ALLOWED_DOCUMENT_MIME_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}


def _validate_subdir(subdir: str) -> str:
    parts = [p for p in subdir.replace("\\", "/").split("/") if p]
    if not parts or parts[0] not in _ALLOWED_SUBDIR_ROOTS:
        raise ValidationAppError("Subdir non consentita.", code="invalid_subdir")
    for p in parts:
        if p in {".", ".."} or "/" in p:
            raise ValidationAppError("Subdir non consentita.", code="invalid_subdir")
    return "/".join(parts)


def _ensure_within(root: Path, target: Path) -> None:
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationAppError("Percorso file non valido.", code="invalid_path") from exc


async def save_upload_image(
    upload: UploadFile,
    *,
    subdir: str,
    max_dimension: int = 4096,
    filename_stem: str | None = None,
) -> str:
    """Salva un upload immagine validato, ri-encoded da Pillow per strippare metadata.

    Ritorna il percorso pubblico relativo (es. `/uploads/organizations/<uuid>.jpg`).
    `filename_stem` permette di forzare il nome (senza estensione) per pattern
    deterministici come `image` (l'estensione è scelta in base al formato).
    """
    settings = get_settings()
    safe_subdir = _validate_subdir(subdir)
    if upload.content_type and upload.content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValidationAppError(
            f"Tipo file non consentito: {upload.content_type}", code="invalid_mime"
        )

    raw = await upload.read()
    if len(raw) == 0:
        raise ValidationAppError("File vuoto.", code="empty_file")
    max_bytes = settings.upload_max_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise ValidationAppError(
            f"File troppo grande (max {settings.upload_max_mb}MB).", code="file_too_large"
        )

    try:
        with Image.open(BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            fmt = (img.format or "").upper()
            if fmt not in ALLOWED_IMAGE_EXT_BY_FORMAT:
                fmt = "PNG" if img.mode in ("RGBA", "LA") else "JPEG"
            ext = ALLOWED_IMAGE_EXT_BY_FORMAT[fmt]
            if max(img.size) > max_dimension:
                img.thumbnail((max_dimension, max_dimension))
            buffer = BytesIO()
            save_kwargs: dict = {"optimize": True}
            if fmt == "JPEG":
                if img.mode != "RGB":
                    img = img.convert("RGB")
                save_kwargs["quality"] = 85
            img.save(buffer, format=fmt, **save_kwargs)
            payload = buffer.getvalue()
    except UnidentifiedImageError as exc:
        raise ValidationAppError(
            "Il file non è un'immagine valida.", code="invalid_image"
        ) from exc

    target_dir = settings.upload_root / safe_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = filename_stem or uuid.uuid4().hex
    filename = f"{stem}{ext}"
    target_path = target_dir / filename
    _ensure_within(settings.upload_root, target_path)
    target_path.write_bytes(payload)
    log.info("file_saved_image", subdir=safe_subdir, filename=filename, size=len(payload))
    return f"/uploads/{safe_subdir}/{filename}"


async def save_upload_audio(
    upload: UploadFile,
    *,
    subdir: str,
    filename_stem: str | None = None,
) -> str:
    """Salva un upload audio (no transcoding). Ritorna il path pubblico."""
    settings = get_settings()
    safe_subdir = _validate_subdir(subdir)
    mime = (upload.content_type or "").lower()
    if mime not in ALLOWED_AUDIO_MIME_TYPES:
        raise ValidationAppError(
            f"Tipo audio non consentito: {mime or 'sconosciuto'}", code="invalid_audio_mime"
        )
    raw = await upload.read()
    if len(raw) == 0:
        raise ValidationAppError("File vuoto.", code="empty_file")
    max_bytes = settings.avatar_audio_max_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise ValidationAppError(
            f"Audio troppo grande (max {settings.avatar_audio_max_mb}MB).",
            code="audio_too_large",
        )

    ext = ALLOWED_AUDIO_EXT_BY_MIME[mime]
    target_dir = settings.upload_root / safe_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = filename_stem or uuid.uuid4().hex
    filename = f"{stem}{ext}"
    target_path = target_dir / filename
    _ensure_within(settings.upload_root, target_path)
    target_path.write_bytes(raw)
    log.info("file_saved_audio", subdir=safe_subdir, filename=filename, size=len(raw))
    return f"/uploads/{safe_subdir}/{filename}"


async def save_upload_document(
    upload: UploadFile,
    *,
    subdir: str,
    filename_stem: str | None = None,
) -> tuple[str, str, int]:
    """Salva un upload documento (PDF/DOCX/TXT/MD/RTF) senza trascodifica.

    Ritorna `(public_path, stored_filename, size_bytes)`.
    """
    settings = get_settings()
    safe_subdir = _validate_subdir(subdir)
    mime = (upload.content_type or "").lower()
    if mime not in ALLOWED_DOCUMENT_MIME_TYPES:
        raise ValidationAppError(
            f"Tipo documento non consentito: {mime or 'sconosciuto'}",
            code="invalid_document_mime",
        )
    raw = await upload.read()
    if len(raw) == 0:
        raise ValidationAppError("File vuoto.", code="empty_file")
    max_bytes = settings.course_document_max_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise ValidationAppError(
            f"Documento troppo grande (max {settings.course_document_max_mb}MB).",
            code="document_too_large",
        )

    ext = ALLOWED_DOCUMENT_MIME_TYPES[mime]
    target_dir = settings.upload_root / safe_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = filename_stem or uuid.uuid4().hex
    filename = f"{stem}{ext}"
    target_path = target_dir / filename
    _ensure_within(settings.upload_root, target_path)
    target_path.write_bytes(raw)
    log.info(
        "file_saved_document",
        subdir=safe_subdir,
        filename=filename,
        mime=mime,
        size=len(raw),
    )
    return f"/uploads/{safe_subdir}/{filename}", filename, len(raw)


async def delete_upload(path: str | None) -> None:
    if not path:
        return
    settings = get_settings()
    if not path.startswith("/uploads/"):
        return
    rel = path.removeprefix("/uploads/")
    target = settings.upload_root / rel
    try:
        _ensure_within(settings.upload_root, target)
    except ValidationAppError:
        return
    if target.exists():
        try:
            target.unlink()
            log.info("file_deleted", path=str(target))
        except OSError as exc:  # pragma: no cover
            log.warning("file_delete_failed", path=str(target), error=str(exc))
