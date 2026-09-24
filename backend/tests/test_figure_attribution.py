"""Riga «Fonte» delle figure di fonte (`figure_attribution`, garanzia G1).

La riga è calcolata a render da dati deterministici: la proposta del
riassunto LLM non entra mai; la variante parlata supera sempre
`validate_tts_safety`; l'attribuzione congelata allo stacco produce la
stessa riga del documento vivo.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.models.course_document import BIBLIOGRAPHY_SOURCES
from app.models.course_document_figure import FIGURE_LICENSES
from app.schemas.document_bibliography import (
    TRUSTED_BIBLIOGRAPHY_SOURCES,
    DocumentBibliography,
)
from app.services.course_lesson_speech_service import validate_tts_safety
from app.services.figure_attribution import (
    AttributionSource,
    attribution_line,
    attribution_source,
    figure_attribution_line,
    figure_number_from_label,
    freeze_attribution,
    license_label,
    readable_filename,
    spoken_source,
)

_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass
class _Fig:
    source_kind: str = "uploaded"
    page: int | None = 12
    source_label: str | None = "Figura 3.2"
    license: str = "cc_by"
    attribution: dict[str, Any] | None = None


@dataclass
class _Doc:
    filename_original: str = "rossi_2020_laser_vibrometry_a1b2c3.pdf"
    mime_type: str = "application/pdf"
    origin: str = "paper_import"
    is_own_work: bool = False
    bibliography: dict[str, Any] | None = field(
        default_factory=lambda: {
            "title": "Laser Doppler vibrometry.",
            "authors": ["Mario Rossi", "Anna Bianchi"],
            "year": 2020,
            "container": "J. Sound Vib.",
        }
    )
    bibliography_source: str | None = "openalex"


# ---------------------------------------------------------------------------
# Riga scritta
# ---------------------------------------------------------------------------


def test_full_line_in_italian() -> None:
    line = figure_attribution_line(_Fig(), _Doc(), language="it")
    assert line == (
        "Fonte: Mario Rossi e Anna Bianchi, «Laser Doppler vibrometry», "
        "J. Sound Vib., 2020, fig. 3.2, p. 12 (CC BY)"
    )


def test_full_line_in_english_and_regional_code() -> None:
    line = figure_attribution_line(_Fig(), _Doc(), language="en-GB")
    assert line == (
        "Source: Mario Rossi and Anna Bianchi, “Laser Doppler vibrometry”, "
        "J. Sound Vib., 2020, fig. 3.2, p. 12 (CC BY)"
    )


@pytest.mark.parametrize("language", ["de", "zh-cn", None, ""])
def test_other_languages_fall_back_to_italian(language: str | None) -> None:
    line = figure_attribution_line(_Fig(), _Doc(), language=language)
    assert line is not None and line.startswith("Fonte: ")


def test_adapted_prefix() -> None:
    src = attribution_source(_Fig(), _Doc())
    assert src is not None
    assert attribution_line(src, language="it", adapted=True).startswith("Adattato da: ")
    assert attribution_line(src, language="en", adapted=True).startswith("Adapted from: ")


def test_more_than_three_authors_use_et_al() -> None:
    doc = _Doc(bibliography={"title": "T", "authors": ["A Uno", "B Due", "C Tre", "D Quattro"]})
    line = figure_attribution_line(_Fig(page=None, source_label=None), doc, language="it")
    assert line == "Fonte: A Uno et al., «T» (CC BY)"
    three = _Doc(bibliography={"authors": ["A Uno", "B Due", "C Tre"]})
    line3 = figure_attribution_line(_Fig(page=None, source_label=None), three, language="it")
    assert line3 == "Fonte: A Uno, B Due e C Tre (CC BY)"


_BIB_FIELDS = ("authors", "title", "container", "year")
_BIB_VALUES: dict[str, Any] = {
    "authors": ["Mario Rossi"],
    "title": "Titolo",
    "container": "Rivista",
    "year": 2019,
}


@pytest.mark.parametrize(
    "subset",
    [c for r in range(len(_BIB_FIELDS) + 1) for c in itertools.combinations(_BIB_FIELDS, r)],
)
@pytest.mark.parametrize("page", [None, 7])
@pytest.mark.parametrize("label", [None, "Fig. 4"])
def test_every_field_subset_gives_a_clean_line(
    subset: tuple[str, ...], page: int | None, label: str | None
) -> None:
    doc = _Doc(bibliography={k: _BIB_VALUES[k] for k in subset})
    line = figure_attribution_line(
        _Fig(page=page, source_label=label, license="unknown"), doc, language="it"
    )
    assert line is not None
    assert line.startswith("Fonte: ")
    body = line.removeprefix("Fonte: ")
    assert body, "la riga non è mai vuota: ricade sul nome del file"
    for bad in (",,", ", ,", "  ", "..", "()", "None"):
        assert bad not in line, (subset, page, label, line)
    assert not body.endswith(",")
    # Il nome del file compare solo se mancano autori e titolo.
    has_name = "rossi 2020 laser vibrometry" in line
    assert has_name == ("authors" not in subset and "title" not in subset)
    assert ("p. 7" in line) == (page == 7)
    assert ("fig. 4" in line) == (label is not None)


def test_summary_proposal_never_enters_the_line() -> None:
    doc = _Doc(bibliography_source="summary_proposal")
    line = figure_attribution_line(_Fig(), doc, language="it")
    assert line == "Fonte: rossi 2020 laser vibrometry, fig. 3.2, p. 12 (CC BY)"
    assert "Rossi e" not in line and "Laser Doppler" not in line


@pytest.mark.parametrize("source", sorted(BIBLIOGRAPHY_SOURCES))
def test_bibliography_is_used_only_from_trusted_sources(source: str) -> None:
    line = figure_attribution_line(_Fig(), _Doc(bibliography_source=source), language="it")
    assert line is not None
    assert ("Mario Rossi" in line) == (source in TRUSTED_BIBLIOGRAPHY_SOURCES)
    assert (source in TRUSTED_BIBLIOGRAPHY_SOURCES) == (source != "summary_proposal")


def test_missing_bibliography_falls_back_to_filename() -> None:
    doc = _Doc(filename_original="Dispense_Misure_2024.pdf", bibliography=None, origin="upload")
    doc.bibliography_source = None
    line = figure_attribution_line(_Fig(source_label=None), doc, language="it")
    assert line == "Fonte: Dispense Misure 2024, p. 12 (CC BY)"


def test_pptx_uses_slide_instead_of_page() -> None:
    doc = _Doc(mime_type=_PPTX, bibliography=None, filename_original="lezione.pptx")
    doc.bibliography_source = None
    fig = _Fig(page=4, source_label=None)
    assert figure_attribution_line(fig, doc, language="it") == "Fonte: lezione, slide 4 (CC BY)"
    assert figure_attribution_line(fig, doc, language="en") == "Source: lezione, slide 4 (CC BY)"


@pytest.mark.parametrize("license", FIGURE_LICENSES)
def test_every_license_has_a_label_or_is_omitted(license: str) -> None:
    for language in ("it", "en"):
        label = license_label(license, language=language)
        if license in ("unknown", "other"):
            assert label is None
        else:
            assert label, (license, language)
        line = figure_attribution_line(_Fig(license=license), _Doc(), language=language)
        assert line is not None
        assert line.endswith(f"({label})") if label else line.endswith("p. 12")


def test_license_labels_are_localised() -> None:
    assert license_label("public_domain", language="it") == "pubblico dominio"
    assert license_label("public_domain", language="en") == "public domain"
    assert license_label("all_rights_reserved", language="it") == "tutti i diritti riservati"
    assert license_label("cc_by_nc_sa", language="en") == "CC BY-NC-SA"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Figura 3.2", "3.2"),
        ("Fig. 12a", "12a"),
        ("FIGURE 4-1: schema", "4-1"),
        ("Fig.7", "7"),
        ("Abb. 2", "2"),
        ("Tabella 3", None),
        ("Figures", None),
        ("", None),
        (None, None),
    ],
)
def test_figure_number_only_when_read(label: str | None, expected: str | None) -> None:
    assert figure_number_from_label(label) == expected


@pytest.mark.parametrize(
    ("filename", "paper_import", "expected"),
    [
        ("rossi_2020_laser_vibrometry_a1b2c3.pdf", True, "rossi 2020 laser vibrometry"),
        ("anon_ny_paper_0f0f0f.md", True, "anon ny paper"),
        ("Dispense  Misure.docx", False, "Dispense Misure"),
        ("senza_estensione", False, "senza estensione"),
        # Il suffisso si toglie solo ai file importati dalla ricerca paper.
        ("Verbale_esame_202403.pdf", False, "Verbale esame 202403"),
        ("Appunti_decade.pdf", False, "Appunti decade"),
        ("_.pdf", False, None),
        (".pdf", False, None),
        ("_1a2b3c.pdf", True, None),
        ("", False, None),
        (None, False, None),
    ],
)
def test_readable_filename(filename: str | None, paper_import: bool, expected: str | None) -> None:
    assert readable_filename(filename, paper_import=paper_import) == expected


@pytest.mark.parametrize("filename", ["_.pdf", ".pdf", "___.docx"])
def test_document_without_any_name_is_not_attributable(filename: str) -> None:
    """Una riga con la sola pagina non è un'attribuzione: la figura non si
    mostra (`attribution_missing`) invece di uscire con «Fonte: p. 3»."""
    doc = _Doc(filename_original=filename, bibliography=None, origin="upload")
    doc.bibliography_source = None
    assert attribution_source(_Fig(page=3), doc) is None
    assert figure_attribution_line(_Fig(page=3), doc, language="it") is None
    own = _Doc(filename_original=filename, bibliography=None, origin="upload", is_own_work=True)
    own.bibliography_source = None
    assert figure_attribution_line(_Fig(page=3, source_label=None), own, language="it") == (
        "Fonte: materiale del docente, p. 3 (CC BY)"
    )


def test_own_work_without_any_name_says_instructor_material() -> None:
    src = AttributionSource(is_own_work=True, page=2, license="unknown")
    assert attribution_line(src, language="it") == "Fonte: materiale del docente, p. 2"
    assert attribution_line(src, language="en") == "Source: instructor's material, p. 2"


# ---------------------------------------------------------------------------
# Attribuzione congelata (stacco e figure esterne)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["it", "en"])
@pytest.mark.parametrize("mime", ["application/pdf", _PPTX])
def test_frozen_attribution_gives_the_same_line(language: str, mime: str) -> None:
    fig, doc = _Fig(), _Doc(mime_type=mime)
    live = figure_attribution_line(fig, doc, language=language)
    frozen = freeze_attribution(fig, doc)
    assert frozen is not None
    detached = _Fig(attribution=frozen)
    assert figure_attribution_line(detached, None, language=language) == live
    for mode in ("spoken",):
        assert figure_attribution_line(
            detached, None, language=language, mode=mode
        ) == figure_attribution_line(fig, doc, language=language, mode=mode)


@pytest.mark.parametrize(
    "frozen",
    [None, {}, {"foo": 1}, {"year": True}, {"page": 3, "license": "cc_by"}, {"authors": []}],
)
def test_detached_or_external_without_identifying_data_is_not_attributable(
    frozen: dict[str, Any] | None,
) -> None:
    assert attribution_source(_Fig(attribution=frozen), None) is None
    wiki = _Fig(source_kind="wikimedia", attribution=frozen)
    assert figure_attribution_line(wiki, None, language="it") is None


def test_external_figures_are_never_own_work() -> None:
    fig = _Fig(source_kind="openalex", attribution={"is_own_work": True})
    assert attribution_source(fig, None) is None
    fig = _Fig(source_kind="openalex", attribution={"is_own_work": True, "title": "T"})
    src = attribution_source(fig, None)
    assert src is not None and src.is_own_work is False


def test_external_attribution_with_credit() -> None:
    fig = _Fig(
        source_kind="wikimedia",
        page=None,
        source_label=None,
        license="cc_by_sa",
        attribution={"credit": "Jane Doe", "title": "Laser vibrometer diagram.svg"},
    )
    line = figure_attribution_line(fig, None, language="en")
    assert line == "Source: Jane Doe, “Laser vibrometer diagram.svg” (CC BY-SA)"


def test_frozen_license_follows_the_figure_row() -> None:
    # Per le figure staccate valgono i dati congelati (niente etichetta viva)
    # e la licenza della riga della figura.
    fig = _Fig(license="cc0", attribution={"title": "T", "license": "cc_by"})
    assert figure_attribution_line(fig, None, language="it") == "Fonte: «T» (CC0)"


def test_from_json_tolerates_garbage() -> None:
    src = AttributionSource.from_json(
        {"authors": ["", 3, " A  B "], "year": "2020", "page": 0, "page_kind": "x"}
    )
    assert src.authors == ("A B",)
    assert src.year is None and src.page is None and src.page_kind == "page"
    # Un autore come stringa non si scompone in lettere; i booleani non sono
    # numeri; `is_own_work` vale solo se è proprio True.
    odd = AttributionSource.from_json(
        {"authors": "Mario Rossi", "year": True, "page": True, "is_own_work": "false"}
    )
    assert odd.authors == ("Mario Rossi",)
    assert odd.year is None and odd.page is None and odd.is_own_work is False
    assert AttributionSource.from_json({"authors": {"a": 1}}).authors == ()


def test_frozen_form_has_no_empty_keys() -> None:
    frozen = freeze_attribution(_Fig(page=None, source_label=None), _Doc())
    assert frozen is not None
    assert all(v not in (None, [], False, "") for v in frozen.values())
    assert {"title", "authors", "fallback_name"} <= set(frozen)


# ---------------------------------------------------------------------------
# Variante parlata (Fase 5)
# ---------------------------------------------------------------------------

_HOSTILE_TITLES = [
    "Vibrometry, e.g. laser Doppler methods, etc.",
    "Misure ca. 1990: es. i.e. p.es. tutto",
    "The $\\alpha$-decay of `code` and **bold** #tag_name",
    "E.G. UPPERCASE ETC. CA.",
]


@pytest.mark.parametrize("title", _HOSTILE_TITLES)
@pytest.mark.parametrize("language", ["it", "en", "fr"])
def test_spoken_line_is_always_tts_safe(title: str, language: str) -> None:
    doc = _Doc(
        bibliography={
            "title": title,
            "authors": ["Rossi_Mario", "etc. Bianchi", "Verdi"],
            "year": 2001,
        }
    )
    src = attribution_source(_Fig(), doc)
    assert src is not None
    line = attribution_line(src, language=language, mode="spoken")
    assert validate_tts_safety(line) == [], line
    data = spoken_source(src, language=language)
    for value in (data["title"], *data["authors"], data.get("name")):
        if value:
            assert validate_tts_safety(value) == [], value


@pytest.mark.parametrize(
    ("title", "language", "expected", "forbidden"),
    [
        ("Vibrometry, e.g. laser methods, etc.", "it", "eccetera", "etc"),
        ("Vibrometry, e.g. laser methods, etc.", "en", "etcetera", "etc,"),
        ("Misure CA.", "it", "circa", "CA"),
    ],
)
def test_spoken_title_expands_final_abbreviations(
    title: str, language: str, expected: str, forbidden: str
) -> None:
    doc = _Doc(bibliography={"title": title})
    line = figure_attribution_line(_Fig(), doc, language=language, mode="spoken")
    assert line is not None
    assert expected in line, line
    assert f"{forbidden}»" not in line and f"{forbidden}\u201d" not in line, line
    assert validate_tts_safety(line) == []


def test_written_title_keeps_the_period_of_an_abbreviation() -> None:
    doc = _Doc(bibliography={"title": "Methods, etc.", "authors": ["A B"]})
    line = figure_attribution_line(_Fig(page=None, source_label=None), doc, language="it")
    assert line == "Fonte: A B, «Methods, etc.» (CC BY)"


def test_spoken_line_is_empty_when_nothing_is_speakable() -> None:
    src = AttributionSource(title="$\\frac$", fallback_name=None, license="cc_by", page=3)
    assert attribution_line(src, language="it", mode="spoken") == ""
    assert attribution_line(AttributionSource(), language="en", mode="spoken") == ""


def test_spoken_line_has_no_page_figure_or_license() -> None:
    line = figure_attribution_line(_Fig(), _Doc(), language="it", mode="spoken")
    assert line == "Fonte: Rossi e Bianchi, «Laser Doppler vibrometry», 2020"
    assert "p. 12" not in line and "fig." not in line and "CC BY" not in line


def test_spoken_source_limits_authors_and_title() -> None:
    long_title = "parola " * 40
    src = AttributionSource(
        authors=("Mario Rossi", "Bianchi, Anna", "C Verdi"), title=long_title, year=2020
    )
    data = spoken_source(src, language="it")
    assert data["authors"] == ["Rossi", "Bianchi"]
    assert data["more_authors"] is True
    assert len(data["title"]) <= 120
    line = attribution_line(src, language="it", mode="spoken")
    assert "e collaboratori" in line


def test_spoken_source_falls_back_to_name() -> None:
    src = AttributionSource(fallback_name="dispense misure", license="unknown")
    data = spoken_source(src, language="it")
    assert data["authors"] == [] and data["title"] is None
    assert data["name"] == "dispense misure"
    assert attribution_line(src, language="it", mode="spoken") == "Fonte: dispense misure"


# ---------------------------------------------------------------------------
# Schema della bibliografia
# ---------------------------------------------------------------------------


def test_bibliography_schema_forbids_extra_and_drops_blanks() -> None:
    bib = DocumentBibliography(title="  ", authors=["  A  "], doi="", year=2020)
    assert bib.as_json() == {"authors": ["A"], "year": 2020}
    with pytest.raises(ValueError):
        DocumentBibliography.model_validate({"title": "T", "license": "cc_by"})
    with pytest.raises(ValueError):
        DocumentBibliography(authors=[""])
    with pytest.raises(ValueError):
        DocumentBibliography(year=99)
    with pytest.raises(ValueError):
        DocumentBibliography(url="javascript:alert(1)")
    assert DocumentBibliography(url="https://doi.org/10.1/x").url == "https://doi.org/10.1/x"


# ---------------------------------------------------------------------------
# Fase D: credito di terzi, numero di figura, metadati di default
# ---------------------------------------------------------------------------


@dataclass
class _CaptionedFig(_Fig):
    source_caption: str | None = None


def test_third_party_credit_from_the_original_caption_is_in_the_line() -> None:
    """Una figura «Reprinted from … © Elsevier» dentro un articolo CC BY non
    è dell'autore dell'articolo: la riga nomina l'origine."""
    fig = _CaptionedFig(
        source_caption=(
            "Figure 3. Pump schematic. Reprinted from Smith et al. (2010), "
            "© Elsevier, with permission."
        ),
        license="unknown",
    )
    line = figure_attribution_line(fig, _Doc(), language="it")
    assert line.startswith("Fonte: Reprinted from Smith et al. (2010), © Elsevier")
    assert "Mario Rossi" in line and "(CC BY)" not in line
    plain = _CaptionedFig(source_caption="Figura 3.2. Schema del vibrometro.")
    assert "Reprinted" not in figure_attribution_line(plain, _Doc(), language="it")


