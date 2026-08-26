"""Risoluzione dei riferimenti di contabilità della Fase 3 (§6.4).

`sections[].objectives_addressed` e `coverage_check.objectives_covered[]`
sono l'unico punto della pipeline in cui l'AI deve riferirsi a un dato di
Fase 2 tramite il suo TESTO invece che tramite un ID (i temi hanno
`topic_id`, le slide `slide_id`, le sezioni `section_id`). Pretendere la
riproduzione alla lettera di una frase di 90-150 caratteri è un contratto
che il modello non può onorare in modo affidabile: un apostrofo
tipografico, un accento composto diversamente, un punto finale bastavano
a far scartare l'intera dispensa.

Da qui la doppia rete:
- nel prompt gli obiettivi sono numerati `[O1] … [On]` e lo schema JSON
  della chiamata vincola i due campi a quell'`enum` (il modello non PUÒ
  più emettere un valore inesistente);
- questo modulo risolve comunque ciò che arriva, con una cascata
  DETERMINISTICA, e restituisce il valore canonico di Fase 2.

Nessun matching fuzzy, deliberatamente. Con due obiettivi fratelli
«… di un segnale periodico» / «… di un segnale non periodico», la
parafrasi «… di un segnale aperiodico» ha `difflib` ratio 0.947 sul primo
e 0.783 sul secondo: qualunque soglia con margine sceglierebbe con
sicurezza l'opposto semantico. Un falso negativo costa un warning; un
falso positivo attacca la sezione all'obiettivo sbagliato in silenzio e
può far fallire la copertura di quello giusto.

Funzioni pure: nessun accesso a Settings, DB o I/O; nessun import di
`app.models` / `app.schemas` (la policy — scarta, avvisa, fallisce,
deriva — resta nel service chiamante).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Codici mostrati al modello nel prompt: `[O1]`, `[O2]`, … Sono maniglie
# POSIZIONALI valide solo per la durata di una chiamata: non vengono mai
# persistite (vedi `course_lesson_content_service._canonicalize_coverage`).
OBJECTIVE_ID_PREFIX = "O"

# `O3`, `[O3]`, `o3`, `O3.`, `[ O3 ]` — tutto ciò che è palesemente un
# codice e non prosa.
_OBJECTIVE_ID_RE = re.compile(r"^\[?\s*o\s*[-.:]?\s*(\d{1,2})\s*\]?\s*[.:)]?\s*$", re.IGNORECASE)
_BRACKETED_RE = re.compile(r"^\[\s*(.+?)\s*\]$")

# Soglia del contenimento: sotto questa lunghezza un frammento è contenuto
# in più obiettivi fratelli (che condividono il prefisso d'obbligo «Lo
# studente sarà in grado di») e il match non sarebbe informativo.
MIN_CONTAINMENT_CHARS = 30


def collapse_spaces(value: str) -> str:
    """Collassa spazi interni e a-capo: la forma canonica di un obiettivo.

    Un obiettivo con un `\\n` verrebbe reso nel prompt come DUE voci
    dell'elenco puntato e non potrebbe più essere riprodotto intero.
    """
    return " ".join((value or "").split())


def normalize(value: str) -> str:
    """NFKD + casefold + rimozione diacritici + non-alfanumerici → spazio.

    Stessa ricetta di `document_citation_guard.normalize`: copiata invece
    di estrarre un util condiviso (sarebbe la terza sede a cambiare per
    zero guadagno). Un test tiene allineate le due copie.
    """
    s = unicodedata.normalize("NFKD", (value or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else " " for c in s)
    return " ".join(s.split())


def legacy_normalize(value: str) -> str:
    """Normalizzazione storica di §6.4: minuscole + spazi collassati.

    Conservata come primo confronto testuale così tutto ciò che oggi
    passa continua a passare per la stessa strada.
    """
    return " ".join((value or "").lower().split())


def objective_prompt_ids(objectives: Sequence[str]) -> list[str]:
    """`["O1", …, "On"]` — un codice per obiettivo, in ordine di lista."""
    return [f"{OBJECTIVE_ID_PREFIX}{i}" for i in range(1, len(objectives) + 1)]


@dataclass(frozen=True)
class ObjectiveIndex:
    """Obiettivi di Fase 2 nella forma canonica + le chiavi di confronto."""

    objectives: tuple[str, ...]
    ids: tuple[str, ...]
    legacy: tuple[str, ...]
    norms: tuple[str, ...]


@dataclass(frozen=True)
class TopicIndex:
    """`topic_id` di Fase 2 + il testo del tema (fallback di risoluzione)."""

    topic_ids: tuple[str, ...]
    folded: tuple[str, ...]
    topic_norms: tuple[str, ...]


@dataclass(frozen=True)
class Resolution:
    """`value=None` = irrisolto. `method` serve solo a log e test."""

    value: str | None
    method: str


_UNRESOLVED = Resolution(value=None, method="unresolved")


def build_objective_index(objectives: Sequence[Any]) -> ObjectiveIndex:
    canonical = tuple(collapse_spaces(str(o)) for o in objectives if o and str(o).strip())
    return ObjectiveIndex(
        objectives=canonical,
        ids=tuple(objective_prompt_ids(canonical)),
        legacy=tuple(legacy_normalize(o) for o in canonical),
        norms=tuple(normalize(o) for o in canonical),
    )


def build_topic_index(mandatory_topics: Sequence[Any]) -> TopicIndex:
    ids: list[str] = []
    topics: list[str] = []
    for entry in mandatory_topics or []:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("topic_id") or "").strip()
        if not tid:
            continue
        ids.append(tid)
        # `topic` è la chiave dello schema di Fase 2; `title` compare in
        # dati storici e nei builder dei test.
        topics.append(str(entry.get("topic") or entry.get("title") or ""))
    return TopicIndex(
        topic_ids=tuple(ids),
        folded=tuple(t.casefold() for t in ids),
        topic_norms=tuple(normalize(t) for t in topics),
    )


def _unique_index(candidates: list[int]) -> int | None:
    return candidates[0] if len(candidates) == 1 else None


def resolve_objective(raw: str, index: ObjectiveIndex) -> Resolution:
    """Cascata deterministica; il primo esito vince."""
    value = (raw or "").strip()
    if not value or not index.objectives:
        return _UNRESOLVED

    # 1. Codice `O3` / `[O3]`. Se l'indice è fuori range NON si ripiega
    #    sui confronti testuali: un codice non è prosa.
    match = _OBJECTIVE_ID_RE.match(value)
    if match:
        position = int(match.group(1)) - 1
        if 0 <= position < len(index.objectives):
            return Resolution(index.objectives[position], "id")
        return _UNRESOLVED

    # 2. Uguaglianza storica (minuscole + spazi).
    legacy = legacy_normalize(value)
    for i, candidate in enumerate(index.legacy):
        if legacy == candidate:
            return Resolution(index.objectives[i], "exact")

    # 3. Uguaglianza normalizzata: accenti, apostrofi tipografici, NBSP,
    #    trattini, punteggiatura finale.
    norm = normalize(value)
    if not norm:
        return _UNRESOLVED
    for i, candidate in enumerate(index.norms):
        if norm == candidate:
            return Resolution(index.objectives[i], "normalized")

    # 4. Contenimento (troncamenti ed estensioni), solo se il candidato è
    #    UNICO: altrimenti un frammento vincerebbe per ordine di lista.
    if len(norm) >= MIN_CONTAINMENT_CHARS:
        hits = [
            i
            for i, candidate in enumerate(index.norms)
            if len(candidate) >= MIN_CONTAINMENT_CHARS and (norm in candidate or candidate in norm)
        ]
        unique = _unique_index(hits)
        if unique is not None:
            return Resolution(index.objectives[unique], "containment")

    return _UNRESOLVED


def resolve_topic(raw: str, index: TopicIndex) -> Resolution:
    """Come sopra per i `topic_id`. Mai fuzzy: `T1` vs `T2` sarebbe un
    lancio di moneta."""
    value = (raw or "").strip()
    if not value or not index.topic_ids:
        return _UNRESOLVED
    bracketed = _BRACKETED_RE.match(value)
    if bracketed:
        value = bracketed.group(1).strip()

    if value in index.topic_ids:
        return Resolution(value, "id")

    folded = value.casefold()
    hits = [i for i, candidate in enumerate(index.folded) if folded == candidate]
    position = _unique_index(hits)
    if position is not None:
        return Resolution(index.topic_ids[position], "id_folded")

    # Il modello a volte scrive il TITOLO del tema invece del codice.
    norm = normalize(value)
    if norm:
        hits = [
            i for i, candidate in enumerate(index.topic_norms) if candidate and norm == candidate
        ]
        position = _unique_index(hits)
        if position is not None:
            return Resolution(index.topic_ids[position], "topic_text")

    return _UNRESOLVED


def _resolve_all(
    raws: Sequence[str],
    resolver: Any,
    index: Any,
) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    unresolved: list[str] = []
    for raw in raws or []:
        outcome = resolver(str(raw), index)
        if outcome.value is None:
            unresolved.append(str(raw))
        elif outcome.value not in resolved:
            resolved.append(outcome.value)
    return resolved, unresolved


def resolve_objectives(raws: Sequence[str], index: ObjectiveIndex) -> tuple[list[str], list[str]]:
    """`(canonici deduplicati in ordine, irrisolti)`."""
    return _resolve_all(raws, resolve_objective, index)


def resolve_topics(raws: Sequence[str], index: TopicIndex) -> tuple[list[str], list[str]]:
    """`(topic_id canonici deduplicati in ordine, irrisolti)`."""
    return _resolve_all(raws, resolve_topic, index)
