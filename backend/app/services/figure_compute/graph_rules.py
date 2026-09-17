"""Gate editoriale dei grafi (D13, D14): densità misurata sul sorgente (puro).

Gemello formale di `vegalite_rules`: costanti `MAX_*` pubbliche e una
funzione che ritorna l'elenco delle violazioni (vuoto = conforme). Vale per
i formati che disegnano grafi, Mermaid e Graphviz DOT; Vega-Lite ha le
proprie regole, `function` non è un grafo.

Le soglie sono EDITORIALI e distinte dai tetti di risorsa del registro
(`figure_render_service.DOT_MAX_EDGES`, `settings.figure_dot_max_chars`,
`VEGALITE_MAX_CHARS`, `course_lesson_content.VISUAL_ASSET_CONTENT_MAX_CHARS`):
un tetto di risorsa protegge il server, una soglia editoriale dice che la
figura, così com'è, non si legge. Ogni messaggio comincia con
`graph_too_dense:` seguito da `<cosa> <n> > <max>` e da che cosa ridurre,
perché arriva al fix AI (`openai_asset_fix_service`) e al 422 del docente.
Il worker tratta ogni errore della validazione come recuperabile e
rigenera la lezione intera: per questo le soglie sono tarate perché nessuna
figura normale le superi.

Soglie PROVVISORIE, calibrate il 16 settembre 2026 sui 57 modelli degli
editor (15 Mermaid, 18 DOT, 24 Vega-Lite; tabella 2(d) del piano), con
margine ≥ 1,4× sul massimo osservato: nodi 10 → 30, archi 8 → 45, etichetta
31 → 64, titolo 73 → 110, sorgente Mermaid 388 → 3.000, righe 39 → 120,
incroci 1 (`hashTable`) → 4. Da riconfermare su figure reali con
`scripts/measure_asset_refs.py --figures`; se il p90 reale supera il 60 %
di una soglia, la soglia si alza, mai si boccia il contenuto.

`MAX_EDGE_CROSSINGS` è una soglia DIAGNOSTICA, non un rifiuto, per entrambi
i formati: gli incroci misurati sulla figura resa (`figure_geometry`)
oltre soglia diventano la voce `graph_too_dense: incroci fra archi …` fra
i difetti della figura, il warning `figure_geometry_defects` e una riga
del report dell'export. Un grafo a strati completi, normale in un corso
(percettrone multistrato 3-3-2: 8 incroci; 3-4-2: 16; K3,3: 7), ha
incroci che nessun ordine dei nodi toglie, e il fix AI non può togliere
archi: un rifiuto rigenererebbe la lezione fino all'esaurimento dei
tentativi. Le soglie sul sorgente restano i soli rifiuti.

Conteggi sul SORGENTE, regole per tipo
--------------------------------------
- flowchart / graph e block-beta: nodi = id distinti (dichiarati o estremi
  di un collegamento), archi = collegamenti (una catena `A --> B --> C`
  vale 2, `A & B --> C` vale 2), etichette = testo delle forme, dei
  collegamenti (`-->|x|`, `-- x -->`) e dei subgraph, più l'id dei nodi
  senza forma (è il testo reso);
- sequenceDiagram: nodi = partecipanti, archi = messaggi, etichette =
  alias, testi dei messaggi, note e blocchi (`loop`, `alt`, …);
- stateDiagram: nodi = stati (`[*]` compreso), archi = transizioni,
  etichette = descrizioni, testi delle transizioni e note;
- classDiagram: nodi = classi, archi = relazioni, etichette = membri,
  testi delle relazioni e note;
- erDiagram: nodi = entità, archi = relazioni, etichette = nomi delle
  entità, attributi e testi delle relazioni;
- mindmap e treemap: nodi = righe, archi = nodi − 1;
- timeline: nodi = sezioni e periodi, archi 0, etichette = sezioni,
  periodi ed eventi;
- pie: nodi = fette; quadrantChart: nodi = punti; radar: nodi = assi;
  xychart: nodi = categorie dell'asse x (una serie numerica lunga non
  affolla il grafico); archi 0;
- sankey: nodi = nomi distinti, archi = righe CSV;
- gantt: nodi = attività, archi = riferimenti `after`.
- DOT: nodi = id distinti negli statement (le porte `n:p` valgono `n`),
  archi = coppie generate dagli statement d'arco come in Graphviz: un
  operando sottografo (`{b c}`, `subgraph s { … }`) vale i nodi nominati al
  suo interno, una catena somma i prodotti degli operandi adiacenti
  (`{a b} -> {c d} -> e` vale 6), in un grafo `strict` una coppia ripetuta
  (non orientata per `graph`) conta una volta; etichette = valori di
  `label`/`xlabel`/`headlabel`/`taillabel` (i campi dei record separati, i
  tag delle etichette HTML tolti) e id dei nodi senza `label`; titolo =
  `label` del grafo al primo livello. Punto cieco: `subgraph s` senza
  corpo (rimando a un sottografo già definito) vale zero nodi. Il tetto di
  risorsa del registro (`DOT_MAX_EDGES`) conta invece gli operatori.
Per ogni etichetta conta la riga più lunga (`\\n`, `\\l`, `\\r`, a capo
reali); il titolo (`title`, frontmatter, `label` del grafo) ha una soglia
propria. Righe = righe non vuote; caratteri = lunghezza del sorgente
sanificato (solo Mermaid: il tetto DOT è di risorsa).

Etichette che Mermaid manda a capo da solo
------------------------------------------
Dove Mermaid 11 spezza il testo sulla larghezza (verifica in Chromium del
17 settembre 2026, etichette di 84 e 92 caratteri rese su più righe; il
test `test_mermaid_really_wraps_only_the_exempted_contexts` la ripete) la riga
del sorgente non è la riga resa: lì `MAX_LABEL_CHARS` vale per la PAROLA
più lunga, non per la riga. Contesti: flowchart (testo delle forme, anche
fra virgolette, markdown e `@{ label }`, e testo dei collegamenti),
stateDiagram (tutte le etichette), mindmap (tutti i nodi), timeline
(sezioni, periodi, eventi), classDiagram (testo delle relazioni e note),
erDiagram (testo delle relazioni), sequenceDiagram (etichette dei blocchi
`loop`, `alt`, `else`, `opt`, `par`, `and`, `critical`, `option`,
`break`). Restano a riga intera, perché resi su una riga sola: i titoli
dei subgraph e l'id di un nodo senza forma, block-beta, i messaggi, le
note, gli alias e `box` della sequence, i membri e i nomi delle classi,
attributi e nomi delle entità ER, treemap, gantt, pie, quadrant, radar,
xychart, sankey, e tutto il DOT (Graphviz non va a capo da solo).

Costo della misura
------------------
La misura gira nel PATCH e nel worker PRIMA del confronto con le soglie,
quindi il suo costo è limitato per costruzione, non dal tetto editoriale:
- il sorgente Mermaid è letto al più per `MAX_MERMAID_MEASURED_CHARS`
  caratteri (pari al tetto A1 dello schema: un sorgente ammesso è letto
  intero); oltre, nodi, archi ed etichette sono quelli del prefisso,
  righe e caratteri quelli del sorgente intero, che è comunque già oltre
  `MAX_MERMAID_SOURCE_CHARS`;
- le regex che su una corsa di caratteri ripetevano il lavoro (due
  quantificatori che se la contendono prima di un'ancora che fallisce, o
  una ricerca che riparte da ogni posizione della corsa) hanno
  quantificatori possessivi o un lookbehind sull'inizio della corsa; la
  relazione di classe, quadratica con i quantificatori pigri su una corsa
  di `-`, è letta da `_class_relation` con le posizioni di spazi e `:`
  indicizzate una volta sola;
- nel tokenizer DOT, dopo la prima stringa non chiusa nessuna virgoletta
  successiva può chiuderne una, e la ricerca della stringa si spegne; i
  tag delle etichette sono cercati solo fino all'ultimo `>`;
- un riferimento numerico HTML oltre il limite di cifre degli interi di
  Python lascia l'etichetta com'è invece di sollevare `ValueError`;
- le righe Mermaid sono spezzate anche su un `\\r` isolato, come fa
  `cleanupText`: il CSV del sankey non vede mai un a capo dentro un campo
  (`csv.Error`, che nel PATCH diventava un 500).
Il gate resta lineare anche su un sorgente patologico al tetto dello schema
(`tests/test_graph_rules_cost.py`).
"""

