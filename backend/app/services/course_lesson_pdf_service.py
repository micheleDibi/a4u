"""Service di export PDF delle lezioni (§7).

Pipeline:
  content_raw (JSONB) + pdf_template (org)
        ↓ Playwright (pre-render) → mermaid SVG inline
        ↓ latex2mathml → MathML inline (math)
        ↓ markdown-it-py + Jinja2 → HTML completo
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

Asset visivi:
  - `format=mermaid` → SVG pre-renderizzato server-side (una sessione
    Playwright headless per lezione carica mermaid.esm e produce SVG;
    WeasyPrint embedda SVG nativamente)
  - `format=image_prompt|image_search_query|description` → placeholder
    testuale (in MVP non scarichiamo immagini)
  - `tables[].markdown` → pre-renderizzato a HTML
  - `equations[].latex` → convertito in MathML server-side via
    `latex2mathml` (WeasyPrint renderizza MathML nativamente)
  - `examples[].content` → markdown ricorsivo

Il template `pdf_templates` (org-scope) determina colori, font, page size,
margini, header/footer height (mm), loghi e background. Il pattern CSS
del PDF (sfondo edge-to-edge, header running, page counter) è basato
su CSS Paged Media puro — niente JavaScript, niente Chromium per il
rendering finale.
"""
from __future__ import annotations

import asyncio
import base64
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from latex2mathml.converter import convert as _latex_to_mathml
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from weasyprint import HTML as WeasyHTML

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
from app.services import remote_storage

log = get_logger("app.course_lesson_pdf.service")


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


async def load_course_full(
    db: AsyncSession, *, course_id: uuid.UUID
) -> Course | None:
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


async def _get_organization(
    db: AsyncSession, organization_id: uuid.UUID
) -> Organization | None:
    return await db.get(Organization, organization_id)


# ---------------------------------------------------------------------------
# Markdown → HTML pipeline
# ---------------------------------------------------------------------------


_LATEX_INLINE_BSPAREN_RE = re.compile(r"\\\(([\s\S]*?)\\\)")
_LATEX_DISPLAY_BSBRACK_RE = re.compile(r"\\\[([\s\S]*?)\\\]")


def _normalize_math_delimiters(md: str) -> str:
    """Mappa i delimitatori in stile LaTeX puro (`\\(..\\)`, `\\[..\\]`) verso
    `$..$` / `$$..$$` riconosciuti dal plugin dollarmath. Esclude i pattern
    che assomigliano a riferimenti asset (`\\[FIG:..\\]`)."""

    def _display_sub(m: re.Match[str]) -> str:
        inner = m.group(1)
        if re.match(r"^\s*(FIG|TAB|EQ|EX):", inner):
            return m.group(0)
        return f"$${inner}$$"

    md = _LATEX_DISPLAY_BSBRACK_RE.sub(_display_sub, md)
    md = _LATEX_INLINE_BSPAREN_RE.sub(lambda m: f"${m.group(1)}$", md)
    return md


def _convert_math_to_mathml(latex: str, *, display: str) -> str:
    """Converte LaTeX in MathML via `latex2mathml`. WeasyPrint renderizza
    MathML nativamente — quindi niente KaTeX/JS in PDF.

    `display` ∈ {"inline","block"}. In caso di parse error ritorna un
    fallback `<code>` col LaTeX grezzo, così la lezione resta leggibile
    anche con sintassi malformata."""
    src = (latex or "").strip()
    if not src:
        return ""
    try:
        # latex2mathml.convert(...) produce sempre `<math ...>...</math>`.
        # Per il display block aggiungiamo `display="block"` dopo il tag
        # apertura — l'API non lo espone come kwarg in tutte le versioni.
        mathml = _latex_to_mathml(src)
    except Exception as exc:  # noqa: BLE001 — convertitore di terze parti
        log.warning("math_convert_failed", latex=src[:120], error=str(exc))
        return f'<code class="math-error">{_html_escape_text(src)}</code>'
    if display == "block" and "<math" in mathml and 'display="block"' not in mathml:
        mathml = mathml.replace("<math ", '<math display="block" ', 1)
    return mathml


