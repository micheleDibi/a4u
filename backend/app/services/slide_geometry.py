"""Geometria delle slide (D12): box della figura per pagina effettiva.

Modulo puro (libreria standard più `figure_markup.FigureBox`), specchio
delle costanti CSS di `templates/lesson_slides_pdf.html.j2`: ogni campo di
`SlideGeometry` porta il riferimento alla riga del template e un test a
regex (`tests/test_slide_figure_geometry.py`) li tiene allineati.

Modello additivo del `.slide-body` (255 × 120 mm, `overflow: hidden`):

    tag + titolo + prosa + bullet + margine degli asset
    + Σ blocchi (immagine + didascalia + margine didascalia) + safety

`page_figure_budget` calcola, per una pagina RESA (dopo la decisione di
split), il budget verticale di ogni blocco asset; `image_box` lo converte
nel box dell'immagine sottraendo la didascalia stimata sul testo reale
(fino a 600 caratteri più la coda calcolata di `function`);
`truncate_fallback_source` taglia il sorgente del `<pre>` di fallback al
numero di righe che entrano nel box, perché WeasyPrint ignora
`max-height` sui blocchi frammentati dal fondo pagina
(`weasyprint/layout/block.py`, ramo «fill the blank space at the bottom of
the page»).

`estimate_lines` è un LIMITE SUPERIORE delle righe rese sul testo delle
slide, indipendente dal font: larghezze per classe di carattere
(maiuscola, minuscola, cifra, spazio, CJK) invece di una media a 0,5 em,
che sottostima i titoli in maiuscolo su Verdana, Noto Sans, DejaVu Sans e
Liberation Sans (una riga di titolo vale 10,37 mm contro 3 mm di
safety), più l'avanzamento massimo misurato dei simboli larghi (`%`, `@`,
`&`, `—`, `→`, `±`, `«`, …), rari nella prosa. Le classi delle lettere sono
medie prudenti, non massimi per glifo: una sequenza artificiale di sole
`m`, `w` o `W` (0,97-1,13 em in Verdana e DejaVu Sans) può restare
sottostimata di una riga; è un limite dichiarato, fuori dai testi reali.
Calibrazione `real ≤ stima ≤ real + 1` sui `LineBox` reali di WeasyPrint
per sei famiglie nel test del modulo.

Il `<pre>` di fallback delle slide NON va a capo (`white-space: pre`,
settimo giro di verifica di D12): una riga sorgente è una riga resa, e
`overflow: hidden` taglia a destra le righe più larghe del box. Sei giri
di stima delle righe a capo in `pre-wrap` (spazi, tab, separatori
Unicode, profili di lingua, larghezze per script, regole UAX #14 di
Pango) sono stati battuti ognuno da un caso nuovo; senza a capo il
conteggio è esatto in WeasyPrint e in Chromium, e non dipende né dal font
né dalla larghezza. Il costo è confinato al percorso d'errore di una
figura non resa (log `figure_render_fallback`): le righe lunghe del
sorgente si leggono solo fino al bordo destro. Separatori di riga
(`_SOURCE_LINE_BREAK_RE`, misura su tutti i caratteri Cc, Cf, Zs, Zl e Zp
e sul BMP, nel container e in locale, e sui piani 1 e 2 nel container):
`\n`, `\r\n` e `\r` in entrambi i motori (l'HTML normalizza CR), U+2028
e U+2029 solo in WeasyPrint (Pango ci va a capo anche in `pre`); NEL, FF
e VT in nessuno dei due. La riga con un separatore conta come spezzata e
`truncate_fallback_source` riscrive ogni separatore come `\n`, così i due
motori rendono le stesse righe; i NUL, che il parser HTML scarta, non
contano.

Altezza della riga resa: 1,3 em solo per l'insieme BASE (`_MONO_BASE_RE`:
ASCII, Latin-1, Latin Extended-A, greco e cirillico di base,
punteggiatura, frecce, operatori e filetti d'uso comune, tutti nel font
mono, verificati carattere per carattere) con una lingua del corso NEUTRA
(vi compresa, senza greco, cirillico, ∏, ∑ e ∫: `_MONO_VI_NOT_BASE_RE`).
Altrimenti la riga resa può prendere le metriche di un altro font (misure
nel container, `fonts-noto-core` e `fonts-noto-cjk`, su ogni lingua nota
a fontconfig):

- la lingua del corso (`<html lang>`) cambia il font da cui WeasyPrint
  ricava la riga base del `<pre>` (`fc-match monospace:lang=xx`): con
  ja, ko, zh-cn una riga tutta ASCII è alta 1,39 em, con kn 1,55, con bo
  1,59;
- i caratteri fuori dall'insieme base cadono su un altro font: la riga
  cresce a 1,3 em + |ΔA−D|/2, dove A−D è ascendente meno discendente
  dei font sulla riga (DejaVu 0,692, Noto Sans CJK 0,872, Noto Serif
  Kannada 0,200, Noto Serif Tibetan 0,117).

Per queste righe il modello è prudente: ogni riga resa vale
`fallback_tall_line_budget` (1,70 em = 1,3 + (0,917 − 0,117)/2, cioè ogni
combinazione di font con A−D fra 0,117 di Noto Serif Tibetan e 0,917, poco
sopra Noto Sans Symbols, 0,910; il massimo misurato su 44 lingue × 31
script è 1,6775 em; su 302 lingue × 75 campioni lo superano solo mn-cn,
riga base in Noto Sans Mongolian con A−D 1,164, e tcy, che valgono 1,83 e
1,76 em: `_MONO_TALL_LINE_EM`; una riga con mongolo tradizionale vale
1,83 em in ogni corso). Limiti dichiarati: script con A−D fuori banda
insieme a font all'altro estremo sulla stessa riga (Nastaliq 1,308 o
mongolo 1,164 accanto a Siddham −0,030 o Myanmar Serif −0,021; il mongolo
accanto a tibetano, 0,117, e kannada, 0,200, è coperto) e immagini con
altri font o un altro fontconfig (i profili di lingua valgono per il
container).

Il modello «1,3 em + |ΔA−D|/2» vale solo se le run della riga condividono
la baseline romana. Pango (≥ 1.50, `apply_baseline_shift`) allinea le run
di font diversi sulla baseline dello script della PRIMA run della riga
(paragrafo: Pango itemizza fra due a capo), cioè del primo carattere con
script reale (non Common, Inherited o Unknown). Se quello script ha
baseline ideografica (Han, Hangul, Hiragana, Katakana, Bopomofo, Tangut,
Nüshu, Khitan: HarfBuzz `hb_ot_layout_get_horizontal_baseline_tag_for_script`),
le altre run si spostano della differenza fra le baseline ideografiche dei
font, sintetizzate dal discendente dove manca la tabella BASE: con
ideogrammi in testa e tibetano la riga è alta 2,006 em in it e 2,293 in bo
(ottavo giro). Con tag romano o sospeso (devanagari, tibetano, …) non c'è
spostamento: senza BASE HarfBuzz dà 0 e 0,6 em a ogni font, e le BASE del
container (Noto CJK) hanno gli stessi valori. Le righe con prima run
ideografica (`_ideographic_lead`) valgono `fallback_ideo_line_budget`,
2,46 em: la riga vale 1,3 em + |c_strut − c_testo|, con c = (alto − basso)/2
dei rettangoli logici spostati; c_testo sta fra i valori delle coppie
(prima run, altra run), e su tutte le coppie delle 321 facce del container
con BASE e ripieghi di HarfBuzz 10.2 c_testo arriva a 1,1405 e la strut di
qualunque font a −0,015, quindi la riga resta sotto 2,4555 em.
"""

