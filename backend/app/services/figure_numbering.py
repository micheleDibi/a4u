"""Numerazione editoriale degli asset (D3, D4, Q2): modulo puro.

Il numero di un asset è legato al suo id, non all'occorrenza: la prima
citazione `[KIND:id]` nel corpo della dispensa (introduzione → sezioni →
sintesi) assegna N crescente; le citazioni ripetute condividono lo stesso
N; un id senza asset non consuma numeri (il blocco `missing-asset` non
«ruba» un numero); gli asset mai citati sono accodati dopo la sintesi
(A12) e ricevono gli ultimi numeri. Il contatore è indipendente per kind
(`FIG`, `TAB`, `EQ`, `EX`): «Figura 1» e «Tabella 1» convivono; il ramo
teorema di un'equazione (`equation_label_family` → `THM`) condivide il
contatore `EQ`, perché il tag `[EQ:id]` non trasporta la famiglia.

Mantenere allineato con `frontend/src/lib/figureNumbering.ts`: la fixture
condivisa `tests/fixtures/figure_numbering_cases.json` fissa i casi
(sezioni `cases` per le sole figure, `asset_cases` per i quattro kind,
`equation_label_family`, `strip_prefix`) e `tests/test_figure_numbering.py`
la esegue su entrambi i lati.

Regole:
- `ASSET_REF_RE` e `FIG_REF_RE` sono case-sensitive sul kind, come
  `_ASSET_REF_RE` del PDF e `ASSET_REF_RE` del frontend: un `[fig:x]` non
  viene sostituito da nessun renderer e numerarlo produrrebbe un numero
  fantasma; il tag non attraversa la riga (`[^\\]\\n]+`); l'id è
  confrontato con `.strip().lower()`;
- `strip_figure_prefix` toglie un prefisso «Figura 3.», «Fig. 2:», «Figure
  4 –» già presente nella didascalia (cifra obbligatoria: «Figurativo» e
  «Fig. X» restano intatti; separatore obbligatorio dopo il numero, o fine
  del testo: «Figure 2 shows the flow», «Figura 3 e 4 a confronto» e
  «Figura 1.2 Schema» restano intatti, il prefisso non è mai «lossy»).
  Applicato SOLO a render, mai persistito; solo alle figure (nessuno
  strip per tabelle, equazioni ed esempi).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

ASSET_KINDS: tuple[str, ...] = ("FIG", "TAB", "EQ", "EX")

ASSET_REF_RE = re.compile(r"\[(FIG|TAB|EQ|EX):([^\]\n]+)\]")
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


def cited_asset_ids(markdown: str) -> list[tuple[str, str]]:
    """Coppie `(KIND, id_lower)` citate nel markdown, nell'ordine della
    prima occorrenza, senza duplicati."""
    seen: dict[tuple[str, str], None] = {}
    for m in ASSET_REF_RE.finditer(markdown or ""):
        key = (m.group(1), _norm(m.group(2)))
        if key[1] and key not in seen:
            seen[key] = None
    return list(seen)


def cited_figure_ids(markdown: str) -> list[str]:
    """Id (normalizzati) delle figure citate nel markdown, nell'ordine della
    prima occorrenza, senza duplicati (proiezione `FIG` di
    `cited_asset_ids`)."""
    return [asset_id for kind, asset_id in cited_asset_ids(markdown) if kind == "FIG"]


def _round_trips(kind: str, asset_id: str) -> bool:
    """Il token `[KIND:{id}]` rilegge esattamente `id`: falso per gli id con
    `]` o con un a capo, che nessun percorso automatico produce ma un PATCH
    manuale può salvare (gli schemi accettano qualunque stringa 1..50)."""
    m = ASSET_REF_RE.fullmatch(f"[{kind}:{asset_id}]")
    return m is not None and m.group(2) == asset_id


def append_uncited_asset_refs(markdown: str, ids_by_kind: Mapping[str, Iterable[str]]) -> str:
    """Accoda `"\\n\\n[KIND:{id}]"` per ogni asset mai citato, kind per kind
    nell'ordine `FIG → TAB → EQ → EX` e, dentro il kind, nell'ordine
    dell'array (A12, D3). L'id è scritto come dichiarato (il lookup del
    renderer è già case-insensitive). Id vuoti e duplicati sono ignorati,
    e così gli id che il token non sa trasportare: `A]` produrrebbe
    `[FIG:A]]`, che `ASSET_REF_RE` legge come `A` — un «Asset non trovato»
    falso e un `]` orfano nel testo, invece della figura (COR-4). Un kind
    assente dalla mappa non accoda nulla."""
    out = markdown or ""
    cited = set(cited_asset_ids(out))
    for kind in ASSET_KINDS:
        for asset_id in ids_by_kind.get(kind) or ():
            raw = str(asset_id or "").strip()
            key = (kind, raw.lower())
            if not key[1] or key in cited or not _round_trips(kind, raw):
                continue
            cited.add(key)
            out += f"\n\n[{kind}:{raw}]"
    return out


def append_uncited_figure_refs(markdown: str, asset_ids: Iterable[str]) -> str:
    """`append_uncited_asset_refs` per le sole figure."""
    return append_uncited_asset_refs(markdown, {"FIG": asset_ids})


def compute_asset_numbers(
    markdown: str, ids_by_kind: Mapping[str, Iterable[str]]
) -> dict[tuple[str, str], int]:
    """`{(KIND, id_lower): N}`: contatore indipendente per kind; prima
    occorrenza → N crescente; citazioni ripetute → stesso N; id senza
    asset → nessun numero consumato; kind assente dalla mappa → nessun
    numero. Da applicare al markdown DOPO `append_uncited_asset_refs`, così
    la coda è numerata dopo gli asset citati, e PRIMA del normalizzatore
    dei rimandi (`asset_ref_normalize`), che riscrive le citazioni."""
    known: set[tuple[str, str]] = set()
    for kind in ASSET_KINDS:
        for asset_id in ids_by_kind.get(kind) or ():
            id_key = _norm(asset_id)
            if id_key:
                known.add((kind, id_key))
    numbers: dict[tuple[str, str], int] = {}
    counters: dict[str, int] = dict.fromkeys(ASSET_KINDS, 0)
    for key in cited_asset_ids(markdown):
        if key in known and key not in numbers:
            counters[key[0]] += 1
            numbers[key] = counters[key[0]]
    return numbers


def compute_figure_numbers(markdown: str, asset_ids: Iterable[str]) -> dict[str, int]:
    """`{id_lower: N}` delle sole figure (proiezione `FIG` di
    `compute_asset_numbers`, stessi risultati di sempre)."""
    numbers = compute_asset_numbers(markdown, {"FIG": asset_ids})
    return {asset_id: n for (_kind, asset_id), n in numbers.items()}


def proof_steps(eq: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Passi della dimostrazione con `latex` o `text` non vuoti (selezione
    del renderer del blocco equazione)."""
    return [
        s
        for s in eq.get("proof") or []
        if isinstance(s, dict)
        and (str(s.get("latex") or "").strip() or str(s.get("text") or "").strip())
    ]


