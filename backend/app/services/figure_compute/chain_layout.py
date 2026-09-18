"""Direzione di una catena lineare Mermaid (D15): la variante verticale (puro).

Un flowchart che è una CATENA LINEARE dichiarata in orizzontale
(`flowchart LR`) cresce in larghezza e non in altezza: nel box della
dispensa (168 × 242 mm) la scala è imposta dalla sola larghezza e il corpo
del testo crolla. Misurato con il motore reale il 18 settembre 2026:
catena di 8 nodi 1922 × 77 uu → 3,47 pt, catena di 12 nodi 2923 × 62 uu →
2,28 pt, contro 11,00 pt e 8,59 pt delle stesse catene dichiarate `TB`.
La serpentina non è una strada: `direction` dentro un `subgraph` viene
ignorato quando ci sono archi che attraversano il gruppo (due colonne da 6
danno 3023 × 132 uu, 2,21 pt, peggio del `TB`). L'unica leva è la
direzione del grafo.

Questo modulo è la leva, e nient'altro: `vertical_chain_variant` riconosce
la catena orizzontale e ritorna il sorgente con il SOLO token di direzione
cambiato (`LR` → `TB`, `RL` → `BT`), byte per byte identico altrove.
Chi rende decide se usarla MISURANDO le due varianti (`figure_scale`): qui
non c'è nessuna soglia di superficie e nessuna resa.

Il riconoscimento è conservativo — in caso di dubbio `None`, e la figura
resta com'è:
- prima riga utile `flowchart LR|RL` o `graph LR|RL`, con il `;` finale
  facoltativo come nel corpo (frontmatter YAML e commenti `%%` prima sono
  ammessi e non toccati);
- nessun `subgraph`, `end`, `direction` nel corpo;
- archi solo semplici (`-->`, `--->`, `---`): nessun arco etichettato,
  punteggiato, spesso, bidirezionale, e nessun operando multiplo (`&`);
- ogni nodo con al più un arco entrante e uno uscente, nessun cappio;
- un solo componente connesso e almeno `MIN_CHAIN_NODES` nodi.

Un falso positivo del riconoscimento non può alterare il contenuto: la
variante cambia solo la direzione, quindi al massimo costa una resa in più
che la misura poi scarta. Mirror TypeScript in
`frontend/src/lib/chainLayout.ts`, parità provata dalla fixture condivisa
`backend/tests/fixtures/chain_layout_cases.json`.

Modulo leaf: solo libreria standard, nessun import da `app.*`.
"""

from __future__ import annotations

import re

# Sotto tre nodi «catena» non significa niente: due nodi in orizzontale
# stanno larghi quanto due etichette e restano leggibili.
MIN_CHAIN_NODES = 3

# Le due direzioni orizzontali e la verticale corrispondente. `TD` non
# compare: è un alias di `TB` in Mermaid e qui si scrive la forma canonica.
VERTICAL_OF: dict[str, str] = {"LR": "TB", "RL": "BT"}

# Intestazione: `flowchart` o il suo alias storico `graph`, seguiti dal
# token di direzione. Il gruppo 1 è il prefisso, e la sua lunghezza dà
# l'inizio esatto del token da sostituire (il mirror TS non può leggere lo
# span di un gruppo senza il flag `d`).
_HEADER_RE = re.compile(r"([ \t]*(?:flowchart|graph)[ \t]+)(LR|RL)[ \t]*;?[ \t]*\r?\n?\Z")

