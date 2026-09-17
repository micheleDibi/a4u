"""Box della figura per pagina nelle slide e nei frame video (D12, WP3b).

`slide_geometry` è il mirror delle costanti CSS di `lesson_slides_pdf.html.j2`
e calcola, per ogni pagina resa, il budget verticale di ogni blocco asset
(`page_figure_budget`), il box dell'immagine al netto della didascalia
reale (`image_box`) e la troncatura del sorgente di fallback
(`truncate_fallback_source`). Oracoli:

- fixture `slide_figure_box_cases.json` (budget, box, troncatura, stime);
- calibrazione dello stimatore di righe sui `LineBox` reali di WeasyPrint
  per sei famiglie (`real ≤ stima ≤ real + 1`);
- `<pre>` di fallback senza a capo (`white-space: pre`, settimo giro): per
  il corpus avversario della fixture (regole UAX #14 dei giri 6 e 7, tab,
  spazi, righe vuote, una riga da 25.000 caratteri, JSON minificato, 200
  righe corte, CJK, kana, Hangul, ebraico, arabo, devanagari, kannada,
  tibetano, mongolo, emoji, separatori e controlli, riga DOT reale,
  ideogrammi, kana e Hangul in testa del settimo giro di verifica) in 13
  lingue le righe rese sono ESATTAMENTE quelle stimate nei `LineBox` di
  WeasyPrint e nelle righe di Chromium, l'altezza stimata copre quella
  resa, in WeasyPrint riga per riga; controprova con la regola `pre-wrap`
  di prima, che sottostima; l'insieme base resta su righe da 1,3 em;
- prima run ideografica (ottavo giro): righe a 2,46 em, classificatore
  contro gli script di GLib su tutto Unicode, controprova con il modello
  di prima (righe oltre la stima e sotto il clip);
- riga base più alta come nel container con una lingua CJK (emulata con
  STIXNonUni davanti a DejaVu Sans Mono): con la lingua giusta il
  marcatore «…» resta nel `<pre>`, con il profilo neutro l'ultima riga
  esce dal box (controprova);
- pin a regex delle costanti sul template (chi ritocca il CSS senza il
  mirror, o viceversa, rompe il test) e riscontro del modello additivo con
  la geometria resa;
- lezione di prova da 14 pagine resa da WeasyPrint (`.render()` +
  camminata sui box): nessun `div.slide-asset` sotto il fondo del
  `.slide-body`, nessuna immagine sopra `--figure-h`, `<pre>` entro le
  righe che entrano, log di clamp/condivisione/troncatura; controprova con
  il cap 80 mm di prima, che DEVE sbordare;
- stesso HTML nei frame video (Chromium con `_VIDEO_OVERRIDE_CSS`, skip
  solo su `chromium.launch()`), 1 slide JSON → 1 pagina senza split;
- slide con il fallback del corpus in 13 lingue: righe esatte nei due
  motori, nessuna riga più alta della sua stima, nessun asset sotto il
  body, didascalia nella pagina, marcatore «…» visibile quando il
  sorgente è troncato, nessun inchiostro oltre il bordo destro del body
  (le righe lunghe sono tagliate); controprove con `pre-wrap` (sbordo di
  55 mm e didascalia persa sui casi del sesto giro) e senza `overflow:
  hidden` (testo oltre il bordo); slide di un corso in giapponese
  troncata a 18 righe più «…».
"""

from __future__ import annotations

import base64
import io
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog.testing

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import figure_render_service as frs
from app.services import lesson_slides_video_render_service as video
from app.services import mermaid_prerender as mp
from app.services import slide_geometry as sg
from app.services.figure_markup import FigureBox

_FIXTURES = Path(__file__).parent / "fixtures"
_TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "lesson_slides_pdf.html.j2"
_CASES = json.loads((_FIXTURES / "slide_figure_box_cases.json").read_text(encoding="utf-8"))
_MM = 96.0 / 25.4
_TOL = 0.05
_FAMILIES = ("Helvetica", "Arial", "Verdana", "Noto Sans", "Liberation Sans", "DejaVu Sans")
_G = sg.DEFAULT_GEOMETRY

_FIGURE_H_RE = re.compile(r"--figure-h:\s*([\d.]+)mm")


# ---------------------------------------------------------------------------
# Fixture: budget, box, troncatura, stime
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _CASES["pages"], ids=[c["name"] for c in _CASES["pages"]])
def test_page_budget_cases(case: dict[str, Any]) -> None:
    got = sg.page_figure_budget(
        title=case["title"], body=case["body"], bullets=case["bullets"], n_blocks=case["n_blocks"]
    )
    exp = case["expected"]
    assert got.w_mm == 255.0
    assert (got.block_h_mm, got.available_mm, got.n_blocks, got.clamped) == (
        exp["block_h_mm"],
        exp["available_mm"],
        exp["n_blocks"],
        exp["clamped"],
    )
    assert got.clamped is (got.available_mm < _G.min_block_h_mm)
    assert got.block_h_mm >= round(_G.min_block_h_mm, 3)


@pytest.mark.parametrize("case", _CASES["captions"], ids=[c["name"] for c in _CASES["captions"]])
def test_image_box_cases(case: dict[str, Any]) -> None:
    block_h = case["block_h_mm"]
    budget = sg.PageFigureBudget(255.0, block_h, block_h, 1, block_h < _G.min_block_h_mm)
    box, squeezed = sg.image_box(budget, caption_text=case["caption_text"])
    exp = case["expected"]
    assert (box.w_mm, box.h_mm, squeezed) == (exp["w_mm"], exp["h_mm"], exp["squeezed"])
    assert box.h_mm >= _G.figure_min_h_mm
    assert box.h_mm * 10 == int(box.h_mm * 10 + 1e-9)  # al decimo


def test_figure_box_rounds_and_rejects_non_positive_values() -> None:
    assert FigureBox(255, 86.633).style == "--figure-w: 255.0mm; --figure-h: 86.6mm"
    assert FigureBox(255, 86.66).h_mm == 86.7
    for w, h in ((0, 10), (255, -1), (-1, -1)):
        with pytest.raises(ValueError):
            FigureBox(w, h)


@pytest.mark.parametrize("case", _CASES["fallback"], ids=[c["name"] for c in _CASES["fallback"]])
def test_fallback_truncation_cases(case: dict[str, Any]) -> None:
    box = FigureBox(255.0, case["box_h_mm"])
    exp = case["expected"]
    language = case.get("language")
    assert sg.fallback_lines_that_fit(box) == exp["fit"]
    text, omitted = sg.truncate_fallback_source(case["source"], box=box, language=language)
    assert (text, omitted) == (exp["text"], exp["omitted"])
    # Righe rese e altezza del testo tenuto: le righe tenute più il
    # marcatore entrano nel box (padding e bordi esclusi).
    rows, height = sg.fallback_source_rows(text, language=language)
    assert (rows, round(height, 3)) == (exp["rows"], exp["height_mm"])
    assert height <= box.h_mm - _G.fallback_chrome_mm + 1e-9
    lines = text.split("\n")
    source_lines = _source_lines(case["source"])
    # Nessun separatore diverso da `\n` arriva al `<pre>`: i due motori
    # rendono le stesse righe.
    assert _SOURCE_BREAK_RE.sub("", text.replace("\n", "")) == text.replace("\n", "")
    assert rows == len(lines)
    if omitted:
        assert lines[-1] == sg.TRUNCATION_MARK and len(lines) - 1 == exp["kept_lines"]
        assert lines[:-1] == source_lines[: exp["kept_lines"]]
        assert omitted == len(source_lines) - exp["kept_lines"]
    else:
        assert lines == source_lines and sg.TRUNCATION_MARK not in text
        assert exp["kept_lines"] == len(source_lines)


# A capo del sorgente di fallback: `\n`, `\r\n`, `\r` e, come in Pango,
# U+2028 e U+2029; i NUL non arrivano al DOM.
_SOURCE_BREAK_RE = re.compile("\r\n|[\r\n\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]")


def _source_lines(source: str) -> list[str]:
    return _SOURCE_BREAK_RE.split(source.replace("\x00", ""))


@pytest.mark.parametrize(
    ("language", "profile"),
    [
        (None, "neutral"),
        ("", "neutral"),
        ("it", "neutral"),
        ("en-US", "neutral"),
        ("pt_BR", "neutral"),
        ("ru", "neutral"),
        ("ar", "neutral"),
        ("el", "neutral"),
        ("mn", "neutral"),
        ("ja", "other"),
        ("ko", "other"),
        ("zh", "other"),
        ("zh-CN", "other"),
        ("zh_TW", "other"),
        ("zh-hk", "other"),
        ("zh-sg", "other"),
        ("yue", "other"),
        ("hi", "other"),
        ("kn", "other"),
        ("bo", "other"),
        ("th", "other"),
        ("he", "other"),
        ("vi", "neutral"),
        ("vi-VN", "neutral"),
        ("lo", "other"),
        ("ber-MA", "other"),
        ("mn-cn", "other"),
        ("pa-PK", "other"),
        ("fil", "other"),
        ("sh", "other"),
        ("ps", "neutral"),
        ("ps-AF", "other"),
        ("ti", "neutral"),
        ("ti-ER", "other"),
        ("ti_et", "other"),
    ],
)
def test_mono_language_profiles(language: str | None, profile: str) -> None:
    """Profilo del `<pre>` per lingua del corso: neutra dove, nel
    container, `fc-match monospace:lang=xx` dà DejaVu Sans Mono e per vi
    (Noto Sans Mono, righe da 1,3 em sull'insieme base meno greco,
    cirillico, ∏, ∑ e ∫); le altre (riga base più alta come ja, ko e zh,
    insieme base fuori modello come sh, tag con regione come ps-af e
    ti-er) e i codici di tre lettere pagano la riga alta."""
    assert sg._mono_profile(language) == profile
    assert sg._mono_lang(language).profile == profile
    assert sg._mono_lang(language).is_base("n0 -> n1;") is (profile == "neutral")


def test_mono_tall_row_of_mn_cn() -> None:
    """mn-cn: riga base in Noto Sans Mongolian (A−D 1,164); con kannada o
    tibetano (A−D 0,200 e 0,117) la riga resa è alta 4,40-4,50 mm, oltre
    il budget comune di 1,70 em (4,198 mm): la lingua paga 1,83 em
    (4,519 mm), e così ogni riga con mongolo tradizionale in qualunque
    corso (bo e kn con mongolo: 4,50 e 4,40 mm). tcy (ideogrammi accanto
    a Noto Serif Kannada, 4,317 mm) paga 1,76 em. Le altre righe restano
    a 1,70 em; con la prima run ideografica la riga vale 2,46 em anche in
    tcy e mn-cn (ottavo giro)."""
    unit = _G.fallback_pt * sg.MM_PER_PT
    line = "ಸಂವೇದಕ --> ಸಂಕೇತ བོད་ཡིག"
    assert sg.fallback_source_rows(line, language="mn-CN") == (1, pytest.approx(1.83 * unit))
    assert sg.fallback_source_rows(line, language="mn-cn")[1] >= 4.503
    assert sg.fallback_source_rows("n0 传感器 --> 信号", language="tcy") == (
        1,
        pytest.approx(1.76 * unit),
    )
    assert sg.fallback_source_rows("n0 传感器 --> 信号", language="tcy")[1] >= 4.317
    for language in ("tcy", "mn-cn"):
        assert sg.fallback_source_rows("传感器 --> 信号", language=language) == (
            1,
            pytest.approx(2.46 * unit),
        )
    for language in ("mn", "kn", "bo", "it", "ja"):
        assert sg.fallback_source_rows(line, language=language)[1] == pytest.approx(1.7 * unit)
        # Con il mongolo tradizionale la riga è alta come in mn-cn.
        assert sg.fallback_source_rows("ᠮᠣᠩᠭᠣᠯ " + line, language=language)[1] == pytest.approx(
            1.83 * unit
        )
    text, omitted = sg.truncate_fallback_source(
        "x\n" * 40, box=FigureBox(255.0, 86.6), language="mn-cn"
    )
    # 82,32 mm utili = 33,33 em: marcatore 1,83, poi 17 righe da 1,83.
    assert (text.count("\n"), omitted) == (17, 24)


def test_mono_vietnamese_base_exclusions() -> None:
    """vi: righe da 1,3 em per l'insieme base salvo greco, cirillico, ∏, ∑
    e ∫ (altro font, righe da 3,314 mm), che valgono come i caratteri fuori
    dall'insieme. ⇐, ⇒, ⇔ e ∅ (1,2 em, righe da 3,210 mm) erano esclusi
    solo per la larghezza: senza a capo restano a 1,3 em."""
    unit = _G.fallback_pt * sg.MM_PER_PT
    assert sg.fallback_source_rows("n0 -> n1; àèìòù ←→ ┌─┐ … ⇐⇒⇔∅", language="vi") == (
        1,
        pytest.approx(1.3 * unit),
    )
    for ch in "ΑωЖя∏∑∫":
        assert sg.fallback_source_rows(f"n0 {ch} n1", language="vi") == (
            1,
            pytest.approx(1.7 * unit),
        ), hex(ord(ch))
        assert sg.fallback_source_rows(f"n0 {ch} n1", language="it")[1] == pytest.approx(1.3 * unit)
    assert sg.fallback_source_rows("⇒" * 150, language="vi") == (1, pytest.approx(1.3 * unit))
    text, omitted = sg.truncate_fallback_source(
        "x\n" * 40, box=FigureBox(255.0, 86.6), language="vi"
    )
    assert (text.count("\n"), omitted) == (24, 17)


def test_mono_base_set_and_tall_rows() -> None:
    """Insieme base: ASCII, Latin-1, Latin Extended-A, greco e cirillico di
    base, punteggiatura, frecce, operatori e filetti d'uso comune; fuori
    i caratteri che nel container o in locale cadono su un altro font
    (U+01C6, U+0460, U+2016, U+2222, U+2318, CJK, emoji) e i controlli.
    Una riga fuori dall'insieme, o con lingua non neutra, vale 1,70 em; una
    riga sorgente è sempre UNA riga resa, qualunque sia la sua lunghezza."""
    base = (
        "aZ09 ~\t\xa0àèìòùßÆœ ΑΩαωάώ ЖЯжяЁ –—\N{LEFT SINGLE QUOTATION MARK}"
        "\N{RIGHT SINGLE QUOTATION MARK}“”„†•…‰\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}"
        "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}€"
        " ←→↔⇐⇒⇔ ∀∂∃∅∇∈∑−√∞∫≈≠≡≤≥⊂⊆ ┌─┐│└┘"
    )
    assert sg._MONO_BASE_RE.fullmatch(base)
    outside = ("ǆ", "Ѡ", "‖", "∢", "⌘", "分", "🚀", "\x85", "\x0c", "\x0b")
    for ch in outside:
        assert not sg._MONO_BASE_RE.fullmatch(ch), hex(ord(ch))
    unit = _G.fallback_pt * sg.MM_PER_PT
    assert _G.fallback_tall_line_mm == pytest.approx(4.1981, abs=1e-4)
    assert sg.fallback_source_rows("n0 -> n1;") == (1, pytest.approx(1.3 * unit))
    assert sg.fallback_source_rows("n0 -> n1;", language="ja") == (1, pytest.approx(1.7 * unit))
    assert sg.fallback_source_rows("n0 -> 分;") == (1, pytest.approx(1.7 * unit))
    ls, ps = "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}"
    assert sg.fallback_source_rows(f"a{ls}b{ps}c\nd") == (4, pytest.approx(5.2 * unit))
    assert sg.fallback_source_rows("a\r\nb\rc\n") == (4, pytest.approx(5.2 * unit))
    # NEL, FF e VT non vanno a capo in nessun motore: la riga resta una,
    # alta perché fuori dall'insieme base.
    assert sg.fallback_source_rows("a\x85b\x0cc\x0bd") == (1, pytest.approx(1.7 * unit))
    # I NUL non arrivano al DOM.
    assert sg.fallback_source_rows("\x00\nx\n\x00") == (3, pytest.approx(3.9 * unit))
    assert sg.fallback_source_rows("") == (1, pytest.approx(1.3 * unit))
    # Nessuna larghezza: righe lunghe, spazi, tab e token senza spazi
    # valgono una riga (tagliata a destra).
    for line in (
        "x" * 25_000,
        "ab " * 5_000,
        " " * 1_000,
        "\t" * 200 + "ab",
        ("voce " + ". " * 40) * 48,
        "分" * 3_000,
        "ண " * 500,
    ):
        assert sg.fallback_source_rows(line)[0] == 1, line[:20]
    # La troncatura paga anche il marcatore alla riga alta.
    box = FigureBox(255.0, 86.6)
    assert sg.fallback_lines_that_fit(box) == 25
    text, omitted = sg.truncate_fallback_source("x\n" * 40, box=box, language="ja")
    assert (text.count("\n"), omitted) == (18, 23)


