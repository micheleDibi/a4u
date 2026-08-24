"""Guard citazionale per i documenti a fonte riservata (Blocco 2).

Rete di sicurezza deterministica a valle dell'anonimizzazione dei
prompt: costruisce un indice identitario dei documenti non citabili
(`citation_policy` in content_only/excluded) e intercetta le voci di
bibliografia/references generate che vi corrispondono.

Matching (deliberatamente conservativo, MAI per solo autore — i
cognomi sono comuni e produrrebbero falsi positivi certi):
- containment bidirezionale tra titoli normalizzati (min 12 char);
- oppure `difflib` ratio >= 0.85 sul titolo;
- oppure ratio >= 0.65 E cognome del primo autore presente nella voce.

FALSI NEGATIVI STRUTTURALI dichiarati: citazioni parafrasate, titoli
brevissimi, summary storici con campi identitari vuoti + filename muto.
Mitigazione: lo scrub/ri-analisi al passaggio a `content_only` popola i
campi dedicati (`source_title`/`authors_and_references`) su cui questo
indice si basa. Soglie come costanti di modulo: non c'è motivo di
variarle per ambiente. Nessuna dipendenza nuova (difflib stdlib).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.course_document import CourseDocument

_MIN_CONTAINMENT_CHARS = 12
_TITLE_RATIO = 0.85
_TITLE_WITH_AUTHOR_RATIO = 0.65

# Suffisso uuid6 dei filename generati dall'import paper
# ({autore}_{anno}_{titolo}_{uuid4hex[:6]}).
_PAPER_UUID_SUFFIX = re.compile(r"_[0-9a-f]{6}$")

_RESERVED_POLICIES = ("content_only", "excluded")


def normalize(value: str) -> str:
    s = unicodedata.normalize("NFKD", (value or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else " " for c in s)
    return " ".join(s.split())


@dataclass
class ReservedDocIdentity:
    """Identità normalizzata di un documento non citabile."""

    doc_id: str
    filename_original: str
    titles: list[str] = field(default_factory=list)  # normalizzati
    author_lastnames: list[str] = field(default_factory=list)


def _filename_stem_title(filename_original: str) -> str:
    stem = Path(filename_original or "").stem
    stem = _PAPER_UUID_SUFFIX.sub("", stem)
    return normalize(stem.replace("_", " ").replace("-", " "))


def build_identity_index(
    docs: list[CourseDocument],
) -> list[ReservedDocIdentity]:
    """Indice identitario dei documenti con fonte riservata/esclusa."""
    index: list[ReservedDocIdentity] = []
    for doc in docs:
        if getattr(doc, "citation_policy", "citable") not in _RESERVED_POLICIES:
            continue
        entry = ReservedDocIdentity(
            doc_id=str(doc.id), filename_original=doc.filename_original
        )
        summary = doc.summary or {}
        source_title = normalize(str(summary.get("source_title") or ""))
        if len(source_title) >= 4:
            entry.titles.append(source_title)
        stem_title = _filename_stem_title(doc.filename_original)
        if len(stem_title) >= 4:
            entry.titles.append(stem_title)
        for item in summary.get("authors_and_references") or []:
            if not isinstance(item, dict) or item.get("type") != "author":
                continue
            lastname = normalize(str(item.get("value") or "")).split()
            if lastname and len(lastname[-1]) >= 3:
                entry.author_lastnames.append(lastname[-1])
        if entry.titles or entry.author_lastnames:
            index.append(entry)
    return index


def _title_matches(candidate_norm: str, entry: ReservedDocIdentity) -> bool:
    if not candidate_norm:
        return False
    for title in entry.titles:
        if (
            len(title) >= _MIN_CONTAINMENT_CHARS
            and len(candidate_norm) >= _MIN_CONTAINMENT_CHARS
            and (title in candidate_norm or candidate_norm in title)
        ):
            return True
        ratio = SequenceMatcher(None, title, candidate_norm).ratio()
        if ratio >= _TITLE_RATIO:
            return True
        if ratio >= _TITLE_WITH_AUTHOR_RATIO and any(
            f" {ln} " in f" {candidate_norm} "
            for ln in entry.author_lastnames
        ):
            return True
    return False


def match_reserved(
    text: str, index: list[ReservedDocIdentity]
) -> ReservedDocIdentity | None:
    """Prima identità riservata che corrisponde al testo (None se
    nessuna). MAI match per solo autore."""
    candidate = normalize(text)
    for entry in index:
        if _title_matches(candidate, entry):
            return entry
    return None


def filter_bibliography_items(
    items: list[dict], index: list[ReservedDocIdentity]
) -> tuple[list[dict], list[dict]]:
    """Divide le voci di `recommended_bibliography` in (tenute,
    scartate). Il match usa titolo (+ autori come criterio secondario);
    si scartano ANCHE le voci `general_knowledge_suggestion` che
    coincidono con un'opera riservata: per il lettore sarebbe
    indistinguibile dal citarla (il docente può reinserirla a mano)."""
    if not index:
        return list(items), []
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in items:
        probe = " ".join(
            str(item.get(key) or "") for key in ("title", "authors")
        )
        if match_reserved(probe, index) is not None:
            dropped.append(item)
        else:
            kept.append(item)
    return kept, dropped


def filter_reference_items(
    references: list[dict], index: list[ReservedDocIdentity]
) -> tuple[list[dict], list[dict]]:
    """Divide le `references[]` di P3 (citation libere) in (tenute,
    scartate)."""
    if not index:
        return list(references), []
    kept: list[dict] = []
    dropped: list[dict] = []
    for ref in references:
        if match_reserved(str(ref.get("citation") or ""), index) is not None:
            dropped.append(ref)
        else:
            kept.append(ref)
    return kept, dropped


def reserved_filtered_bibliography(course, lesson) -> list[dict]:
    """Voci di `lesson.recommended_bibliography` SENZA quelle che
    matchano i documenti a fonte riservata del corso.

    Filtro IN LETTURA per i prompt P3/P4/P5: la bibliografia PERSISTITA
    (generata quando il documento era ancora citabile) rientra nei
    prompt a ogni rigenerazione — senza questo filtro, cambiare policy
    dopo P1 lascerebbe il leak attivo su dispensa/slide/audio. Il dato
    persistito resta intatto (non retroattivo); `course.documents` deve
    essere eager-loaded (vero nei tre service chiamanti)."""
    items = [
        item
        for item in (lesson.recommended_bibliography or [])
        if isinstance(item, dict)
    ]
    if not items:
        return []
    index = build_identity_index(list(course.documents))
    kept, _dropped = filter_bibliography_items(items, index)
    return kept


def scan_text_for_leaks(
    text: str, index: list[ReservedDocIdentity]
) -> list[str]:
    """Scan SOFT della prosa: ritorna i filename dei documenti riservati
    il cui titolo compare nel testo (solo containment sul testo
    normalizzato). Non muta mai il testo: il chiamante logga/audita."""
    if not index or not text:
        return []
    haystack = normalize(text)
    hits: list[str] = []
    for entry in index:
        for title in entry.titles:
            if len(title) >= _MIN_CONTAINMENT_CHARS and title in haystack:
                hits.append(entry.filename_original)
                break
    return hits