# Spazi riconosciuti come tali: l'INTERSEZIONE fra quelli di Python e
# quelli di JavaScript. `str.strip()` toglie anche U+001C-U+001F e U+0085,
# che per `String.prototype.trim()` sono caratteri come gli altri;
# `trim()` toglie U+FEFF, che per Python non è spazio. Lasciare a ciascun
# linguaggio il proprio insieme faceva scegliere due direzioni diverse alla
# pagina e allo schermo; qui un carattere fuori da questa classe è un
# carattere qualunque e fa fallire il parse, cioè `None` da tutte e due le
# parti. Stessa classe, carattere per carattere, nel mirror TypeScript.
SPACE_CLASS = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
_SPACE_CLASS = SPACE_CLASS
# Il sanificatore (`mermaid_prerender._sanitize_mermaid_code` e il gemello
# `sanitizeMermaidCode` della vista) toglie ANCHE il BOM: è spazzatura di
# codifica, e `String.trim()` lo toglieva già di suo. Senza, la pagina e lo
# schermo partivano da byte diversi e sceglievano due direzioni diverse.
SANITIZE_TRIM_CLASS = SPACE_CLASS + r"\ufeff"
# Gli stessi caratteri come INSIEME, per `str.strip(chars)`: il taglio dei
# bordi è lineare, mentre una regex ancorata `[...]+\\Z` su un sorgente al
# tetto di 12.000 caratteri costa secondi (misurato dai test di costo).
SANITIZE_TRIM_CHARS = (
    "\t\n\x0b\x0c\r \u00a0\u1680"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_STRIP_RE = re.compile(rf"\A[{_SPACE_CLASS}]+|[{_SPACE_CLASS}]+\Z")
_RSTRIP_RE = re.compile(rf"[{_SPACE_CLASS}]+\Z")
_SPLIT_RE = re.compile(rf"[{_SPACE_CLASS}]+")

# Identificatore di nodo: lettere, cifre, `_` e `.`. Volutamente senza `-`
# (ambiguo con gli archi) e senza caratteri non ASCII: un id fuori da
# questo insieme fa ricadere su `None`.
_NODE_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]*")

# Archi semplici: freccia `-->` (con qualunque numero di trattini) e linea
# aperta `---`. Tutto il resto (`-.->`, `==>`, `--x`, `--o`, `<-->`,
# `-- testo -->`, `-->|testo|`) non è semplice e ferma il riconoscimento.
_LINK_RE = re.compile(r"-{2,}>|-{3,}")

# Prima parola che ferma il riconoscimento: il gruppo cambia la geometria e
# `direction` dentro un gruppo non si comporta come la direzione del grafo.
_BAIL_KEYWORDS = frozenset({"subgraph", "end", "direction"})

# Prima parola di una riga che non disegna nodi né archi: la direzione non
# la tocca, quindi si salta senza rinunciare alla variante.
_NEUTRAL_KEYWORDS = frozenset({"classdef", "class", "style", "linkstyle"})

_SHAPE_OPEN = "[({"
_SHAPE_CLOSE = "])}"


def _lines_with_offsets(source: str) -> list[tuple[int, str]]:
    """`(offset assoluto, riga con il terminatore)` per ogni riga.

    Taglia SOLO su `\\n` (mai `str.splitlines`, che taglia anche su `\\r`,
    `\\x0b`, `\\u2028` e darebbe righe diverse dal mirror TypeScript)."""
    out: list[tuple[int, str]] = []
    start = 0
    total = len(source)
    while start < total:
        newline = source.find("\n", start)
        end = total if newline < 0 else newline + 1
        out.append((start, source[start:end]))
        start = end
    return out


def _strip(text: str) -> str:
    """`str.strip()` ristretto agli spazi comuni ai due linguaggi."""
    return _STRIP_RE.sub("", text)


def _rstrip(text: str) -> str:
    """`str.rstrip()` ristretto agli spazi comuni ai due linguaggi."""
    return _RSTRIP_RE.sub("", text)


def _first_word(text: str) -> str:
    """Prima parola di `text` già ripulito, senza il `;` finale."""
    return _SPLIT_RE.split(text)[0].lower().rstrip(";")


def _is_comment(text: str) -> bool:
    """`%%` è un commento; `%%{ … }%%` è una DIRETTIVA, che può riscrivere
    la configurazione (direzione compresa) e ferma il riconoscimento."""
    return text.startswith("%%") and not text.startswith("%%{")


def _skippable(line: str) -> bool:
    text = _strip(line)
    return not text or _is_comment(text)


def _header(lines: list[tuple[int, str]]) -> tuple[int, int, int, str] | None:
    """`(indice della riga, inizio, fine, direzione)` dell'intestazione
    orizzontale, o `None`. Inizio e fine sono offset assoluti del solo
    token di direzione."""
    i = 0
    total = len(lines)
    while i < total and _skippable(lines[i][1]):
        i += 1
    if i < total and _strip(lines[i][1]) == "---":
        # Frontmatter YAML: si salta fino al `---` di chiusura.
        i += 1
        while i < total and _strip(lines[i][1]) != "---":
            i += 1
        if i >= total:
            return None
        i += 1
        while i < total and _skippable(lines[i][1]):
            i += 1
    if i >= total:
        return None
    offset, raw = lines[i]
    m = _HEADER_RE.match(raw)
    if m is None:
        return None
    start = offset + len(m.group(1))
    return i, start, start + len(m.group(2)), m.group(2)


