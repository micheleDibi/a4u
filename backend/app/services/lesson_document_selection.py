"""Selezione per lezione degli estratti dei riassunti documentali (Fase 3).

Prima di questo modulo il prompt delle dispense riceveva, per ogni
lezione, lo STESSO blocco documenti del corso (`_build_documents_context`,
cap 20k caratteri, abstract + concetti + definizioni), senza esempi né
formule e senza alcuna selezione: con 5 documenti ogni riassunto era
tagliato a 4k caratteri. Qui, senza retrieval né embedding, si scelgono
le voci del riassunto (definizioni, formule, concetti, esempi, struttura)
più pertinenti alla lezione, misurando la sovrapposizione lessicale con
titolo, temi obbligatori, scaletta, obiettivi e sinossi della lezione.

Proprietà:
- deterministico: nessuna casualità, ordine dei documenti per
  `(created_at, id)`, tie-break espliciti; stesso input → stesso testo;
- smorzamento di frequenza: gli stem presenti in molte voci del corpus
  del corso pesano meno (`1 / (1 + log(1 + df))`) e quelli ubiqui (> 30%
  delle voci) sono ignorati, così "funzione"/"continuità" in un corso di
  Analisi non fanno vincere le voci più lunghe;
- abstract sempre presente (carta d'identità del documento, serve alle
  `references`); nessuna voce troncata a metà; le formule non si
  troncano mai (se non entrano, si saltano);
- documenti riservati (`content_only`) restano anonimi (header da
  `_document_header_lines`, stessa sede della regola) e in coda, dopo il
  framing non citabile; gli `excluded` non entrano;
- fallback `overview` (abstract + primi concetti + tag) quando nessuna
  voce supera la soglia o per la lezione introduttiva.

Funzioni pure: nessun accesso a Settings, DB o I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Any

from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.services import document_citation_guard
from app.services.course_architecture_service import (
    _NON_CITABLE_FRAMING,
    _document_header_lines,
)

# --- Costanti di modulo (non Settings: sono dettagli dell'algoritmo) -------

STEM_LEN = 5
MIN_TOKEN_LEN = 3
MIN_ENTRY_SCORE = 1.0
UBIQUITY_RATIO = 0.30
UBIQUITY_MIN_ENTRIES = 12
ENTRY_MAX_CHARS = 700
ABSTRACT_COMPACT_CHARS = 800
ABSTRACT_MIN_CHARS = 400
PER_DOC_MIN_CHARS = 2_500
IDENTITY_SHARE = 0.35
OVERVIEW_CONCEPTS = 8
INTRO_OUTLINE_ITEMS = 10

CATEGORY_ORDER: tuple[str, ...] = (
    "definitions",
    "formulas_or_rules",
    "key_concepts",
    "examples_or_cases",
    "structure_outline",
)
CATEGORY_CAPS: dict[str, int] = {
    "definitions": 15,
    "formulas_or_rules": 12,
    "key_concepts": 12,
    "examples_or_cases": 8,
    "structure_outline": 6,
}
CATEGORY_TITLES: dict[str, str] = {
    "definitions": "Definizioni",
    "formulas_or_rules": "Formule e regole",
    "key_concepts": "Concetti chiave",
    "examples_or_cases": "Esempi e casi",
    "structure_outline": "Struttura",
}

# Pesi delle sorgenti del profilo lezione (moltiplicatori per stem).
WEIGHT_TITLE = 3.0
WEIGHT_TOPIC = 3.0
WEIGHT_RATIONALE = 1.0
WEIGHT_SECTION_TITLE = 2.0
WEIGHT_SECTION_PURPOSE = 1.0
WEIGHT_OBJECTIVE = 1.0
WEIGHT_SUMMARY = 1.0
BIGRAM_FACTOR = 2.0

NO_DOCS_TEXT = "(Nessun documento di riferimento elaborato.)"
OVERVIEW_NOTE = (
    "(Nessun estratto specifico per questa lezione: i documenti coprono il tema solo in generale.)"
)
LANGUAGE_NOTE = "Nota: lingua del documento diversa da quella del corso."

# Stopword minime IT/EN + parole "didattiche" che compaiono in ogni
# scaletta e non discriminano. Volutamente corte: lo smorzamento di
# frequenza fa il resto.
_STOPWORDS_TEXT = """
    il lo la le gli un uno una di a da in con su per tra fra e ed o che chi
    cui non come dove quando anche ancora gia piu meno molto poco tutto
    tutti ogni altro altri altra altre questo questa questi queste quello
    quella quelli quelle suo sua suoi sue loro nostro nostra essere avere
    sono era erano sara stato stata stati state viene vengono venire fare
    fatto puo possono deve devono dei del della delle degli dello dal dalla
    dalle dai dagli nel nella nelle nei negli sul sulla sulle sui sugli al
    alla alle agli allo col
    the a an and or of to in on for with by from as at is are was were be
    been being this that these those it its their our your his her they
    them not but if then than also into over under about between
    lezione lezioni sezione sezioni studente studenti studentessa concetto
    concetti esempio esempi introduzione definizione definizioni capitolo
    obiettivo obiettivi tema temi argomento argomenti modulo corso parte
    lesson lessons section sections student students concept concepts
    example examples introduction chapter definition definitions objective
    objectives topic topics module course part
    """
STOPWORDS: frozenset[str] = frozenset(_STOPWORDS_TEXT.split())


# --- Normalizzazione ----------------------------------------------------


def tokenize(text: str | None) -> list[str]:
    """Stem (troncamento a `STEM_LEN`) delle parole significative di `text`.

    Usa `document_citation_guard.normalize` (casefold, accenti rimossi,
    non-alfanumerici → spazio); scarta token corti, numerici e stopword.
    """
    out: list[str] = []
    for tok in document_citation_guard.normalize(text or "").split():
        if len(tok) < MIN_TOKEN_LEN or tok.isdigit() or tok in STOPWORDS:
            continue
        out.append(tok[:STEM_LEN])
    return out


def terms(text: str | None) -> set[str]:
    """Unigrammi e bigrammi (stem consecutivi) del testo."""
    stems = tokenize(text)
    result = set(stems)
    result.update(f"{a} {b}" for a, b in pairwise(stems))
    return result


def _is_bigram(term: str) -> bool:
    return " " in term


# --- Profilo della lezione ------------------------------------------------


def _absorb(profile: dict[str, float], text: str | None, weight: float) -> None:
    for t in terms(text):
        w = weight * (BIGRAM_FACTOR if _is_bigram(t) else 1.0)
        if w > profile.get(t, 0.0):
            profile[t] = w


def build_query_profile(lesson: CourseLesson) -> dict[str, float]:
    """Profilo lessicale della lezione: stem/bigramma → peso massimo tra le
    sorgenti in cui compare (le ripetizioni non gonfiano il peso)."""
    profile: dict[str, float] = {}
    _absorb(profile, lesson.title, WEIGHT_TITLE)
    for topic in lesson.mandatory_topics or []:
        if not isinstance(topic, dict):
            continue
        _absorb(profile, topic.get("topic") or topic.get("title"), WEIGHT_TOPIC)
        _absorb(profile, topic.get("rationale"), WEIGHT_RATIONALE)
    for section in lesson.section_outline or []:
        if not isinstance(section, dict):
            continue
        _absorb(profile, section.get("title"), WEIGHT_SECTION_TITLE)
        _absorb(profile, section.get("purpose"), WEIGHT_SECTION_PURPOSE)
    for objective in lesson.learning_objectives or []:
        _absorb(profile, str(objective), WEIGHT_OBJECTIVE)
    _absorb(profile, lesson.summary, WEIGHT_SUMMARY)
    return profile


# --- Voci candidate del riassunto ----------------------------------------


@dataclass(frozen=True)
class Entry:
    category: str
    index: int
    match_text: str
    render: str
    terms: frozenset[str]


def _truncate_prose(text: str, max_chars: int = ENTRY_MAX_CHARS) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    dot = cut.rfind(". ")
    if dot > max_chars // 2:
        return cut[: dot + 1]
    return cut.rstrip() + "…"


def _entries_from_summary(summary: dict[str, Any], *, reserved: bool) -> list[Entry]:
    """Voci candidate (con cap per categoria) nel formato di rendering."""
    out: list[Entry] = []

    def add(category: str, index: int, match_text: str, render: str) -> None:
        out.append(
            Entry(
                category=category,
                index=index,
                match_text=match_text,
                render=render,
                terms=frozenset(terms(match_text)),
            )
        )

    for i, d in enumerate(summary.get("definitions") or []):
        if i >= CATEGORY_CAPS["definitions"] or not isinstance(d, dict):
            continue
        term = str(d.get("term") or "").strip()
        definition = _truncate_prose(str(d.get("definition") or ""))
        if term and definition:
            add("definitions", i, f"{term} {definition}", f"- **{term}**: {definition}")

    for i, f in enumerate(summary.get("formulas_or_rules") or []):
        if i >= CATEGORY_CAPS["formulas_or_rules"] or not isinstance(f, dict):
            continue
        label = str(f.get("label") or "").strip()
        latex = " ".join(str(f.get("latex_or_text") or "").split())
        meaning = _truncate_prose(str(f.get("meaning") or ""))
        if label and (latex or meaning):
            body = f"`{latex}`" if latex else ""
            if meaning:
                body = f"{body} — {meaning}" if body else meaning
            add("formulas_or_rules", i, f"{label} {meaning}", f"- **{label}**: {body}")

    for i, c in enumerate(summary.get("key_concepts") or []):
        if i >= CATEGORY_CAPS["key_concepts"] or not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        expl = _truncate_prose(str(c.get("explanation") or ""))
        if name and expl:
            add("key_concepts", i, f"{name} {expl}", f"- **{name}**: {expl}")

    for i, e in enumerate(summary.get("examples_or_cases") or []):
        if i >= CATEGORY_CAPS["examples_or_cases"] or not isinstance(e, dict):
            continue
        title = str(e.get("title") or "").strip()
        synthesis = _truncate_prose(str(e.get("synthesis") or ""))
        if not synthesis:
            continue
        # Per i riservati il titolo dell'esempio può identificare l'opera:
        # entra solo la sintesi.
        render = f"- {synthesis}" if reserved or not title else f"- **{title}**: {synthesis}"
        add("examples_or_cases", i, f"{title} {synthesis}", render)

    for i, item in enumerate(summary.get("structure_outline") or []):
        if i >= CATEGORY_CAPS["structure_outline"]:
            continue
        text = str(item or "").strip()
        if text:
            add("structure_outline", i, text, f"- {text}")

    return out


# --- Scoring ----------------------------------------------------------------


@dataclass(frozen=True)
class ScoredEntry:
    entry: Entry
    score: float

    @property
    def chars(self) -> int:
        return len(self.entry.render) + 1


def compute_document_frequency(entries: list[Entry]) -> dict[str, int]:
    """df[stem] = numero di voci (di tutto il corpus del corso) che lo contengono."""
    df: dict[str, int] = {}
    for e in entries:
        for t in e.terms:
            df[t] = df.get(t, 0) + 1
    return df


def score_entry(
    entry: Entry,
    profile: dict[str, float],
    df: dict[str, int],
    n_entries: int,
) -> float:
    """Sovrapposizione pesata e smorzata tra la voce e il profilo lezione."""
    if not entry.terms:
        return 0.0
    ubiquity_cut = n_entries >= UBIQUITY_MIN_ENTRIES
    raw = 0.0
    for t in entry.terms:
        w = profile.get(t)
        if not w:
            continue
        d = df.get(t, 1)
        if ubiquity_cut and d / n_entries > UBIQUITY_RATIO:
            continue
        raw += w / (1.0 + math.log1p(d))
    if raw == 0.0:
        return 0.0
    # Le voci lunghe accumulano più coincidenze: penalità dolce.
    return raw / (1.0 + math.log1p(len(entry.terms) / 10.0))


def score_summary_entries(
    entries: list[Entry],
    profile: dict[str, float],
    df: dict[str, int],
    n_entries: int,
) -> list[ScoredEntry]:
    """Voci sopra soglia, ordinate per (score desc, categoria, indice)."""
    scored = [ScoredEntry(entry=e, score=score_entry(e, profile, df, n_entries)) for e in entries]
    kept = [s for s in scored if s.score >= MIN_ENTRY_SCORE]
    kept.sort(
        key=lambda s: (
            -s.score,
            CATEGORY_ORDER.index(s.entry.category),
            s.entry.index,
        )
    )
    return kept


# --- Selezione e rendering --------------------------------------------------


@dataclass
class LessonDocumentsContext:
    text: str
    stats: dict[str, Any] = field(default_factory=dict)
    # Termini delle voci selezionate (per la misura di copertura offline).
    selected_terms: list[str] = field(default_factory=list)


@dataclass
class _DocPlan:
    doc: CourseDocument
    reserved: bool
    summary: dict[str, Any]
    scored: list[ScoredEntry]
    doc_score: float
    language_mismatch: bool
    anonymous_index: int | None = None
    identity_text: str = ""
    extract_budget: int = 0

    @property
    def relevant(self) -> bool:
        return bool(self.scored)


def _doc_sort_key(doc: CourseDocument) -> tuple[datetime, str]:
    created = getattr(doc, "created_at", None) or datetime.min
    return (created.replace(tzinfo=None), str(doc.id))


def _abstract(summary: dict[str, Any], max_chars: int | None) -> str:
    text = " ".join(str(summary.get("abstract") or "").split())
    if max_chars is None or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    dot = cut.rfind(". ")
    return cut[: dot + 1] if dot > max_chars // 2 else cut.rstrip() + "…"


def _identity_block(
    plan: _DocPlan,
    *,
    abstract_max: int | None,
    overview: bool,
    introductory: bool,
) -> str:
    s = plan.summary
    parts = _document_header_lines(plan.doc, anonymous_index=plan.anonymous_index)
    if plan.language_mismatch:
        parts.append(LANGUAGE_NOTE)
    parts += ["", "### Abstract", _abstract(s, abstract_max)]
    if overview:
        concepts = [c for c in (s.get("key_concepts") or []) if isinstance(c, dict)]
        if concepts:
            parts += ["", "### Concetti chiave"]
            for c in concepts[:OVERVIEW_CONCEPTS]:
                parts.append(f"- **{c.get('name', '')}**: {c.get('explanation', '')}")
    if introductory:
        structure = [str(x) for x in (s.get("structure_outline") or []) if x]
        if structure:
            parts += ["", "### Struttura"]
            parts += [f"- {item}" for item in structure[:INTRO_OUTLINE_ITEMS]]
    tags = s.get("didactic_relevance_tags") or []
    if tags and (overview or introductory):
        parts += ["", f"### Tag rilevanza didattica: {', '.join(str(t) for t in tags)}"]
    return "\n".join(parts)


def _render_extracts(plan: _DocPlan, budget: int) -> tuple[str, list[ScoredEntry]]:
    """Riempimento greedy per score; rendering raggruppato per categoria."""
    chosen: list[ScoredEntry] = []
    remaining = budget
    for s in plan.scored:
        if s.chars <= remaining:
            chosen.append(s)
            remaining -= s.chars
    if not chosen:
        return "", []
    by_cat: dict[str, list[ScoredEntry]] = {}
    for s in chosen:
        by_cat.setdefault(s.entry.category, []).append(s)
    parts: list[str] = []
    for cat in CATEGORY_ORDER:
        items = by_cat.get(cat)
        if not items:
            continue
        items.sort(key=lambda s: s.entry.index)
        parts.append(f"### {CATEGORY_TITLES[cat]}")
        parts.extend(s.entry.render for s in items)
        parts.append("")
    return "\n".join(parts).rstrip("\n"), chosen


def select_documents_context_for_lesson(
    docs: list[CourseDocument],
    lesson: CourseLesson,
    *,
    total_max_chars: int,
    per_doc_max_chars: int,
    course_language: str,
) -> LessonDocumentsContext:
    """Blocco `## Documenti di riferimento` selezionato per la lezione."""
    ready = sorted(
        (
            d
            for d in docs
            if d.summary_status == "ready"
            and d.summary
            and getattr(d, "citation_policy", "citable") != "excluded"
        ),
        key=_doc_sort_key,
    )
    stats: dict[str, Any] = {
        "docs_ready": len(ready),
        "docs_relevant": 0,
        "docs_reserved": 0,
        "entries_selected": 0,
        "chars": 0,
        "fallback_overview": False,
        "introductory": bool(lesson.is_introductory),
    }
    if not ready:
        return LessonDocumentsContext(text=NO_DOCS_TEXT, stats=stats)

    profile = build_query_profile(lesson)
    reserved_index = document_citation_guard.build_identity_index(ready)
    course_lang = (course_language or "")[:2].lower()

    plans: list[_DocPlan] = []
    all_entries: list[tuple[_DocPlan, list[Entry]]] = []
    for doc in ready:
        reserved = getattr(doc, "citation_policy", "citable") == "content_only"
        summary = dict(doc.summary or {})
        entries = _entries_from_summary(summary, reserved=reserved)
        if reserved:
            # Best effort: una voce che nomina l'opera riservata è un leak
            # del riassunto, non del modello.
            entries = [
                e
                for e in entries
                if document_citation_guard.match_reserved(e.match_text, reserved_index) is None
            ]
        detected = str(summary.get("detected_language") or "")[:2].lower()
        plan = _DocPlan(
            doc=doc,
            reserved=reserved,
            summary=summary,
            scored=[],
            doc_score=0.0,
            language_mismatch=bool(course_lang and detected and detected != course_lang),
        )
        plans.append(plan)
        all_entries.append((plan, entries))

    flat = [e for _p, es in all_entries for e in es]
    df = compute_document_frequency(flat)
    n_entries = len(flat)
    for plan, entries in all_entries:
        plan.scored = score_summary_entries(entries, profile, df, n_entries)
        tag_terms = terms(
            " ".join(str(t) for t in (plan.summary.get("didactic_relevance_tags") or []))
        )
        abstract_terms = terms(plan.summary.get("abstract"))
        plan.doc_score = (
            sum(s.score for s in plan.scored)
            + 2.0 * len(tag_terms & profile.keys())
            + len(abstract_terms & profile.keys())
        )

    relevant = [p for p in plans if p.relevant]
    introductory = bool(lesson.is_introductory)
    overview = introductory or not relevant
    stats["docs_relevant"] = len(relevant)
    stats["fallback_overview"] = overview and not introductory

    # Ordine di rendering: citabili per (-doc_score, ordine base), poi i
    # riservati con lo stesso criterio; numerazione anonima dopo.
    base_order = {id(p): i for i, p in enumerate(plans)}
    citable = sorted(
        (p for p in plans if not p.reserved),
        key=lambda p: (-p.doc_score, base_order[id(p)]),
    )
    reserved_plans = sorted(
        (p for p in plans if p.reserved),
        key=lambda p: (-p.doc_score, base_order[id(p)]),
    )
    for i, p in enumerate(reserved_plans, start=1):
        p.anonymous_index = i
    stats["docs_reserved"] = len(reserved_plans)
    ordered = citable + reserved_plans

    # Quota identità (header + abstract [+ overview]).
    def build_identity(abstract_compact: int) -> int:
        total = 0
        for p in ordered:
            full = overview or p.relevant
            p.identity_text = _identity_block(
                p,
                abstract_max=None if full else abstract_compact,
                overview=overview,
                introductory=introductory,
            )
            total += len(p.identity_text) + 2
        return total

    identity_total = build_identity(ABSTRACT_COMPACT_CHARS)
    if identity_total > total_max_chars * IDENTITY_SHARE and not overview:
        identity_total = build_identity(ABSTRACT_MIN_CHARS)

    # Quota estratti proporzionale al doc_score, con clamp.
    extract_total = max(0, total_max_chars - identity_total)
    if relevant and not overview:
        if len(relevant) == 1:
            relevant[0].extract_budget = extract_total
        else:
            score_sum = sum(p.doc_score for p in relevant) or 1.0
            for p in relevant:
                share = int(extract_total * (p.doc_score / score_sum))
                p.extract_budget = max(PER_DOC_MIN_CHARS, min(per_doc_max_chars, share))

    chunks: list[str] = []
    selected_terms: list[str] = []
    if overview and not introductory:
        chunks.append(OVERVIEW_NOTE)
    framing_added = False
    for p in ordered:
        if p.reserved and not framing_added:
            chunks.append(_NON_CITABLE_FRAMING)
            framing_added = True
        block = p.identity_text
        if p.relevant and not overview and p.extract_budget > 0:
            extracts, chosen = _render_extracts(p, p.extract_budget)
            if extracts:
                block = f"{block}\n\n{extracts}"
                stats["entries_selected"] += len(chosen)
                selected_terms.extend(s.entry.match_text for s in chosen)
        chunks.append(block)

    text = "\n\n---\n\n".join(chunks)
    if len(text) > total_max_chars:
        cut = text.rfind("\n", 0, total_max_chars)
        text = text[: cut if cut > 0 else total_max_chars].rstrip()
        stats["truncated"] = True
    stats["chars"] = len(text)
    return LessonDocumentsContext(text=text, stats=stats, selected_terms=selected_terms)
