/**
 * Direzione di una catena lineare Mermaid (D15): la variante verticale.
 *
 * Mirror riga per riga di `backend/app/services/figure_compute/chain_layout.py`,
 * con parità provata dalla fixture condivisa
 * `backend/tests/fixtures/chain_layout_cases.json` (eseguita dai due lati,
 * come `figure_scale_cases.json`).
 *
 * Un flowchart che è una CATENA LINEARE dichiarata in orizzontale
 * (`flowchart LR`) cresce in larghezza e non in altezza: nel box della
 * dispensa (168 × 242 mm) la scala la impone la sola larghezza e il corpo
 * del testo crolla (catena di 12 nodi: 2,28 pt contro 8,59 pt della stessa
 * catena dichiarata `TB`). `verticalChainVariant` riconosce la catena
 * orizzontale e ritorna il sorgente con il SOLO token di direzione cambiato;
 * chi rende decide se usarla misurando le due varianti.
 *
 * Riconoscimento conservativo: nel dubbio `null` e la figura resta com'è.
 */

/** Sotto tre nodi «catena» non significa niente. */
export const MIN_CHAIN_NODES = 3;

/** Direzione verticale corrispondente a ciascuna orizzontale. */
export const VERTICAL_OF: Readonly<Record<string, string>> = {
  LR: "TB",
  RL: "BT",
};

const HEADER_RE = /^([ \t]*(?:flowchart|graph)[ \t]+)(LR|RL)[ \t]*;?[ \t]*\r?\n?$/;
const NODE_ID_RE = /^[A-Za-z0-9_][A-Za-z0-9_.]*/;
const LINK_RE = /^(?:-{2,}>|-{3,})/;

const BAIL_KEYWORDS = new Set(["subgraph", "end", "direction"]);
const NEUTRAL_KEYWORDS = new Set(["classdef", "class", "style", "linkstyle"]);

const SHAPE_OPEN = "[({";
const SHAPE_CLOSE = "])}";

/**
 * Spazi riconosciuti come tali: l'INTERSEZIONE fra quelli di Python e
 * quelli di JavaScript. `String.prototype.trim()` e `\s` tolgono anche
 * U+FEFF, che per Python non è spazio; `str.strip()` toglie U+001C-U+001F
 * e U+0085, che per JavaScript sono caratteri come gli altri. Lasciare a
 * ciascun linguaggio il proprio insieme faceva scegliere due direzioni
 * diverse alla pagina e allo schermo; qui un carattere fuori da questa
 * classe è un carattere qualunque e fa fallire il parse, cioè `null` da
 * tutte e due le parti. Stessa classe, carattere per carattere, in
 * `chain_layout._SPACE_CLASS`.
 */
const SPACE =
  "\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
/** Caratteri che il sanificatore toglie ai bordi: gli spazi comuni ai due
 * linguaggi piu' il BOM (mirror di `chain_layout.SANITIZE_TRIM_CHARS`). Il
 * taglio e' lineare, come lo `strip(chars)` di Python: una regex ancorata
 * su un sorgente al tetto dei 12.000 caratteri costa secondi. */
const SANITIZE_TRIM_CHARS = new Set(
  [
    "\t", "\n", "\v", "\f", "\r", " ", "\u00a0", "\u1680",
    "\u2028", "\u2029", "\u202f", "\u205f", "\u3000", "\ufeff",
  ].concat(Array.from({ length: 11 }, (_, i) => String.fromCharCode(0x2000 + i))),
);

export function sanitizeTrim(text: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && SANITIZE_TRIM_CHARS.has(text[start])) start += 1;
  while (end > start && SANITIZE_TRIM_CHARS.has(text[end - 1])) end -= 1;
  return text.slice(start, end);
}

const LSTRIP_RE = new RegExp(`^[${SPACE}]+`);
const RSTRIP_RE = new RegExp(`[${SPACE}]+$`);
const SPLIT_RE = new RegExp(`[${SPACE}]+`);

/** `String.trim()` ristretto agli spazi comuni ai due linguaggi. */
function strip(text: string): string {
  return text.replace(LSTRIP_RE, "").replace(RSTRIP_RE, "");
}

/** `String.trimEnd()` ristretto agli spazi comuni ai due linguaggi. */
function rstrip(text: string): string {
  return text.replace(RSTRIP_RE, "");
}

/** Prima parola di `text` già ripulito, senza il `;` finale. */
function firstWord(text: string): string {
  return text.split(SPLIT_RE)[0].toLowerCase().replace(/;+$/, "");
}

interface Line {
  offset: number;
  text: string;
}

/**
 * `{offset assoluto, riga con il terminatore}` per ogni riga. Taglia SOLO
 * su `\n`, come `_lines_with_offsets` del Python.
 */
function linesWithOffsets(source: string): Line[] {
  const out: Line[] = [];
  let start = 0;
  while (start < source.length) {
    const newline = source.indexOf("\n", start);
    const end = newline < 0 ? source.length : newline + 1;
    out.push({ offset: start, text: source.slice(start, end) });
    start = end;
  }
  return out;
}

/**
 * `%%` è un commento; `%%{ … }%%` è una DIRETTIVA, che può riscrivere la
 * configurazione (direzione compresa) e ferma il riconoscimento.
 */
function isComment(text: string): boolean {
  return text.startsWith("%%") && !text.startsWith("%%{");
}

function skippable(line: string): boolean {
  const text = strip(line);
  return text === "" || isComment(text);
}

interface Header {
  index: number;
  start: number;
  end: number;
  direction: string;
}

