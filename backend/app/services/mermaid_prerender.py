"""Pre-render Mermaid → SVG (Playwright headless) e post-processing.

Estratto da `course_lesson_pdf_service`, che re-esporta i vecchi nomi: i
chiamanti (PDF dispensa, PDF slide, frame video, script di rivalidazione)
importano da lì o da qui indifferentemente.

Pin unico della libreria: `settings.mermaid_cdn_version` (lo stesso del
validatore in `asset_validation_service` e del lock npm del frontend).
Configurazione unica: `figure_theme.mermaid_initialize_js` (tema D3,
`htmlLabels: false` al livello TOP).

Perché `htmlLabels: false`: per le label Mermaid usa di default
`<foreignObject>` con HTML dentro l'SVG, che WeasyPrint non renderizza.
Con l'opzione al livello top Mermaid 11.17.2 emette `<text>` puro
(0 `<foreignObject>`) per tutti i quindici tipi di D8 — verificato dal test
`test_mermaid_no_foreignobject` sull'output reale. L'opzione per-tipo da
sola non basta: in Mermaid 11 i renderer «unificati» (flowchart, state,
block, class) leggono il valore top-level. `journey` emette due
`<foreignObject>` anche in 10.9.4 (non dipende dall'opzione) ed è per
questo escluso (`figure_theme.MERMAID_EXCLUDED_TYPES`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import figure_geometry
from app.services.figure_geometry import GeometryReport
from app.services.figure_scale import SvgMetrics
from app.services.figure_theme import MERMAID_FONT_FAMILY, mermaid_initialize_js

log = get_logger("app.mermaid_prerender")


# ---------------------------------------------------------------------------
# Post-processing dell'SVG
# ---------------------------------------------------------------------------

# Con `useMaxWidth: true` Mermaid imposta `style="max-width: <natural_px>;"`
# sull'SVG generato. Questo IMPEDISCE all'SVG di crescere oltre la sua
# dimensione naturale (tipicamente ~300-400px), anche se il container del
# PDF è molto più largo (un foglio A4 ha ~170mm di content area = ~640px).
# Risultato: il diagramma rimane piccolo e le label illeggibili. Strippiamo
# quel `max-width:Xpx` lasciando tutto il resto dello stile così l'SVG
# riempie il container. La regex è indipendente dalla versione di Mermaid:
# la 11.17.2 emette ancora `max-width: <px>px;` (fixture
# `tests/fixtures/mermaid11_flowchart.svg`); se una versione futura
# cambiasse unità la funzione degrada a no-op, segnalato dal test D8.
_MERMAID_MAX_WIDTH_RE = re.compile(r"max-width\s*:\s*[\d.]+px\s*;?", re.IGNORECASE)


def _strip_mermaid_max_width(svg: str) -> str:
    return _MERMAID_MAX_WIDTH_RE.sub("", svg)


# `sankey-beta` scrive nome e valore del nodo in UN SOLO `<text>` separati da
# un a capo letterale (`<text …>Lezioni\n48</text>`): non usa `<tspan>`.
# Con `xml:space` di default la specifica SVG 1.1 dice di RIMUOVERE i fine
# riga — WeasyPrint lo fa e nel PDF si legge «Lezioni48», mentre Chromium è
# indulgente e mostra «Lezioni 48». La normalizzazione porta i due renderer
# a dire la stessa cosa. Vale per ogni figura sankey, non per i soli modelli
# degli editor. Nessun altro tipo D8 emette a capo dentro `<text>` (gli altri
# vanno a capo con `<tspan>`), quindi la sostituzione è mirata: solo il
# contenuto di un `<text>` SENZA figli.
_MERMAID_TEXT_NEWLINE_RE = re.compile(r"(<text\b[^>]*>)([^<]*\n[^<]*)(</text>)")


def _join_mermaid_text_newlines(svg: str) -> str:
    """A capo letterali dentro un `<text>` senza figli → spazio singolo."""
    return _MERMAID_TEXT_NEWLINE_RE.sub(
        lambda m: m.group(1) + " ".join(m.group(2).split()) + m.group(3), svg
    )


# ---------------------------------------------------------------------------
# Pagina headless di rendering
# ---------------------------------------------------------------------------

# Misura del corpo dei testi di un SVG nella pagina già aperta (D10): host
# fuori schermo (mai `visibility:hidden`, che azzera i testi), un solo
# `getComputedStyle` per elemento `text`/`tspan` con nodo di testo proprio
# non vuoto, filtro SOLO su `display:none` (come `_MISURA_JS` del test dei
# template frontend), host rimosso in `finally`. `fontSize` calcolato è in
# unità utente, indipendente da viewBox e larghezza resa (`font-size="10"`
# → 10, `"11pt"` → 14.667, `"4ex"` → 29.29). Ritorna `{min, median, count}`
# (`{null, null, 0}` senza testi). `measureSvgFontPx` in
# `frontend/src/lib/figureFormats.ts` ne è il mirror funzionale, non una
# copia letterale (TypeScript: `textContent ?? ""`, host creato dentro il
# `try`, `catch` → `null`): stessa selezione, stesso filtro, stessa
# mediana; la parità è provata in Chromium da
# `tests/test_frontend_figure_layout.py::test_measure_js_parity_with_frontend`.
MEASURE_SVG_FONT_PX_JS = """(svg) => {
  const host = document.createElement("div");
  host.style.cssText = "position:absolute;left:-100000px;top:0;width:1000px";
  host.innerHTML = svg;
  document.body.appendChild(host);
  try {
    const sizes = [];
    for (const el of host.querySelectorAll("text, tspan")) {
      const own = Array.from(el.childNodes).some(
        (n) => n.nodeType === 3 && n.textContent.trim() !== "",
      );
      if (!own) continue;
      const cs = getComputedStyle(el);
      if (cs.display === "none") continue;
      const px = parseFloat(cs.fontSize);
      if (Number.isFinite(px) && px > 0) sizes.push(px);
    }
    if (sizes.length === 0) return { min: null, median: null, count: 0 };
    sizes.sort((a, b) => a - b);
    const mid = sizes.length >> 1;
    const median = sizes.length % 2 === 1 ? sizes[mid] : (sizes[mid - 1] + sizes[mid]) / 2;
    return { min: sizes[0], median, count: sizes.length };
  } finally {
    host.remove();
  }
}"""

# Geometria di un SVG nella pagina già aperta (D14): incroci arco × arco e
# testo fuori dalla tela. Gemello di `figure_geometry.measure_svg`, di cui
# specchia la selezione degli archi (`g.edge > path`, `g.edgePath > path`,
# anche attraverso gli involucri `<a>` / `g#a_…` dei collegamenti di
# Graphviz, classi `edge-thickness-*` e `messageLine*`, `flowchart-link`,
# `transition`, `relation`, `relationshipLine`), il passo di campionamento,
# il test d'intersezione con estremi inclusi e collineari esclusi, lo
# scarto a meno di `endpointTol` dagli estremi dei due tracciati e il
# raggruppamento a `clusterRadius`; la parità è provata sui diciotto modelli
# DOT (`tests/test_figure_geometry.py`). Il campionamento usa
# `getPointAtLength` trasformato nel sistema della radice, con passo locale
# `step / k` (`k`: allungamento massimo della trasformazione, 1 senza
# scala), così i segmenti misurano al più un passo della radice come in
# Python (giro 3, V3-N1); ogni salto fra sotto-tracciati dentro un
# intervallo è trovato per bisezione e mai tracciato. Costo limitato per
# costruzione, con lo schema del docstring di `figure_geometry` (giro 3,
# V3-F1: un arco scalato 5000 volte enumerava milioni di celle, «Map
# maximum size exceeded» dopo 8 s e 2,5 GB; un intervallo con due salti
# tracciava un segmento fantasma di un milione di unità, oltre 60 s):
# lunghezze lette PRIMA di campionare (oltre `maxSegments` o il residuo
# `budget` la misura è saltata), tela dal viewBox (o riquadro unione),
# passo della griglia adattivo, impronte ritagliate e celle contate prima
# di enumerarle, coppie candidate contate prima di confrontarle (ciascuna
# provata una volta nella cella d'angolo, senza insieme dei visti), costo
# del raggruppamento contato prima; oltre `maxWork` o il residuo
# `workBudget` la misura è saltata. `work` è il lavoro contato, `spent`
# quello eseguito (la pagina lo sottrae al residuo del batch). Taratura del
# 17 settembre 2026 (Chromium di Playwright, macchina di sviluppo): 9-54 ns
# per unità di lavoro (54 su un reticolo da 0,65 milioni, dove pesano i
# costi fissi, 31 su uno da 7,3 milioni, 9 su un fascio di archi
# coincidenti), contro 0,33-0,35 µs di Python sugli stessi SVG; i modelli
# Mermaid dell'editor valgono meno di 1.300 unità, il bipartito 5×9 (45
# archi, il massimo editoriale) 0,34 milioni. Da qui
# `figure_geometry.MAX_BROWSER_MEASURE_WORK`. Il controllo del testo
# (`texts: true`) è quello di `_MISURA_JS` dei test del frontend, con la
# tolleranza `textTol`; gli altri tre difetti DOT non valgono per Mermaid
# (le etichette degli archi stanno sull'arco per costruzione). L'SVG è
# misurato in un host fuori schermo e non è mai riscritto; l'host è rimosso
# in `finally`.
MEASURE_SVG_GEOMETRY_JS = """(svg, opts) => {
  const o = Object.assign({ step: 2, endpointTol: 2, clusterRadius: 3, textTol: 4,
                            maxSegments: 50000, budget: Infinity, texts: true,
                            maxDefects: 20, cell: 16, maxCells: 250000,
                            maxWork: 10000000, workBudget: Infinity }, opts || {});
  const none = (skipped, edges, segments, work = 0, spent = 0) =>
    ({ crossings: null, pairs: null, edges, segments, defects: [], skipped, work, spent });
  const RANGE = "geometry_out_of_range";
  const host = document.createElement("div");
  host.style.cssText = "position:absolute;left:-100000px;top:0;width:1000px";
  host.innerHTML = svg;
  document.body.appendChild(host);
  try {
    const root = host.querySelector("svg");
    const rootCtm = root ? root.getScreenCTM() : null;
    if (!rootCtm) return none("no_svg", 0, 0);
    const inv = rootCtm.inverse();
    const toRoot = (el) => inv.multiply(el.getScreenCTM());
    // Allungamento massimo (valore singolare maggiore) della parte lineare.
    const stretch = (m) => {
      const t = m.a * m.a + m.b * m.b + m.c * m.c + m.d * m.d;
      const det = m.a * m.d - m.b * m.c;
      return Math.sqrt((t + Math.sqrt(Math.max(0, t * t - 4 * det * det))) / 2);
    };
    const PREFIXES = ["edge-thickness-", "messageLine"];
    const NAMES = new Set(["flowchart-link", "transition", "relation", "relationshipLine"]);
    const GROUPS = ["edge", "edgePath"];
    const isEdgeClass = (c) => NAMES.has(c) || PREFIXES.some((p) => c.startsWith(p));
    const isAnchorWrapper = (el) => {
      const tag = el.tagName.toLowerCase();
      return tag === "a" || (tag === "g" && !(el.getAttribute("class") || "").trim() &&
                             (el.getAttribute("id") || "").startsWith("a_"));
    };
    const owner = (el) => {
      let p = el.parentElement;
      while (p && p !== root && isAnchorWrapper(p)) p = p.parentElement;
      return p;
    };
    const units = new Map();
    const items = [];
    let estimate = 0;
    for (const el of root.querySelectorAll("path, line, polyline")) {
      const tag = el.tagName.toLowerCase();
      const parent = owner(el);
      let unit = null;
      if (parent && parent.tagName.toLowerCase() === "g" &&
          GROUPS.some((g) => parent.classList.contains(g))) {
        if (tag !== "path") continue;
        unit = parent;
      } else if ([...el.classList].some(isEdgeClass)) {
        unit = el;
      } else {
        continue;
      }
      const length = el.getTotalLength();
      if (!(length > 0)) continue;
      if (!units.has(unit)) units.set(unit, units.size);
      const m = toRoot(el);
      const k = Math.round(stretch(m) * 1e9) / 1e9;
      const n = Math.max(1, Math.ceil((length * k) / o.step));
      if (!Number.isFinite(n)) return none(RANGE, units.size, 0);
      estimate += n;
      items.push({ el, unit: units.get(unit), length, m, n });
    }
    if (estimate > o.maxSegments) return none("figure_segment_cap", units.size, estimate);
    if (estimate > o.budget) return none("batch_segment_cap", units.size, estimate);
    const segs = [];
    const ends = [];
    // Due punti dello stesso sotto-tracciato distano al più la lunghezza
    // percorsa fra loro: oltre, fra i due c'è un `M` intermedio (salto).
    const joined = (a, pa, b, pb) =>
      Math.hypot(pb.x - pa.x, pb.y - pa.y) <= (b - a) * 1.01 + 1e-3;
    for (let t = 0; t < items.length; t++) {
      const { el, unit, length, m, n } = items[t];
      const raw = (d) => el.getPointAtLength(d);
      let prevD = 0;
      let prevRaw = raw(0);
      let prev = prevRaw.matrixTransform(m);
      const first = prev;
      for (let i = 1; i <= n; i++) {
        const d = Math.min(length, (length * i) / n);
        const curRaw = raw(d);
        const cur = curRaw.matrixTransform(m);
        if (!Number.isFinite(cur.x) || !Number.isFinite(cur.y)) {
          return none(RANGE, units.size, estimate);
        }
        // Ogni salto dell'intervallo è cercato per bisezione e saltato;
        // un segmento unisce solo punti contigui dello stesso tratto.
        let fromD = prevD, fromRaw = prevRaw, from = prev;
        while (!joined(fromD, fromRaw, d, curRaw)) {
          let lo = fromD, hi = d;
          for (let q = 0; q < 40 && hi - lo > 1e-4; q++) {
            const mid = (lo + hi) / 2;
            if (joined(fromD, fromRaw, mid, raw(mid))) lo = mid; else hi = mid;
          }
          const tail = raw(lo).matrixTransform(m);
          segs.push([from.x, from.y, tail.x, tail.y, unit, t]);
          if (segs.length > o.maxSegments) {
            return none("figure_segment_cap", units.size, segs.length);
          }
          fromD = hi;
          fromRaw = raw(hi);
          from = fromRaw.matrixTransform(m);
        }
        segs.push([from.x, from.y, cur.x, cur.y, unit, t]);
        prevD = d;
        prevRaw = curRaw;
        prev = cur;
      }
      ends.push([first, prev]);
    }
    // Tela, passo e impronte ritagliate (docstring di `figure_geometry`).
    let X0 = 0, Y0 = 0, X1 = 0, Y1 = 0;
    const vb = root.viewBox ? root.viewBox.baseVal : null;
    if (vb && vb.width > 0 && vb.height > 0) {
      X0 = vb.x; Y0 = vb.y; X1 = vb.x + vb.width; Y1 = vb.y + vb.height;
    } else if (segs.length) {
      X0 = Infinity; Y0 = Infinity; X1 = -Infinity; Y1 = -Infinity;
      for (const s of segs) {
        X0 = Math.min(X0, s[0], s[2]); Y0 = Math.min(Y0, s[1], s[3]);
        X1 = Math.max(X1, s[0], s[2]); Y1 = Math.max(Y1, s[1], s[3]);
      }
    }
    if (![X0, Y0, X1, Y1, (X1 - X0) + (Y1 - Y0)].every(Number.isFinite)) {
      return none(RANGE, units.size, segs.length);
    }
    const side = Math.floor(Math.sqrt(o.maxCells));
    const size = Math.max(o.cell, ((X1 - X0) + (Y1 - Y0)) / (2 * (side - 2)));
    const rows = Math.floor((Y1 - Y0) / size) + 1;
    const col = (x) => Math.floor((Math.min(Math.max(x, X0), X1) - X0) / size);
    const row = (y) => Math.floor((Math.min(Math.max(y, Y0), Y1) - Y0) / size);
    const spans = segs.map((s) => [col(Math.min(s[0], s[2])), row(Math.min(s[1], s[3])),
                                   col(Math.max(s[0], s[2])), row(Math.max(s[1], s[3]))]);
    const over = (w) =>
      (w > o.maxWork ? "figure_work_cap" : w > o.workBudget ? "batch_work_cap" : null);
    let work = 0;
    for (const p of spans) work += (p[2] - p[0] + 1) * (p[3] - p[1] + 1);
    if (over(work)) return none(over(work), units.size, segs.length, work, 0);
    const grid = new Map();
    spans.forEach((p, i) => {
      for (let gx = p[0]; gx <= p[2]; gx++) {
        for (let gy = p[1]; gy <= p[3]; gy++) {
          const key = gx * rows + gy;
          let cell = grid.get(key);
          if (!cell) grid.set(key, (cell = []));
          cell.push(i);
        }
      }
    });
    let spent = work;
    let pairWork = 0;
    for (const members of grid.values()) pairWork += (members.length * (members.length - 1)) / 2;
    work += pairWork;
    if (over(work)) return none(over(work), units.size, segs.length, work, spent);
    const EPS = 1e-9;
    const orient = (ax, ay, bx, by, cx, cy) => {
      const v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
      return Math.abs(v) < EPS ? 0 : v;
    };
    const near = (x, y, pair) =>
      pair.some((p) => Math.hypot(p.x - x, p.y - y) < o.endpointTol);
    const hits = [];
    for (const [key, members] of grid) {
      const gy = key % rows;
      const gx = (key - gy) / rows;
      for (let a = 0; a < members.length; a++) {
        const i = members[a];
        const s = segs[i];
        const si = spans[i];
        for (let b = a + 1; b < members.length; b++) {
          const j = members[b];
          const r = segs[j];
          if (s[4] === r[4]) continue;
          const sj = spans[j];
          // Una coppia è provata solo nella cella d'angolo delle due impronte.
          if (Math.max(si[0], sj[0]) !== gx || Math.max(si[1], sj[1]) !== gy) continue;
          const d1 = orient(r[0], r[1], r[2], r[3], s[0], s[1]);
          const d2 = orient(r[0], r[1], r[2], r[3], s[2], s[3]);
          if ((d1 > 0 && d2 > 0) || (d1 < 0 && d2 < 0) || (d1 === 0 && d2 === 0)) continue;
          const d3 = orient(s[0], s[1], s[2], s[3], r[0], r[1]);
          const d4 = orient(s[0], s[1], s[2], s[3], r[2], r[3]);
          if ((d3 > 0 && d4 > 0) || (d3 < 0 && d4 < 0)) continue;
          const k = d1 / (d1 - d2);
          const x = s[0] + k * (s[2] - s[0]);
          const y = s[1] + k * (s[3] - s[1]);
          if (near(x, y, ends[s[5]]) || near(x, y, ends[r[5]])) continue;
          hits.push([x, y, Math.min(s[4], r[4]), Math.max(s[4], r[4])]);
        }
      }
    }
    spent += pairWork;
    // Raggruppamento a piano (`figure_geometry._cluster_plan`): celle di
    // lato appena sotto R/√2, coppie di celle vicine decise dai riquadri,
    // confronti solo per le coppie incerte, costo contato prima.
    const R = o.clusterRadius;
    const cside = (R / Math.SQRT2) * (1 - 1e-9);
    const cmap = new Map();
    hits.forEach((h, i) => {
      const cx = Math.floor(h[0] / cside), cy = Math.floor(h[1] / cside);
      const key = cx + "," + cy;
      let c = cmap.get(key);
      if (!c) cmap.set(key, (c = { cx, cy, items: [], x0: h[0], y0: h[1], x1: h[0], y1: h[1] }));
      c.items.push(i);
      c.x0 = Math.min(c.x0, h[0]); c.y0 = Math.min(c.y0, h[1]);
      c.x1 = Math.max(c.x1, h[0]); c.y1 = Math.max(c.y1, h[1]);
    });
    const OFFSETS = [[0, 1], [0, 2], [1, -2], [1, -1], [1, 0], [1, 1], [1, 2],
                     [2, -2], [2, -1], [2, 0], [2, 1], [2, 2]];
    const linked = [];
    const unsure = [];
    let checks = 0;
    for (const c of cmap.values()) {
      for (const [dx, dy] of OFFSETS) {
        const e = cmap.get((c.cx + dx) + "," + (c.cy + dy));
        if (!e) continue;
        const gap = Math.hypot(Math.max(0, e.x0 - c.x1, c.x0 - e.x1),
                               Math.max(0, e.y0 - c.y1, c.y0 - e.y1));
        if (gap > R * (1 + 1e-9)) continue;
        const far = Math.hypot(Math.max(c.x1 - e.x0, e.x1 - c.x0),
                               Math.max(c.y1 - e.y0, e.y1 - c.y0));
        if (far <= R * (1 - 1e-9)) {
          linked.push([c, e]);
        } else {
          unsure.push([c, e]);
          checks += c.items.length * e.items.length;
        }
      }
    }
    work += 2 * hits.length + OFFSETS.length * cmap.size + checks;
    if (over(work)) return none(over(work), units.size, segs.length, work, spent);
    const parent = hits.map((_, i) => i);
    const find = (i) => {
      while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; }
      return i;
    };
    for (const c of cmap.values()) {
      for (const i of c.items) parent[find(i)] = find(c.items[0]);
    }
    for (const [c, e] of linked) parent[find(c.items[0])] = find(e.items[0]);
    for (const [c, e] of unsure) {
      if (find(c.items[0]) === find(e.items[0])) continue;
      const close = c.items.some((i) => e.items.some((j) =>
        Math.hypot(hits[i][0] - hits[j][0], hits[i][1] - hits[j][1]) <= R));
      if (close) parent[find(c.items[0])] = find(e.items[0]);
    }
    const clusters = new Map();
    hits.forEach((h, i) => {
      const c = find(i);
      if (!clusters.has(c)) clusters.set(c, new Set());
      clusters.get(c).add(h[2] + ":" + h[3]);
    });
    let pairs = 0;
    for (const set of clusters.values()) pairs += set.size;
    const defects = [];
    if (o.texts) {
      const vb = root.viewBox.baseVal;
      for (const t of root.querySelectorAll("text")) {
        if (getComputedStyle(t).display === "none") continue;
        const text = (t.textContent || "").trim();
        if (!text) continue;
        const b = t.getBBox();
        const m = toRoot(t);
        const pt = (x, y) => {
          const p = root.createSVGPoint(); p.x = x; p.y = y;
          return p.matrixTransform(m);
        };
        const c = [pt(b.x, b.y), pt(b.x + b.width, b.y),
                   pt(b.x, b.y + b.height), pt(b.x + b.width, b.y + b.height)];
        const xs = c.map((p) => p.x), ys = c.map((p) => p.y);
        const over = Math.max(vb.x - Math.min(...xs), Math.max(...xs) - (vb.x + vb.width),
                              vb.y - Math.min(...ys), Math.max(...ys) - (vb.y + vb.height));
        if (over > o.textTol) {
          const short = text.length <= 40 ? text : text.slice(0, 39) + "\\u2026";
          defects.push(`text_outside_canvas: «${short}» fuori dalla tela di ${over.toFixed(1)}`);
        }
      }
    }
    return { crossings: clusters.size, pairs, edges: units.size, segments: segs.length,
             defects: [...new Set(defects)].slice(0, o.maxDefects), skipped: null,
             work, spent: work };
  } finally {
    host.remove();
  }
}"""

# HTML mini-doc che carica mermaid.esm da CDN ed espone una funzione
# globale `__renderMermaid(id, code)` che ritorna SVG (o null se errore) e
# `__renderMermaidMeasured(id, code)` che ritorna `{svg, metrics, geometry}`
# con le metriche del testo misurate da `__measureSvgFontPx` e la geometria
# da `__measureSvg` nella stessa pagina (una sola `page.evaluate` per
# figura; i residui dei tetti del batch vivono in `__measureBudget`,
# segmenti delle misure complete, e `__measureWorkBudget`, lavoro eseguito
# anche dalle misure saltate; una pagina per batch). `__renderMermaid`
# resta una stringa: i test del sanitizer e dei `<foreignObject>` lo chiamano
# direttamente. Segnaposto sostituiti da `build_mermaid_renderer_html`: la
# versione (`settings.mermaid_cdn_version`), l'istruzione
# `mermaid.initialize(...)` prodotta da `figure_theme`, le due funzioni di
# misura e le loro opzioni (le graffe del JS impediscono `str.format`).
_MERMAID_RENDERER_HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>body{margin:0;padding:0;font-family:__MERMAID_FONT_FAMILY__;}</style></head>
<body>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@__MERMAID_VERSION__/dist/mermaid.esm.min.mjs';
__MERMAID_INITIALIZE__
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
window.__measureSvgFontPx = __MERMAID_MEASURE__;
window.__measureSvg = __MERMAID_GEOMETRY__;
window.__measureOptions = __MERMAID_GEOMETRY_OPTIONS__;
window.__measureBudget = window.__measureOptions.batchSegments;
window.__measureWorkBudget = window.__measureOptions.batchWork;
window.__renderMermaidMeasured = async (id, code) => {
  const svg = await window.__renderMermaid(id, code);
  if (typeof svg !== "string" || !svg) return null;
  let metrics = null;
  try {
    metrics = window.__measureSvgFontPx(svg);
  } catch (e) {
    metrics = null;
  }
  let geometry = null;
  try {
    geometry = window.__measureSvg(svg, Object.assign({}, window.__measureOptions, {
      budget: window.__measureBudget, workBudget: window.__measureWorkBudget }));
    if (geometry && geometry.skipped === null) window.__measureBudget -= geometry.segments;
    if (geometry) window.__measureWorkBudget -= geometry.spent || 0;
  } catch (e) {
    geometry = null;
  }
  return { svg, metrics, geometry };
};
window.__mermaidReady = true;
</script>
</body></html>
"""


