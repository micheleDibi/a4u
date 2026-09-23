"""Bibliografia del documento da fonti deterministiche (mai dal modello).

- `pdf_metadata`: dizionario Info e XMP del PDF (pypdf), `docProps/core.xml`
  di DOCX e PPTX; titoli e autori implausibili (nomi di file, «Microsoft
  Word - …», account utente) scartati;
- `crossref`: DOI trovato nel testo delle prime pagine e risolto da
  Crossref (autori, titolo, rivista, anno).

Il worker delle figure la calcola solo se il documento non ha già una
bibliografia (mai sovrascritte quelle del docente o di OpenAlex).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.schemas.document_bibliography import DocumentBibliography

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}|\\^`\[\]]+)", re.IGNORECASE)
_JUNK_TITLE_RE = re.compile(
    r"(^microsoft (word|powerpoint)|^untitled|^senza titolo|^presentazione|^documento\d*$"
    r"|\.(docx?|pptx?|pdf|tex|dvi|odt)$|^slide \d+$)",
    re.IGNORECASE,
)
_CORE_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dcterms": "http://purl.org/dc/terms/",
}


def plausible_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    title = " ".join(value.split())
    if len(title) < 6 or len(title) > 500 or _JUNK_TITLE_RE.search(title):
        return None
    if not re.search(r"[A-Za-zÀ-ÿ]{3}", title):
        return None
    return title


def plausible_authors(value: Any) -> list[str]:
    """Autori da una stringa «A. Rossi; B. Bianchi» o «Rossi, Bianchi»:
    solo nomi con almeno due parole (un account `mrossi` non è un autore)."""
    if isinstance(value, list):
        parts = [str(v) for v in value]
    elif isinstance(value, str):
        parts = re.split(r";|\band\b|\be\b|,(?=\s*[A-ZÀ-Ý][^,]*\s)", value)
    else:
        return []
    out = []
    for part in parts:
        name = " ".join(part.split()).strip(" ,;")
        if len(name.split()) >= 2 and len(name) <= 200 and not re.search(r"[@\\/_]", name):
            out.append(name)
    return out[:50]


def _year(value: Any) -> int | None:
    match = re.search(r"(1[4-9]\d\d|20\d\d|2100)", str(value or ""))
    return int(match.group(1)) if match else None


def pdf_bibliography(path: Path) -> DocumentBibliography | None:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return None
        info: dict[str, Any] = dict(reader.metadata or {})
        title = plausible_title(info.get("/Title"))
        authors = plausible_authors(info.get("/Author"))
        year = _year(info.get("/CreationDate"))
        xmp = reader.xmp_metadata
        if xmp is not None:
            if title is None and xmp.dc_title:
                title = plausible_title(next(iter(xmp.dc_title.values()), None))
            if not authors and xmp.dc_creator:
                authors = plausible_authors(list(xmp.dc_creator))
    except Exception:
        return None
    if title is None and not authors:
        return None
    return DocumentBibliography(title=title, authors=authors, year=year)


def office_bibliography(path: Path) -> DocumentBibliography | None:
    try:
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("docProps/core.xml"))
    except Exception:
        return None
    title = plausible_title(root.findtext("dc:title", default=None, namespaces=_CORE_NS))
    authors = plausible_authors(root.findtext("dc:creator", default=None, namespaces=_CORE_NS))
    year = _year(root.findtext("dcterms:created", default=None, namespaces=_CORE_NS))
    if title is None and not authors:
        return None
    return DocumentBibliography(title=title, authors=authors, year=year)


def find_doi(text: str | None) -> str | None:
    if not text:
        return None
    match = DOI_RE.search(text)
    if match is None:
        return None
    return match.group(1).rstrip(".,;:)]}'\"").lower()


def pdf_first_pages_text(path: Path, pages: int = 2) -> str:
    import pdfplumber

    try:
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages[:pages])
    except Exception:
        return ""