def test_an_absurd_figure_number_is_not_a_number() -> None:
    assert figure_number_from_label("Figura 3.2") == "3.2"
    assert figure_number_from_label("Fig. 1.2.3.4.5.6.7.8.9.10.11.12.13") is None


@pytest.mark.parametrize(
    ("caption", "credit"),
    [
        (
            "Figure 3. Pump schematic. Reprinted from Smith et al. (2010), © Elsevier, "
            "with permission.",
            "Reprinted from Smith et al. (2010), © Elsevier, with permission",
        ),
        ("Fig. 3. (a) Setup, (b) signal, (c) spectrum.", None),  # pannelli, non ©
        ("Figure 4. Diagram (c) 2019 Elsevier", "© 2019 Elsevier"),
        ("Figura 2. Schema. Fonte: elaborazione propria", None),  # materiale proprio
        ("Figura 2. Schema. Fonte: Rossi 2019", "Rossi 2019"),  # niente «Fonte: Fonte:»
        ("Figura 5. Schema (Rossi et al., 2019).", "Rossi et al., 2019"),
        ("Figure 1. Transformer. Sources: primary and secondary windings", None),
        ("Figure 2. Energy sources: solar and wind", None),
        ("図1。構成。出典\uff1a日本機械学会", "日本機械学会"),  # due punti a larghezza piena
    ],
)
def test_third_party_credit(caption: str, credit: str | None) -> None:
    """Fase D (confutatore): etichette di pannello, materiale proprio e code
    senza un nome non sono crediti di terzi."""
    from app.services.source_caption import third_party_credit

    assert third_party_credit(caption) == credit


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("Maximilian Alexander von Humboldt", "M. A. von Humboldt"),
        ("Rossi M.", "Rossi M."),
        ("Smith J", "Smith J"),
        ("De Luca, Giovanni", "De Luca, Giovanni"),
        ("Mario Rossi Jr.", "M. Rossi"),
    ],
)
def test_initials_keep_the_surname(name: str, short: str) -> None:
    from app.services.figure_attribution import _initials

    assert _initials(name) == short
