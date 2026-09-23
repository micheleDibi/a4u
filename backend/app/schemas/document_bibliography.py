"""Metadati bibliografici di un documento del corso (colonna `bibliography`).

Servono alla riga di attribuzione delle figure di fonte, calcolata a render
e mai scritta dal modello. La provenienza dei dati sta in
`course_document.bibliography_source`: `user` (il docente), `openalex`
(import dalla ricerca paper), `pdf_metadata` (Info/XMP del PDF, core.xml di
DOCX/PPTX), `crossref` (DOI risolto), `summary_proposal` (proposta dal
riassunto dell'LLM, da confermare: non entra nella riga «Fonte»).
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# Fonti ammesse nella riga di attribuzione (tutte tranne la proposta LLM).
TRUSTED_BIBLIOGRAPHY_SOURCES: frozenset[str] = frozenset(
    {"user", "openalex", "pdf_metadata", "crossref"}
)

_Author = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class DocumentBibliography(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=500)
    authors: list[_Author] = Field(default_factory=list, max_length=50)
    year: int | None = Field(default=None, ge=1400, le=2100)
    # Rivista, atti o collana; `publisher` per l'editore di un libro.
    container: str | None = Field(default=None, max_length=300)
    publisher: str | None = Field(default=None, max_length=300)
    doi: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=1000)
    openalex_id: str | None = Field(default=None, max_length=100)

    @field_validator("title", "container", "publisher", "doi", "url", "openalex_id", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def as_json(self) -> dict[str, Any]:
        """Dict da salvare in JSONB, senza chiavi vuote."""
        return self.model_dump(exclude_none=True, exclude_defaults=False)
