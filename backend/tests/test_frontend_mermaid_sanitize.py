"""Sanificazione client dell'SVG Mermaid (SEC-1, giro 5).

Il gate statico del PATCH e la scansione dell'SVG proteggono il documento
consegnato; nell'editor e nella vista lezione, invece, il diagramma è reso
da Mermaid NEL BROWSER DI CHI GUARDA, senza passare dal backend. Fino al
giro 4 quel markup entrava nel documento vivo così com'era
(`dangerouslySetInnerHTML` in `MermaidDiagram.tsx`), quindi un
`<image href="http://…">` uscito da una shape che il gate non riconosce
faceva partire la richiesta dal browser del lettore.

Due livelli di verifica:

1. lettura dell'albero `frontend/` (come `test_frontend_figure_i18n.py`):
   la funzione esiste, è chiamata prima dell'inserimento nel DOM, e non
   rimuove né `<style>` (il tema Mermaid) né `<foreignObject>`;
2. prova REALE in Chromium: il modulo `lib/figureFormats.ts` è compilato
   con l'esbuild del frontend, Mermaid 11 rende davvero i sorgenti maligni,
   e si misura quali richieste il browser tenta prima e dopo la
   sanificazione. Serve Playwright con Chromium, Node e cdn.jsdelivr.net:
   senza, il test salta con motivo esplicito.
"""

from __future__ import annotations

import base64
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services import figure_theme as theme
from app.services.mermaid_prerender import PRERENDER_ALLOWED_PREFIX, build_mermaid_renderer_html

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_FIGURE_FORMATS = _FRONTEND / "src" / "lib" / "figureFormats.ts"
_MERMAID_DIAGRAM = _FRONTEND / "src" / "components" / "shared" / "MermaidDiagram.tsx"

_HOST = "esempio.non.esiste"
_URL = f"http://{_HOST}"
# PNG 1×1 con cui la prova serve in loco la richiesta della controprova:
# nessun byte esce dalla macchina e Mermaid può misurare l'immagine.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# Sorgenti che il gate statico del giro 5 rifiuta, ma che l'editor rende
# comunque mentre il docente li scrive: è il residuo che solo la
# sanificazione client chiude. Il primo produce un `<image href>` (una GET
# vera), gli altri due un `<a xlink:href>` verso l'host scelto dall'autore.
# `__N__` è sostituito da un contatore a ogni prova: senza un URL nuovo
# Chromium servirebbe la seconda richiesta dalla cache e il registro
# resterebbe vuoto anche quando il nodo la fa davvero.
_VECTORS: dict[str, str] = {
    "shape_img": f'flowchart LR\n  A@{{ img: "{_URL}/i__N__.png", w: 60, h: 60 }}\n  A --> B',
    "flowchart_click": f'flowchart LR\n  A --> B\n  click A href "{_URL}/c__N__.png"',
    "state_click": f'stateDiagram-v2\n  [*] --> A\n  click A href "{_URL}/s__N__.png"',
}


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"sorgente frontend assente: {path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. lettura dell'albero frontend
# ---------------------------------------------------------------------------


def test_the_mermaid_component_sanitizes_before_touching_the_document() -> None:
    """L'SVG reso non arriva mai a `dangerouslySetInnerHTML` senza passare
    da `sanitizeMermaidSvg`."""
    src = _read(_MERMAID_DIAGRAM)
    assert "sanitizeMermaidSvg" in src, "il componente non sanifica l'SVG di Mermaid"
    # Ogni resa (originale e variante verticale della catena, D15) passa da
    # `renderCleanSvg`, che sanifica prima di togliere il `max-width`.
    assert re.search(r"const\s+safe\s*=\s*sanitizeMermaidSvg\(await renderMermaidSvg\(", src), src[
        :400
    ]
    assert src.count("renderMermaidSvg(") == 1, "resa Mermaid fuori da renderCleanSvg"
    assert "renderCleanSvg(mermaid, id, cleanCode)" in src
    assert "flippedCode,\n          ).catch(() => null)" in src, "la variante non è isolata"
    # Il markup grezzo di `mermaid.render` non deve più finire nello stato.
    assert "rendered.replace(" not in src
    assert "html: chosen" in src


