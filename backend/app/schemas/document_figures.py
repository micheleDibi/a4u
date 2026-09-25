"""Schemi delle API delle figure di fonte del corso (WP4).

La riga «Fonte» (`attribution`) è calcolata dal server
(`figure_attribution`) nella lingua del corso: il frontend la mostra così
com'è, non la ricompone mai. L'immagine non è mai un URL dello storage:
passa dall'endpoint autenticato `…/document-figures/{id}/image`.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FigureResolutionOut(BaseModel):
    """Risoluzione effettiva (doc 18 §22), calcolata con le stesse funzioni
    del render (`source_figure_resolution`) alla colonna di riferimento."""

    model_config = ConfigDict(populate_by_name=True)

    # good | acceptable | low | unusable (classe di selezione e di stampa).
    resolution_class: str = Field(serialization_alias="class")
    # good | acceptable | low nelle slide (solo informazione e spareggio).
    slide_class: str | None = None
    # Base della misura naturale: measured, bbox, render, convention, estimate.
    basis: str
    print_width_mm: float
    print_ppi: int


class DocumentFigureOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID | None
    document_filename: str | None = None
    source_kind: str
    page: int | None
    locator: str
    kind: str | None
    description: str | None
    keywords: dict[str, Any] | None
    source_caption: str | None
    source_label: str | None
    # Didascalia proposta dal selettore: quella originale senza la coda di
    # fonte (`clean_caption`, come nella fusione) o la descrizione, entro
    # i 600 caratteri della didascalia di un asset.
    suggested_caption: str = ""
    width: int | None
    height: int | None
    mime_type: str | None
    quality_score: int | None
    is_useful_for_teaching: bool | None
    excluded_by_user: bool
    status: str
    license: str
    detached: bool
    # Riga «Fonte» scritta, nella lingua del corso ("" se non attribuibile).
    attribution: str
    # Resa di una figura già collocata (modo render: non retroattivo, U1).
    renderable: bool
    # Proponibile ora (modo select con la politica di licenza effettiva).
    selectable: bool
    # Motivo di `selectable = False` (predicato, qualità, tipo).
    reason: str | None = None
    # Revisione dell'immagine: cambia quando cambiano i byte (ri-ritaglio v2
    # o ripristino), così il frontend non mostra quella in cache. Hash del
    # percorso, mai il percorso (U5).
    image_rev: str = ""
    # None con FIGURE_RESOLUTION_RULES_ENABLED=false o senza pixel.
    resolution: FigureResolutionOut | None = None


class DocumentFigureUpdate(BaseModel):
    """PATCH di una figura: il docente la esclude (o la riammette) dalle
    proposte. Non retroattivo (U1): le lezioni già generate non cambiano."""

    model_config = ConfigDict(extra="forbid")

    excluded_by_user: bool


class FigureUsageLesson(BaseModel):
    lesson_id: uuid.UUID
    lesson_code: str
    title: str
    asset_ids: list[str]


class DocumentFigureUsageOut(BaseModel):
    """Uso delle figure di un documento nelle lezioni del corso: il dialogo
    di cancellazione o di cambio di politica lo mostra (le figure usate
    restano dove sono, U1)."""

    document_id: uuid.UUID
    figures_total: int
    figures_used: int
    lessons: list[FigureUsageLesson]