def test_ideographic_first_run_pays_the_shifted_row() -> None:
    """Ottavo giro (V7-1): Pango allinea le run di font diversi sulla
    baseline dello script della prima run; con uno script ideografico in
    testa (Han, Hangul, kana, Bopomofo, …) la riga resa arriva a 2,293 em
    nel container. Decide il primo carattere con script reale: spazi,
    cifre, punteggiatura, emoji, PUA, segni combinanti, modificatori e
    alfanumerici matematici non decidono; una lettera latina, greca,
    araba, devanagari decide per la baseline romana o sospesa, senza
    spostamento (1,70 em). Le righe base non cambiano."""
    unit = _G.fallback_pt * sg.MM_PER_PT
    assert _G.fallback_ideo_line_budget == 2.46
    assert _G.fallback_ideo_line_mm == pytest.approx(6.0748, abs=1e-4)
    ideographic = (
        '    藏文0["བོད་ཡིག་ 0"] --> 汉字0',
        '    \u30ce\u30fc\u30c90["မြန်မာ 0"] --> \u30ce\u30fc\u30c91',
        '    단계0["\uf8ff 아이콘 0"] --> 단계1',
        "    🚀 分析",
        "1分",
        "[分] x",
        "ʰ分",
        "𝐀分",
        "\u0301分",
        "\uf8ff\U000e0100한",
        "ー分",
        "ㄅ x",
        "\U00017000 x",
        "\U0001b170 x",
        "\U00018b00 x",
        "\U00020000 x",
    )
    roman = (
        '    n0["藏文 བོད་ཡིག་ 0"] --> 汉字0',
        "x分",
        "ªb分",
        "Ω分",
        "Ж分",
        "سینسر 分",
        "कख 分",
        "བོད分",
        "Ａ分",
        "    🚀 ∑ ✅",
        "",
    )
    for line in ideographic:
        assert sg._ideographic_lead(line), ascii(line)
        assert sg.fallback_source_rows(line, language="it") == (1, pytest.approx(2.46 * unit))
        assert sg.fallback_source_rows(line, language="mn-cn")[1] == pytest.approx(2.46 * unit)
    for line in roman:
        assert not sg._ideographic_lead(line), ascii(line)
        assert sg.fallback_source_rows(line, language="bo")[1] <= 1.83 * unit + 1e-9
    # La riga base resta a 1,3 em, anche senza caratteri che decidono.
    assert sg.fallback_source_rows("    --> ; 1", language="it") == (1, pytest.approx(1.3 * unit))
    # Il costo non dipende dalle righe vicine (Pango itemizza ogni riga).
    rows, height = sg.fallback_source_rows("    -->\n分析\nx 分", language="it")
    assert (rows, height) == (3, pytest.approx((1.3 + 2.46 + 1.7) * unit))
    # Troncatura in bo: «flowchart LR» e il marcatore a 1,70, le righe con
    # ideogrammi in testa a 2,46 (12 righe); con l'id ASCII in testa 17.
    box = FigureBox(255.0, 86.6)
    cjk = "flowchart LR\n" + "\n".join(f'    藏文{i}["བོད་ཡིག་"] --> 汉字{i}' for i in range(40))
    text, omitted = sg.truncate_fallback_source(cjk, box=box, language="bo")
    assert (text.count("\n"), omitted) == (13, 28)
    ascii_lead = cjk.replace("    藏文", "    n")
    text, omitted = sg.truncate_fallback_source(ascii_lead, box=box, language="bo")
    assert (text.count("\n"), omitted) == (18, 23)


# Script con baseline ideografica in HarfBuzz
# (`hb_ot_layout_get_horizontal_baseline_tag_for_script`), codici ISO 15924.
_IDEO_ISO = frozenset(("Hani", "Hang", "Hira", "Kana", "Bopo", "Tang", "Nshu", "Kits"))
_NOT_REAL_ISO = frozenset(("Zyyy", "Zinh", "Zzzz"))


def _glib() -> Any:
    """La GLib caricata da Pango (stessa tabella Unicode degli script)."""
    import ctypes

    _weasyprint()
    for name in ("libglib-2.0.so.0", "libglib-2.0.0.dylib", "libglib-2.0.dylib", "glib-2.0-0"):
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.g_unichar_get_script.argtypes = [ctypes.c_uint32]
        lib.g_unichar_get_script.restype = ctypes.c_int
        lib.g_unicode_script_to_iso15924.argtypes = [ctypes.c_int]
        lib.g_unicode_script_to_iso15924.restype = ctypes.c_uint32
        return lib
    raise AssertionError("GLib non caricabile: WeasyPrint la carica con Pango")


def test_ideographic_lead_tables_follow_glib_scripts() -> None:
    """Le tabelle del classificatore contro gli script di GLib, quelli che
    Pango usa per le run, su tutto Unicode: ogni carattere con script a
    baseline ideografica è in `_IDEO_SCRIPT_RE`; ogni carattere che decide
    per la baseline romana (lettera fuori da `_IDEO_SCRIPT_RE` e da
    `_NOT_REAL_LETTER_RE`) ha uno script reale non ideografico."""
    import unicodedata

    glib = _glib()
    get_script = glib.g_unichar_get_script
    to_iso = glib.g_unicode_script_to_iso15924
    iso: dict[int, str] = {}
    missing: list[str] = []
    wrong: list[str] = []
    ideographic = 0
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        code = get_script(cp)
        tag = iso.get(code)
        if tag is None:
            tag = iso[code] = to_iso(code).to_bytes(4, "big").decode("ascii")
        ch = chr(cp)
        is_ideo = sg._IDEO_SCRIPT_RE.match(ch) is not None
        if tag in _IDEO_ISO:
            ideographic += 1
            if not is_ideo:
                missing.append(f"{cp:04X} {tag}")
        elif (
            not is_ideo
            and unicodedata.category(ch).startswith("L")
            and sg._NOT_REAL_LETTER_RE.match(ch) is None
            and tag in _NOT_REAL_ISO
        ):
            wrong.append(f"{cp:04X} {tag}")
    assert ideographic > 110_000, ideographic
    assert missing == [] and wrong == [], (missing[:20], wrong[:20])


def test_truncation_rewrites_separators_and_drops_nul() -> None:
    """Il testo del `<pre>` ha solo `\\n` come a capo, anche quando tutto
    entra: U+2028 e U+2029 (a capo in WeasyPrint, non in Chromium), CRLF e
    CR diventano `\\n`; i NUL spariscono (il parser HTML li scarta e, in
    testa al `<pre>`, scarterebbe anche l'a capo che li segue)."""
    box = FigureBox(255.0, 86.6)
    ls, ps = "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}"
    source = f"a{ls}b{ps}c\r\nd\re\x85f\x00g\n"
    assert sg.truncate_fallback_source(source, box=box) == ("a\nb\nc\nd\ne\x85fg\n", 0)
    assert sg.truncate_fallback_source("\x00\nx", box=box) == ("\nx", 0)
    assert sg.truncate_fallback_source("n0 -> n1;\nn1 -> n2;", box=box) == (
        "n0 -> n1;\nn1 -> n2;",
        0,
    )


_REMOVED_WIDTH_MODEL = (
    "_EM_MONO",
    "_EM_MONO_OTHER",
    "_EM_MONO_WIDE_SCRIPT",
    "_MONO_TAB_COLS",
    "_MONO_TAB_MAX_COLS",
    "_MONO_CJK",
    "_MONO_CJK_TAGS",
    "_MONO_CJK_FULL_RE",
    "_MONO_WIDE_SCRIPT_RE",
    "_MONO_WIDE_ASCII_TAGS",
    "_MONO_WIDE_ASCII_LANGS",
    "_MONO_ASCII_CROSS",
    "_MONO_ASCII_CROSS_RE",
    "_WIDE_ASCII_ALL",
    "_WIDE_ASCII_DEJAVU_SANS",
    "_WIDE_ASCII_INDIC",
    "_WIDE_ASCII_MN_CN",
    "_WIDE_ASCII_ZH_SG",
    "_PRE_TOKEN_RE",
    "_HAN_BREAK_RE",
    "_mono_rows",
    "_mono_char_em",
    "MM_PER_CSS_PX",
)


def test_the_wrap_width_model_is_gone() -> None:
    """Il `<pre>` non va a capo: la macchina delle larghezze mono non ha più
    consumatori e non esiste più (né `estimate_lines(mono=True)`)."""
    assert [name for name in _REMOVED_WIDTH_MODEL if hasattr(sg, name)] == []
    assert not hasattr(_G, "fallback_w_mm")
    assert not hasattr(sg._MonoLang(), "wide_ascii")
    with pytest.raises(TypeError):
        sg.estimate_lines("x", font_pt=7, width_mm=248, mono=True)  # type: ignore[call-arg]


_STYLE_ARGS: dict[str, dict[str, Any]] = {
    "title": {"font_pt": _G.title_pt, "width_mm": _G.body_w_mm, "bold": True},
    "bullet": {"font_pt": _G.bullet_pt, "width_mm": _G.bullet_w_mm},
    "body": {"font_pt": _G.body_text_pt, "width_mm": _G.text_w_mm},
    "caption": {"font_pt": _G.caption_pt, "width_mm": _G.body_w_mm},
}


@pytest.mark.parametrize(
    "case", _CASES["lines"], ids=[f"{c['style']}:{c['text'][:24]}" for c in _CASES["lines"]]
)
def test_estimated_lines_are_pinned(case: dict[str, Any]) -> None:
    assert sg.estimate_lines(case["text"], **_STYLE_ARGS[case["style"]]) == case["estimated"]


def test_estimate_lines_edge_cases() -> None:
    assert sg.estimate_lines("", font_pt=28, width_mm=255) == 0
    assert sg.estimate_lines("   \n\t ", font_pt=28, width_mm=255) == 0
    assert sg.estimate_lines("a  b\n\nc", font_pt=28, width_mm=255) == 1  # spazi collassati
    # Parola più larga della riga: ceil(larghezza/riga), senza una riga in più.
    assert sg.estimate_lines("x" * 60, font_pt=28, width_mm=255, bold=True) == 2
    assert sg.estimate_lines("分散" * 20, font_pt=28, width_mm=255, bold=True) == 2


# ---------------------------------------------------------------------------
# WeasyPrint: helper
# ---------------------------------------------------------------------------


def _weasyprint() -> Any:
    try:
        import weasyprint
    except (ImportError, OSError) as exc:  # su macOS serve DYLD_FALLBACK_LIBRARY_PATH
        pytest.skip(f"weasyprint non importabile: {exc}")
    return weasyprint


def _walk(box: Any) -> Iterator[Any]:
    yield box
    children = getattr(box, "all_children", None)
    for child in children() if children else getattr(box, "children", []):
        yield from _walk(child)


def _classes(box: Any) -> list[str]:
    try:
        return ((box.element.get("class") or "") if box.element is not None else "").split()
    except Exception:  # box anonimi senza elemento
        return []


def _is_block(box: Any) -> bool:
    # `.slide-body` (position: absolute) è un `AbsolutePlaceholder` che
    # delega al suo BlockBox.
    return type(box).__name__ in ("BlockBox", "AbsolutePlaceholder")


def _line_boxes(box: Any) -> int:
    return sum(1 for b in _walk(box) if type(b).__name__ == "LineBox")


def _bottom_mm(box: Any) -> float:
    return (box.position_y + box.margin_height()) / _MM


# ---------------------------------------------------------------------------
# Calibrazione dello stimatore sui LineBox reali
# ---------------------------------------------------------------------------

_STYLE_CSS = {
    "title": (
        "font-size: 28pt; font-weight: 700; line-height: 1.05; letter-spacing: -0.01em; "
        "width: 255mm;"
    ),
    "bullet": "font-size: 11pt; line-height: 1.35; width: 248mm;",
    "body": "font-size: 13pt; line-height: 1.45; max-width: 72ch; width: 255mm;",
    "caption": "font-size: 8pt; width: 255mm; text-align: center;",
}


def _real_lines(weasyprint: Any, family: str, style: str, text: str) -> int:
    from markupsafe import escape

    html = (
        "<html><head><style>@page { size: A4 landscape; margin: 0 } "
        f'body {{ margin: 0; font-family: "{family}"; }} .t {{ {_STYLE_CSS[style]} }}'
        f'</style></head><body><div id="probe" class="t">{escape(text)}</div></body></html>'
    )
    total = 0
    for page in weasyprint.HTML(string=html).render().pages:
        for box in _walk(page._page_box):
            if _is_block(box) and box.element is not None and box.element.get("id") == "probe":
                total += _line_boxes(box)
    return total


@pytest.mark.parametrize("family", _FAMILIES)
def test_estimated_lines_are_an_upper_bound(family: str) -> None:
    """`real ≤ stima ≤ real + 1` sui `LineBox` reali per ogni testo della
    fixture (22 titoli fra cui uno tutto in maiuscolo, uno CJK e due a
    simboli larghi, 6 bullet, 4 prose, 4 didascalie): una sottostima
    taglierebbe la figura, una sovrastima di due righe la rimpicciolirebbe
    inutilmente. Liberation Sans e DejaVu Sans sono i font del container;
    in locale si risolvono a proxy (fontconfig)."""
    weasyprint = _weasyprint()
    for case in _CASES["lines"]:
        real = _real_lines(weasyprint, family, case["style"], case["text"])
        est = case["estimated"]
        assert real <= est <= real + 1, (family, case["style"], case["text"][:40], real, est)


# ---------------------------------------------------------------------------
# Mirror delle costanti sul template
# ---------------------------------------------------------------------------


def _css() -> str:
    """CSS del template senza commenti e con le espressioni Jinja
    (`{{ tpl.text_color }}`) neutralizzate: le loro graffe chiuderebbero
    le regole prima del tempo."""
    raw = _TEMPLATE.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    return re.sub(r"\{[{%].*?[}%]\}", "JINJA", css, flags=re.S)


def _rule(css: str, selector: str) -> str:
    assert selector in css, selector
    return css.split(selector, 1)[1].split("}", 1)[0]


def _prop(block: str, name: str) -> str:
    m = re.search(rf"(?m)^\s*{re.escape(name)}:\s*([^;]+);", block)
    assert m, name
    return m.group(1).strip()