from __future__ import annotations

import functools
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app.services.figure_markup import FigureBox

MM_PER_PT = 25.4 / 72

# Larghezze in em per classe di carattere (limite superiore per le
# famiglie sans del template: Helvetica, Arial, Verdana, Noto Sans,
# Liberation Sans, DejaVu Sans).
_EM_SPACE = 0.30
_EM_UPPER = 0.72
_EM_UPPER_BOLD = 0.76
_EM_LOWER = 0.58
_EM_LOWER_BOLD = 0.62
_EM_DIGIT = 0.60
_EM_OTHER = 0.45
_EM_WIDE = 1.0  # CJK e forme a larghezza piena (east_asian_width W/F)
# A capo del sorgente di fallback in `white-space: pre`: `\n`, `\r\n` e
# `\r` in WeasyPrint e Chromium, U+2028 e U+2029 solo in WeasyPrint
# (Pango); NEL, FF e VT in nessuno dei due.
_SOURCE_LINE_BREAK_RE = re.compile("\r\n|[\r\n\u2028\u2029]")
# Insieme base del fallback: caratteri resi dal font mono del template su
# righe da 1,3 em, nel container (DejaVu Sans Mono) e in locale (Menlo),
# per ogni lingua neutra.
_MONO_BASE_RE = re.compile(
    "[\t\x20-\x7e\xa0-\u017f"
    "\u0384-\u038a\u038c\u038e-\u03a1\u03a3-\u03ce\u0400-\u045f"
    "\u2013\u2014\u2018-\u201e\u2020-\u2022\u2026\u2030\u2039\u203a\u20ac"
    "\u2190-\u2195\u21d0-\u21d4"
    "\u2200\u2202\u2203\u2205\u2207-\u2209\u220f\u2211\u2212\u221a\u221e"
    "\u2227-\u222b\u2248\u2260\u2261\u2264\u2265\u2282\u2283\u2286\u2287"
    "\u2500-\u257f]*"
)
# Profili di lingua del `<pre>`: neutra (riga base di DejaVu Sans Mono) o
# altra (riga base più alta o insieme base fuori dal font mono).
_MONO_NEUTRAL = "neutral"
_MONO_OTHER = "other"
# Sottotag primari per cui `fc-match monospace:lang=xx`, nel container, non
# dà DejaVu Sans Mono (fonts-noto-core e fonts-noto-cjk): riga base più
# alta o insieme base fuori modello (sh: Noto Mono, 154 caratteri base su
# righe da 3,324 mm; ja, ko e zh: Noto Sans Mono CJK o riga base CJK).
# «zh» senza regione dà DejaVu Sans Mono ma le varianti no: è nell'elenco
# per prudenza. Codici di tre lettere: sempre «altra» (fra quelli provati
# byn e hne cambiano font). vi (Noto Sans Mono) ha righe da 1,3 em
# sull'insieme base meno `_MONO_VI_NOT_BASE_RE`: è neutra con quella
# esclusione.
_MONO_TALL_LANGS = frozenset(
    code
    for group in (
        "ab am as ba bh bn bo cu dv dz gu he hi ii iu ja km kn ko ks",
        "lo ml mr my ne or pa sa sd sh si ta te tg th ug ur yi zh",
    )
    for code in group.split()
)
# Tag con regione che nel container cambiano font rispetto al sottotag
# primario (ber-ma, ku-iq, ku-ir: DejaVu Sans; mn-cn: Noto Sans Mongolian;
# pa-pk, ps-af, ps-pk: Noto Kufi Arabic, riga 3,242 mm; ti-er, ti-et: Noto
# Sans Ethiopic, riga 3,314 mm).
_MONO_TALL_TAGS = (
    "ber-ma",
    "ku-iq",
    "ku-ir",
    "mn-cn",
    "pa-pk",
    "ps-af",
    "ps-pk",
    "ti-er",
    "ti-et",
)
# Con vi il font della riga è Noto Sans Mono: greco e cirillico in una riga
# pura passano a DejaVu Sans Mono (righe da 3,314 mm), come ∏, ∑ e ∫. Tutto
# il resto dell'insieme base resta su righe da 3,210 mm, anche ⇐, ⇒, ⇔ e ∅
# (avanzano di 1,2 em, ma senza a capo la larghezza non conta).
_MONO_VI_NOT_BASE_RE = re.compile("[\u0384-\u03ce\u0400-\u045f\u220f\u2211\u222b]")
# Riga alta (em) per le lingue in cui la riga resa supera il budget comune
# (misure nel container su 302 lingue × 75 campioni): mn-cn prende Noto
# Sans Mongolian (A−D 1,164), con kannada o tibetano la riga è alta
# 4,40-4,50 mm (1,3 + (1,164 − 0,117)/2 = 1,8235 em); con tcy, che Pango
# non conosce, gli spazi e i simboli accanto a ideogrammi, kana e Hangul
# restano in Noto Serif Kannada accanto a Noto Sans CJK: 4,317 mm
# (1,7485 em). Il mongolo tradizionale in un corso qualunque alza la riga
# come in mn-cn: le sue righe valgono almeno 1,83 em.
_EM_TALL_MONGOLIAN = 1.83
_MONO_TALL_LINE_EM: dict[str, float] = {"mn-cn": _EM_TALL_MONGOLIAN, "tcy": 1.76}
_MONGOLIAN_RE = re.compile("[\u1800-\u18af\U00011660-\U0001167f]")
# Blocchi che contengono ogni carattere degli script con baseline
# ideografica (Han, Hangul, Hiragana, Katakana, Bopomofo, Tangut, Nüshu,
# Khitan) in Unicode 15, 16 e 17; ci sono anche caratteri Common (per
# esempio la punteggiatura CJK): contarli come ideografici è prudente.
_IDEO_SCRIPT_RE = re.compile(
    "[\u02ea\u02eb\u1100-\u11ff\u2e80-\u2fdf\u3000-\u312f\u3130-\u318f\u31a0-\u31bf"
    "\u31f0-\u4dbf\u4e00-\u9fff\ua960-\ua97f\uac00-\ud7ff\uf900-\ufaff\uff66-\uffdc"
    "\U00016fe0-\U00016fff\U00017000-\U00018dff\U0001aff0-\U0001b2ff\U0001f200"
    "\U00020000-\U0003ffff]"
)
# Lettere (categoria L*) fuori da `_IDEO_SCRIPT_RE` con script non reale
# (Common o Inherited) in almeno una fra Unicode 15, 16 e 17: modificatori,
# simboli letterali, alfanumerici matematici. Blocchi interi: una lettera
# reale contata qui non decide la prima run, ed è prudente.
_NOT_REAL_LETTER_RE = re.compile(
    "[\u00b5\u02b0-\u02ff\u0374\u0640\u1cd0-\u1cff\u2100-\u214f\u2e2f"
    "\ua700-\ua721\ua788\ua9cf\U0001d400-\U0001d7ff]"
)
_EPS = 1e-9
# Simboli più larghi della classe `_EM_OTHER`: (regolare, grassetto) come
# avanzamento massimo, arrotondato per eccesso, fra Helvetica, Arial,
# Verdana, Tahoma, Trebuchet MS e DejaVu Sans (misura sui file dei font).
# Senza, un titolo di soli `@` e `%` era stimato su una riga contro tre.
_EM_SYMBOLS: dict[str, tuple[float, float]] = {
    "%": (1.08, 1.28),
    "@": (1.02, 1.00),
    "∞": (1.00, 1.06),
    "…": (1.00, 1.05),
    "—": (1.00, 1.00),
    "←": (1.00, 1.00),
    "→": (1.00, 1.00),
    "&": (0.78, 0.88),
    "√": (0.82, 0.87),
    "«": (0.65, 0.85),
    "»": (0.65, 0.85),
    "€": (0.75, 0.75),
    "∑": (0.73, 0.72),
    **dict.fromkeys("#+<=>^~±×÷≤≥≠", (0.84, 0.87)),
    **dict.fromkeys("{}$*_`£–", (0.64, 0.72)),
}