from __future__ import annotations

import contextlib
import csv
import html
import re
from bisect import bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from itertools import pairwise

MAX_GRAPH_NODES = 30
MAX_GRAPH_EDGES = 45
MAX_LABEL_CHARS = 64
MAX_TITLE_CHARS = 110
MAX_MERMAID_SOURCE_CHARS = 3_000
MAX_GRAPH_LINES = 120
MAX_EDGE_CROSSINGS = 4
# Tetto di RISORSA della lettura Mermaid (docstring, «Costo della misura»):
# uguale a `course_lesson_content.VISUAL_ASSET_CONTENT_MAX_CHARS`, che il
# modulo, puro, non importa (un test li confronta).
MAX_MERMAID_MEASURED_CHARS = 12_000

GRAPH_TOO_DENSE = "graph_too_dense"
GRAPH_FORMATS: tuple[str, ...] = ("mermaid", "dot")  # formati con archi

# Clausola in coda al messaggio: il system prompt del fix AI vieta di
# togliere contenuti; per una soglia editoriale semplificare è proprio la
# correzione richiesta.
_SIMPLIFY = "qui semplificare è la correzione richiesta: mantieni tipo e significato"
_LABEL_PREVIEW = 40


@dataclass(frozen=True)
class GraphSourceMetrics:
    """Misure di un sorgente. `kind` è il tipo Mermaid canonico (`flowchart`,
    `sequenceDiagram`, …) o `dot`; `longest_label` è la riga d'etichetta più
    lunga, o la parola più lunga dove il renderer va a capo da solo (per il
    messaggio); `label_chars` è la sua lunghezza."""

    kind: str
    nodes: int
    edges: int
    label_chars: int
    longest_label: str
    title_chars: int
    lines: int
    chars: int


@dataclass
class _Acc:
    nodes: dict[str, None] = field(default_factory=dict)
    edges: int = 0
    labels: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    extra_nodes: int = 0
    # Etichette che il renderer manda a capo da solo (docstring del modulo).
    wrapped: list[str] = field(default_factory=list)

    def node(self, name: str) -> None:
        name = name.strip()
        if name:
            self.nodes.setdefault(name, None)

    def label(self, text: str, *, wrap: bool = False) -> None:
        text = text.strip()
        if text:
            (self.wrapped if wrap else self.labels).append(text)


_LINE_BREAK_RE = re.compile(r"\\[nlr]|\r\n?|\n|<br\s*/?>", re.IGNORECASE)


def _unescape(text: str) -> str:
    """`html.unescape` che non solleva: `&#` seguito da più cifre del limite
    di conversione degli interi (4.300) dà `ValueError`, e il testo resta
    com'è (un'etichetta così lunga è comunque oltre soglia)."""
    with contextlib.suppress(ValueError):
        return html.unescape(text)
    return text


def _label_lines(text: str) -> Iterator[str]:
    for line in _LINE_BREAK_RE.split(text):
        clean = _unescape(line).strip().strip('"`').strip()
        if clean:
            yield clean


def _longest(texts: Iterable[str]) -> str:
    best = ""
    for text in texts:
        for line in _label_lines(text):
            if len(line) > len(best):
                best = line
    return best


def _longest_word(texts: Iterable[str]) -> str:
    best = ""
    for text in texts:
        for line in _label_lines(text):
            for word in line.split():
                if len(word) > len(best):
                    best = word
    return best


def _metrics(kind: str, acc: _Acc, source: str) -> GraphSourceMetrics:
    longest = max(_longest(acc.labels), _longest_word(acc.wrapped), key=len)
    title = _longest(acc.titles)
    return GraphSourceMetrics(
        kind=kind,
        nodes=len(acc.nodes) + acc.extra_nodes,
        edges=acc.edges,
        label_chars=len(longest),
        longest_label=longest,
        title_chars=len(title),
        lines=sum(1 for line in source.splitlines() if line.strip()),
        chars=len(source),
    )


# ---------------------------------------------------------------------------
# Mermaid: righe utili, tipo, frontmatter
# ---------------------------------------------------------------------------

_MERMAID_ALIASES = {
    "graph": "flowchart",
    "stateDiagram": "stateDiagram-v2",
    "classDiagram-v2": "classDiagram",
}
_FRONTMATTER_TITLE_RE = re.compile(r"^\s*title\s*:\s*(.*)$")
_TITLE_RE = re.compile(r"^title(?:\s+|\s*:\s*)(.*)$", re.IGNORECASE)
_ACC_RE = re.compile(r"^acc(?:Title|Descr)\b", re.IGNORECASE)
# A capo di `cleanupText` (Mermaid 11), applicato prima di ogni parser:
# `\r\n?` diventa `\n`, quindi anche un `\r` isolato chiude la riga.
_MERMAID_CR_RE = re.compile(r"\r\n?")


def _mermaid_body(source: str) -> tuple[list[str], list[str]]:
    """`(righe utili del corpo, titoli del frontmatter)`: niente righe vuote,
    commenti `%%`, voci di accessibilità (anche `accDescr { … }`).

    Le righe sono quelle di Mermaid (a capo `\\n`, `\\r\\n` e `\\r`
    isolato), quindi nessuna riga contiene `\\r` o `\\n`: è la condizione
    per cui `csv.reader` di `_sankey` non solleva."""
    lines = _MERMAID_CR_RE.sub("\n", source).split("\n")
    titles: list[str] = []
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip() == "---":
        j = i + 1
        while j < len(lines) and lines[j].strip() != "---":
            m = _FRONTMATTER_TITLE_RE.match(lines[j])
            if m:
                titles.append(m.group(1))
            j += 1
        i = j + 1
    body: list[str] = []
    in_acc_block = False
    for raw in lines[i:]:
        line = raw.strip()
        if in_acc_block:
            in_acc_block = "}" not in line
            continue
        if not line or line.startswith("%%"):
            continue
        if _ACC_RE.match(line):
            in_acc_block = line.endswith("{")
            continue
        body.append(line)
    return body, titles