def test_geometry_constants_mirror_the_template_css() -> None:
    css = _css()
    slide = _rule(css, "\n    .slide {")
    assert (_prop(slide, "width"), _prop(slide, "height")) == (
        f"{_G.page_w_mm:g}mm",
        f"{_G.page_h_mm:g}mm",
    )
    body = _rule(css, ".slide-body {")
    assert [_prop(body, k) for k in ("left", "right", "top", "bottom")] == [
        f"{_G.body_left_mm:g}mm",
        f"{_G.body_right_mm:g}mm",
        f"{_G.body_top_mm:g}mm",
        f"{_G.body_bottom_mm:g}mm",
    ]
    tag = _rule(css, ".slide-tag {")
    assert _prop(tag, "font-size") == f"{_G.tag_pt:g}pt" and "line-height" not in tag
    title = _rule(css, ".slide-title {")
    assert _prop(title, "margin") == f"{_G.title_margin_top_mm:g}mm 0 0"
    assert _prop(title, "font-size") == f"{_G.title_pt:g}pt"
    assert _prop(title, "line-height") == f"{_G.title_line_height:g}"
    text = _rule(css, ".slide-body-text {")
    assert _prop(text, "margin") == f"{_G.body_text_margin_top_mm:g}mm 0 0"
    assert _prop(text, "font-size") == f"{_G.body_text_pt:g}pt"
    assert _prop(text, "line-height") == f"{_G.body_text_line_height:g}"
    assert _prop(text, "max-width") == f"{_G.body_text_max_width_ch}ch"
    bullets = _rule(css, ".slide-bullets {")
    assert _prop(bullets, "margin") == f"{_G.bullets_margin_top_mm:g}mm 0 0"
    li = _rule(css, ".slide-bullets li {")
    assert _prop(li, "gap") == f"{_G.bullet_gap_mm:g}mm"
    assert _prop(li, "font-size") == f"{_G.bullet_pt:g}pt"
    assert _prop(li, "line-height") == f"{_G.bullet_line_height:g}"
    assert _prop(li, "margin") == f"0 0 {_G.bullet_margin_bottom_mm:g}mm"
    dot = _rule(css, ".slide-bullets li::before {")
    assert _prop(dot, "width") == f"{_G.bullet_dot_mm:g}mm"
    assert _prop(_rule(css, ".slide-assets {"), "margin-top") == f"{_G.assets_margin_top_mm:g}mm"
    assert _prop(_rule(css, "\n    .slide-asset {"), "margin") == f"{_G.asset_margin_mm:g}mm 0"
    caption = _rule(css, ".slide-asset figcaption {")
    assert _prop(caption, "font-size") == f"{_G.caption_pt:g}pt"
    assert _prop(caption, "margin-top") == f"{_G.caption_margin_top_mm:g}mm"
    assert "line-height" not in caption
    fallback = _rule(css, ".slide-asset .figure-fallback,")
    assert _prop(fallback, "font-size") == f"{_G.fallback_pt:g}pt"
    assert _prop(fallback, "line-height") == f"{_G.fallback_line_height:g}"
    assert _prop(fallback, "padding") == (
        f"{_G.fallback_padding_v_mm:g}mm {_G.fallback_padding_h_mm:g}mm"
    )
    assert _prop(fallback, "border").startswith(f"{_G.fallback_border_pt:g}pt ")
    assert _prop(fallback, "max-height") == "var(--figure-h)"
    # Il fallback delle slide non va a capo: una riga sorgente, una riga
    # resa; le righe lunghe sono tagliate a destra.
    assert _prop(fallback, "white-space") == "pre"
    assert _prop(fallback, "overflow") == "hidden"
    assert _prop(_rule(css, ".slide-asset .figure-body {"), "width") == "var(--figure-w)"
    # Le proprietà derivate del modello additivo.
    assert (_G.body_w_mm, _G.body_h_mm, _G.body_bottom_y_mm) == (255.0, 120.0, 155.0)
    assert _G.tag_h_mm == pytest.approx(4.7625)
    assert _G.title_line_mm == pytest.approx(10.3717, abs=1e-4)
    assert _G.text_line_mm == pytest.approx(6.6499, abs=1e-4)
    assert _G.text_w_mm == pytest.approx(183.59, abs=0.01)
    assert _G.bullet_line_mm == pytest.approx(5.2388, abs=1e-4)
    assert _G.bullet_w_mm == 248.0
    assert _G.caption_line_mm == pytest.approx(4.2333, abs=1e-4)
    assert _G.fallback_line_mm == pytest.approx(3.2103, abs=1e-4)
    assert _G.fallback_chrome_mm == pytest.approx(4.2822, abs=1e-4)
    assert _G.min_block_h_mm == pytest.approx(31.2333, abs=1e-4)


def test_mirror_comments_point_at_the_right_template_lines() -> None:
    """Ogni «mirror di lesson_slides_pdf.html.j2:<riga>» in `slide_geometry`
    indica una riga del template che contiene davvero il valore."""
    source = Path(sg.__file__).read_text(encoding="utf-8")
    template_lines = _TEMPLATE.read_text(encoding="utf-8").splitlines()
    refs = re.findall(
        r"^\s*(\w+): (?:float|int) = ([\d.]+)\s*# mirror di lesson_slides_pdf\.html\.j2:(\d+)",
        source,
        re.M,
    )
    assert len(refs) >= 28, refs
    for field, value, line_no in refs:
        line = template_lines[int(line_no) - 1]
        number = f"{float(value):g}"
        assert re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", line), (field, line_no, line)


# ---------------------------------------------------------------------------
# Il <pre> di fallback senza a capo: righe esatte nei due motori
# ---------------------------------------------------------------------------

# Lingue del corso su cui gira il corpus avversario (`<html lang>`); my
# dall'ottavo giro (riga base in Noto Sans Myanmar, V7-1).
_LANGUAGES = ("it", "vi", "hi", "th", "he", "ar", "ru", "ja", "zh-cn", "ko", "kn", "bo", "my")
# Casi del sesto giro (V6-1): con `pre-wrap` Pango non va a capo prima di
# «.», «;» e «—» dopo uno spazio e rende quasi il doppio delle righe.
_V61_CASES = (
    "UAX #14: puntini di guida su una riga (lead_48)",
    "UAX #14: punti e virgola (semi_48)",
    "UAX #14: trattini lunghi su una riga (b2_48)",
)
# Casi del settimo giro di verifica (V7-1): script ideografico in testa, poi tibetano,
# birmano, emoji o PUA; con l'id ASCII in testa (controllo) nessuno
# spostamento.
_V71_CASES = (
    "V7-1: ideogrammi in testa e tibetano (cjk_tib)",
    "V7-1: ideogrammi in testa ed emoji (cjk_emoji)",
    "V7-1: katakana in testa e birmano (kana_my)",
    "V7-1: Hangul in testa e PUA (hangul_pua)",
    "V7-1: controllo, id ASCII in testa e tibetano (ascii_tib)",
)
_PRE_RE = re.compile(r'<pre class="figure-fallback">(.*?)</pre>', re.S)


def _pre_sources() -> list[tuple[str, str, int]]:
    """Corpus della fixture: (nome, sorgente, righe rese attese)."""
    return [(c["name"], c["text"] * c["repeat"], c["rows"]) for c in _CASES["pre_sources"]]


def _pipeline_pre(source: str, language: str) -> tuple[str, str]:
    """Testo del `<pre>` come lo emette il percorso delle slide (a capo
    riscritti da `truncate_fallback_source` con un box che non tronca,
    escape e righe vuote del partial) e contenuto HTML del `<pre>`."""
    from app.services.figure_markup import render_figure_html

    text, omitted = sg.truncate_fallback_source(
        source, box=FigureBox(255.0, 100_000.0), language=language
    )
    assert omitted == 0
    html = render_figure_html(
        body_html=None,
        caption="",
        alt_text="",
        asset_id="x",
        fmt="dot",
        number=None,
        labels=None,
        variant="slide",
        fallback_source=text,
    )
    match = _PRE_RE.search(html)
    assert match, html[:200]
    return text, match.group(1)


def _fallback_rule(*, white_space: str | None = None) -> str:
    """Dichiarazioni della regola `.figure-fallback` del template senza
    `max-height`, con `white-space` sostituito a richiesta."""
    rule = _rule(_css(), ".slide-asset .figure-fallback,").split("{", 1)[1]
    rule = re.sub(r"(?m)^\s*max-height:[^;]*;", "", rule)
    if white_space is not None:
        rule = re.sub(r"(?m)^\s*white-space:[^;]*;", f"white-space: {white_space};", rule)
    return rule


def _corpus_page(language: str, *, white_space: str | None = None) -> str:
    """Pagina alta 20 m (niente frammentazione) con la lingua del corso su
    `<html lang>`, un `<pre id="c<i>">` per caso del corpus con il testo del
    percorso reale e la regola del template su 255 mm, più i `<pre>` di una
    e di due righe (`one`, `two`): in Chromium la riga resa è la differenza
    delle loro altezze (il `line-height` arrotondato alle unità di
    layout)."""
    pres = ['<pre id="one">x</pre>', '<pre id="two">x\ny</pre>']
    for i, (_name, source, _rows) in enumerate(_pre_sources()):
        pres.append(f'<pre id="c{i}">{_pipeline_pre(source, language)[1]}</pre>')
    return (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><style>'
        " *, *::before, *::after { box-sizing: border-box; } body { margin: 0; }"
        " @page { size: 297mm 20000mm; margin: 0; }"
        f" pre {{ {_fallback_rule(white_space=white_space)} width: 255mm; margin: 0 0 2mm; }}"
        "</style></head><body>" + "".join(pres) + "</body></html>"
    )


def _weasyprint_pre_rows(weasyprint: Any, html: str) -> dict[str, list[float]]:
    """Altezza in mm di ogni `LineBox` di ogni `<pre>`, in ordine."""
    found: dict[str, list[float]] = {}
    for page in weasyprint.HTML(string=html).render().pages:
        for box in _walk(page._page_box):
            if _is_block(box) and box.element_tag == "pre":
                rows = [b for b in _walk(box) if type(b).__name__ == "LineBox"]
                found.setdefault(box.element.get("id"), []).extend(b.height / _MM for b in rows)
    return found


def _rows_over_estimate(heights: list[float], text: str, language: str) -> list[str]:
    """Righe rese più alte della loro stima (una riga sorgente del testo
    del `<pre>` alla volta): il controllo per riga copre ogni prefisso, non
    solo il totale."""
    lines = text.split("\n")
    assert len(lines) == len(heights), (len(lines), len(heights))
    over = []
    for index, (line, height) in enumerate(zip(lines, heights, strict=True)):
        estimate = sg.fallback_source_rows(line, language=language)[1]
        if height > estimate + 1e-3:
            over.append(f"riga {index}: {height:.3f} contro {estimate:.3f} ({line[:24]!r})")
    return over


_CHROMIUM_ROWS_JS = """() => {
    const px = (el) => {
        const cs = getComputedStyle(el);
        const chrome = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)
            + parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
        return el.getBoundingClientRect().height - chrome;
    };
    const one = document.getElementById('one');
    const two = document.getElementById('two');
    // Riga resa = differenza fra i `<pre>` di due righe e di una: il resto
    // dell'altezza (bordi arrotondati) si elide.
    const row = one && two ? px(two) - px(one) : null;
    const rows = (el) => (row ? (px(el) - px(one)) / row + 1 : null);
    return Object.fromEntries([...document.querySelectorAll('pre')].map((el) => [el.id, [
        rows(el), px(el), el.scrollWidth > el.clientWidth, el.scrollHeight - el.clientHeight]]));
}"""


def _chromium_pre_rows(html: str, tmp_path: Path) -> dict[str, Any]:
    """Per ogni `<pre>` in Chromium: righe rese (dall'altezza del contenuto,
    con la riga misurata fra `#one` e `#two`, se ci sono), altezza del
    contenuto in px CSS, testo più largo del box, pixel nascosti in
    verticale."""
    sync_api = pytest.importorskip("playwright.sync_api")
    path = tmp_path / "pre.html"
    path.write_text(html, encoding="utf-8")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])
        try:
            page = browser.new_page()
            page.goto(path.as_uri())
            page.evaluate("() => document.fonts.ready.then(() => true)")
            return page.evaluate(_CHROMIUM_ROWS_JS)
        finally:
            browser.close()


def test_pre_corpus_rows_are_counted_from_line_breaks() -> None:
    """Le righe attese della fixture (split sugli a capo, NUL tolti) sono
    quelle di `fallback_source_rows` e del testo emesso dal percorso reale;
    il corpus contiene i casi che i giri precedenti hanno trovato."""
    names = {c["name"] for c in _CASES["pre_sources"]}
    assert set(_V61_CASES) <= names and len(names) == len(_CASES["pre_sources"])
    assert set(_V71_CASES) <= names
    longest = max(len(source) for _name, source, _rows in _pre_sources())
    assert longest >= 25_000
    for name, source, rows in _pre_sources():
        assert rows == len(_source_lines(source)), name
        text, inner = _pipeline_pre(source, "it")
        assert sg.fallback_source_rows(source)[0] == rows, name
        assert sg.fallback_source_rows(text) == sg.fallback_source_rows(source), name
        assert text.count("\n") + 1 == rows and inner.count("\n") + 1 == rows, name
        assert "\N{LINE SEPARATOR}" not in inner and "\r" not in inner and "\x00" not in inner


@pytest.mark.parametrize("language", _LANGUAGES)
def test_pre_rows_are_exact_in_both_engines(language: str, tmp_path: Path) -> None:
    """Corpus avversario con la regola del template (`white-space: pre`) e
    la lingua del corso: per ogni caso i `LineBox` di WeasyPrint e le righe
    di Chromium sono ESATTAMENTE le righe stimate (una per riga sorgente),
    l'altezza resa non supera quella stimata in nessuno dei due motori, e
    in WeasyPrint nemmeno riga per riga (ottavo giro: la riga con uno
    script ideografico in testa è più alta di quella con un id ASCII in
    testa, a parità di contenuto), e le righe più larghe del box escono a
    destra invece di andare a capo."""
    weasyprint = _weasyprint()
    html = _corpus_page(language)
    wp = _weasyprint_pre_rows(weasyprint, html)
    chromium = _chromium_pre_rows(html, tmp_path)
    assert (len(wp["one"]), len(wp["two"])) == (1, 2)
    assert chromium["two"][0] == pytest.approx(2.0)
    wide = 0
    over: dict[str, list[str]] = {}
    for i, (name, source, rows) in enumerate(_pre_sources()):
        est_rows, est_mm = sg.fallback_source_rows(source, language=language)
        assert est_rows == rows, name
        heights = wp[f"c{i}"]
        wp_rows, wp_mm = len(heights), sum(heights)
        ch_rows, ch_px, ch_wide, _hidden = chromium[f"c{i}"]
        assert wp_rows == rows, (language, name, wp_rows, rows)
        if rows_over := _rows_over_estimate(heights, _pipeline_pre(source, language)[0], language):
            over[name] = rows_over[:3]
        assert round(ch_rows) == rows and abs(ch_rows - rows) < 0.02, (language, name, ch_rows)
        assert wp_mm <= est_mm + _TOL, (language, name, wp_mm, est_mm)
        assert ch_px / _MM <= est_mm + _TOL, (language, name, ch_px / _MM, est_mm)
        wide += bool(ch_wide)
    assert over == {}, (language, over)
    assert wide >= 20, wide


def test_pre_wrap_rule_breaks_the_exact_count(tmp_path: Path) -> None:
    """Controprova: con la regola `pre-wrap` di prima lo stesso oracolo
    vede righe in più di quelle stimate, in WeasyPrint quasi il doppio sui
    casi del sesto giro (46 contro 25 righe per le 48 ripetizioni) e in
    Chromium sulle righe lunghe."""
    weasyprint = _weasyprint()
    html = _corpus_page("it", white_space="pre-wrap")
    wp = _weasyprint_pre_rows(weasyprint, html)
    chromium = _chromium_pre_rows(html, tmp_path)
    more_wp: dict[str, int] = {}
    more_ch = 0
    for i, (name, _source, rows) in enumerate(_pre_sources()):
        if len(wp[f"c{i}"]) > rows:
            more_wp[name] = len(wp[f"c{i}"])
        more_ch += round(chromium[f"c{i}"][0]) > rows
    assert all(more_wp.get(name, 0) >= 40 for name in _V61_CASES), more_wp
    assert len(more_wp) >= 30 and more_ch >= 30, (len(more_wp), more_ch)