def _render_math(latex: str, *, display: str, svg_map: dict | None = None) -> str:
    """Rende una formula LaTeX per il PDF/slide.

    Se `svg_map` contiene l'SVG pre-renderizzato (MathJax via Playwright,
    `_prerender_math_for_lesson`) lo usa: WeasyPrint rende l'SVG
    correttamente, a differenza del MathML che NON supporta. In assenza
    di SVG (formula non raccolta, MathJax/CDN non disponibile) ricade su
    `_convert_math_to_mathml` (MathML → `<code>`): mai peggio di prima.
    La chiave della mappa è `(latex.strip(), display)`."""
    src = (latex or "").strip()
    if not src:
        return ""
    if svg_map:
        svg = svg_map.get((src, display))
        if svg:
            return svg
    return _convert_math_to_mathml(src, display=display)


def _build_markdown_renderer() -> MarkdownIt:
    """Crea un'istanza markdown-it configurata per le lezioni:
    GFM (tabelle), HTML inline/block, dollarmath per i blocchi math.

    I rule custom di math emettono MathML direttamente (via
    `_convert_math_to_mathml`), così WeasyPrint può renderizzare le
    formule senza dipendere da JavaScript in-page."""
    md = (
        MarkdownIt("commonmark", {"html": True, "linkify": True, "breaks": False})
        .enable(["table", "strikethrough"])
        .use(dollarmath_plugin, allow_labels=False, double_inline=True)
    )

    # I rule leggono la mappa SVG pre-renderizzata dall'`env` di
    # markdown-it (passato da `render_markdown`); il 5° parametro È l'env.
    def _render_math_inline(_self, tokens, idx, _options, env):
        svg_map = (env or {}).get("math_svg")
        inner = _render_math(tokens[idx].content, display="inline", svg_map=svg_map)
        return f'<span class="math-inline">{inner}</span>'

    def _render_math_block(_self, tokens, idx, _options, env):
        svg_map = (env or {}).get("math_svg")
        inner = _render_math(tokens[idx].content, display="block", svg_map=svg_map)
        return f'<div class="math-block">{inner}</div>'

    md.add_render_rule("math_inline", _render_math_inline)
    md.add_render_rule("math_block", _render_math_block)
    return md


_md_renderer = _build_markdown_renderer()


def render_markdown(source: str, math_svg_map: dict | None = None) -> str:
    """Pipeline markdown → HTML (con normalizzazione math).

    `math_svg_map` (opzionale): mappa `{(latex, display) → svg}` pre-
    renderizzata da MathJax; passata ai rule math via l'`env` di
    markdown-it. Se omessa, le formule ricadono su MathML."""
    if not source:
        return ""
    return _md_renderer.render(
        _normalize_math_delimiters(source), {"math_svg": math_svg_map}
    )


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


def _render_visual_asset_block(
    asset: dict[str, Any],
    *,
    mermaid_svg_map: dict[str, str] | None = None,
) -> str:
    fmt = asset.get("format", "")
    asset_id = str(asset.get("asset_id", ""))
    content = asset.get("content", "") or ""
    caption = asset.get("caption", "") or ""
    caption_html = (
        f'<figcaption>{_html_escape_text(caption)}</figcaption>' if caption else ""
    )
    if fmt == "mermaid":
        # WeasyPrint NON esegue JS — il rendering Mermaid avviene server-side
        # via Playwright in `_prerender_mermaid_for_lesson`. Qui inseriamo
        # l'SVG già renderizzato. Se per qualche motivo il rendering è
        # fallito (rete, sintassi mermaid, ...) emettiamo un fallback
        # leggibile col codice originale.
        svg = (mermaid_svg_map or {}).get(asset_id) if asset_id else None
        if svg:
            body = f'<div class="mermaid-svg">{svg}</div>'
        else:
            body = (
                f'<pre class="mermaid-fallback">{_html_escape_text(content)}</pre>'
            )
        return f'<figure class="visual"><div class="figure-body">{body}</div>{caption_html}</figure>'
    if fmt == "image":
        # Asset immagine caricato dall'utente (path relativo `lesson_assets/...`).
        # Riusiamo il resolver dei template asset: legge dal filesystem e
        # produce una data URL base64 — WeasyPrint-friendly senza dipendenze
        # di rete.
        alt = _html_escape_text(asset.get("alt_text") or "")
        data_url = _resolve_template_asset_url(content)
        if data_url:
            body = (
                f'<img class="uploaded-image" src="{data_url}" alt="{alt}" />'
            )
        else:
            body = (
                f'<div class="placeholder-image">[immagine mancante: '
                f'{_html_escape_text(content)}]</div>'
            )
        return f'<figure class="visual"><div class="figure-body">{body}</div>{caption_html}</figure>'
    if fmt in {"image_prompt", "image_search_query", "description"}:
        body = (
            f'<div class="placeholder-image">{_html_escape_text(content)}</div>'
        )
        return f'<figure class="visual"><div class="figure-body">{body}</div>{caption_html}</figure>'
    # formato sconosciuto
    body = f'<div class="placeholder-image">[{fmt}] {_html_escape_text(content)}</div>'
    return f'<figure class="visual"><div class="figure-body">{body}</div>{caption_html}</figure>'


