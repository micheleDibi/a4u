"""Sostituzione chirurgica delle stringhe di un documento JSON (I18N-3).

La localizzazione D7 di una figura `vegalite` o `function` cambia POCHE
stringhe di una spec JSON scritta dal docente. Ricostruire il sorgente con
`json.dumps` è semplice ma butta via la formattazione (rientri, a capo,
spaziatura) e rende non byte-identico anche il round-trip con traduzioni
identiche: la spec di dodici righe torna dal ciclo di localizzazione come
una riga sola.

Qui il sorgente non viene mai riserializzato: uno scanner trova la
posizione ESATTA di ogni stringa valore e `replace_strings` sostituisce
solo quelle effettivamente cambiate. Se nessun valore cambia, il sorgente
torna identico byte per byte.

Modulo puro: nessuna dipendenza dal resto dell'applicazione. Il percorso di
una stringa ha la stessa forma degli estrattori che lo consumano
(`figure_render_service._walk_vegalite_text`,
`figure_function_service.extract_translatable`): segmenti separati da `.`,
indici delle liste come numeri (`encoding.x.axis.title`,
`expressions.0.label`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping

_WHITESPACE = " \t\n\r"
# Profondità massima della ricorsione dello scanner: `_parse_vegalite` limita
# già l'annidamento delle spec, questo è il paracadute del modulo puro (un
# `[[[[…]]]]` non deve poter sollevare `RecursionError` nel chiamante).
_MAX_DEPTH = 200


class JsonScanError(ValueError):
    """Il sorgente non è un documento JSON scandibile carattere per carattere."""


def _skip_ws(src: str, i: int) -> int:
    n = len(src)
    while i < n and src[i] in _WHITESPACE:
        i += 1
    return i


def _scan_string(src: str, i: int) -> int:
    """Indice successivo alla virgoletta che chiude la stringa iniziata in
    `src[i] == '"'` (le sequenze `\\"` non chiudono)."""
    n = len(src)
    i += 1
    while i < n:
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            return i + 1
        i += 1
    raise JsonScanError("stringa non chiusa")


def _scan_value(src: str, i: int, path: str, out: dict[str, tuple[int, int]], depth: int) -> int:
    if depth > _MAX_DEPTH:
        raise JsonScanError("documento troppo annidato")
    i = _skip_ws(src, i)
    if i >= len(src):
        raise JsonScanError("valore atteso")
    ch = src[i]
    if ch == '"':
        end = _scan_string(src, i)
        out[path] = (i, end)
        return end
    if ch == "{":
        return _scan_object(src, i, path, out, depth)
    if ch == "[":
        return _scan_array(src, i, path, out, depth)
    # Numeri e letterali: si consumano fino al primo delimitatore. La
    # validità è garantita dal `json.loads` finale di `replace_strings`.
    j = i
    while j < len(src) and src[j] not in ",]}" and src[j] not in _WHITESPACE:
        j += 1
    if j == i:
        raise JsonScanError(f"carattere inatteso: {ch!r}")
    return j


def _scan_object(src: str, i: int, path: str, out: dict[str, tuple[int, int]], depth: int) -> int:
    i = _skip_ws(src, i + 1)
    if i < len(src) and src[i] == "}":
        return i + 1
    while True:
        i = _skip_ws(src, i)
        if i >= len(src) or src[i] != '"':
            raise JsonScanError("chiave attesa")
        key_end = _scan_string(src, i)
        key = json.loads(src[i:key_end])
        i = _skip_ws(src, key_end)
        if i >= len(src) or src[i] != ":":
            raise JsonScanError("`:` atteso")
        # Chiave duplicata: vince l'ultima, come `json.loads`.
        i = _scan_value(src, i + 1, f"{path}.{key}" if path else str(key), out, depth + 1)
        i = _skip_ws(src, i)
        if i >= len(src):
            raise JsonScanError("oggetto non chiuso")
        if src[i] == ",":
            i += 1
            continue
        if src[i] == "}":
            return i + 1
        raise JsonScanError("`,` o `}` attesi")


def _scan_array(src: str, i: int, path: str, out: dict[str, tuple[int, int]], depth: int) -> int:
    i = _skip_ws(src, i + 1)
    if i < len(src) and src[i] == "]":
        return i + 1
    index = 0
    while True:
        i = _scan_value(src, i, f"{path}.{index}" if path else str(index), out, depth + 1)
        i = _skip_ws(src, i)
        if i >= len(src):
            raise JsonScanError("lista non chiusa")
        if src[i] == ",":
            i += 1
            index += 1
            continue
        if src[i] == "]":
            return i + 1
        raise JsonScanError("`,` o `]` attesi")


def string_spans(source: str) -> dict[str, tuple[int, int]] | None:
    """`{percorso: (inizio, fine)}` per ogni stringa VALORE del documento
    (`fine` esclusa, virgolette comprese); `None` se il sorgente non è
    scandibile. Le chiavi degli oggetti non sono valori e non compaiono."""
    out: dict[str, tuple[int, int]] = {}
    try:
        end = _scan_value(source, 0, "", out, 0)
        if _skip_ws(source, end) != len(source):
            raise JsonScanError("contenuto dopo il valore radice")
    except (JsonScanError, RecursionError, ValueError):
        return None
    return out


def replace_strings(source: str, values: Mapping[str, str]) -> str | None:
    """Sorgente con le sole stringhe indicate sostituite, tutto il resto
    (formattazione compresa) intatto. Un valore già uguale a quello nel
    sorgente non tocca nemmeno un byte, quindi il round-trip con
    traduzioni identiche è byte-identico.

    `None` — e il chiamante ricade sulla riserializzazione — se il
    sorgente non è scandibile, se la sostituzione non produrrebbe JSON
    valido oppure se un percorso richiesto non è una stringa del
    documento: in quel caso la sostituzione chirurgica perderebbe una
    traduzione che il chiamante ha invece applicato alla struttura."""
    spans = string_spans(source)
    if spans is None:
        return None
    edits: list[tuple[int, int, str]] = []
    for path, value in values.items():
        span = spans.get(path)
        if span is None:
            return None
        start, end = span
        try:
            if json.loads(source[start:end]) == value:
                continue
        except ValueError:
            return None
        edits.append((start, end, json.dumps(value, ensure_ascii=False)))
    if not edits:
        return source
    out = source
    for start, end, text in sorted(edits, reverse=True):
        out = out[:start] + text + out[end:]
    try:
        json.loads(out)
    except ValueError:
        return None
    return out


__all__ = ["JsonScanError", "replace_strings", "string_spans"]
