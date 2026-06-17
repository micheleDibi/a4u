"""Wrapper minimo sull'I/O file (org/avatar/template).

Astrazione sottile: il DB salva un percorso logico opaco (`/uploads/...`) e il
resto dell'app passa solo da queste funzioni per scrivere/leggere/cancellare.

L'I/O effettivo è delegato a :mod:`app.services.remote_storage`, che instrada
verso il backend attivo (filesystem locale o OVH via FTP) in base a
``settings.storage_backend``. Qui restano solo la whitelist dei subdir e la
validazione del filename.
"""
from __future__ import annotations

from app.core.errors import ValidationAppError
from app.core.logging import get_logger
from app.services import remote_storage

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


def save_bytes(*, subdir: str, filename: str, data: bytes) -> str:
    """Scrive `data` su `{subdir}/{filename}` (storage attivo) e ritorna il
    path pubblico relativo `/uploads/{subdir}/{filename}`."""
    safe_subdir = _validate_subdir(subdir)
    if "/" in filename or filename in {".", ".."} or not filename:
        raise ValidationAppError("Filename non consentito.", code="invalid_filename")
    public_path = f"/uploads/{safe_subdir}/{filename}"
    remote_storage.get_storage().upload_bytes(
        remote_storage.uploads_key(public_path), data
    )
    log.info("storage_save", subdir=safe_subdir, filename=filename, size=len(data))
    return public_path


def read_bytes(path: str) -> bytes:
    if not path.startswith("/uploads/"):
        raise ValidationAppError("Path non consentito.", code="invalid_path")
    try:
        return remote_storage.get_storage().download_bytes(
            remote_storage.uploads_key(path)
        )
    except remote_storage.StorageFileNotFound as exc:
        raise ValidationAppError("File non trovato.", code="file_not_found") from exc


def delete(path: str | None) -> None:
    if not path or not path.startswith("/uploads/"):
        return
    try:
        remote_storage.get_storage().delete(remote_storage.uploads_key(path))
        log.info("storage_delete", path=path)
    except remote_storage.StorageError as exc:  # pragma: no cover - best effort
        log.warning("storage_delete_failed", path=path, error=str(exc))


def delete_directory(subdir: str) -> None:
    """Cancella ricorsivamente una sottodirectory di upload (e tutto il suo
    contenuto). Idempotente."""
    try:
        safe_subdir = _validate_subdir(subdir)
    except ValidationAppError:
        return
    try:
        remote_storage.get_storage().delete_prefix(
            remote_storage.uploads_key(f"/uploads/{safe_subdir}")
        )
        log.info("storage_delete_dir", subdir=safe_subdir)
    except remote_storage.StorageError as exc:  # pragma: no cover - best effort
        log.warning("storage_delete_dir_failed", subdir=safe_subdir, error=str(exc))


def public_url(path: str) -> str:
    """URL pubblico raggiungibile dall'esterno (per MiniMax/RunPod)."""
    return remote_storage.public_url(remote_storage.uploads_key(path))