def _skip_shape(text: str, start: int) -> int:
    """Indice dopo la forma che comincia in `start` (`[`, `(`, `{` o la
    forma asimmetrica `>etichetta]`), `-1` se non si chiude. Le virgolette
    proteggono il contenuto: `A["x]y"]` si chiude dove deve."""
    depth = 0
    quote = ""
    i = start
    total = len(text)
    while i < total:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
        elif ch in _SHAPE_OPEN or (ch == ">" and depth == 0):
            depth += 1
        elif ch in _SHAPE_CLOSE:
            depth -= 1
            if depth == 0:
                return i + 1
            if depth < 0:
                return -1
        i += 1
    return -1


def _scan_node(text: str, start: int) -> tuple[str, int] | None:
    """`(id, indice dopo il nodo)` del riferimento a nodo che comincia in
    `start`, `None` se lì non c'è un nodo riconoscibile."""
    m = _NODE_ID_RE.match(text, start)
    if m is None:
        return None
    end = m.end()
    if end < len(text) and (text[end] in _SHAPE_OPEN or text[end] == ">"):
        end = _skip_shape(text, end)
        if end < 0:
            return None
    return m.group(0), end


def _parse_statement(text: str, nodes: list[str], edges: list[tuple[str, str]]) -> bool:
    """Legge `A`, `A["x"]`, `A --> B`, `A --> B --> C` accumulando nodi e
    archi; `False` appena incontra qualcosa che non è questa forma."""
    i = 0
    total = len(text)
    previous: str | None = None
    while True:
        while i < total and text[i] in " \t":
            i += 1
        scanned = _scan_node(text, i)
        if scanned is None:
            return False
        node_id, i = scanned
        if node_id not in nodes:
            nodes.append(node_id)
        if previous is not None:
            if previous == node_id:
                return False  # cappio
            edges.append((previous, node_id))
        previous = node_id
        while i < total and text[i] in " \t":
            i += 1
        if i >= total:
            return True
        link = _LINK_RE.match(text, i)
        if link is None:
            return False
        i = link.end()


def _connected(nodes: list[str], edges: list[tuple[str, str]]) -> bool:
    """Un solo componente connesso, guardando gli archi come non orientati."""
    parent = {n: n for n in nodes}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return len({find(n) for n in nodes}) == 1


def is_linear_chain(nodes: list[str], edges: list[tuple[str, str]]) -> bool:
    """I nodi e gli archi letti formano una catena: almeno
    `MIN_CHAIN_NODES` nodi, `n-1` archi distinti, grado entrante e uscente
    al più 1, un solo componente connesso."""
    if len(nodes) < MIN_CHAIN_NODES or len(edges) != len(nodes) - 1:
        return False
    if len(set(edges)) != len(edges):
        return False
    incoming: dict[str, int] = {}
    outgoing: dict[str, int] = {}
    for a, b in edges:
        outgoing[a] = outgoing.get(a, 0) + 1
        incoming[b] = incoming.get(b, 0) + 1
        if outgoing[a] > 1 or incoming[b] > 1:
            return False
    return _connected(nodes, edges)


def vertical_chain_variant(source: str) -> str | None:
    """Il sorgente con la direzione verticale se `source` è una catena
    lineare dichiarata in orizzontale, altrimenti `None`.

    Cambia SOLO il token della direzione: `source[:i] + nuovo + source[j:]`,
    dove `i:j` è lo span del token nell'intestazione. Nessun altro byte si
    muove — né spaziatura, né terminatori di riga, né commenti.
    """
    if not source:
        return None
    lines = _lines_with_offsets(source)
    head = _header(lines)
    if head is None:
        return None
    index, start, end, direction = head
    nodes: list[str] = []
    edges: list[tuple[str, str]] = []
    for _offset, raw in lines[index + 1 :]:
        text = _strip(raw)
        if not text or _is_comment(text):
            continue
        if text.startswith("%%{"):
            return None
        first = _first_word(text)
        if first in _BAIL_KEYWORDS:
            return None
        if first in _NEUTRAL_KEYWORDS:
            continue
        if text.endswith(";"):
            text = _rstrip(text[:-1])
            if not text:
                continue
        if not _parse_statement(text, nodes, edges):
            return None
    if not is_linear_chain(nodes, edges):
        return None
    return source[:start] + VERTICAL_OF[direction] + source[end:]


__all__ = [
    "MIN_CHAIN_NODES",
    "VERTICAL_OF",
    "is_linear_chain",
    "vertical_chain_variant",
]
