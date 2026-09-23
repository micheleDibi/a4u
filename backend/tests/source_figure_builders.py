"""Builder condivisi per i test delle figure di fonte.

Stesso stile di `course_builders`: oggetti in memoria con valori minimi
validi; chi li usa decide se persisterli. Nessun default nasconde la
licenza: va passata in modo esplicito come nel codice di produzione.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from app.models.course_document_figure import CourseDocumentFigure


def build_document_figure(
    course_id: uuid.UUID,
    document_id: uuid.UUID | None,
    *,
    license: str,
    locator: str | None = None,
    source_kind: str = "uploaded",
    status: str = "ready",
    page: int | None = 1,
    storage_path: str | None = None,
    kind: str | None = "technical_drawing",
    description: str | None = "Schema a blocchi di un vibrometro laser.",
    keywords: dict[str, Any] | None = None,
    quality_score: int | None = 4,
    is_useful_for_teaching: bool | None = True,
    excluded_by_user: bool = False,
    attribution: dict[str, Any] | None = None,
    **overrides: Any,
) -> CourseDocumentFigure:
    """`CourseDocumentFigure` in memoria, pronta da aggiungere alla sessione."""
    loc = locator or f"p{page or 0:04d}-x{uuid.uuid4().hex}"
    figure = CourseDocumentFigure(
        course_id=course_id,
        document_id=document_id,
        source_kind=source_kind,
        locator=loc,
        extraction_version=1,
        engine="docling",
        page=page,
        bbox={"l": 72.0, "t": 100.0, "r": 400.0, "b": 300.0, "page_w": 595.0, "page_h": 842.0},
        source_label="Fig. 1.",
        source_caption="Schema di principio di un vibrometro laser Doppler.",
        storage_path=storage_path
        or f"/uploads/courses/{course_id}/document_figures/{document_id}/{loc}.png",
        mime_type="image/png",
        width=1200,
        height=800,
        dpi=300,
        byte_size=1024,
        is_vector=True,
        phash="0" * 16,
        kind=kind,
        description=description,
        keywords=keywords
        if keywords is not None
        else {"course": ["vibrometro", "laser"], "en": ["vibrometer", "laser"]},
        quality_score=quality_score,
        is_useful_for_teaching=is_useful_for_teaching,
        status=status,
        excluded_by_user=excluded_by_user,
        license=license,
        attribution=attribution,
    )
    for key, value in overrides.items():
        setattr(figure, key, value)
    return figure


def png_bytes(seed: int = 0, size: tuple[int, int] = (64, 48)) -> bytes:
    """PNG deterministico (Pillow) per i test di storage e di resa."""
    from PIL import Image

    image = Image.new("RGB", size, color=(seed % 256, (seed * 7) % 256, (seed * 13) % 256))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
