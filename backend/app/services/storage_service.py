"""Wrapper minimo sull'I/O file su disco.

Astrazione sottile sopra il filesystem locale: il DB salva un percorso logico
opaco (`/uploads/...`) e il resto dell'app passa solo da queste funzioni per
scrivere/leggere/cancellare.

Quando passeremo a S3, basterà sostituire l'implementazione di queste funzioni
mantenendo la stessa firma; lo schema DB e i call site non cambiano.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.core.errors import ValidationAppError
from app.core.logging import get_logger

log = get_logger("app.storage")

_ALLOWED_ROOTS = {"organizations", "avatars", "templates"}


def _validate_subdir(subdir: str) -> str:
    parts = [p for p in subdir.replace("\\", "/").split("/") if p]
    if not parts or parts[0] not in _ALLOWED_ROOTS:
        raise ValidationAppError("Subdir non consentita.", code="invalid_subdir")
    for p in parts:
        if p in {".", ".."}:
            raise ValidationAppError("Subdir non consentita.", code="invalid_subdir")
    return "/".join(parts)


def _ensure_within(root: Path, target: Path) -> None:
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationAppError("Percorso file non valido.", code="invalid_path") from exc


def save_bytes(*, subdir: str, filename: str, data: bytes) -> str:
    """Scrive `data` in `{upload_root}/{subdir}/{filename}` e ritorna il path
    pubblico relativo `/uploads/{subdir}/{filename}`."""
    settings = get_settings()
    safe_subdir = _validate_subdir(subdir)
    if "/" in filename or filename in {".", ".."} or not filename:
        raise ValidationAppError("Filename non consentito.", code="invalid_filename")
    target_dir = settings.upload_root / safe_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    _ensure_within(settings.upload_root, target_path)
    target_path.write_bytes(data)
    log.info("storage_save", subdir=safe_subdir, filename=filename, size=len(data))
    return f"/uploads/{safe_subdir}/{filename}"


def read_bytes(path: str) -> bytes:
    settings = get_settings()
    if not path.startswith("/uploads/"):
        raise ValidationAppError("Path non consentito.", code="invalid_path")
    rel = path.removeprefix("/uploads/")
    target = settings.upload_root / rel
    _ensure_within(settings.upload_root, target)
    if not target.exists():
        raise ValidationAppError("File non trovato.", code="file_not_found")
    return target.read_bytes()


def delete(path: str | None) -> None:
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
            log.info("storage_delete", path=str(target))
        except OSError as exc:  # pragma: no cover
            log.warning("storage_delete_failed", path=str(target), error=str(exc))


def delete_directory(subdir: str) -> None:
    """Cancella ricorsivamente una sottodirectory di upload (e tutto il suo
    contenuto). Idempotente."""
    settings = get_settings()
    try:
        safe_subdir = _validate_subdir(subdir)
    except ValidationAppError:
        return
    target = settings.upload_root / safe_subdir
    try:
        _ensure_within(settings.upload_root, target)
    except ValidationAppError:
        return
    if not target.exists() or not target.is_dir():
        return
    for child in sorted(target.rglob("*"), key=lambda p: -len(p.parts)):
        try:
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        except OSError as exc:  # pragma: no cover
            log.warning("storage_delete_child_failed", path=str(child), error=str(exc))
    try:
        target.rmdir()
        log.info("storage_delete_dir", path=str(target))
    except OSError:  # pragma: no cover
        pass


def public_url(path: str) -> str:
    """Costruisce l'URL pubblico raggiungibile dall'esterno (per MiniMax)."""
    settings = get_settings()
    base = settings.public_base_url.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"
