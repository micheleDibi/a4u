"""Predicato unico di visibilità delle figure di fonte (puro, senza I/O).

Due modi, perché il cambio di politica non è retroattivo (decisione U1,
docs/courses/18-literature-figures.md):

- ``select``: vale per il catalogo del prompt di Fase 3, per il PATCH di un
  asset nuovo o cambiato e per il ricontrollo di fine generazione. Applica
  politica di citazione del documento (gli esclusi non si propongono; le
  fonti riservate sì, come materiale del docente), esclusione del docente
  e politica di licenza.
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
from datetime import datetime
from typing import Any, Literal, Protocol, get_args

from app.services.figure_attribution import attribution_source

VisibilityMode = Literal["select", "render"]
LicensePolicy = Literal["cite_all", "open_only"]

# Licenze riproducibili con la politica `open_only` (oltre ai documenti
# dichiarati propri dal docente).
OPEN_LICENSES: frozenset[str] = frozenset({"cc0", "public_domain", "cc_by", "cc_by_sa"})

# Motivi di non visibilità, in ordine di valutazione.
REASONS: tuple[str, ...] = (
    "not_found",
    "wrong_course",
    "document_mismatch",
    "not_ready",
    "excluded_by_user",
    "superseded",
    "document_excluded",
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
    reject_reason: str | None
    license: str
    detached_at: datetime | None
    attribution: dict[str, Any] | None
    storage_path: str | None
    page: int | None
    source_label: str | None


class DocumentLike(Protocol):
    id: uuid.UUID
    citation_policy: str
    is_own_work: bool
    filename_original: str
    mime_type: str
    origin: str
    bibliography: dict[str, Any] | None
    bibliography_source: str | None


@dataclass(frozen=True)
class Visibility:
    renderable: bool
    reason: str | None = None


VISIBLE = Visibility(True, None)


def effective_license_policy(org_policy: str | None, settings_policy: str) -> LicensePolicy:
    """Politica effettiva: l'override dell'organizzazione se presente,
    altrimenti il setting globale. Se nessuna delle due è valida (il DB ha
    un CHECK e Settings un Literal) vale la più restrittiva, `open_only`."""
    for candidate in (org_policy, settings_policy):
        if candidate == "open_only":
            return "open_only"
        if candidate == "cite_all":
            return "cite_all"
    return "open_only"


def is_open_license(license: str | None, *, is_own_work: bool) -> bool:
    """True se la figura è riproducibile con `open_only`."""
    return is_own_work or (license in OPEN_LICENSES)


def document_license_to_figure(document_license: str | None) -> str:
    """Licenza della figura dedotta dal documento: sul documento la licenza
    sconosciuta è NULL, sulla figura è sempre valorizzata ('unknown')."""
    return document_license or "unknown"


def _is_own_work(fig: FigureLike, doc: DocumentLike | None) -> bool:
    if doc is not None:
        # Una fonte riservata si assume materiale del docente.
        return bool(doc.is_own_work) or doc.citation_policy == "content_only"
    if fig.source_kind != "uploaded":
        # La letteratura aperta non è mai materiale proprio del docente.
        return False
    attribution = fig.attribution or {}
    return attribution.get("is_own_work") is True


def figure_visibility(
    fig: FigureLike | None,
    doc: DocumentLike | None,
    *,
    course_id: uuid.UUID,
    license_policy: str,
    mode: VisibilityMode,
    file_present: bool = True,
) -> Visibility:
    """Decide se la figura si può mostrare (render) o proporre (select).

    Un modo o una politica fuori dominio sono un errore del chiamante
    (ValueError), mai un ripiego permissivo.
    """
    if mode not in get_args(VisibilityMode):
        raise ValueError(f"modo di visibilità sconosciuto: {mode!r}")
    if license_policy not in get_args(LicensePolicy):
        raise ValueError(f"politica di licenza sconosciuta: {license_policy!r}")
    if fig is None:
        return Visibility(False, "not_found")
    if fig.course_id != course_id:
        return Visibility(False, "wrong_course")
    if doc is not None and doc.id != fig.document_id:
        return Visibility(False, "document_mismatch")
    if fig.status != "ready":
        return Visibility(False, "not_ready")
    if mode == "select":
        if fig.excluded_by_user:
            return Visibility(False, "excluded_by_user")
        if fig.reject_reason is not None:
            # Riga di un'estrazione precedente (`superseded`): resta dove è
            # già collocata (U1), non si propone più.
            return Visibility(False, "superseded")
        if fig.source_kind == "uploaded":
            if doc is None:
                # Figura staccata da un documento cancellato: resta dove è
                # già collocata, non si propone più.
                return Visibility(False, "document_excluded")
            if doc.citation_policy == "excluded":
                return Visibility(False, "document_excluded")
        if license_policy == "open_only" and not is_open_license(
            fig.license, is_own_work=_is_own_work(fig, doc)
        ):
            return Visibility(False, "license_not_open")
    if attribution_source(fig, doc) is None:
        return Visibility(False, "attribution_missing")
    if not fig.storage_path or not file_present:
        return Visibility(False, "file_missing")
    return VISIBLE