# Famiglia del `<pre>` per il test dell'insieme base (None: quella del
# template, Menlo in locale e DejaVu Sans Mono nel container).
_MONO_STACKS: dict[str, str | None] = {
    "DejaVu Sans Mono": '"A4UDejaVuMono"',
    "pila del template": None,
}


def _dejavu_mono_ttf() -> Path:
    import matplotlib

    path = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / "DejaVuSansMono.ttf"
    assert path.is_file(), path
    return path


def _base_chars() -> list[str]:
    return [chr(cp) for cp in range(0x20, 0x2600) if sg._MONO_BASE_RE.fullmatch(chr(cp))]


@pytest.mark.parametrize("stack", list(_MONO_STACKS))
def test_mono_base_set_stays_on_base_rows(stack: str) -> None:
    """Ogni carattere dell'insieme base, fra due «a» in `white-space: pre`,
    sta su una riga alta 1,3 em (3,210 mm) con il font del container
    (DejaVu Sans Mono) e con quello locale (Menlo): nessun ripiego su un
    font con altre metriche, quindi le righe base valgono 1,3 em."""
    from markupsafe import escape

    weasyprint = _weasyprint()
    chars = _base_chars()
    assert len(chars) == 669
    family = _MONO_STACKS[stack] or '"Menlo", "Consolas", monospace'
    html = (
        "<html><head><style>"
        f'@font-face {{ font-family: "A4UDejaVuMono"; src: url("{_dejavu_mono_ttf().as_uri()}"); }}'
        " @page { size: 297mm 3000mm; margin: 0 }"
        f" pre {{ font-family: {family}; font-size: 7pt; line-height: 1.3;"
        " white-space: pre; margin: 0 }"
        f"</style></head><body><pre>{escape(chr(10).join('a' + c + 'a' for c in chars))}</pre>"
        "</body></html>"
    )
    rows = [
        box
        for page in weasyprint.HTML(string=html).render().pages
        for box in _walk(page._page_box)
        if type(box).__name__ == "LineBox"
    ]
    assert len(rows) == len(chars)
    for ch, row in zip(chars, rows, strict=True):
        assert row.height / _MM <= _G.fallback_line_mm + 1e-3, (stack, hex(ord(ch)))


def _stix_non_uni_ttf() -> Path:
    import matplotlib

    path = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / "STIXNonUni.ttf"
    assert path.is_file(), path
    return path


_TALL_SOURCE = "\n".join(f"n{i}->n{i + 1};" for i in range(90))


def _tall_base_row_page() -> tuple[str, dict[str, str]]:
    """Due `<pre>` con il sorgente da 90 righe troncato nel box da 86,6 mm:
    con la lingua ja (profilo CJK) e con il profilo neutro. Il font del
    `<pre>` mette davanti STIXNonUni (solo lo spazio fra i glifi utili,
    A−D 0,898 come Noto Sans CJK 0,872) a DejaVu Sans Mono: la riga base
    prende le metriche di STIX e ogni riga resa è alta 3,464 mm, come nel
    container con un corso in giapponese (3,432 mm)."""
    from markupsafe import escape

    box = FigureBox(255.0, 86.6)
    texts = {
        lang: sg.truncate_fallback_source(_TALL_SOURCE, box=box, language=lang)[0]
        for lang in ("ja", "it")
    }
    rule = _rule(_css(), ".slide-asset .figure-fallback,").split("{", 1)[1]
    rule = re.sub(r"(?m)^\s*font-family:[^;]*;", 'font-family: "A4UStrut", "A4UDejaVuMono";', rule)
    pres = "".join(
        f'<pre id="{lang}" lang="{lang}" style="max-height: {box.h_mm}mm">{escape(text)}</pre>'
        for lang, text in texts.items()
    )
    html = (
        "<!doctype html><html><head><style>"
        f'@font-face {{ font-family: "A4UStrut"; src: url("{_stix_non_uni_ttf().as_uri()}"); }}'
        f'@font-face {{ font-family: "A4UDejaVuMono"; src: url("{_dejavu_mono_ttf().as_uri()}"); }}'
        " *, *::before, *::after { box-sizing: border-box; } body { margin: 0; }"
        " @page { size: 297mm 400mm; margin: 0; }"
        f" pre {{ {rule} width: 255mm; margin: 0; break-after: page; }}"
        f"</style></head><body>{pres}</body></html>"
    )
    return html, texts


def test_fallback_marker_survives_a_taller_base_row(tmp_path: Path) -> None:
    """Riga base più alta (lingua CJK nel container, emulata con STIXNonUni):
    con `language="ja"` il sorgente è troncato a 18 righe più «…» (1,70 em
    per riga) e in WeasyPrint l'ultima riga, quella del marcatore, sta
    dentro il `<pre>` e il marcatore è nel testo del PDF; in Chromium
    nessun pixel nascosto. Controprova: con il profilo neutro (24 righe
    più «…», 1,3 em per riga) le righe da 3,464 mm escono dal padding del
    `<pre>` e il marcatore finisce sotto il bordo."""
    import pypdf

    weasyprint = _weasyprint()
    html, texts = _tall_base_row_page()
    assert texts["ja"].count("\n") == 18 and texts["it"].count("\n") == 24
    document = weasyprint.HTML(string=html).render()
    found: dict[str, dict[str, float]] = {}
    for page in document.pages:
        for box in _walk(page._page_box):
            if _is_block(box) and box.element_tag == "pre":
                rows = [b for b in _walk(box) if type(b).__name__ == "LineBox"]
                found[box.element.get("id")] = {
                    "row_h": max(b.height for b in rows) / _MM,
                    "last_bottom": (rows[-1].position_y + rows[-1].height) / _MM,
                    "clip": (box.padding_box_y() + box.padding_height()) / _MM,
                }
    assert len(document.pages) == 2 and set(found) == {"ja", "it"}, found
    for info in found.values():
        assert info["row_h"] == pytest.approx(3.464, abs=0.01), found
    assert found["ja"]["last_bottom"] <= found["ja"]["clip"], found
    assert found["it"]["last_bottom"] > found["it"]["clip"] + 2, found
    ja_text = pypdf.PdfReader(io.BytesIO(document.write_pdf())).pages[0].extract_text()
    assert "n17->n18;" in ja_text and "n18->n19;" not in ja_text, ja_text
    assert ja_text.rstrip().endswith(sg.TRUNCATION_MARK), ja_text
    chromium = _chromium_pre_rows(html, tmp_path)
    assert chromium["ja"][3] <= 1, chromium


def test_cjk_course_slide_truncates_to_the_tall_rows() -> None:
    """Slide di un corso in giapponese con un fallback ASCII da 90 righe:
    la lingua arriva alla troncatura (18 righe più «…», 72 omesse, contro
    24 e 66 in italiano), il `<pre>` rende al più 19 righe in WeasyPrint,
    la didascalia resta nella pagina e il frame video non nasconde
    pixel."""
    weasyprint = _weasyprint()
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw={
            "introduction": "",
            "sections": [],
            "summary": "",
            "visual_assets": [_asset("D", "dot", _CASES["fallback"][10]["source"], "Grafo")],
        },
        slides_raw={"slides": [_slide("s1", _T1, ["D"])]},
    )
    assert _CASES["fallback"][10]["language"] == "ja"
    omitted = {}
    for language in ("ja", "it"):
        html, logs = _render(lesson, {}, language=language)
        (event,) = [e for e in logs if e["event"] == "figure_fallback_truncated"]
        omitted[language] = event["omitted_lines"]
        if language == "ja":
            (info,) = _page_geometry(weasyprint, html)
            assert info["pre_lines"] == [19] and len(info["captions"]) == 1, info
            assert max(info["assets"]) <= info["body_bottom"] + _TOL, info
            (frame,) = _video_frames(html, 1)
            assert frame["hidden"] == [0] and len(frame["captions"]) == 1, frame
    assert omitted == {"ja": 72, "it": 66}


# ---------------------------------------------------------------------------
# Lezione di prova
# ---------------------------------------------------------------------------

_V11 = mp._strip_mermaid_max_width(
    (_FIXTURES / "mermaid11_flowchart.svg").read_text(encoding="utf-8")
)


def _fluid_svg(vb_w: float, vb_h: float) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {vb_w} {vb_h}">'
        f'<rect x="0" y="0" width="{vb_w}" height="{vb_h}" fill="#eee"/>'
        '<text x="10" y="20" font-size="14">Nodo</text></svg>'
    )


_INTRINSIC_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="321" height="359" viewBox="0 0 321 359">'
    '<rect x="0" y="0" width="321" height="359" fill="#eee"/>'
    '<text x="10" y="20" font-size="14">Nodo</text></svg>'
)


def _png_data_url(width: int, height: int) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("1", (width, height), 1).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _text(name: str) -> dict[str, Any]:
    """Caso della fixture per prefisso del nome (sezione `pages`)."""
    return next(c for c in _CASES["pages"] if c["name"].startswith(name))


def _patch_uploaded_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'asset `image` risolve a una PNG 1920×1080 (data URL)."""
    url = _png_data_url(1920, 1080)
    monkeypatch.setattr(pdf, "_resolve_template_asset_url", lambda *a, **k: url)


_T1 = "Titolo su una riga"
_TUP = _text("titolo tutto in maiuscolo")["title"]
_T3 = next(c for c in _CASES["lines"] if c["text"].startswith("SOSTENIBILITA"))["text"]
_BODY234 = _text("slide dedicata Fase 4")["body"]
_B5 = _text("titolo + 5 bullet")["bullets"]
_CAP559 = _CASES["captions"][1]["caption_text"].removeprefix("Figura. ")
_SRC60 = _CASES["fallback"][0]["source"]


def _asset(asset_id: str, fmt: str, content: str, caption: str) -> dict[str, Any]:
    return {"asset_id": asset_id, "format": fmt, "content": content, "caption": caption}


def _slide(
    slide_id: str, title: str, refs: list[str], *, body: str = "", bullets: list | None = None
):
    return {
        "slide_id": slide_id,
        "type": "diagram",
        "title": title,
        "body": body,
        "bullets": bullets or [],
        "references_assets": refs,
    }


def _fourteen_page_lesson() -> tuple[CourseLesson, dict[str, str]]:
    content_raw = {
        "introduction": "[FIG:A] [FIG:T] [FIG:I] [FIG:P] [FIG:D] [FIG:C] [TAB:t1]",
        "sections": [],
        "summary": "",
        "visual_assets": [
            _asset("A", "mermaid", "flowchart LR\n A --> B", "Schema"),
            _asset("T", "mermaid", "flowchart TD\n A --> B", "Sequenza alta"),
            _asset("I", "vegalite", "{}", "Intrinseca"),
            _asset("P", "image", "lesson_assets/foto.png", "Foto"),
            _asset("D", "dot", _SRC60, "Grafo senza render"),
            _asset("C", "mermaid", "flowchart LR\n A --> B", _CAP559),
        ],
        "tables": [{"table_id": "t1", "caption": "Confronto", "markdown": "| a |\n|---|\n| 1 |"}],
    }
    slides_raw = {
        "slides": [
            _slide("p01", _T1, ["A"]),
            _slide("p02", _TUP, ["A"]),
            _slide("p03", _T3, ["A"]),
            _slide("p04", _T1, ["T"], body=_BODY234),
            _slide("p05", _T1, ["T"], bullets=_B5[:3]),
            _slide("p06", _T1, ["T"], bullets=_B5),
            _slide("p07", _T1, ["A"], bullets=[""]),
            _slide("p08", _T1, ["I"]),
            _slide("p09", _T1, ["P"]),
            _slide("p10", _T1, ["D"]),
            _slide("p11", _T1, ["A", "T"]),
            _slide("p12", _T1, ["A", "t1"]),
            _slide("p13", _T1, ["C"]),
            _slide("p14", _T1, ["T"], body=_BODY234, bullets=_B5 * 2),
        ]
    }
    lesson = CourseLesson(
        lesson_code="M1.L1", title="Lezione", content_raw=content_raw, slides_raw=slides_raw
    )
    svg_map = {"A": _V11, "T": _fluid_svg(650, 907), "I": _INTRINSIC_SVG, "C": _V11}
    return lesson, svg_map


def _render(
    lesson: CourseLesson,
    svg_map: dict[str, str],
    *,
    enable_split: bool = False,
    language: str = "it",
) -> tuple[str, list[dict[str, Any]]]:
    with structlog.testing.capture_logs() as logs:
        html = slides_pdf.render_slides_html(
            course=Course(title="Corso", language_code=language, cfu=6),
            lesson=lesson,
            organization=None,
            slide_template=None,
            enable_split=enable_split,
            visual_svg_map=svg_map,
        )
    return html, logs


def _walk_in_figures(box: Any, figure_h: float | None = None) -> Iterator[tuple[Any, float | None]]:
    """Camminata che porta con sé il `--figure-h` del `<figure>` più vicino
    sopra il box (gli elementi di WeasyPrint non hanno `getparent`)."""
    if _is_block(box) and box.element_tag == "figure":
        m = _FIGURE_H_RE.search(box.element.get("style") or "")
        figure_h = float(m.group(1)) if m else None
    yield box, figure_h
    children = getattr(box, "all_children", None)
    for child in children() if children else getattr(box, "children", []):
        yield from _walk_in_figures(child, figure_h)


def _page_geometry(weasyprint: Any, html: str) -> list[dict[str, Any]]:
    """Per ogni pagina resa: fondo del `.slide-body`, fondo (con margine) di
    ogni `div.slide-asset`, altezza e `--figure-h` di ogni `img`/`pre`,
    righe del `<pre>`, fondo di ogni `<figcaption>`."""
    pages: list[dict[str, Any]] = []
    for page in weasyprint.HTML(string=html).render().pages:
        info: dict[str, Any] = {"assets": [], "images": [], "pre_lines": [], "captions": []}
        for box, figure_h in _walk_in_figures(page._page_box):
            tag = box.element_tag
            classes = _classes(box)
            if _is_block(box) and tag == "div" and "slide-body" in classes:
                info["body_bottom"] = (box.position_y + box.height) / _MM
            elif _is_block(box) and tag == "div" and "slide-asset" in classes:
                info["assets"].append(_bottom_mm(box))
            elif tag == "img" and {"mermaid-svg", "figure-svg", "uploaded-image"} & set(classes):
                info["images"].append((box.height / _MM, figure_h))
            elif _is_block(box) and tag == "pre" and "figure-fallback" in classes:
                info["images"].append((box.height / _MM, figure_h))
                info["pre_lines"].append(_line_boxes(box))
            elif _is_block(box) and tag == "figcaption":
                info["captions"].append((box.position_y + box.height) / _MM)
        assert "body_bottom" in info
        pages.append(info)
    return pages


