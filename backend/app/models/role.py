from __future__ import annotations

from sqlalchemy import SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPKMixin


class OrganizationRole(UUIDPKMixin, Base):
    __tablename__ = "organization_roles"

    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name_it: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=100)