# ---------------------------------------------------------------------------
# Isolamento di rete della pagina headless
# ---------------------------------------------------------------------------

# Unica origine che le pagine headless devono poter contattare: il CDN da
# cui importano i moduli (Mermaid nel pre-render, Mermaid e KaTeX nel
# validatore). Tutto il resto è bloccato PRIMA della richiesta: se un
# giorno un costrutto Mermaid sfuggisse al gate statico (`A@{ img: "…" }`,
# SEC-1) il server non eseguirebbe comunque la GET verso l'host scelto
# dall'autore. La barra finale è parte del prefisso: `cdn.jsdelivr.net.…`
# non lo soddisfa.
PRERENDER_ALLOWED_PREFIX = "https://cdn.jsdelivr.net/"
# Schemi che non escono dal processo (il documento stesso, gli URL inline).
_PRERENDER_INERT_SCHEMES = ("about:", "data:", "blob:")


def allows_prerender_url(url: str, *, allowed_prefixes: Sequence[str] = ()) -> bool:
    """`True` se la pagina headless può eseguire la richiesta: il CDN, gli
    schemi inerti e, se dati, i prefissi in più di `allowed_prefixes`
    (confronto senza maiuscole; un prefisso vuoto non ammette nulla)."""
    lowered = (url or "").strip().lower()
    extra = tuple(p.strip().lower() for p in allowed_prefixes if p and p.strip())
    return lowered.startswith((PRERENDER_ALLOWED_PREFIX, *_PRERENDER_INERT_SCHEMES, *extra))


