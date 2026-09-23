"""Testi traducibili delle figure `tikz` (D7, WP6, puro).

Si traducono solo i testi dei nodi (`\\node … {testo}` e `node[…] {testo}`
dentro un tracciato) che contengono lettere e nessuna matematica o
comando: le etichette `$R_1$`, le unità e i simboli restano come sono. La
chiave è `node.N` (N = ordine nel sorgente). In applicazione il testo
tradotto passa dall'escape di TeX (`& % $ # _ { }` con la barra, `~ ^ \\ @`
tolti) e il chiamante rilancia il controllo statico: se non passa, resta
l'originale.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_NODE_RE = re.compile(r"(?:\\node\b|\bnode\b)")
_SPECIALS = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}"}


def _skip_group(source: str, i: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    while i < len(source):
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _text_spans(source: str) -> list[tuple[int, int]]:
    """Intervalli (inizio, fine) del testo fra graffe di ogni nodo."""
    spans: list[tuple[int, int]] = []
    for match in _NODE_RE.finditer(source):
        i = match.end()
        while i < len(source):
            ch = source[i]
            if ch.isspace():
                i += 1
            elif ch == "[":
                i = _skip_group(source, i, "[", "]")
            elif ch == "(":
                i = _skip_group(source, i, "(", ")")
            elif source.startswith("at", i) and not source[i + 2 : i + 3].isalpha():
                i += 2
            elif ch == "{":
                end = _skip_group(source, i, "{", "}")
                spans.append((i + 1, end - 1))
                break
            else:
                break
    return spans


def _translatable(text: str) -> bool:
    return any(ch.isalpha() for ch in text) and "$" not in text and "\\" not in text


def extract(source: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, (start, end) in enumerate(_text_spans(source)):
        text = source[start:end]
        if _translatable(text):
            out[f"node.{n}"] = " ".join(text.split())
    return out


def escape(text: str) -> str:
    cleaned = re.sub(r"[\\~^@]", "", text)
    return "".join(_SPECIALS.get(ch, ch) for ch in " ".join(cleaned.split()))


def apply(source: str, translations: Mapping[str, str]) -> str:
    spans = _text_spans(source)
    out = source
    # Da destra a sinistra: gli indici degli intervalli precedenti restano validi.
    for n, (start, end) in reversed(list(enumerate(spans))):
        key = f"node.{n}"
        if key in translations and _translatable(source[start:end]):
            out = out[:start] + escape(translations[key]) + out[end:]
    return out