def test_rendered_slides_never_overflow_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le 14 pagine (titoli da 1/2/3 righe, prosa, bullet, SVG fluido alto,
    SVG intrinseco, immagine caricata, fallback da 60 righe, due figure,
    figura + tabella, didascalia da 559 caratteri, pagina impossibile)
    rese senza split: ogni asset sta sopra il fondo del body, ogni
    immagine entro `--figure-h`, il `<pre>` entro le righe che entrano;
    l'unica pagina che sborda è quella impossibile, loggata."""
    weasyprint = _weasyprint()
    _patch_uploaded_image(monkeypatch)
    lesson, svg_map = _fourteen_page_lesson()
    html, logs = _render(lesson, svg_map)
    assert html.count('<div class="slide">') == 14
    pages = _page_geometry(weasyprint, html)
    assert len(pages) == 14
    overflowing = []
    for number, info in enumerate(pages, start=1):
        assert info["assets"], number
        if max(info["assets"]) > info["body_bottom"] + _TOL:
            overflowing.append(number)
        for height, cap in info["images"]:
            assert cap is not None, number
            assert height <= cap + _TOL, (number, height, cap)
    assert overflowing == [14]
    # Il `<pre>` di fallback (pagina 10) entro le righe che entrano nel box.
    (pre_lines,) = pages[9]["pre_lines"]
    (_h, cap) = pages[9]["images"][0]
    assert cap is not None and pre_lines <= sg.fallback_lines_that_fit(FigureBox(255, cap))
    exhausted = [e for e in logs if e["event"] == "slide_figure_box_exhausted"]
    assert [(e["slide_id"], e["n_blocks"]) for e in exhausted] == [("p14", 1)]
    assert exhausted[0]["deficit_mm"] > 0 and exhausted[0]["available_mm"] < _G.min_block_h_mm
    truncated = [e for e in logs if e["event"] == "figure_fallback_truncated"]
    assert [(e["asset_id"], e["omitted_lines"]) for e in truncated] == [("D", 36)]
    shared = [e for e in logs if e["event"] == "slide_figure_box_shared"]
    assert [(e["slide_id"], e["n_blocks"]) for e in shared] == [("p11", 2), ("p12", 2)]
    assert not [e for e in logs if e["event"] == "slide_figure_caption_squeezed"]
    # Il box è nel markup come style del <figure>, dopo aria-label.
    assert (
        len(re.findall(r'aria-label="[^"]*" style="--figure-w: 255\.0mm; --figure-h: ', html)) == 15
    )


def test_the_oracle_catches_the_old_80mm_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controprova: con il cap fisso di 80 mm al posto di `image_box`, la
    slide dedicata di Fase 4 (titolo + prosa) e la pagina con 5 bullet
    sbordano di più di 5 mm, quella con due figure di più di 10 mm: il
    test precedente vede la patologia di prima di D12."""
    weasyprint = _weasyprint()

    def old_cap(budget: Any, *, caption_text: str, geometry: Any = _G) -> tuple[FigureBox, bool]:
        return FigureBox(255, 80), False

    monkeypatch.setattr(sg, "image_box", old_cap)
    # Solo le tre pagine di interesse: la pagina impossibile con il cap 80
    # supererebbe il foglio e WeasyPrint la frammenterebbe su più pagine.
    lesson, svg_map = _fourteen_page_lesson()
    keep = ("p04", "p06", "p11")
    slides = [s for s in lesson.slides_raw["slides"] if s["slide_id"] in keep]
    lesson.slides_raw = {**lesson.slides_raw, "slides": slides}
    html, _logs = _render(lesson, svg_map)
    assert html.count("--figure-h: 80.0mm") == 4
    pages = _page_geometry(weasyprint, html)
    assert len(pages) == 3
    overflow = [max(p["assets"]) - p["body_bottom"] for p in pages]
    assert overflow[0] > 5 and overflow[1] > 5 and overflow[2] > 10, overflow


def test_the_geometry_matches_the_rendered_template() -> None:
    """Modello additivo contro il motore: `.slide-body` 255 × 120 a y 35,
    riga del titolo 10,37, la figura inizia a 35 + tag + 3 + 10,372 + 4
    (i margini di `.slide-assets` e `.slide-asset` collassano a 4 mm), il
    tag e la didascalia stanno nei budget 1,5 × corpo."""
    weasyprint = _weasyprint()
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw={
            "introduction": "[FIG:A]",
            "sections": [],
            "summary": "",
            "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A --> B", "Schema")],
        },
        slides_raw={"slides": [_slide("s1", _T1, ["A"])]},
    )
    html, _logs = _render(lesson, {"A": _V11})
    (page,) = weasyprint.HTML(string=html).render().pages
    found: dict[str, Any] = {}
    for box in _walk(page._page_box):
        tag, classes = box.element_tag, _classes(box)
        if _is_block(box) and tag == "div" and "slide-body" in classes:
            found["body"] = (box.position_y / _MM, box.width / _MM, box.height / _MM)
        elif _is_block(box) and tag == "div" and "slide-tag" in classes:
            found["tag_h"] = box.height / _MM
        elif _is_block(box) and tag == "h1":
            found["title_h"] = box.height / _MM
            found["title_lines"] = _line_boxes(box)
        elif _is_block(box) and tag == "figure":
            found["figure_y"] = box.position_y / _MM
        elif _is_block(box) and tag == "figcaption":
            found["caption_h"] = box.height / _MM
            found["caption_lines"] = _line_boxes(box)
    assert found["body"] == pytest.approx((35.0, 255.0, 120.0), abs=0.01)
    assert found["title_lines"] == 1
    assert found["title_h"] == pytest.approx(10.372, abs=0.01)
    assert 0 < found["tag_h"] <= _G.tag_h_mm
    assert found["figure_y"] == pytest.approx(35 + found["tag_h"] + 3 + 10.372 + 4, abs=0.05)
    assert found["caption_lines"] == 1 and found["caption_h"] <= _G.caption_line_mm


_VIDEO_MEASURE_JS = """() => {
    const slide = [...document.querySelectorAll('.slide')].find(
        (el) => el.style.display !== 'none');
    const body = slide.querySelector('.slide-body').getBoundingClientRect();
    const assets = [...slide.querySelectorAll('.slide-asset')].map(
        (a) => a.getBoundingClientRect().bottom);
    const images = [...slide.querySelectorAll(
        '.slide-asset img, .slide-asset pre')].map((el) => {
        const fig = el.closest('figure');
        const cap = fig ? getComputedStyle(fig).getPropertyValue('--figure-h').trim() : '';
        return [el.getBoundingClientRect().height, cap];
    });
    const captions = [...slide.querySelectorAll('.slide-asset figcaption')].map(
        (el) => el.getBoundingClientRect().bottom);
    const hidden = [...slide.querySelectorAll('.slide-asset pre')].map(
        (el) => el.scrollHeight - el.clientHeight);
    return {bodyBottom: body.bottom, assets, images, captions, hidden};
}"""


def _video_frames(html: str, n_slides: int) -> list[dict[str, Any]]:
    """Frame video: `_VIDEO_OVERRIDE_CSS` prima di `</head>`, viewport
    1980×1400, ogni `.slide` mostrata da sola come fa il renderer; per
    frame le misure di `_VIDEO_MEASURE_JS` in pixel dello schermo. Skip
    solo se Chromium non si avvia."""
    sync_api = pytest.importorskip("playwright.sync_api")
    html_video = html.replace("</head>", video._VIDEO_OVERRIDE_CSS + "</head>", 1)
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])
        try:
            page = browser.new_page(
                viewport={"width": video.VIDEO_WIDTH, "height": video.VIDEO_HEIGHT}
            )
            page.set_content(html_video)
            count = page.evaluate("document.querySelectorAll('.slide').length")
            assert count == n_slides
            frames = []
            for index in range(count):
                page.evaluate(
                    """(idx) => {
                        const all = document.querySelectorAll('.slide');
                        all.forEach((el, j) => {
                            el.style.display = (j === idx) ? '' : 'none';
                        });
                    }""",
                    index,
                )
                frames.append(page.evaluate(_VIDEO_MEASURE_JS))
        finally:
            browser.close()
    return frames


def test_video_frames_keep_figures_inside_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stesso HTML nei frame video: `_VIDEO_OVERRIDE_CSS` prima di `</head>`,
    viewport 1980×1400, ogni `.slide` mostrata da sola come fa il renderer;
    in Chromium ogni `.slide-asset` (più il margine di 2 mm) sta sopra il
    fondo del `.slide-body` e ogni `img`/`pre` entro `--figure-h`, salvo la
    pagina impossibile. Skip solo se Chromium non si avvia."""
    _patch_uploaded_image(monkeypatch)
    lesson, svg_map = _fourteen_page_lesson()
    html, _logs = _render(lesson, svg_map)
    scale = video._VIDEO_SLIDE_SCALE * _MM  # px per mm sullo schermo
    frames = _video_frames(html, 14)
    overflowing = []
    for number, frame in enumerate(frames, start=1):
        assert frame["assets"], number
        worst = max(
            (a + _G.asset_margin_mm * scale - frame["bodyBottom"]) / scale for a in frame["assets"]
        )
        if worst > 0.5:
            overflowing.append(number)
        for height_px, cap in frame["images"]:
            assert cap.endswith("mm"), (number, cap)
            assert height_px / scale <= float(cap[:-2]) + 0.1, (number, height_px / scale, cap)
    assert overflowing == [14]


_LONG_FALLBACKS = (
    "box 86.6, sorgente 60 righe",
    "box 86.6, 58 righe DOT da 176 caratteri con 22 spazi",
    "box 86.6, 60 righe di parole brevi da 200 colonne",
    "box 86.6, 60 righe con 170 spazi iniziali",
    "box 86.6, 60 righe con 22 tab iniziali",
    "box 86.6, 40 righe con U+2028 e U+2029",
    "box 86.6, una riga da 4.000 caratteri tagliata a destra",
    "box 86.6, puntini di guida ripetuti su una riga (V6-1)",
    "box 86.6, 200 righe corte e riga vuota finale",
    "box 86.6, separatori CRLF, CR, U+2028, U+2029 riscritti come a capo",
)


def _fallback_lesson() -> CourseLesson:
    """Slide asset-only con il fallback `<pre>` (render DOT assente): le 60
    righe corte di sempre, i sorgenti a righe lunghe, quelli con i
    separatori Unicode e quelli del sesto giro della fixture."""
    by_name = {c["name"]: c for c in _CASES["fallback"]}
    assets = [
        _asset(f"F{i}", "dot", by_name[name]["source"], "Grafo senza render")
        for i, name in enumerate(_LONG_FALLBACKS)
    ]
    return CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw={"introduction": "", "sections": [], "summary": "", "visual_assets": assets},
        slides_raw={
            "slides": [_slide(f"f{i}", _T1, [f"F{i}"]) for i in range(len(_LONG_FALLBACKS))]
        },
    )


def test_long_fallback_lines_stay_inside_the_body() -> None:
    """Fallback con righe lunghe (DOT reale da 176 caratteri, parole brevi,
    170 spazi o 22 tab iniziali, una riga da 4.000 caratteri, i puntini di
    guida del sesto giro) o con i separatori U+2028 e U+2029: in WeasyPrint
    il `<pre>` rende esattamente le righe del testo troncato, ogni asset e
    la didascalia stanno sopra il fondo del body; nel frame video nessun
    asset sotto il body, didascalia visibile, `<pre>` senza pixel nascosti
    in verticale. La slide da 60 righe corte resta com'era (24 righe più
    «…»)."""
    weasyprint = _weasyprint()
    html, logs = _render(_fallback_lesson(), {})
    truncated = [
        (e["asset_id"], e["omitted_lines"])
        for e in logs
        if e["event"] == "figure_fallback_truncated"
    ]
    by_name = {c["name"]: c for c in _CASES["fallback"]}
    assert truncated == [
        (f"F{i}", by_name[name]["expected"]["omitted"])
        for i, name in enumerate(_LONG_FALLBACKS)
        if by_name[name]["expected"]["omitted"]
    ]
    pages = _page_geometry(weasyprint, html)
    assert len(pages) == len(_LONG_FALLBACKS)
    for name, info in zip(_LONG_FALLBACKS, pages, strict=True):
        ((_h, cap),) = info["images"]
        (pre_lines,) = info["pre_lines"]
        assert cap == 86.6, (name, cap)
        assert pre_lines == by_name[name]["expected"]["rows"], (name, pre_lines)
        assert pre_lines <= by_name[name]["expected"]["fit"], (name, pre_lines)
        assert max(info["assets"]) <= info["body_bottom"] + _TOL, (name, info)
        assert len(info["captions"]) == 1, (name, info["captions"])
        assert info["captions"][0] <= info["body_bottom"] + _TOL, (name, info)
    assert pages[0]["pre_lines"] == [25]
    scale = video._VIDEO_SLIDE_SCALE * _MM
    for name, frame in zip(_LONG_FALLBACKS, _video_frames(html, len(_LONG_FALLBACKS)), strict=True):
        worst = max(frame["assets"]) + _G.asset_margin_mm * scale - frame["bodyBottom"]
        assert worst / scale <= 0.5, (name, worst / scale)
        assert len(frame["captions"]) == 1, (name, frame)
        assert frame["captions"][0] <= frame["bodyBottom"], (name, frame)
        assert frame["hidden"] == [0], (name, frame["hidden"])


# ---------------------------------------------------------------------------
# Slide con il corpus avversario in 12 lingue
# ---------------------------------------------------------------------------

_B2 = ["Il trasduttore converte la grandezza fisica", "Il condizionamento filtra il segnale"]


def _corpus_lesson(names: tuple[str, ...] | None = None) -> CourseLesson:
    """Una slide asset-only per caso del corpus (formati a rotazione fra
    quelli renderizzabili, render assente) e, per i casi con più righe e
    per quelli del sesto giro, la stessa figura con due bullet."""
    cases = [c for c in _pre_sources() if names is None or c[0] in names]
    formats = ("dot", "mermaid", "vegalite", "function")
    assets = []
    slides = []
    for i, (name, source, rows) in enumerate(cases):
        assets.append(_asset(f"C{i}", formats[i % len(formats)], source, f"Sorgente {i}"))
        slides.append(_slide(f"c{i}", _T1, [f"C{i}"]))
        if rows > 1 or name in _V61_CASES:
            slides.append(_slide(f"c{i}+b", _T1, [f"C{i}"], bullets=_B2))
    return CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw={"introduction": "", "sections": [], "summary": "", "visual_assets": assets},
        slides_raw={"slides": slides},
    )


def _fallback_slide_pages(weasyprint: Any, html: str, language: str) -> list[dict[str, Any]]:
    """Per ogni pagina resa da WeasyPrint: fondo del body, degli asset e
    delle didascalie; per il `<pre>` di fallback righe rese e stimate (sul
    testo del DOM, con la lingua del corso), altezza resa e stimata, righe
    rese più alte della loro stima, fondo
    dell'ultima riga contro il clip del `<pre>`, fondo del `<pre>`,
    marcatore «…» nell'ultima riga resa."""
    pages: list[dict[str, Any]] = []
    for page in weasyprint.HTML(string=html).render().pages:
        info: dict[str, Any] = {"assets": [], "captions": [], "pres": []}
        for box in _walk(page._page_box):
            if not _is_block(box):
                continue
            tag, classes = box.element_tag, _classes(box)
            if tag == "div" and "slide-body" in classes:
                info["body_bottom"] = (box.position_y + box.height) / _MM
            elif tag == "div" and "slide-asset" in classes:
                info["assets"].append(_bottom_mm(box))
            elif tag == "figcaption":
                info["captions"].append((box.position_y + box.height) / _MM)
            elif tag == "pre" and "figure-fallback" in classes:
                rows = [b for b in _walk(box) if type(b).__name__ == "LineBox"]
                text = "".join(box.element.itertext())
                est_rows, est_mm = sg.fallback_source_rows(text, language=language)
                heights = [b.height / _MM for b in rows]
                last = rows[-1]
                last_text = "".join(b.text for b in _walk(last) if type(b).__name__ == "TextBox")
                info["pres"].append(
                    {
                        "rows": len(rows),
                        "est_rows": est_rows,
                        "height": sum(b.height for b in rows) / _MM,
                        "est_mm": est_mm,
                        "rows_over": (
                            _rows_over_estimate(heights, text, language)
                            if len(rows) == est_rows
                            else []
                        ),
                        "last_bottom": (last.position_y + last.height) / _MM,
                        "clip": (box.padding_box_y() + box.padding_height()) / _MM,
                        "bottom": _bottom_mm(box),
                        "truncated": text.endswith(sg.TRUNCATION_MARK),
                        "marker": sg.TRUNCATION_MARK in last_text,
                    }
                )
        assert "body_bottom" in info
        pages.append(info)
    return pages