def media_url_prefixes() -> tuple[str, ...]:
    """Origini in più per i motori che rendono contenuti d'autore (frame
    video in Chromium, WeasyPrint dei tre PDF): l'host pubblico dei media
    (`ovh_public_base_url`, con la barra finale) quando lo storage è remoto.
    Figure e formule sono data URL e i loghi e lo sfondo del template
    caricati dall'app arrivano come data URL (`_resolve_template_asset_url`):
    l'host dei media serve solo a un path assoluto già salvato in quella
    forma. Il backend locale (`public_base_url`) non è mai ammesso."""
    settings = get_settings()
    if settings.storage_backend not in ("ovh_ftp", "ovh_sftp"):
        return ()
    base = (settings.ovh_public_base_url or "").strip().rstrip("/")
    return (f"{base}/",) if base.startswith(("https://", "http://")) else ()


# Qualunque WebSocket: `page.route` vede solo le richieste HTTP(S), non gli
# upgrade `ws://`/`wss://`, che hanno un instradamento a parte.
_ANY_WEBSOCKET = re.compile(r".*")


async def block_external_requests(page: Any, *, allowed_prefixes: Sequence[str] = ()) -> None:
    """Instrada TUTTE le richieste della pagina e annulla quelle che
    `allows_prerender_url` non ammette (isolamento di rete del pre-render,
    difesa in profondità di SEC-1). `allowed_prefixes` aggiunge origini
    esplicite (i frame video, `lesson_slides_video_render_service`: l'host
    dei media); il default lascia la regola invariata.

    I WebSocket non passano da `page.route`: sono instradati a parte
    (`page.route_web_socket`) e chiusi tutti prima dell'handshake (nessuna
    pagina headless ne usa). Quell'instradamento vale per i documenti
    caricati dopo: per questo la pagina è riportata su `about:blank` prima
    di tornare, e il chiamante carica il contenuto con `set_content`.
    Restano fuori i canali che Playwright non instrada (WebRTC,
    WebTransport) e, da un Worker, l'apertura TCP verso l'host del
    WebSocket (l'handshake non parte): per questo la pagina dei frame
    video, l'unica che carica HTML d'autore, gira con JavaScript spento."""
    prefixes = tuple(allowed_prefixes)

    async def _guard(route: Any) -> None:
        url = route.request.url
        if allows_prerender_url(url, allowed_prefixes=prefixes):
            await route.continue_()
            return
        log.warning("prerender_request_blocked", url=url[:200])
        await route.abort()

    async def _close_websocket(ws: Any) -> None:
        log.warning("prerender_websocket_blocked", url=str(ws.url)[:200])
        await ws.close()

    await page.route("**/*", _guard)
    await page.route_web_socket(_ANY_WEBSOCKET, _close_websocket)
    await page.goto("about:blank")