# Marcatore della troncatura del fallback (U+2026): nessuna stringa UI.
TRUNCATION_MARK = "…"


@dataclass(frozen=True)
class SlideGeometry:
    """Costanti del template slide. Ogni campo è il mirror della regola
    CSS indicata («lesson_slides_pdf.html.j2:<riga>»)."""

    page_w_mm: float = 297.0  # mirror di lesson_slides_pdf.html.j2:27 (`.slide` width)
    page_h_mm: float = 210.0  # mirror di lesson_slides_pdf.html.j2:28 (`.slide` height)
    body_left_mm: float = 18.0  # mirror di lesson_slides_pdf.html.j2:106 (`.slide-body` left)
    body_right_mm: float = 24.0  # mirror di lesson_slides_pdf.html.j2:107 (right)
    body_top_mm: float = 35.0  # mirror di lesson_slides_pdf.html.j2:108 (top)
    body_bottom_mm: float = 55.0  # mirror di lesson_slides_pdf.html.j2:109 (bottom)
    tag_pt: float = 9.0  # mirror di lesson_slides_pdf.html.j2:114 (`.slide-tag` font-size)
    # `.slide-tag` ha `line-height: normal`: 3,18 mm in WeasyPrint (Helvetica),
    # ≈3,7 mm in Chromium → budget 1,5 × corpo (4,76 mm) copre entrambi.
    tag_line_budget: float = 1.5
    title_pt: float = 28.0  # mirror di lesson_slides_pdf.html.j2:122 (`.slide-title` font-size)
    title_line_height: float = 1.05  # mirror di lesson_slides_pdf.html.j2:124
    title_margin_top_mm: float = 3.0  # mirror di lesson_slides_pdf.html.j2:121 (margin)
    body_text_pt: float = 13.0  # mirror di lesson_slides_pdf.html.j2:133 (`.slide-body-text`)
    body_text_line_height: float = 1.45  # mirror di lesson_slides_pdf.html.j2:134
    body_text_margin_top_mm: float = 5.0  # mirror di lesson_slides_pdf.html.j2:132 (margin)
    body_text_max_width_ch: int = 72  # mirror di lesson_slides_pdf.html.j2:138 (max-width)
    ch_em: float = 0.556  # avanzamento dello «0» in em (Helvetica/Arial/Liberation)
    bullet_pt: float = 11.0  # mirror di lesson_slides_pdf.html.j2:151 (`.slide-bullets li`)
    bullet_line_height: float = 1.35  # mirror di lesson_slides_pdf.html.j2:152
    bullets_margin_top_mm: float = 5.0  # mirror di lesson_slides_pdf.html.j2:143 (`.slide-bullets`)
    bullet_margin_bottom_mm: float = 2.5  # mirror di lesson_slides_pdf.html.j2:153 (margin)
    bullet_gap_mm: float = 4.0  # mirror di lesson_slides_pdf.html.j2:150 (gap)
    bullet_dot_mm: float = 3.0  # mirror di lesson_slides_pdf.html.j2:160 (`li::before` width)
    assets_margin_top_mm: float = 4.0  # mirror di lesson_slides_pdf.html.j2:179 (`.slide-assets`)
    asset_margin_mm: float = 2.0  # mirror di lesson_slides_pdf.html.j2:183 (`.slide-asset` margin)
    caption_pt: float = 8.0  # mirror di lesson_slides_pdf.html.j2:221 (figcaption font-size)
    # figcaption a `line-height: normal`: 2,82 mm WeasyPrint, ≈3,3 Chromium → 1,5 × corpo.
    caption_line_budget: float = 1.5
    caption_margin_top_mm: float = 2.0  # mirror di lesson_slides_pdf.html.j2:224 (margin-top)
    fallback_pt: float = 7.0  # mirror di lesson_slides_pdf.html.j2:274 (`.figure-fallback`)
    fallback_line_height: float = 1.3  # mirror di lesson_slides_pdf.html.j2:275
    # Riga resa del fallback fuori dall'insieme base o con lingua non neutra
    # (vedi il docstring del modulo): 1,70 × corpo = 4,198 mm.
    fallback_tall_line_budget: float = 1.70
    # Riga resa del fallback con prima run a baseline ideografica (vedi il
    # docstring del modulo): 2,46 × corpo = 6,075 mm.
    fallback_ideo_line_budget: float = 2.46
    fallback_padding_v_mm: float = 2.0  # mirror di lesson_slides_pdf.html.j2:281 (padding)
    fallback_padding_h_mm: float = 3.0  # mirror di lesson_slides_pdf.html.j2:281 (padding)
    fallback_border_pt: float = 0.4  # mirror di lesson_slides_pdf.html.j2:279 (border)
    # Margine di sicurezza sulle differenze fra motori (WeasyPrint/Chromium)
    # e sull'arrotondamento; pavimento dell'immagine sotto cui la pagina è
    # dichiarata impossibile (`slide_figure_box_exhausted`).
    safety_mm: float = 3.0
    figure_min_h_mm: float = 25.0

    @property
    def body_w_mm(self) -> float:
        return self.page_w_mm - self.body_left_mm - self.body_right_mm  # 255

    @property
    def body_h_mm(self) -> float:
        return self.page_h_mm - self.body_top_mm - self.body_bottom_mm  # 120

    @property
    def body_bottom_y_mm(self) -> float:
        return self.page_h_mm - self.body_bottom_mm  # 155

    @property
    def tag_h_mm(self) -> float:
        return self.tag_pt * self.tag_line_budget * MM_PER_PT  # 4.7625

    @property
    def title_line_mm(self) -> float:
        return self.title_pt * self.title_line_height * MM_PER_PT  # 10.372

    @property
    def text_line_mm(self) -> float:
        return self.body_text_pt * self.body_text_line_height * MM_PER_PT  # 6.650

    @property
    def text_w_mm(self) -> float:
        """Larghezza della prosa: `max-width: 72ch` (183,6 mm con lo «0»
        a 0,556 em), mai oltre il body."""
        ch_mm = self.ch_em * self.body_text_pt * MM_PER_PT
        return min(self.body_w_mm, self.body_text_max_width_ch * ch_mm)

    @property
    def bullet_line_mm(self) -> float:
        return self.bullet_pt * self.bullet_line_height * MM_PER_PT  # 5.239

    @property
    def bullet_w_mm(self) -> float:
        return self.body_w_mm - self.bullet_gap_mm - self.bullet_dot_mm  # 248

    @property
    def caption_line_mm(self) -> float:
        return self.caption_pt * self.caption_line_budget * MM_PER_PT  # 4.233

    @property
    def fallback_line_mm(self) -> float:
        return self.fallback_pt * self.fallback_line_height * MM_PER_PT  # 3.210

    @property
    def fallback_tall_line_mm(self) -> float:
        return self.fallback_pt * self.fallback_tall_line_budget * MM_PER_PT  # 4.198

    @property
    def fallback_ideo_line_mm(self) -> float:
        return self.fallback_pt * self.fallback_ideo_line_budget * MM_PER_PT  # 6.075

    @property
    def fallback_chrome_mm(self) -> float:
        """Padding verticale e bordi del `<pre>` di fallback (4,28 mm)."""
        return 2 * self.fallback_padding_v_mm + 2 * self.fallback_border_pt * MM_PER_PT

    @property
    def min_block_h_mm(self) -> float:
        """Pavimento del blocco: immagine al minimo più una riga di didascalia."""
        return self.figure_min_h_mm + self.caption_margin_top_mm + self.caption_line_mm


