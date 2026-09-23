"""Preambolo fisso del formato `tikz` (WP6, puro, senza I/O).

Il corpo della figura (un solo ambiente `tikzpicture` o `circuitikz`,
controllato da `tikz_lexer`) viene incluso in un documento XeLaTeX che il
server scrive per intero: nessun pacchetto, libreria o macro arriva dal
contenuto.

- `article` 10 pt, `\\tracinglostchars=3` (un glifo mancante è un errore,
  mai un quadratino), `fontspec` con il font scelto DAL CONTENUTO
  (`font_for`), non dalla lingua del corso: la chiave della cache degli SVG
  non contiene la lingua. Matematica in Latin Modern (default di XeLaTeX);
- TikZ con le sole librerie ammesse (niente `external`, `shadings`,
  `fadings`); `circuitikz` (stile europeo, IEC) e `pgfplots` solo se il
  corpo li usa;
- `preview` con `tightpage` ritaglia la pagina sulla figura (niente
  `standalone`, che in Debian sta in `texlive-latex-extra`);
- colori `a4u*` dalla palette di `figure_theme` (stessi colori delle altre
  figure), stile di base `line width=0.6pt`, frecce `Stealth`.

`PREAMBLE_VERSION` entra nella chiave di cache: un cambio del preambolo
invalida solo le figure `tikz`.
"""

from __future__ import annotations

import re
import unicodedata

from app.services.figure_theme import (
    COLOR_AXIS,
    COLOR_GRID,
    COLOR_INK,
    COLOR_MUTED,
    COLOR_SURFACE,
    PALETTE,
)

PREAMBLE_VERSION = "tikz-2026.09.1"

TIKZ_LIBRARIES: tuple[str, ...] = (
    "positioning",
    "arrows.meta",
    "calc",
    "fit",
    "backgrounds",
    "shapes.geometric",
    "shapes.misc",
    "decorations.pathreplacing",
    "decorations.markings",
    "quotes",
    "angles",
)

FONT_LATIN = "Noto Sans"
FONT_JP = "Noto Sans CJK JP"
FONT_KR = "Noto Sans CJK KR"
FONT_SC = "Noto Sans CJK SC"

# Nomi dei colori della palette e dei neutri, in ordine.
PALETTE_COLOR_NAMES: tuple[str, ...] = tuple(f"a4uC{i}" for i in range(len(PALETTE)))
NEUTRAL_COLORS: dict[str, str] = {
    "a4uInk": COLOR_INK,
    "a4uAxis": COLOR_AXIS,
    "a4uGrid": COLOR_GRID,
    "a4uMuted": COLOR_MUTED,
    "a4uSurface": COLOR_SURFACE,
}

_AXIS_RE = re.compile(r"\\begin\{(?:axis|semilogxaxis|semilogyaxis|loglogaxis)\}")
_CIRCUIT_RE = re.compile(r"\\begin\{circuitikz\}|\bto\s*\[")
# Blocchi Unicode supportati dai font Noto installati: latino, greco,
# cirillico, CJK. Altri script (arabo, ebraico, indiani, thai) no.
_UNSUPPORTED_SCRIPTS = (
    "ARABIC",
    "HEBREW",
    "DEVANAGARI",
    "BENGALI",
    "GURMUKHI",
    "GUJARATI",
    "TAMIL",
    "TELUGU",
    "KANNADA",
    "MALAYALAM",
    "THAI",
    "LAO",
    "TIBETAN",
    "MYANMAR",
    "ETHIOPIC",
    "KHMER",
    "SINHALA",
    "ARMENIAN",
    "GEORGIAN",
)


class TikzScriptUnsupportedError(ValueError):
    """Il testo della figura usa uno script senza font nel preambolo."""


def _name(ch: str) -> str:
    try:
        return unicodedata.name(ch)
    except ValueError:
        return ""


def font_for(source: str) -> str:
    """Font sans del preambolo scelto dai caratteri della figura."""
    kana = hangul = ideograph = False
    for ch in source:
        if ord(ch) < 0x0370:
            continue
        name = _name(ch)
        if name.startswith(("HIRAGANA", "KATAKANA")):
            kana = True
        elif name.startswith("HANGUL"):
            hangul = True
        elif name.startswith("CJK UNIFIED IDEOGRAPH") or name.startswith("CJK COMPATIBILITY"):
            ideograph = True
        elif name.startswith(_UNSUPPORTED_SCRIPTS):
            raise TikzScriptUnsupportedError(f"script non supportato: {name.split()[0].lower()}")
    if kana:
        return FONT_JP
    if hangul:
        return FONT_KR
    if ideograph:
        return FONT_SC
    return FONT_LATIN


def uses_axis(source: str) -> bool:
    return bool(_AXIS_RE.search(source))


def uses_circuit(source: str) -> bool:
    return bool(_CIRCUIT_RE.search(source))


def _hex(value: str) -> str:
    return value.lstrip("#").upper()


def preamble(*, font: str, pgfplots: bool, circuit: bool) -> str:
    """Preambolo completo fino a `\\begin{document}` compreso."""
    colors = [
        f"\\definecolor{{{name}}}{{HTML}}{{{_hex(value)}}}"
        for name, value in zip(PALETTE_COLOR_NAMES, PALETTE, strict=True)
    ] + [
        f"\\definecolor{{{name}}}{{HTML}}{{{_hex(value)}}}"
        for name, value in NEUTRAL_COLORS.items()
    ]
    lines = [
        "\\documentclass[10pt]{article}",
        "\\usepackage{fontspec}",
        f"\\setsansfont{{{font}}}",
        "\\renewcommand{\\familydefault}{\\sfdefault}",
        "\\usepackage{amsmath}",
        "\\usepackage{amssymb}",
        "\\usepackage{xcolor}",
        "\\usepackage{tikz}",
        "\\usetikzlibrary{" + ",".join(TIKZ_LIBRARIES) + "}",
    ]
    if circuit:
        lines.append("\\usepackage[european]{circuitikz}")
    if pgfplots:
        lines += ["\\usepackage{pgfplots}", "\\pgfplotsset{compat=1.18}"]
    lines += [
        "\\usepackage[active,tightpage]{preview}",
        "\\PreviewEnvironment{tikzpicture}",
        "\\PreviewEnvironment{circuitikz}",
        "\\setlength\\PreviewBorder{2pt}",
        *colors,
        "\\tikzset{every picture/.style={line width=0.6pt,>={Stealth},node distance=12mm,"
        "color=a4uInk},every node/.style={font=\\small}}",
        "\\pagestyle{empty}",
        "\\tracinglostchars=3",
        "\\begin{document}",
    ]
    return "\n".join(lines) + "\n"


def document(body: str) -> tuple[str, int]:
    """Documento completo e numero di righe del preambolo (per riportare
    gli errori alla riga del corpo)."""
    head = preamble(font=font_for(body), pgfplots=uses_axis(body), circuit=uses_circuit(body))
    return head + body.rstrip("\n") + "\n\\end{document}\n", head.count("\n")