def build_mermaid_renderer_html(*, version: str | None = None) -> str:
    """Pagina di rendering con il pin richiesto (default: il setting) e
    l'inizializzazione del tema (`useMaxWidth: true`: l'SVG riempie il
    contenitore; il `max-width` naturale è poi rimosso da
    `_strip_mermaid_max_width`)."""
    pin = version or get_settings().mermaid_cdn_version
    return (
        _MERMAID_RENDERER_HTML_TEMPLATE.replace("__MERMAID_VERSION__", pin)
        .replace("__MERMAID_FONT_FAMILY__", MERMAID_FONT_FAMILY)
        .replace("__MERMAID_INITIALIZE__", mermaid_initialize_js(use_max_width=True))
        .replace("__MERMAID_MEASURE__", MEASURE_SVG_FONT_PX_JS)
        .replace("__MERMAID_GEOMETRY_OPTIONS__", json.dumps(geometry_measure_options()))
        .replace("__MERMAID_GEOMETRY__", MEASURE_SVG_GEOMETRY_JS)
    )


def geometry_measure_options(*, texts: bool = True) -> dict[str, Any]:
    """Opzioni di `window.__measureSvg` dalle costanti di `figure_geometry`
    (unico punto dei valori: passo, tolleranze, tetti)."""
    return {
        "step": figure_geometry.SAMPLE_STEP,
        "endpointTol": figure_geometry.ENDPOINT_TOLERANCE,
        "clusterRadius": figure_geometry.CLUSTER_RADIUS,
        "textTol": figure_geometry.MERMAID_TEXT_TOLERANCE,
        "maxSegments": figure_geometry.MAX_MEASURE_SEGMENTS,
        "batchSegments": figure_geometry.MAX_BATCH_MEASURE_SEGMENTS,
        "cell": figure_geometry.GRID_CELL,
        "maxCells": figure_geometry.MAX_GRID_CELLS,
        "maxWork": figure_geometry.MAX_BROWSER_MEASURE_WORK,
        "batchWork": figure_geometry.MAX_BATCH_BROWSER_MEASURE_WORK,
        "maxDefects": figure_geometry.MAX_DEFECTS,
        "texts": texts,
    }


