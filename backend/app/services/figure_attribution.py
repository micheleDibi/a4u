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
- :func:`fitted_written_line` è la stessa riga scritta accorciata con «…»
  per stare nella fascia delle slide e dei frame (larghezza stimata con
  :func:`text_em`); senza bisogno di tagli coincide con la riga intera.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, TypeGuard

from app.schemas.document_bibliography import TRUSTED_BIBLIOGRAPHY_SOURCES
from app.services.source_caption import third_party_credit

AttributionMode = Literal["written", "spoken"]
PageKind = Literal["page", "slide"]

_FALLBACK_LANGUAGE = "it"
_MAX_AUTHORS_WRITTEN = 3
_MAX_SURNAMES_SPOKEN = 2
_MAX_SPOKEN_TITLE = 120

_PAPER_ORIGINS = frozenset({"paper_import", "paper_metadata"})
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
_MAX_FIGURE_NUMBER_CHARS = 10
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
    origin: str
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
    # Versione della licenza Creative Commons («4.0»), quando la fonte la
    # dichiara (Wikimedia): la riga scritta la riporta accanto al nome.
    license_version: str | None = None
    license_url: str | None = None
    url: str | None = None
    is_own_work: bool = False
    # Nome leggibile del file, usato quando mancano autori e titolo.
    fallback_name: str | None = None
    # Riga di credito imposta dalla fonte (es. «Artist» di Wikimedia).
    credit: str | None = None

    @property
    def identifies_source(self) -> bool:
        """True se c'è almeno un dato che nomina la fonte: una riga con la
        sola pagina o la sola licenza non è un'attribuzione."""
        return bool(
            self.authors or self.title or self.credit or self.fallback_name or self.is_own_work
        )

    def to_json(self) -> dict[str, Any]:
        """Forma congelata, senza chiavi vuote (il CHECK del DB esige almeno
        una chiave che identifichi la fonte)."""
        data: dict[str, Any] = {
            "authors": list(self.authors),
            "title": self.title,
            "container": self.container,
            "year": self.year,
            "figure_number": self.figure_number,
            "page": self.page,
            "page_kind": self.page_kind,
            "license": self.license,
            "license_version": self.license_version,
            "license_url": self.license_url,
            "url": self.url,
            "is_own_work": self.is_own_work,
            "fallback_name": self.fallback_name,
            "credit": self.credit,
        }
        return {k: v for k, v in data.items() if v not in (None, [], False, "")}

    @classmethod
    def from_json(cls, data: dict[str, Any], *, license: str | None = None) -> AttributionSource:
        """Ricostruisce l'attribuzione congelata, tollerando chiavi mancanti."""
        authors = data.get("authors") or ()
        if isinstance(authors, str):
            authors = [authors]
        elif not isinstance(authors, list | tuple):
            authors = ()
        year = data.get("year")
        page = data.get("page")
        page_kind = data.get("page_kind")
        return cls(
            authors=tuple(_clean(a) for a in authors if isinstance(a, str) and _clean(a)),
            title=_clean_optional(data.get("title")),
            container=_clean_optional(data.get("container")),
            year=year if _is_int(year) else None,
            figure_number=_clean_optional(data.get("figure_number")),
            page=page if _is_int(page) and page >= 1 else None,
            page_kind="slide" if page_kind == "slide" else "page",
            license=license or str(data.get("license") or "unknown"),
            license_version=_clean_optional(data.get("license_version")),
            license_url=_clean_optional(data.get("license_url")),
            url=_clean_optional(data.get("url")),
            is_own_work=data.get("is_own_work") is True,
            fallback_name=_clean_optional(data.get("fallback_name")),
            credit=_clean_optional(data.get("credit")),
        )


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


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
    # Un «numero» di 60 caratteri riempie la fascia e fa sparire chi e che
    # cosa (Fase D): oltre _MAX_FIGURE_NUMBER_CHARS non è un numero.
    if match is None or len(match.group(1)) > _MAX_FIGURE_NUMBER_CHARS:
        return None
    return match.group(1)


