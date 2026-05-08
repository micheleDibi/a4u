from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AvatarVoiceScript(TimestampMixin, Base):
    """Testo da leggere ad alta voce durante la registrazione audio dell'avatar.

    L'utente seleziona la sua lingua nell'editor avatar e legge questo
    testo: avere un campione audio standardizzato (foneticamente vario, di
    durata sufficiente) è il prerequisito per addestrare un modello di
    voice cloning.

    PK = `language_code` (FK → languages.code), una sola riga per lingua.
    """

    __tablename__ = "avatar_voice_scripts"

    language_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("languages.code", ondelete="CASCADE"),
        primary_key=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
