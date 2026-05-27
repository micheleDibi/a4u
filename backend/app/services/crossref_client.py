"""Client per Crossref REST API (https://api.crossref.org).

Usato come secondary source per arricchire i metadata di OpenAlex.
Particolarmente utile per:
- `abstract`: abstract pulito (JATS XML embedded) — Crossref e' spesso
  la fonte primaria dei publisher.
- `subject[]`: categorie tematiche (campo `subject` o `category-name`
  dentro le references).
- `references_count`: numero di riferimenti bibliografici.

Errori NON bloccanti: la funzione `get_work_by_doi` ritorna `None`
su HTTP error / timeout / not found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.crossref")


@dataclass(frozen=True)
class CrossrefWork:
    doi: str | None
    title: str | None
    abstract: str | None  # ripulito da tag JATS
    references_count: int | None
    subjects: list[str]
    published_year: int | None


_JATS_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_abstract(raw: str | None) -> str | None:
    """Rimuove i tag JATS XML (es. `<jats:p>...</jats:p>`) dall'abstract
    Crossref e normalizza gli spazi bianchi."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = _JATS_TAG_RE.sub(" ", raw)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned or None


def _user_agent() -> str:
    email = (get_settings().papers_polite_email or "").strip()
    if email:
        return f"a4u/1.0 (mailto:{email})"
    return "a4u/1.0"


def _extract_title(message: dict[str, Any]) -> str | None:
    titles = message.get("title") or []
    if isinstance(titles, list) and titles:
        first = titles[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return None


def _extract_published_year(message: dict[str, Any]) -> int | None:
    # `published-print.date-parts` o `published-online.date-parts`:
    # `[[year, month, day], ...]`.
    for key in ("published-print", "published-online", "issued"):
        node = message.get(key) or {}
        if not isinstance(node, dict):
            continue
        parts = node.get("date-parts") or []
        if isinstance(parts, list) and parts:
            first = parts[0]
            if isinstance(first, list) and first:
                year = first[0]
                if isinstance(year, int):
                    return year
    return None


def _extract_subjects(message: dict[str, Any]) -> list[str]:
    out: list[str] = []
    raw = message.get("subject") or []
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, str) and s.strip():
                out.append(s.strip())
    return out


async def get_work_by_doi(doi: str) -> CrossrefWork | None:
    """Lookup paper su Crossref via DOI. Ritorna `None` su 404 / errore
    HTTP / timeout. Resilient: log warning ma niente eccezioni."""
    if not doi or not isinstance(doi, str):
        return None
    doi = doi.strip()
    if not doi:
        return None
    base_url = get_settings().crossref_base_url.rstrip("/")
    url = f"{base_url}/works/{doi}"
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={
                "User-Agent": _user_agent(),
                "Accept": "application/json",
            },
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        log.warning("crossref_http_error", doi=doi, error=str(exc))
        return None

    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        log.warning(
            "crossref_api_error",
            doi=doi,
            status=resp.status_code,
            body=resp.text[:200],
        )
        return None

    try:
        data = resp.json()
    except Exception:
        log.warning("crossref_invalid_json", doi=doi)
        return None
    if not isinstance(data, dict):
        return None
    message = data.get("message") or {}
    if not isinstance(message, dict):
        return None

    references = message.get("reference") or []
    references_count = (
        len(references) if isinstance(references, list) else None
    )

    return CrossrefWork(
        doi=doi,
        title=_extract_title(message),
        abstract=_clean_abstract(message.get("abstract")),
        references_count=references_count,
        subjects=_extract_subjects(message),
        published_year=_extract_published_year(message),
    )
