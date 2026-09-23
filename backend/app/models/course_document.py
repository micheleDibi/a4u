from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    from app.models.course_document_figure import CourseDocumentFigure


# Valori ammessi delle colonne di provenienza e di estrazione figure
# (migrazione 0037; la migrazione li riporta come letterali e un test ne
# verifica la parità).
DOCUMENT_ORIGINS: tuple[str, ...] = ("upload", "paper_import", "paper_metadata")
BIBLIOGRAPHY_SOURCES: tuple[str, ...] = (
    "user",
    "openalex",
    "pdf_metadata",
    "crossref",
    "summary_proposal",
)
DOCUMENT_LICENSE_SOURCES: tuple[str, ...] = ("user", "openalex")
FIGURES_STATUSES: tuple[str, ...] = (
    "pending",
    "processing",
    "ready",
    "failed",
    "skipped",
)
FIGURES_ERROR_CODES: tuple[str, ...] = (
    # skipped
    "policy_excluded",
    "policy_content_only",
    "unsupported_format",
    "extraction_disabled",
    # failed
    "source_missing",
    "encrypted",
    "corrupt",
    "engine_unavailable",
    "timeout",
    "crashed",
    "oom",
    "crashed_repeatedly",
    "resources_unavailable",
    "vision_unavailable",
    "storage_error",
    "attempts_exhausted",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ",".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


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
        CheckConstraint(
            _in_list("origin", DOCUMENT_ORIGINS),
            name="ck_course_document_origin_valid",
        ),
        CheckConstraint(
            # Stessi valori di `course_document_figure.license` tranne
            # 'unknown': qui la licenza sconosciuta è NULL.
            "license IS NULL OR license IN ('cc0','public_domain','cc_by',"
            "'cc_by_sa','cc_by_nc','cc_by_nd','cc_by_nc_sa','cc_by_nc_nd',"
            "'all_rights_reserved','other')",
            name="ck_course_document_license_valid",
        ),
        CheckConstraint(
            "license_source IS NULL OR "
            + _in_list("license_source", DOCUMENT_LICENSE_SOURCES),
            name="ck_course_document_license_source_valid",
        ),
        CheckConstraint(
            "bibliography_source IS NULL OR "
            + _in_list("bibliography_source", BIBLIOGRAPHY_SOURCES),
            name="ck_course_document_bibliography_source_valid",
        ),
        CheckConstraint(
            "figures_status IS NULL OR "
            + _in_list("figures_status", FIGURES_STATUSES),
            name="ck_course_document_figures_status_valid",
        ),
        CheckConstraint(
            "figures_error_code IS NULL OR "
            + _in_list("figures_error_code", FIGURES_ERROR_CODES),
            name="ck_course_document_figures_error_code_valid",
        ),
        CheckConstraint(
            "figures_coverage IS NULL OR figures_coverage IN ('full','partial')",
            name="ck_course_document_figures_coverage_valid",
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

    # --- Provenienza (migrazione 0037) ------------------------------------
    # Origine del documento: caricato dal docente o importato dalla ricerca
    # paper (PDF open access oppure .md di soli metadati).
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="upload", server_default="upload"
    )
    # Documento dichiarato dal docente come opera propria: con la politica
    # open_only le sue figure sono riproducibili come quelle a licenza
    # aperta.
    is_own_work: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Licenza del documento (NULL = sconosciuta) e sua provenienza.
    license: Mapped[str | None] = mapped_column(String(30), nullable=True)
    license_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Metadati bibliografici per la riga di attribuzione (DocumentBibliography:
    # titolo, autori, anno, editore o rivista, DOI, URL, id OpenAlex). Mai
    # scritti dal modello: `summary_proposal` è solo una proposta da
    # confermare e non entra nella riga «Fonte».
    bibliography: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    bibliography_source: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    # --- Estrazione delle figure (migrazione 0037) -----------------------
    # Stato separato dal riassunto: un errore qui non tocca `summary_*` e
    # viceversa. NULL = estrazione mai richiesta (nessun backfill).
    figures_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    figures_error_code: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    figures_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    figures_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    figures_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    figures_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    figures_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    figures_coverage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    figures_engine: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # sha256(file ‖ versione di estrazione ‖ motore ‖ parametri di ritaglio).
    figures_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    figures_pages_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    figures_pages_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Solo visualizzazione: {stage, candidates_total, candidates_done, ...}.
    figures_progress: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Statistiche dell'ultima estrazione (tempi, pagine, motore): niente
    # costi, che stanno sulle righe delle figure (`vision_usage`).
    figures_stats: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    course: Mapped[Course] = relationship("Course", back_populates="documents")
    figures: Mapped[list[CourseDocumentFigure]] = relationship(
        "CourseDocumentFigure",
        back_populates="document",
        passive_deletes=True,
    )
