"""Schemi delle API delle figure di fonte del corso (WP4).

La riga «Fonte» (`attribution`) è calcolata dal server
(`figure_attribution`) nella lingua del corso: il frontend la mostra così
com'è, non la ricompone mai. L'immagine non è mai un URL dello storage:
passa dall'endpoint autenticato `…/document-figures/{id}/image`.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict


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