def __getattr__(name: str) -> str:
    """`_MERMAID_RENDERER_HTML` è costruita alla prima lettura (PEP 562): il
    pin viene da `get_settings()` e il modulo resta importabile senza ambiente
    configurato (test puri, `--help` degli script)."""
    if name == "_MERMAID_RENDERER_HTML":
        return build_mermaid_renderer_html()
    raise AttributeError(name)


@dataclass(frozen=True)
class MermaidPrerender:
    """SVG post-processato, metriche del testo e geometria misurate nella
    stessa pagina (`None` se la misura è fallita: la figura resta valida)."""

    svg: str
    metrics: SvgMetrics | None
    geometry: GeometryReport | None = None


def _metrics_from_page(raw: object, *, preview: str) -> SvgMetrics | None:
    """`SvgMetrics` dal dizionario `{min, median, count}` della pagina;
    `None` (con warning) se assente o malformato: una misura fallita NON
    degrada la figura."""
    if isinstance(raw, dict):
        count = raw.get("count")
        minimum = raw.get("min")
        median = raw.get("median")
        if count == 0:
            return SvgMetrics(None, None, 0, "no_text")
        if (
            isinstance(count, int)
            and count > 0
            and isinstance(minimum, int | float)
            and minimum > 0
            and isinstance(median, int | float)
        ):
            return SvgMetrics(float(minimum), float(median), count, "measured")
    log.warning("mermaid_font_measure_failed", preview=preview)
    return None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _work_or_zero(value: object) -> int:
    """Lavoro riportato dalla pagina (facoltativo, solo diagnostica): un
    numero finito non negativo, altrimenti 0."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value) if math.isfinite(value) and value >= 0 else 0


def _geometry_from_page(raw: object, *, preview: str) -> GeometryReport | None:
    """`GeometryReport` dal dizionario di `window.__measureSvg`; `None` (con
    warning) se assente o malformato: una misura fallita NON degrada la
    figura. Un salto per tetto (`skipped`) è un report valido con
    `crossings=None`, loggato dal registro che conosce l'`asset_id`."""
    if isinstance(raw, dict):
        edges = _int_or_none(raw.get("edges"))
        segments = _int_or_none(raw.get("segments"))
        skipped = raw.get("skipped")
        defects = raw.get("defects")
        crossings = _int_or_none(raw.get("crossings"))
        pairs = _int_or_none(raw.get("pairs"))
        shape_ok = (
            edges is not None
            and segments is not None
            and isinstance(defects, list)
            and all(isinstance(d, str) for d in defects)
        )
        work = _work_or_zero(raw.get("work"))
        spent = _work_or_zero(raw.get("spent"))
        if shape_ok and isinstance(skipped, str) and skipped:
            return GeometryReport(
                None, None, edges or 0, segments or 0, skipped=skipped, work=work, spent=spent
            )
        if shape_ok and skipped is None and crossings is not None and pairs is not None:
            return GeometryReport(
                crossings=crossings,
                crossing_pairs=pairs,
                edges=edges or 0,
                segments=segments or 0,
                defects=tuple(str(d) for d in (defects or [])),
                work=work,
                spent=spent,
            )
    log.warning("mermaid_geometry_measure_failed", preview=preview)
    return None


