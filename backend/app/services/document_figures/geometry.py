"""Rettangoli in punti PDF con origine in alto a sinistra (come pdfplumber)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BBox:
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def area(self) -> float:
        return self.width * self.height

    def union(self, other: BBox) -> BBox:
        return BBox(
            min(self.x0, other.x0),
            min(self.top, other.top),
            max(self.x1, other.x1),
            max(self.bottom, other.bottom),
        )

    def intersection(self, other: BBox) -> BBox | None:
        box = BBox(
            max(self.x0, other.x0),
            max(self.top, other.top),
            min(self.x1, other.x1),
            min(self.bottom, other.bottom),
        )
        return box if box.x1 > box.x0 and box.bottom > box.top else None

    def intersection_area(self, other: BBox) -> float:
        box = self.intersection(other)
        return box.area if box is not None else 0.0

    def iou(self, other: BBox) -> float:
        inter = self.intersection_area(other)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def coverage_of(self, other: BBox) -> float:
        """Quota di `other` contenuta in questo rettangolo."""
        return self.intersection_area(other) / other.area if other.area > 0 else 0.0

    def expand(self, margin: float) -> BBox:
        return BBox(self.x0 - margin, self.top - margin, self.x1 + margin, self.bottom + margin)

    def clamp(self, page_w: float, page_h: float) -> BBox:
        return BBox(
            max(0.0, self.x0), max(0.0, self.top), min(page_w, self.x1), min(page_h, self.bottom)
        )

    def gap_to(self, other: BBox) -> float:
        """Distanza minima fra i bordi (0 se si toccano o si sovrappongono)."""
        dx = max(0.0, max(self.x0, other.x0) - min(self.x1, other.x1))
        dy = max(0.0, max(self.top, other.top) - min(self.bottom, other.bottom))
        return max(dx, dy)

    def as_json(self, page_w: float, page_h: float) -> dict[str, float]:
        return {
            "l": round(self.x0, 2),
            "t": round(self.top, 2),
            "r": round(self.x1, 2),
            "b": round(self.bottom, 2),
            "page_w": round(page_w, 2),
            "page_h": round(page_h, 2),
        }