def _render_table_block(
    table: dict[str, Any], *, math_svg_map: dict | None = None
) -> str:
    md = (table.get("markdown") or "").strip()
    caption = table.get("caption") or ""
    table_html = render_markdown(md, math_svg_map) if md else ""
    caption_html = (
        f'<figcaption>{_html_escape_text(caption)}</figcaption>' if caption else ""
    )
    return f'<figure class="table"><div class="figure-body">{table_html}</div>{caption_html}</figure>'


def _render_equation_block(
    eq: dict[str, Any],
    *,
    math_svg_map: dict | None = None,
    language: str = "it",
) -> str:
    latex = (eq.get("latex") or "").strip()
    label = (eq.get("label") or "").strip()
    explanation = (eq.get("explanation") or "").strip()
    kind = (eq.get("kind") or "formula").strip().lower()
    statement = (eq.get("statement") or "").strip()
    proof = eq.get("proof") or []

    formula_html = (
        f'<div class="math-block">'
        f'{_render_math(latex, display="block", svg_map=math_svg_map)}</div>'
        if latex
        else ""
    )

    proof_steps = [
        s
        for s in proof
        if isinstance(s, dict)
        and ((s.get("latex") or "").strip() or (s.get("text") or "").strip())
    ]
    has_proof = bool(proof_steps)

    # Caso semplice (retro-compatibile): formula "nuda" senza enunciato né
    # dimostrazione → rendering attuale (formula + caption label/explanation).
    if not statement and not has_proof:
        caption_parts = []
        if label:
            caption_parts.append(
                f'<span class="label">{_html_escape_text(label)}</span>'
            )
        if explanation:
            caption_parts.append(_html_escape_text(explanation))
        caption_html = (
            f'<figcaption>{" — ".join(caption_parts)}</figcaption>'
            if caption_parts
            else ""
        )
        return (
            f'<figure class="equation"><div class="figure-body">{formula_html}</div>'
            f"{caption_html}</figure>"
        )

    # Blocco teorema/proposizione/definizione: intestazione + enunciato +
    # formula + (eventuale) dimostrazione a passaggi.
    labels = _labels_for(language)
    kind_label = labels.get(f"kind_{kind}", labels.get("kind_theorem", "Teorema"))
    head = kind_label + (f" {_html_escape_text(label)}" if label else "")
    parts = [f'<div class="theorem-head">{head}</div>']
    if statement:
        parts.append(
            f'<div class="theorem-statement">'
            f"{render_markdown(statement, math_svg_map)}</div>"
        )
    if formula_html:
        parts.append(formula_html)
    if has_proof:
        proof_parts = [
            f'<div class="proof-head">{_html_escape_text(labels.get("proof", "Dimostrazione"))}.</div>'
        ]
        for step in proof_steps:
            slatex = (step.get("latex") or "").strip()
            stext = (step.get("text") or "").strip()
            step_html = '<div class="proof-step">'
            if stext:
                step_html += (
                    f'<div class="proof-step-text">'
                    f"{render_markdown(stext, math_svg_map)}</div>"
                )
            if slatex:
                step_html += (
                    f'<div class="math-block">'
                    f'{_render_math(slatex, display="block", svg_map=math_svg_map)}</div>'
                )
            step_html += "</div>"
            proof_parts.append(step_html)
        proof_parts.append('<div class="proof-qed">&#8718;</div>')
        parts.append(f'<div class="proof">{"".join(proof_parts)}</div>')
    if explanation:
        parts.append(f'<figcaption>{_html_escape_text(explanation)}</figcaption>')
    return (
        f'<figure class="equation theorem" data-kind="{_html_escape_text(kind)}">'
        f'{"".join(parts)}</figure>'
    )