DEFAULT_GEOMETRY = SlideGeometry()


def _char_em(ch: str, *, bold: bool) -> float:
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return _EM_WIDE
    symbol = _EM_SYMBOLS.get(ch)
    if symbol is not None:
        return symbol[1] if bold else symbol[0]
    if ch.isupper():
        return _EM_UPPER_BOLD if bold else _EM_UPPER
    if ch.islower():
        return _EM_LOWER_BOLD if bold else _EM_LOWER
    if ch.isdigit():
        return _EM_DIGIT
    return _EM_OTHER


def _language_tag(language: str | None) -> str:
    return (language or "").strip().lower().replace("_", "-")


def _mono_profile(language: str | None) -> str:
    """Profilo del `<pre>` per la lingua del corso (`<html lang>`): neutra o
    altra (vedi `_MONO_TALL_LANGS`). Senza lingua: neutra."""
    tag = _language_tag(language)
    primary = tag.split("-", 1)[0]
    if not primary:
        return _MONO_NEUTRAL
    if len(primary) != 2 or primary in _MONO_TALL_LANGS or tag.startswith(_MONO_TALL_TAGS):
        return _MONO_OTHER
    return _MONO_NEUTRAL


@dataclass(frozen=True, eq=False)
class _MonoLang:
    """Altezza delle righe del `<pre>` di fallback per una lingua del corso:
    profilo, caratteri dell'insieme base che con questa lingua escono dalle
    righe da 1,3 em (`not_base`) e riga alta propria (`tall_line_em`)."""

    profile: str = _MONO_NEUTRAL
    not_base: re.Pattern[str] | None = None
    # Riga resa fuori dall'insieme base, se più alta del budget comune.
    tall_line_em: float = 0.0

    def is_base(self, text: str) -> bool:
        """`text` sta su righe da 1,3 em: profilo neutro, insieme base,
        nessun carattere escluso."""
        return (
            self.profile == _MONO_NEUTRAL
            and _MONO_BASE_RE.fullmatch(text) is not None
            and (self.not_base is None or self.not_base.search(text) is None)
        )


