"""Service di export PDF delle lezioni (§7).

Pipeline:
  content_raw (JSONB) + pdf_template (org)
        ↓ registro dei renderer (`figure_render_service.render_svg_map`):
          Mermaid via Playwright, Vega-Lite, DOT, function → SVG
        ↓ MathJax via Playwright → SVG inline (formule: una sola grammatica
          markdown-it per renderer e collector, `$..$`/`$$..$$` e
          `\\(..\\)`/`\\[..\\]` come token; MathML solo come fallback,
          loggato: WeasyPrint lo stampa piatto)
        ↓ markdown-it-py + Jinja2 → HTML completo (i campi inline —
          didascalie, titoli degli esempi, label, punti chiave, citazioni —
          passano da `render_markdown_inline`: solo testo escapato e math)
        ↓ WeasyPrint → PDF bytes
        ↓ filesystem (`generated_pdfs/{org}/{course}/{lesson}.pdf`)

Lo stato del PDF è scoped a livello LEZIONE
(`course_lesson.pdf_status` ∈ empty/pending/processing/ready/failed),
indipendente dallo stato di contenuto. Il rendering vero
avviene nel worker, asincrono: questo modulo espone:
  - request_lesson_pdf / request_all_lessons_pdf — accodano (status →
    `pending`)
  - cancel_all_pdf_exports — annulla in flight
  - materialize_lesson_pdf — vero rendering, chiamato dal worker
  - render_lesson_html — pure-function (markdown → HTML), riusabile in test
  - generate_pdf_bytes — pure-function (HTML → PDF bytes via WeasyPrint)

Asset visivi (tutti resi dal partial unico `partials/figure.html.j2` con
l'etichetta «Figura N.» in ordine di citazione, D4; gli asset mai citati
sono accodati dopo la sintesi, A12):
  - `format=mermaid` → SVG pre-renderizzato server-side (una sessione
    Playwright headless per lezione carica mermaid.esm e produce SVG)
    inserito inline (`<div class="mermaid-svg">`, catena byte-identica)
  - `format=vegalite|dot|function` → SVG del registro (vl-convert,
    Graphviz `dot`, matplotlib), normalizzato e incapsulato in
    `<img class="figure-svg" src="data:image/svg+xml;base64,…">`; per
    `function` la didascalia calcolata (D9) segue quella dell'autore
  - SVG assente (render fallito) → `<pre class="figure-fallback">` con il
    sorgente e `log.error("figure_render_fallback", …)` (A23)
  - `format=image` → immagine caricata (data URL base64)
  - `format=image_prompt|image_search_query|description` → placeholder
    testuale (formati legacy, solo in lettura)
  - `tables[].markdown` → pre-renderizzato a HTML
  - `equations[].latex` → SVG pre-renderizzato via MathJax (Playwright,
    pin `settings.mathjax_cdn_version`); `latex2mathml` resta il fallback
    senza CDN (MathML piatto, loggato come `math_render_fallback`)
  - `examples[].content` → markdown ricorsivo; l'HTML iniettato nel corpo
    non ha righe vuote (`_neutralize_blank_lines`): resta un solo HTML
    block e i suoi `<pre>` non tornano a essere markdown (L11)

Il template `pdf_templates` (org-scope) determina colori, font, page size,
margini, header/footer height (mm), loghi e background. Il pattern CSS
del PDF (sfondo edge-to-edge, header running, page counter) è basato
su CSS Paged Media puro — niente JavaScript, niente Chromium per il
rendering finale.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import sys
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NamedTuple

from jinja2 import Environment, FileSystemLoader, select_autoescape
from latex2mathml.converter import convert as _latex_to_mathml
from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token
from markupsafe import Markup, escape
from mdit_py_plugins.dollarmath import dollarmath_plugin
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from weasyprint import HTML

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.organization import Organization
from app.models.pdf_template import PdfTemplate
from app.models.user import User

# Re-export dei nomi storici del pre-render Mermaid (vedi la sezione
# «Mermaid pre-rendering» più avanti): slides_pdf, video e test li importano
# da qui. `_MERMAID_RENDERER_HTML` è pigra (dipende dal setting del pin) ed è
# servita dal `__getattr__` di modulo in coda al file.
from app.services import figure_render_service, remote_storage
from app.services import mermaid_prerender as _mermaid_prerender
from app.services.asset_ref_normalize import cite_asset_refs, normalize_asset_refs
from app.services.figure_markup import FigureVariant, caption_text, render_figure_html
from app.services.figure_numbering import (
    ASSET_KINDS,
    append_uncited_asset_refs,
    compute_asset_numbers,
    equation_label_family,
    proof_steps,
)
from app.services.figure_render_service import RENDERABLE_FORMATS, RenderedFigure, VisualSvgMap
from app.services.figure_scale import (
    READABILITY_BANDS_PT,
    FigureFitEntry,
    fit_figure_width_mm,
    format_mm,
    resolve_base_font_px,
)
from app.services.figure_theme import asset_label, asset_ref, figure_labels
from app.services.mermaid_prerender import (  # noqa: F401
    _MERMAID_JUNK_LINE_RE,
    _MERMAID_MAX_WIDTH_RE,
    _prerender_mermaid_to_svg_batch,
    _prerender_mermaid_to_svg_batch_async,
    _prerender_mermaid_to_svg_batch_sync,
    _sanitize_mermaid_code,
    _strip_mermaid_max_width,
)
from app.services.svg_normalize import svg_intrinsic_box, svg_to_data_uri

log = get_logger("app.course_lesson_pdf.service")


def __getattr__(name: str) -> Any:
    """Pagine headless costruite alla prima lettura (PEP 562): il pin viene
    da `get_settings()` e il modulo resta importabile senza ambiente."""
    if name == "_MERMAID_RENDERER_HTML":
        return _mermaid_prerender._MERMAID_RENDERER_HTML
    if name == "_MATHJAX_RENDERER_HTML":
        return build_mathjax_renderer_html()
    raise AttributeError(name)


# ---------------------------------------------------------------------------
# Costanti e scelte di stato (§7)
# ---------------------------------------------------------------------------

# Da quali content_status è ammesso esportare. Solo "ready" e "approved":
# `empty/pending/processing/failed` non hanno contenuto stabile.
EXPORTABLE_CONTENT_STATUSES: tuple[str, ...] = ("ready", "approved")

# Da quali pdf_status è ammesso (ri-)avviare un export.
VALID_PDF_REQUEST_STATUSES: tuple[str, ...] = ("empty", "ready", "failed")


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _settings_pdf_root() -> Path:
    """Risolve la directory radice dove vengono salvati i PDF."""
    settings = get_settings()
    raw = settings.generated_pdfs_dir
    p = Path(raw)
    if not p.is_absolute():
        # Path relativo alla CWD del backend.
        p = (Path.cwd() / p).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def pdf_relative_path(
    *,
    organization_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
) -> str:
    """Path relativo alla root, persistito su DB. Stabile per (org,corso,lezione)."""
    return f"{organization_id}/{course_id}/{lesson_id}.pdf"


def pdf_absolute_path(rel: str) -> Path:
    """Risolve il path assoluto sotto la root configurata."""
    return _settings_pdf_root() / rel


def pdf_filename_for_download(course_title: str, lesson: CourseLesson) -> str:
    """Nome file user-friendly per il download."""
    safe_lesson = re.sub(r"[^\w\-. ]+", "_", lesson.title)[:80].strip("_ ")
    safe_course = re.sub(r"[^\w\-. ]+", "_", course_title)[:60].strip("_ ")
    return f"{safe_course} — {lesson.lesson_code} {safe_lesson}.pdf"


# ---------------------------------------------------------------------------
# Eager loaders
# ---------------------------------------------------------------------------


def _eager_full_options() -> list:
    return [
        selectinload(Course.modules).selectinload(CourseModule.lessons),
    ]


async def load_course_full(db: AsyncSession, *, course_id: uuid.UUID) -> Course | None:
    res = await db.execute(
        select(Course).where(Course.id == course_id).options(*_eager_full_options())
    )
    return res.scalar_one_or_none()


async def get_lesson_or_404(
    db: AsyncSession, *, course: Course, lesson_id: uuid.UUID
) -> CourseLesson:
    for module in course.modules:
        for lesson in module.lessons:
            if lesson.id == lesson_id:
                return lesson
    raise NotFoundError(f"Lezione {lesson_id} non trovata in {course.id}")


async def _get_default_pdf_template(
    db: AsyncSession, *, organization_id: uuid.UUID
) -> PdfTemplate | None:
    """Restituisce il template `is_default=True` dell'org, o il primo se
    non c'è un default, o `None` se l'org non ne ha alcuno."""
    res = await db.execute(
        select(PdfTemplate)
        .where(PdfTemplate.organization_id == organization_id)
        .order_by(PdfTemplate.is_default.desc(), PdfTemplate.created_at.asc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _get_pdf_template_or_404(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    template_id: uuid.UUID,
) -> PdfTemplate:
    """Restituisce il template scelto dall'utente, validando che
    appartenga all'org. Solleva NotFoundError se non esiste."""
    res = await db.execute(
        select(PdfTemplate).where(
            PdfTemplate.id == template_id,
            PdfTemplate.organization_id == organization_id,
        )
    )
    tpl = res.scalar_one_or_none()
    if tpl is None:
        raise NotFoundError(
            f"PDF template {template_id} non trovato.",
            code="pdf_template_not_found",
        )
    return tpl


async def _resolve_pdf_template_for_lesson(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    lesson: CourseLesson,
) -> PdfTemplate | None:
    """Risolve il template da usare per il rendering: se la lezione ha
    `pdf_template_id` settato (perché l'utente l'ha scelto al momento
    della richiesta di export), usa quello; altrimenti fall-back al
    default dell'org. Se il template scelto è stato eliminato nel
    frattempo, fall-back al default per evitare di bloccare l'export.
    """
    if lesson.pdf_template_id is not None:
        res = await db.execute(
            select(PdfTemplate).where(
                PdfTemplate.id == lesson.pdf_template_id,
                PdfTemplate.organization_id == organization_id,
            )
        )
        tpl = res.scalar_one_or_none()
        if tpl is not None:
            return tpl
        log.warning(
            "lesson_pdf_requested_template_missing",
            lesson_id=str(lesson.id),
            requested_template_id=str(lesson.pdf_template_id),
        )
    return await _get_default_pdf_template(db, organization_id=organization_id)


async def _get_organization(db: AsyncSession, organization_id: uuid.UUID) -> Organization | None:
    return await db.get(Organization, organization_id)


# ---------------------------------------------------------------------------
# Markdown → HTML pipeline
# ---------------------------------------------------------------------------


def _convert_math_to_mathml(latex: str, *, display: str) -> str:
    """Converte LaTeX in MathML via `latex2mathml`: è SOLO il fallback
    offline. WeasyPrint NON rende il MathML: lo stampa come testo piatto
    (`x^{2}` → «x2», `\\frac{a}{b}` → «ab»), quindi ogni uso di questa
    funzione nel PDF è una perdita tipografica, loggata a monte da
    `_render_math_by_key` (`math_render_fallback`).

    `display` ∈ {"inline","block"}. In caso di parse error ritorna un
    fallback `<code>` col LaTeX grezzo, così la lezione resta leggibile
    anche con sintassi malformata."""
    src = (latex or "").strip()
    if not src:
        return ""
    try:
        # latex2mathml.convert(...) produce `<math … display="inline">…</math>`.
        mathml = _latex_to_mathml(src)
    except Exception as exc:  # convertitore di terze parti
        log.warning("math_convert_failed", latex=src[:120], error=str(exc))
        return f'<code class="math-error">{_html_escape_text(src)}</code>'
    if display == "block":
        # L'attributo c'è già: va sostituito, non aggiunto, altrimenti il
        # tag porta due `display`.
        if 'display="inline"' in mathml:
            mathml = mathml.replace('display="inline"', 'display="block"', 1)
        elif 'display="block"' not in mathml:
            mathml = mathml.replace("<math ", '<math display="block" ', 1)
    return mathml