def _render_example_block(
    example: dict[str, Any], *, math_svg_map: dict | None = None
) -> str:
    title = (example.get("title") or "").strip()
    content = (example.get("content") or "").strip()
    inner_html = render_markdown(content, math_svg_map) if content else ""
    title_html = (
        f'<div class="example-title">{_html_escape_text(title)}</div>'
        if title
        else ""
    )
    return f'<aside class="example">{title_html}<div class="example-body">{inner_html}</div></aside>'


def _build_asset_html_map(
    content: dict[str, Any],
    *,
    mermaid_svg_map: dict[str, str] | None = None,
    math_svg_map: dict | None = None,
    language: str = "it",
) -> dict[tuple[str, str], str]:
    """Pre-renderizza ogni asset una sola volta. Chiavi: (KIND, id).

    L'id nella chiave è normalizzato a minuscolo: i riferimenti `[KIND:id]`
    nel testo e l'id dichiarato dell'asset sono generati dall'AI con case
    non sempre coerente (es. asset `TAB_x` referenziato come `[TAB:tab_x]`).
    Il lookup in `_substitute_asset_refs` normalizza nello stesso modo.

    `mermaid_svg_map` è il dict {asset_id → svg_string} prodotto da
    `_prerender_mermaid_for_lesson`. Se omesso, gli asset mermaid
    vanno in fallback testuale."""
    out: dict[tuple[str, str], str] = {}
    for asset in content.get("visual_assets") or []:
        out[("FIG", str(asset.get("asset_id", "")).lower())] = (
            _render_visual_asset_block(asset, mermaid_svg_map=mermaid_svg_map)
        )
    for table in content.get("tables") or []:
        out[("TAB", str(table.get("table_id", "")).lower())] = _render_table_block(
            table, math_svg_map=math_svg_map
        )
    for eq in content.get("equations") or []:
        out[("EQ", str(eq.get("equation_id", "")).lower())] = _render_equation_block(
            eq, math_svg_map=math_svg_map, language=language
        )
    for ex in content.get("examples") or []:
        out[("EX", str(ex.get("example_id", "")).lower())] = _render_example_block(
            ex, math_svg_map=math_svg_map
        )
    return out


# ---------------------------------------------------------------------------
# Mermaid pre-rendering (Playwright headless → SVG)
# ---------------------------------------------------------------------------


# HTML mini-doc che carica mermaid.esm da CDN ed espone una funzione
# globale `__renderMermaid(id, code)` che ritorna SVG (o null se errore).
#
# `htmlLabels: false` su tutti i diagrammi: Mermaid di default usa
# `<foreignObject>` con HTML per le label dei nodi, ma WeasyPrint non
# supporta foreignObject. Forzando le label come SVG `<text>` puro,
# il diagramma si renderizza correttamente nel PDF.
# Mermaid imposta `style="max-width: <natural_px>;"` sull'SVG generato
# (con `useMaxWidth: true`). Questo IMPEDISCE all'SVG di crescere
# oltre la sua dimensione naturale (tipicamente ~300-400px), anche
# se il container del PDF è molto più largo (un foglio A4 ha ~170mm
# di content area = ~640px). Risultato: il diagramma rimane piccolo
# e le label illeggibili. Strippiamo quel `max-width:Xpx` lasciando
# tutto il resto dello stile così l'SVG riempie il container.
_MERMAID_MAX_WIDTH_RE = re.compile(r"max-width\s*:\s*[\d.]+px\s*;?", re.IGNORECASE)


def _strip_mermaid_max_width(svg: str) -> str:
    return _MERMAID_MAX_WIDTH_RE.sub("", svg)


