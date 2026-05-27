"""Client per Semantic Scholar Graph API (https://api.semanticscholar.org).

Usato come secondary source per arricchire i risultati di OpenAlex.
Particolarmente utile per:
- `tldr.text`: riassunto AI auto-generato (~1-3 frasi) molto utile
  da mostrare in UI prima del riassunto completo a richiesta.
- `openAccessPdf.url`: fallback OA quando OpenAlex non ha il PDF.
- `abstract`: abstract pulito, talvolta piu' completo del ricostruito
  da OpenAlex (inverted index).

Errori NON bloccanti: la funzione `get_paper_by_doi` ritorna `None`
su HTTP error / timeout / not found, cosi' l'enrichment puo' procedere
senza saltare il flusso principale.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.semantic_scholar")


@dataclass(frozen=True)
class SemanticScholarPaper:
    paper_id: str | None  # S2 ID, es. "1a2b3c..."
    title: str | None
    abstract: str | None
    tldr: str | None  # tldr.text
    open_access_pdf_url: str | None
    citation_count: int | None
    year: int | None


# Campi richiesti via `?fields=...`. Riduce il payload e i tempi di
# risposta dell'API. Documentazione:
# https://api.semanticscholar.org/api-docs/graph
_FIELDS = ",".join(
    [
        "paperId",
        "title",
        "abstract",
        "tldr",
        "openAccessPdf",
        "citationCount",
        "year",
    ]
)


def _user_agent() -> str:
    email = (get_settings().papers_polite_email or "").strip()
    if email:
        return f"a4u/1.0 (mailto:{email})"
    return "a4u/1.0"


async def get_paper_by_doi(doi: str) -> SemanticScholarPaper | None:
    """Lookup paper su Semantic Scholar via DOI.

    Ritorna `None` su 404, errore HTTP, timeout. Logga warning ma non
    propaga eccezioni: l'enrichment deve essere graceful.
    """
    if not doi or not isinstance(doi, str):
        return None
    doi = doi.strip()
    if not doi:
        return None
    base_url = get_settings().semantic_scholar_base_url.rstrip("/")
    url = f"{base_url}/graph/v1/paper/DOI:{doi}"
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={
                "User-Agent": _user_agent(),
                "Accept": "application/json",
            },
        ) as client:
            resp = await client.get(url, params={"fields": _FIELDS})
    except httpx.HTTPError as exc:
        log.warning(
            "semantic_scholar_http_error", doi=doi, error=str(exc)
        )
        return None

    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        log.warning(
            "semantic_scholar_api_error",
            doi=doi,
            status=resp.status_code,
            body=resp.text[:200],
        )
        return None

    try:
        data = resp.json()
    except Exception:
        log.warning("semantic_scholar_invalid_json", doi=doi)
        return None
    if not isinstance(data, dict):
        return None

    tldr = data.get("tldr") or {}
    tldr_text = tldr.get("text") if isinstance(tldr, dict) else None
    oa_pdf = data.get("openAccessPdf") or {}
    oa_url = oa_pdf.get("url") if isinstance(oa_pdf, dict) else None

    return SemanticScholarPaper(
        paper_id=data.get("paperId") if isinstance(data.get("paperId"), str) else None,
        title=data.get("title") if isinstance(data.get("title"), str) else None,
        abstract=data.get("abstract") if isinstance(data.get("abstract"), str) else None,
        tldr=tldr_text if isinstance(tldr_text, str) else None,
        open_access_pdf_url=oa_url if isinstance(oa_url, str) else None,
        citation_count=(
            int(data["citationCount"])
            if isinstance(data.get("citationCount"), int)
            else None
        ),
        year=int(data["year"]) if isinstance(data.get("year"), int) else None,
    )