def _fallback_page_problems(info: dict[str, Any]) -> list[str]:
    """Difetti di una pagina con un fallback: righe diverse dalla stima,
    altezza oltre la stima (in totale o per una riga), righe sotto il
    clip, `<pre>`, asset o didascalia sotto il body, didascalia assente,
    marcatore non reso."""
    bottom = info["body_bottom"]
    problems = []
    if len(info["captions"]) != 1:
        problems.append("didascalia assente")
    problems += [f"didascalia +{c - bottom:.1f}" for c in info["captions"] if c > bottom + _TOL]
    problems += [f"asset +{a - bottom:.1f}" for a in info["assets"] if a > bottom + _TOL]
    if len(info["pres"]) != 1:
        problems.append(f"{len(info['pres'])} pre")
    for pre in info["pres"]:
        if pre["rows"] != pre["est_rows"]:
            problems.append(f"righe {pre['rows']} contro {pre['est_rows']}")
        if pre["height"] > pre["est_mm"] + _TOL:
            problems.append(f"altezza {pre['height']:.2f} contro {pre['est_mm']:.2f}")
        if pre["rows_over"]:
            problems.append(f"{len(pre['rows_over'])} righe oltre la stima")
        if pre["last_bottom"] > pre["clip"] + _TOL:
            problems.append(f"righe oltre il clip +{pre['last_bottom'] - pre['clip']:.1f}")
        if pre["bottom"] > bottom + _TOL:
            problems.append(f"pre oltre il body +{pre['bottom'] - bottom:.1f}")
        if pre["truncated"] and not pre["marker"]:
            problems.append("marcatore non reso")
    return problems


_VIDEO_PRE_JS = """(scale) => {
    const slide = [...document.querySelectorAll('.slide')].find(
        (el) => el.style.display !== 'none');
    const body = slide.querySelector('.slide-body').getBoundingClientRect();
    const assets = [...slide.querySelectorAll('.slide-asset')].map(
        (a) => a.getBoundingClientRect().bottom);
    const captions = [...slide.querySelectorAll('.slide-asset figcaption')].map(
        (el) => el.getBoundingClientRect().bottom);
    const pres = [...slide.querySelectorAll('.slide-asset pre')].map((el) => {
        const cs = getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        const height = (lines) => {
            const ref = el.cloneNode(false);
            ref.textContent = lines;
            el.after(ref);
            const h = ref.getBoundingClientRect().height / scale;
            ref.remove();
            return h;
        };
        const one = height('x');
        const row = height('x\\ny') - one;
        const text = el.textContent;
        let marker = null;
        if (text.endsWith('\\u2026')) {
            const node = el.firstChild;
            const range = document.createRange();
            range.setStart(node, node.length - 1);
            range.setEnd(node, node.length);
            const r = range.getBoundingClientRect();
            const inner = rect.bottom
                - (parseFloat(cs.paddingBottom) + parseFloat(cs.borderBottomWidth)) * scale;
            marker = r.height > 0 && r.bottom <= inner + 0.5;
        }
        return {rows: (rect.height / scale - one) / row + 1, text, marker,
                hidden: el.scrollHeight - el.clientHeight};
    });
    return {bodyBottom: body.bottom, assets, captions, pres};
}"""


_SHOW_ONLY_JS = """(idx) => {
    document.querySelectorAll('.slide').forEach((el, j) => {
        el.style.display = (j === idx) ? '' : 'none';
    });
}"""


def _video_pre_frames(html: str, n_slides: int) -> list[dict[str, Any]]:
    """Frame video come `_video_frames`, con le misure del `<pre>` di
    `_VIDEO_PRE_JS` (righe dall'altezza, con la riga misurata fra due
    cloni di una e di due righe; testo del DOM, marcatore visibile, pixel
    nascosti)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    html_video = html.replace("</head>", video._VIDEO_OVERRIDE_CSS + "</head>", 1)
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])
        try:
            page = browser.new_page(
                viewport={"width": video.VIDEO_WIDTH, "height": video.VIDEO_HEIGHT}
            )
            page.set_content(html_video)
            assert page.evaluate("document.querySelectorAll('.slide').length") == n_slides
            frames = []
            for index in range(n_slides):
                page.evaluate(_SHOW_ONLY_JS, index)
                frames.append(page.evaluate(_VIDEO_PRE_JS, video._VIDEO_SLIDE_SCALE))
        finally:
            browser.close()
    return frames


def _frame_problems(frame: dict[str, Any], language: str) -> list[str]:
    scale = video._VIDEO_SLIDE_SCALE * _MM  # px per mm sullo schermo
    bottom = frame["bodyBottom"]
    problems = []
    if len(frame["captions"]) != 1:
        problems.append("didascalia assente")
    problems += ["didascalia oltre il body" for c in frame["captions"] if c > bottom + 0.5]
    worst = max(frame["assets"]) + _G.asset_margin_mm * scale - bottom
    if worst / scale > 0.5:
        problems.append(f"asset +{worst / scale:.1f}")
    for pre in frame["pres"]:
        expected = sg.fallback_source_rows(pre["text"], language=language)[0]
        if round(pre["rows"]) != expected or abs(pre["rows"] - expected) > 0.05:
            problems.append(f"righe {pre['rows']:.2f} contro {expected}")
        if pre["hidden"] > 1:
            problems.append(f"nascosti {pre['hidden']} px")
        if pre["marker"] is False:
            problems.append("marcatore invisibile")
    return problems


@pytest.mark.parametrize("language", _LANGUAGES)
def test_fallback_corpus_slides_in_every_language(language: str) -> None:
    """Il corpus avversario nelle slide di un corso in 13 lingue (asset-only
    e con due bullet): in WeasyPrint e nel frame video il `<pre>` rende
    esattamente le righe stimate sul suo testo, nessuna riga più alta della
    sua stima, nessuna riga sotto il clip,
    nessun asset né didascalia sotto il body, una didascalia per slide,
    marcatore «…» reso e visibile dove il sorgente è troncato."""
    weasyprint = _weasyprint()
    lesson = _corpus_lesson()
    html, logs = _render(lesson, {}, language=language)
    n_slides = len(lesson.slides_raw["slides"])
    truncated = [e for e in logs if e["event"] == "figure_fallback_truncated"]
    assert truncated, "nessuna troncatura nel corpus"
    pages = _fallback_slide_pages(weasyprint, html, language)
    assert len(pages) == n_slides
    slide_ids = [s["slide_id"] for s in lesson.slides_raw["slides"]]
    problems = {
        sid: p
        for sid, info in zip(slide_ids, pages, strict=True)
        if (p := _fallback_page_problems(info))
    }
    assert problems == {}, (language, problems)
    marked = sum(pre["truncated"] for info in pages for pre in info["pres"])
    assert marked >= 10, marked
    frames = _video_pre_frames(html, n_slides)
    problems = {
        sid: p
        for sid, frame in zip(slide_ids, frames, strict=True)
        if (p := _frame_problems(frame, language))
    }
    assert problems == {}, (language, problems)
    assert sum(pre["marker"] is True for f in frames for pre in f["pres"]) == marked


def _pre_wrap(html: str) -> str:
    """La regola del fallback delle slide riportata a `white-space: pre-wrap`."""
    head, rest = html.split(".slide-asset .figure-fallback,", 1)
    rule, tail = rest.split("}", 1)
    assert rule.count("white-space: pre;") == 1
    return (
        head
        + ".slide-asset .figure-fallback,"
        + rule.replace("white-space: pre;", "white-space: pre-wrap;")
        + "}"
        + tail
    )


def test_the_oracle_catches_the_pre_wrap_rule() -> None:
    """Controprova: con la regola `pre-wrap` di prima le slide del sesto giro
    (una riga di «voce . . .», «x ; ; ;» o «a — — —» ripetuta 48 volte,
    stimata e resa ora su una riga, da sola o sotto due bullet) rendono
    più di 40 righe in WeasyPrint: il `<pre>` esce dal body di oltre 40 mm
    e la didascalia sparisce dalla pagina; lo stesso oracolo le segnala."""
    weasyprint = _weasyprint()
    lesson = _corpus_lesson(_V61_CASES)
    html, _logs = _render(lesson, {})
    assert _pre_wrap(html) != html
    slide_ids = [s["slide_id"] for s in lesson.slides_raw["slides"]]
    assert slide_ids == ["c0", "c0+b", "c1", "c1+b", "c2", "c2+b"]
    now = _fallback_slide_pages(weasyprint, html, "it")
    assert [_fallback_page_problems(info) for info in now] == [[]] * 6
    before = _fallback_slide_pages(weasyprint, _pre_wrap(html), "it")
    assert len(before) == 6
    for sid, info in zip(slide_ids, before, strict=True):
        problems = _fallback_page_problems(info)
        assert "didascalia assente" in problems, (sid, problems)
        (pre,) = info["pres"]
        assert pre["rows"] >= 40 and pre["bottom"] - info["body_bottom"] > 40, (sid, pre)


def test_the_oracle_catches_the_ideographic_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controprova dell'ottavo giro: con il modello di prima (nessuna riga a
    2,46 em per la prima run ideografica) la slide con katakana in testa e
    birmano (righe da 1,871 em in locale, 2,041 nel container con bo) tiene
    troppe righe: in WeasyPrint le righe restano più alte della stima e
    l'ultima, quella del marcatore «…», finisce sotto il clip del `<pre>`;
    nel container accade anche con ideogrammi in testa e tibetano (2,293
    em). Con il modello di ora nessun difetto; il controllo con l'id ASCII
    in testa è pulito in entrambi i casi."""
    weasyprint = _weasyprint()
    names = (_V71_CASES[0], _V71_CASES[2], _V71_CASES[4])
    lesson = _corpus_lesson(names)
    slide_ids = [s["slide_id"] for s in lesson.slides_raw["slides"]]
    # Ordine del corpus: c0 cjk_tib, c1 kana_my, c2 ascii_tib.
    sources = {name: source for name, source, _rows in _pre_sources()}
    assert [a["content"] for a in lesson.content_raw["visual_assets"]] == [
        sources[name] for name in names
    ]
    assert slide_ids == ["c0", "c0+b", "c1", "c1+b", "c2", "c2+b"]
    html, _logs = _render(lesson, {}, language="bo")
    now = _fallback_slide_pages(weasyprint, html, "bo")
    assert [_fallback_page_problems(info) for info in now] == [[]] * 6
    assert all(pre["truncated"] and pre["marker"] for info in now for pre in info["pres"])
    with monkeypatch.context() as patched:
        patched.setattr(sg, "_ideographic_lead", lambda line: False)
        html_before, _logs = _render(lesson, {}, language="bo")
        before = _fallback_slide_pages(weasyprint, html_before, "bo")
    problems = dict(zip(slide_ids, map(_fallback_page_problems, before), strict=True))
    for sid in ("c1", "c1+b"):
        assert any(p.startswith("righe oltre il clip") for p in problems[sid]), problems[sid]
        assert any(p.endswith("righe oltre la stima") for p in problems[sid]), problems[sid]
    assert problems["c2"] == [] and problems["c2+b"] == [], problems


# Striscia a destra del body (273 mm) fino al bordo del foglio: con le righe
# tagliate dal `<pre>` e dal body non c'è inchiostro.
_INK_LANGUAGES = ("it", "he", "ar", "ja")
_WIDE_CASES = (
    "UAX #14: puntini di guida su una riga (lead_48)",
    "spazi iniziali e finali",
    "una riga da 25.000 caratteri",
    "JSON minificato su una riga",
    "stimatore a capo: CJK senza spazi",
    "stimatore a capo: etichette devanagari in lingua hi",
    "ebraico e arabo su una riga",
    "tutti gli script su una riga",
)


def _without_clip(html: str) -> str:
    """Il body e il `<pre>` delle slide senza `overflow: hidden`."""
    out = []
    for chunk in html.split("}"):
        if (".slide-body {" in chunk or ".slide-asset .figure-fallback," in chunk) and (
            "overflow: hidden;" in chunk
        ):
            chunk = chunk.replace("overflow: hidden;", "overflow: visible;")
        out.append(chunk)
    result = "}".join(out)
    assert result.count("overflow: visible;") == 2
    return result


def _pdf_ink_right_of_body(weasyprint: Any, html: str) -> list[int]:
    """Pixel scuri, pagina per pagina, nella striscia fra 274 e 296 mm
    all'altezza del `<pre>` di fallback (PDF rasterizzato a 144 dpi)."""
    import pypdfium2

    document = weasyprint.HTML(string=html).render()
    spans: list[tuple[float, float]] = []
    for page in document.pages:
        pre = next(
            b
            for b in _walk(page._page_box)
            if _is_block(b) and b.element_tag == "pre" and "figure-fallback" in _classes(b)
        )
        spans.append((pre.position_y / _MM, _bottom_mm(pre)))
    pdf = pypdfium2.PdfDocument(document.write_pdf())
    px_per_mm = 2 * 72 / 25.4
    ink = []
    for index, (top, bottom) in enumerate(spans):
        image = pdf[index].render(scale=2).to_pil().convert("L")
        strip = image.crop(
            (
                round(274 * px_per_mm),
                round(top * px_per_mm),
                round(296 * px_per_mm),
                round(bottom * px_per_mm),
            )
        )
        ink.append(sum(strip.histogram()[:150]))
    pdf.close()
    return ink


_VIDEO_INK_BOX_JS = """() => {
    const slide = [...document.querySelectorAll('.slide')].find(
        (el) => el.style.display !== 'none');
    const s = slide.getBoundingClientRect();
    const b = slide.querySelector('.slide-body').getBoundingClientRect();
    const p = slide.querySelector('.slide-asset pre').getBoundingClientRect();
    return {x: b.right + 3, y: p.top, width: s.right - b.right - 6, height: p.height};
}"""


def _video_ink_right_of_body(html: str, n_slides: int) -> list[int]:
    """Pixel scuri, frame per frame, nello screenshot della striscia fra il
    bordo destro del body e quello della slide, all'altezza del `<pre>`."""
    from PIL import Image

    sync_api = pytest.importorskip("playwright.sync_api")
    html_video = html.replace("</head>", video._VIDEO_OVERRIDE_CSS + "</head>", 1)
    ink = []
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])
        try:
            page = browser.new_page(
                viewport={"width": video.VIDEO_WIDTH, "height": video.VIDEO_HEIGHT}
            )
            page.set_content(html_video)
            for index in range(n_slides):
                page.evaluate(_SHOW_ONLY_JS, index)
                clip = page.evaluate(_VIDEO_INK_BOX_JS)
                shot = Image.open(io.BytesIO(page.screenshot(clip=clip))).convert("L")
                ink.append(sum(shot.histogram()[:150]))
        finally:
            browser.close()
    return ink


@pytest.mark.parametrize("language", _INK_LANGUAGES)
def test_long_fallback_lines_are_cut_at_the_right_edge(language: str) -> None:
    """Le righe più larghe del box (puntini di guida, 170 spazi iniziali,
    una riga da 25.000 caratteri, JSON minificato, ideogrammi, devanagari,
    ebraico e arabo, tutti gli script) sono tagliate al bordo destro del
    body: nessun pixel scuro fra 274 e 296 mm nel PDF né a destra del body
    nel frame video. Controprova: senza `overflow: hidden` su body e
    `<pre>` ognuna esce dal bordo in entrambi i motori."""
    weasyprint = _weasyprint()
    lesson = _corpus_lesson(_WIDE_CASES)
    lesson.slides_raw = {"slides": [s for s in lesson.slides_raw["slides"] if not s["bullets"]]}
    n_slides = len(_WIDE_CASES)
    html, _logs = _render(lesson, {}, language=language)
    assert html.count('<div class="slide">') == n_slides
    assert _pdf_ink_right_of_body(weasyprint, html) == [0] * n_slides
    assert _video_ink_right_of_body(html, n_slides) == [0] * n_slides
    unclipped = _without_clip(html)
    pdf_ink = _pdf_ink_right_of_body(weasyprint, unclipped)
    video_ink = _video_ink_right_of_body(unclipped, n_slides)
    assert all(ink > 0 for ink in pdf_ink), pdf_ink
    assert all(ink > 0 for ink in video_ink), video_ink