_MONO_LANG_NEUTRAL = _MonoLang()
_MONO_LANG_VI = _MonoLang(not_base=_MONO_VI_NOT_BASE_RE)


@functools.lru_cache(maxsize=256)
def _mono_lang(language: str | None) -> _MonoLang:
    """Righe del `<pre>` per la lingua del corso: neutra (vi con
    l'esclusione di `_MONO_VI_NOT_BASE_RE`) o altra, con la riga alta
    propria di mn-cn e tcy (`_MONO_TALL_LINE_EM`)."""
    tag = _language_tag(language)
    if _mono_profile(language) == _MONO_NEUTRAL:
        return _MONO_LANG_VI if tag.split("-", 1)[0] == "vi" else _MONO_LANG_NEUTRAL
    tall = next((em for key, em in _MONO_TALL_LINE_EM.items() if tag.startswith(key)), 0.0)
    return _MonoLang(profile=_MONO_OTHER, tall_line_em=tall)


def _split_source_lines(text: str) -> list[str]:
    """Righe sorgente del fallback: a capo su CRLF, CR, LF, U+2028, U+2029
    (`_SOURCE_LINE_BREAK_RE`). Ogni riga, anche vuota, è una riga resa. I
    NUL non arrivano al DOM (il parser HTML di WeasyPrint e di Chromium li
    scarta, e in testa al `<pre>` scarta anche l'a capo che li segue): si
    tolgono prima di contare."""
    return _SOURCE_LINE_BREAK_RE.split(text.replace("\x00", ""))


