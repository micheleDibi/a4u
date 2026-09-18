"""Rimandi testuali e ancore degli asset (D1, D2, D4): modulo puro.

Un tag `[KIND:id]` (`FIG`, `TAB`, `EQ`, `EX`) è **gestito** quando il kind
compare in `numbers` e l'id normalizzato (`_trim(...).lower()`) vi ha un
numero: dopo `append_uncited_asset_refs` + `compute_asset_numbers` ogni
asset dichiarato ne ha uno, quindi «gestito» coincide con «risolvibile».
Un tag non gestito (id irrisolto, `[fig:x]`, kind assente dalla mappa)
resta byte-identico in ogni passo: a valle produce il `missing-asset`
visibile o il blocco di oggi.

`normalize_asset_refs(markdown, *, numbers, reference)`:
- ogni citazione in linea di un tag gestito diventa il rimando testuale
  `reference(kind, id_lower, n)` («Figura 2», «Tabella 1», «Lemma 2»:
  senza punto, la punteggiatura dell'autore resta);
- una riga fatta del solo tag (`ANCHOR_LINE_RE`, spazi e `\\r` tollerati)
  è un'**ancora** e resta byte-identica: è il punto in cui il renderer a
  valle inserisce il blocco; la prima ancora per chiave `(KIND, id_lower)`
  è tenuta, le successive sono rimosse con la riga vuota adiacente;
- se una chiave gestita ha citazioni ma nessuna ancora, UNA ancora
  `[KIND:id]` (id come scritto nella prima citazione, ripulito) è inserita
  su riga propria subito dopo il **blocco** che contiene la prima
  citazione: blocco = righe contigue non vuote; i fence (``` / ~~~) chiusi
  e i blocchi `$$…$$` chiusi (anche con righe vuote interne: `dollarmath`
  ha `allow_blank_lines=True` e remark-math le accetta) sono unità opache;
  se il blocco entra in una lista (prima riga item o continuazione
  indentata, oppure lista che interrompe un paragrafo attaccato), il
  blocco si estende sull'intera lista (altrimenti la lista sciolta viene
  spezzata in due, con `<ol start="2">` su entrambi i parser);
- dentro fence, code span e math i tag sono **citazioni** (riscritte),
  mai ancore: lasciarli intatti produrrebbe a valle un secondo blocco o
  markup escapato nel `<pre>` (`_substitute_asset_refs` e
  `preprocessAssetRefs` sono sostituzioni globali); `- [FIG:a]` è
  citazione (D2 letterale).

`cite_asset_refs(text, *, numbers, reference)`: sola sostituzione dei tag
gestiti con il rimando, ancore comprese, senza rimozioni né inserimenti:
per la coda (punti chiave, riferimenti) che non rende blocchi (C9).

I numeri sono dati (calcolati PRIMA, sul corpo non normalizzato), mai
ricalcolati; il testo fuori dai tag è byte-identico; la funzione è
idempotente. Regex con classi esplicite (`[ \\t]`, `[0-9]`, `[^ \\t\\r\\n]`),
mai `\\s`/`\\d`, `.lower()` senza `IGNORECASE` e `_trim` al posto di
`.strip()`/`.trim()` (classi diverse: vedi `_WS`): stesso esito Python/JS.

Limiti dichiarati: blocchi indentati di 4 spazi non riconosciuti come
codice (ambigui con la continuazione di item); fence con prefisso (`> `,
item con 4+ spazi) e code span multi-riga non riconosciuti; `\\[ … \\]`
LaTeX display non opaco (convertito in `$$` solo a valle); una riga-tag
da sola dentro un HTML block è ancora (blocco dentro l'HTML, come oggi);
fence e `$$` non chiusi non sono regioni (i parser divergono già a
valle); nessuna guardia «parola-etichetta» («Nella Figura [FIG:a]» →
«Nella Figura Figura 1», pinnato in fixture). Un heading con citazione
(`## T [FIG:a]` seguito da un paragrafo attaccato) riceve l'ancora dopo
il paragrafo: nessuna regola dedicata agli heading.

Mantenere allineato con `frontend/src/lib/assetRefNormalize.ts`: la
fixture condivisa `tests/fixtures/asset_ref_normalize_cases.json` fissa i
casi e `tests/test_asset_ref_normalize.py` la esegue su entrambi i lati.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

ASSET_REF_RE = re.compile(r"\[(FIG|TAB|EQ|EX):([^\]\n]+)\]")
ANCHOR_LINE_RE = re.compile(r"^[ \t]*\[(FIG|TAB|EQ|EX):([^\]\n]+)\][ \t]*\r?$")

_BLANK_RE = re.compile(r"^[ \t]*\r?$")
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MATH_OPEN_RE = re.compile(r"^ {0,3}\$\$")
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-+*]|[0-9]{1,9}[.)])(?:[ \t]|\r?$)")
# Marcatore che può INTERROMPERE un paragrafo attaccato: puntato, oppure
# ordinato che comincia da 1 (CommonMark 5.2, «lists can interrupt a
# paragraph only if …»); `2. due` dopo una riga di prosa è continuazione.
_LIST_START_RE = re.compile(r"^[ \t]*(?:[-+*]|1[.)])(?:[ \t]|\r?$)")
_INDENTED_RE = re.compile(r"^(?: {2,}|\t)[^ \t\r\n]")

AssetNumbers = Mapping[str, Mapping[str, int]]
Reference = Callable[[str, str, int], str]

_NO_REGION = -1

# `str.strip()` e `String.trim()` non tolgono gli stessi caratteri:
# `\x1c-\x1f` e `\x85` solo in Python, `\ufeff` (BOM) solo in JavaScript.
# La classe esplicita è l'unione dei due insiemi, così i due lati leggono
# id, righe e tag allo stesso modo; `\A`/`\Z` perché in Python `$` accetta
# anche la posizione prima di un `\n` finale, in JavaScript no.
_WS = (
    r" \t\n\v\f\r\x1c-\x1f\x85\xa0\u1680\u2000-\u200a"
    r"\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_TRIM_RE = re.compile(rf"\A[{_WS}]+|[{_WS}]+\Z")


def _trim(text: str) -> str:
    """`strip()` con la stessa classe del `trim()` del mirror TypeScript."""
    return _TRIM_RE.sub("", text)


def _handled(numbers: AssetNumbers, kind: str, raw_id: str) -> tuple[str, int] | None:
    """`(id_lower, n)` se il tag è gestito, altrimenti `None`."""
    by_id = numbers.get(kind)
    if not by_id:
        return None
    key = _trim(raw_id).lower()
    n = by_id.get(key)
    if n is None:
        return None
    return key, n


def _rewrite_line(line: str, numbers: AssetNumbers, reference: Reference) -> str:
    """Sostituisce in posizione ogni tag gestito con il rimando; i tag non
    gestiti restano byte-identici."""
    out: list[str] = []
    pos = 0
    for m in ASSET_REF_RE.finditer(line):
        handled = _handled(numbers, m.group(1), m.group(2))
        if handled is None:
            continue
        out.append(line[pos : m.start()])
        out.append(reference(m.group(1), handled[0], handled[1]))
        pos = m.end()
    out.append(line[pos:])
    return "".join(out)


def _opaque_regions(lines: list[str]) -> tuple[list[int], list[int]]:
    """Per ogni riga, inizio e fine (inclusiva) della regione opaca che la
    contiene, oppure `-1`: fence chiusi (stesso carattere, marcatore di
    chiusura lungo almeno quanto l'apertura) e blocchi `$$` chiusi (righe
    vuote interne ammesse). Le regioni non si annidano; senza chiusura non
    c'è regione."""
    n = len(lines)
    start = [_NO_REGION] * n
    end = [_NO_REGION] * n
    i = 0
    while i < n:
        line = lines[i]
        close = _NO_REGION
        fence = _FENCE_OPEN_RE.match(line)
        if fence:
            marker = fence.group(1)
            close_re = re.compile(
                "^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + ",}[ \t]*\r?$"
            )
            for j in range(i + 1, n):
                if close_re.match(lines[j]):
                    close = j
                    break
        elif _MATH_OPEN_RE.match(line):
            stripped = _trim(line)
            if len(stripped) > 3 and stripped.endswith("$$"):
                close = i
            else:
                for j in range(i + 1, n):
                    if _trim(lines[j]).endswith("$$"):
                        close = j
                        break
        if close == _NO_REGION:
            i += 1
            continue
        for k in range(i, close + 1):
            start[k] = i
            end[k] = close
        i = close + 1
    return start, end


