"""Percorsi e I/O dei ritagli delle figure di fonte nello storage.

I ritagli stanno sotto `/uploads` come i documenti del corso (decisione U5;
in produzione backend `ovh_sftp`, docroot pubblica: stessa esposizione per
URL dei PDF sorgente, nomi non indovinabili e mai esposti dal frontend,
che passa dall'endpoint autenticato). Percorsi costruiti solo da UUID e da
nomi validati: niente traversal, e nessun helper può toccare cartelle fuori
da `courses/{id}/document_figures/`. `storage_service` non è esteso a
`courses` di proposito: il suo `delete_directory("courses")` cancellerebbe
tutti i corsi.
"""

from __future__ import annotations

import re
import uuid

from app.services import remote_storage

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,90}\.(png|jpg)$")
EXTERNAL_OWNER = "external"


class FigureStoragePathError(ValueError):
    """Nome o percorso di un ritaglio non valido."""


def course_prefix(course_id: uuid.UUID) -> str:
    return f"/uploads/courses/{uuid.UUID(str(course_id))}/document_figures"


def owner_prefix(course_id: uuid.UUID, document_id: uuid.UUID | None) -> str:
    owner = str(uuid.UUID(str(document_id))) if document_id is not None else EXTERNAL_OWNER
    return f"{course_prefix(course_id)}/{owner}"


def figure_path(course_id: uuid.UUID, document_id: uuid.UUID | None, name: str) -> str:
    if not _NAME_RE.match(name):
        raise FigureStoragePathError(f"nome di ritaglio non valido: {name!r}")
    return f"{owner_prefix(course_id, document_id)}/{name}"


def belongs_to_course(path: str | None, course_id: uuid.UUID) -> bool:
    """True se `path` è un ritaglio del corso (controllo anti-IDOR)."""
    if not path:
        return False
    prefix = course_prefix(course_id) + "/"
    if not path.startswith(prefix):
        return False
    rest = path[len(prefix) :].split("/")
    return len(rest) == 2 and bool(_NAME_RE.match(rest[1])) and ".." not in rest


def upload(path: str, data: bytes) -> None:
    remote_storage.get_storage().upload_bytes(remote_storage.uploads_key(path), data)


def read(path: str) -> bytes:
    return remote_storage.get_storage().download_bytes(remote_storage.uploads_key(path))


def delete(path: str | None) -> None:
    if path:
        remote_storage.get_storage().delete(remote_storage.uploads_key(path))


def delete_owner(course_id: uuid.UUID, document_id: uuid.UUID | None) -> None:
    remote_storage.get_storage().delete_prefix(
        remote_storage.uploads_key(owner_prefix(course_id, document_id))
    )


def delete_course(course_id: uuid.UUID) -> None:
    remote_storage.get_storage().delete_prefix(remote_storage.uploads_key(course_prefix(course_id)))
