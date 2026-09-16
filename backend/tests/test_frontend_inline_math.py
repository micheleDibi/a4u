"""Math inline nei campi dei blocchi del frontend (WP2, D9): didascalie di
figura e di tabella, label delle equazioni e titolo degli esempi rendono
il SOLO math (`$..$`, `$$..$$`, `\\(..\\)`, `\\[..\\]`) con KaTeX, come
`render_markdown_inline` del PDF, e lasciano letterale tutto il resto.

Tre prove:
- strutturale: i sei siti (`FigureFrame`, `TableBlock`, i due rami di
  `EquationBlock`, `ExampleBlock`, la vista slide) passano dal componente
  `InlineMath`, l'etichetta resta fuori, nessun `dangerouslySetInnerHTML`;
- parità di grammatica: `lib/inlineMath.ts` eseguito con Node
  (`--experimental-strip-types`, come `test_figure_numbering.py`) produce,
  su un corpus e sui casi inline di `fixtures/math_grammar_cases.json`,
  gli stessi segmenti dei token dell'istanza zero del PDF;
- DOM reale: la `FigureFrame` vera, compilata con l'esbuild del frontend e
  montata in Chromium, rende la didascalia senza math con il markup
  storico byte-identico e quella con math con `span.katex` in linea,
  senza `<p>` né `katex-display`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services import course_lesson_pdf_service as pdf

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_SRC = _FRONTEND / "src"
_LIB = _SRC / "lib" / "inlineMath.ts"
_COMPONENT = _SRC / "components" / "shared" / "InlineMath.tsx"
_FIGURE_FRAME = _SRC / "components" / "shared" / "FigureFrame.tsx"
_RENDERER = _SRC / "components" / "shared" / "MarkdownRenderer.tsx"
_SLIDES_VIEW = _SRC / "pages" / "org" / "courses" / "components" / "LessonSlidesView.tsx"
_FIXTURE = Path(__file__).parent / "fixtures" / "math_grammar_cases.json"


def _read(path: Path) -> str:
    assert path.is_file(), f"sorgente frontend assente: {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. i siti e il componente
# ---------------------------------------------------------------------------


def test_inline_fields_pass_through_inline_math_and_labels_stay_outside() -> None:
    """Ogni campo inline è reso da `<InlineMath text=…>` con lo spazio di
    separazione dentro il testo (il nodo di testo resta ` didascalia`,
    come prima); l'etichetta («Figura N.», «Tabella N.», …) e l'`aria-label`
    restano fuori dal renderer."""
    frame = _read(_FIGURE_FRAME)
    assert re.search(r"const tail = text \? ` \$\{text\}\$\{stop\}` : \"\";", frame)
    assert "<InlineMath text={tail} />" in frame
    assert '<span className="figure-label font-semibold">{label}</span>' in frame
    assert "aria-label={altText || text || label}" in frame
    assert '{extra ? ` ${extra}` : ""}' in frame, "la coda calcolata resta testo"

    renderer = _read(_RENDERER)
    assert "<InlineMath text={` ${table.caption}`} />" in renderer
    assert renderer.count("<InlineMath text={` ${equation.label}`} />") == 2, (
        "la label passa dal renderer in ENTRAMBI i rami (EQ e THM)"
    )
    assert "<InlineMath text={` ${example.title}`} />" in renderer
    for leftover in ("` ${table.caption}` : ", "` ${equation.label}` : ", "` ${example.title}` : "):
        assert leftover not in renderer, leftover

    slides = _read(_SLIDES_VIEW)
    assert "<InlineMath text={` ${resolved.payload.caption}`} />" in slides
    assert "<InlineMath text={` ${ex.title}`} />" in slides
    assert "` ${resolved.payload.caption}` : " not in slides
    assert "` ${ex.title}` : " not in slides


def test_inline_math_component_uses_katex_render_without_injected_html() -> None:
    """`InlineMath` monta KaTeX tramite ref (`katex.render`, come
    `FunctionEditor`), mai da stringa HTML; opzioni pinnate: nessuna
    eccezione, nessun comando `\\htmlClass`/`\\url` fidato, sempre text
    style (un blocco spezzerebbe la didascalia)."""
    component = _read(_COMPONENT)
    assert "dangerouslySetInnerHTML" not in component
    assert "katex.render(" in component
    for option in ("throwOnError: false", "trust: false", "displayMode: false"):
        assert option in component, option
    assert "splitInlineMath" in component
    assert 'from "@/lib/inlineMath"' in component
    lib = _read(_LIB)
    assert re.search(r"^import ", lib, re.M) is None, (
        "lib/inlineMath.ts non ha import: Node la carica da sola"
    )
    assert "export function splitInlineMath(" in lib


# ---------------------------------------------------------------------------
# 2. parità della grammatica con l'istanza zero del PDF
# ---------------------------------------------------------------------------

# Campi inline reali e i casi limite della grammatica (B3 §3): importi in
# tutte le forme, decimali italiani, cifra adiacente, escape, `\[..\]` di
# asset e citazioni, spazi interni, doppio dollaro, markdown ricco che deve
# restare letterale.
_PARITY_CORPUS = [
    "Angolo $30^\\circ$ tra le rette.",
    "Il costo e $50 e sale a $70 al mese.",
    "Prezzi: $50/$70, $5-$10, 5$/10$, 5$, 10$, 5$,10$.",
    "US$50 e US$70; 50$-70$ euro; 5$-10$; $2$3; $50\u2192$70.",
    "Vale $15{,}9$ e $0{,}866$ e $2^{10}$ e $10^{-3}$ e $1,5$.",
    "la base 2$^{10}$ e 2$\\pi$",
    "$0$, $-1$, $3/4$, $2+2$, $5$, $50$, $50-70$, $1$-$2$, $x$2, $a$1",
    "costa \\$5 e \\\\(x\\\\) letterali",
    "Sia $$E = mc^2$$ la relazione e \\[E=mc^2\\] qui.",
    "Vedi \\[FIG:x\\], \\[1\\], \\[2, 3\\], \\[12\u201314\\] e \\(\\beta\\).",
    "$ x_0 $ resta prosa, $x_0$ no",
    'Retta "y" con <b>x</b> & *enfasi* [link](http://a.b) `code`',
    "Figura 7. Angolo $a\n b$ su due righe",
    "$$ x $$ e $$x$$ e $$$$",
    "$$a$ b$$",
    "\\[ \\] vuoto e \\( \\) vuoto e $ $",
    "Testo senza math",
    "$\\(x\\)$ malformato",
    "fine con dollaro $",
    "\\[a\\]\\(b\\)$c$$$d$$",
]

_NODE_RUNNER = """
import * as im from {module!r};
import {{ readFileSync }} from "node:fs";
const cases = JSON.parse(readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(cases.map((c) => im.splitInlineMath(c))));
"""


def _pdf_segments(text: str) -> list[list]:
    """Token dell'istanza zero (`text`, `math_inline`, `math_inline_double`)
    dopo la guardia currency e `text_join`, nella forma dei segmenti FE."""
    out: list[list] = []
    for tok in pdf._md_inline_renderer.parseInline(text, {})[0].children or []:
        if tok.type == "text":
            out.append(["text", tok.content])
        elif tok.type == "math_inline":
            out.append(["math", tok.content, False])
        elif tok.type == "math_inline_double":
            out.append(["math", tok.content, True])
        else:  # pragma: no cover - nessun'altra rule è attiva nel preset zero
            out.append([tok.type, tok.content])
    return out


def _frontend_segments(cases: list[str]) -> list[list[list]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node non disponibile: parità frontend non eseguibile")
    proc = subprocess.run(
        [
            node,
            "--no-warnings",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            _NODE_RUNNER.format(module=str(_LIB)),
        ],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0 and "strip-types" in proc.stderr:
        pytest.skip(f"node senza --experimental-strip-types: {proc.stderr.strip()[:200]}")
    assert proc.returncode == 0, proc.stderr
    return [
        [
            ["text", s["text"]] if s["kind"] == "text" else ["math", s["latex"], s["display"]]
            for s in segments
        ]
        for segments in json.loads(proc.stdout)
    ]


def test_frontend_splitter_matches_the_pdf_inline_grammar() -> None:
    """Per ogni caso, i segmenti dello splitter FE coincidono con i token
    dell'istanza zero del PDF: stesso testo letterale (importi, `$ x $`,
    `\\[FIG:x\\]`, `\\$5`, markdown ricco) e stesse formule con lo stesso
    contenuto e la stessa modalità."""
    fixture = [c["markdown"] for c in json.loads(_FIXTURE.read_text()) if c["mode"] == "inline"]
    assert fixture, "nessun caso inline nella fixture"
    cases = _PARITY_CORPUS + fixture
    frontend = _frontend_segments(cases)
    divergent = [
        (text, fe, be)
        for text, fe in zip(cases, frontend, strict=True)
        if fe != (be := _pdf_segments(text))
    ]
    assert not divergent, divergent
    # Il corpus esercita davvero i tre esiti: prosa, math inline, math block.
    kinds = {
        seg[0] + ("_block" if seg[0] == "math" and seg[2] else "") for fe in frontend for seg in fe
    }
    assert kinds == {"text", "math", "math_block"}, kinds
    literal = frontend[cases.index("Il costo e $50 e sale a $70 al mese.")]
    assert literal == [["text", "Il costo e $50 e sale a $70 al mese."]]


def test_frontend_splitter_returns_the_text_unchanged_without_math() -> None:
    """Senza delimitatori riconosciuti il risultato è il solo segmento di
    testo, identico all'ingresso: il nodo di testo della figcaption non
    cambia rispetto a prima."""
    texts = [
        " Angolo retto.",
        ' Retta "y" & <b>x</b> *enfasi* `code`',
        " Il costo e $50 e sale a $70 al mese.",
        " Vedi \\[FIG:x\\] e \\[1\\] e \\$5.",
        " $ x $ con spazi",
    ]
    for text, segments in zip(texts, _frontend_segments(texts), strict=True):
        assert segments == [["text", text]], (text, segments)


# ---------------------------------------------------------------------------
# 3. DOM reale in Chromium
# ---------------------------------------------------------------------------

pytest.importorskip("playwright.sync_api")

# Stub di react-i18next: le sole chiavi della cornice, in italiano.
_I18N_STUB = """
export function useTranslation() {
  return {
    t: (key, opts) => {
      if (key === "courses.figures.label") return `Figura ${String(opts?.n)}.`;
      if (key === "courses.figures.labelUnnumbered") return "Figura.";
      return key;
    },
  };
}
"""

# Monta la FigureFrame VERA (react-i18next stubbata) e ritorna il markup
# della figcaption; `flushSync` esegue anche gli effetti di layout, quindi
# KaTeX ha già scritto nel DOM quando si legge.
_HARNESS = """
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { FigureFrame } from "@/components/shared/FigureFrame";

