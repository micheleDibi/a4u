"""Didascalie delle figure di fonte senza la fonte scritta a mano.

La riga «Fonte» la compone SOLO il server a render: una coda «Fonte: …»
nella didascalia (del documento, nel catalogo del PROMPT 3; o del modello,
nella fusione) si toglie. Condiviso da `lesson_figure_selection` (catalogo)
e `source_figure_fusion` (output del modello).
"""

from __future__ import annotations

import re

# Coda di fonte scritta dal modello, SOLO dopo un separatore (fine frase,
# punto e virgola, parentesi, trattino): «. Fonte: …», « (Source: …)»,
# «; tratto da …», « — © …», « (Rossi et al., 2019)». Serve un segno
# esplicito (due punti, anche a larghezza piena, o una formula di
# rimando): «Energy sources: solar» e «il tratto da A a B» non sono code.
_SOURCE_WORDS = (
    r"(?:fonte|fonti|sources?|credits?|crediti|quelle|quellen|fuente|fuentes|"
    r"źródło|źródła|zdroj|источник|来源|來源|出典|出处|出處|출처)\s*[:\uff1a]"
)
_SOURCE_PHRASES = (
    r"(?:courtesy of|tratt[aoie] da|adattat[aoie] da|riprodott[aoie] da|adapted from|"
    r"reprinted from|reproduced from|redrawn from|modified from|adaptado de|"
    r"adapté de|tomado de|tiré de)\b"
)
# Citazione autore-anno: serve «et al.» o una virgola prima dell'anno
# («(Rossi, 2019)», «(Rossi et al. 2019)»), non «(Gennaio 2020)».
_CITATION = r"\(\s*[A-ZÀ-Ý][^()]{0,80}?(?:\s+et al\.?\s*,?|,)\s*(?:1[5-9]|20)\d\d[a-z]?\s*\)"
_SOURCE_TAIL_RE = re.compile(
    rf"(?:(?<=[.;:!?])\s+|(?<=[\u3002\uff1b\uff01\uff1f])\s*|\s*[(\[]\s*|\s+[—–-]\s+)"
    rf"(?:{_SOURCE_WORDS}|{_SOURCE_PHRASES}|©|copyright\b).*$"
    rf"|\s+(?:\(c\)|©)\s*\S.*$"
    rf"|\s+{_CITATION}\s*\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def clean_caption(text: str) -> tuple[str, bool]:
    """Didascalia senza coda di fonte; (testo, è stata tagliata). Se il
    taglio lascerebbe la didascalia vuota, resta quella originale."""
    original = (text or "").strip()
    cleaned = _SOURCE_TAIL_RE.sub("", original).strip().rstrip(";,:—–- ").strip()
    if not cleaned:
        return original, False
    return cleaned, cleaned != original


def third_party_credit(caption: str | None) -> str | None:
    """Credito di terzi nella didascalia ORIGINALE di una figura estratta
    («Reprinted from Smith et al. (2010), © Elsevier, with permission»,
    «Fonte: …», «(Rossi et al., 2019)»): la figura non è dell'autore del
    documento, quindi non ne eredita la licenza e la riga «Fonte» deve
    nominarne l'origine (Fase D). None se non c'è."""
    text = " ".join((caption or "").split())
    match = _SOURCE_TAIL_RE.search(text)
    if match is None:
        return None
    credit = match.group(0).strip().lstrip("([—–-;:").strip()
    if credit.count(")") > credit.count("("):
        credit = credit.rstrip(")").strip()
    credit = credit.rstrip(".;, ").strip()
    return credit[:200] or None
