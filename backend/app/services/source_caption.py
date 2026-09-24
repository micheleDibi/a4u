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
# «Fonte» nelle lingue dell'interfaccia e in quelle non latine più usate
# (Fase D: nl, el, fi, hu, ro, sv, da, bg, lv, lt, et, sl, hr, mt, ga, ar,
# he mancavano).
_SOURCE_WORDS = (
    r"(?:fonte|fonti|sources?|credits?|crediti|quelle|quellen|fuente|fuentes|"
    r"źródło|źródła|zdroj|источник|джерело|bron|πηγή|lähde|forrás|sursa|källa|kilde|"
    r"източник|avots|šaltinis|allikas|vir|izvor|sors|foinse|المصدر|מקור|"
    r"来源|來源|出典|出处|出處|출처)\s*[:\uff1a]"
)
_SOURCE_PHRASES = (
    r"(?:(?:image |photo |foto |immagine )?courtesy of|per gentile concessione|"
    r"tratt[aoie] da|adattat[aoie] da|riprodott[aoie] da|adapted from|"
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


# Etichetta di pannello «(c)» (dopo «(a)», «(b)»): non è un ©. Un vero
# «(c)» di copyright ha un anno o un titolare subito dopo.
_PANEL_C_RE = re.compile(r"^\(?c\)\s*(?!\d{4}\b|copyright\b)", re.IGNORECASE)
_LEADING_SOURCE_WORD_RE = re.compile(rf"^{_SOURCE_WORDS}\s*", re.IGNORECASE)
_NAMES_SOMEONE_RE = re.compile(r"[A-ZÀ-ÝΑ-ΩА-ЯЁ0-9©]|[\u0600-\u06ff\u05d0-\u05ea\u2e80-\uffff]")
# Materiale dell'autore del documento: non è un credito di terzi.
_OWN_WORK_RE = re.compile(
    r"^(?:elaborazione (?:propria|dell'autore|degli autori)|own (?:elaboration|work)|"
    r"author'?s? own|elaborated by the authors?|figura originale|original figure)\b",
    re.IGNORECASE,
)


def third_party_credit(caption: str | None) -> str | None:
    """Credito di terzi nella didascalia ORIGINALE di una figura estratta
    («Reprinted from Smith et al. (2010), © Elsevier, with permission»,
    «Fonte: Rossi 2019», «(Rossi et al., 2019)»): la figura non è
    dell'autore del documento, quindi non ne eredita la licenza e la riga
    «Fonte» deve nominarne l'origine (Fase D). None se non c'è, se è
    un'etichetta di pannello «(c)» o se dichiara materiale proprio. Il
    credito non ripete la parola «Fonte» (la mette la riga)."""
    text = " ".join((caption or "").split())
    match = _SOURCE_TAIL_RE.search(text)
    if match is None:
        return None
    raw = match.group(0).strip().lstrip("[—–-;:").strip()
    if _PANEL_C_RE.match(raw) and re.search(r"\((?:a|b)\)", text, re.IGNORECASE):
        return None
    credit = re.sub(r"^\(c\)", "©", raw, flags=re.IGNORECASE).lstrip("(").strip()
    credit = _LEADING_SOURCE_WORD_RE.sub("", credit).strip().rstrip(".;, ").strip()
    if credit.count(")") > credit.count("("):
        credit = credit.rstrip(")").strip().rstrip(".;, ").strip()
    if not credit or _OWN_WORK_RE.match(credit):
        return None
    # Un credito nomina qualcuno (maiuscola, anche greca o cirillica), porta
    # un anno o un ©, o è in una scrittura senza maiuscole (CJK): «Sources:
    # primary and secondary windings» non lo è.
    if not _NAMES_SOMEONE_RE.search(credit):
        return None
    return credit[:200]
