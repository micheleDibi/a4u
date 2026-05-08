from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # `eager_defaults=True` fa sì che SQLAlchemy aggiunga RETURNING agli
    # INSERT/UPDATE per recuperare immediatamente i valori generati lato
    # server (server_default, onupdate=func.now(), ecc.). Senza questo
    # flag, attributi come `updated_at` restano "expired" dopo flush e il
    # lazy-load successivo in contesto sync (es. Pydantic.model_validate)
    # genera "MissingGreenlet" in async context.
    # L'uso di `@declared_attr.directive` garantisce che ogni mapper
    # concreto erediti questa configurazione.
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, object]:  # noqa: N805
        return {"eager_defaults": True}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPKMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
