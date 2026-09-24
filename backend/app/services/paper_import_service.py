"""Service di import di paper scientifici come `CourseDocument`.

Strategia per ogni paper selezionato dall'utente:
- Se `oa_pdf_url` presente: rilegge il lavoro da OpenAlex (`get_work`) e
  usa l'URL del PDF e i metadati del SERVER, mai quelli arrivati dal
  client (chiude la SSRF dell'import); poi `openalex_client.download_pdf`
  (con `safe_http`). Se OK -> salva come `.pdf` via
  `course_service.add_document_from_bytes`, con la licenza della location
  open access quando è Creative Commons o pubblico dominio.
- Se l'editore rifiuta il download automatico (403 di MDPI, Hindawi…) o il
  PDF non si scarica: la copia ospitata da OpenAlex (`has_content.pdf`,
  con la API key, 0,01 $ a PDF), con la licenza della stessa location.
- Se download fallisce o NO PDF disponibile: genera un file `.md` con
  i metadata del paper (titolo, autori, anno, journal, DOI, abstract,
  tldr, keywords, subjects) e salva come `text/markdown` via lo stesso
  helper.

In entrambi i casi il `CourseDocument` viene creato con
`summary_status='pending'` e il worker `course_document_worker`
prende in carico l'analisi (extract_text + summarize AI).

Funzione: `import_paper(db, course, paper, actor_id) -> (CourseDocument, mode)`
dove `mode` e' "pdf" o "metadata".
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ValidationAppError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.schemas.document_bibliography import DocumentBibliography
from app.schemas.paper_search import PaperOut
from app.services import course_service
from app.services.openalex_client import (
    OpenAlexError,
    OpenAlexWork,
    content_pdf_available,
    download_content_pdf,
    download_pdf,
    get_work,
    oa_best_pdf_url,
    oa_location_license,
)

log = get_logger("app.paper_import")

ImportMode = Literal["pdf", "metadata"]


@dataclass(frozen=True)
class PaperImportResult:
    document: CourseDocument
    mode: ImportMode


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    """Slug minimale per nomi file: ascii lowercase, separatori `_`."""
    if not text:
        return "paper"
    s = text.lower()
    # Sostituisce caratteri non-ascii con vuoto via NFKD decomp
    import unicodedata

    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = _SLUG_RE.sub("_", s).strip("_")
    if not s:
        return "paper"
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s


def _build_filename_stem(paper: PaperOut) -> str:
    """Compone uno stem deterministico ma leggibile per il file salvato.

    Pattern: `{primo_autore_slug}_{anno}_{titolo_slug}` (max ~80 char
    totali). Aggiunge un suffisso UUID corto per evitare collisioni se
    l'utente importa lo stesso paper piu' volte.
    """
    author = paper.authors[0] if paper.authors else ""
    author_slug = _slugify(author, max_len=20) if author else "anon"
    year = str(paper.year) if paper.year else "ny"
    title_slug = _slugify(paper.title, max_len=40)
    # Suffix UUID corto per unicità (l'utente potrebbe re-importare).
    suffix = uuid.uuid4().hex[:6]
    return f"{author_slug}_{year}_{title_slug}_{suffix}"


def _paper_bibliography(paper: PaperOut) -> DocumentBibliography:
    """Bibliografia del documento dai metadati OpenAlex dell'import.

    Serve alla riga «Fonte» delle figure di fonte; la licenza del PDF non
    è nei metadati di ricerca e resta sconosciuta (NULL) sul documento.
    """
    def clip(value: str | None, limit: int) -> str | None:
        cleaned = " ".join((value or "").split())[:limit].strip()
        return cleaned or None

    year = paper.year if paper.year and 1400 <= paper.year <= 2100 else None
    url = clip(paper.doi_url, 1000)
    if url and not url.lower().startswith(("https://", "http://")):
        url = None
    authors = [a for a in (clip(name, 200) for name in paper.authors) if a]
    return DocumentBibliography(
        title=clip(paper.title, 500),
        authors=authors[:50],
        year=year,
        container=clip(paper.journal, 300),
        doi=clip(paper.doi, 200),
        url=url,
        openalex_id=clip(paper.id, 100),
    )


def _render_metadata_md(paper: PaperOut) -> str:
    """Genera un Markdown con tutti i metadata del paper. Verra'
    processato dal worker (extract_text legge .md come plain text +
    summarize AI produce summary Appendice A)."""
    parts: list[str] = []
    parts.append(f"# {paper.title}")
    parts.append("")

    # Authors block
    if paper.authors:
        authors_str = ", ".join(paper.authors)
        parts.append(f"**Authors:** {authors_str}")
    if paper.year:
        parts.append(f"**Year:** {paper.year}")
    if paper.journal:
        parts.append(f"**Journal / Venue:** {paper.journal}")
    if paper.work_type:
        parts.append(f"**Type:** {paper.work_type}")
    if paper.doi:
        parts.append(f"**DOI:** {paper.doi}")
    if paper.doi_url:
        parts.append(f"**Link:** {paper.doi_url}")
    if paper.citations:
        parts.append(f"**Citations (at import time):** {paper.citations}")
    parts.append(f"**Open Access:** {'yes' if paper.is_oa else 'no'}")
    parts.append("")

    if paper.abstract:
        parts.append("## Abstract")
        parts.append("")
        parts.append(paper.abstract)
        parts.append("")

    if paper.tldr:
        parts.append("## TL;DR (Semantic Scholar)")
        parts.append("")
        parts.append(paper.tldr)
        parts.append("")

    if paper.keywords:
        parts.append("## Keywords")
        parts.append("")
        parts.append(", ".join(paper.keywords))
        parts.append("")

    if paper.subjects:
        parts.append("## Subject Categories (Crossref)")
        parts.append("")
        parts.append(", ".join(paper.subjects))
        parts.append("")

    parts.append("---")
    parts.append("")
    parts.append(
        "_Questo documento e' stato importato automaticamente dalla "
        "ricerca paper scientifici. Il PDF originale non e' stato "
        "scaricato (paper non open-access o download non riuscito), "
        "quindi l'analisi AI lavorera' su questi metadati + abstract._"
    )
    return "\n".join(parts)


def _from_server(paper: PaperOut, work: OpenAlexWork) -> PaperOut:
    """Il paper con URL del PDF e metadati letti dal server OpenAlex."""
    return paper.model_copy(
        update={
            "doi": work.doi,
            "title": work.title or paper.title,
            "authors": work.authors,
            "year": work.publication_year,
            "journal": work.journal,
            "is_oa": work.is_oa,
            "oa_pdf_url": work.oa_pdf_url,
            "doi_url": f"https://doi.org/{work.doi}" if work.doi else None,
        }
    )


async def import_paper(
    db: AsyncSession,
    *,
    course: Course,
    paper: PaperOut,
    actor_id: uuid.UUID,
    citation_policy: str = "citable",
) -> PaperImportResult:
    """Importa un singolo paper come `CourseDocument`.

    Strategia:
    1. Se `oa_pdf_url` presente -> tenta download PDF -> se OK salva .pdf.
    2. Se l'editore rifiuta -> copia del PDF ospitata da OpenAlex.
    3. Altrimenti (o se download fallisce) -> genera .md con metadati.

    Solleva eccezione solo per errori non recuperabili (es. validation
    file_service). Errori di download sono gestiti con fallback a .md.
    """
    settings = get_settings()
    max_bytes = settings.course_document_max_mb * 1024 * 1024

    pdf_bytes: bytes | None = None
    license_code: str | None = None
    work: OpenAlexWork | None = None
    if paper.oa_pdf_url:
        # L'URL del client non si scarica mai: vale quello del server.
        try:
            work = await get_work(paper.id)
            paper = _from_server(paper, work)
            # La licenza vale solo per il PDF della sua location.
            best_pdf = oa_best_pdf_url(work)
            if best_pdf:
                paper = paper.model_copy(update={"oa_pdf_url": best_pdf})
                license_code = oa_location_license(work)
        except OpenAlexError as exc:
            log.warning(
                "paper_import_reread_failed_fallback_to_metadata",
                paper_id=paper.id,
                error=str(exc),
            )
            paper = paper.model_copy(update={"oa_pdf_url": None})
    if paper.oa_pdf_url:
        try:
            pdf_bytes = await download_pdf(
                paper.oa_pdf_url, max_bytes=max_bytes
            )
            log.info(
                "paper_import_pdf_downloaded",
                paper_id=paper.id,
                size=len(pdf_bytes),
            )
        except OpenAlexError as exc:
            log.warning(
                "paper_import_pdf_failed_fallback_to_metadata",
                paper_id=paper.id,
                url=paper.oa_pdf_url,
                error=str(exc),
            )
            pdf_bytes = None
    if pdf_bytes is None and work is not None and content_pdf_available(work):
        # Editore che blocca i download automatici o PDF non scaricabile: la
        # copia ospitata da OpenAlex, con la licenza della location migliore.
        try:
            pdf_bytes = await download_content_pdf(work, max_bytes=max_bytes)
            license_code = oa_location_license(work)
            log.info(
                "paper_import_pdf_from_openalex_copy",
                paper_id=paper.id,
                size=len(pdf_bytes),
            )
        except OpenAlexError as exc:
            log.warning(
                "paper_import_openalex_copy_failed_fallback_to_metadata",
                paper_id=paper.id,
                error=str(exc),
            )
            pdf_bytes = None

    stem = _build_filename_stem(paper)
    bibliography = _paper_bibliography(paper)

    if pdf_bytes is not None:
        try:
            doc = await course_service.add_document_from_bytes(
                db,
                course=course,
                payload=pdf_bytes,
                filename_original=f"{stem}.pdf",
                mime_type="application/pdf",
                actor_id=actor_id,
                citation_policy=citation_policy,
                origin="paper_import",
                bibliography=bibliography,
                bibliography_source="openalex",
            )
            if license_code is not None:
                doc.license = license_code
                doc.license_source = "openalex"
            return PaperImportResult(document=doc, mode="pdf")
        except ValidationAppError as exc:
            log.warning(
                "paper_import_pdf_validation_failed_fallback",
                paper_id=paper.id,
                error=str(exc),
            )
            # Fallback a metadata .md
            pdf_bytes = None

    # Fallback: salva .md con metadati
    md_text = _render_metadata_md(paper)
    payload = md_text.encode("utf-8")
    doc = await course_service.add_document_from_bytes(
        db,
        course=course,
        payload=payload,
        filename_original=f"{stem}.md",
        mime_type="text/markdown",
        actor_id=actor_id,
        citation_policy=citation_policy,
        origin="paper_metadata",
        bibliography=bibliography,
        bibliography_source="openalex",
    )
    return PaperImportResult(document=doc, mode="metadata")