def _is_list_block(line: str) -> bool:
    return bool(_LIST_ITEM_RE.match(line) or _INDENTED_RE.match(line))


def _block_bounds(
    i: int, lines: list[str], is_blank: list[bool], rstart: list[int], rend: list[int]
) -> int:
    """Indice della riga vuota (o `len(lines)`) che chiude il blocco della
    riga `i`; se il blocco ENTRA in una lista — perché comincia con un item
    o una continuazione indentata, oppure perché una lista interrompe un
    paragrafo attaccato — il confine salta le righe vuote interne alla
    lista. Una lista interrompe un paragrafo solo se comincia con un
    marcatore puntato o con `1.`/`1)` (CommonMark): dopo il primo item
    qualunque marcatore prosegue la lista."""
    n = len(lines)
    first = rstart[i] if rstart[i] != _NO_REGION else i
    while first > 0 and not is_blank[first - 1]:
        prev = first - 1
        first = rstart[prev] if rstart[prev] != _NO_REGION else prev
    list_block = _is_list_block(lines[first])
    j = first
    while True:
        while j < n and not is_blank[j]:
            if not list_block and _LIST_START_RE.match(lines[j]):
                list_block = True
            j = rend[j] + 1 if rend[j] != _NO_REGION else j + 1
        if j >= n or not list_block:
            return j
        k = j
        while k < n and is_blank[k]:
            k += 1
        if k >= n or not _is_list_block(lines[k]):
            return j
        j = k


