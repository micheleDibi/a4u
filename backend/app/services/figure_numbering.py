"""Numerazione editoriale delle figure (D4, Q2): modulo puro.

Il numero di una figura è legato al suo `asset_id`, non all'occorrenza: la
prima citazione `[FIG:id]` nel corpo della dispensa (introduzione →
sezioni → sintesi) assegna N crescente; le citazioni ripetute condividono
lo stesso N; un id senza asset non consuma numeri (il blocco `missing-asset`
non «ruba» un numero); gli asset mai citati sono accodati dopo la sintesi
(A12) e ricevono gli ultimi numeri.

Mantenere allineato con `frontend/src/lib/figureNumbering.ts`: la fixture
condivisa `tests/fixtures/figure_numbering_cases.json` fissa i casi
(duplicati, id mancante, case diverso, `[fig:x]` ignorato, non citati in
coda, nessuna figura) e `tests/test_figure_numbering.py` la esegue.

Regole:
- `FIG_REF_RE` è case-sensitive su `FIG`, come `_ASSET_REF_RE` del PDF e
  `ASSET_REF_RE` del frontend: un `[fig:x]` non viene sostituito da nessun
  renderer e numerarlo produrrebbe un numero fantasma; l'id è confrontato
  con `.strip().lower()`;
- `strip_figure_prefix` toglie un prefisso «Figura 3.», «Fig. 2:», «Figure
  4 –» già presente nella didascalia (cifra obbligatoria: «Figurativo» e
  «Fig. X» restano intatti; separatore obbligatorio dopo il numero, o fine
  del testo: «Figure 2 shows the flow», «Figura 3 e 4 a confronto» e
  «Figura 1.2 Schema» restano intatti, il prefisso non è mai «lossy»).
  Applicato SOLO a render, mai persistito.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

FIG_REF_RE = re.compile(r"\[FIG:([^\]\n]+)\]")

# Prefisso editoriale: parola, numero (con eventuali sottonumeri «1.2» e
# lettera «4a»), poi UN separatore `. : - – — )` non seguito da cifra
# (altrimenti «Figura 1.2 Schema» perderebbe «1.»), oppure fine del testo.
# Il separatore è obbligatorio: senza, «Figure 2 shows …» è una frase.
_FIGURE_PREFIX_RE = re.compile(
    r"^\s*(?:figura|figure|fig\.?|abb\.?)\s*\d+(?:\.\d+)*[a-z]?\s*(?:[.:\-–—)](?!\d)\s*|$)",
    re.IGNORECASE,
)


def _norm(asset_id: object) -> str:
    return str(asset_id or "").strip().lower()


def cited_figure_ids(markdown: str) -> list[str]:
    """Id (normalizzati) citati nel markdown, nell'ordine della prima
    occorrenza, senza duplicati."""
    seen: dict[str, None] = {}
    for m in FIG_REF_RE.finditer(markdown or ""):
        key = _norm(m.group(1))
        if key and key not in seen:
            seen[key] = None
    return list(seen)


def append_uncited_figure_refs(markdown: str, asset_ids: Iterable[str]) -> str:
    """Accoda `"\\n\\n[FIG:{id}]"` per ogni asset mai citato, nell'ordine
    dell'array (A12). L'id è scritto come dichiarato (il lookup del
    renderer è già case-insensitive). Id vuoti e duplicati sono ignorati."""
    out = markdown or ""
    cited = set(cited_figure_ids(out))
    for asset_id in asset_ids:
        raw = str(asset_id or "").strip()
        key = raw.lower()
        if not key or key in cited:
            continue
        cited.add(key)
        out += f"\n\n[FIG:{raw}]"
    return out


def compute_figure_numbers(markdown: str, asset_ids: Iterable[str]) -> dict[str, int]:
    """`{id_lower: N}`: prima occorrenza → N crescente; citazioni ripetute →
    stesso N; id senza asset → nessun numero consumato. Da applicare al
    markdown DOPO `append_uncited_figure_refs`, così la coda è numerata
    dopo le figure citate."""
    known = {_norm(a) for a in asset_ids}
    known.discard("")
    numbers: dict[str, int] = {}
    for key in cited_figure_ids(markdown):
        if key in known and key not in numbers:
            numbers[key] = len(numbers) + 1
    return numbers


def strip_figure_prefix(caption: str) -> str:
    """Rimuove un prefisso «Figura N.» / «Fig. N:» / «Figure N –» / «Abb.
    N)» (cifra obbligatoria, eventuale lettera, separatore obbligatorio
    salvo a fine testo) dalla didascalia. Solo a render: il testo
    persistito non cambia."""
    return _FIGURE_PREFIX_RE.sub("", caption or "", count=1)


__all__ = [
    "FIG_REF_RE",
    "append_uncited_figure_refs",
    "cited_figure_ids",
    "compute_figure_numbers",
    "strip_figure_prefix",
]