def equation_label_family(eq: Mapping[str, Any]) -> Literal["EQ", "THM"]:
    """`THM` se `statement` non è vuoto o almeno un passo di `proof` non è
    vuoto: il blocco è reso come teorema («Lemma 2.») e il rimando usa la
    parola del kind; altrimenti `EQ` («Equazione 2.»). Il campo `kind` da
    solo non decide la famiglia."""
    if str(eq.get("statement") or "").strip() or proof_steps(eq):
        return "THM"
    return "EQ"


def strip_figure_prefix(caption: str) -> str:
    """Rimuove un prefisso «Figura N.» / «Fig. N:» / «Figure N –» / «Abb.
    N)» (cifra obbligatoria, eventuale lettera, separatore obbligatorio
    salvo a fine testo) dalla didascalia. Solo a render: il testo
    persistito non cambia."""
    return _FIGURE_PREFIX_RE.sub("", caption or "", count=1)


__all__ = [
    "ASSET_KINDS",
    "ASSET_REF_RE",
    "FIG_REF_RE",
    "append_uncited_asset_refs",
    "append_uncited_figure_refs",
    "cited_asset_ids",
    "cited_figure_ids",
    "compute_asset_numbers",
    "compute_figure_numbers",
    "equation_label_family",
    "proof_steps",
    "strip_figure_prefix",
]