def estimate_lines(text: str, *, font_pt: float, width_mm: float, bold: bool = False) -> int:
    """Limite superiore delle righe rese da `text` a `font_pt` in una riga
    larga `width_mm`: word-wrap greedy sulle parole (spazi bianchi
    collassati come fa l'HTML), larghezza per classe di carattere; una
    parola più larga della riga costa `ceil(w / budget)` righe (i CJK senza
    spazi sono una parola unica spezzata così). Testo vuoto → 0. Vale per
    titoli, prosa, bullet e didascalie; il `<pre>` di fallback non va a
    capo e non passa di qui (`fallback_source_rows`)."""
    budget = width_mm / (font_pt * MM_PER_PT)  # em per riga
    words = text.split()
    if not words:
        return 0
    if budget <= 0:
        return len(words)
    lines = 1
    used = 0.0
    for word in words:
        w = sum(_char_em(ch, bold=bold) for ch in word)
        if used > 0:
            if used + _EM_SPACE + w <= budget:
                used += _EM_SPACE + w
                continue
            lines += 1
            used = 0.0
        if w <= budget:
            used = w
        else:
            spans = math.ceil(w / budget)
            lines += spans - 1
            used = w - (spans - 1) * budget
    return lines


@dataclass(frozen=True)
class PageFigureBudget:
    """Budget verticale di OGNI blocco asset di una pagina resa (immagine +
    didascalia + margine della didascalia), in mm a tre decimali.
    `available_mm` è il valore prima del pavimento (negativo per una
    pagina impossibile); `clamped` dice che il pavimento è intervenuto."""

    w_mm: float
    block_h_mm: float
    available_mm: float
    n_blocks: int
    clamped: bool