async def _prerender_mermaid_batch_async(
    codes: list[str],
) -> list[MermaidPrerender | None]:
    """Implementazione async del pre-render. NON va chiamata direttamente
    dal worker uvicorn — Playwright richiede `subprocess_exec`, che su
    Windows è supportato SOLO da `ProactorEventLoop` (non dal
    SelectorEventLoop che uvicorn può aver impostato). Wrappare via
    `_prerender_mermaid_to_svg_batch` che gira in un thread con loop
    dedicato.

    Renderizza una lista di sorgenti mermaid a SVG con UNA sola
    sessione Playwright headless (~1s startup + ~50-200ms per
    diagramma) e misura nella stessa pagina il corpo dei testi e la
    geometria (`__renderMermaidMeasured`, una `page.evaluate` per figura;
    tetto cumulativo dei segmenti per batch nella pagina). Ritorna lista
    parallela; ogni elemento è `MermaidPrerender(svg, metrics, geometry)`
    o `None` se il rendering ha fallito.
    """
    if not codes:
        return []

    from playwright.async_api import async_playwright

    results: list[MermaidPrerender | None] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await block_external_requests(page)
            await page.set_content(build_mermaid_renderer_html(), wait_until="domcontentloaded")
            try:
                await page.wait_for_function("window.__mermaidReady === true", timeout=15_000)
            except Exception as exc:  # CDN irraggiungibile o pagina non pronta
                log.warning("mermaid_renderer_setup_failed", error=str(exc))
                # Non possiamo renderizzare nulla → tutti None.
                return [None] * len(codes)

            for i, code in enumerate(codes):
                if not (code or "").strip():
                    results.append(None)
                    continue
                try:
                    rendered = await page.evaluate(
                        "([id, code]) => window.__renderMermaidMeasured(id, code)",
                        [f"mmd-{i}", code],
                    )
                    svg = rendered.get("svg") if isinstance(rendered, dict) else None
                    if isinstance(svg, str) and svg.strip():
                        results.append(
                            MermaidPrerender(
                                svg=_join_mermaid_text_newlines(_strip_mermaid_max_width(svg)),
                                metrics=_metrics_from_page(
                                    rendered.get("metrics"), preview=code[:80]
                                ),
                                geometry=_geometry_from_page(
                                    rendered.get("geometry"), preview=code[:80]
                                ),
                            )
                        )
                    else:
                        log.warning(
                            "mermaid_render_returned_empty",
                            preview=code[:80],
                        )
                        results.append(None)
                except Exception as exc:  # errore JS/Playwright: quel diagramma degrada
                    log.warning(
                        "mermaid_render_failed",
                        error=str(exc),
                        preview=code[:80],
                    )
                    results.append(None)
        finally:
            await browser.close()
    return results


