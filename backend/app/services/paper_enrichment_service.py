"""Service di arricchimento dei metadata di un paper.

Riceve un `OpenAlexWork` (dalla search primaria) e — se ha DOI — esegue
in parallelo lookup su Semantic Scholar e Crossref. Merge graceful:
per ogni campo, usa OpenAlex se presente, altrimenti il secondo
source, altrimenti il terzo. Aggiunge campi extra (`tldr`,
`references_count`, `subjects`).

Funzione: `enrich_paper(openalex_work) -> EnrichedPaper`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.logging import get_logger
from app.services.crossref_client import CrossrefWork, get_work_by_doi as crossref_lookup
from app.services.openalex_client import OpenAlexWork
from app.services.semantic_scholar_client import (
    SemanticScholarPaper,
    get_paper_by_doi as s2_lookup,
)

log = get_logger("app.paper_enrichment")


@dataclass(frozen=True)
class EnrichedPaper:
    """Paper con tutti i campi merged dai 3 source."""

    # Da OpenAlex (sorgente primaria, sempre presente)
    id: str
    doi: str | None
    title: str
    authors: list[str]
    year: int | None
    journal: str | None
    citations: int
    is_oa: bool
    oa_pdf_url: str | None  # priorita': OpenAlex > S2 fallback
    work_type: str | None
    keywords: list[str]
    relevance_score: float | None
    # Abstract: priorita' OpenAlex > Crossref > S2
    abstract: str | None
    # Extra dai source secondari
    tldr: str | None  # da Semantic Scholar
    subjects: list[str]  # da Crossref
    references_count: int | None  # da Crossref


def _merge_abstract(
    openalex_abstract: str | None,
    s2: SemanticScholarPaper | None,
    cr: CrossrefWork | None,
) -> str | None:
    """Priorita' OpenAlex > Crossref > Semantic Scholar.

    OpenAlex restituisce abstract ricostruiti da inverted index (talvolta
    spezzati). Crossref ha quelli "ufficiali" dei publisher quando
    disponibili. Semantic Scholar e' il fallback.
    """
    if openalex_abstract and len(openalex_abstract) >= 40:
        return openalex_abstract
    if cr and cr.abstract and len(cr.abstract) >= 40:
        return cr.abstract
    if s2 and s2.abstract and len(s2.abstract) >= 40:
        return s2.abstract
    # Anche se corti, ritorniamo qualcosa se disponibile
    return openalex_abstract or (cr.abstract if cr else None) or (
        s2.abstract if s2 else None
    )


def _merge_oa_pdf_url(
    openalex_url: str | None, s2: SemanticScholarPaper | None
) -> str | None:
    if openalex_url:
        return openalex_url
    if s2 and s2.open_access_pdf_url:
        return s2.open_access_pdf_url
    return None


async def enrich_paper(work: OpenAlexWork) -> EnrichedPaper:
    """Arricchisce un paper OpenAlex con dati da Semantic Scholar +
    Crossref (chiamate in parallelo). Se il paper non ha DOI, salta
    l'enrichment e ritorna i soli dati OpenAlex."""
    s2_data: SemanticScholarPaper | None = None
    cr_data: CrossrefWork | None = None
    if work.doi:
        results = await asyncio.gather(
            s2_lookup(work.doi),
            crossref_lookup(work.doi),
            return_exceptions=True,
        )
        s2_raw, cr_raw = results
        if isinstance(s2_raw, SemanticScholarPaper):
            s2_data = s2_raw
        elif isinstance(s2_raw, BaseException):
            log.warning(
                "paper_enrichment_s2_failed", doi=work.doi, error=str(s2_raw)
            )
        if isinstance(cr_raw, CrossrefWork):
            cr_data = cr_raw
        elif isinstance(cr_raw, BaseException):
            log.warning(
                "paper_enrichment_crossref_failed",
                doi=work.doi,
                error=str(cr_raw),
            )

    return EnrichedPaper(
        id=work.id,
        doi=work.doi,
        title=work.title,
        authors=work.authors,
        year=work.publication_year,
        journal=work.journal,
        citations=work.cited_by_count,
        is_oa=work.is_oa,
        oa_pdf_url=_merge_oa_pdf_url(work.oa_pdf_url, s2_data),
        work_type=work.work_type,
        keywords=work.keywords,
        relevance_score=work.relevance_score,
        abstract=_merge_abstract(work.abstract, s2_data, cr_data),
        tldr=s2_data.tldr if s2_data else None,
        subjects=cr_data.subjects if cr_data else [],
        references_count=cr_data.references_count if cr_data else None,
    )