_MERMAID_RENDERER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>body{margin:0;padding:0;}</style></head>
<body>
<script type="module">
// Mermaid 10.9.x rispetta `htmlLabels: false` ed emette SVG <text> puro
// per le label dei nodi. Mermaid 11.x usa il "neo look" che ignora
// l'opzione e produce sempre <foreignObject>+HTML — non renderizzabile
// da WeasyPrint. Pinniamo deliberatamente alla 10.9.x.
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10.9.4/dist/mermaid.esm.min.mjs';
mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'loose',
  flowchart: { htmlLabels: false, useMaxWidth: true },
  sequence: { useMaxWidth: true },
  class: { htmlLabels: false },
  state: { htmlLabels: false },
  er: { useMaxWidth: true },
});
window.__renderMermaid = async (id, code) => {
  try {
    // Pre-validate: se la parse fallisce, NON chiamiamo render(),
    // altrimenti mermaid emette nel DOM un'icona "bomba" + scritta
    // "Syntax error in text" che finirebbe nell'SVG ritornato.
    // Con `suppressErrors: true`, parse ritorna `false` invece di
    // throware e senza side-effects nel DOM.
    const ok = await mermaid.parse(code, { suppressErrors: true });
    if (!ok) return null;
    const { svg } = await mermaid.render(id, code);
    return svg;
  } catch (e) {
    return null;
  }
};
window.__mermaidReady = true;
</script>
</body></html>
"""


async def _prerender_mermaid_to_svg_batch_async(
    codes: list[str],
) -> list[str | None]:
    """Implementazione async del pre-render. NON va chiamata direttamente
    dal worker uvicorn — Playwright richiede `subprocess_exec`, che su
    Windows è supportato SOLO da `ProactorEventLoop` (non dal
    SelectorEventLoop che uvicorn può aver impostato). Wrappare via
    `_prerender_mermaid_to_svg_batch` che gira in un thread con loop
    dedicato.

    Renderizza una lista di sorgenti mermaid a SVG con UNA sola
    sessione Playwright headless (~1s startup + ~50-200ms per
    diagramma). Ritorna lista parallela; ogni elemento è la stringa
    SVG o `None` se il rendering ha fallito.
    """
    if not codes:
        return []

    from playwright.async_api import async_playwright

    results: list[str | None] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await page.set_content(
                _MERMAID_RENDERER_HTML, wait_until="domcontentloaded"
            )
            try:
                await page.wait_for_function(
                    "window.__mermaidReady === true", timeout=15_000
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("mermaid_renderer_setup_failed", error=str(exc))
                # Non possiamo renderizzare nulla → tutti None.
                return [None] * len(codes)

            for i, code in enumerate(codes):
                if not (code or "").strip():
                    results.append(None)
                    continue
                try:
                    svg = await page.evaluate(
                        "([id, code]) => window.__renderMermaid(id, code)",
                        [f"mmd-{i}", code],
                    )
                    if isinstance(svg, str) and svg.strip():
                        results.append(_strip_mermaid_max_width(svg))
                    else:
                        log.warning(
                            "mermaid_render_returned_empty",
                            preview=code[:80],
                        )
                        results.append(None)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "mermaid_render_failed",
                        error=str(exc),
                        preview=code[:80],
                    )
                    results.append(None)
        finally:
            await browser.close()
    return results


def _prerender_mermaid_to_svg_batch_sync(
    codes: list[str],
) -> list[str | None]:
    """Sync wrapper: crea un loop asyncio NUOVO e dedicato (su Windows
    forza `ProactorEventLoop`, l'unico che supporta `subprocess_exec`
    necessario al transport di Playwright). Va chiamato da un thread
    diverso dal main (via `asyncio.to_thread`) per non interferire col
    loop di uvicorn."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_prerender_mermaid_to_svg_batch_async(codes))
    finally:
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass


async def _prerender_mermaid_to_svg_batch(
    codes: list[str],
) -> list[str | None]:
    """Wrapper async: esegue il pre-render Playwright in un thread pool.
    Il thread crea il proprio loop (ProactorEventLoop su Windows) così
    indipendente dal loop scelto da uvicorn. Stessa firma della vecchia
    versione async — caller non cambia."""
    if not codes:
        return []
    return await asyncio.to_thread(_prerender_mermaid_to_svg_batch_sync, codes)


# Righe spurie a volte emesse dall'AI nel codice Mermaid: fence markdown
# residuo (```/```mermaid) o nodi-segnaposto isolati come `mermaid` /
# `all` / `all:`. Passano mermaid.parse ma compaiono come box anomali.
# Rimosse solo quando una riga è ESATTAMENTE uno di questi token (non
# tocchiamo archi/nodi reali tipo `A --> all` o `all[Etichetta]`).
# Mirror del sanitizer frontend in MermaidDiagram.tsx.
_MERMAID_JUNK_LINE_RE = re.compile(r"^(?:```.*|mermaid|all)\s*:?\s*$", re.IGNORECASE)


def _sanitize_mermaid_code(code: str) -> str:
    if not code:
        return code
    lines = [
        ln for ln in code.split("\n") if not _MERMAID_JUNK_LINE_RE.match(ln.strip())
    ]
    return "\n".join(lines).strip()


