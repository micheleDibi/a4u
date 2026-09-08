"""Il nodo di misura che Mermaid lascia indietro quando il render fallisce
(Fase D, giro 8).

`mermaid.render` calcola la geometria del diagramma dentro un
`<div id="d<id>">` che ATTACCA al `<body>`, e lo rimuove solo quando il
render arriva in fondo (`removeTempElements`, `mermaid.core.mjs`). Se
`draw` lancia, il div resta nella pagina: in flusso normale, largo quanto
la finestra, con dentro l'SVG del diagramma.

Con la Content-Security-Policy del giro 7 l'innesco è certo in produzione:
una shape `img:` verso un host esterno non carica più, il render lancia
`EncodingError` e l'editor rende a ogni battuta — dieci tentativi
lasciavano dieci copie visibili impilate sotto l'applicazione. La
correzione è `renderMermaidSvg` (`lib/figureFormats.ts`), che toglie il
nodo in un `finally`.

Due livelli, come per la sanificazione client:

1. lettura dell'albero `frontend/`: il componente passa da `renderMermaidSvg`
   e non più da `mermaid.render` nudo;
2. prova REALE in Chromium: Mermaid 11 rende davvero dieci volte lo stesso
   sorgente che fallisce, prima con `mermaid.render` nudo (controprova: i
   dieci nodi restano) e poi con la funzione del frontend, compilata con
   l'esbuild del progetto (nessun nodo resta). Serve Playwright con
   Chromium, Node e cdn.jsdelivr.net: senza, il test salta con motivo
   esplicito.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services import figure_theme as theme
from app.services.mermaid_prerender import PRERENDER_ALLOWED_PREFIX, build_mermaid_renderer_html

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_FIGURE_FORMATS = _FRONTEND / "src" / "lib" / "figureFormats.ts"
_MERMAID_DIAGRAM = _FRONTEND / "src" / "components" / "shared" / "MermaidDiagram.tsx"

# Host che il guard della pagina annulla: l'immagine non carica, quindi il
# render fallisce come fallisce sotto la politica del browser. Nessun byte
# esce dalla macchina.
_HOST = "esempio.non.esiste"
_VETTORE = f'flowchart LR\n  A@{{ img: "http://{_HOST}/__N__.png", w: 60, h: 60 }}\n  A --> B'
_TENTATIVI = 10


# ---------------------------------------------------------------------------
# 1. lettura dell'albero frontend
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"sorgente frontend assente: {path}")
    return path.read_text(encoding="utf-8")


def test_the_component_renders_through_the_cleaning_wrapper() -> None:
    """Il componente non chiama più `mermaid.render` direttamente: se
    qualcuno ce lo rimette, il nodo residuo torna e questo test lo vede."""
    src = _read(_MERMAID_DIAGRAM)
    assert re.search(r"const\s+rendered\s*=\s*await\s+renderMermaidSvg\(", src), src[:600]
    assert "mermaid.render(" not in src, "torna il render nudo: il nodo di misura resta"


def test_the_wrapper_removes_the_measuring_node_in_a_finally() -> None:
    """La rimozione sta in un `finally`, non nel ramo d'errore: vale anche
    per il render che riesce (dove è un no-op) e per ogni eccezione."""
    src = _read(_FIGURE_FORMATS)
    m = re.search(
        r"export async function renderMermaidSvg\([^)]*\)[^{]*\{(.*?)\n\}",
        src,
        re.DOTALL,
    )
    assert m, "renderMermaidSvg assente da figureFormats.ts"
    corpo = m.group(1)
    assert "finally" in corpo, corpo
    assert re.search(r"document\.getElementById\(`d\$\{id\}`\)\?\.remove\(\)", corpo), corpo


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


# Dieci render falliti con `mermaid.render` nudo (controprova) e dieci con
# `renderMermaidSvg`, nello stesso browser e sullo stesso sorgente. Conta i
# `<div id="d…">` che restano dopo ciascuna metà; fra le due ripulisce
# quelli della controprova, così la seconda misura parte dal pulito.
_MISURA_JS = """
async ([codice, tentativi]) => {
  const mermaid = window.__mermaid;
  const residui = (prefisso) =>
    Array.from(document.body.children).filter((el) => (el.id || '').startsWith(prefisso));
  const figliPrima = document.body.children.length;
  let erroriNudi = 0;
  const messaggi = [];
  for (let i = 0; i < tentativi; i++) {
    try {
      await mermaid.render('nudo-' + i, codice.replace('__N__', 'nudo' + i));
    } catch (e) {
      erroriNudi += 1;
      if (messaggi.length < 1) messaggi.push(String(e).slice(0, 80));
    }
  }
  const nudi = residui('dnudo-');
  const misure = nudi.slice(0, 1).map((el) => {
    const r = el.getBoundingClientRect();
    return {
      larghezza: Math.round(r.width),
      altezza: Math.round(r.height),
      visibilita: getComputedStyle(el).visibility,
      svg: el.querySelectorAll('svg').length,
    };
  });
  const orfaniNudi = nudi.length;
  for (const el of nudi) el.remove();
  let erroriPuliti = 0;
  for (let i = 0; i < tentativi; i++) {
    try {
      await window.A4U_FF.renderMermaidSvg(
        mermaid, 'pulito-' + i, codice.replace('__N__', 'pulito' + i));
    } catch (e) {
      erroriPuliti += 1;
    }
  }
  return {
    figliPrima,
    erroriNudi,
    orfaniNudi,
    messaggi,
    misure,
    erroriPuliti,
    orfaniPuliti: residui('dpulito-').length,
    figliDopo: document.body.children.length,
  };
}
"""

_SANO_JS = """
async ([codice]) => {
  const figliPrima = document.body.children.length;
  const svg = await window.A4U_FF.renderMermaidSvg(window.__mermaid, 'sano-1', codice);
  return {
    figliPrima,
    figliDopo: document.body.children.length,
    residui: Array.from(document.body.children).filter(
      (el) => (el.id || '').startsWith('dsano-')).length,
    svg: (svg || '').slice(0, 40),
  };
}
"""


@pytest.fixture(scope="module")
def pagina_mermaid():
    """Chromium con la pagina di rendering di produzione, l'istanza vera di
    Mermaid esposta su `window.__mermaid` e il bundle di
    `lib/figureFormats.ts`. Ogni richiesta fuori dal CDN è annullata: la
    shape `img:` non carica e il render fallisce come sotto la politica."""
    from playwright.sync_api import sync_playwright

    if not _FIGURE_FORMATS.is_file():
        pytest.skip(f"sorgente frontend assente: {_FIGURE_FORMATS}")
    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:
        pytest.skip("cdn.jsdelivr.net non raggiungibile")
    bundle = _bundle_figure_formats()
    pin = get_settings().mermaid_cdn_version

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # Chromium non installato: verifica locale
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:200])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})

            def _guard(route) -> None:
                if route.request.url.lower().startswith(PRERENDER_ALLOWED_PREFIX):
                    route.continue_()
                    return
                route.abort()

            page.route("**/*", _guard)
            page.set_content(build_mermaid_renderer_html(), wait_until="domcontentloaded")
            try:
                page.wait_for_function("window.__mermaidReady === true", timeout=20_000)
            except Exception as exc:
                pytest.skip(f"pagina di rendering non pronta: {exc!r}"[:200])
            # Stessa URL del modulo già importato dalla pagina: la cache dei
            # moduli restituisce l'ISTANZA che il tema ha inizializzato.
            page.add_script_tag(
                content=(
                    f"import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@{pin}"
                    "/dist/mermaid.esm.min.mjs';\n"
                    "window.__mermaid = mermaid;\n"
                ),
                type="module",
            )
            try:
                page.wait_for_function("window.__mermaid !== undefined", timeout=20_000)
            except Exception as exc:
                pytest.skip(f"Mermaid non esposto: {exc!r}"[:200])
            page.add_script_tag(content=bundle)
            yield page
        finally:
            browser.close()


def test_ten_failed_renders_do_not_leave_ten_copies_in_the_body(pagina_mermaid) -> None:
    """La misura del giro 8, controprova compresa.

    Con `mermaid.render` nudo dieci render falliti lasciano dieci `<div>`
    visibili a piena larghezza, ciascuno con l'SVG del diagramma dentro:
    è quello che il docente vedeva accumularsi mentre scriveva. Con
    `renderMermaidSvg` gli stessi dieci fallimenti non lasciano nulla."""
    esito = pagina_mermaid.evaluate(_MISURA_JS, [_VETTORE, _TENTATIVI])

    assert esito["erroriNudi"] == _TENTATIVI, esito
    assert esito["orfaniNudi"] == _TENTATIVI, f"controprova inerte: {esito}"
    misura = esito["misure"][0]
    assert misura["visibilita"] == "visible" and misura["svg"] >= 1, misura
    assert misura["larghezza"] > 400, misura

    assert esito["erroriPuliti"] == _TENTATIVI, esito
    assert esito["orfaniPuliti"] == 0, esito
    assert esito["figliDopo"] == esito["figliPrima"], esito


def test_a_healthy_diagram_still_renders_and_leaves_nothing(pagina_mermaid) -> None:
    """Il percorso felice non cambia: l'SVG torna e Mermaid ha già tolto il
    nodo da sé (il `finally` è un no-op)."""
    esito = pagina_mermaid.evaluate(_SANO_JS, [theme.MERMAID_D8_SAMPLES["flowchart"]])
    assert "<svg" in esito["svg"], esito
    assert esito["residui"] == 0, esito
    assert esito["figliDopo"] == esito["figliPrima"], esito