def page_figure_budget(
    *,
    title: str,
    body: str,
    bullets: Sequence[str],
    n_blocks: int,
    geometry: SlideGeometry = DEFAULT_GEOMETRY,
) -> PageFigureBudget:
    """Budget per blocco della pagina resa: 120 mm meno tag, titolo (almeno
    una riga: l'`<h1>` vuoto occupa comunque la sua riga), prosa (solo se
    non vuota), bullet (TUTTI, anche vuoti: il template rende ogni `<li>`
    con il punto), margini degli asset e safety, diviso in parti uguali fra
    i blocchi. Sotto `min_block_h_mm` il budget è portato al pavimento e
    `clamped=True` (la pagina sborda per il testo, non per la figura)."""
    g = geometry
    title_lines = max(
        1, estimate_lines(str(title or ""), font_pt=g.title_pt, width_mm=g.body_w_mm, bold=True)
    )
    used = g.tag_h_mm + g.title_margin_top_mm + g.title_line_mm * title_lines
    body_text = str(body or "")
    if body_text.strip():
        used += g.body_text_margin_top_mm + g.text_line_mm * estimate_lines(
            body_text, font_pt=g.body_text_pt, width_mm=g.text_w_mm
        )
    items = [str(b) for b in bullets]
    if items:
        used += g.bullets_margin_top_mm
        for item in items:
            rows = max(1, estimate_lines(item, font_pt=g.bullet_pt, width_mm=g.bullet_w_mm))
            used += g.bullet_line_mm * rows
        used += g.bullet_margin_bottom_mm * (len(items) - 1)
    n = max(1, int(n_blocks))
    used += g.assets_margin_top_mm + g.asset_margin_mm * (n - 1) + g.asset_margin_mm + g.safety_mm
    available = round((g.body_h_mm - used) / n, 3)
    clamped = available < g.min_block_h_mm
    block = g.min_block_h_mm if clamped else available
    return PageFigureBudget(
        w_mm=g.body_w_mm,
        block_h_mm=round(block, 3),
        available_mm=available,
        n_blocks=n,
        clamped=clamped,
    )


def _floor10(value: float) -> float:
    return math.floor(value * 10 + 1e-9) / 10


def image_box(
    budget: PageFigureBudget,
    *,
    caption_text: str,
    geometry: SlideGeometry = DEFAULT_GEOMETRY,
) -> tuple[FigureBox, bool]:
    """Box dell'immagine dal budget del blocco meno la didascalia stimata
    sul testo reale (etichetta, didascalia dell'autore, coda calcolata;
    almeno una riga: l'etichetta «Figura.» c'è sempre), arrotondato per
    difetto al decimo. Sotto `figure_min_h_mm` il box è portato al pavimento
    e il secondo valore (`squeezed`) è `True`."""
    g = geometry
    rows = max(1, estimate_lines(caption_text, font_pt=g.caption_pt, width_mm=budget.w_mm))
    h = _floor10(round(budget.block_h_mm - g.caption_margin_top_mm - g.caption_line_mm * rows, 3))
    squeezed = h < g.figure_min_h_mm
    if squeezed:
        h = g.figure_min_h_mm
    return FigureBox(budget.w_mm, h), squeezed


def fallback_lines_that_fit(box: FigureBox, *, geometry: SlideGeometry = DEFAULT_GEOMETRY) -> int:
    """Righe BASE (1,3 em) del `<pre>` di fallback che entrano nel box
    (almeno una); le righe fuori dall'insieme base valgono
    `fallback_tall_line_budget` o `fallback_ideo_line_budget` (vedi
    `fallback_source_rows`)."""
    g = geometry
    return max(1, math.floor((box.h_mm - g.fallback_chrome_mm) / g.fallback_line_mm + 1e-9))


