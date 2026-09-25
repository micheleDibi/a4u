from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    REAL,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.course_document import CourseDocument


# Valori ammessi (fonte unica per i CHECK del modello; la migrazione 0037
# li riporta come letterali e un test ne verifica la parità).

# Provenienza della figura: estratta da un documento del corso, oppure
# presa dalla letteratura aperta (WP5) senza alcun documento del corso.
FIGURE_SOURCE_KINDS: tuple[str, ...] = ("uploaded", "openalex", "wikimedia")

# Licenza della figura. Mai NULL: 'unknown' quando non è nota.
FIGURE_LICENSES: tuple[str, ...] = (
    "cc0",
    "public_domain",
    "cc_by",
    "cc_by_sa",
    "cc_by_nc",
    "cc_by_nd",
    "cc_by_nc_sa",
    "cc_by_nc_nd",
    "all_rights_reserved",
    "other",
    "unknown",
)

# Da dove viene la licenza registrata sulla figura.
FIGURE_LICENSE_SOURCES: tuple[str, ...] = (
    "document",
    "openalex",
    "wikimedia",
    "user",
)

# Ciclo di vita della riga: 'extracted' = ritaglio salvato, descrizione
# Vision non ancora fatta; 'ready' = descritta e utilizzabile (l'idoneità
# didattica si calcola in lettura); 'rejected' = scartata dai filtri
# deterministici o superata da una nuova estrazione; 'failed' = errore
# definitivo sulla singola figura.
FIGURE_STATUSES: tuple[str, ...] = ("extracted", "ready", "rejected", "failed")

FIGURE_REJECT_REASONS: tuple[str, ...] = (
    "too_small",
    "bad_aspect",
    "blank",
    "header_footer",
    "detector_noise",
    "repeated",
    "duplicate",
    "unsupported_image_format",
    "too_large",
    "describe_capped",
    "superseded",
)

FIGURE_MIME_TYPES: tuple[str, ...] = ("image/png", "image/jpeg")

# Come è stato fatto il ritaglio (migrazione 0039; NULL per i ritagli v1):
# 'raster_native' = raster di un PDF reso sulla sua griglia di pixel;
# 'mixed' = raster con tratti vettoriali sopra, reso a k volte il nativo;
# 'vector' = solo tratti vettoriali; 'office' = immagine incorporata in un
# DOCX o PPTX; 'external' = file della letteratura non ritagliato da un PDF
# (Wikimedia Commons).
FIGURE_CROP_MODES: tuple[str, ...] = ("raster_native", "mixed", "vector", "office", "external")