def normalize_asset_refs(markdown: str, *, numbers: AssetNumbers, reference: Reference) -> str:
    """Vedi la docstring del modulo. `numbers` è `{KIND: {id_lower: N}}`
    (kind assente o vuoto = identità su quel kind); `reference(kind,
    id_lower, n)` produce il rimando testuale."""
    if not markdown:
        return markdown
    lines = markdown.split("\n")
    n = len(lines)
    rstart, rend = _opaque_regions(lines)
    is_blank = [rstart[i] == _NO_REGION and bool(_BLANK_RE.match(lines[i])) for i in range(n)]

    first_anchor: dict[tuple[str, str], int] = {}
    drop: set[int] = set()
    first_cite: dict[tuple[str, str], tuple[int, str]] = {}
    for i, line in enumerate(lines):
        anchor = ANCHOR_LINE_RE.match(line) if rstart[i] == _NO_REGION else None
        if anchor is not None:
            handled = _handled(numbers, anchor.group(1), anchor.group(2))
            if handled is not None:
                key = (anchor.group(1), handled[0])
                if key in first_anchor:
                    drop.add(i)
                else:
                    first_anchor[key] = i
                continue
        for m in ASSET_REF_RE.finditer(line):
            handled = _handled(numbers, m.group(1), m.group(2))
            if handled is None:
                continue
            key = (m.group(1), handled[0])
            if key not in first_cite:
                first_cite[key] = (i, f"[{m.group(1)}:{_trim(m.group(2))}]")

    inserts: dict[int, list[str]] = {}
    for key, (i, tag) in first_cite.items():
        if key in first_anchor:
            continue
        j = _block_bounds(i, lines, is_blank, rstart, rend)
        inserts.setdefault(j, []).append(tag)

    anchor_lines = set(first_anchor.values())
    out: list[str] = []
    skip_blank = False
    for i, line in enumerate(lines):
        if i in inserts:
            for tag in inserts[i]:
                out.extend(("", tag))
            skip_blank = False
        if skip_blank and is_blank[i]:
            skip_blank = False
            continue
        skip_blank = False
        if i in drop:
            if out and _BLANK_RE.match(out[-1]):
                skip_blank = True
            continue
        if i in anchor_lines:
            out.append(line)
        else:
            out.append(_rewrite_line(line, numbers, reference))
    if skip_blank and out and _BLANK_RE.match(out[-1]):
        out.pop()
    for tag in inserts.get(n, ()):
        out.extend(("", tag))
    return "\n".join(out)


def cite_asset_refs(text: str, *, numbers: AssetNumbers, reference: Reference) -> str:
    """Sola sostituzione dei tag gestiti con il rimando, su tutto il testo
    (ancore comprese): mai blocchi nella coda (C9)."""
    if not text:
        return text
    return _rewrite_line(text, numbers, reference)


__all__ = [
    "ANCHOR_LINE_RE",
    "ASSET_REF_RE",
    "AssetNumbers",
    "Reference",
    "cite_asset_refs",
    "normalize_asset_refs",
]
