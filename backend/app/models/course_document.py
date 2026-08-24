from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.course import Course


class CourseDocument(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "course_document"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_course_document_size_bytes_min"),
        CheckConstraint(
            "summary_status IN ('pending','processing','ready','failed')",
            name="ck_course_document_summary_status_valid",
        ),
        CheckConstraint(
            "summary_coverage IN ('full','partial')",
            name="ck_course_document_summary_coverage_valid",
        ),
        CheckConstraint(
            "citation_policy IN ('citable','content_only','excluded')",
            name="ck_course_document_citation_policy_valid",
        ),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename_original: Mapped[str] = mapped_column(String(300), nullable=False)
    filename_stored: Mapped[str] = mapped_column(
        String(300), nullable=False, unique=True
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Politica di citazione (Blocco 2 "visibilità delle fonti"):
    # - 'citable'      = comportamento storico: il documento può comparire
    #                    come fonte (bibliografia, references, filename);
    # - 'content_only' = "Fonte riservata": il contenuto viene usato per
    #                    generare, l'origine non viene MAI citata (niente
    #                    filename/titolo/autori nei prompt, filtri
    #                    post-generazione sulla bibliografia);
    # - 'excluded'     = il riassunto non entra in alcun prompt (il worker
    #                    lo analizza comunque: riattivazione istantanea).
    citation_policy: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="citable",
        server_default="citable",
    )

    # Output dell'Appendice A (riassunto strutturato).
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", server_default="pending"
    )
    summary_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Metadati del worker di pre-processing (Appendice A).
    text_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    text_chars_extracted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_tokens: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )

    # Copertura dell'analisi (migrazione 0035): 'full' = intero testo
    # analizzato (single-shot sotto soglia o pipeline chunked); 'partial'
    # = superato l'hard cap di sicurezza (analizzato il prefisso, MAI in
    # silenzio: visibile in FE e audit); NULL = riassunto legacy
    # precedente alla feature (nessun backfill).
    summary_coverage: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    # Progresso denormalizzato della pipeline chunked per la UI
    # (il polling della detail corso li fa arrivare al FE gratis).
    summary_chunks_total: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    summary_chunks_done: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Fingerprint del run chunked: sha256(bytes file ‖ parametri chunking
    # ‖ modello ‖ PROMPT_VERSION). Se cambia, i chunk persistiti vengono
    # scartati (mai risultati misti tra run con parametri diversi).
    summary_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    course: Mapped[Course] = relationship("Course", back_populates="documents")