# Chiavi che identificano la fonte in un'attribuzione congelata
# (`figure_attribution.AttributionSource.to_json` omette quelle vuote).
FIGURE_ATTRIBUTION_IDENTIFYING_KEYS: tuple[str, ...] = (
    "title",
    "authors",
    "credit",
    "fallback_name",
    "is_own_work",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ",".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


_ATTRIBUTION_PRESENT = (
    "attribution IS NOT NULL AND jsonb_typeof(attribution) = 'object' "
    "AND attribution ?| array["
    + ",".join(f"'{k}'" for k in FIGURE_ATTRIBUTION_IDENTIFYING_KEYS)
    + "]"
)


class CourseDocumentFigure(UUIDPKMixin, TimestampMixin, Base):
    """Figura estratta da una fonte (documento del corso o letteratura).

    Catalogo per corso delle figure di fonte: la lezione le cita con un
    asset `format="source_figure"` il cui `content` è l'id di questa riga
    (riferimento vivo). Byte e riga di attribuzione si risolvono sempre
    lato server da qui, mai dal JSON della lezione.

    Cancellazione del documento (decisione U1, non retroattiva): le figure
    ancora usate da qualche lezione vengono *staccate* (`document_id` →
    NULL, `detached_at` valorizzato, attribuzione congelata in
    `attribution`); quelle non usate vengono cancellate dal servizio.
    """

    __tablename__ = "course_document_figure"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "locator",
            name="uq_course_document_figure_locator",
        ),
        CheckConstraint(
            _in_list("source_kind", FIGURE_SOURCE_KINDS),
            name="ck_course_document_figure_source_kind_valid",
        ),
        CheckConstraint(
            "source_kind <> 'uploaded' OR document_id IS NOT NULL OR detached_at IS NOT NULL",
            name="ck_course_document_figure_uploaded_has_document",
        ),
        # `jsonb_typeof` distingue un oggetto dal JSON `null`; la parte
        # `IS NOT NULL` serve perché un CHECK con esito NULL passerebbe; `?|`
        # esige almeno un dato che identifichi la fonte (`{}` non basta).
        CheckConstraint(
            "source_kind = 'uploaded' OR (" + _ATTRIBUTION_PRESENT + ")",
            name="ck_course_document_figure_external_has_attribution",
        ),
        CheckConstraint(
            "detached_at IS NULL OR (" + _ATTRIBUTION_PRESENT + ")",
            name="ck_course_document_figure_detached_has_attribution",
        ),
        CheckConstraint(
            _in_list("license", FIGURE_LICENSES),
            name="ck_course_document_figure_license_valid",
        ),
        CheckConstraint(
            "license_source IS NULL OR " + _in_list("license_source", FIGURE_LICENSE_SOURCES),
            name="ck_course_document_figure_license_source_valid",
        ),
        CheckConstraint(
            _in_list("status", FIGURE_STATUSES),
            name="ck_course_document_figure_status_valid",
        ),
        CheckConstraint(
            "reject_reason IS NULL OR " + _in_list("reject_reason", FIGURE_REJECT_REASONS),
            name="ck_course_document_figure_reject_reason_valid",
        ),
        CheckConstraint(
            "mime_type IS NULL OR " + _in_list("mime_type", FIGURE_MIME_TYPES),
            name="ck_course_document_figure_mime_type_valid",
        ),
        CheckConstraint(
            "status <> 'ready' OR (storage_path IS NOT NULL AND storage_path <> '')",
            name="ck_course_document_figure_ready_has_file",
        ),
        CheckConstraint(
            "page IS NULL OR page >= 1",
            name="ck_course_document_figure_page_min",
        ),
        CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_course_document_figure_size_positive",
        ),
        CheckConstraint(
            "quality_score IS NULL OR quality_score BETWEEN 1 AND 5",
            name="ck_course_document_figure_quality_score_range",
        ),
        # Ingressi della risoluzione effettiva e ri-ritaglio (migrazione 0039).
        CheckConstraint(
            "native_ppi IS NULL OR native_ppi > 0",
            name="ck_course_document_figure_native_ppi_positive",
        ),
        CheckConstraint(
            "natural_width_mm IS NULL OR natural_width_mm > 0",
            name="ck_course_document_figure_natural_width_positive",
        ),
        CheckConstraint(
            "crop_mode IS NULL OR " + _in_list("crop_mode", FIGURE_CROP_MODES),
            name="ck_course_document_figure_crop_mode_valid",
        ),
        CheckConstraint(
            "crop_version >= 1",
            name="ck_course_document_figure_crop_version_min",
        ),
        Index(
            "ix_course_document_figure_course_status",
            "course_id",
            "status",
        ),
        Index(
            "ix_course_document_figure_course_phash",
            "course_id",
            "phash",
        ),
        # La stessa figura esterna (letteratura aperta, WP5) una volta per
        # corso; le figure staccate non hanno `external_id` (migrazione 0038).
        Index(
            "uq_course_document_figure_external",
            "course_id",
            "source_kind",
            "external_id",
            unique=True,
            postgresql_where=text("document_id IS NULL AND external_id IS NOT NULL"),
        ),
    )

    # --- Riferimenti ------------------------------------------------------
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SET NULL (non CASCADE): le figure usate sopravvivono alla
    # cancellazione del documento come figure staccate; le non usate le
    # cancella esplicitamente il servizio.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_document.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    detached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)

    # --- Posizione nella fonte ed estrazione -----------------------------
    # Chiave stabile per l'upsert idempotente (es. `p0012-x031-y045`,
    # `docx-i0007`, `s004-17`): la stessa figura ri-estratta conserva l'id.
    locator: Mapped[str] = mapped_column(String(60), nullable=False)
    extraction_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    engine: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Pagina FISICA (1-based; per PPTX il numero di slide; NULL per DOCX).
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {l, t, r, b, page_w, page_h} in punti, origine in alto a sinistra.
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    source_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- File -------------------------------------------------------------
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preview_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dpi: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_vector: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    phash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detector_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detector_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Risoluzione effettiva (migrazione 0039) --------------------------
    # Solo gli ingressi: la classe (good/acceptable/low/unusable) si calcola
    # in lettura (`source_figure_resolution`). `dpi` resta il dpi del
    # RENDER; `native_ppi` è il ppi del raster dominante nelle unità della
    # pagina della fonte (NULL = vettoriale o ignoto); `natural_width_mm` è
    # la misura nell'originale normalizzata alla pagina (min(1, 612/page_w)).
    native_ppi: Mapped[float | None] = mapped_column(REAL, nullable=True)
    natural_width_mm: Mapped[float | None] = mapped_column(REAL, nullable=True)
    crop_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 1 = ritaglio storico (150-300 dpi, JPEG per i raster); 2 = griglia
    # nativa e PNG per il tratto (`document_figures.CROP_VERSION`).
    crop_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    # Ri-ritaglio sul posto (stesso UUID, V2): percorsi e metadati del
    # ritaglio precedente, per `rerender_document_figures --revert`.
    recropped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recrop_previous: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # --- Analisi Vision ---------------------------------------------------
    kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {"course": [...], "en": [...]}
    keywords: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    quality_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    is_useful_for_teaching: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    legibility: Mapped[str | None] = mapped_column(String(20), nullable=True)
    described_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    describe_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Descrizione riusata da una figura quasi identica (phash) dello stesso
    # corso: nessuna seconda chiamata Vision.
    describe_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_document_figure.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Usage cumulativo delle chiamate AI su questa figura (build_usage_dict
    # + `calls`); `vision_usage_at` data l'ultima chiamata (finestre della
    # dashboard admin).
    vision_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    vision_usage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Stato ------------------------------------------------------------
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_document_figure.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Esclusione dal catalogo decisa dal docente (non retroattiva sulle
    # lezioni che la usano già). Mai toccata dall'upsert di estrazione.
    excluded_by_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # --- Diritti e attribuzione ------------------------------------------
    # Mai NULL e senza server_default: ogni percorso di creazione deve
    # valorizzarla in modo esplicito ('unknown' se non nota).
    license: Mapped[str] = mapped_column(String(30), nullable=False)
    license_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    license_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Attribuzione esplicita: TASL delle fonti esterne (WP5) oppure
    # attribuzione congelata di una figura staccata dal suo documento.
    attribution: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )

    # --- Letteratura aperta (WP5, migrazione 0038) ------------------------
    # Identità nella fonte esterna (`commons:<pageid>`, `<W…>#<locator>`),
    # pagina della fonte e data del recupero. NULL per le figure dei
    # documenti del corso.
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[CourseDocument | None] = relationship(
        "CourseDocument", back_populates="figures"
    )
