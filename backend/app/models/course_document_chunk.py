from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.course_document import CourseDocument


class CourseDocumentChunk(UUIDPKMixin, TimestampMixin, Base):
    """Risultato per-chunk della pipeline di analisi a copertura totale.

    Semantica insert-only-on-success: una riga esiste ⇔ il chunk è stato
    analizzato con successo (la ripresa processa gli indici senza riga).
    Righe effimere: cancellate al successo del reduce, conservate su
    failure per riprendere dal chunk mancante. Il file su storage resta
    la fonte di verità: ri-mappare è sempre possibile.
    """

    __tablename__ = "course_document_chunk"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index",
            name="uq_course_document_chunk_index",
        ),
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_course_document_chunk_index_min",
        ),
        CheckConstraint(
            "char_end > char_start",
            name="ck_course_document_chunk_char_range",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_document.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Coordinate sul testo estratto normalizzato (strip globale).
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # Solo PDF: pagine coperte dal chunk (1-based).
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ChunkFactsOut validato (fatti già nei sotto-schemi Appendice A).
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # {prompt, completion, total, model} della chiamata map.
    tokens: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    document: Mapped[CourseDocument] = relationship("CourseDocument")
