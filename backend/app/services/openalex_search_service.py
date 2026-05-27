"""Wrapper di alto livello attorno a `openalex_client.search_works`.

Mappa `OpenAlexWork` -> `PaperOut` (schema esposto al FE). Applica
clamping del relevance_score in [0, 1] e calcola `doi_url`.

NON esegue enrichment (rimane on-demand per evitare 40 chiamate
secondarie per ricerca).
"""
from __future__ import annotations

from typing import Literal, cast

from app.core.logging import get_logger
from app.schemas.paper_search import (
    PaperOut,
    PaperSearchFilters,
    PaperSearchResultsOut,
    PaperType,
)
from app.services.openalex_client import (
    OpenAlexError,
    OpenAlexWork,
    search_works as _openalex_search,
)

log = get_logger("app.openalex_search")


_ALLOWED_TYPES: set[str] = {"article", "preprint", "review", "other"}


def _normalize_type(raw: str | None) -> PaperType | None:
    """Mappa il `type` di OpenAlex a uno dei valori previsti dal nostro
    schema. OpenAlex usa tipi piu' granulari (es. `journal-article`,
    `posted-content`, ecc.): collassiamo nei 4 valori del FE."""
    if not raw or not isinstance(raw, str):
        return None
    norm = raw.strip().lower()
    # Mapping euristico
    if "article" in norm or norm == "journal-article":
        return cast(PaperType, "article")
    if "preprint" in norm or norm == "posted-content":
        return cast(PaperType, "preprint")
    if "review" in norm:
        return cast(PaperType, "review")
    return cast(PaperType, "other")


def _doi_url(doi: str | None) -> str | None:
    if not doi:
        return None
    return f"https://doi.org/{doi}"


def _clamp_relevance(score: float | None) -> float | None:
    if score is None:
        return None
    if score < 0:
        return 0.0
    # OpenAlex relevance_score puo' essere > 1 (tipicamente in range
    # 0-10 per query specifiche). Normalizziamo grossolanamente in
    # [0, 1] con sigmoide-like compressa: score / (score + k) con k=5.
    # Per score 5 -> 0.5; per score 20 -> 0.8; per score 1 -> 0.17.
    k = 5.0
    return float(score) / (float(score) + k)


def _to_paper_out(work: OpenAlexWork) -> PaperOut:
    return PaperOut(
        id=work.id,
        doi=work.doi,
        title=work.title or "(senza titolo)",
        abstract=work.abstract,
        authors=work.authors,
        year=work.publication_year,
        journal=work.journal,
        citations=work.cited_by_count,
        is_oa=work.is_oa,
        oa_pdf_url=work.oa_pdf_url,
        doi_url=_doi_url(work.doi),
        work_type=_normalize_type(work.work_type),
        keywords=work.keywords,
        relevance_score=_clamp_relevance(work.relevance_score),
        tldr=None,
        subjects=[],
        references_count=None,
    )


async def search_papers(
    *,
    query: str,
    filters: PaperSearchFilters,
    cursor: str | None,
    per_page: int = 20,
) -> PaperSearchResultsOut:
    """Esegue la ricerca su OpenAlex + mapping schema."""
    raw_type: str | None = None
    if filters.work_type and filters.work_type in _ALLOWED_TYPES:
        # Mapping inverso: nostro `article` -> OpenAlex `article`
        # (OpenAlex accetta sia "article" che "journal-article"). Per
        # `preprint` -> "posted-content". Per `review` -> "review".
        # "other" non passiamo filtro (nessun match utile).
        if filters.work_type == "preprint":
            raw_type = "posted-content"
        elif filters.work_type == "review":
            raw_type = "review"
        elif filters.work_type == "article":
            raw_type = "article"
        else:
            raw_type = None

    results = await _openalex_search(
        query=query,
        year_from=filters.year_from,
        year_to=filters.year_to,
        is_oa=filters.is_oa,
        min_citations=filters.min_citations,
        author_name=filters.author_name,
        venue_name=filters.venue_name,
        work_type=raw_type,
        cursor=cursor,
        per_page=per_page,
    )
    out = [_to_paper_out(w) for w in results.works]
    return PaperSearchResultsOut(
        results=out,
        next_cursor=results.next_cursor,
        total_count=results.total_count,
    )


__all__ = ["search_papers", "OpenAlexError"]
