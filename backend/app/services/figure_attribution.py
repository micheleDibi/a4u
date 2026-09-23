"""Riga «Fonte» delle figure di fonte, calcolata a render e mai dal modello.

Unico punto che compone l'attribuzione: dispensa, PDF, slide, frame video,
vista ed editor la ricevono già pronta (il frontend non la ricompone).

- :func:`attribution_source` ricava i dati da figura e documento. Per un
  documento del corso usa la bibliografia solo se viene da una fonte
  deterministica (docente, OpenAlex, metadati del file, Crossref); la
  proposta del riassunto LLM (`summary_proposal`) non entra mai nella riga,
  che in quel caso ricade sul nome del file. Per le figure staccate (docum.
  cancellato) e per quelle della letteratura aperta vale l'attribuzione
  congelata in ``fig.attribution`` (:meth:`AttributionSource.to_json`).
- :func:`attribution_line` compone il testo scritto (it/en, ripiego su it
  come `figure_theme.figure_labels`) o la variante parlata per il TTS.
- :func:`spoken_source` dà i dati parlati per il PROMPT 6 (Fase 5), già
  resi sicuri per il TTS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.schemas.document_bibliography import TRUSTED_BIBLIOGRAPHY_SOURCES

AttributionMode = Literal["written", "spoken"]
PageKind = Literal["page", "slide"]

_FALLBACK_LANGUAGE = "it"
_MAX_AUTHORS_WRITTEN = 3
_MAX_SURNAMES_SPOKEN = 2
_MAX_SPOKEN_TITLE = 120

_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# Testi per lingua. Le lingue diverse da it/en ricadono su it.
_TEXTS: dict[str, dict[str, str]] = {
    "it": {
        "prefix": "Fonte:",
        "prefix_adapted": "Adattato da:",
        "and": "e",
        "et_al": "et al.",
        "page": "p. {n}",
        "slide": "slide {n}",
        "figure": "fig. {n}",
        "title_open": "«",
        "title_close": "»",
        "own_work": "materiale del docente",
        "more_authors_spoken": "e collaboratori",
    },
    "en": {
        "prefix": "Source:",
        "prefix_adapted": "Adapted from:",
        "and": "and",
        "et_al": "et al.",
        "page": "p. {n}",
        "slide": "slide {n}",
        "figure": "fig. {n}",
        "title_open": "“",
        "title_close": "”",
        "own_work": "instructor's material",
        "more_authors_spoken": "and colleagues",
    },
}

# Etichette delle licenze; `other` e `unknown` non si scrivono.
_LICENSE_LABELS: dict[str, dict[str, str]] = {
    "it": {
        "cc0": "CC0",
        "public_domain": "pubblico dominio",
        "cc_by": "CC BY",
        "cc_by_sa": "CC BY-SA",
        "cc_by_nc": "CC BY-NC",
        "cc_by_nd": "CC BY-ND",
        "cc_by_nc_sa": "CC BY-NC-SA",
        "cc_by_nc_nd": "CC BY-NC-ND",
        "all_rights_reserved": "tutti i diritti riservati",
    },
    "en": {
        "cc0": "CC0",
        "public_domain": "public domain",
        "cc_by": "CC BY",
        "cc_by_sa": "CC BY-SA",
        "cc_by_nc": "CC BY-NC",
        "cc_by_nd": "CC BY-ND",
        "cc_by_nc_sa": "CC BY-NC-SA",
        "cc_by_nc_nd": "CC BY-NC-ND",
        "all_rights_reserved": "all rights reserved",
    },
}

# Abbreviazioni vietate nel parlato (course_lesson_speech_service) e loro
# forma estesa; la chiave è la forma minuscola.
_SPOKEN_EXPANSIONS: dict[str, dict[str, str]] = {
    "it": {
        "p.es.": "per esempio",
        "es.": "ad esempio",
        "etc.": "eccetera",
        "ca.": "circa",
        "i.e.": "cioè",
        "e.g.": "per esempio",
    },
    "en": {
        "p.es.": "for example",
        "es.": "for example",
        "etc.": "etcetera",
        "ca.": "circa",
        "i.e.": "that is",
        "e.g.": "for example",
    },
}
_SPOKEN_ABBREVIATION_RE = re.compile(
    r"\bp\.es\.|\bi\.e\.|\be\.g\.|\betc\.|\bca\.|\bes\.", re.IGNORECASE
)

# «Figura 3.2», «Fig. 12a», «Figure 4-1», «Abb. 2»: numero letto
# dall'etichetta estratta dal documento, mai inventato.
_FIGURE_NUMBER_RE = re.compile(
    r"\b(?:fig(?:ure|ura|\.)?|abb(?:ildung|\.)?)\s*(\d+(?:[.\-]\d+)*[a-z]?)\b",
    re.IGNORECASE,
)
# Suffisso casuale dei file importati dalla ricerca paper (`_1a2b3c`).
_PAPER_SUFFIX_RE = re.compile(r"_[0-9a-f]{6}$")
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_SPACES_RE = re.compile(r"\s+")


class FigureAttributionLike(Protocol):
    source_kind: str
    page: int | None
    source_label: str | None
    license: str
    attribution: dict[str, Any] | None


class DocumentAttributionLike(Protocol):
    filename_original: str
    mime_type: str
    is_own_work: bool
    bibliography: dict[str, Any] | None
    bibliography_source: str | None


@dataclass(frozen=True)
class AttributionSource:
    """Dati dell'attribuzione, già ripuliti. Serializzabile per congelarla."""

    authors: tuple[str, ...] = ()
    title: str | None = None
    container: str | None = None
    year: int | None = None
    figure_number: str | None = None
    page: int | None = None
    page_kind: PageKind = "page"
    license: str = "unknown"
    license_url: str | None = None
    url: str | None = None
    is_own_work: bool = False
    # Nome leggibile del file, usato quando mancano autori e titolo.
    fallback_name: str | None = None
    # Riga di credito imposta dalla fonte (es. «Artist» di Wikimedia).
    credit: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "authors": list(self.authors),
            "title": self.title,
            "container": self.container,
            "year": self.year,
            "figure_number": self.figure_number,
            "page": self.page,
            "page_kind": self.page_kind,
            "license": self.license,
            "license_url": self.license_url,
            "url": self.url,
            "is_own_work": self.is_own_work,
            "fallback_name": self.fallback_name,
            "credit": self.credit,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], *, license: str | None = None) -> AttributionSource:
        """Ricostruisce l'attribuzione congelata, tollerando chiavi mancanti."""
        authors = data.get("authors") or ()
        year = data.get("year")
        page = data.get("page")
        page_kind = data.get("page_kind")
        return cls(
            authors=tuple(_clean(a) for a in authors if isinstance(a, str) and _clean(a)),
            title=_clean_optional(data.get("title")),
            container=_clean_optional(data.get("container")),
            year=year if isinstance(year, int) else None,
            figure_number=_clean_optional(data.get("figure_number")),
            page=page if isinstance(page, int) and page >= 1 else None,
            page_kind="slide" if page_kind == "slide" else "page",
            license=license or str(data.get("license") or "unknown"),
            license_url=_clean_optional(data.get("license_url")),
            url=_clean_optional(data.get("url")),
            is_own_work=bool(data.get("is_own_work")),
            fallback_name=_clean_optional(data.get("fallback_name")),
            credit=_clean_optional(data.get("credit")),
        )