def _normalize_math_source(latex: str) -> str:
    """Normalizza una sorgente LaTeX per il rendering: rimuove eventuali
    delimitatori (`$$`, `$`, `\\[`, `\\]`) e RIBILANCIA gli ambienti
    malformati emessi a volte dall'AI: `\\end{env}` senza `\\begin{env}`
    (e viceversa), oppure allineamento (`&` / `\\\\`) fuori da un ambiente.
    Così una formula `aligned` col `\\begin` mancante torna renderizzabile.

    DEVE essere applicata in modo identico alla raccolta
    (`_collect_math_from_content`, che genera la chiave della mappa SVG) e
    al lookup (`_render_math`), altrimenti le chiavi non combaciano."""
    s = (latex or "").strip()
    if not s:
        return ""
    s = re.sub(r"^\\\[", "", s)
    s = re.sub(r"\\\]$", "", s)
    s = re.sub(r"^\$+", "", s)
    s = re.sub(r"(?<!\\)\$+$", "", s)
    s = s.strip()
    begin_m = re.search(r"\\begin\{([a-zA-Z*]+)\}", s)
    end_m = re.search(r"\\end\{([a-zA-Z*]+)\}", s)
    if end_m and not begin_m:
        s = f"\\begin{{{end_m.group(1)}}} {s}"
    elif begin_m and not end_m:
        s = f"{s} \\end{{{begin_m.group(1)}}}"
    elif not begin_m and not end_m and (re.search(r"\\\\", s) or re.search(r"(?<!\\)&", s)):
        s = f"\\begin{{aligned}} {s} \\end{{aligned}}"
    return s.strip()


# ---------------------------------------------------------------------------
# Grammatica del math (B3): un plugin, quattro token, una rule di render
# ---------------------------------------------------------------------------

# Opzioni di dollarmath, pinnate dai test. `allow_space=False` esclude
# `$ x $` e gli importi `$50 e sale a $70` (nessun token, L4);
# `allow_digits=True` conserva `la base 2$^{10}$` e `2$\pi$`.
_DOLLARMATH_OPTIONS: Final[dict[str, bool]] = {
    "allow_labels": False,
    "double_inline": True,
    "allow_space": False,
    "allow_digits": True,
}

# Token dollarmath → `display` della chiave della mappa SVG: UNICA fonte
# per la rule di render (e, da WP2, per il collector). `math_inline_double`
# (`$$..$$` in frase, in cella, o su righe proprie senza riga vuota) ha
# chiave block: è display style in LaTeX, coincide con la chiave del
# `math_block` (un solo SVG per formula) e con `_DISPLAY_RE` del collector
# regex. `math_block_label` è irraggiungibile con `allow_labels=False`:
# registrato perché nessun token resti alla rule di default del plugin.
_MATH_TOKEN_DISPLAY: Final[dict[str, str]] = {
    "math_inline": "inline",
    "math_inline_double": "block",
    "math_block": "block",
    "math_block_label": "block",
}

# Contenuto di un `$..$` che è un importo, non una formula: «importo +
# separatore» (`$50/$70` → `50/`, `$5-$10` → `5-`) o «separatore +
# importo» (`5$, 10$` → `, 10`, `5$/10$` → `/10`). Trattini en/em come
# escape `\u2013`/`\u2014`, letti da `re` (stringa raw).
_CURRENCY_CONTENT_RE = re.compile(r"^(?:\d[\d.,]*\s*[-\u2013\u2014/,;:]|[/,;:]\s*\d[\d.,]*)$")
# Contenuto che inizia con una cifra, al più preceduta da segno o
# separatore (`-70`, `, 10`, `/10`): con una cifra subito PRIMA del `$` di
# apertura è la coda di un importo (`50$-70$`).
_NUMERIC_START_RE = re.compile(r"^[\s+\-\u2013\u2014/,;:]*\d")


def _is_currency_math(children: Sequence[Token], i: int) -> bool:
    """Un `$..$` singolo è prosa (importo) se: il contenuto è «importo +
    separatore» o «separatore + importo» (`$50/$70`, `$5-$10`, `5$, 10$`);
    oppure subito DOPO il `$` di chiusura c'è una cifra e il contenuto
    inizia con cifra (`$2$3`, `US$50 e US$70`); oppure subito PRIMA del `$`
    di apertura c'è una cifra e il contenuto inizia con cifra/segno
    (`50$-70$`, `5$-10$`). `$5$`, `$-1$`, `$0{,}866$`, `$x$2`, `2$^{10}$`
    restano math. Solo `math_inline` con markup `$` (mai `$$`)."""
    tok = children[i]
    if tok.type != "math_inline" or tok.markup != "$":
        return False
    content = tok.content
    if _CURRENCY_CONTENT_RE.match(content.strip()):
        return True
    prev = children[i - 1] if i else None
    nxt = children[i + 1] if i + 1 < len(children) else None
    next_c = nxt.content[:1] if nxt is not None and nxt.type == "text" else ""
    prev_c = prev.content[-1:] if prev is not None and prev.type == "text" else ""
    if next_c.isdigit() and content[:1].isdigit():
        return True
    return prev_c.isdigit() and bool(_NUMERIC_START_RE.match(content))


def _math_currency_guard(state: StateCore) -> None:
    """Core rule prima di `text_join` (attiva in `parse` E `parseInline`):
    declassa i token currency a `text` col `$..$` originale; `text_join`
    rifonde i frammenti. Collector e renderer non possono divergere."""
    for tok in state.tokens:
        if tok.type != "inline" or not tok.children:
            continue
        for i, child in enumerate(tok.children):
            if _is_currency_math(tok.children, i):
                child.type, child.tag, child.markup = "text", "", ""
                child.content = f"${child.content}$"


def _token_math_key(tok: Token) -> tuple[str, str] | None:
    """`(sorgente normalizzata, display)` del token, o None se non è math
    (o è vuoto). Usata dalla rule di render e, da WP2, dal collector: una
    sola funzione token → chiave."""
    display = _MATH_TOKEN_DISPLAY.get(tok.type)
    if display is None:
        return None
    src = _normalize_math_source(tok.content)
    return (src, display) if src else None


def _math_markup(tok: Token) -> tuple[str, str]:
    """`(tag, classe)` del contenitore. `math_inline_double` con i
    delimitatori su righe proprie (contenuto `\\n…\\n`, la condizione del
    flow math del frontend) è un blocco via span + CSS
    (`span.math-block { display: block; }`); in frase, cella o titolo resta
    in linea. Mai un `<div>` dentro `<p>`/`<td>`/`<h2>`."""
    if tok.type == "math_inline":
        return ("span", "math-inline")
    if tok.type == "math_inline_double":
        content = tok.content
        own_lines = content.startswith("\n") and content.endswith("\n")
        return ("span", "math-block" if own_lines else "math-inline")
    return ("div", "math-block")


