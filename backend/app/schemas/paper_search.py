"""Schemi Pydantic per la ricerca paper scientifici.

Espone:
- `PaperSearchFilters` / `PaperSearchInput`: input dell'endpoint POST
  `/papers/search`.
- `PaperOut`: paper singolo restituito al FE (campi unificati dai 3
  source: OpenAlex per discovery, Semantic Scholar e Crossref per
  enrichment on-demand).
- `PaperSearchResultsOut`: response paginata.
- `PaperImportInput`: input per POST `/papers/import` (lista paper
  selezionati con i metadata gia' visti dal FE).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PaperType = Literal["article", "preprint", "review", "other"]


class PaperSearchFilters(BaseModel):
    """Filtri della ricerca paper. Tutti opzionali."""

    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    # None = qualsiasi, True = solo open access
    is_oa: bool | None = None
    min_citations: int | None = Field(default=None, ge=0)
    author_name: str | None = Field(default=None, max_length=200)
    venue_name: str | None = Field(default=None, max_length=200)
    work_type: PaperType | None = None


class PaperSearchInput(BaseModel):
    query: str = Field(default="", max_length=500)
    filters: PaperSearchFilters = Field(default_factory=PaperSearchFilters)
    cursor: str | None = None
    per_page: int = Field(default=20, ge=1, le=50)


class PaperOut(BaseModel):
    """Paper singolo. Campi `tldr`, `subjects`, `references_count`
    sono popolati solo via enrichment on-demand (initialmente None)."""

    id: str  # OpenAlex Work ID (full URL "https://openalex.org/W...")
    doi: str | None
    title: str
    abstract: str | None
    authors: list[str]
    year: int | None
    journal: str | None
    citations: int
    is_oa: bool
    oa_pdf_url: str | None
    doi_url: str | None  # https://doi.org/{doi} se DOI presente
    work_type: PaperType | None
    keywords: list[str]
    relevance_score: float | None
    tldr: str | None = None
    subjects: list[str] = Field(default_factory=list)
    references_count: int | None = None


class PaperSearchResultsOut(BaseModel):
    results: list[PaperOut]
    next_cursor: str | None
    total_count: int


class PaperAISummaryInput(BaseModel):
    """Body POST `/papers/ai-summary`. Il FE passa i metadata gia'
    visti dalla card; il BE puo' eventualmente eseguire enrichment
    aggiuntivo se DOI presente e abstract vuoto."""

    paper: PaperOut


class PaperImportInput(BaseModel):
    """Body POST `/papers/import`. Lista di paper selezionati dal FE."""

    papers: list[PaperOut] = Field(min_length=1, max_length=50)


class PaperImportItemResultOut(BaseModel):
    """Risultato singolo dell'import: documento creato + nota sulla
    modalita' di import (PDF reale vs metadata-only)."""

    document_id: str
    filename: str
    mode: Literal["pdf", "metadata"]  # pdf = OA scaricato, metadata = .md
    paper_id: str


class PaperImportResultOut(BaseModel):
    imported: list[PaperImportItemResultOut]
    pdf_count: int
    metadata_count: int
