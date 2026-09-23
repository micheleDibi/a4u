"""Figura rilevata su una pagina, prima di ritaglio e filtri."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.document_figures.geometry import BBox


@dataclass(frozen=True)
class Detection:
    page: int
    bbox: BBox
    page_w: float
    page_h: float
    is_vector: bool
    detector_class: str
    confidence: float | None = None
    caption: str | None = None
    # ppi nativo del raster (None per i vettoriali o se ignoto).
    native_ppi: float | None = None