def readable_filename(filename: str | None, *, paper_import: bool = False) -> str | None:
    """Nome del file senza estensione e con gli underscore resi spazi; per i
    documenti importati dalla ricerca paper toglie anche il suffisso casuale
    (`_1a2b3c`) che l'import aggiunge."""
    if not filename:
        return None
    stem = _EXTENSION_RE.sub("", filename.strip())
    if paper_import:
        stem = _PAPER_SUFFIX_RE.sub("", stem)
    stem = _clean(stem.replace("_", " "))
    return stem or None


def attribution_source(
    fig: FigureAttributionLike, doc: DocumentAttributionLike | None
) -> AttributionSource | None:
    """Dati di attribuzione della figura; None se non attribuibile.

    Documento presente → dati vivi dal documento (bibliografia fidata o
    nome del file). Documento assente (figura staccata o esterna) →
    attribuzione congelata sulla figura. Senza un dato che nomini la fonte
    (autori, titolo, credito, nome del file, materiale proprio) la figura
    non è attribuibile.
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
        source = AttributionSource(
            # Credito di terzi nella didascalia originale: la figura viene da
            # lì, il documento è solo il tramite (Fase D).
            credit=third_party_credit(getattr(fig, "source_caption", None)),
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
            fallback_name=readable_filename(
                doc.filename_original, paper_import=doc.origin in _PAPER_ORIGINS
            ),
        )
        return source if source.identifies_source else None
    frozen = fig.attribution
    if not isinstance(frozen, dict) or not frozen:
        return None
    source = AttributionSource.from_json(frozen, license=fig.license)
    if fig.source_kind != "uploaded" and source.is_own_work:
        # Le figure della letteratura aperta non sono mai materiale proprio.
        source = replace(source, is_own_work=False)
    return source if source.identifies_source else None


def freeze_attribution(
    fig: FigureAttributionLike, doc: DocumentAttributionLike
) -> dict[str, Any] | None:
    """Attribuzione da congelare su una figura prima di staccarla dal
    documento cancellato: la riga resta identica a prima."""
    source = attribution_source(fig, doc)
    return source.to_json() if source is not None else None


def _join_authors(
    authors: tuple[str, ...], texts: dict[str, str], *, max_authors: int = _MAX_AUTHORS_WRITTEN
) -> str | None:
    if not authors:
        return None
    if len(authors) > max_authors:
        return f"{authors[0]} {texts['et_al']}"
    if len(authors) == 1:
        return authors[0]
    return f"{', '.join(authors[:-1])} {texts['and']} {authors[-1]}"


# Un punto finale che chiude un'abbreviazione o un'iniziale resta.
_KEEP_FINAL_PERIOD_RE = re.compile(r"(?:\b(?:etc|al|ca|vs|e\.g|i\.e)|\b[A-Za-z]|\.\.)\.$")


def _strip_final_period(value: str) -> str:
    if not value.endswith(".") or _KEEP_FINAL_PERIOD_RE.search(value):
        return value
    return value[:-1].rstrip()


def license_label(license: str | None, *, language: str | None) -> str | None:
    """Etichetta della licenza; None per `unknown`, `other` o valori ignoti."""
    if not license:
        return None
    return _LICENSE_LABELS[_language(language)].get(license)


def _written_parts(
    src: AttributionSource, texts: dict[str, str], *, max_authors: int = _MAX_AUTHORS_WRITTEN
) -> tuple[list[str], list[str]]:
    """Segmenti della riga scritta: testa (chi e che cosa) e coda (anno,
    figura, pagina), che la versione compatta non accorcia mai."""
    head: list[str] = []
    if src.credit:
        head.append(src.credit)
    authors = _join_authors(src.authors, texts, max_authors=max_authors)
    if authors:
        head.append(authors)
    if src.title:
        title = _strip_final_period(src.title)
        head.append(f"{texts['title_open']}{title}{texts['title_close']}")
    if not authors and not src.title and not src.credit:
        if src.fallback_name:
            head.append(src.fallback_name)
        elif src.is_own_work:
            head.append(texts["own_work"])
    if src.container:
        head.append(src.container)
    tail: list[str] = []
    if src.year:
        tail.append(str(src.year))
    if src.figure_number:
        tail.append(texts["figure"].format(n=src.figure_number))
    if src.page:
        key = "slide" if src.page_kind == "slide" else "page"
        tail.append(texts[key].format(n=src.page))
    return head, tail


def _compose(src: AttributionSource, lang: str, segments: list[str], *, adapted: bool) -> str:
    texts = _TEXTS[lang]
    body = ", ".join(s for s in segments if s)
    label = license_label(src.license, language=lang)
    if label and src.license_version:
        label = f"{label} {src.license_version}"
    if label:
        body = f"{body} ({label})" if body else f"({label})"
    prefix = texts["prefix_adapted"] if adapted else texts["prefix"]
    return f"{prefix} {body}".strip()


def _written_line(
    src: AttributionSource,
    lang: str,
    *,
    adapted: bool,
    max_authors: int = _MAX_AUTHORS_WRITTEN,
) -> str:
    head, tail = _written_parts(src, _TEXTS[lang], max_authors=max_authors)
    return _compose(src, lang, head + tail, adapted=adapted)


# --- Riga compatta per la fascia delle slide e dei frame ----------------------
# Larghezza stimata di un carattere, in em del corpo: stima prudente per i
# font del tema e per i ripieghi del container (DejaVu Sans, il più largo
# fra quelli comuni) e per gli ideogrammi (1 em).
_EM_WIDE = 1.05
_EM_BROAD = 1.0
_EM_UPPER = 0.8
_EM_SPACE = 0.34
_EM_DEFAULT = 0.62
_BROAD_CHARS = frozenset("mwMW@%&")
_ELLIPSIS = "\u2026"
_MAX_BAND_CONTAINER_CHARS = 40
_MIN_BAND_TITLE_CHARS = 16
_MAX_BAND_NAME_CHARS = 32


def text_em(text: str) -> float:
    """Larghezza stimata (in em del corpo) di una riga di testo."""
    total = 0.0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            total += _EM_WIDE
        elif ch in _BROAD_CHARS:
            total += _EM_BROAD
        elif ch.isspace():
            total += _EM_SPACE
        elif ch.isupper() or ch.isdigit():
            total += _EM_UPPER
        else:
            total += _EM_DEFAULT
    return total


def _ellipsize(text: str, max_chars: int) -> str:
    """Taglia a `max_chars` (puntini compresi), a fine parola se possibile."""
    if len(text) <= max_chars:
        return text
    cut = text[: max(1, max_chars - 1)]
    space = cut.rfind(" ")
    if space >= max_chars // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-\u2013\u2014") + _ELLIPSIS


def fitted_written_line(
    src: AttributionSource, *, language: str | None, max_em: float, adapted: bool = False
) -> str:
    """Riga scritta che sta in `max_em` em (fascia delle slide e dei frame).

    Se la riga intera non ci sta accorcia con «…», nell'ordine: contenitore,
    titolo (fino a 16 caratteri), autori (il primo più «et al.»), nome del
    primo autore e nome di ripiego; poi toglie il contenitore. Anno, figura,
    pagina e licenza restano sempre; se ancora non basta taglia la testa.
    """
    lang = _language(language)
    line = _written_line(src, lang, adapted=adapted)
    if text_em(line) <= max_em:
        return line

    def fits(candidate: AttributionSource, max_authors: int = _MAX_AUTHORS_WRITTEN) -> str | None:
        text = _written_line(candidate, lang, adapted=adapted, max_authors=max_authors)
        return text if text_em(text) <= max_em else None

    cur = src
    if cur.container:
        cur = replace(cur, container=_ellipsize(cur.container, _MAX_BAND_CONTAINER_CHARS))
        if found := fits(cur):
            return found
    if cur.title and len(cur.title) > _MIN_BAND_TITLE_CHARS:
        # Il titolo più lungo che ci sta (ricerca binaria sui caratteri).
        lo, hi, best = _MIN_BAND_TITLE_CHARS, len(cur.title) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            found = fits(replace(cur, title=_ellipsize(cur.title, mid)))
            if found:
                best, lo = found, mid + 1
            else:
                hi = mid - 1
        if best:
            return best
        cur = replace(cur, title=_ellipsize(cur.title, _MIN_BAND_TITLE_CHARS))
    max_authors = _MAX_AUTHORS_WRITTEN
    # «et al.» solo con almeno tre autori (Fase D): con due restano entrambi.
    if len(cur.authors) > 2:
        max_authors = 1
        if found := fits(cur, max_authors):
            return found
    if cur.authors:
        # Prenomi in iniziali prima di tagliare: il cognome resta
        # («Maximilian Alexander von Humboldt» → «M. A. von Humboldt»).
        first = _ellipsize(_initials(cur.authors[0]), _MAX_BAND_NAME_CHARS)
        cur = replace(cur, authors=(first, *cur.authors[1:]))
    if cur.credit:
        cur = replace(cur, credit=_ellipsize(cur.credit, _MAX_BAND_NAME_CHARS))
    if cur.fallback_name:
        cur = replace(cur, fallback_name=_ellipsize(cur.fallback_name, _MAX_BAND_NAME_CHARS))
    if found := fits(cur, max_authors):
        return found
    if cur.container:
        cur = replace(cur, container=None)
        if found := fits(cur, max_authors):
            return found
    # Senza il titolo, prima di tagliare la testa a metà (un taglio dentro
    # «…» lasciava le virgolette aperte, Fase D).
    if cur.title and (cur.authors or cur.credit):
        cur = replace(cur, title=None)
        if found := fits(cur, max_authors):
            return found
    # Ultima risorsa: la testa accorciata quanto serve, la coda intera.
    texts = _TEXTS[lang]
    head, tail = _written_parts(cur, texts, max_authors=max_authors)
    joined = ", ".join(head)
    for size in range(len(joined), 0, -1):
        shortened = _ellipsize(joined, size) if size < len(joined) else joined
        text = _compose(cur, lang, [shortened, *tail], adapted=adapted)
        if text_em(text) <= max_em:
            return text
    return _compose(cur, lang, tail, adapted=adapted)


_PARTICLES = frozenset({"de", "di", "da", "del", "della", "van", "von", "der", "la", "le", "du"})


def _initials(name: str) -> str:
    """Prenomi in iniziali, cognome (con le particelle) intero."""
    words = name.split()
    # Già abbreviato o con il cognome davanti («Rossi M.», «Smith J»,
    # «De Luca, Giovanni»): resta com'è.
    if len(words) < 2 or "," in name or re.fullmatch(r"[A-ZÀ-Ý]\.?", words[-1]):
        return name
    # Suffissi (Jr., Sr., II, III) non sono il cognome.
    while len(words) > 2 and re.fullmatch(r"(?:jr|sr)\.?|[ivx]+", words[-1], re.IGNORECASE):
        words = words[:-1]
        name = " ".join(words)
    # Cognome = ultima parola con lettere (un numero o un suffisso dopo resta).
    surname_start = max(
        (i for i, w in enumerate(words) if any(ch.isalpha() for ch in w)), default=0
    )
    while surname_start > 1 and words[surname_start - 1].lower() in _PARTICLES:
        surname_start -= 1
    if surname_start == 0:
        return name
    given = [
        f"{w[0]}." if w[:1].isalpha() and not w.endswith(".") else w for w in words[:surname_start]
    ]
    return " ".join([*given, *words[surname_start:]])


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
    # Prima l'espansione delle abbreviazioni (che hanno il punto), poi il
    # punto finale e il taglio.
    title = _tts_safe(src.title, lang)
    if title:
        title = _strip_final_period(title)
        if len(title) > _MAX_SPOKEN_TITLE:
            title = title[:_MAX_SPOKEN_TITLE].rsplit(" ", 1)[0]
    data: dict[str, Any] = {
        "authors": surnames,
        "more_authors": len(src.authors) > _MAX_SURNAMES_SPOKEN,
        "title": title or None,
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
    if not segments:
        # Niente di pronunciabile in sicurezza: il discorso omette la fonte.
        return ""
    if data["year"]:
        segments.append(str(data["year"]))
    prefix = texts["prefix_adapted"] if adapted else texts["prefix"]
    return f"{prefix} {', '.join(segments)}"


def attribution_line(
    src: AttributionSource,
    *,
    language: str | None,
    mode: AttributionMode = "written",
    adapted: bool = False,
) -> str:
    """Testo della riga «Fonte» (scritta) o della sua variante parlata.

    La variante parlata non contiene pagine, numeri di figura né licenze,
    supera sempre `validate_tts_safety` ed è vuota se nessun dato si può
    pronunciare in sicurezza (il discorso allora omette la fonte).
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