def _prerender_mermaid_batch_sync(
    codes: list[str],
) -> list[MermaidPrerender | None]:
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
        return loop.run_until_complete(_prerender_mermaid_batch_async(codes))
    finally:
        with contextlib.suppress(Exception):
            loop.close()


def _project_svgs(rendered: list[MermaidPrerender | None]) -> list[str | None]:
    return [r.svg if r is not None else None for r in rendered]


async def _prerender_mermaid_to_svg_batch_async(
    codes: list[str],
) -> list[str | None]:
    """Nome storico: proiezione `.svg` di `_prerender_mermaid_batch_async`."""
    return _project_svgs(await _prerender_mermaid_batch_async(codes))


def _prerender_mermaid_to_svg_batch_sync(
    codes: list[str],
) -> list[str | None]:
    """Nome storico: proiezione `.svg` di `_prerender_mermaid_batch_sync`
    (script di rivalidazione, re-export di `course_lesson_pdf_service`)."""
    return _project_svgs(_prerender_mermaid_batch_sync(codes))


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


# ---------------------------------------------------------------------------
# Pulizia del sorgente
# ---------------------------------------------------------------------------

# Righe spurie a volte emesse dall'AI nel codice Mermaid: fence markdown
# residuo (```/```mermaid) o nodi-segnaposto isolati come `mermaid` /
# `all` / `all:`. Passano mermaid.parse ma compaiono come box anomali.
# Rimosse solo quando una riga è ESATTAMENTE uno di questi token (non
# tocchiamo archi/nodi reali tipo `A --> all` o `all[Etichetta]`).
# Mirror del sanitizer frontend in MermaidDiagram.tsx. Resta scoped a
# Mermaid: `all` è un nodo legittimo in DOT.
_MERMAID_JUNK_LINE_RE = re.compile(r"^(?:```.*|mermaid|all)\s*:?\s*$", re.IGNORECASE)


def _sanitize_mermaid_code(code: str) -> str:
    if not code:
        return code
    lines = [ln for ln in code.split("\n") if not _MERMAID_JUNK_LINE_RE.match(ln.strip())]
    return "\n".join(lines).strip()
