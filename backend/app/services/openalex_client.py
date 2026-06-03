"""Client per OpenAlex API (https://api.openalex.org).

OpenAlex e' un database libero di metadata scientifici (~250M paper).
Senza chiave API ma con `mailto:` nel User-Agent l'IP entra nel "polite
pool" (rate-limit piu' permissivo).

Funzioni esposte:
- `search_works(query, filters, cursor, per_page)`: ricerca paper con
  filtri (anno, OA, citations, autore, venue, type). Cursor-based
  pagination.
- `download_pdf(url, max_bytes)`: scarica un PDF dal `oa_url`.

Errori -> `OpenAlexError(status, message)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.openalex")


class OpenAlexError(Exception):
    def __init__(
        self, status: int | None, message: str, *, payload: Any = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.payload = payload

    def __str__(self) -> str:
        return f"[OpenAlex {self.status}] {self.message}"


@dataclass(frozen=True)
class OpenAlexWork:
    """Wrapper tipizzato (e parziale) della struttura `Work` di OpenAlex.

    Campi `raw` contiene il dict completo per fallback su attributi non
    mappati esplicitamente.
    """

    id: str  # es. "https://openalex.org/W123..."
    doi: str | None  # es. "10.1234/abc"
    title: str
    abstract: str | None
    authors: list[str]
    publication_year: int | None
    journal: str | None
    cited_by_count: int
    is_oa: bool
    oa_pdf_url: str | None
    work_type: str | None
    keywords: list[str]
    relevance_score: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class OpenAlexSearchResults:
    works: list[OpenAlexWork]
    next_cursor: str | None
    total_count: int


def _user_agent() -> str:
    settings = get_settings()
    email = (settings.papers_polite_email or "").strip()
    if email:
        return f"a4u/1.0 (mailto:{email})"
    return "a4u/1.0"


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=settings.openalex_base_url.rstrip("/"),
        headers={
            "User-Agent": _user_agent(),
            "Accept": "application/json",
        },
        timeout=timeout,
    )


def _reconstruct_abstract(
    inverted_index: dict[str, list[int]] | None,
) -> str | None:
    """OpenAlex restituisce l'abstract come "inverted index":
    `{"word": [pos1, pos2, ...]}`. Ricostruisce la stringa originale."""
    if not inverted_index or not isinstance(inverted_index, dict):
        return None
    positions: dict[int, str] = {}
    for word, indices in inverted_index.items():
        if not isinstance(indices, list):
            continue
        for pos in indices:
            if isinstance(pos, int) and pos >= 0:
                positions[pos] = word
    if not positions:
        return None
    ordered = [positions[i] for i in sorted(positions.keys())]
    return " ".join(ordered)


def _extract_doi(work: dict[str, Any]) -> str | None:
    raw = work.get("doi")
    if not raw or not isinstance(raw, str):
        return None
    # OpenAlex restituisce DOI come URL completo: "https://doi.org/10.x/y".
    prefix = "https://doi.org/"
    return raw[len(prefix):] if raw.startswith(prefix) else raw


def _extract_authors(work: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for a in (work.get("authorships") or []):
        if not isinstance(a, dict):
            continue
        author = a.get("author") or {}
        name = author.get("display_name") if isinstance(author, dict) else None
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def _extract_journal(work: dict[str, Any]) -> str | None:
    primary = work.get("primary_location") or {}
    if not isinstance(primary, dict):
        return None
    source = primary.get("source") or {}
    if isinstance(source, dict):
        name = source.get("display_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _extract_keywords(work: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in (work.get("keywords") or []):
        if isinstance(k, dict):
            name = k.get("display_name") or k.get("keyword")
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
    return out


def _extract_oa_pdf_url(work: dict[str, Any]) -> tuple[bool, str | None]:
    """Ritorna `(is_oa, pdf_url)`. URL preferenziale:
    `best_oa_location.pdf_url` > `open_access.oa_url`."""
    oa = work.get("open_access") or {}
    is_oa = bool(oa.get("is_oa")) if isinstance(oa, dict) else False
    pdf_url: str | None = None
    best = work.get("best_oa_location") or {}
    if isinstance(best, dict):
        candidate = best.get("pdf_url") or best.get("landing_page_url")
        if isinstance(candidate, str) and candidate.startswith("http"):
            pdf_url = candidate
    if pdf_url is None and isinstance(oa, dict):
        candidate = oa.get("oa_url")
        if isinstance(candidate, str) and candidate.startswith("http"):
            pdf_url = candidate
    return is_oa, pdf_url


def _to_work(raw: dict[str, Any]) -> OpenAlexWork:
    is_oa, pdf_url = _extract_oa_pdf_url(raw)
    return OpenAlexWork(
        id=str(raw.get("id") or ""),
        doi=_extract_doi(raw),
        title=str(raw.get("title") or raw.get("display_name") or ""),
        abstract=_reconstruct_abstract(raw.get("abstract_inverted_index")),
        authors=_extract_authors(raw),
        publication_year=(
            raw.get("publication_year")
            if isinstance(raw.get("publication_year"), int)
            else None
        ),
        journal=_extract_journal(raw),
        cited_by_count=int(raw.get("cited_by_count") or 0),
        is_oa=is_oa,
        oa_pdf_url=pdf_url,
        work_type=raw.get("type") if isinstance(raw.get("type"), str) else None,
        keywords=_extract_keywords(raw),
        relevance_score=(
            float(raw["relevance_score"])
            if isinstance(raw.get("relevance_score"), (int, float))
            else None
        ),
        raw=raw,
    )


def _build_filter_string(
    *,
    year_from: int | None,
    year_to: int | None,
    is_oa: bool | None,
    min_citations: int | None,
    author_name: str | None,
    venue_name: str | None,
    work_type: str | None,
) -> str | None:
    """Compone la stringa `filter=...` per OpenAlex. Ritorna None se
    nessun filtro e' specificato."""
    parts: list[str] = []
    if year_from is not None:
        parts.append(f"publication_year:>{year_from - 1}")
    if year_to is not None:
        parts.append(f"publication_year:<{year_to + 1}")
    if is_oa is True:
        parts.append("is_oa:true")
    if min_citations is not None and min_citations > 0:
        parts.append(f"cited_by_count:>{min_citations - 1}")
    if author_name:
        # `authorships.author.display_name.search` accetta full-text.
        # Escape virgole (separatore filtri).
        parts.append(
            f"authorships.author.display_name.search:{author_name.replace(',', ' ')}"
        )
    if venue_name:
        parts.append(
            f"primary_location.source.display_name.search:{venue_name.replace(',', ' ')}"
        )
    if work_type:
        # OpenAlex types: article | preprint | review | dataset | ...
        parts.append(f"type:{work_type}")
    return ",".join(parts) if parts else None