class MathSvgMap(dict[tuple[str, str], str]):
    """`{(latex_normalizzato, display) → svg}` con `requested` (chiavi
    raccolte dal collector) e `misses` (lookup falliti, in ordine di
    rendering). È un `dict`: i chiamanti delle slide e del video passano
    la mappa come prima."""

    def __init__(self, *args: Any, requested: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.requested = requested
        self.misses: list[tuple[str, str]] = []


def _render_math_by_key(key: tuple[str, str], *, svg_map: dict | None) -> str:
    """Lookup dell'SVG per chiave; senza SVG ricade sul MathML e lo dice.
    WeasyPrint stampa il MathML piatto e in silenzio (`x^{2}` → «x2»,
    `\\frac{a}{b}` → «ab»): il fallback è una perdita visibile solo nel
    log `math_render_fallback`, dove `reason` distingue la mappa assente
    (`svg_map_missing`: chiamante senza pre-render), la CDN MathJax giù
    (`svg_map_empty`) e il drift fra collector e renderer (`svg_missing`).
    Su una `MathSvgMap` il miss è anche contato (`misses`), per il summary
    `lesson_pdf_math_fallbacks` di fine lezione."""
    if svg_map:
        svg = svg_map.get(key)
        if svg:
            return svg
    if svg_map is None:
        reason = "svg_map_missing"
    elif not svg_map:
        reason = "svg_map_empty"
    else:
        reason = "svg_missing"
    if isinstance(svg_map, MathSvgMap):
        svg_map.misses.append(key)
    log.warning("math_render_fallback", reason=reason, display=key[1], latex=key[0][:80])
    return _convert_math_to_mathml(key[0], display=key[1])


def _render_math(latex: str, *, display: str, svg_map: dict | None = None) -> str:
    """Rende una formula LaTeX per il PDF/slide (equazioni dedicate e
    passaggi di dimostrazione). La sorgente è normalizzata
    (`_normalize_math_source`) e la chiave della mappa è
    `(sorgente_normalizzata, display)`, come in raccolta; lookup e fallback
    (loggato) sono in `_render_math_by_key`."""
    src = _normalize_math_source(latex)
    if not src:
        return ""
    return _render_math_by_key((src, display), svg_map=svg_map)


def _log_math_fallbacks(*, lesson_code: str, svg_map: dict | None) -> None:
    """Un evento `lesson_pdf_math_fallbacks` per lezione, dopo il render:
    quante formule sono ricadute sul MathML (`count`), quante ne aveva
    raccolte il collector (`requested`), quante ne ha rese MathJax
    (`rendered`) e un campione delle chiavi mancanti. Tace se non ci sono
    miss (o se la mappa è un `dict` storico senza contatori)."""
    misses = getattr(svg_map, "misses", None)
    if not misses:
        return
    log.error(
        "lesson_pdf_math_fallbacks",
        lesson_code=lesson_code,
        count=len(misses),
        requested=getattr(svg_map, "requested", 0),
        rendered=len(svg_map or {}),
        sample=misses[:5],
    )


def _render_math_token(
    _self: Any, tokens: Sequence[Token], idx: int, _options: Any, env: Any
) -> str:
    """L'unica rule di render dei quattro token dollarmath. `add_render_rule`
    la installa come metodo del renderer: il 5° parametro È l'`env`, da cui
    legge la mappa SVG passata da `render_markdown`."""
    key = _token_math_key(tokens[idx])
    if key is None:
        return ""
    inner = _render_math_by_key(key, svg_map=(env or {}).get("math_svg"))
    tag, cls = _math_markup(tokens[idx])
    return f'<{tag} class="{cls}">{inner}</{tag}>'


# Contenuto di un `\[..\]` che NON è math: un tag asset (`\[FIG:x\]`) o una
# citazione bibliografica numerica (`\[1\]`, `\[2, 3\]`, `\[12–14\]`),
# lasciati alla rule `escape` che li rende «[FIG:x]», «[1]».
_ASSET_TAG_RE = re.compile(r"^\s*(FIG|TAB|EQ|EX):")
_CITATION_LIKE_RE = re.compile(r"^\s*\d+(?:\s*[-–,;]\s*\d+)*\s*$")


def _is_escaped_at(src: str, pos: int) -> bool:
    """Numero dispari di `\\` subito prima di `pos` (stessa semantica di
    `dollarmath.is_escaped`, riscritta in locale per non dipendere dall'API
    privata del plugin)."""
    count, i = 0, pos - 1
    while i >= 0 and src[i] == "\\":
        count += 1
        i -= 1
    return count % 2 == 1


def _math_bsdelim(state: StateInline, silent: bool) -> bool:
    """Rule inline per i delimitatori LaTeX puri: `\\(..\\)` → `math_inline`,
    `\\[..\\]` → `math_inline_double` (stessa chiave block di `$$..$$`).
    Registrata prima di `escape`; limitata a `state.posMax` (il testo di un
    link) e al contenuto inline di un paragrafo: fence, code span e HTML
    block non passano mai di qui, quindi `print("a\\[0\\]")` in un fence
    resta byte-identico (L11). `\\[FIG:x\\]` e `\\[1\\]` sono rifiutati e
    lasciati a `escape`."""
    src, pos, pmax = state.src, state.pos, state.posMax
    if src[pos] != "\\" or pos + 1 >= pmax or src[pos + 1] not in "([":
        return False
    if _is_escaped_at(src, pos):
        return False
    opener = src[pos + 1]
    closer = "\\)" if opener == "(" else "\\]"
    end = src.find(closer, pos + 2, pmax)
    while end != -1 and _is_escaped_at(src, end):
        end = src.find(closer, end + 1, pmax)
    if end == -1 or end + 2 > pmax:
        return False
    inner = src[pos + 2 : end]
    if not inner.strip():
        return False
    if opener == "[" and (_ASSET_TAG_RE.match(inner) or _CITATION_LIKE_RE.match(inner)):
        return False
    if not silent:
        tok = state.push("math_inline" if opener == "(" else "math_inline_double", "math", 0)
        tok.content = inner
        # La guardia currency guarda solo i token con markup `$`.
        tok.markup = "\\(" if opener == "(" else "\\["
    state.pos = end + 2
    return True


def _install_math_grammar(md: MarkdownIt) -> MarkdownIt:
    """L'unica grammatica del math: plugin dollarmath con le opzioni
    pinnate, rule inline `math_bsdelim` (`\\(..\\)`/`\\[..\\]`) prima di
    `escape`, core rule anti-currency prima di `text_join`, quattro rule
    di render legate a `_render_math_token`. Condivisa da renderer e
    collector (`_collect_math_from_content`): non possono divergere."""
    md.use(dollarmath_plugin, **_DOLLARMATH_OPTIONS)
    md.inline.ruler.before("escape", "math_bsdelim", _math_bsdelim)
    md.core.ruler.before("text_join", "math_currency_guard", _math_currency_guard)
    for token_type in _MATH_TOKEN_DISPLAY:
        md.add_render_rule(token_type, _render_math_token)
    return md


def _render_text_token(
    _self: Any, tokens: Sequence[Token], idx: int, _options: Any, _env: Any
) -> str:
    """Rule `text` dell'istanza inline: `markupsafe.escape`, così senza math
    l'output è byte-identico a quello del partial figura (`&#34;`, non
    `&quot;`)."""
    return str(escape(tokens[idx].content))


def _build_markdown_renderer() -> MarkdownIt:
    """Crea l'istanza markdown-it delle lezioni: GFM (tabelle), HTML
    inline/block e la grammatica del math (`_install_math_grammar`)."""
    return _install_math_grammar(
        MarkdownIt("commonmark", {"html": True, "linkify": True, "breaks": False}).enable(
            ["table", "strikethrough"]
        )
    )


def _build_inline_renderer() -> MarkdownIt:
    """Istanza dei campi inline (D9: didascalie, titoli degli esempi, label
    delle equazioni, punti chiave, citazioni): preset `zero` (solo la rule
    `text`) più la grammatica del math. Niente enfasi, link, code o
    escape: il frontend rende questi campi letterali e il PDF fa lo
    stesso; con `_render_text_token` l'output senza math è identico a
    `markupsafe.escape`, a meno della core rule `normalize` di markdown-it
    (CRLF/CR → LF, NUL → U+FFFD), che resta attiva anche nel preset
    `zero`."""
    md = _install_math_grammar(MarkdownIt("zero"))
    md.add_render_rule("text", _render_text_token)
    return md


_md_renderer = _build_markdown_renderer()
_md_inline_renderer = _build_inline_renderer()


def render_markdown(source: str, math_svg_map: dict | None = None) -> str:
    """Pipeline markdown → HTML dei campi a blocchi (corpo, tabelle,
    esempi, enunciati). Nessun pre-processing testuale: `\\(..\\)` e
    `\\[..\\]` sono una rule della grammatica (`_math_bsdelim`).

    `math_svg_map` (opzionale): mappa `{(latex, display) → svg}` pre-
    renderizzata da MathJax; passata ai rule math via l'`env` di
    markdown-it. Se omessa, le formule ricadono su MathML."""
    if not source:
        return ""
    return _md_renderer.render(source, {"math_svg": math_svg_map})


def render_markdown_inline(text: str, math_svg_map: dict | None = None) -> str:
    """Campi inline (D9): solo testo escapato e math (`$..$`, `$$..$$`,
    `\\(..\\)`, `\\[..\\]`), senza `<p>` avvolgente e senza markdown ricco.
    Senza math ritorna `str(markupsafe.escape(text))`, salvo la
    normalizzazione d'ingresso di markdown-it (core rule `normalize`:
    CRLF/CR → LF, NUL → U+FFFD)."""
    if not text:
        return ""
    return _md_inline_renderer.renderInline(text, {"math_svg": math_svg_map})


# ---------------------------------------------------------------------------
# Asset substitution
# ---------------------------------------------------------------------------


_ASSET_REF_RE = re.compile(r"\[(FIG|TAB|EQ|EX):([^\]\n]+)\]")


def _html_escape_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# Formati legacy di Fase 3/4 (solo in lettura): placeholder testuale.
_LEGACY_PLACEHOLDER_FORMATS: frozenset[str] = frozenset(
    {"image_prompt", "image_search_query", "description"}
)

# Padding orizzontale totale del wrapper Mermaid nella dispensa
# (`figure.visual:has(.mermaid-svg) .figure-body { padding: 1mm }` in
# `lesson_pdf.html.j2`): il box di 170 mm diventa 168 mm per l'SVG inline.
_MERMAID_BODY_PADDING_MM: Final = 2.0


def _figure_entry(svg_map: VisualSvgMap, asset_id: str) -> RenderedFigure | None:
    """Record della figura dalla mappa: una stringa vale
    `RenderedFigure.from_svg` (metriche lette dagli attributi); assente o
    vuota → `None` (fallback del partial)."""
    value = svg_map.get(asset_id) if asset_id else None
    if isinstance(value, RenderedFigure):
        return value if value.svg else None
    if isinstance(value, str) and value:
        return RenderedFigure.from_svg(value)
    return None


def _figure_width_style(
    fig: RenderedFigure,
    *,
    fmt: str,
    variant: FigureVariant,
    box: tuple[float, float | None] | None,
    body_padding_mm: float = 0.0,
    expect_measured: bool = False,
    asset_id: str,
    lesson_code: str | None,
    fit_report: list[FigureFitEntry] | None,
) -> str:
    """Attributo ` style="width:Wmm"` (ultimo del tag) dalla banda di
    leggibilità (D10, `figure_scale.fit_figure_width_mm`), oppure `""`
    senza box (chiamanti senza geometria) o senza viewBox. `box` è
    `(larghezza, altezza | None)` in mm del contenuto della figura;
    `body_padding_mm` è il padding del wrapper da sottrarre;
    `expect_measured` segnala nei log un fallback dalle metriche misurate
    (`figure_font_fallback`). Ogni fit è loggato (`figure_fit`); una banda
    irraggiungibile produce `figure_fit_out_of_band` e la voce del
    `fit_report` porta `in_band=False` (gate D13). L'SVG non è mai toccato."""
    if box is None:
        return ""
    sbox = svg_intrinsic_box(fig.svg)
    if sbox is None:
        log.warning(
            "figure_fit_skipped",
            lesson_code=lesson_code,
            asset_id=asset_id,
            format=fmt,
            reason="no_viewbox",
        )
        return ""
    box_w = box[0] - body_padding_mm
    box_h = box[1]
    base, source = resolve_base_font_px(fmt, fig.metrics)
    fit = fit_figure_width_mm(
        vb_w=sbox.vb_w,
        vb_h=sbox.vb_h,
        base_font_px=base,
        box_w_mm=box_w,
        box_h_mm=box_h,
        variant=variant,
        intrinsic_w_px=sbox.width_px,
    )
    if fit is None:
        log.warning(
            "figure_fit_skipped",
            lesson_code=lesson_code,
            asset_id=asset_id,
            format=fmt,
            reason="degenerate_box",
        )
        return ""
    band = READABILITY_BANDS_PT[variant]
    text_count = fig.metrics.text_count if fig.metrics is not None else 0
    fields: dict[str, Any] = {
        "lesson_code": lesson_code,
        "asset_id": asset_id,
        "format": fmt,
        "variant": variant,
        "width_mm": fit.width_mm,
        "scale": fit.scale,
        "text_pt": fit.text_pt,
        "band": band,
        "in_band": fit.in_band,
        "font_source": source,
        "text_count": text_count,
    }
    log.info("figure_fit", **fields)
    if expect_measured and source != "measured":
        log.warning("figure_font_fallback", **fields)
    if not fit.in_band:
        log.warning("figure_fit_out_of_band", **fields)
    if fit_report is not None:
        fit_report.append(
            FigureFitEntry(
                asset_id=asset_id,
                fmt=fmt,
                variant=variant,
                width_mm=fit.width_mm,
                scale=fit.scale,
                text_pt=fit.text_pt,
                band=band,
                in_band=fit.in_band,
                font_source=source,
                text_count=text_count,
            )
        )
    return f' style="width:{format_mm(fit.width_mm)}mm"'


def _render_visual_asset_block(
    asset: dict[str, Any],
    *,
    visual_svg_map: VisualSvgMap | None = None,
    math_svg_map: dict | None = None,
    number: int | None = None,
    labels: Mapping[str, str] | None = None,
    variant: FigureVariant = "lesson",
    language: str | None = None,
    lesson_code: str | None = None,
    figure_box_mm: tuple[float, float | None] | None = None,
    fit_report: list[FigureFitEntry] | None = None,
) -> str:
    """Blocco HTML di un asset visivo, per ogni formato, attraverso il
    partial unico `render_figure_html` (D4).

    `visual_svg_map` è `{asset_id → svg | RenderedFigure}` prodotto da
    `_prerender_visual_assets_for_lesson` (registro dei renderer; una
    stringa vale `RenderedFigure.from_svg`); `math_svg_map` serve alla
    didascalia, che riceve il math inline via `render_markdown_inline`
    (D9) dopo `strip_figure_prefix` e prima dell'etichetta «Figura N.»,
    che resta fuori dal renderer. Corpo per formato:
      - `mermaid`: SVG inline `<div class="mermaid-svg">` nella dispensa
        (byte-identico alla catena precedente, A11-L3), `<img
        class="mermaid-svg">` con data URI nelle slide (`variant="slide"`);
      - `vegalite` / `dot` / `function`: `<img class="figure-svg">` con
        data URI (replaced element: `max-height` rispettato da WeasyPrint);
        `function` riceve la didascalia calcolata come `extra_caption`;
      - `image`: immagine caricata (data URL) o placeholder «immagine
        mancante»; formati legacy: placeholder con il testo;
      - SVG assente per un formato renderizzabile, o formato sconosciuto:
        `<pre class="figure-fallback">` con il sorgente escapato, mai il
        contenuto in chiaro nel corpo; per i formati renderizzabili è un
        errore visibile nei log (`figure_render_fallback`, A23).

    `figure_box_mm` (D10) è il box `(larghezza, altezza | None)` in mm del
    contenuto della figura: con esso i tre corpi SVG ricevono come ULTIMO
    attributo `style="width:Wmm"` dalla banda di leggibilità
    (`_figure_width_style`); `None` = nessuna larghezza (chiamanti senza
    geometria). `fit_report` raccoglie una `FigureFitEntry` per figura.

    `number` è il numero editoriale («Figura N.») o `None` («Figura.», slide
    e frame video, A2); `labels` è la mappa di `figure_labels(language)`.
    """
    fmt = str(asset.get("format", "") or "")
    asset_id = str(asset.get("asset_id", "") or "")
    content = asset.get("content", "") or ""
    caption = asset.get("caption", "") or ""
    alt_text = asset.get("alt_text") or ""
    svg_map: VisualSvgMap = visual_svg_map or {}

    body: Markup | None = None
    fallback_source: str | None = None
    fallback_reason = ""
    extra_caption = ""

    if fmt == "mermaid":
        # WeasyPrint NON esegue JS — il rendering Mermaid avviene server-side
        # via Playwright (registro dei renderer). Qui inseriamo l'SVG già
        # renderizzato: inline nella dispensa, `<img>` nelle slide.
        fig = _figure_entry(svg_map, asset_id)
        if fig is not None:
            svg = fig.svg
            style = _figure_width_style(
                fig,
                fmt=fmt,
                variant=variant,
                box=figure_box_mm,
                body_padding_mm=_MERMAID_BODY_PADDING_MM if variant == "lesson" else 0.0,
                expect_measured=True,
                asset_id=asset_id,
                lesson_code=lesson_code,
                fit_report=fit_report,
            )
            if variant == "slide":
                body = Markup(
                    f'<img class="mermaid-svg" src="{svg_to_data_uri(svg)}" alt=""{style} />'
                )
            else:
                body = Markup(f'<div class="mermaid-svg"{style}>{svg}</div>')
        else:
            fallback_source, fallback_reason = content, "svg_missing"
    elif fmt in RENDERABLE_FORMATS:
        # vegalite | dot | function: SVG normalizzato del registro.
        fig = _figure_entry(svg_map, asset_id)
        if fig is not None:
            style = _figure_width_style(
                fig,
                fmt=fmt,
                variant=variant,
                box=figure_box_mm,
                asset_id=asset_id,
                lesson_code=lesson_code,
                fit_report=fit_report,
            )
            body = Markup(
                f'<img class="figure-svg" src="{svg_to_data_uri(fig.svg)}" '
                f'alt="{_html_escape_text(alt_text)}"{style} />'
            )
            if fmt == "function":
                extra_caption = figure_render_service.function_computed_caption(
                    content, language=language, asset_id=asset_id
                )
        else:
            fallback_source, fallback_reason = content, "svg_missing"
    elif fmt == "image":
        # Asset immagine caricato dall'utente (path relativo `lesson_assets/...`).
        # Riusiamo il resolver dei template asset: legge dallo storage e
        # produce una data URL base64 — WeasyPrint-friendly senza dipendenze
        # di rete.
        alt = _html_escape_text(alt_text)
        data_url = _resolve_template_asset_url(content)
        if data_url:
            body = Markup(f'<img class="uploaded-image" src="{data_url}" alt="{alt}" />')
        else:
            body = Markup(
                f'<div class="placeholder-image">[immagine mancante: '
                f"{_html_escape_text(content)}]</div>"
            )
    elif fmt in _LEGACY_PLACEHOLDER_FORMATS:
        body = Markup(f'<div class="placeholder-image">{_html_escape_text(content)}</div>')
    else:
        # Formato sconosciuto: sorgente nel fallback, mai in chiaro nel corpo.
        fallback_source, fallback_reason = content, "format_unknown"

    if fallback_source is not None:
        if fmt in RENDERABLE_FORMATS:
            log.error(
                "figure_render_fallback",
                lesson_code=lesson_code,
                asset_id=asset_id,
                format=fmt,
                reason=fallback_reason,
            )
        else:
            log.warning(
                "figure_format_unknown",
                lesson_code=lesson_code,
                asset_id=asset_id,
                format=fmt,
            )

    return render_figure_html(
        body_html=body,
        caption=str(caption),
        alt_text=str(alt_text),
        asset_id=asset_id,
        fmt=fmt,
        number=number,
        labels=labels if labels is not None else figure_labels(language),
        variant=variant,
        fallback_source=str(fallback_source) if fallback_source is not None else None,
        extra_caption=extra_caption,
        caption_renderer=lambda text: Markup(render_markdown_inline(text, math_svg_map)),
    )


def _label_span(labels: Mapping[str, str], kind: str, number: int | None, **kw: str) -> str:
    """`<span class="figure-label">Tabella 3.</span>` (o «Tabella.» senza
    numero, A2): l'etichetta del blocco è sempre presente, anche senza
    didascalia, come per le figure (D3, D5)."""
    text = _html_escape_text(asset_label(labels, kind, number, **kw))
    return f'<span class="figure-label">{text}</span>'


def _render_table_block(
    table: dict[str, Any],
    *,
    math_svg_map: dict | None = None,
    number: int | None = None,
    labels: Mapping[str, str] | None = None,
    language: str = "it",
) -> str:
    """Blocco tabella con la didascalia «Tabella N.» (`number` None →
    «Tabella.», slide e frame video); `labels` è la mappa di
    `figure_labels(language)`."""
    labels = labels if labels is not None else figure_labels(language)
    md = (table.get("markdown") or "").strip()
    caption = table.get("caption") or ""
    table_html = render_markdown(md, math_svg_map) if md else ""
    # Didascalia con il math inline (D9), etichetta fuori dal renderer.
    caption_inner = f" {render_markdown_inline(caption, math_svg_map)}" if caption else ""
    caption_html = f"<figcaption>{_label_span(labels, 'TAB', number)}{caption_inner}</figcaption>"
    return (
        f'<figure class="table"><div class="figure-body">{table_html}</div>{caption_html}</figure>'
    )


def _theorem_kind_word(eq: Mapping[str, Any], pdf_labels: Mapping[str, str]) -> str:
    """Parola del kind del blocco teorema («Lemma», «Teorema» di fallback)
    dalla mappa `_labels_for(language)`: unico punto per l'intestazione
    del blocco e per il rimando in linea («Lemma 2»)."""
    kind = str(eq.get("kind") or "formula").strip().lower()
    return pdf_labels.get(f"kind_{kind}", pdf_labels.get("kind_theorem", "Teorema"))


def _render_equation_block(
    eq: dict[str, Any],
    *,
    math_svg_map: dict | None = None,
    language: str = "it",
    number: int | None = None,
    labels: Mapping[str, str] | None = None,
) -> str:
    """Blocco equazione: formula nuda con «Equazione N.» (famiglia `EQ`)
    oppure teorema con «Lemma N.» (famiglia `THM`, `equation_label_family`);
    `number` None → «Equazione.» / sola parola del kind (slide e frame
    video, byte-identico a prima per i teoremi). `labels` è la mappa di
    `figure_labels(language)`; le parole dei kind vengono da
    `_labels_for(language)` (`pdf_labels`)."""
    labels = labels if labels is not None else figure_labels(language)
    latex = (eq.get("latex") or "").strip()
    label = (eq.get("label") or "").strip()
    explanation = (eq.get("explanation") or "").strip()
    kind = (eq.get("kind") or "formula").strip().lower()
    statement = (eq.get("statement") or "").strip()

    formula_html = (
        f'<div class="math-block">'
        f"{_render_math(latex, display='block', svg_map=math_svg_map)}</div>"
        if latex
        else ""
    )

    steps = proof_steps(eq)
    has_proof = bool(steps)

    # Caso semplice (retro-compatibile): formula "nuda" senza enunciato né
    # dimostrazione → formula + didascalia «Equazione N.» sempre presente,
    # poi label/explanation dell'autore.
    if equation_label_family(eq) == "EQ":
        caption_inner = _label_span(labels, "EQ", number)
        if label:
            # Label dell'autore con il math inline (D9).
            caption_inner += (
                f' <span class="label">{render_markdown_inline(label, math_svg_map)}</span>'
            )
        if explanation:
            # Reso come markdown → math inline `$..$` tipografato (SVG).
            caption_inner += (
                f'<div class="explanation">{render_markdown(explanation, math_svg_map)}</div>'
            )
        return (
            f'<figure class="equation"><div class="figure-body">{formula_html}</div>'
            f"<figcaption>{caption_inner}</figcaption></figure>"
        )

    # Blocco teorema/proposizione/definizione: intestazione «Lemma N.» +
    # enunciato + formula + (eventuale) dimostrazione a passaggi.
    pdf_labels = _labels_for(language)
    kind_word = _theorem_kind_word(eq, pdf_labels)
    head = _html_escape_text(asset_label(labels, "THM", number, kind_word=kind_word))
    head += f" {render_markdown_inline(label, math_svg_map)}" if label else ""
    parts = [f'<div class="theorem-head">{head}</div>']
    if statement:
        parts.append(
            f'<div class="theorem-statement">{render_markdown(statement, math_svg_map)}</div>'
        )
    if formula_html:
        parts.append(formula_html)
    if has_proof:
        proof_head = _html_escape_text(pdf_labels.get("proof", "Dimostrazione"))
        proof_parts = [f'<div class="proof-head">{proof_head}.</div>']
        for step in steps:
            slatex = (step.get("latex") or "").strip()
            stext = (step.get("text") or "").strip()
            step_html = '<div class="proof-step">'
            if stext:
                step_html += (
                    f'<div class="proof-step-text">{render_markdown(stext, math_svg_map)}</div>'
                )
            if slatex:
                step_html += (
                    f'<div class="math-block">'
                    f"{_render_math(slatex, display='block', svg_map=math_svg_map)}</div>"
                )
            step_html += "</div>"
            proof_parts.append(step_html)
        proof_parts.append('<div class="proof-qed">&#8718;</div>')
        parts.append(f'<div class="proof">{"".join(proof_parts)}</div>')
    if explanation:
        parts.append(f"<figcaption>{render_markdown(explanation, math_svg_map)}</figcaption>")
    return (
        f'<figure class="equation theorem" data-kind="{_html_escape_text(kind)}">'
        f"{''.join(parts)}</figure>"
    )


def _render_example_block(
    example: dict[str, Any],
    *,
    math_svg_map: dict | None = None,
    number: int | None = None,
    labels: Mapping[str, str] | None = None,
    language: str = "it",
) -> str:
    """Blocco esempio con la barra del titolo «Esempio N.» sempre presente
    (`number` None → «Esempio.», slide e frame video); `labels` è la mappa
    di `figure_labels(language)`."""
    labels = labels if labels is not None else figure_labels(language)
    title = (example.get("title") or "").strip()
    content = (example.get("content") or "").strip()
    inner_html = render_markdown(content, math_svg_map) if content else ""
    # Titolo dell'autore con il math inline (D9), etichetta fuori dal renderer.
    title_text = f" {render_markdown_inline(title, math_svg_map)}" if title else ""
    title_html = f'<div class="example-title">{_label_span(labels, "EX", number)}{title_text}</div>'
    body_html = f'<div class="example-body">{inner_html}</div>'
    return f'<aside class="example">{title_html}{body_html}</aside>'


def _asset_key(asset_id: object) -> str:
    """Id normalizzato della chiave della mappa asset (`.strip().lower()`,
    come `_substitute_asset_refs` e `figure_numbering`)."""
    return str(asset_id or "").strip().lower()


def _build_asset_html_map(
    content: dict[str, Any],
    *,
    visual_svg_map: VisualSvgMap | None = None,
    math_svg_map: dict | None = None,
    language: str = "it",
    asset_numbers: Mapping[tuple[str, str], int] | None = None,
    labels: Mapping[str, str] | None = None,
    lesson_code: str | None = None,
    figure_box_mm: tuple[float, float] | None = None,
    fit_report: list[FigureFitEntry] | None = None,
) -> dict[tuple[str, str], str]:
    """Pre-renderizza ogni asset una sola volta. Chiavi: (KIND, id).

    L'id nella chiave è normalizzato con `.strip().lower()`: i riferimenti
    `[KIND:id]` nel testo e l'id dichiarato dell'asset sono generati
    dall'AI con case e spazi non sempre coerenti (es. asset `TAB_x`
    referenziato come `[TAB:tab_x]`, o `[FIG: A ]`). Il lookup in
    `_substitute_asset_refs` e `compute_asset_numbers` normalizzano nello
    stesso modo.

    `visual_svg_map` è il dict {asset_id → svg | RenderedFigure} prodotto da
    `_prerender_visual_assets_for_lesson` (tutti i formati renderizzabili).
    Se omesso, le figure vanno in fallback testuale. `asset_numbers` è la
    mappa `{(KIND, id_lower) → N}` di `compute_asset_numbers` (contatore
    per kind; l'ordine dell'array qui non conta: il numero è legato
    all'id) e `labels` la mappa di `figure_labels(language)`; senza numero
    il blocco porta la forma non numerata («Tabella.», A2).
    `figure_box_mm` è il box `(larghezza, altezza)` in mm del contenuto
    della pagina (`_compute_template_margins_cm`) per la banda di
    leggibilità (D10); `fit_report` raccoglie le voci del fit."""
    numbers = asset_numbers or {}
    figure_i18n = labels if labels is not None else figure_labels(language)
    out: dict[tuple[str, str], str] = {}
    for asset in content.get("visual_assets") or []:
        key = _asset_key(asset.get("asset_id"))
        out[("FIG", key)] = _render_visual_asset_block(
            asset,
            visual_svg_map=visual_svg_map,
            math_svg_map=math_svg_map,
            number=numbers.get(("FIG", key)),
            labels=figure_i18n,
            variant="lesson",
            language=language,
            lesson_code=lesson_code,
            figure_box_mm=figure_box_mm,
            fit_report=fit_report,
        )
    for table in content.get("tables") or []:
        key = _asset_key(table.get("table_id"))
        out[("TAB", key)] = _render_table_block(
            table,
            math_svg_map=math_svg_map,
            number=numbers.get(("TAB", key)),
            labels=figure_i18n,
            language=language,
        )
    for eq in content.get("equations") or []:
        key = _asset_key(eq.get("equation_id"))
        out[("EQ", key)] = _render_equation_block(
            eq,
            math_svg_map=math_svg_map,
            language=language,
            number=numbers.get(("EQ", key)),
            labels=figure_i18n,
        )
    for ex in content.get("examples") or []:
        key = _asset_key(ex.get("example_id"))
        out[("EX", key)] = _render_example_block(
            ex,
            math_svg_map=math_svg_map,
            number=numbers.get(("EX", key)),
            labels=figure_i18n,
            language=language,
        )
    return out


def _asset_ids_by_kind(content: dict[str, Any]) -> dict[str, list[str]]:
    """Id dichiarati per kind, nell'ordine degli array (`visual_assets`,
    `tables`, `equations`, `examples`): input di `append_uncited_asset_refs`
    e `compute_asset_numbers`."""
    fields = {
        "FIG": ("visual_assets", "asset_id"),
        "TAB": ("tables", "table_id"),
        "EQ": ("equations", "equation_id"),
        "EX": ("examples", "example_id"),
    }
    return {
        kind: [
            str(item.get(id_field) or "")
            for item in content.get(field) or []
            if isinstance(item, dict)
        ]
        for kind, (field, id_field) in fields.items()
    }


def _asset_reference_fn(
    content: dict[str, Any],
    *,
    figure_i18n: Mapping[str, str],
    pdf_labels: Mapping[str, str],
) -> Callable[[str, str, int], str]:
    """Callable `reference(kind, id_lower, n)` del normalizzatore: «Figura
    2», «Tabella 1», e per un `[EQ:id]` in famiglia teorema la parola del
    kind («Lemma 2», stessa di `_render_equation_block`)."""
    theorem_words = {
        _asset_key(eq.get("equation_id")): _theorem_kind_word(eq, pdf_labels)
        for eq in content.get("equations") or []
        if isinstance(eq, dict) and equation_label_family(eq) == "THM"
    }

    def reference(kind: str, id_lower: str, n: int) -> str:
        word = theorem_words.get(id_lower) if kind == "EQ" else None
        if word:
            return asset_ref(figure_i18n, "THM", n, kind_word=word)
        return asset_ref(figure_i18n, kind, n)

    return reference


# ---------------------------------------------------------------------------
# Pre-render delle figure (registro dei renderer → SVG)
# ---------------------------------------------------------------------------

# La pagina di rendering Mermaid (pin `settings.mermaid_cdn_version`, tema di
# `figure_theme` con `htmlLabels: false` top-level), il batch Playwright, il
# post-processing `_strip_mermaid_max_width` e il sanitizer del sorgente
# vivono in `mermaid_prerender` e sono re-esportati in testa a questo modulo
# con i vecchi nomi: i chiamanti (`course_lesson_slides_pdf_service`, video,
# test) non cambiano. Il dispatch per formato (Mermaid, Vega-Lite, DOT,
# function) è di `figure_render_service.render_svg_map`.


async def _prerender_visual_assets_for_lesson(
    content: dict[str, Any],
    *,
    language: str = "it",
) -> dict[str, RenderedFigure]:
    """Pre-renderizza in batch tutti gli asset visivi renderizzabili della
    lezione attraverso il registro (`render_figure_map`: un batch per
    formato, cache LRU, semaforo e timeout). Ritorna {asset_id →
    RenderedFigure} (SVG e metriche del testo, D10); chiavi assenti
    indicano rendering fallito o formato non disponibile (fallback del
    partial). Non solleva mai: i worker non vedono eccezioni nuove."""
    assets = [a for a in content.get("visual_assets") or [] if isinstance(a, dict)]
    if not assets:
        return {}
    return await figure_render_service.render_figure_map(assets, language=language)


# Alias del nome storico (i chiamanti esterni e la documentazione lo citano):
# oggi pre-renderizza tutti i formati, non solo Mermaid.
_prerender_mermaid_for_lesson = _prerender_visual_assets_for_lesson


# ---------------------------------------------------------------------------
# Pre-render LaTeX → SVG (MathJax via Playwright)
#
# WeasyPrint NON renderizza MathML (stampa solo il contenuto testuale,
# perdendo pedici/apici/frazioni). Pre-renderizziamo quindi ogni formula in
# SVG autonomo via MathJax in Playwright — stesso pattern dei diagrammi
# Mermaid — ed embeddiamo l'SVG, che WeasyPrint rende correttamente.
# ---------------------------------------------------------------------------

# Segnaposto sostituito da `build_mathjax_renderer_html` con il pin
# `settings.mathjax_cdn_version` (stesso schema di `mermaid_prerender`);
# `_MATHJAX_RENDERER_HTML` è servita pigra dal `__getattr__` di modulo.
_MATHJAX_RENDERER_HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>body{margin:0;padding:0;
font-family:"Noto Sans CJK JP","Noto Sans","DejaVu Sans",sans-serif;}</style></head>
<body>
<script>
// Config PRIMA del load. fontCache:'none' → ogni SVG è autonomo (glyph
// come path inline, niente <defs>/<use> con id condivisi che collidono
// incollando molte formule nello stesso documento). typeset:false → niente
// auto-render: usiamo MathJax.tex2svg() in modo programmatico.
window.MathJax = {
  svg: { fontCache: 'none' },
  startup: {
    typeset: false,
    ready: () => {
      window.MathJax.startup.defaultReady();
      window.__mathReady = true;
    }
  }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@__MATHJAX_VERSION__/es5/tex-svg.js"></script>
<script>
window.__renderMath = (latex, display) => {
  try {
    const node = window.MathJax.tex2svg(latex, { display: !!display });
    // Su errore di parsing MathJax emette un nodo merror: trattiamo come
    // fallimento → il chiamante ricade su MathML.
    if (node.querySelector('[data-mjx-error], [data-mml-node="merror"]')) {
      return null;
    }
    const svg = node.querySelector('svg');
    return svg ? svg.outerHTML : null;
  } catch (e) { return null; }
};
</script>
</body></html>
"""


def build_mathjax_renderer_html(*, version: str | None = None) -> str:
    """Pagina di rendering MathJax con il pin richiesto (default:
    `settings.mathjax_cdn_version`)."""
    pin = version or get_settings().mathjax_cdn_version
    return _MATHJAX_RENDERER_HTML_TEMPLATE.replace("__MATHJAX_VERSION__", pin)


async def _prerender_math_to_svg_batch_async(
    items: list[tuple[str, str]],
) -> list[str | None]:
    """Renderizza una lista di `(latex, display)` a SVG con UNA sessione
    Playwright headless (MathJax tex-svg). Ritorna lista parallela: SVG o
    `None` se il rendering fallisce. Stesso pattern di
    `_prerender_mermaid_to_svg_batch_async` (va wrappata via il `_sync`/
    `_batch` per il ProactorEventLoop su Windows); la pagina può
    contattare solo il CDN (`block_external_requests`, SEC-1). Nessun retry
    del launch: un launch fallito propaga (auto-retry del worker sull'intera
    lezione), una CDN scaduta degrada tutta la lezione a MathML; un retry
    del solo launch è un follow-up da decidere sui log di produzione."""
    if not items:
        return []

    from playwright.async_api import async_playwright

    results: list[str | None] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await _mermaid_prerender.block_external_requests(page)
            await page.set_content(build_mathjax_renderer_html(), wait_until="domcontentloaded")
            try:
                # MathJax tex-svg.js è ~1MB: timeout più ampio del Mermaid.
                await page.wait_for_function("window.__mathReady === true", timeout=20_000)
            except Exception as exc:  # CDN irraggiungibile
                log.warning("mathjax_renderer_setup_failed", error=str(exc))
                return [None] * len(items)

            for latex, display in items:
                if not (latex or "").strip():
                    results.append(None)
                    continue
                try:
                    svg = await page.evaluate(
                        "([code, disp]) => window.__renderMath(code, disp)",
                        [latex, display == "block"],
                    )
                    results.append(svg if (isinstance(svg, str) and svg.strip()) else None)
                except Exception as exc:
                    log.warning("math_render_failed", error=str(exc), preview=latex[:80])
                    results.append(None)
        finally:
            await browser.close()
    return results


def _prerender_math_to_svg_batch_sync(
    items: list[tuple[str, str]],
) -> list[str | None]:
    """Sync wrapper con loop dedicato (ProactorEventLoop su Windows, unico
    a supportare `subprocess_exec` di Playwright). Da `asyncio.to_thread`."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_prerender_math_to_svg_batch_async(items))
    finally:
        with contextlib.suppress(Exception):
            loop.close()


async def _prerender_math_to_svg_batch(
    items: list[tuple[str, str]],
) -> list[str | None]:
    """Esegue il pre-render Playwright in un thread pool (loop dedicato)."""
    if not items:
        return []
    return await asyncio.to_thread(_prerender_math_to_svg_batch_sync, items)


def _dicts(seq: object) -> list[dict[str, Any]]:
    """Gli elementi `dict` di una lista di `content_raw` (gli altri sono
    ignorati, come nei renderer)."""
    return [x for x in (seq if isinstance(seq, list) else []) if isinstance(x, dict)]


def _citation_text(ref: object) -> str:
    """Testo di una voce di `references`: `citation` del dict, o la stringa
    grezza (forma storica, solo nei test)."""
    return str(ref.get("citation") or "") if isinstance(ref, dict) else str(ref or "")


def _walk_tokens(tokens: Sequence[Token]) -> Iterator[Token]:
    """Tutti i token, figli compresi, in ordine di documento."""
    stack: deque[Token] = deque(tokens)
    while stack:
        tok = stack.popleft()
        yield tok
        if tok.children:
            stack.extendleft(reversed(tok.children))


def _iter_math_sources(
    content: dict[str, Any], *, language: str = "it"
) -> Iterator[tuple[str, str]]:
    """`(testo, "block"|"inline")` per ogni campo markdown reso: specchio
    dei call-site di `render_markdown` / `render_markdown_inline` in
    `render_lesson_html` e `_build_asset_html_map`, con lo stesso
    pre-trattamento (`strip`, `caption_text`). Il corpo è quello che il
    renderer parsa davvero: `_prepare_lesson_body` (titoli `## title`,
    sintesi, numerazione e rimandi «Figura N» già riscritti, nella lingua
    del corso) con ogni ancora sostituita da un HTML block segnaposto,
    perché una formula a cavallo di un'ancora (`$a\\n[FIG:x]\\nb$`) è
    spezzata dal blocco nel renderer e deve esserlo anche qui; la coda
    (`key_takeaways`, `references[].citation`) passa da `cite_asset_refs`
    come in `render_lesson_html` (`$a [FIG:x] b$` → `a Figura 1 b` su
    entrambi i lati). Ogni nuovo campo reso va aggiunto QUI: il test di
    parità collector/renderer fallisce altrimenti."""
    prepared = _prepare_lesson_body(content, language=language)
    placeholders = dict.fromkeys(prepared.asset_numbers, _ASSET_ANCHOR_PLACEHOLDER_HTML)
    yield _substitute_asset_refs(prepared.markdown, placeholders), "block"
    for table in _dicts(content.get("tables")):
        yield (table.get("markdown") or "").strip(), "block"
        yield table.get("caption") or "", "inline"
    for ex in _dicts(content.get("examples")):
        yield (ex.get("content") or "").strip(), "block"
        yield (ex.get("title") or "").strip(), "inline"
    for eq in _dicts(content.get("equations")):
        yield (eq.get("statement") or "").strip(), "block"
        yield (eq.get("explanation") or "").strip(), "block"
        yield (eq.get("label") or "").strip(), "inline"
        for step in proof_steps(eq):
            yield (step.get("text") or "").strip(), "block"
    for asset in _dicts(content.get("visual_assets")):
        yield caption_text(str(asset.get("caption") or "")), "inline"
    for kt in content.get("key_takeaways") or []:
        yield prepared.cite(kt), "inline"
    for ref in content.get("references") or []:
        yield prepared.cite(_citation_text(ref)), "inline"


def _collect_math_from_content(
    content: dict[str, Any], *, language: str = "it"
) -> list[tuple[str, str]]:
    """Raccoglie tutte le chiavi `(latex_normalizzato, display)` di una
    lezione con la STESSA grammatica del renderer: le equazioni dedicate
    (`equations[].latex` e `proof[].latex`, rese da `_render_math`) e ogni
    token math prodotto dal parse dei campi di `_iter_math_sources` con le
    due istanze markdown-it (`parse` per i blocchi, `parseInline` per i
    campi inline). La chiave nasce da `_token_math_key`, la stessa della
    rule di render; i `$..$` currency sono già stati declassati dalla core
    rule: mai raccolti, mai resi. `language` è la lingua del corso: decide
    il testo dei rimandi riscritti («Figura 1» / «Figure 1») che può finire
    dentro una formula. Dedup nell'ordine di prima comparsa."""
    seen: set[tuple[str, str]] = set()
    items: list[tuple[str, str]] = []

    def _add(key: tuple[str, str] | None) -> None:
        if key and key not in seen:
            seen.add(key)
            items.append(key)

    for eq in _dicts(content.get("equations")):
        src = _normalize_math_source(eq.get("latex") or "")
        _add((src, "block") if src else None)
        for step in proof_steps(eq):
            src = _normalize_math_source(step.get("latex") or "")
            _add((src, "block") if src else None)

    for text, mode in _iter_math_sources(content, language=language):
        if not text:
            continue
        if mode == "inline":
            tokens = _md_inline_renderer.parseInline(text, {})
        else:
            tokens = _md_renderer.parse(text, {})
        for tok in _walk_tokens(tokens):
            _add(_token_math_key(tok))

    return items


async def _prerender_math_for_lesson(
    content: dict[str, Any], *, language: str = "it"
) -> MathSvgMap:
    """Estrae tutte le formule e le pre-renderizza in batch. Ritorna sempre
    una `MathSvgMap` `{(latex, display) → svg}` con `requested` = chiavi
    raccolte (anche vuota: nessuna formula, o batch fallito); le chiavi
    assenti ricadono sul MathML, loggate da `_render_math_by_key`.
    `language` è la lingua del corso, la stessa di `render_lesson_html`
    (vedi `_collect_math_from_content`)."""
    items = _collect_math_from_content(content, language=language)
    out = MathSvgMap(requested=len(items))
    if not items:
        return out
    svgs = await _prerender_math_to_svg_batch(items)
    for (latex, display), svg in zip(items, svgs, strict=True):
        if svg:
            out[(latex, display)] = svg
    return out


_PRE_SEGMENT_RE = re.compile(r"(<pre\b[\s\S]*?</pre>)")
_BLANK_LINE_RE = re.compile(r"(?m)^[ \t]*$")
_BLANK_LINE_WITH_BREAK_RE = re.compile(r"(?m)^[ \t]*\r?\n")


def _neutralize_blank_lines(html: str) -> str:
    """L'HTML di un asset entra nel markdown della dispensa come HTML block,
    che markdown-it chiude alla prima riga vuota: il resto finirebbe in un
    `<p>` e le sue `\\[..\\]` o `$..$` tornerebbero a essere math (L11).
    Dentro un `<pre>` (fence di un esempio) le righe vuote diventano U+00A0
    (invisibile, come `figure_markup._fallback_text`); fuori sono rimosse
    (come `figure_markup._body_without_blank_lines`). Gli a capo CRLF/CR
    sono prima normalizzati a LF come in `_fallback_text`: markdown-it li
    normalizza solo al parse, e una riga fatta di `\\r` chiuderebbe l'HTML
    block. Identità sul partial figura, che non ha righe vuote per
    costruzione."""
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for i, part in enumerate(_PRE_SEGMENT_RE.split(html)):
        if part.startswith("<pre"):
            out.append(_BLANK_LINE_RE.sub("\u00a0", part))
        elif i == 0:
            out.append(_BLANK_LINE_WITH_BREAK_RE.sub("", part))
        else:
            # Dopo un `</pre>` la prima riga del segmento è la coda della
            # riga di chiusura, non una riga vuota: il suo a capo resta.
            head, sep, rest = part.partition("\n")
            out.append(head + sep + _BLANK_LINE_WITH_BREAK_RE.sub("", rest))
    return "".join(out)


# HTML block segnaposto con cui il collector (`_iter_math_sources`)
# sostituisce le ancore al posto dei blocchi veri: `<div>` apre un HTML
# block di markdown-it come `<figure>`/`<aside>` degli asset, quindi il
# parse ha la stessa struttura di paragrafi del renderer.
_ASSET_ANCHOR_PLACEHOLDER_HTML: Final = "<div></div>"


def _substitute_asset_refs(md_source: str, asset_html_map: dict[tuple[str, str], str]) -> str:
    """Sostituisce ogni `[KIND:id]` con il blocco HTML pre-renderizzato.
    Il blocco è inserito su righe proprie (con righe vuote prima/dopo) e
    senza righe vuote interne (`_neutralize_blank_lines`), in modo che
    markdown-it lo riconosca come UN SOLO HTML block-level."""

    def _sub(m: re.Match[str]) -> str:
        kind = m.group(1)
        ref_id = m.group(2).strip()
        # Lookup case-insensitive sull'id: le chiavi della mappa sono
        # normalizzate a minuscolo (vedi `_build_asset_html_map`).
        html = asset_html_map.get((kind, ref_id.lower()))
        if html is None:
            return (
                f'\n\n<div class="missing-asset">'
                f"Asset non trovato: [{kind}:{_html_escape_text(ref_id)}]"
                f"</div>\n\n"
            )
        return f"\n\n{_neutralize_blank_lines(html)}\n\n"

    return _ASSET_REF_RE.sub(_sub, md_source)


def _build_lesson_body_markdown(content: dict[str, Any]) -> str:
    """Concatena introduction + sections (con `## title`) + summary in un
    unico documento markdown, analogamente a `LessonContentView` lato FE."""
    parts: list[str] = []
    intro = (content.get("introduction") or "").strip()
    if intro:
        parts.append(intro)
    for section in content.get("sections") or []:
        title = (section.get("title") or "").strip()
        body = (section.get("content") or "").strip()
        if title:
            parts.append(f"## {title}")
        if body:
            parts.append(body)
    summary = (content.get("summary") or "").strip()
    if summary:
        # Etichetta della sezione "Sintesi" (lingua corso applicata fuori).
        parts.append("## __SUMMARY_HEADING__")
        parts.append(summary)
    return "\n\n".join(parts)


def _replace_summary_heading(md: str, summary_label: str) -> str:
    return md.replace("__SUMMARY_HEADING__", summary_label)


class _PreparedBody(NamedTuple):
    """Corpo della dispensa dopo numerazione e rimandi (D1-D4) e PRIMA
    della sostituzione delle ancore con l'HTML degli asset: è il testo che
    renderer (`render_lesson_html`) e collector (`_iter_math_sources`)
    parsano, con le stesse citazioni riscritte."""

    markdown: str
    asset_numbers: dict[tuple[str, str], int]
    numbers: dict[str, dict[str, int]]
    reference: Callable[[str, str, int], str]

    def cite(self, text: object) -> str:
        """Rimandi testuali della coda (`cite_asset_refs`): mai blocchi."""
        return cite_asset_refs(str(text or ""), numbers=self.numbers, reference=self.reference)


def _prepare_lesson_body(content: dict[str, Any], *, language: str) -> _PreparedBody:
    """Pipeline D3/D4 + D1/D2 del corpo, unica per renderer e collector:
    introduzione → sezioni → sintesi, tag degli asset mai citati in coda
    (`append_uncited_asset_refs`), numeri per kind sul corpo NON ancora
    normalizzato (`compute_asset_numbers`), etichetta della sintesi nella
    lingua del corso, poi `normalize_asset_refs` (citazioni in linea →
    «Figura N», una sola ancora per asset). Le ancore restano da
    sostituire: con l'HTML vero nel renderer, con un segnaposto nel
    collector."""
    labels = _labels_for(language)
    figure_i18n = figure_labels(language)
    ids_by_kind = _asset_ids_by_kind(content)
    body_md = _build_lesson_body_markdown(content)
    body_md = append_uncited_asset_refs(body_md, ids_by_kind)
    asset_numbers = compute_asset_numbers(body_md, ids_by_kind)
    numbers: dict[str, dict[str, int]] = {kind: {} for kind in ASSET_KINDS}
    for (kind, asset_id), n in asset_numbers.items():
        numbers[kind][asset_id] = n
    reference = _asset_reference_fn(content, figure_i18n=figure_i18n, pdf_labels=labels)
    body_md = _replace_summary_heading(body_md, labels["summary"])
    body_md = normalize_asset_refs(body_md, numbers=numbers, reference=reference)
    return _PreparedBody(body_md, asset_numbers, numbers, reference)


# ---------------------------------------------------------------------------
# Asset URL resolver (loghi + background)
# ---------------------------------------------------------------------------


_IMAGE_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
}


def _resolve_template_asset_url(
    raw: str | None, *, public_base_url: str | None = None
) -> str | None:
    """Risolve un path asset del PDF template in URL utilizzabile da
    Playwright.

    Strategia (in ordine di tentativo):
      1. URL assoluto (http/https/file/data) → restituito così com'è.
      2. Path relativo `/uploads/...` (storage locale) → letto dal
         filesystem e embeddato come **data URL base64**, in modo che
         Playwright NON debba fare fetch HTTP verso il server backend
         (eviterebbe dipendenze di rete e problemi di reachability del
         localhost dal Chromium).
      3. Fallback: se `public_base_url` è fornito, costruisce
         `{public_base_url}/{path}` (utile in test o per CDN).
      4. Asset non risolvibile → `None` (il template renderizza
         comunque, senza quell'asset specifico).

    `public_base_url` è mantenuto per backward compat / testing ma il
    path normale di produzione non lo usa più.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if re.match(r"^(https?://|file://|data:)", raw):
        return raw

    # Path relativo: scarica i bytes dallo storage attivo (RETR su OVH, read
    # locale altrimenti) e li embedda come data URL base64, così
    # WeasyPrint/Playwright non devono fare fetch HTTP a runtime.
    del public_base_url  # strategia #3 (fetch HTTP) deprecata: ora si embedda
    try:
        data = remote_storage.get_storage().download_bytes(remote_storage.uploads_key(raw))
    except remote_storage.StorageFileNotFound:
        log.warning("pdf_template_asset_missing", path=raw)
        return None
    except remote_storage.StorageError as exc:
        log.warning("pdf_template_asset_read_failed", path=raw, error=str(exc))
        return None
    suffix = Path(raw).suffix.lower()
    mime = _IMAGE_MIME_BY_EXT.get(suffix, "application/octet-stream")
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# ---------------------------------------------------------------------------
# Jinja2 — render dell'HTML completo
# ---------------------------------------------------------------------------


_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _BACKEND_DIR / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html", "xml", "j2")),
    trim_blocks=False,
    lstrip_blocks=False,
)


def _format_pdf_template_for_render(
    tpl: PdfTemplate, *, public_base_url: str | None
) -> dict[str, Any]:
    return {
        "font_family": tpl.font_family,
        "text_color": tpl.text_color,
        "primary_color": tpl.primary_color,
        "secondary_color": tpl.secondary_color,
        "page_size": tpl.page_size,
        "margin_mm": tpl.margin_mm,
        "header_height_mm": tpl.header_height_mm,
        "footer_height_mm": tpl.footer_height_mm,
        "background_opacity_pct": tpl.background_opacity_pct,
        "background_image_url": _resolve_template_asset_url(
            tpl.background_image_path, public_base_url=public_base_url
        ),
        "logo_left_url": _resolve_template_asset_url(
            tpl.logo_left_path, public_base_url=public_base_url
        ),
        "logo_right_url": _resolve_template_asset_url(
            tpl.logo_right_path, public_base_url=public_base_url
        ),
    }


def _default_template_dict(*, language: str) -> dict[str, Any]:
    """Usato quando l'org non ha nessun pdf_template configurato — un set
    di valori sicuri che produce comunque un PDF dignitoso."""
    return {
        "font_family": "Inter",
        "text_color": "#1F1F1F",
        "primary_color": "#1976D2",
        "secondary_color": "#9C27B0",
        "page_size": "A4",
        "margin_mm": 20,
        "header_height_mm": 20,
        "footer_height_mm": 15,
        "background_opacity_pct": 0,
        "background_image_url": None,
        "logo_left_url": None,
        "logo_right_url": None,
    }


# Altezza e larghezza fisiche del foglio in cm per ciascuna page-size
# supportata. Usate da `_compute_template_margins_cm` per derivare l'area
# utile del content-area (paper - margini): l'altezza dà il `max-height`
# delle figure (cintura contro il taglio dal page-break) e, con la
# larghezza, il box entro cui `figure_scale` sceglie la larghezza dalla
# banda di leggibilità (D10).
_PAGE_HEIGHTS_CM: dict[str, float] = {
    "A4": 29.7,
    "A3": 42.0,
    "Letter": 27.94,
    "letter": 27.94,
    "LETTER": 27.94,
}
_PAGE_WIDTHS_CM: dict[str, float] = {
    "A4": 21.0,
    "A3": 29.7,
    "Letter": 21.59,
    "letter": 21.59,
    "LETTER": 21.59,
}


def _compute_template_margins_cm(tpl_dict: dict[str, Any]) -> dict[str, float]:
    """Converte `margin_mm` + `header_height_mm` + `footer_height_mm` del
    template in cm per il CSS `@page`.

    - top: `margin_mm` esteso a `header_height_mm + 5mm` se ci sono
      loghi (servono spazio per il running header).
    - bottom: `margin_mm` esteso a `footer_height_mm + 5mm` per fare
      spazio al page counter senza schiacciarlo sui contenuti.
    - side: sempre `margin_mm` (il watermark è gestito via offset
      negativi sul `.page-background`).

    Calcola anche `max_figure_height_cm` = altezza utile del content
    area meno una safety di ~1.5cm (per padding figure + caption +
    breathing room): è il `max-height` delle figure, cintura contro il
    taglio dal page-break (misurato: su A4 di default l'SVG che entra in
    una pagina intera è alto al più 248,7 mm; il valore 242 mm sta sotto).
    Il box della banda di leggibilità (D10) è `figure_box_w_mm` (larghezza
    del contenuto: 170 mm su A4 con margine 20) × `figure_box_h_mm`
    (`max_figure_height_cm·10`).
    """
    margin_mm = max(5, int(tpl_dict.get("margin_mm", 20)))
    header_h_mm = int(tpl_dict.get("header_height_mm", 0))
    footer_h_mm = int(tpl_dict.get("footer_height_mm", 0))

    has_running_header = bool(tpl_dict.get("logo_left_url") or tpl_dict.get("logo_right_url"))
    top_mm = max(margin_mm, header_h_mm + 5) if has_running_header else margin_mm
    # Footer riservato sempre per page counter — anche se template non ha
    # footer_height_mm > 0, lasciamo almeno `margin_mm` di spazio.
    bottom_mm = max(margin_mm, footer_h_mm + 5) if footer_h_mm > 0 else margin_mm

    page_size = tpl_dict.get("page_size", "A4")
    paper_h_cm = _PAGE_HEIGHTS_CM.get(page_size, 29.7)
    paper_w_cm = _PAGE_WIDTHS_CM.get(page_size, 21.0)
    content_h_cm = paper_h_cm - (top_mm / 10.0) - (bottom_mm / 10.0)
    max_figure_height_cm = max(5.0, round(content_h_cm - 1.5, 2))
    figure_box_w_mm = max(10.0, round(paper_w_cm * 10.0 - 2 * margin_mm, 2))

    return {
        "margin_top_cm": round(top_mm / 10.0, 3),
        "margin_side_cm": round(margin_mm / 10.0, 3),
        "margin_bottom_cm": round(bottom_mm / 10.0, 3),
        "max_figure_height_cm": max_figure_height_cm,
        "figure_box_w_mm": figure_box_w_mm,
        "figure_box_h_mm": round(max_figure_height_cm * 10.0, 2),
    }


def _labels_for(language: str) -> dict[str, str]:
    if (language or "it").lower().startswith("en"):
        return {
            "summary": "Summary",
            "key_takeaways": "Key takeaways",
            "references": "References",
            "module": "Module",
            "lesson": "lesson",
            "cfu": "ECTS",
            "teacher": "Instructor",
            "proof": "Proof",
            "kind_definition": "Definition",
            "kind_formula": "Formula",
            "kind_identity": "Identity",
            "kind_theorem": "Theorem",
            "kind_proposition": "Proposition",
            "kind_lemma": "Lemma",
            "kind_corollary": "Corollary",
        }
    return {
        "summary": "Sintesi",
        "key_takeaways": "Punti chiave",
        "references": "Riferimenti",
        "module": "Modulo",
        "lesson": "lezione",
        "cfu": "CFU",
        "teacher": "Docente",
        "proof": "Dimostrazione",
        "kind_definition": "Definizione",
        "kind_formula": "Formula",
        "kind_identity": "Identità",
        "kind_theorem": "Teorema",
        "kind_proposition": "Proposizione",
        "kind_lemma": "Lemma",
        "kind_corollary": "Corollario",
    }


def _format_lesson_code_label(lesson_code: str | None, labels: dict[str, str]) -> str:
    """Trasforma il codice lezione `M1.L1` in `Modulo 1 - lezione 1`
    (localizzato). Se il codice non matcha il pattern atteso, lo ritorna
    invariato (es. lezioni di verifica o codici legacy)."""
    m = re.match(r"^M(\d+)\.L(\d+)$", (lesson_code or "").strip())
    if not m:
        return lesson_code or ""
    return f"{labels['module']} {int(m.group(1))} - {labels['lesson']} {int(m.group(2))}"


def render_lesson_html(
    *,
    course: Course,
    lesson: CourseLesson,
    organization: Organization | None,
    pdf_template: PdfTemplate | None,
    public_base_url: str | None = None,
    mermaid_svg_map: VisualSvgMap | None = None,
    math_svg_map: dict | None = None,
    teacher_name: str | None = None,
    visual_svg_map: VisualSvgMap | None = None,
    fit_report: list[FigureFitEntry] | None = None,
) -> str:
    """Pure-function: produce l'HTML completo della lezione, pronto per
    WeasyPrint.

    `visual_svg_map` è `{asset_id → svg | RenderedFigure}` per tutti i
    formati renderizzabili (Mermaid, Vega-Lite, DOT, function; una stringa
    vale `RenderedFigure.from_svg`); `mermaid_svg_map` è il nome storico
    dello stesso argomento, mantenuto per i chiamanti esistenti: le due
    mappe sono fuse (`visual_svg_map` prevale). Se una figura manca dalla
    mappa, il partial emette il fallback `<pre class="figure-fallback">` e
    il log registra `figure_render_fallback` (A23). Nel flusso di
    produzione (`materialize_lesson_pdf`) la mappa è riempita da
    `_prerender_visual_assets_for_lesson`. Indipendente dal DB e dal
    worker: testabile in isolamento.

    Larghezza delle figure (D10): il box del contenuto viene dal template
    (`_compute_template_margins_cm`, calcolato PRIMA della mappa degli
    asset) e ogni figura riceve `style="width:Wmm"` dalla banda di
    leggibilità; `fit_report`, se dato, raccoglie una `FigureFitEntry` per
    figura (`figure_fit_report` in `materialize_lesson_pdf`).

    Numerazione D3/D4 e rimandi D1/D2: il corpo (introduzione → sezioni →
    sintesi) riceve in coda i tag `[KIND:id]` degli asset mai citati
    (figure, tabelle, equazioni, esempi; ordine FIG → TAB → EQ → EX, A12),
    poi `compute_asset_numbers` assegna N per kind nell'ordine di prima
    citazione, sul corpo NON ancora normalizzato; la mappa degli asset è
    costruita DOPO, con i numeri. Solo allora `normalize_asset_refs`
    riscrive ogni citazione in linea nel rimando testuale («Figura 2»,
    «Tabella 1», «Lemma 2») e lascia/inserisce un'unica ancora su riga
    propria per asset, che `_substitute_asset_refs` sostituisce con il
    blocco: dopo la normalizzazione gli unici tag risolvibili nel corpo
    sono ancore. La coda (`key_takeaways`, `references[].citation`) non
    partecipa alla numerazione: riceve solo rimandi testuali
    (`cite_asset_refs`), mai blocchi, su entrambi i lati (frontend
    `LessonContentView`), e poi il math inline via `render_markdown_inline`
    (D9: prima il rimando, poi il math; il risultato è `Markup` per
    l'autoescape del template).
    """
    raw = lesson.content_raw or {}
    if not raw:
        raise ConflictError(
            f"Lezione {lesson.lesson_code} senza content_raw — impossibile esportare.",
            code="lesson_content_missing",
        )

    language = (course.language_code or "it").lower()
    labels = _labels_for(language)
    figure_i18n = figure_labels(language)
    svg_map: dict[str, str | RenderedFigure] = {
        **(mermaid_svg_map or {}),
        **(visual_svg_map or {}),
    }

    tpl_dict: dict[str, Any]
    if pdf_template is not None:
        tpl_dict = _format_pdf_template_for_render(pdf_template, public_base_url=public_base_url)
    else:
        tpl_dict = _default_template_dict(language=language)

    # Prima della mappa degli asset: il box delle figure viene da qui (D10).
    margins_cm = _compute_template_margins_cm(tpl_dict)

    prepared = _prepare_lesson_body(raw, language=language)
    asset_map = _build_asset_html_map(
        raw,
        visual_svg_map=svg_map,
        math_svg_map=math_svg_map,
        language=language,
        asset_numbers=prepared.asset_numbers,
        labels=figure_i18n,
        lesson_code=lesson.lesson_code,
        figure_box_mm=(margins_cm["figure_box_w_mm"], margins_cm["figure_box_h_mm"]),
        fit_report=fit_report,
    )
    body_md = _substitute_asset_refs(prepared.markdown, asset_map)
    body_html = render_markdown(body_md, math_svg_map)

    def _tail(text: object) -> Markup:
        return Markup(render_markdown_inline(prepared.cite(text), math_svg_map))

    key_takeaways = [_tail(kt) for kt in raw.get("key_takeaways") or []]
    references = [
        {**(ref if isinstance(ref, dict) else {}), "citation": _tail(_citation_text(ref))}
        for ref in raw.get("references") or []
    ]

    template = _jinja_env.get_template("lesson_pdf.html.j2")
    html = template.render(
        language=language,
        labels=labels,
        course={
            "title": course.title,
            "language": language,
            "cfu": course.cfu,
            "teacher": teacher_name,
        },
        lesson={
            "title": lesson.title,
            "lesson_code": lesson.lesson_code,
            "code_label": _format_lesson_code_label(lesson.lesson_code, labels),
        },
        tpl=tpl_dict,
        margin_top_cm=margins_cm["margin_top_cm"],
        margin_side_cm=margins_cm["margin_side_cm"],
        margin_bottom_cm=margins_cm["margin_bottom_cm"],
        max_figure_height_cm=margins_cm["max_figure_height_cm"],
        body_html=body_html,
        key_takeaways=key_takeaways,
        references=references,
    )
    return html


# ---------------------------------------------------------------------------
# WeasyPrint — HTML → PDF bytes
# ---------------------------------------------------------------------------


def _log_figure_fit_report(
    *, lesson_code: str | None, fit_report: Sequence[FigureFitEntry]
) -> None:
    """Summary per lezione del fit delle figure (D10): totale, in banda e
    l'elenco delle figure fuori banda con corpo e provenienza del font
    (input del gate editoriale D13). Condiviso da dispensa e slide."""
    out_of_band = [
        (e.asset_id, e.fmt, e.text_pt, e.font_source) for e in fit_report if not e.in_band
    ]
    log.info(
        "figure_fit_report",
        lesson_code=lesson_code,
        total=len(fit_report),
        in_band=len(fit_report) - len(out_of_band),
        out_of_band=out_of_band,
    )


def _render_with_weasyprint_sync(html: str, *, base_url: str | None = None) -> bytes:
    """Render sincrono HTML → PDF via WeasyPrint.

    Niente JavaScript (WeasyPrint non lo esegue): tutta la logica
    JS-dependent (KaTeX, Mermaid) è già stata espansa server-side prima
    di arrivare qui. WeasyPrint legge `@page` dal CSS della pagina,
    quindi tutta la geometria (formato, margini, header running, page
    counter) è gestita dal template `lesson_pdf.html.j2`.

    `base_url` è opzionale e serve solo se il template usa percorsi
    relativi per immagini (loghi/sfondo). Nel flusso normale gli asset
    sono embedded come data: URL e `base_url=None` va bene.
    """
    return HTML(string=html, base_url=base_url).write_pdf()


async def generate_pdf_bytes(
    *,
    html: str,
    base_url: str | None = None,
) -> bytes:
    """Wrapper async: esegue WeasyPrint in un thread pool per non bloccare
    il loop asyncio del worker. WeasyPrint è CPU-bound e relativamente
    veloce (~500ms-1s per A4 multipagina) — l'overhead di `to_thread`
    è trascurabile."""
    return await asyncio.to_thread(_render_with_weasyprint_sync, html, base_url=base_url)


# ---------------------------------------------------------------------------
# Materializzazione (chiamato dal worker)
# ---------------------------------------------------------------------------


async def materialize_lesson_pdf(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    public_base_url: str | None = None,
) -> str:
    """Genera e salva su disco il PDF della lezione. Aggiorna i campi DB
    (`pdf_path`, `pdf_template_id`, `pdf_generated_at`). Restituisce il
    path relativo persistito.

    Pipeline:
      1. Risolve il template (lesson.pdf_template_id → org default).
      2. Pre-renderizza le figure attraverso il registro dei renderer
         (Mermaid via Playwright con una sola sessione per lezione,
         Vega-Lite, DOT, function → SVG) e le formule via MathJax.
      3. Costruisce l'HTML completo della lezione (markdown → HTML +
         math → SVG MathJax + asset map con gli SVG delle figure e la
         numerazione «Figura N.»).
      4. Renderizza il PDF con WeasyPrint (single-pass, sfondo
         edge-to-edge garantito dal CSS Paged Media).

    Il template usato è quello scelto dall'utente al momento della
    richiesta di export (`lesson.pdf_template_id` settato al momento
    di `request_lesson_pdf`); se quel campo è `None` o il template è
    stato eliminato, fall-back al default dell'org.

    Non gestisce transizioni di stato: lo fa il worker.
    """
    organization = await _get_organization(db, course.organization_id)
    pdf_template = await _resolve_pdf_template_for_lesson(
        db, organization_id=course.organization_id, lesson=lesson
    )
    # Docente del corso (assegnatario) per la copertina.
    teacher = await db.get(User, course.assignee_user_id)
    teacher_name = teacher.full_name if teacher else None

    # Pre-render delle figure: un batch per formato attraverso il registro
    # (per Mermaid una singola sessione Playwright produce gli SVG di tutti
    # i diagrammi della lezione; senza diagrammi Mermaid nessun Chromium).
    raw_content = lesson.content_raw or {}
    language = (course.language_code or "it").lower()
    visual_svg_map = await _prerender_visual_assets_for_lesson(raw_content, language=language)
    # Pre-render LaTeX → SVG (MathJax): WeasyPrint non rende il MathML.
    math_svg_map = await _prerender_math_for_lesson(raw_content, language=language)

    fit_report: list[FigureFitEntry] = []
    html = await asyncio.to_thread(
        render_lesson_html,
        course=course,
        lesson=lesson,
        organization=organization,
        pdf_template=pdf_template,
        public_base_url=public_base_url,
        visual_svg_map=visual_svg_map,
        math_svg_map=math_svg_map,
        teacher_name=teacher_name,
        fit_report=fit_report,
    )
    # Un evento per lezione se qualche formula è ricaduta sul MathML.
    _log_math_fallbacks(lesson_code=lesson.lesson_code, svg_map=math_svg_map)
    # Un evento per lezione con l'esito del fit delle figure (input del gate D13).
    _log_figure_fit_report(lesson_code=lesson.lesson_code, fit_report=fit_report)

    pdf_bytes = await generate_pdf_bytes(html=html)

    rel = pdf_relative_path(
        organization_id=course.organization_id,
        course_id=course.id,
        lesson_id=lesson.id,
    )
    await asyncio.to_thread(
        remote_storage.get_storage().upload_bytes,
        remote_storage.pdf_key(rel),
        pdf_bytes,
    )

    lesson.pdf_path = rel
    lesson.pdf_template_id = pdf_template.id if pdf_template else None
    lesson.pdf_generated_at = datetime.now(UTC)
    return rel


# ---------------------------------------------------------------------------
# Public API: enqueue + cancel
# ---------------------------------------------------------------------------


async def request_lesson_pdf(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
    pdf_template_id: uuid.UUID | None = None,
) -> Course:
    """Sposta `pdf_status → pending`. Il worker prende la riga al
    prossimo tick e genera il PDF in parallelo (cap 2).

    Se `pdf_template_id` è fornito, persiste la scelta su
    `lesson.pdf_template_id` PRIMA che il worker prenda il task — il
    rendering userà quel template invece del default dell'org.
    Validazione: il template deve appartenere all'org del corso.

    Vincoli:
      - `lesson.content_status` deve essere `ready` o `approved`
      - `lesson.pdf_status` deve essere `empty`, `ready` o `failed`
        (NON `pending`/`processing` — già accodato/in flight)
    """
    if lesson.is_assessment:
        raise ConflictError(
            f"La lezione {lesson.lesson_code} è una verifica delle "
            f"competenze: non è esportabile in PDF.",
            code="lesson_is_assessment_no_pdf",
        )
    if lesson.content_status not in EXPORTABLE_CONTENT_STATUSES:
        raise ConflictError(
            f"Lezione {lesson.lesson_code} non ha contenuto stabile "
            f"(content_status={lesson.content_status}).",
            code="invalid_lesson_content_status_for_pdf",
        )
    if lesson.pdf_status not in VALID_PDF_REQUEST_STATUSES:
        raise ConflictError(
            f"Export PDF già in corso per {lesson.lesson_code}: {lesson.pdf_status}",
            code="pdf_already_in_progress",
        )

    if pdf_template_id is not None:
        # Valida l'appartenenza all'org (404 se non esiste). Il
        # rendering la riprenderà più tardi tramite
        # `_resolve_pdf_template_for_lesson`.
        await _get_pdf_template_or_404(
            db,
            organization_id=course.organization_id,
            template_id=pdf_template_id,
        )
        lesson.pdf_template_id = pdf_template_id

    lesson.pdf_status = "pending"
    lesson.pdf_error = None
    lesson.pdf_progress = 0
    lesson.pdf_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.pdf.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "pdf_template_id": (str(pdf_template_id) if pdf_template_id else None),
        },
    )
    await db.commit()
    return await _refresh_course_full(db, course)


async def request_all_lessons_pdf(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    pdf_template_id: uuid.UUID | None = None,
    only_missing: bool = False,
) -> Course:
    """Marca TUTTE le lezioni esportabili (`content_status` ∈
    ready/approved e `pdf_status` ∈ empty/ready/failed) come `pending`.

    Se `only_missing=True`, esclude le lezioni con PDF già `ready`:
    filtra a `pdf_status ∈ (empty, failed)`. Utile per il pulsante
    "Genera PDF mancanti" che rigenera solo ciò che non è pronto.

    Se `pdf_template_id` è fornito, lo applica a tutte le lezioni
    eligibili (override del template scelto in passato per ogni
    lezione). Se è `None`, lascia invariato `lesson.pdf_template_id`
    (il worker fall-back al default dell'org per le lezioni che non
    hanno un template specificato).
    """
    pdf_status_filter: tuple[str, ...] = (
        ("empty", "failed") if only_missing else VALID_PDF_REQUEST_STATUSES
    )
    eligible: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if (
                lesson.content_status in EXPORTABLE_CONTENT_STATUSES
                and lesson.pdf_status in pdf_status_filter
                and not lesson.is_assessment
            ):
                eligible.append(lesson)
    if not eligible:
        raise ConflictError(
            "Nessuna lezione esportabile.",
            code="no_eligible_lessons_for_pdf",
        )

    if pdf_template_id is not None:
        await _get_pdf_template_or_404(
            db,
            organization_id=course.organization_id,
            template_id=pdf_template_id,
        )

    for lesson in eligible:
        if pdf_template_id is not None:
            lesson.pdf_template_id = pdf_template_id
        lesson.pdf_status = "pending"
        lesson.pdf_error = None
        lesson.pdf_progress = 0
        lesson.pdf_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.pdf.requested_all",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "lessons_count": len(eligible),
            "pdf_template_id": (str(pdf_template_id) if pdf_template_id else None),
        },
    )
    await db.commit()
    return await _refresh_course_full(db, course)


async def cancel_all_pdf_exports(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Annulla tutti gli export in flight (`pending`/`processing`).
    Il worker post-Playwright re-controlla lo status e scarta il
    risultato se non è più `processing`."""
    affected: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if lesson.pdf_status in ("pending", "processing"):
                lesson.pdf_status = "failed"
                lesson.pdf_error = "Export annullato"
                lesson.pdf_progress = 0
                lesson.pdf_progress_phase = None
                affected.append(lesson)

    if affected:
        await write_audit(
            db,
            action="course.lesson.pdf.cancelled",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course",
            target_id=str(course.id),
            metadata={
                "cancelled_lesson_codes": [item.lesson_code for item in affected],
            },
        )
    await db.commit()
    return await _refresh_course_full(db, course)


async def _refresh_course_full(db: AsyncSession, course: Course) -> Course:
    res = await db.execute(
        select(Course).where(Course.id == course.id).options(*_eager_full_options())
    )
    return res.scalar_one()


# ---------------------------------------------------------------------------
# In-process lock per evitare doppi worker run (riusato dal worker module)
# ---------------------------------------------------------------------------

# Lock condiviso col worker (ti permette di rendere `materialize_lesson_pdf`
# thread/coroutine-safe quando chiamata anche al di fuori del worker).
materialize_lock = asyncio.Lock()