def _ideographic_lead(line: str) -> bool:
    """La prima run della riga può avere baseline ideografica: il primo
    carattere che decide è in `_IDEO_SCRIPT_RE` (sì) o è una lettera con
    script reale non ideografico (no); spazi, cifre, punteggiatura,
    simboli, segni combinanti, PUA e le lettere di `_NOT_REAL_LETTER_RE`
    non decidono, come in Pango (`pango_script_iter`). Una riga senza
    caratteri che decidono ha script Common: baseline romana."""
    for ch in line:
        if _IDEO_SCRIPT_RE.match(ch):
            return True
        if unicodedata.category(ch).startswith("L") and not _NOT_REAL_LETTER_RE.match(ch):
            return False
    return False


def _fallback_line_em(line: str, *, language: str | None, geometry: SlideGeometry) -> float:
    """Altezza in em del corpo del fallback della riga resa da UNA riga
    sorgente (senza a capo, `white-space: pre`): 1,3 se la riga è tutta
    nell'insieme base e la lingua è neutra, `fallback_tall_line_budget`
    altrimenti (o la riga alta della lingua, se maggiore: mn-cn e tcy;
    1,83 per le righe con mongolo tradizionale), `fallback_ideo_line_budget`
    se la prima run ha baseline ideografica (`_ideographic_lead`). Una riga
    base non ha caratteri ideografici: resta a 1,3 em."""
    g = geometry
    lang = _mono_lang(language)
    if lang.is_base(line):
        return g.fallback_line_height
    tall = max(g.fallback_tall_line_budget, lang.tall_line_em)
    if _MONGOLIAN_RE.search(line) is not None:
        tall = max(tall, _EM_TALL_MONGOLIAN)
    if _ideographic_lead(line):
        tall = max(tall, g.fallback_ideo_line_budget)
    return tall


def fallback_source_rows(
    source: str, *, language: str | None = None, geometry: SlideGeometry = DEFAULT_GEOMETRY
) -> tuple[int, float]:
    """Righe rese del sorgente di fallback e loro altezza in mm (contenuto
    del `<pre>`, senza padding e bordi) per la lingua del corso
    `language`. Le righe sono esatte: una per riga sorgente, anche vuota
    (`white-space: pre`, a capo solo sui separatori di
    `_SOURCE_LINE_BREAK_RE`); l'altezza è un limite superiore."""
    g = geometry
    lines = _split_source_lines(source)
    ems = sum(_fallback_line_em(line, language=language, geometry=g) for line in lines)
    return len(lines), ems * g.fallback_pt * MM_PER_PT


def truncate_fallback_source(
    source: str,
    *,
    box: FigureBox,
    language: str | None = None,
    geometry: SlideGeometry = DEFAULT_GEOMETRY,
) -> tuple[str, int]:
    """Sorgente del fallback tagliato all'altezza che entra nel box:
    `(testo, righe sorgente omesse)`. Il costo di una riga sorgente è
    l'altezza della sua riga resa (`white-space: pre`, nessun a capo:
    1,3 em solo per l'insieme base con lingua neutra,
    `fallback_tall_line_budget` altrimenti, `fallback_ideo_line_budget` con
    prima run a baseline ideografica; le righe vuote costano una riga; il
    costo non dipende dalle righe vicine, perché Pango itemizza ogni riga
    da sola). Il testo ha le righe sorgente unite da `\n`: CRLF, CR, U+2028 e
    U+2029 diventano `\n`, così WeasyPrint e Chromium rendono le stesse
    righe (Chromium non va a capo su U+2028 e U+2029). Se tutto entra le
    righe sono tutte; altrimenti restano le righe iniziali con costo
    cumulato che lascia posto a una riga finale con il marcatore «…»
    (anche il marcatore paga la riga alta con una lingua non neutra)."""
    g = geometry
    room = (box.h_mm - g.fallback_chrome_mm) / (g.fallback_pt * MM_PER_PT)
    lines = _split_source_lines(source)
    costs = [_fallback_line_em(line, language=language, geometry=g) for line in lines]
    if sum(costs) <= room + _EPS:
        return "\n".join(lines), 0
    room -= _fallback_line_em(TRUNCATION_MARK, language=language, geometry=g)
    kept: list[str] = []
    total = 0.0
    for line, cost in zip(lines, costs, strict=True):
        if total + cost > room + _EPS:
            break
        kept.append(line)
        total += cost
    return "\n".join([*kept, TRUNCATION_MARK]), len(lines) - len(kept)


__all__ = [
    "DEFAULT_GEOMETRY",
    "MM_PER_PT",
    "TRUNCATION_MARK",
    "PageFigureBudget",
    "SlideGeometry",
    "estimate_lines",
    "fallback_lines_that_fit",
    "fallback_source_rows",
    "image_box",
    "page_figure_budget",
    "truncate_fallback_source",
]