async def search_works(
    *,
    query: str,
    year_from: int | None = None,
    year_to: int | None = None,
    is_oa: bool | None = None,
    min_citations: int | None = None,
    author_name: str | None = None,
    venue_name: str | None = None,
    work_type: str | None = None,
    cursor: str | None = None,
    per_page: int = 20,
) -> OpenAlexSearchResults:
    """Cerca paper su OpenAlex. Cursor-based pagination."""
    params: dict[str, Any] = {
        "per-page": max(1, min(per_page, 50)),
        "cursor": cursor or "*",
    }
    if query and query.strip():
        params["search"] = query.strip()
    filt = _build_filter_string(
        year_from=year_from,
        year_to=year_to,
        is_oa=is_oa,
        min_citations=min_citations,
        author_name=author_name,
        venue_name=venue_name,
        work_type=work_type,
    )
    if filt:
        params["filter"] = filt

    log.info(
        "openalex_search_request",
        query=(query or "")[:100],
        filter=filt,
        per_page=params["per-page"],
    )
    try:
        async with _client(timeout=30.0) as client:
            resp = await client.get("/works", params=params)
    except httpx.HTTPError as exc:
        log.error("openalex_http_error", error=str(exc))
        raise OpenAlexError(
            status=None, message=f"Errore HTTP verso OpenAlex: {exc}"
        ) from exc

    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text[:500]}
        msg = (
            payload.get("error")
            if isinstance(payload, dict)
            else None
        )
        raise OpenAlexError(
            status=resp.status_code,
            message=msg or f"OpenAlex ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    meta = data.get("meta") or {}
    results_raw = data.get("results") or []
    works = [_to_work(w) for w in results_raw if isinstance(w, dict)]
    next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
    total = int(meta.get("count") or 0) if isinstance(meta, dict) else 0
    log.info(
        "openalex_search_response",
        count=len(works),
        total=total,
        has_next=bool(next_cursor),
    )
    return OpenAlexSearchResults(
        works=works,
        next_cursor=next_cursor if isinstance(next_cursor, str) else None,
        total_count=total,
    )


async def download_pdf(
    url: str,
    *,
    max_bytes: int,
) -> bytes:
    """Scarica un PDF (binario) dall'URL specificato.

    Solleva `OpenAlexError(status=None)` su HTTP error, timeout o
    `OpenAlexError(status=413)` se la dimensione supera `max_bytes`.
    """
    if not url or not url.startswith("http"):
        raise OpenAlexError(
            status=None, message=f"URL PDF non valido: {url!r}"
        )
    try:
        async with httpx.AsyncClient(
            timeout=120.0,
            follow_redirects=True,
            headers={"User-Agent": _user_agent()},
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        log.warning("openalex_pdf_download_failed", url=url, error=str(exc))
        raise OpenAlexError(
            status=None, message=f"Download PDF fallito: {exc}"
        ) from exc

    if resp.status_code >= 400:
        log.warning(
            "openalex_pdf_download_http_error",
            url=url,
            status=resp.status_code,
        )
        raise OpenAlexError(
            status=resp.status_code,
            message=f"Download PDF HTTP {resp.status_code}.",
        )
    payload = resp.content
    if len(payload) > max_bytes:
        raise OpenAlexError(
            status=413,
            message=(
                f"PDF troppo grande: {len(payload)} byte > "
                f"limite {max_bytes}."
            ),
        )
    # Molti URL "oa_pdf" puntano a landing page / paywall che rispondono
    # 200 con HTML invece del PDF. Senza questo controllo l'HTML verrebbe
    # salvato come .pdf e poi fallirebbe l'estrazione testo ("No /Root
    # object! - Is this really a PDF?"). Verifichiamo l'header %PDF (la
    # spec ne tollera la presenza entro i primi ~1024 byte) cosi' l'import
    # puo' ripiegare sui metadati .md.
    if b"%PDF-" not in payload[:1024]:
        ctype = resp.headers.get("content-type", "?")
        log.warning(
            "openalex_pdf_not_a_pdf",
            url=url,
            content_type=ctype,
            size=len(payload),
            head=payload[:16].hex(),
        )
        raise OpenAlexError(
            status=None,
            message=(
                f"Il contenuto scaricato non e' un PDF "
                f"(content-type={ctype})."
            ),
        )
    return payload