def test_the_oracle_catches_unicode_line_separators(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controprova: se U+2028 e U+2029 non sono a capo, la slide con i
    separatori tiene 18 righe sorgente (righe alte: i separatori non sono
    nell'insieme base) che arrivano al `<pre>` con i separatori, WeasyPrint
    le rende su tre righe l'una e il `<pre>` esce dal body di oltre 40 mm."""
    weasyprint = _weasyprint()
    monkeypatch.setattr(sg, "_SOURCE_LINE_BREAK_RE", re.compile("\r\n|[\r\n]"))
    by_name = {c["name"]: c for c in _CASES["fallback"]}
    source = by_name["box 86.6, 40 righe con U+2028 e U+2029"]["source"]
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw={
            "introduction": "",
            "sections": [],
            "summary": "",
            "visual_assets": [_asset("U", "dot", source, "Grafo senza render")],
        },
        slides_raw={"slides": [_slide("u", _T1, ["U"])]},
    )
    html, logs = _render(lesson, {})
    omitted = [e["omitted_lines"] for e in logs if e["event"] == "figure_fallback_truncated"]
    assert omitted == [22], omitted
    (info,) = _page_geometry(weasyprint, html)
    assert max(info["assets"]) - info["body_bottom"] > 40, info


def test_video_render_keeps_one_page_per_slide() -> None:
    """Contratto del video (`render_slides_to_png`): senza split il numero
    di pagine rese è quello delle slide JSON, anche con bullet e figura
    nella stessa slide; con lo split la stessa lezione produce una pagina
    in più per ogni slide legacy bullet + asset."""
    lesson, svg_map = _fourteen_page_lesson()
    html, _logs = _render(lesson, svg_map, enable_split=False)
    n_slides = len(lesson.slides_raw["slides"])
    assert html.count('<div class="slide">') == n_slides == 14
    split_html, _logs = _render(lesson, svg_map, enable_split=True)
    slides = lesson.slides_raw["slides"]
    legacy = sum(1 for s in slides if s["bullets"] and s["references_assets"])
    assert legacy == 4 and split_html.count('<div class="slide">') == n_slides + legacy


def test_split_pages_get_their_own_budget() -> None:
    """Slide legacy con 5 bullet + figura: con lo split la pagina bullet non
    ha figure e la pagina asset-only riceve il box pieno (86,6 mm); senza
    split la pagina unica riceve il box lasciato dai bullet (45,4 mm)."""
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw={
            "introduction": "[FIG:A]",
            "sections": [],
            "summary": "",
            "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A --> B", "Schema")],
        },
        slides_raw={"slides": [_slide("s1", _T1, ["A"], bullets=_B5)]},
    )
    html, _logs = _render(lesson, {"A": _V11}, enable_split=True)
    pages = html.split('<div class="slide">')[1:]
    assert len(pages) == 2
    assert "<figure" not in pages[0] and "slide-bullets" in pages[0]
    assert "--figure-h: 86.6mm" in pages[1] and "slide-bullets" not in pages[1]
    html, _logs = _render(lesson, {"A": _V11}, enable_split=False)
    pages = html.split('<div class="slide">')[1:]
    assert len(pages) == 1 and "slide-bullets" in pages[0]
    expected = sg.image_box(
        sg.page_figure_budget(title=_T1, body="", bullets=_B5, n_blocks=1),
        caption_text="Figura. Schema",
    )[0]
    assert expected.h_mm == 45.4 and "--figure-h: 45.4mm" in pages[0]


def test_shared_page_splits_the_budget_equally_and_logs() -> None:
    """Due figure nella stessa pagina: entrambe a 39,2 mm e un
    `slide_figure_box_shared` con `n_blocks == 2`; figura + tabella: la
    figura a 39,2 mm, la tabella senza `style`, stesso log."""
    lesson, svg_map = _fourteen_page_lesson()
    html, logs = _render(lesson, svg_map)
    pages = html.split('<div class="slide">')[1:]
    assert pages[10].count("--figure-h: 39.2mm") == 2
    assert pages[11].count("--figure-h: 39.2mm") == 1
    assert '<figure class="table">' in pages[11] and 'class="table" style=' not in pages[11]
    shared = [e for e in logs if e["event"] == "slide_figure_box_shared"]
    assert [(e["slide_id"], e["n_blocks"], e["block_h_mm"]) for e in shared] == [
        ("p11", 2, 45.433),
        ("p12", 2, 45.433),
    ]


_LONG_CAPTION = " ".join([_CAP559, _CAP559])


def _caption_crossing(tail: str, *, lines_without: int) -> str:
    """Prefisso (a parole) di una didascalia lunga stimato a
    `lines_without` righe da solo e a una riga in più con la coda: così la
    coda calcolata è ciò che abbassa il box."""
    words = _LONG_CAPTION.split()
    for n in range(len(words), 0, -1):
        base = " ".join(words[:n])
        without = sg.estimate_lines(f"Figura. {base}", font_pt=8, width_mm=255)
        with_tail = sg.estimate_lines(f"Figura. {base} {tail}", font_pt=8, width_mm=255)
        if without == lines_without and with_tail == lines_without + 1:
            return base
    raise AssertionError("nessun prefisso al confine di riga")


@pytest.mark.skipif(
    not frs.REGISTRY["function"].available(), reason="numpy, matplotlib o sympy assenti"
)
def test_long_caption_and_function_tail_shrink_the_image() -> None:
    """La didascalia entra nel conto con il testo reale: la didascalia da
    559 caratteri porta il box a 73,9 mm e la coda calcolata di `function`
    (dal risultato in cache del renderer reale: zeri, punti critici,
    asintoti) lo abbassa ancora; `slide_figure_caption_squeezed` compare
    solo quando il box tocca il pavimento con un budget non clampato (5
    bullet + didascalia al confine della sesta riga con la coda)."""
    spec = json.dumps(
        {
            "kind": "function_study",
            "expressions": [{"expr": "(x**2 - 1)/(x - 2)"}],
            "domain": [-3, 3],
            "show": ["zeros", "critical_points", "asymptotes"],
        }
    )
    svg = frs.REGISTRY["function"].render_svg(spec, asset_id="F")
    assert svg
    tail = frs.function_computed_caption(spec, language="it", asset_id="F")
    assert tail.startswith("Zeri in x = ") and "Asintoto" in tail

    def lesson_for(caption: str, bullets: list[str]) -> CourseLesson:
        return CourseLesson(
            lesson_code="M1.L1",
            title="Lezione",
            content_raw={
                "introduction": "[FIG:F] [FIG:A]",
                "sections": [],
                "summary": "",
                "visual_assets": [
                    _asset("F", "function", spec, caption),
                    _asset("A", "mermaid", "flowchart LR\n A --> B", caption),
                ],
            },
            slides_raw={
                "slides": [
                    _slide("f", _T1, ["F"], bullets=bullets),
                    _slide("a", _T1, ["A"], bullets=bullets),
                ]
            },
        )

    def figure_h(page_html: str) -> float:
        m = _FIGURE_H_RE.search(page_html)
        assert m, page_html[:200]
        return float(m.group(1))

    html, logs = _render(lesson_for(_CAP559, []), {"F": svg, "A": _V11})
    with_tail, without_tail = html.split('<div class="slide">')[1:]
    assert tail in with_tail and tail not in without_tail
    assert figure_h(without_tail) == 73.9 and figure_h(with_tail) <= 73.9
    # Al confine della quinta riga la coda vale una riga di didascalia.
    html, logs = _render(
        lesson_for(_caption_crossing(tail, lines_without=4), []), {"F": svg, "A": _V11}
    )
    with_tail, without_tail = html.split('<div class="slide">')[1:]
    assert figure_h(without_tail) == 73.9 and figure_h(with_tail) == 69.6
    assert not [e for e in logs if e["event"] == "slide_figure_caption_squeezed"]
    # Pavimento raggiunto con budget non clampato: solo la figura con la coda.
    caption = _caption_crossing(tail, lines_without=5)
    html, logs = _render(lesson_for(caption, _B5), {"F": svg, "A": _V11})
    with_tail, without_tail = html.split('<div class="slide">')[1:]
    assert figure_h(with_tail) == 25.0 and figure_h(without_tail) == 28.5
    squeezed = [e for e in logs if e["event"] == "slide_figure_caption_squeezed"]
    assert [(e["asset_id"], e["format"], e["box_h_mm"]) for e in squeezed] == [
        ("F", "function", 25.0)
    ]
    assert not [e for e in logs if e["event"] == "slide_figure_box_exhausted"]


# ---------------------------------------------------------------------------
# Equazioni: senza box, la regola generica ripiega sugli 80 mm
# ---------------------------------------------------------------------------

# Attributi esterni di due SVG MathJax reali (`_prerender_math_for_slides`):
# `aligned` di 8 righe con frazioni (107 mm al naturale nella slide) e
# `pmatrix` di 20 righe (132 mm). Il corpo è un rettangolo: la geometria
# resa dipende solo da `width`/`height` in ex e dal `viewBox`.
_MATHJAX_HEADS = {
    "aligned8": ('width="25.7ex" height="50.542ex"', "0 -11419.7 11359.4 22339.5", "-24.705ex"),
    "pmatrix20": ('width="7.041ex" height="62.443ex"', "0 -14050 3112.1 27600", "-30.656ex"),
}
_EQ_LATEX = r"\begin{aligned} x \end{aligned}"
_B3 = ["Primo punto", "Secondo punto", "Terzo punto"]


def _mathjax_stub(name: str) -> str:
    size, view_box, align = _MATHJAX_HEADS[name]
    x, y, w, h = view_box.split()
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" {size} role="img" focusable="false" '
        f'viewBox="{view_box}" aria-hidden="true" style="vertical-align: {align};">'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}"/></svg>'
    )


def _equation_html(name: str, bullets: list[str]) -> str:
    """Slide dedicata a un'equazione alta, resa senza split come nel video."""
    equation = {"equation_id": "E1", "latex": _EQ_LATEX, "label": "Derivazione"}
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw={
            "introduction": "[EQ:E1]",
            "sections": [],
            "summary": "",
            "visual_assets": [],
            "equations": [{**equation, "explanation": ""}],
        },
        slides_raw={"slides": [_slide("e", "Derivazione", ["E1"], bullets=bullets)]},
    )
    key = (pdf._normalize_math_source(_EQ_LATEX), "block")
    return slides_pdf.render_slides_html(
        course=Course(title="Corso", language_code="it", cfu=6),
        lesson=lesson,
        organization=None,
        slide_template=None,
        enable_split=False,
        math_svg_map={key: _mathjax_stub(name)},
    )


def _equation_geometry(weasyprint: Any, html: str) -> dict[str, Any]:
    """Pagina unica: altezza e fondo dell'SVG della formula, fondo (con
    margine) del `div.slide-asset`, fondo del `.slide-body`."""
    (page,) = weasyprint.HTML(string=html).render().pages
    found: dict[str, Any] = {"svg_h": None, "svg_bottom": None, "asset_bottom": None}
    for box in _walk(page._page_box):
        tag, classes = box.element_tag, _classes(box)
        if type(box).__name__.endswith("ReplacedBox") and str(tag).endswith("svg"):
            found["svg_h"] = box.height / _MM
            found["svg_bottom"] = (box.position_y + box.height) / _MM
        elif _is_block(box) and tag == "div" and "slide-asset" in classes:
            found["asset_bottom"] = _bottom_mm(box)
        elif _is_block(box) and tag == "div" and "slide-body" in classes:
            found["body_bottom"] = (box.position_y + box.height) / _MM
    return found


@pytest.mark.parametrize("name", sorted(_MATHJAX_HEADS))
def test_tall_equations_keep_the_80mm_fallback(name: str) -> None:
    """Il blocco equazione non riceve box: `var(--figure-h, 80mm)` nella
    regola generica gli ridà il cap di prima. Da sola sotto il titolo la
    formula alta sta nel body; con tre bullet (slide legacy nel video)
    resta sulla pagina a 80 mm, come prima di D12. Controprova: senza il
    ripiego la formula sborda di oltre 5 mm, e con i bullet la `pmatrix`
    sparisce dalla pagina."""
    weasyprint = _weasyprint()
    html = _equation_html(name, [])
    assert '<figure class="equation">' in html and "--figure-h" not in html.split("</style>")[1]
    alone = _equation_geometry(weasyprint, html)
    assert alone["svg_h"] == pytest.approx(80.0, abs=_TOL)
    assert alone["asset_bottom"] <= alone["body_bottom"] + _TOL, alone
    legacy = _equation_geometry(weasyprint, _equation_html(name, _B3))
    assert legacy["svg_h"] == pytest.approx(80.0, abs=_TOL), legacy

    def without_fallback(source: str) -> str:
        assert source.count("max-height: var(--figure-h, 80mm);") == 1
        return source.replace("max-height: var(--figure-h, 80mm);", "")

    uncapped = _equation_geometry(weasyprint, without_fallback(html))
    assert uncapped["svg_h"] > 100
    assert uncapped["asset_bottom"] > uncapped["body_bottom"] + 5, uncapped
    if name == "pmatrix20":
        gone = _equation_geometry(weasyprint, without_fallback(_equation_html(name, _B3)))
        assert gone["svg_h"] is None and gone["asset_bottom"] is None, gone


def test_video_frame_keeps_the_80mm_fallback_for_equations() -> None:
    """Stesso cap nel frame video (Chromium con `_VIDEO_OVERRIDE_CSS`): la
    formula `aligned` di 8 righe è alta 80 mm e sta sopra il fondo del
    `.slide-body`. Skip solo se Chromium non si avvia."""
    sync_api = pytest.importorskip("playwright.sync_api")
    html = _equation_html("aligned8", [])
    html_video = html.replace("</head>", video._VIDEO_OVERRIDE_CSS + "</head>", 1)
    scale = video._VIDEO_SLIDE_SCALE * _MM  # px per mm sullo schermo
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])
        try:
            page = browser.new_page(
                viewport={"width": video.VIDEO_WIDTH, "height": video.VIDEO_HEIGHT}
            )
            page.set_content(html_video)
            frame = page.evaluate(
                """() => {
                    const body = document.querySelector('.slide-body').getBoundingClientRect();
                    const svg = document.querySelector('.slide-asset .math-block svg')
                        .getBoundingClientRect();
                    return {bodyBottom: body.bottom, svgBottom: svg.bottom, svgH: svg.height};
                }"""
            )
        finally:
            browser.close()
    assert frame["svgH"] / scale == pytest.approx(80.0, abs=0.1)
    assert frame["svgBottom"] <= frame["bodyBottom"] + 0.5 * scale, frame


# ---------------------------------------------------------------------------
# Formule nella prosa (WP4): il budget resta un limite superiore
# ---------------------------------------------------------------------------

