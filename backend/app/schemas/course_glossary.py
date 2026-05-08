"""Schemi Pydantic per il glossario del corso (§10.1).

Generato una sola volta da Fase 1 + documenti, riusato come
`{{glossario}}` nei prompt successivi (Fasi 2, 3, 5). Single-shot, non
serializzato come worker — chiamata sync.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Output AI (§10.1) — validato dopo la chiamata OpenAI
# ---------------------------------------------------------------------------


class GlossaryTermSchema(BaseModel):
    """Singolo termine del glossario."""

    model_config = ConfigDict(extra="forbid")
    term: str = Field(min_length=1, max_length=200)
    translation: str = Field(default="", max_length=300)
    usage_note: str = Field(min_length=1, max_length=600)


class GlossaryOutput(BaseModel):
    """Output completo dell'AI per il glossario di un corso.

    Il system prompt chiede 10-30 termini come linea guida (§10.1), ma
    il modello può sforare leggermente in casi di domini ricchi → cap
    Pydantic a 50 per evitare reject inutili. Il floor a 5 esclude
    glossari quasi-vuoti (genuinamente sbagliati).
    """

    model_config = ConfigDict(extra="forbid")
    course_id: str
    terms: list[GlossaryTermSchema] = Field(min_length=5, max_length=50)


# ---------------------------------------------------------------------------
# Input dal frontend
# ---------------------------------------------------------------------------


class GlossaryRegenerateInput(BaseModel):
    """Body opzionale per `POST /glossary/regenerate`. Vuoto in MVP."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Output verso il frontend (DTO read-only)
# ---------------------------------------------------------------------------


class GlossaryTermOut(ORMModel):
    term: str
    translation: str = ""
    usage_note: str


class GlossaryOut(ORMModel):
    """Sub-DTO esposto in `CourseOut.glossary` (parsa `glossary_raw`)."""

    status: str
    terms: list[GlossaryTermOut] = Field(default_factory=list)
    generated_at: datetime | None = None
    error: str | None = None
    tokens: dict[str, Any] | None = None