async def _prerender_mermaid_for_lesson(
    content: dict[str, Any],
) -> dict[str, str]:
    """Estrae tutti i mermaid dalla lezione (se presenti) e li
    pre-renderizza in batch. Ritorna {asset_id → svg}; chiavi assenti
    indicano rendering fallito (gestito dal fallback testuale)."""
    pairs: list[tuple[str, str]] = []
    for asset in content.get("visual_assets") or []:
        if asset.get("format") != "mermaid":
            continue
        asset_id = str(asset.get("asset_id", ""))
        code = _sanitize_mermaid_code(asset.get("content") or "")
        if asset_id and code.strip():
            pairs.append((asset_id, code))

    if not pairs:
        return {}

    svgs = await _prerender_mermaid_to_svg_batch([code for _, code in pairs])
    out: dict[str, str] = {}
    for (asset_id, _), svg in zip(pairs, svgs, strict=True):
        if svg:
            out[asset_id] = svg
    return out


# ---------------------------------------------------------------------------
# Pre-render LaTeX → SVG (MathJax via Playwright)
#
# WeasyPrint NON renderizza MathML (stampa solo il contenuto testuale,
# perdendo pedici/apici/frazioni). Pre-renderizziamo quindi ogni formula in
# SVG autonomo via MathJax in Playwright — stesso pattern dei diagrammi
# Mermaid — ed embeddiamo l'SVG, che WeasyPrint rende correttamente.
# ---------------------------------------------------------------------------

_MATHJAX_RENDERER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>body{margin:0;padding:0;}</style></head>
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
<script src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"></script>
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


async def _prerender_math_to_svg_batch_async(
    items: list[tuple[str, str]],
) -> list[str | None]:
    """Renderizza una lista di `(latex, display)` a SVG con UNA sessione
    Playwright headless (MathJax tex-svg). Ritorna lista parallela: SVG o
    `None` se il rendering fallisce. Stesso pattern di
    `_prerender_mermaid_to_svg_batch_async` (va wrappata via il `_sync`/
    `_batch` per il ProactorEventLoop su Windows)."""
    if not items:
        return []

    from playwright.async_api import async_playwright

    results: list[str | None] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await page.set_content(
                _MATHJAX_RENDERER_HTML, wait_until="domcontentloaded"
            )
            try:
                # MathJax tex-svg.js è ~1MB: timeout più ampio del Mermaid.
                await page.wait_for_function(
                    "window.__mathReady === true", timeout=20_000
                )
            except Exception as exc:  # noqa: BLE001 — CDN irraggiungibile
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
                    results.append(
                        svg if (isinstance(svg, str) and svg.strip()) else None
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "math_render_failed", error=str(exc), preview=latex[:80]
                    )
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
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass


async def _prerender_math_to_svg_batch(
    items: list[tuple[str, str]],
) -> list[str | None]:
    """Esegue il pre-render Playwright in un thread pool (loop dedicato)."""
    if not items:
        return []
    return await asyncio.to_thread(_prerender_math_to_svg_batch_sync, items)


def _collect_math_from_content(
    content: dict[str, Any],
) -> list[tuple[str, str]]:
    """Raccoglie tutte le formule `(latex, display)` di una lezione: le
    equazioni dedicate (`equations[].latex`, block) e il math inline/block
    `$..$`/`$$..$$` nei campi testo (intro, sezioni, summary, celle tabella,
    esempi). Riusa `_find_math_spans` (estrazione condivisa con la
    validazione asset). Dedup per `(latex.strip(), display)`."""
    # Import lazy: evita di tirare openai_asset_fix_service all'import.
    from app.services.asset_validation_service import _find_math_spans

    seen: set[tuple[str, str]] = set()
    items: list[tuple[str, str]] = []

    def _add(latex: str, display: str) -> None:
        key = ((latex or "").strip(), display)
        if key[0] and key not in seen:
            seen.add(key)
            items.append(key)

    for eq in content.get("equations") or []:
        if isinstance(eq, dict):
            _add(eq.get("latex") or "", "block")
            # Passaggi della dimostrazione (LaTeX block).
            for step in eq.get("proof") or []:
                if isinstance(step, dict):
                    _add(step.get("latex") or "", "block")

    texts: list[str] = [
        content.get("introduction") or "",
        content.get("summary") or "",
    ]
    for s in content.get("sections") or []:
        if isinstance(s, dict):
            texts.append(s.get("content") or "")
    for t in content.get("tables") or []:
        if isinstance(t, dict):
            texts.append(t.get("markdown") or "")
    for ex in content.get("examples") or []:
        if isinstance(ex, dict):
            texts.append(ex.get("content") or "")
    # Enunciato e testo dei passaggi: math inline `$..$`.
    for eq in content.get("equations") or []:
        if isinstance(eq, dict):
            texts.append(eq.get("statement") or "")
            for step in eq.get("proof") or []:
                if isinstance(step, dict):
                    texts.append(step.get("text") or "")

    for text in texts:
        if not text:
            continue
        # Normalizza `\(..\)`/`\[..\]` → `$..$`/`$$..$$` come fa il renderer,
        # così le chiavi raccolte combaciano con i token dollarmath.
        for sp in _find_math_spans(_normalize_math_delimiters(text)):
            _add(sp.inner, "block" if sp.display else "inline")

    return items