# Attributi esterni di SVG MathJax 3 reali (`_prerender_math_to_svg_batch`):
# `width`, `height`, `viewBox`, `vertical-align`. Il corpo è un rettangolo:
# la geometria resa dipende solo da questi.
_PROSE_MATH_HEADS: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("x", "inline"): ("1.294ex", "1.025ex", "0 -442 572 453", "-0.025ex"),
    ("G = 2^{10}", "inline"): ("7.714ex", "2.072ex", "0 -833.9 3409.7 915.9", "-0.186ex"),
    ("\\frac{a}{b}", "inline"): ("1.842ex", "2.395ex", "0 -705.8 814.1 1058.6", "-0.798ex"),
    ("\\sum_{i=1}^{n} i", "inline"): ("6.331ex", "2.563ex", "0 -789.6 2798.3 1132.9", "-0.777ex"),
    ("\\int_0^1 \\frac{x^2}{\\sqrt{1+x}}\\,dx", "inline"): (
        "11.042ex",
        "3.604ex",
        "0 -1003.5 4880.4 1593",
        "-1.334ex",
    ),
    ("\\begin{pmatrix}a&b\\\\c&d\\end{pmatrix}", "inline"): (
        "7.966ex",
        "5.43ex",
        "0 -1450 3521 2400",
        "-2.149ex",
    ),
    ("\\left(\\sum_{k=0}^{\\infty} \\frac{x^k}{k!}\\right)", "inline"): (
        "11.166ex",
        "4.07ex",
        "0 -1149.5 4935.4 1799",
        "-1.469ex",
    ),
    ("\\begin{cases} a & x>0 \\\\ b & x=0 \\\\ c & x<0 \\end{cases}", "inline"): (
        "10.913ex",
        "7.692ex",
        "0 -1950 4823.6 3400",
        "-3.281ex",
    ),
    ("x^{2^{2^{2}}}_{i_{j_{k}}}", "inline"): (
        "3.64ex",
        "3.847ex",
        "0 -1163.4 1608.7 1700.3",
        "-1.215ex",
    ),
    ("\\sum_{i=1}^{n} i", "block"): ("4.425ex", "6.354ex", "0 -1562.5 1955.7 2808.5", "-2.819ex"),
    ("\\int_{-\\infty}^{\\infty} e^{-x^2}\\,dx = \\sqrt{\\pi}", "block"): (
        "17.852ex",
        "5.328ex",
        "0 -1400.6 7890.7 2354.9",
        "-2.159ex",
    ),
}
_INLINE_KEYS = [k for k in _PROSE_MATH_HEADS if k[1] == "inline"]
_BLOCK_KEYS = [k for k in _PROSE_MATH_HEADS if k[1] == "block"]


def _prose_svg(key: tuple[str, str]) -> str:
    width, height, view_box, align = _PROSE_MATH_HEADS[key]
    x, y, w, h = view_box.split()
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'role="img" focusable="false" viewBox="{view_box}" aria-hidden="true" '
        f'style="vertical-align: {align};"><rect x="{x}" y="{y}" width="{w}" height="{h}"/></svg>'
    )


_PROSE_SVG_MAP = {key: _prose_svg(key) for key in _PROSE_MATH_HEADS}


def _inline_math(key: tuple[str, str]) -> str:
    return f"${key[0]}$"


def _block_math(key: tuple[str, str]) -> str:
    return f"$$\n{key[0]}\n$$"


def _prose_cases() -> list[tuple[str, str]]:
    """(stile, testo): ogni formula in linea da sola, ripetuta fino a più
    righe e in una frase con tutte le altre; le formule a blocco fra due
    frasi e in testa."""
    cases: list[tuple[str, str]] = []
    everything = " e ".join(_inline_math(k) for k in _INLINE_KEYS)
    for style in ("title", "body", "bullet"):
        for key in _INLINE_KEYS:
            cases.append((style, f"Sia {_inline_math(key)} la grandezza"))
            cases.append((style, " ".join(f"passo {_inline_math(key)}," for _ in range(9))))
        cases.append((style, f"Tutte insieme: {everything} e basta"))
        for key in _BLOCK_KEYS:
            cases.append((style, f"Vale\n{_block_math(key)}\nper ogni n."))
            cases.append((style, f"{_block_math(key)}\ne {_inline_math(_INLINE_KEYS[5])} dopo"))
    return cases


_PROSE_STYLE_GEOMETRY = {
    # stile: (corpo pt, altezza di riga, larghezza mm, grassetto)
    "title": (_G.title_pt, _G.title_line_height, _G.body_w_mm, True),
    "body": (_G.body_text_pt, _G.body_text_line_height, _G.text_w_mm, False),
    "bullet": (_G.bullet_pt, _G.bullet_line_height, _G.bullet_w_mm, False),
}
_PROSE_MATH_CSS = (
    ".math-inline svg { display: inline; margin: 0; max-height: none; max-width: 100%; } "
    "span.math-block { display: block; } "
    ".math-block svg { display: block; margin: 0 auto; max-width: 100%; height: auto; }"
)


def _prose_estimate_mm(style: str, text: str) -> float:
    pt, line_height, width_mm, bold = _PROSE_STYLE_GEOMETRY[style]
    prose = slides_pdf._prose_for_budget(text, _PROSE_SVG_MAP)
    assert not isinstance(prose, str), text
    rows, extra_em = sg.prose_extent(
        prose, font_pt=pt, width_mm=width_mm, line_height=line_height, bold=bold
    )
    return rows * pt * line_height * sg.MM_PER_PT + extra_em * pt * sg.MM_PER_PT


def _prose_real_heights(weasyprint: Any, family: str, cases: list[tuple[str, str]]) -> list[float]:
    """Altezza resa (mm) di ogni campo, nello stile del template e con
    l'HTML di `render_markdown_inline`, in un solo documento su una pagina
    molto alta: un campo spezzato dal fondo pagina riceverebbe lo spazio
    bianco residuo (`weasyprint/layout/block.py`)."""
    probes = "".join(
        f'<div id="p{i}" class="t-{style}">{pdf.render_markdown_inline(text, _PROSE_SVG_MAP)}</div>'
        for i, (style, text) in enumerate(cases)
    )
    styles = " ".join(f".t-{name} {{ {css} }}" for name, css in _STYLE_CSS.items())
    html = (
        "<html><head><style>@page { size: 297mm 20000mm; margin: 0 } "
        f'body {{ margin: 0; font-family: "{family}"; }} {styles} {_PROSE_MATH_CSS}'
        f"</style></head><body>{probes}</body></html>"
    )
    heights: dict[int, float] = {}
    for page in weasyprint.HTML(string=html).render().pages:
        # Solo i figli diretti del body: i blocchi anonimi dentro un campo
        # (formula a blocco) portano lo stesso elemento del campo.
        body = next(b for b in _walk(page._page_box) if b.element_tag == "body")
        for box in body.children:
            probe = int(box.element.get("id")[1:])
            heights[probe] = heights.get(probe, 0.0) + box.height / _MM
    assert sorted(heights) == list(range(len(cases)))
    return [heights[i] for i in range(len(cases))]


@pytest.mark.parametrize("family", _FAMILIES)
def test_prose_math_extent_is_an_upper_bound(family: str) -> None:
    """Righe e altezza extra di `prose_extent` coprono l'altezza resa dei
    campi con formule reali (in linea alte, ripetute su più righe, a
    blocco) nei tre stili delle slide, per le sei famiglie; la sovrastima
    resta sotto le due righe più 1,5 em per formula."""
    weasyprint = _weasyprint()
    cases = _prose_cases()
    real = _prose_real_heights(weasyprint, family, cases)
    for (style, text), real_mm in zip(cases, real, strict=True):
        est_mm = _prose_estimate_mm(style, text)
        assert real_mm <= est_mm + _TOL, (family, style, text[:60], real_mm, est_mm)
        pt, line_height, _w, _b = _PROSE_STYLE_GEOMETRY[style]
        formulas = text.count("$$") // 2 + (text.count("$") - 2 * text.count("$$")) // 2
        slack = (2 * line_height + 1.5 * formulas) * pt * sg.MM_PER_PT
        assert est_mm <= real_mm + slack, (family, style, text[:60], real_mm, est_mm)


def test_the_prose_oracle_catches_the_missing_row_growth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controprova: senza la crescita di riga delle formule in linea, o con
    un ex più piccolo di quello dei font del template, la stima scende
    sotto l'altezza resa in più casi del corpus."""
    weasyprint = _weasyprint()
    cases = _prose_cases()
    real = _prose_real_heights(weasyprint, "Helvetica", cases)

    def under() -> int:
        return sum(
            1
            for (style, text), real_mm in zip(cases, real, strict=True)
            if real_mm > _prose_estimate_mm(style, text) + _TOL
        )

    assert under() == 0
    with monkeypatch.context() as m:
        m.setattr(sg, "_inline_math_overhang_em", lambda box, *, line_height: (0.0, 0.0))
        assert under() > 10
    with monkeypatch.context() as m:
        m.setattr(sg, "_EX_EM_MAX", 0.45)
        assert under() > 10


def test_prose_extent_without_math_is_the_old_estimate() -> None:
    """Senza formule il campo resta una stringa e la stima è quella di
    prima (la fixture dei budget non cambia); senza SVG la formula vale il
    suo sorgente come parola unica; un SVG illeggibile vale una riga."""
    assert slides_pdf._prose_for_budget("Costo $50 e $70", _PROSE_SVG_MAP) == "Costo $50 e $70"
    assert slides_pdf._prose_for_budget("", _PROSE_SVG_MAP) == ""
    for case in _CASES["lines"]:
        pt, line_height, width_mm, bold = _PROSE_STYLE_GEOMETRY.get(
            case["style"], (_G.caption_pt, 1.0, _G.body_w_mm, False)
        )
        if case["style"] not in _PROSE_STYLE_GEOMETRY:
            continue
        rows, extra = sg.prose_extent(
            case["text"], font_pt=pt, width_mm=width_mm, line_height=line_height, bold=bold
        )
        assert (rows, extra) == (
            sg.estimate_lines(case["text"], font_pt=pt, width_mm=width_mm, bold=bold),
            0.0,
        )
    no_svg = slides_pdf._prose_for_budget("Sia $a + b$ fine", {})
    assert no_svg == ("Sia ", sg.ProseMath("$a + b$"), " fine")
    rows, extra = sg.prose_extent(no_svg, font_pt=13, width_mm=100, line_height=1.45)
    assert (rows, extra) == (1, 0.0)
    odd = ("Sia ", sg.ProseMath("$x$", svg='<svg width="100%"></svg>'), " fine")
    rows, extra = sg.prose_extent(odd, font_pt=13, width_mm=100, line_height=1.45)
    assert (rows, extra) == (1, 1.45)
    box = sg.svg_inline_box(_PROSE_SVG_MAP[("\\frac{a}{b}", "inline")], font_pt=13)
    assert box == sg.SvgInlineBox(
        w_em=pytest.approx(1.842 * 0.58),
        h_em=pytest.approx(2.395 * 0.58),
        depth_em=pytest.approx(0.798 * 0.58),
    )
    assert sg.svg_inline_box('<svg stroke-width="3" height="12pt" width="24px">', font_pt=12) == (
        sg.SvgInlineBox(w_em=pytest.approx(1.5), h_em=pytest.approx(1.0), depth_em=0.0)
    )


def _math_prose_lesson() -> tuple[CourseLesson, dict[str, Any], dict[str, str]]:
    """Tre pagine con figura alta e formule alte nella prosa, rese senza
    split come nel video: slide dedicata di Fase 4 (titolo con formula,
    prosa con formule in linea e a blocco), slide legacy con tre bullet di
    formule alte, titolo su due righe con frazioni."""
    k = _INLINE_KEYS
    body = (
        f"Con {_inline_math(k[7])} e {_inline_math(k[5])} si ottiene\n"
        f"{_block_math(_BLOCK_KEYS[1])}\ncome {_inline_math(k[6])}."
    )
    bullets = [
        f"Il caso {_inline_math(k[7])} e {_inline_math(k[5])}",
        f"La somma {_inline_math(k[6])} converge",
        f"Gli indici {_inline_math(k[8])} e l'integrale {_inline_math(k[4])}",
    ]
    title_long = " ".join(f"Rapporto {_inline_math(k[2])}" for _ in range(8))
    content_raw = {
        "introduction": "[FIG:T]",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("T", "mermaid", "flowchart TD\n A --> B", "Sequenza alta")],
    }
    slides_raw = {
        "slides": [
            _slide("m1", f"Serie {_inline_math(k[3])}", ["T"], body=body),
            _slide("m2", _T1, ["T"], bullets=bullets),
            _slide("m3", title_long, ["T"]),
        ]
    }
    lesson = CourseLesson(
        lesson_code="M1.L1", title="Lezione", content_raw=content_raw, slides_raw=slides_raw
    )
    return lesson, {"T": _fluid_svg(650, 907)}, dict(_PROSE_SVG_MAP)


def _render_math_prose(math_map: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    lesson, svg_map, _ = _math_prose_lesson()
    with structlog.testing.capture_logs() as logs:
        html = slides_pdf.render_slides_html(
            course=Course(title="Corso", language_code="it", cfu=6),
            lesson=lesson,
            organization=None,
            slide_template=None,
            enable_split=False,
            visual_svg_map=svg_map,
            math_svg_map=math_map,
        )
    return html, logs


def test_tall_prose_formulas_keep_the_figure_inside_the_body() -> None:
    """Con formule alte nel titolo, nella prosa e nei bullet la figura resta
    sopra il fondo del `.slide-body` e dentro `--figure-h` in WeasyPrint;
    tutte le formule sono SVG (nessun ripiego) e il box è sul `<figure>`.
    Controprova: con il budget di prima (sorgente come testo, nessuna
    altezza di riga in più) le tre pagine sbordano."""
    weasyprint = _weasyprint()
    lesson, _svg_map, math_map = _math_prose_lesson()
    collected = set(
        pdf._collect_math_from_content(
            slides_pdf._math_content_for_slides(lesson.content_raw, lesson.slides_raw)
        )
    )
    assert collected <= set(math_map)
    math_svg_map = pdf.MathSvgMap(math_map, requested=len(collected))
    html, logs = _render_math_prose(math_svg_map)
    assert math_svg_map.misses == []
    assert "<math" not in html
    assert len(re.findall(r'<figure class="visual[^>]*style="--figure-w: 255\.0mm; ', html)) == 3
    pages = _page_geometry(weasyprint, html)
    assert len(pages) == 3
    for number, info in enumerate(pages, start=1):
        (bottom,) = info["assets"]
        assert bottom <= info["body_bottom"] + _TOL, (number, bottom, info["body_bottom"])
        for height, cap in info["images"]:
            assert cap is not None and height <= cap + _TOL, (number, height, cap)
    assert not [e for e in logs if e["event"] == "slide_figure_box_exhausted"]

    def old_budget(text: str, _map: Any) -> str:
        return text

    with pytest.MonkeyPatch.context() as mp_ctx:
        mp_ctx.setattr(slides_pdf, "_prose_for_budget", old_budget)
        old_html, _old_logs = _render_math_prose(pdf.MathSvgMap(math_map))
    old_pages = _page_geometry(weasyprint, old_html)
    overflow = [max(p["assets"]) - p["body_bottom"] for p in old_pages]
    assert overflow[0] > 5 and overflow[1] > 5, overflow
    # Il titolo della terza pagina misurato sugli SVG (più stretti del
    # sorgente) sta su meno righe: la figura guadagna altezza.
    new_h = [float(h) for h in _FIGURE_H_RE.findall(html)]
    old_h = [float(h) for h in _FIGURE_H_RE.findall(old_html)]
    assert new_h[0] < old_h[0] and new_h[1] < old_h[1] and new_h[2] > old_h[2], (new_h, old_h)


def test_tall_prose_formulas_keep_the_figure_inside_the_video_frame() -> None:
    """Stesso HTML nei frame video (Chromium con `_VIDEO_OVERRIDE_CSS`):
    ogni `.slide-asset` (più il margine) sta sopra il fondo del
    `.slide-body` e ogni immagine dentro `--figure-h`."""
    html, _logs = _render_math_prose(pdf.MathSvgMap(_PROSE_SVG_MAP))
    scale = video._VIDEO_SLIDE_SCALE * _MM
    frames = _video_frames(html, 3)
    for number, frame in enumerate(frames, start=1):
        (bottom,) = frame["assets"]
        worst = (bottom + _G.asset_margin_mm * scale - frame["bodyBottom"]) / scale
        assert worst <= 0.5, (number, worst)
        for height_px, cap in frame["images"]:
            assert height_px / scale <= float(cap[:-2]) + 0.1, (number, height_px / scale, cap)