def test_the_mermaid_sanitizer_keeps_the_theme_and_removes_what_fetches() -> None:
    """La lista degli elementi rimossi è quella che carica o esegue: togliere
    `<style>` smonterebbe il tema, togliere `<foreignObject>` cancellerebbe
    una label (con `htmlLabels:false` non ne esistono)."""
    src = _read(_FIGURE_FORMATS)
    m = re.search(r"const MERMAID_ACTIVE_SELECTOR = \[(.*?)\]\.join", src, re.DOTALL)
    assert m, "MERMAID_ACTIVE_SELECTOR assente da figureFormats.ts"
    removed = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert {"script", "iframe", "image", "set", "animate", "handler"} <= removed
    assert "style" not in removed and "foreignObject" not in removed
    assert "export function sanitizeMermaidSvg(" in src
    assert 'parseFromString(svg || "", "text/html")' in src


# ---------------------------------------------------------------------------
# 2. prova reale in Chromium
# ---------------------------------------------------------------------------

pytest.importorskip("playwright.sync_api")


def _bundle_figure_formats() -> str:
    """`lib/figureFormats.ts` compilato in un IIFE con l'esbuild del
    frontend: la prova esegue la funzione VERA, non una copia."""
    esbuild = _FRONTEND / "node_modules" / ".bin" / "esbuild"
    if not esbuild.is_file() or shutil.which("node") is None:
        pytest.skip("esbuild del frontend o node non disponibili")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ff.js"
        proc = subprocess.run(
            [
                str(esbuild),
                str(_FIGURE_FORMATS),
                "--bundle",
                "--format=iife",
                "--global-name=A4U_FF",
                "--platform=browser",
                "--target=es2020",
                f"--outfile={out}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            pytest.skip(f"esbuild non riuscito: {proc.stderr.strip()[:200]}")
        return out.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sanitize_probe():
    """Chromium con la pagina di rendering di produzione, il bundle del
    modulo frontend e un registro delle richieste tentate verso l'host
    dell'attaccante (instradate e annullate: nulla esce davvero)."""
    from playwright.sync_api import sync_playwright

    if not _FIGURE_FORMATS.is_file():
        pytest.skip(f"sorgente frontend assente: {_FIGURE_FORMATS}")
    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:
        pytest.skip("cdn.jsdelivr.net non raggiungibile")
    bundle = _bundle_figure_formats()
    attempted: list[str] = []

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # Chromium non installato: verifica locale
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:200])
        try:
            page = browser.new_page()

            def _guard(route) -> None:
                url = route.request.url
                if url.lower().startswith(PRERENDER_ALLOWED_PREFIX):
                    route.continue_()
                    return
                if _HOST in url:
                    # Richiesta REGISTRATA e servita in loco con un PNG 1×1:
                    # nulla esce dalla macchina e Mermaid può misurare
                    # l'immagine, così la controprova rende davvero.
                    attempted.append(url)
                    route.fulfill(
                        status=200,
                        content_type="image/png",
                        headers={"Cache-Control": "no-store"},
                        body=_PNG_1x1,
                    )
                    return
                route.abort()

            page.route("**/*", _guard)
            page.set_content(build_mermaid_renderer_html(), wait_until="domcontentloaded")
            try:
                page.wait_for_function("window.__mermaidReady === true", timeout=20_000)
            except Exception as exc:
                pytest.skip(f"pagina di rendering non pronta: {exc!r}"[:200])
            page.add_script_tag(content=bundle)

            prove = 0

            def probe(code: str, *, sanitize: bool) -> dict:
                """Rende `code`, opzionalmente sanifica, inserisce nel
                documento VIVO e misura che cosa ne è uscito. Le richieste
                sono contate in DUE fasi separate: quelle del `render` di
                Mermaid (che lavora su un nodo temporaneo tutto suo) e
                quelle del nodo che il componente mette nella pagina — è
                solo la seconda che la sanificazione può chiudere."""
                nonlocal prove
                prove += 1
                code = code.replace("__N__", str(prove))
                before = len(attempted)
                svg = page.evaluate(
                    "async ([id, code]) => await window.__renderMermaid(id, code)",
                    [f"probe-{prove}", code],
                )
                assert svg, f"Mermaid non ha reso il sorgente: {code!r}"
                page.wait_for_timeout(700)
                richieste_render = attempted[before:]
                before = len(attempted)
                misura = page.evaluate(
                    """([svg, sanitize, host, n]) => {
                        const out = sanitize ? window.A4U_FF.sanitizeMermaidSvg(svg) : svg;
                        // Il nodo inserito punta a un URL SUO: la memory
                        // cache di Chromium servirebbe altrimenti quello
                        // gia' scaricato dal render, e il registro
                        // resterebbe vuoto anche con l'`<image>` vivo.
                        const box = document.createElement('div');
                        box.innerHTML = out.split('.png').join('-dom' + n + '.png');
                        document.body.appendChild(box);
                        const esterni = (sel, attrs) =>
                          Array.from(box.querySelectorAll(sel)).filter((el) =>
                            attrs.some((a) => (el.getAttribute(a) || '').includes(host)),
                          ).length;
                        return {
                          markup: out,
                          ancore: esterni('a', ['href', 'xlink:href']),
                          immagini: esterni('image', ['href', 'xlink:href']),
                        };
                    }""",
                    [svg, sanitize, _HOST, prove],
                )
                page.wait_for_timeout(700)
                misura["richieste_render"] = richieste_render
                misura["richieste_dom"] = attempted[before:]
                return misura

            yield probe
        finally:
            browser.close()


