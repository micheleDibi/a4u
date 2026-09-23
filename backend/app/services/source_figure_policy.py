"""Predicato unico di visibilità delle figure di fonte (puro, senza I/O).

Due modi, perché il cambio di politica non è retroattivo (decisione U1,
docs/courses/18-literature-figures.md):

- ``select``: vale per il catalogo del prompt di Fase 3, per il PATCH di un
  asset nuovo o cambiato e per il ricontrollo di fine generazione. Applica
  politica di citazione del documento, esclusione del docente e politica
  di licenza.
- ``render``: vale per le figure già collocate in una lezione. Verifica solo
  le condizioni strutturali (figura del corso, pronta, attribuibile, file
  presente): una figura non si ritira perché il documento è diventato
  riservato o perché l'organizzazione è passata a ``open_only``; esce alla
  rigenerazione della lezione.

Il chiamante carica figura e documento (``doc`` è None per le figure
staccate e per quelle della letteratura aperta) e passa la politica di
licenza effettiva (:func:`effective_license_policy`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

VisibilityMode = Literal["select", "render"]
LicensePolicy = Literal["cite_all", "open_only"]

# Licenze riproducibili con la politica `open_only` (oltre ai documenti
# dichiarati propri dal docente).
OPEN_LICENSES: frozenset[str] = frozenset({"cc0", "public_domain", "cc_by", "cc_by_sa"})

# Motivi di non visibilità, in ordine di valutazione.
REASONS: tuple[str, ...] = (
    "not_found",
    "wrong_course",
    "not_ready",
    "excluded_by_user",
    "document_excluded",
    "document_content_only",
    "license_not_open",
    "attribution_missing",
    "file_missing",
)


class FigureLike(Protocol):
    course_id: uuid.UUID
    document_id: uuid.UUID | None
    status: str
    source_kind: str
    excluded_by_user: bool
    license: str
    detached_at: object
    attribution: dict[str, object] | None
    storage_path: str | None


class DocumentLike(Protocol):
    citation_policy: str
    is_own_work: bool


@dataclass(frozen=True)
class Visibility:
    renderable: bool
    reason: str | None = None


VISIBLE = Visibility(True, None)


def effective_license_policy(org_policy: str | None, settings_policy: str) -> LicensePolicy:
    """Politica effettiva: l'override dell'organizzazione se presente,
    altrimenti il setting globale. Un valore fuori dominio vale `cite_all`
    solo se nessuna delle due fonti è valida (il DB ha un CHECK)."""
    for candidate in (org_policy, settings_policy):
        if candidate == "open_only":
            return "open_only"
        if candidate == "cite_all":
            return "cite_all"
    return "cite_all"


def is_open_license(license: str | None, *, is_own_work: bool) -> bool:
    """True se la figura è riproducibile con `open_only`."""
    return is_own_work or (license in OPEN_LICENSES)


def document_license_to_figure(document_license: str | None) -> str:
    """Licenza della figura dedotta dal documento: sul documento la licenza
    sconosciuta è NULL, sulla figura è sempre valorizzata ('unknown')."""
    return document_license or "unknown"


def _is_own_work(fig: FigureLike, doc: DocumentLike | None) -> bool:
    if doc is not None:
        return bool(doc.is_own_work)
    attribution = fig.attribution or {}
    return bool(attribution.get("is_own_work"))


def _has_attribution(fig: FigureLike, doc: DocumentLike | None) -> bool:
    if fig.source_kind == "uploaded" and doc is not None:
        # Un documento del corso ha sempre almeno il nome del file.
        return True
    attribution = fig.attribution
    return isinstance(attribution, dict) and bool(attribution)


def figure_visibility(
    fig: FigureLike | None,
    doc: DocumentLike | None,
    *,
    course_id: uuid.UUID,
    license_policy: str,
    mode: VisibilityMode,
    file_present: bool = True,
) -> Visibility:
    """Decide se la figura si può mostrare (render) o proporre (select)."""
    if fig is None:
        return Visibility(False, "not_found")
    if fig.course_id != course_id:
        return Visibility(False, "wrong_course")
    if fig.status != "ready":
        return Visibility(False, "not_ready")
    if mode == "select":
        if fig.excluded_by_user:
            return Visibility(False, "excluded_by_user")
        if fig.source_kind == "uploaded":
            if doc is None:
                # Figura staccata da un documento cancellato: resta dove è
                # già collocata, non si propone più.
                return Visibility(False, "document_excluded")
            if doc.citation_policy == "excluded":
                return Visibility(False, "document_excluded")
            if doc.citation_policy == "content_only":
                return Visibility(False, "document_content_only")
        if license_policy == "open_only" and not is_open_license(
            fig.license, is_own_work=_is_own_work(fig, doc)
        ):
            return Visibility(False, "license_not_open")
    if not _has_attribution(fig, doc):
        return Visibility(False, "attribution_missing")
    if not fig.storage_path or not file_present:
        return Visibility(False, "file_missing")
    return VISIBLE