def _clean(value: str) -> str:
    return _SPACES_RE.sub(" ", value).strip()


def _clean_optional(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _clean(value)
    return cleaned or None


def _language(language: str | None) -> str:
    code = (language or _FALLBACK_LANGUAGE).strip().lower().split("-")[0].split("_")[0]
    return code if code in _TEXTS else _FALLBACK_LANGUAGE


def figure_number_from_label(source_label: str | None) -> str | None:
    """Numero della figura nel documento sorgente, solo se letto davvero."""
    if not source_label:
        return None
    match = _FIGURE_NUMBER_RE.search(source_label)
    return match.group(1) if match else None


def readable_filename(filename: str | None) -> str | None:
    """Nome del file senza estensione, senza il suffisso degli import dei
    paper e con gli underscore resi spazi."""
    if not filename:
        return None
    stem = _EXTENSION_RE.sub("", filename.strip())
    stem = _PAPER_SUFFIX_RE.sub("", stem)
    stem = _clean(stem.replace("_", " "))
    return stem or None


def attribution_source(
    fig: FigureAttributionLike, doc: DocumentAttributionLike | None
) -> AttributionSource | None:
    """Dati di attribuzione della figura; None se non attribuibile.

    Documento presente → dati vivi dal documento (bibliografia fidata o
    nome del file). Documento assente (figura staccata o esterna) →
    attribuzione congelata sulla figura.
    """
    figure_number = figure_number_from_label(fig.source_label)
    if fig.source_kind == "uploaded" and doc is not None:
        bibliography: dict[str, Any] = {}
        if doc.bibliography_source in TRUSTED_BIBLIOGRAPHY_SOURCES and isinstance(
            doc.bibliography, dict
        ):
            bibliography = doc.bibliography
        base = AttributionSource.from_json(
            {
                "authors": bibliography.get("authors"),
                "title": bibliography.get("title"),
                "container": bibliography.get("container") or bibliography.get("publisher"),
                "year": bibliography.get("year"),
                "url": bibliography.get("url"),
            }
        )
        return AttributionSource(
            authors=base.authors,
            title=base.title,
            container=base.container,
            year=base.year,
            figure_number=figure_number,
            page=fig.page if fig.page and fig.page >= 1 else None,
            page_kind="slide" if doc.mime_type == _PPTX_MIME else "page",
            license=fig.license or "unknown",
            url=base.url,
            is_own_work=bool(doc.is_own_work),
            fallback_name=readable_filename(doc.filename_original),
        )
    frozen = fig.attribution
    if not isinstance(frozen, dict) or not frozen:
        return None
    return AttributionSource.from_json(frozen, license=fig.license)


def freeze_attribution(
    fig: FigureAttributionLike, doc: DocumentAttributionLike
) -> dict[str, Any] | None:
    """Attribuzione da congelare su una figura prima di staccarla dal
    documento cancellato: la riga resta identica a prima."""
    source = attribution_source(fig, doc)
    return source.to_json() if source is not None else None


def _join_authors(authors: tuple[str, ...], texts: dict[str, str]) -> str | None:
    if not authors:
        return None
    if len(authors) > _MAX_AUTHORS_WRITTEN:
        return f"{authors[0]} {texts['et_al']}"
    if len(authors) == 1:
        return authors[0]
    return f"{', '.join(authors[:-1])} {texts['and']} {authors[-1]}"


def _strip_final_period(value: str) -> str:
    return value[:-1].rstrip() if value.endswith(".") and not value.endswith("..") else value


def license_label(license: str | None, *, language: str | None) -> str | None:
    """Etichetta della licenza; None per `unknown`, `other` o valori ignoti."""
    if not license:
        return None
    return _LICENSE_LABELS[_language(language)].get(license)


def _written_line(src: AttributionSource, lang: str, *, adapted: bool) -> str:
    texts = _TEXTS[lang]
    segments: list[str] = []
    if src.credit:
        segments.append(src.credit)
    authors = _join_authors(src.authors, texts)
    if authors:
        segments.append(authors)
    if src.title:
        title = _strip_final_period(src.title)
        segments.append(f"{texts['title_open']}{title}{texts['title_close']}")
    if not authors and not src.title and not src.credit:
        if src.fallback_name:
            segments.append(src.fallback_name)
        elif src.is_own_work:
            segments.append(texts["own_work"])
    if src.container:
        segments.append(src.container)
    if src.year:
        segments.append(str(src.year))
    if src.figure_number:
        segments.append(texts["figure"].format(n=src.figure_number))
    if src.page:
        key = "slide" if src.page_kind == "slide" else "page"
        segments.append(texts[key].format(n=src.page))
    body = ", ".join(s for s in segments if s)
    label = license_label(src.license, language=lang)
    if label:
        body = f"{body} ({label})" if body else f"({label})"
    prefix = texts["prefix_adapted"] if adapted else texts["prefix"]
    return f"{prefix} {body}".strip()


def _tts_safe(value: str | None, lang: str) -> str | None:
    """Versione sicura per il TTS; None se non lo si può rendere tale."""
    if not value:
        return None
    # Import locale: il servizio del discorso importa i modelli ORM.
    from app.services.course_lesson_speech_service import (
        sanitize_tts_text,
        validate_tts_safety,
    )

    expansions = _SPOKEN_EXPANSIONS[lang]
    expanded = _SPOKEN_ABBREVIATION_RE.sub(lambda m: expansions.get(m.group(0).lower(), " "), value)
    cleaned = _clean(sanitize_tts_text(expanded))
    if not cleaned or validate_tts_safety(cleaned):
        return None
    return cleaned


def _surname(author: str) -> str:
    """Cognome da «Nome Cognome» o «Cognome, Nome»."""
    if "," in author:
        return author.split(",", 1)[0].strip()
    parts = author.split()
    return parts[-1] if parts else author


def spoken_source(src: AttributionSource, *, language: str | None) -> dict[str, Any]:
    """Dati della fonte da pronunciare (PROMPT 6): al più due cognomi, un
    flag per gli altri autori, titolo breve, anno. Ogni campo che non si
    riesce a rendere sicuro per il TTS viene tolto."""
    lang = _language(language)
    surnames: list[str] = []
    for author in src.authors[:_MAX_SURNAMES_SPOKEN]:
        safe = _tts_safe(_surname(author), lang)
        if safe:
            surnames.append(safe)
    title = _strip_final_period(src.title) if src.title else None
    if title and len(title) > _MAX_SPOKEN_TITLE:
        title = title[:_MAX_SPOKEN_TITLE].rsplit(" ", 1)[0]
    data: dict[str, Any] = {
        "authors": surnames,
        "more_authors": len(src.authors) > _MAX_SURNAMES_SPOKEN,
        "title": _tts_safe(title, lang),
        "year": src.year,
    }
    if not surnames and not data["title"]:
        data["name"] = _tts_safe(src.credit or src.fallback_name, lang)
    return data


def _spoken_line(src: AttributionSource, lang: str, *, adapted: bool) -> str:
    texts = _TEXTS[lang]
    data = spoken_source(src, language=lang)
    segments: list[str] = []
    surnames: list[str] = data["authors"]
    if surnames:
        who = f" {texts['and']} ".join(surnames)
        if data["more_authors"]:
            who = f"{who} {texts['more_authors_spoken']}"
        segments.append(who)
    if data["title"]:
        segments.append(f"{texts['title_open']}{data['title']}{texts['title_close']}")
    if not segments and data.get("name"):
        segments.append(data["name"])
    if not segments and src.is_own_work:
        segments.append(texts["own_work"])
    if data["year"]:
        segments.append(str(data["year"]))
    prefix = texts["prefix_adapted"] if adapted else texts["prefix"]
    return f"{prefix} {', '.join(segments)}".strip()


def attribution_line(
    src: AttributionSource,
    *,
    language: str | None,
    mode: AttributionMode = "written",
    adapted: bool = False,
) -> str:
    """Testo della riga «Fonte» (scritta) o della sua variante parlata.

    La variante parlata non contiene pagine, numeri di figura né licenze e
    supera sempre `validate_tts_safety`.
    """
    lang = _language(language)
    if mode == "spoken":
        return _spoken_line(src, lang, adapted=adapted)
    return _written_line(src, lang, adapted=adapted)


def figure_attribution_line(
    fig: FigureAttributionLike,
    doc: DocumentAttributionLike | None,
    *,
    language: str | None,
    mode: AttributionMode = "written",
    adapted: bool = False,
) -> str | None:
    """Scorciatoia figura+documento → riga; None se non attribuibile."""
    src = attribution_source(fig, doc)
    if src is None:
        return None
    return attribution_line(src, language=language, mode=mode, adapted=adapted)