/** Indice della riga e span assoluto del token di direzione orizzontale. */
function header(lines: Line[]): Header | null {
  let i = 0;
  while (i < lines.length && skippable(lines[i].text)) i += 1;
  if (i < lines.length && strip(lines[i].text) === "---") {
    // Frontmatter YAML: si salta fino al `---` di chiusura.
    i += 1;
    while (i < lines.length && strip(lines[i].text) !== "---") i += 1;
    if (i >= lines.length) return null;
    i += 1;
    while (i < lines.length && skippable(lines[i].text)) i += 1;
  }
  if (i >= lines.length) return null;
  const { offset, text } = lines[i];
  const m = HEADER_RE.exec(text);
  if (!m) return null;
  const start = offset + m[1].length;
  return { index: i, start, end: start + m[2].length, direction: m[2] };
}

/**
 * Indice dopo la forma che comincia in `start` (`[`, `(`, `{` o la forma
 * asimmetrica `>etichetta]`), `-1` se non si chiude. Le virgolette
 * proteggono il contenuto.
 */
function skipShape(text: string, start: number): number {
  let depth = 0;
  let quote = "";
  let i = start;
  while (i < text.length) {
    const ch = text[i];
    if (quote) {
      if (ch === quote) quote = "";
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (SHAPE_OPEN.includes(ch) || (ch === ">" && depth === 0)) {
      depth += 1;
    } else if (SHAPE_CLOSE.includes(ch)) {
      depth -= 1;
      if (depth === 0) return i + 1;
      if (depth < 0) return -1;
    }
    i += 1;
  }
  return -1;
}

/** `[id, indice dopo il nodo]`, `null` se lì non c'è un nodo. */
function scanNode(text: string, start: number): [string, number] | null {
  const m = NODE_ID_RE.exec(text.slice(start));
  if (!m) return null;
  let end = start + m[0].length;
  if (end < text.length && (SHAPE_OPEN.includes(text[end]) || text[end] === ">")) {
    end = skipShape(text, end);
    if (end < 0) return null;
  }
  return [m[0], end];
}

/**
 * Legge `A`, `A["x"]`, `A --> B`, `A --> B --> C` accumulando nodi e archi;
 * `false` appena incontra qualcosa che non è questa forma.
 */
function parseStatement(
  text: string,
  nodes: string[],
  edges: [string, string][],
): boolean {
  let i = 0;
  let previous: string | null = null;
  for (;;) {
    while (i < text.length && (text[i] === " " || text[i] === "\t")) i += 1;
    const scanned = scanNode(text, i);
    if (scanned === null) return false;
    const [nodeId, next] = scanned;
    i = next;
    if (!nodes.includes(nodeId)) nodes.push(nodeId);
    if (previous !== null) {
      if (previous === nodeId) return false; // cappio
      edges.push([previous, nodeId]);
    }
    previous = nodeId;
    while (i < text.length && (text[i] === " " || text[i] === "\t")) i += 1;
    if (i >= text.length) return true;
    const link = LINK_RE.exec(text.slice(i));
    if (!link) return false;
    i += link[0].length;
  }
}

/** Un solo componente connesso, guardando gli archi come non orientati. */
function connected(nodes: string[], edges: [string, string][]): boolean {
  const parent = new Map<string, string>(nodes.map((n) => [n, n]));
  const find = (node: string): string => {
    let root = node;
    while (parent.get(root) !== root) root = parent.get(root) as string;
    let walk = node;
    while (parent.get(walk) !== root) {
      const next = parent.get(walk) as string;
      parent.set(walk, root);
      walk = next;
    }
    return root;
  };
  for (const [a, b] of edges) {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  }
  return new Set(nodes.map(find)).size === 1;
}

/**
 * I nodi e gli archi letti formano una catena: almeno `MIN_CHAIN_NODES`
 * nodi, `n-1` archi distinti, grado entrante e uscente al più 1, un solo
 * componente connesso.
 */
export function isLinearChain(
  nodes: string[],
  edges: [string, string][],
): boolean {
  if (nodes.length < MIN_CHAIN_NODES || edges.length !== nodes.length - 1) {
    return false;
  }
  const seen = new Set(edges.map(([a, b]) => `${a}\u0000${b}`));
  if (seen.size !== edges.length) return false;
  const incoming = new Map<string, number>();
  const outgoing = new Map<string, number>();
  for (const [a, b] of edges) {
    const out = (outgoing.get(a) ?? 0) + 1;
    const inc = (incoming.get(b) ?? 0) + 1;
    outgoing.set(a, out);
    incoming.set(b, inc);
    if (out > 1 || inc > 1) return false;
  }
  return connected(nodes, edges);
}

/**
 * Il sorgente con la direzione verticale se è una catena lineare dichiarata
 * in orizzontale, altrimenti `null`. Cambia SOLO il token della direzione:
 * nessun altro byte si muove.
 */
export function verticalChainVariant(source: string): string | null {
  if (!source) return null;
  const lines = linesWithOffsets(source);
  const head = header(lines);
  if (head === null) return null;
  const nodes: string[] = [];
  const edges: [string, string][] = [];
  for (const line of lines.slice(head.index + 1)) {
    let text = strip(line.text);
    if (text === "" || isComment(text)) continue;
    if (text.startsWith("%%{")) return null;
    const first = firstWord(text);
    if (BAIL_KEYWORDS.has(first)) return null;
    if (NEUTRAL_KEYWORDS.has(first)) continue;
    if (text.endsWith(";")) {
      text = rstrip(text.slice(0, -1));
      if (text === "") continue;
    }
    if (!parseStatement(text, nodes, edges)) return null;
  }
  if (!isLinearChain(nodes, edges)) return null;
  return (
    source.slice(0, head.start) +
    VERTICAL_OF[head.direction] +
    source.slice(head.end)
  );
}