@pytest.mark.parametrize("nome", sorted(_VECTORS))
def test_the_client_sanitizer_neutralises_what_the_reader_would_fetch_or_follow(
    sanitize_probe, nome: str
) -> None:
    """Controprova e prova, sullo stesso sorgente e nello stesso browser:
    senza sanificazione il nodo vivo porta il riferimento esterno; con la
    sanificazione non resta né l'`<image>`, né l'`<a>`, né l'URL."""
    code = _VECTORS[nome]
    grezzo = sanitize_probe(code, sanitize=False)
    assert _HOST in grezzo["markup"], grezzo["markup"][:300]
    assert grezzo["ancore"] + grezzo["immagini"] > 0, f"controprova inerte: {nome}"

    pulito = sanitize_probe(code, sanitize=True)
    assert _HOST not in pulito["markup"], pulito["markup"][:400]
    assert pulito["ancore"] == 0 and pulito["immagini"] == 0
    assert pulito["richieste_dom"] == [], pulito["richieste_dom"]


def test_the_client_sanitizer_stops_the_get_the_reader_would_make(sanitize_probe) -> None:
    """La shape `img:` è l'unica delle tre che fa partire una richiesta
    subito: il nodo inserito nella pagina la tenta senza sanificazione e
    non la tenta con."""
    code = _VECTORS["shape_img"]
    assert sanitize_probe(code, sanitize=False)["richieste_dom"], "controprova senza richieste"
    assert sanitize_probe(code, sanitize=True)["richieste_dom"] == []


def test_the_render_of_mermaid_itself_still_fetches_declared_limit(sanitize_probe) -> None:
    """Limite dichiarato (sezione 15): `mermaid.render` misura il diagramma
    su un nodo temporaneo che attacca al documento, quindi la GET della
    shape `img:` parte PRIMA che il componente possa sanificare alcunché.
    La sanificazione toglie il riferimento persistente e ogni richiesta
    successiva (ricarica della vista, stampa), non questa. Se una versione
    futura di Mermaid rendesse fuori dal documento, questo test lo dice."""
    misura = sanitize_probe(_VECTORS["shape_img"], sanitize=True)
    assert misura["richieste_render"], "il render non tocca più il documento: aggiornare §15"
    assert misura["richieste_dom"] == []


def test_the_client_sanitizer_keeps_a_healthy_diagram_intact(sanitize_probe) -> None:
    """La sanificazione non è una potatura: su un diagramma sano restano il
    foglio di stile del tema, tutti i `<text>` e la geometria."""
    code = theme.MERMAID_D8_SAMPLES["flowchart"]
    grezzo = sanitize_probe(code, sanitize=False)["markup"]
    pulito = sanitize_probe(code, sanitize=True)
    assert pulito["richieste_dom"] == [] and pulito["richieste_render"] == []
    markup = pulito["markup"]
    assert "<style" in markup and "flowchart" in markup
    assert markup.count("<text") == grezzo.count("<text")
    assert markup.count("<path") == grezzo.count("<path")
    for parola in ("Ipotesi", "Condizione", "Tesi", "Controesempio"):
        assert parola in markup, parola