export function mountFigure(el, caption, number, extraCaption) {
  flushSync(() => {
    createRoot(el).render(
      <FigureFrame assetId="fig_a" format="mermaid" caption={caption}
                   number={number} extraCaption={extraCaption}>
        <div>body</div>
      </FigureFrame>,
    );
  });
  const fc = el.querySelector("figcaption");
  return {
    figcaption: fc ? fc.innerHTML : null,
    katex: el.querySelectorAll(".katex").length,
    ariaLabel: el.querySelector("figure").getAttribute("aria-label"),
  };
}
"""


def _bundle_figure_frame(tmp: Path) -> str:
    """Harness + FigureFrame compilati in un IIFE con l'esbuild del
    frontend: la prova esegue il componente VERO, non una copia."""
    esbuild = _FRONTEND / "node_modules" / ".bin" / "esbuild"
    if not esbuild.is_file() or shutil.which("node") is None:
        pytest.skip("esbuild del frontend o node non disponibili")
    stub = tmp / "react-i18next-stub.js"
    stub.write_text(_I18N_STUB, encoding="utf-8")
    harness = tmp / "harness.tsx"
    harness.write_text(_HARNESS, encoding="utf-8")
    out = tmp / "bundle.js"
    proc = subprocess.run(
        [
            str(esbuild),
            str(harness),
            "--bundle",
            "--format=iife",
            "--global-name=A4U_INLINE_MATH",
            "--platform=browser",
            "--target=es2020",
            "--jsx=automatic",
            "--loader:.css=empty",
            f"--alias:react-i18next={stub}",
            f"--tsconfig={_FRONTEND / 'tsconfig.app.json'}",
            f"--outfile={out}",
        ],
        cwd=str(_FRONTEND),
        env={**os.environ, "NODE_PATH": str(_FRONTEND / "node_modules")},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"esbuild non riuscito: {proc.stderr.strip()[:300]}")
    return out.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def figure_probe():
    """Chromium con il bundle della cornice; ritorna una callable
    `(caption, number, extra) -> {figcaption, katex, ariaLabel}`."""
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        bundle = _bundle_figure_frame(Path(tmp))
        browser = p.chromium.launch()
        page = browser.new_page()
        # Doctype obbligatorio: KaTeX rifiuta il quirks mode (index.html lo ha).
        page.set_content("<!DOCTYPE html><html><body></body></html>")
        page.add_script_tag(content=bundle)

        def probe(caption: str, number: int | None = 1, extra: str | None = None) -> dict:
            return page.evaluate(
                """([caption, number, extra]) => {
                    const el = document.createElement("div");
                    document.body.appendChild(el);
                    return window.A4U_INLINE_MATH.mountFigure(
                        el, caption, number ?? undefined, extra ?? undefined);
                }""",
                [caption, number, extra],
            )

        yield probe
        browser.close()


_LABEL = '<span class="figure-label font-semibold">Figura 1.</span>'


def test_caption_without_math_keeps_the_historical_markup(figure_probe) -> None:
    """Golden catturato prima di WP2 sulla stessa cornice: nessun elemento
    aggiunto, stesso nodo di testo, `aria-label` in chiaro."""
    res = figure_probe("Angolo retto.")
    assert res["figcaption"] == f"{_LABEL} Angolo retto."
    assert res["katex"] == 0
    assert res["ariaLabel"] == "Angolo retto."
    literal = figure_probe("Il costo e $50 e sale a $70 al mese.")
    assert literal["figcaption"] == f"{_LABEL} Il costo e $50 e sale a $70 al mese."
    assert literal["katex"] == 0


def test_caption_math_is_rendered_inline_by_katex(figure_probe) -> None:
    """`$30^\\circ$` diventa `span.math-inline > span.katex` fra i due
    frammenti di testo; niente `<p>`, niente display mode; l'etichetta e
    l'`aria-label` (testo puro) non cambiano."""
    res = figure_probe("Angolo $30^\\circ$ tra le rette.")
    html = res["figcaption"]
    assert res["katex"] == 1
    assert html.startswith(f'{_LABEL} Angolo <span class="math-inline"><span class="katex">')
    assert html.endswith("</span> tra le rette.")
    assert 'annotation encoding="application/x-tex">30^\\circ</annotation>' in html
    assert "<p" not in html and "katex-display" not in html
    assert res["ariaLabel"] == "Angolo $30^\\circ$ tra le rette."


def test_caption_keeps_asset_tags_literal_and_renders_backslash_delimiters(
    figure_probe,
) -> None:
    """`\\[FIG:x\\]` resta letterale (non è math né rimando), `\\(\\beta\\)`
    è reso; le virgolette restano testo."""
    res = figure_probe('Vedi \\[FIG:x\\] e "y" con \\(\\beta\\).')
    html = res["figcaption"]
    assert res["katex"] == 1
    assert html.startswith(f'{_LABEL} Vedi \\[FIG:x\\] e "y" con <span class="math-inline">')
    assert html.endswith("</span>.")


def test_computed_tail_stays_text_after_the_math(figure_probe) -> None:
    """Figure `function`: il punto di chiusura e la coda calcolata seguono
    la formula come testo (stessa regola del partial del PDF)."""
    res = figure_probe("Funzione $f(x)$", 1, "Zeri in x = 1.")
    html = res["figcaption"]
    assert res["katex"] == 1
    assert html.startswith(f'{_LABEL} Funzione <span class="math-inline">')
    assert html.endswith("</span>. Zeri in x = 1.")