async def _prerender_math_for_lesson(
    content: dict[str, Any],
) -> dict[tuple[str, str], str]:
    """Estrae tutte le formule e le pre-renderizza in batch. Ritorna
    `{(latex, display) → svg}`; le chiavi assenti ricadono su MathML."""
    items = _collect_math_from_content(content)
    if not items:
        return {}
    svgs = await _prerender_math_to_svg_batch(items)
    out: dict[tuple[str, str], str] = {}
    for (latex, display), svg in zip(items, svgs, strict=True):
        if svg:
            out[(latex, display)] = svg
    return out


def _substitute_asset_refs(
    md_source: str, asset_html_map: dict[tuple[str, str], str]
) -> str:
    """Sostituisce ogni `[KIND:id]` con il blocco HTML pre-renderizzato.
    Il blocco è inserito su righe proprie (con righe vuote prima/dopo)
    in modo che markdown-it lo riconosca come HTML block-level."""

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
        return f"\n\n{html}\n\n"

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
        data = remote_storage.get_storage().download_bytes(
            remote_storage.uploads_key(raw)
        )
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


# Altezza fisica del foglio in cm per ciascuna page-size supportata.
# Usata da `_compute_template_margins_cm` per derivare l'altezza utile
# del content-area (paper - top - bottom margin) e quindi il
# `max-height` di figure mermaid alte (TD flowchart con molti nodi)
# che altrimenti vengono tagliate dal page-break.
_PAGE_HEIGHTS_CM: dict[str, float] = {
    "A4": 29.7,
    "A3": 42.0,
    "Letter": 27.94,
    "letter": 27.94,
    "LETTER": 27.94,
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
    breathing room). Le figure mermaid usano questo valore come
    `max-height` per scalarsi automaticamente entro la pagina invece
    di farsi tagliare dal page-break.
    """
    margin_mm = max(5, int(tpl_dict.get("margin_mm", 20)))
    header_h_mm = int(tpl_dict.get("header_height_mm", 0))
    footer_h_mm = int(tpl_dict.get("footer_height_mm", 0))

    has_running_header = bool(
        tpl_dict.get("logo_left_url") or tpl_dict.get("logo_right_url")
    )
    top_mm = (
        max(margin_mm, header_h_mm + 5) if has_running_header else margin_mm
    )
    # Footer riservato sempre per page counter — anche se template non ha
    # footer_height_mm > 0, lasciamo almeno `margin_mm` di spazio.
    bottom_mm = max(margin_mm, footer_h_mm + 5) if footer_h_mm > 0 else margin_mm

    paper_h_cm = _PAGE_HEIGHTS_CM.get(tpl_dict.get("page_size", "A4"), 29.7)
    content_h_cm = paper_h_cm - (top_mm / 10.0) - (bottom_mm / 10.0)
    max_figure_height_cm = max(5.0, round(content_h_cm - 1.5, 2))

    return {
        "margin_top_cm": round(top_mm / 10.0, 3),
        "margin_side_cm": round(margin_mm / 10.0, 3),
        "margin_bottom_cm": round(bottom_mm / 10.0, 3),
        "max_figure_height_cm": max_figure_height_cm,
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
    return (
        f"{labels['module']} {int(m.group(1))} - "
        f"{labels['lesson']} {int(m.group(2))}"
    )


def render_lesson_html(
    *,
    course: Course,
    lesson: CourseLesson,
    organization: Organization | None,
    pdf_template: PdfTemplate | None,
    public_base_url: str | None = None,
    mermaid_svg_map: dict[str, str] | None = None,
    math_svg_map: dict | None = None,
    teacher_name: str | None = None,
) -> str:
    """Pure-function: produce l'HTML completo della lezione, pronto per
    WeasyPrint.

    `mermaid_svg_map` è opzionale: se omesso e ci sono asset mermaid,
    il template emette un fallback testuale invece dell'SVG. Nel
    flusso di produzione (`materialize_lesson_pdf`) viene riempito
    via `_prerender_mermaid_for_lesson`. Indipendente dal DB e dal
    worker — testabile in isolamento (purché i mermaid siano stati
    pre-renderizzati a monte se richiesti).
    """
    raw = lesson.content_raw or {}
    if not raw:
        raise ConflictError(
            f"Lezione {lesson.lesson_code} senza content_raw — impossibile esportare.",
            code="lesson_content_missing",
        )

    language = (course.language_code or "it").lower()
    labels = _labels_for(language)

    asset_map = _build_asset_html_map(
        raw,
        mermaid_svg_map=mermaid_svg_map,
        math_svg_map=math_svg_map,
        language=language,
    )
    body_md = _build_lesson_body_markdown(raw)
    body_md = _replace_summary_heading(body_md, labels["summary"])
    body_md = _substitute_asset_refs(body_md, asset_map)
    body_html = render_markdown(body_md, math_svg_map)

    tpl_dict: dict[str, Any]
    if pdf_template is not None:
        tpl_dict = _format_pdf_template_for_render(
            pdf_template, public_base_url=public_base_url
        )
    else:
        tpl_dict = _default_template_dict(language=language)

    margins_cm = _compute_template_margins_cm(tpl_dict)

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
        key_takeaways=raw.get("key_takeaways") or [],
        references=raw.get("references") or [],
    )
    return html


# ---------------------------------------------------------------------------
# WeasyPrint — HTML → PDF bytes
# ---------------------------------------------------------------------------


def _render_with_weasyprint_sync(
    html: str, *, base_url: str | None = None
) -> bytes:
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
    return WeasyHTML(string=html, base_url=base_url).write_pdf()


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
      2. Pre-renderizza i diagrammi mermaid via Playwright headless
         (una sola sessione per lezione → SVG inline).
      3. Costruisce l'HTML completo della lezione (markdown → HTML +
         math → MathML + asset map con SVG mermaid).
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

    # Pre-render mermaid: una singola sessione Playwright produce gli SVG
    # di tutti i diagrammi della lezione in batch. Se la lezione non ha
    # mermaid, non viene avviata nessuna istanza di Playwright.
    raw_content = lesson.content_raw or {}
    mermaid_svg_map = await _prerender_mermaid_for_lesson(raw_content)
    # Pre-render LaTeX → SVG (MathJax): WeasyPrint non rende il MathML.
    math_svg_map = await _prerender_math_for_lesson(raw_content)

    html = await asyncio.to_thread(
        render_lesson_html,
        course=course,
        lesson=lesson,
        organization=organization,
        pdf_template=pdf_template,
        public_base_url=public_base_url,
        mermaid_svg_map=mermaid_svg_map,
        math_svg_map=math_svg_map,
        teacher_name=teacher_name,
    )

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
            f"Export PDF già in corso per {lesson.lesson_code}: "
            f"{lesson.pdf_status}",
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
            "pdf_template_id": (
                str(pdf_template_id) if pdf_template_id else None
            ),
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
            "pdf_template_id": (
                str(pdf_template_id) if pdf_template_id else None
            ),
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
                "cancelled_lesson_codes": [l.lesson_code for l in affected],
            },
        )
    await db.commit()
    return await _refresh_course_full(db, course)


async def _refresh_course_full(db: AsyncSession, course: Course) -> Course:
    res = await db.execute(
        select(Course)
        .where(Course.id == course.id)
        .options(*_eager_full_options())
    )
    return res.scalar_one()


# ---------------------------------------------------------------------------
# In-process lock per evitare doppi worker run (riusato dal worker module)
# ---------------------------------------------------------------------------

# Lock condiviso col worker (ti permette di rendere `materialize_lesson_pdf`
# thread/coroutine-safe quando chiamata anche al di fuori del worker).
materialize_lock = asyncio.Lock()