def _split_outside_quotes(text: str, seps: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    quote = False
    depth = 0
    for ch in text:
        if ch == '"':
            quote = not quote
        elif not quote and ch in "[({":
            depth += 1
        elif not quote and ch in "])}":
            depth = max(0, depth - 1)
        if ch in seps and not quote and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


# --- flowchart e block ------------------------------------------------------

_FLOW_SKIP = frozenset(
    {"end", "direction", "style", "classdef", "class", "linkstyle", "click", "call", "href"}
)
_BLOCK_SKIP = frozenset({"columns", "space", "end", "style", "classdef", "class"})
# Collegamento del flowchart. Niente `\s*` ai bordi (gli spazi restano negli
# operandi, che sono ripuliti dopo): con `split` un `\s*` iniziale riscorreva
# la corsa di spazi da ogni posizione. Il lookbehind fa partire `\.+-` solo
# dal primo punto di una corsa, per la stessa ragione.
_LINK_RE = re.compile(r"(<?(?:-{2,}|={2,}|-\.+-?|(?<!\.)\.+-)[>xo]?|~{3,})")
_OPEN_LINKS = frozenset({"--", "==", "-."})
_PLACEHOLDER = "\x00"
_SHAPE_LABEL_RE = re.compile(r"\blabel\s*:\s*(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|([^,}]*))")
_NODE_ID_RE = re.compile(r"^\s*([^\s\x00&:@\"\[\](){}<>|]+)")
_SHAPE_TRIM = "[](){}/\\> \t"


def _read_balanced(text: str, start: int) -> int:
    """Indice dopo la parentesi che chiude quella in `text[start]`, con le
    stringhe fra virgolette saltate; fine del testo se non chiusa."""
    depth = 0
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            end = text.find('"', i + 1)
            i = n if end < 0 else end + 1
            continue
        if ch in "[({":
            depth += 1
        elif ch in "])}":
            depth -= 1
            if depth <= 0:
                return i + 1
        i += 1
    return n


def _shape_text(raw: str) -> str:
    inner = raw.strip(_SHAPE_TRIM)
    if inner.startswith('"') and inner.endswith('"') and len(inner) >= 2:
        inner = inner[1:-1]
    return inner


def _extract_flow_labels(stmt: str, acc: _Acc, *, wrap: bool) -> str:
    """Sostituisce testo di forme, stringhe e label `|x|` con un segnaposto
    e le registra come etichette (`wrap`: il renderer le manda a capo)."""
    out: list[str] = []
    i, n = 0, len(stmt)
    while i < n:
        ch = stmt[i]
        prev = stmt[i - 1] if i else " "
        if ch == "@" and stmt.startswith("@{", i):
            end = _read_balanced(stmt, i + 1)
            m = _SHAPE_LABEL_RE.search(stmt[i:end])
            if m:
                acc.label(next(g for g in m.groups() if g is not None), wrap=wrap)
            out.append(_PLACEHOLDER)
            i = end
        elif ch == '"':
            end = stmt.find('"', i + 1)
            end = n if end < 0 else end
            acc.label(stmt[i + 1 : end], wrap=wrap)
            out.append(_PLACEHOLDER)
            i = end + 1
        elif ch in "[({" or (ch == ">" and (prev.isalnum() or prev == "_")):
            end = _read_balanced(stmt, i) if ch != ">" else _read_asymmetric(stmt, i)
            acc.label(_shape_text(stmt[i:end]), wrap=wrap)
            out.append(_PLACEHOLDER)
            i = end
        elif ch == "|":
            end = stmt.find("|", i + 1)
            end = n if end < 0 else end
            acc.label(stmt[i + 1 : end], wrap=wrap)
            out.append(" ")
            i = end + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _read_asymmetric(text: str, start: int) -> int:
    end = text.find("]", start + 1)
    return len(text) if end < 0 else end + 1


def _operand_ids(operand: str, *, split_spaces: bool) -> list[tuple[str, bool]]:
    """`(id, ha una forma)` degli operandi separati da `&` (e da spazi per
    block-beta)."""
    parts = operand.split("&")
    if split_spaces:
        parts = [p for part in parts for p in part.split()]
    out: list[tuple[str, bool]] = []
    for part in parts:
        m = _NODE_ID_RE.match(part)
        if not m:
            continue
        name = m.group(1).split(":::")[0]
        if split_spaces and name.split(":")[0].lower() in _BLOCK_SKIP:
            continue
        name = name.split(":")[0] if split_spaces else name
        if name:
            out.append((name, _PLACEHOLDER in part[m.end() :]))
    return out


def _flowchart(body: list[str], *, block: bool = False) -> _Acc:
    acc = _Acc()
    shaped: set[str] = set()
    skip = _BLOCK_SKIP if block else _FLOW_SKIP
    for line in body[1:]:
        for stmt in _split_outside_quotes(line, ";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            head = stmt.split()[0].lower()
            if head.split(":")[0] in skip:
                continue
            if head == "subgraph" or (block and head.startswith("block")):
                rest = stmt.split(None, 1)[1] if " " in stmt else ""
                _extract_flow_labels(rest, acc, wrap=False)
                continue
            if _TITLE_RE.match(stmt):
                acc.titles.append(_TITLE_RE.match(stmt).group(1))  # type: ignore[union-attr]
                continue
            clean = _extract_flow_labels(stmt, acc, wrap=not block)
            tokens = _LINK_RE.split(clean)
            operands = [tokens[0]]
            i = 1
            while i < len(tokens):
                op = tokens[i].strip()
                if op in _OPEN_LINKS and i + 2 < len(tokens):
                    acc.label(tokens[i + 1].replace(_PLACEHOLDER, ""), wrap=not block)
                    operands.append(tokens[i + 3] if i + 3 < len(tokens) else "")
                    i += 4
                else:
                    operands.append(tokens[i + 1] if i + 1 < len(tokens) else "")
                    i += 2
            groups = [_operand_ids(o, split_spaces=block) for o in operands]
            for ids in groups:
                for name, has_shape in ids:
                    acc.node(name)
                    if has_shape:
                        shaped.add(name)
            for left, right in pairwise(groups):
                acc.edges += max(1, len(left)) * max(1, len(right))
    acc.labels.extend(name for name in acc.nodes if name not in shaped)
    return acc


# --- sequence ------------------------------------------------------------

_SEQ_PARTICIPANT_RE = re.compile(r"^(?:create\s+)?(?:participant|actor)\s+", re.IGNORECASE)
# Alias: il primo `as` fra spazi, cercato solo dall'inizio di una corsa di
# spazi (lookbehind), così la corsa è letta una volta.
_SEQ_ALIAS_RE = re.compile(r"(?<!\s)\s++as\s++(?=\S)", re.IGNORECASE)
# Freccia di un messaggio: la prima occorrenza divide mittente e resto (una
# ricerca lineare, niente quantificatori pigri annidati sulla riga intera).
_SEQ_ARROW_RE = re.compile(r"<<-->>|<<->>|-->>|->>|-->|->|--x|-x|--\)|-\)")
# `\s[^:]++:` e non `\s+[^:]+:`: stesso linguaggio (anche lo spazio è un
# non-`:`), senza due quantificatori che si contendono la corsa di spazi.
_SEQ_NOTE_RE = re.compile(r"^note\s+(?:left of|right of|over)\s[^:]++:\s*(.*)$", re.IGNORECASE)
_SEQ_BLOCKS = ("loop", "alt", "else", "opt", "par", "and", "critical", "option", "break", "box")
_SEQ_UNWRAPPED_BLOCKS = frozenset({"box"})


def _sequence_participant(line: str) -> tuple[str, str] | None:
    """`(nome, testo reso)` di una riga `participant A as Alias` già ripulita
    ai bordi, `None` se la riga non lo è. È la lettura di
    `^(?:create\\s+)?(?:participant|actor)\\s+(.+?)(?:\\s+as\\s+(.+))?$`
    (il nome si ferma al primo `as` fra spazi seguito da testo) senza il
    quantificatore pigro, che riscorreva ogni corsa di spazi."""
    head = _SEQ_PARTICIPANT_RE.match(line)
    if head is None or head.end() == len(line):
        return None
    rest = line[head.end() :]
    alias = _SEQ_ALIAS_RE.search(rest)
    if alias is None:
        return rest, rest
    return rest[: alias.start()], rest[alias.end() :]


def _sequence_message(line: str) -> tuple[str, str, str] | None:
    """`(mittente, destinatario, testo)` di una riga messaggio
    `A->>+B: testo`, `None` se la riga non lo è."""
    arrow = _SEQ_ARROW_RE.search(line)
    if arrow is None or arrow.start() == 0:
        return None
    receiver, colon, text = line[arrow.end() :].partition(":")
    receiver = receiver.strip().lstrip("+-").strip()
    if not colon or not receiver:
        return None
    return line[: arrow.start()].strip(), receiver, text


def _sequence(body: list[str]) -> _Acc:
    acc = _Acc()
    for line in body[1:]:
        low = line.lower()
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif (participant := _sequence_participant(line)) is not None:
            acc.node(participant[0])
            acc.label(participant[1])
        elif (m := _SEQ_NOTE_RE.match(line)) is not None:
            acc.label(m.group(1))
        elif (message := _sequence_message(line)) is not None:
            sender, receiver, text = message
            acc.node(sender)
            acc.node(receiver)
            acc.edges += 1
            acc.label(text)
        elif (head := low.split()[0]) in _SEQ_BLOCKS:
            text = line.split(None, 1)[1] if " " in line else ""
            acc.label(text, wrap=head not in _SEQ_UNWRAPPED_BLOCKS)
    return acc


# --- state ---------------------------------------------------------------

_STATE_AS_RE = re.compile(r'^state\s+"([^"]*)"\s+as\s+(\S+)', re.IGNORECASE)
_STATE_DECL_RE = re.compile(r"^state\s+(\S+)", re.IGNORECASE)
_STATE_DESC_RE = re.compile(r"^([^\s:]+)\s*:\s*(.*)$")
_STATE_NOTE_RE = re.compile(r"^note\s+(?:left|right)\s+of\s[^:]++:\s*(.*)$", re.IGNORECASE)
_STATE_SKIP = frozenset({"direction", "classdef", "class", "style", "--", "}", "end"})


def _state(body: list[str]) -> _Acc:
    acc = _Acc()
    in_note = False
    for line in body[1:]:
        low = line.lower()
        if in_note:
            in_note = low != "end note"
            if in_note:
                acc.label(line, wrap=True)
            continue
        if low.split()[0] in _STATE_SKIP:
            continue
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif (m := _STATE_NOTE_RE.match(line)) is not None:
            acc.label(m.group(1), wrap=True)
        elif low.startswith("note "):
            in_note = True
        elif "-->" in line:
            left, right = line.split("-->", 1)
            target, _, text = right.partition(":")
            acc.node(left)
            acc.node(target)
            acc.edges += 1
            acc.label(text, wrap=True)
        elif (m := _STATE_AS_RE.match(line)) is not None:
            acc.label(m.group(1), wrap=True)
            acc.node(m.group(2))
        elif (m := _STATE_DECL_RE.match(line)) is not None:
            acc.node(m.group(1).rstrip("{").split("<<")[0])
        elif (m := _STATE_DESC_RE.match(line)) is not None:
            acc.node(m.group(1))
            acc.label(m.group(2), wrap=True)
        else:
            acc.node(line.rstrip("{"))
    return acc


# --- class ---------------------------------------------------------------

_CLASS_DECL_RE = re.compile(r"^class\s+([^\s{\[~]+)(?:~[^~]*~)?(?:\[\"([^\"]*)\"\])?")
# Frecce di una relazione nell'ordine in cui la lettura le prova (`-->`
# prima di `--`: l'ordine decide dove comincia la classe di destra).
_CLASS_ARROWS = (
    "<|--",
    "*--",
    "o--",
    "-->",
    "--*",
    "--o",
    "--|>",
    "<--",
    "..>",
    "..|>",
    "<..",
    "<|..",
    "--",
    "..",
)
_CLASS_REL_STARTS = frozenset({'"', *(arrow[0] for arrow in _CLASS_ARROWS)})
_SPACE_RUN_RE = re.compile(r"\s+")
_COLON_RE = re.compile(":")
_CLASS_MEMBER_RE = re.compile(r"^([^\s:]+)\s*:\s*(.+)$")
_CLASS_NOTE_RE = re.compile(r'^note\s+(?:for\s+\S+\s+)?"([^"]*)"', re.IGNORECASE)
_CLASS_SKIP = frozenset(
    {"direction", "classdef", "cssclass", "click", "link", "callback", "style", "namespace", "}"}
)


class _ClassRelation:
    """Lettura di una relazione `A "1" <|-- "*" B : testo` (riga senza a
    capo). È quella della regex
    `^(\\S+?)\\s*(?:"[^"]*"\\s*)?(<freccia>)\\s*(?:"[^"]*"\\s*)?(\\S+?)\\s*(?::\\s*(.*))?$`
    con le frecce di `_CLASS_ARROWS`, stessi gruppi e stessa scelta fra le
    divisioni possibili, ma senza backtracking: la regex, per ogni fine
    candidata della classe di sinistra, riscorreva la classe di destra fino
    alla fine della sua corsa (quadratica su una corsa di `-`). Qui le
    corse di spazi e i `:` sono indicizzati una volta, e ogni candidato
    costa O(log n)."""

    def __init__(self, line: str) -> None:
        self.line = line
        self.size = len(line)
        runs = [(m.start(), m.end()) for m in _SPACE_RUN_RE.finditer(line)]
        self.run_starts = [start for start, _ in runs]
        self.run_ends = [end for _, end in runs]
        self.colons = [m.start() for m in _COLON_RE.finditer(line)]

    def skip_spaces(self, pos: int) -> int:
        """Prima posizione `>= pos` che non è uno spazio (o la fine)."""
        k = bisect_right(self.run_starts, pos) - 1
        if k >= 0 and pos < self.run_ends[k]:
            return self.run_ends[k]
        return pos

    def closing_quote(self, pos: int) -> int:
        """Virgoletta che chiude quella in `pos`, `-1` se manca."""
        return self.line.find('"', pos + 1)

    def target(self, start: int) -> tuple[str, str | None] | None:
        """`(classe di destra, testo)` da `start` (non uno spazio): la classe
        finisce al primo `:` della sua corsa, oppure a fine corsa se dopo
        vengono solo spazi e poi la fine o un `:`."""
        line, size = self.line, self.size
        if start >= size:
            return None
        k = bisect_right(self.run_starts, start)
        stop = self.run_starts[k] if k < len(self.run_starts) else size
        c = bisect_right(self.colons, start)
        if c < len(self.colons) and self.colons[c] < stop:
            return line[start : self.colons[c]], line[self.colons[c] + 1 :].lstrip()
        after = self.skip_spaces(stop)
        if after == size:
            return line[start:stop], None
        if line[after] == ":":
            return line[start:stop], line[after + 1 :].lstrip()
        return None

    def after_arrow(self, pos: int) -> tuple[str, str | None] | None:
        """Classe di destra e testo dopo una freccia che finisce in `pos`:
        prima con la cardinalità fra virgolette, poi senza."""
        start = self.skip_spaces(pos)
        if start < self.size and self.line[start] == '"':
            close = self.closing_quote(start)
            if close >= 0:
                found = self.target(self.skip_spaces(close + 1))
                if found is not None:
                    return found
        return self.target(start)

    def read(self) -> tuple[str, str, str | None] | None:
        """`(classe di sinistra, classe di destra, testo)` o `None`."""
        line, size = self.line, self.size
        first_end = self.run_starts[0] if self.run_starts else size
        for end in range(1, first_end + 1):
            pos = end if end < first_end else self.skip_spaces(end)
            if pos >= size or line[pos] not in _CLASS_REL_STARTS:
                continue
            if line[pos] == '"':
                close = self.closing_quote(pos)
                if close < 0:
                    continue
                pos = self.skip_spaces(close + 1)
            for arrow in _CLASS_ARROWS:
                if line.startswith(arrow, pos):
                    found = self.after_arrow(pos + len(arrow))
                    if found is not None:
                        return line[:end], found[0], found[1]
        return None


def _class_relation(line: str) -> tuple[str, str, str | None] | None:
    return _ClassRelation(line).read()


def _class(body: list[str]) -> _Acc:
    acc = _Acc()
    in_body = False
    for line in body[1:]:
        if in_body:
            if line.startswith("}"):
                in_body = False
            elif not line.startswith("<<"):
                acc.label(line)
            continue
        head = line.split()[0].lower()
        if head in _CLASS_SKIP or line.startswith("<<"):
            continue
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif (m := _CLASS_NOTE_RE.match(line)) is not None:
            acc.label(m.group(1), wrap=True)
        elif (m := _CLASS_DECL_RE.match(line)) is not None:
            acc.node(m.group(1))
            acc.label(m.group(2) or m.group(1))
            in_body = line.rstrip().endswith("{")
        elif (relation := _class_relation(line)) is not None:
            left, right, text = relation
            acc.node(left)
            acc.node(right)
            acc.edges += 1
            acc.label(text or "", wrap=True)
        elif (m := _CLASS_MEMBER_RE.match(line)) is not None:
            acc.node(m.group(1))
            acc.label(m.group(2))
    acc.labels.extend(acc.nodes)
    return acc


# --- er ------------------------------------------------------------------

# Quantificatori possessivi dove cedere caratteri non può dare una lettura
# diversa: con quelli avidi la regex del blocco era cubica su `A[` seguito
# da spazi, quella della relazione quadratica su una corsa di `-`. La
# cardinalità della relazione è una corsa intera che contiene `--` o `..`.
_ER_REL_RE = re.compile(r"^(\S++)\s++((?=\S*?(?:--|\.\.))\S++)\s++(\S+)\s*+:\s*(.*)$")
_ER_BLOCK_RE = re.compile(r"^([^\s{\[]++)\s*+(?:\[\s*+\"?+([^\]\"]*+)\"?\s*+\])?\s*+\{\s*+$")


def _er(body: list[str]) -> _Acc:
    acc = _Acc()
    in_block = False
    for line in body[1:]:
        if in_block:
            if line.startswith("}"):
                in_block = False
            else:
                acc.label(" ".join(line.split()[:2]))
                comment = re.search(r'"([^"]*)"', line)
                if comment:
                    acc.label(comment.group(1))
            continue
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif (m := _ER_REL_RE.match(line)) is not None:
            acc.node(m.group(1))
            acc.node(m.group(3))
            acc.edges += 1
            acc.label(m.group(4), wrap=True)
        elif (m := _ER_BLOCK_RE.match(line)) is not None:
            acc.node(m.group(1))
            acc.label(m.group(2) or "")
            in_block = True
        elif line.split()[0].lower() not in ("direction", "style", "classdef", "class"):
            acc.node(line.split()[0])
    acc.labels.extend(acc.nodes)
    return acc


# --- mindmap e treemap ---------------------------------------------------

_MINDMAP_SHAPE_RE = re.compile(
    r"^[^\s()\[\]{}]*?(\(\(|\)\)|\(|\)|\[|\{\{)(.*?)(\)\)|\(\(|\)|\(|\]|\}\})$"
)
# Valore in coda `: 12`; il lookbehind fa partire la ricerca solo
# dall'inizio di una corsa di spazi (con `sub` la corsa era riscorsa da
# ogni sua posizione).
_TREE_VALUE_RE = re.compile(r"(?<!\s)\s*+:\s*+[-+.\d]++\s*+$")


def _outline(body: list[str], *, treemap: bool) -> _Acc:
    acc = _Acc()
    count = 0
    for line in body[1:]:
        if line.startswith("::") or line.startswith(":::") or line.lower().startswith("classdef"):
            continue
        text = line.split(":::")[0].strip()
        if treemap:
            text = _TREE_VALUE_RE.sub("", text)
        else:
            m = _MINDMAP_SHAPE_RE.match(text)
            if m:
                text = m.group(2)
        acc.label(text, wrap=not treemap)
        count += 1
    acc.extra_nodes = count
    acc.edges = max(0, count - 1)
    return acc


# --- timeline, pie, quadrant, radar, xychart -----------------------------


def _timeline(body: list[str]) -> _Acc:
    acc = _Acc()
    for line in body[1:]:
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
            continue
        if line.lower().startswith("section "):
            acc.extra_nodes += 1
            acc.label(line.split(None, 1)[1], wrap=True)
            continue
        parts = [p.strip() for p in line.split(":")]
        if parts[0]:
            acc.extra_nodes += 1
        for part in parts:
            acc.label(part, wrap=True)
    return acc


_PIE_HEAD_RE = re.compile(r"^pie(?:\s+showData)?(?:\s+title\s+(.*))?$", re.IGNORECASE)
_PIE_SLICE_RE = re.compile(r'^"([^"]*)"\s*:\s*[-+.\d]+')


def _pie(body: list[str]) -> _Acc:
    acc = _Acc()
    head = _PIE_HEAD_RE.match(body[0])
    if head and head.group(1):
        acc.titles.append(head.group(1))
    for line in body[1:]:
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif (m := _PIE_SLICE_RE.match(line)) is not None:
            acc.extra_nodes += 1
            acc.label(m.group(1))
    return acc


_QUADRANT_AXIS_RE = re.compile(r"^[xy]-axis\s+(.*)$", re.IGNORECASE)
_QUADRANT_LABEL_RE = re.compile(r"^quadrant-[1-4]\s+(.*)$", re.IGNORECASE)
_QUADRANT_POINT_RE = re.compile(r":\s*\[")


def _quadrant(body: list[str]) -> _Acc:
    acc = _Acc()
    for line in body[1:]:
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif (m := _QUADRANT_AXIS_RE.match(line)) is not None:
            for part in m.group(1).split("-->"):
                acc.label(part)
        elif (m := _QUADRANT_LABEL_RE.match(line)) is not None:
            acc.label(m.group(1))
        elif (m := _QUADRANT_POINT_RE.search(line)) is not None and m.start() > 0:
            acc.extra_nodes += 1
            acc.label(line[: m.start()].split(":::")[0])
    return acc


_RADAR_ITEM_RE = re.compile(r"([^\s,\[\]{}]+)\s*(?:\[\s*\"([^\"]*)\"\s*\])?")


def _radar(body: list[str]) -> _Acc:
    acc = _Acc()
    for line in body[1:]:
        low = line.lower()
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif low.startswith("axis "):
            for item in _split_outside_quotes(line.split(None, 1)[1], ","):
                m = _RADAR_ITEM_RE.search(item)
                if m:
                    acc.extra_nodes += 1
                    acc.label(m.group(2) or m.group(1))
        elif low.startswith("curve "):
            for item in _split_outside_quotes(line.split(None, 1)[1], ","):
                m = _RADAR_ITEM_RE.search(item.split("{")[0])
                if m:
                    acc.label(m.group(2) or m.group(1))
    return acc


_XY_AXIS_RE = re.compile(r"^x-axis\s*(?:\"([^\"]*)\"|([^\[\s\"]+))?\s*(?:\[(.*)\])?", re.IGNORECASE)
_Y_AXIS_RE = re.compile(r"^y-axis\s*\"([^\"]*)\"", re.IGNORECASE)


def _xychart(body: list[str]) -> _Acc:
    acc = _Acc()
    for line in body[1:]:
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif (m := _XY_AXIS_RE.match(line)) is not None:
            acc.label(m.group(1) or "")
            if m.group(3) is not None:
                for item in _split_outside_quotes(m.group(3), ","):
                    if item.strip():
                        acc.extra_nodes += 1
                        acc.label(item)
        elif (m := _Y_AXIS_RE.match(line)) is not None:
            acc.label(m.group(1))
    return acc


# --- sankey e gantt ------------------------------------------------------


def _sankey(body: list[str]) -> _Acc:
    """Righe CSV del corpo. `csv.reader` (non `strict`) solleva solo per un
    a capo dentro un campo non quotato, escluso da `_mermaid_body`, o per
    un campo oltre `csv.field_size_limit()` (131.072), escluso dal tetto
    di lettura `MAX_MERMAID_MEASURED_CHARS`. La soppressione copre un
    limite abbassato altrove nel processo: le righe lette restano contate
    e la misura non solleva mai (un'eccezione qui è un 500 nel PATCH)."""
    acc = _Acc()
    with contextlib.suppress(csv.Error):
        for row in csv.reader(body[1:]):
            if len(row) < 2:
                continue
            acc.node(row[0])
            acc.node(row[1])
            acc.edges += 1
    acc.labels.extend(acc.nodes)
    return acc


_GANTT_SKIP = (
    "dateformat",
    "axisformat",
    "tickinterval",
    "excludes",
    "includes",
    "todaymarker",
    "weekday",
    "weekend",
    "topaxis",
    "inclusiveenddates",
    "displaymode",
    "click",
)
_GANTT_AFTER_RE = re.compile(r"\bafter\s+([^,]+)")


def _gantt(body: list[str]) -> _Acc:
    acc = _Acc()
    for line in body[1:]:
        low = line.lower()
        if (m := _TITLE_RE.match(line)) is not None:
            acc.titles.append(m.group(1))
        elif low.startswith(_GANTT_SKIP):
            continue
        elif low.startswith("section "):
            acc.label(line.split(None, 1)[1])
        elif ":" in line:
            name, _, spec = line.partition(":")
            acc.extra_nodes += 1
            acc.label(name)
            for m in _GANTT_AFTER_RE.finditer(spec):
                acc.edges += len(m.group(1).split())
    return acc


_MERMAID_PARSERS = {
    "flowchart": _flowchart,
    "sequenceDiagram": _sequence,
    "stateDiagram-v2": _state,
    "classDiagram": _class,
    "erDiagram": _er,
    "timeline": _timeline,
    "pie": _pie,
    "quadrantChart": _quadrant,
    "radar-beta": _radar,
    "xychart-beta": _xychart,
    "sankey-beta": _sankey,
    "gantt": _gantt,
}


_OUTLINE_TYPES = frozenset({"mindmap", "treemap-beta"})
# Tipi (canonici e alias) con una regola di conteggio: un tipo fuori elenco è
# misurato solo su righe, caratteri e titolo.
COUNTED_MERMAID_TYPES: frozenset[str] = frozenset(
    {*_MERMAID_PARSERS, *_OUTLINE_TYPES, "block-beta", *_MERMAID_ALIASES}
)


def mermaid_source_metrics(source: str) -> GraphSourceMetrics:
    """Misure di un sorgente Mermaid già sanificato (tipo ignoto: solo
    righe, caratteri e titolo). Il tetto di lettura si applica prima di
    ogni altro lavoro."""
    body, titles = _mermaid_body(source[:MAX_MERMAID_MEASURED_CHARS])
    first = body[0].split()[0].rstrip(";") if body else ""
    kind = _MERMAID_ALIASES.get(first, first)
    if kind == "block-beta":
        acc = _flowchart(body, block=True)
    elif kind in _OUTLINE_TYPES:
        acc = _outline(body, treemap=kind == "treemap-beta")
    elif kind in _MERMAID_PARSERS:
        acc = _MERMAID_PARSERS[kind](body)
    else:
        acc = _Acc()
    acc.titles.extend(titles)
    return _metrics(kind or "?", acc, source)


# ---------------------------------------------------------------------------
# DOT
# ---------------------------------------------------------------------------

_DOT_LABEL_KEYS = frozenset({"label", "xlabel", "headlabel", "taillabel"})
# Un token per alternativa, nell'ordine: commenti (`/* */`, `//`, `#` fino a
# fine riga ovunque, come lo scanner di Graphviz 15), stringa quotata,
# inizio di stringa HTML, operatori d'arco, id, numerale, carattere singolo.
# Le stringhe sono consumate intere: un `//` dentro una label resta testo.
_DOT_COMMENT = r"(?P<comment>/\*.*?(?:\*/|\Z)|//[^\n]*|#[^\n]*)"
_DOT_STRING = r"\"(?:[^\"\\]|\\.)*\""
_DOT_OTHER = (
    r"<"
    r"|->|--"
    r"|[A-Za-z_\x80-\uffff][A-Za-z_0-9\x80-\uffff]*"
    r"|-?(?:\.[0-9]+|[0-9]+\.?[0-9]*)"
    r"|\S"
)
_DOT_TOKEN_RE = re.compile(f"{_DOT_COMMENT}|{_DOT_STRING}|{_DOT_OTHER}", re.DOTALL)
# Dopo una virgoletta che non apre una stringa chiusa, nessuna virgoletta
# successiva ne apre una: la prima scansione ha attraversato tutte le
# successive come `\"`, e da ognuna la scansione ripartirebbe dallo stesso
# punto. Senza l'alternativa della stringa ogni `"` è un carattere singolo
# (lo stesso token di prima) e le virgolette escapate non costano più una
# scansione fino alla fine ciascuna.
_DOT_TOKEN_NO_STRING_RE = re.compile(f"{_DOT_COMMENT}|{_DOT_OTHER}", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]*>")
# Oltre questa profondità di sottografi i blocchi sono saltati senza
# ricorsione (un sorgente di 12.000 caratteri può aprirne 6.000).
_MAX_DOT_DEPTH = 32
# Coppie enumerate per la deduplica di un grafo `strict`: oltre, gli archi
# si contano col prodotto degli operandi (già ben oltre ogni soglia).
_MAX_STRICT_PAIRS = 10_000


def _dot_tokens(source: str) -> list[tuple[str, str]]:
    """Token `(tipo, testo)`: `str` (stringa quotata o HTML, già decodificata),
    `id`, `edge`, `op`. Le concatenazioni `"a" + "b"` sono unite."""
    src = source
    tokens: list[tuple[str, str]] = []
    pos = 0
    pattern = _DOT_TOKEN_RE
    while pos < len(src):
        m = pattern.search(src, pos)
        if m is None:
            break
        text = m.group(0)
        pos = m.end()
        if m.group("comment") is not None:
            continue
        if text == '"':
            pattern = _DOT_TOKEN_NO_STRING_RE
        if text == "<":
            depth, j = 1, pos
            while j < len(src) and depth:
                depth += {"<": 1, ">": -1}.get(src[j], 0)
                j += 1
            tokens.append(("html", src[pos : j - 1]))
            pos = j
        elif text.startswith('"'):
            value = text[1:-1].replace('\\"', '"').replace("\\\n", "")
            if len(tokens) >= 2 and tokens[-1] == ("op", "+") and tokens[-2][0] == "str":
                tokens[-2:] = [("str", tokens[-2][1] + value)]
            else:
                tokens.append(("str", value))
        elif text in ("->", "--"):
            tokens.append(("edge", text))
        elif text[0].isalnum() or text[0] in "_-." or ord(text[0]) >= 0x80:
            tokens.append(("id", text))
        else:
            tokens.append(("op", text))
    return tokens


def _strip_tags(text: str) -> str:
    """`_TAG_RE.sub("", text)` applicata solo fino all'ultimo `>`: dopo,
    nessun `<` apre un tag, e su una corsa di `<` senza chiusura ogni `<`
    riscorreva il resto del testo."""
    end = text.rfind(">") + 1
    return _TAG_RE.sub("", text[:end]) + text[end:]


def _record_fields(text: str) -> list[str]:
    return [_strip_tags(f) for f in re.split(r"(?<!\\)[|{}]", text)]


def _html_lines(text: str) -> list[str]:
    return [_strip_tags(part) for part in re.split(r"<br\s*/?>", text, flags=re.I)]


class _DotParser:
    """Discesa ricorsiva sulla grammatica di Graphviz, tollerante: un
    sorgente malformato produce misure parziali, mai un'eccezione."""

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.i = 0
        self.acc = _Acc()
        self.labeled: set[str] = set()
        # Nodi nominati in ciascun blocco aperto (operandi sottografo).
        self.collectors: list[dict[str, None]] = []
        self.strict = False
        self.directed = True
        self.pairs: set[tuple[str, str]] = set()
        self.counted = 0  # archi contati senza enumerare le coppie

    @property
    def edges(self) -> int:
        return self.counted + len(self.pairs)

    def peek(self, offset: int = 0) -> tuple[str, str]:
        j = self.i + offset
        return self.tokens[j] if j < len(self.tokens) else ("eof", "")

    def is_op(self, text: str, offset: int = 0) -> bool:
        return self.peek(offset) == ("op", text)

    def run(self) -> None:
        header: list[str] = []
        while self.peek()[0] != "eof" and not self.is_op("{"):
            kind, text = self.peek()
            if kind == "id":
                header.append(text.lower())
            self.i += 1
        self.strict = header[:1] == ["strict"]
        self.directed = "digraph" in header[:2]
        if self.is_op("{"):
            self.i += 1
            self.stmt_list(depth=1, shape="")

    def stmt_list(self, *, depth: int, shape: str) -> None:
        while self.peek()[0] != "eof":
            kind, text = self.peek()
            if (kind, text) == ("op", "}"):
                self.i += 1
                return
            if kind == "op" and text in ";,":
                self.i += 1
            elif kind == "id" and text.lower() in ("graph", "node", "edge") and self.is_op("[", 1):
                self.i += 1
                attrs = self.attr_lists()
                shape = self.attr_statement(text.lower(), attrs, depth=depth, shape=shape)
            elif kind in ("id", "str", "html") and self.is_op("=", 1):
                key = text.lower()
                value = self.peek(2)
                self.i += 3
                if key in _DOT_LABEL_KEYS:
                    target = self.acc.titles if depth <= 1 else self.acc.labels
                    target.extend(_dot_texts(value, record=False))
            elif self.starts_subgraph():
                self.subgraph_operand(depth=depth, shape=shape)
            elif kind in ("id", "str", "html"):
                self.node_or_edge(depth=depth, shape=shape)
            else:
                self.i += 1

    def starts_subgraph(self) -> bool:
        kind, text = self.peek()
        return self.is_op("{") or (kind == "id" and text.lower() == "subgraph")

    def attr_lists(self) -> list[tuple[str, tuple[str, str]]]:
        attrs: list[tuple[str, tuple[str, str]]] = []
        while self.is_op("["):
            self.i += 1
            while self.peek()[0] != "eof" and not self.is_op("]"):
                if self.peek()[0] != "op" and self.is_op("=", 1):
                    attrs.append((self.peek()[1].lower(), self.peek(2)))
                    self.i += 3
                else:
                    self.i += 1
            self.i += 1
        return attrs

    def attr_statement(
        self, target: str, attrs: list[tuple[str, tuple[str, str]]], *, depth: int, shape: str
    ) -> str:
        new_shape = shape
        for key, value in attrs:
            if target == "node" and key == "shape":
                new_shape = value[1].lower()
        for key, value in attrs:
            if key not in _DOT_LABEL_KEYS:
                continue
            record = target == "node" and _is_record(new_shape)
            texts = _dot_texts(value, record=record)
            if target == "graph" and depth <= 1:
                self.acc.titles.extend(texts)
            else:
                self.acc.labels.extend(texts)
        return new_shape

    def add_node(self, name: str) -> str:
        """Registra un nodo nel grafo e in ogni blocco aperto."""
        name = name.strip()
        self.acc.node(name)
        if name:
            for bag in self.collectors:
                bag.setdefault(name, None)
        return name

    def connect(self, tails: list[str], heads: list[str]) -> None:
        """Archi fra due operandi adiacenti: il prodotto, deduplicato per
        coppia in un grafo `strict`."""
        product = len(tails) * len(heads)
        if not self.strict or len(self.pairs) + product > _MAX_STRICT_PAIRS:
            self.counted += product
            return
        for tail in tails:
            for head in heads:
                if self.directed or tail <= head:
                    self.pairs.add((tail, head))
                else:
                    self.pairs.add((head, tail))

    def block(self, *, depth: int, shape: str) -> list[str]:
        """`[subgraph [id]] { … }` a partire dal token corrente; ritorna i
        nodi nominati nel blocco (l'operando di un arco)."""
        if not self.is_op("{"):
            self.i += 1  # `subgraph`
            if self.peek()[0] in ("id", "str") and not self.is_op("{"):
                self.i += 1
        if not self.is_op("{"):
            return []
        self.i += 1
        bag: dict[str, None] = {}
        self.collectors.append(bag)
        try:
            if depth + 1 > _MAX_DOT_DEPTH:
                self.skip_block()
            else:
                self.stmt_list(depth=depth + 1, shape=shape)
        finally:
            self.collectors.pop()
        return list(bag)

    def skip_block(self) -> None:
        """Consuma i token fino alla `}` che chiude il blocco aperto, senza
        ricorsione."""
        level = 1
        while level and self.peek()[0] != "eof":
            if self.is_op("{"):
                level += 1
            elif self.is_op("}"):
                level -= 1
            self.i += 1

    def subgraph_operand(self, *, depth: int, shape: str) -> None:
        members = self.block(depth=depth, shape=shape)
        self.edge_tail(members, depth=depth, shape=shape)

    def node_id(self) -> str:
        name = self.peek()[1]
        self.i += 1
        while self.is_op(":") and self.peek(1)[0] in ("id", "str"):
            self.i += 2
        return name

    def node_or_edge(self, *, depth: int, shape: str) -> None:
        name = self.add_node(self.node_id())
        if self.peek()[0] == "edge":
            self.edge_tail([name] if name else [], depth=depth, shape=shape)
            return
        attrs = self.attr_lists()
        node_shape = next((v[1].lower() for k, v in attrs if k == "shape"), shape)
        for key, value in attrs:
            if key in _DOT_LABEL_KEYS:
                self.labeled.add(name)
                self.acc.labels.extend(_dot_texts(value, record=_is_record(node_shape)))

    def edge_tail(self, tails: list[str], *, depth: int, shape: str) -> None:
        while self.peek()[0] == "edge":
            self.i += 1
            heads: list[str] = []
            if self.starts_subgraph():
                heads = self.block(depth=depth, shape=shape)
            elif self.peek()[0] in ("id", "str", "html"):
                name = self.add_node(self.node_id())
                heads = [name] if name else []
            self.connect(tails, heads)
            tails = heads
        for key, value in self.attr_lists():
            if key in _DOT_LABEL_KEYS:
                self.acc.labels.extend(_dot_texts(value, record=False))


def _is_record(shape: str) -> bool:
    return shape in ("record", "mrecord")


def _dot_texts(token: tuple[str, str], *, record: bool) -> list[str]:
    """Righe visibili di un valore di etichetta: i tag HTML tolti (una riga
    per `<br/>`), i campi dei record separati (porte `<p>` tolte)."""
    kind, text = token
    if kind == "html":
        return _html_lines(text)
    return _record_fields(text) if record else [text]


def dot_source_metrics(source: str) -> GraphSourceMetrics:
    """Misure di un sorgente DOT già sanificato."""
    parser = _DotParser(_dot_tokens(source))
    parser.run()
    acc = parser.acc
    acc.edges = parser.edges
    acc.labels.extend(name for name in acc.nodes if name not in parser.labeled)
    return _metrics("dot", acc, source)


# ---------------------------------------------------------------------------
# Regole
# ---------------------------------------------------------------------------


# Contatore per formato (dispatch per tabella, come il registro dei renderer)
# e tetti editoriali del sorgente per i formati che ne hanno uno.
_SOURCE_METRICS = {"mermaid": mermaid_source_metrics, "dot": dot_source_metrics}
_SOURCE_CHAR_LIMITS: dict[str, int] = {"mermaid": MAX_MERMAID_SOURCE_CHARS}


def graph_source_metrics(kind: str, source: str) -> GraphSourceMetrics | None:
    """Misure del sorgente per `kind` in `GRAPH_FORMATS`, altrimenti `None`."""
    measure = _SOURCE_METRICS.get(kind)
    return measure(source) if measure is not None else None


def _violation(what: str, value: int, limit: int, advice: str) -> str:
    return f"{GRAPH_TOO_DENSE}: {what} {value} > {limit} — {advice}"


def format_graph_violations(violations: Iterable[str]) -> str:
    """Un solo messaggio di `validate` per più violazioni, con la clausola
    per il fix AI una volta sola in coda (`""` se l'elenco è vuoto)."""
    items = list(violations)
    return f"{'; '.join(items)} ({_SIMPLIFY})" if items else ""


_CROSSINGS_ADVICE: dict[str, str] = {
    "dot": "riordina i nodi (ordine delle dichiarazioni, `rank=same`, `rankdir`) o togli "
    "gli archi ridondanti che si attraversano",
    "mermaid": "riordina le dichiarazioni dei nodi, prova l'altra direzione del diagramma "
    "(`LR` o `TD`) o togli gli archi ridondanti che si attraversano",
}
_CROSSINGS_ADVICE_DEFAULT = (
    "riordina le dichiarazioni dei nodi o togli gli archi ridondanti che si attraversano"
)


def crossings_violation(crossings: int | None, *, fmt: str) -> str | None:
    """Voce diagnostica per gli incroci fra archi misurati sulla figura resa
    (`None` se entro soglia o non misurati), con il consiglio nella sintassi
    del formato. Non è un rifiuto: finisce fra i difetti della figura."""
    if crossings is None or crossings <= MAX_EDGE_CROSSINGS:
        return None
    return _violation(
        "incroci fra archi",
        crossings,
        MAX_EDGE_CROSSINGS,
        _CROSSINGS_ADVICE.get(fmt, _CROSSINGS_ADVICE_DEFAULT),
    )


def check_graph_rules(
    kind: str, source: str, *, metrics: GraphSourceMetrics | None = None
) -> list[str]:
    """Violazioni editoriali del sorgente (vuoto = conforme). `kind` è il
    formato (`mermaid`, `dot`; altri formati: nessuna regola); `metrics`
    evita di rimisurare un sorgente già misurato."""
    measured = metrics if metrics is not None else graph_source_metrics(kind, source)
    if measured is None:
        return []
    out: list[str] = []
    char_limit = _SOURCE_CHAR_LIMITS.get(kind)
    if char_limit is not None and measured.chars > char_limit:
        out.append(
            _violation(
                "caratteri del sorgente",
                measured.chars,
                char_limit,
                "semplifica la figura o dividila in due figure distinte",
            )
        )
    if measured.nodes > MAX_GRAPH_NODES:
        out.append(
            _violation(
                "nodi",
                measured.nodes,
                MAX_GRAPH_NODES,
                f"riduci i nodi a {MAX_GRAPH_NODES} o meno accorpando quelli secondari "
                "o dividendo la figura in due",
            )
        )
    if measured.edges > MAX_GRAPH_EDGES:
        out.append(
            _violation(
                "archi",
                measured.edges,
                MAX_GRAPH_EDGES,
                f"riduci gli archi a {MAX_GRAPH_EDGES} o meno tenendo solo le relazioni "
                "che il testo discute",
            )
        )
    if measured.label_chars > MAX_LABEL_CHARS:
        preview = measured.longest_label[:_LABEL_PREVIEW]
        out.append(
            _violation(
                "caratteri dell'etichetta",
                measured.label_chars,
                MAX_LABEL_CHARS,
                f"accorcia l'etichetta «{preview}…» e sposta il dettaglio nel testo della lezione",
            )
        )
    if measured.title_chars > MAX_TITLE_CHARS:
        out.append(
            _violation(
                "caratteri del titolo",
                measured.title_chars,
                MAX_TITLE_CHARS,
                "accorcia il titolo della figura (la spiegazione va nella didascalia)",
            )
        )
    if measured.lines > MAX_GRAPH_LINES:
        out.append(
            _violation(
                "righe",
                measured.lines,
                MAX_GRAPH_LINES,
                "riduci nodi, archi e dichiarazioni di stile",
            )
        )
    return out


__all__ = [
    "COUNTED_MERMAID_TYPES",
    "GRAPH_FORMATS",
    "GRAPH_TOO_DENSE",
    "MAX_EDGE_CROSSINGS",
    "MAX_GRAPH_EDGES",
    "MAX_GRAPH_LINES",
    "MAX_GRAPH_NODES",
    "MAX_LABEL_CHARS",
    "MAX_MERMAID_SOURCE_CHARS",
    "MAX_TITLE_CHARS",
    "GraphSourceMetrics",
    "check_graph_rules",
    "crossings_violation",
    "dot_source_metrics",
    "format_graph_violations",
    "graph_source_metrics",
    "mermaid_source_metrics",
]
