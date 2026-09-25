"""Selezione per lezione delle figure di fonte da proporre al PROMPT 3 (J-Q2).

Gemella di `lesson_document_selection`: stesso profilo lessicale della
lezione (`build_query_profile`: titolo, temi obbligatori, scaletta,
obiettivi, sinossi) confrontato con i termini di ogni figura (descrizione
e parole chiave della Vision nella lingua del corso e in inglese,
didascalia originale; il contesto della pagina pesa meno). Il PROMPT 3 fa
da riordinatore: riceve al più `max_items` candidate, con id stabili
`SRC-<hex8>`, e sceglie quali inserire.

- Candidata: punteggio ≥ `CANDIDATE_MIN_SCORE` e almeno un termine
  «forte» (titolo, temi, titoli delle sezioni).
- Pertinente: punteggio ≥ `RELEVANT_MIN_SCORE` e almeno due termini forti;
  conta per i buchi (WP5: letteratura aperta sotto
  `FIGURE_SOURCE_MIN_PER_LESSON`).
- Le lezioni di verifica non ricevono catalogo.
- Il filtro di visibilità (politica del documento, licenza, esclusione,
  qualità) lo applica il chiamante prima: qui arrivano solo figure
  ammesse.

Il catalogo non contiene mai la riga «Fonte» né il nome del documento: la
fonte la scrive il render, non il modello.

Funzioni pure: nessun accesso a Settings, DB o I/O.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.prompt_safety import neutralize_third_party_text
from app.models.course_lesson import CourseLesson
from app.services.lesson_document_selection import (
    UBIQUITY_MIN_ENTRIES,
    UBIQUITY_RATIO,
    WEIGHT_SECTION_TITLE,
    build_query_profile,
    terms,
)
from app.services.source_caption import clean_caption

CANDIDATE_MIN_SCORE = 1.0
RELEVANT_MIN_SCORE = 2.0
CONTEXT_WEIGHT = 0.5
DESCRIPTION_MAX_CHARS = 360
CAPTION_MAX_CHARS = 240
KEYWORDS_MAX = 8
REF_PREFIX = "SRC-"


class FigureLike(Protocol):
    id: uuid.UUID
    document_id: uuid.UUID | None
    page: int | None
    locator: str
    kind: str | None
    description: str | None
    keywords: dict[str, Any] | None
    source_caption: str | None
    source_label: str | None
    context_excerpt: str | None


@dataclass(frozen=True)
class FigureCandidate:
    figure_id: uuid.UUID
    ref: str
    score: float
    strong_terms: int
    relevant: bool
    line: str


@dataclass
class FigureCatalog:
    candidates: list[FigureCandidate] = field(default_factory=list)
    text: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def refs(self) -> dict[str, uuid.UUID]:
        return {c.ref: c.figure_id for c in self.candidates}

    @property
    def relevant_count(self) -> int:
        return sum(1 for c in self.candidates if c.relevant)


def figure_ref(figure_id: uuid.UUID, length: int = 8) -> str:
    """Id stabile della figura nel prompt (`SRC-` + prime cifre esadecimali)."""
    return f"{REF_PREFIX}{figure_id.hex[:length]}"


def _keywords(fig: FigureLike) -> list[str]:
    raw = fig.keywords if isinstance(fig.keywords, dict) else {}
    out: list[str] = []
    for key in ("course", "en"):
        for kw in raw.get(key) or []:
            if isinstance(kw, str) and kw.strip() and kw.strip() not in out:
                out.append(kw.strip())
    return out


def figure_terms(fig: FigureLike) -> tuple[frozenset[str], frozenset[str]]:
    """(termini principali, termini del contesto della pagina)."""
    main = terms(" ".join([fig.description or "", fig.source_caption or "", *_keywords(fig)]))
    context = terms(fig.context_excerpt or "") - main
    return frozenset(main), frozenset(context)


def _clip(text: str | None, limit: int) -> str:
    # Testo di terzi (didascalia del documento, output della Vision): va nel
    # PROMPT 3, quindi passa dalla neutralizzazione anche qui.
    cleaned = " ".join(neutralize_third_party_text(text or "", limit + 200).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "…"


def score_figure(
    main: frozenset[str],
    context: frozenset[str],
    profile: dict[str, float],
    df: dict[str, int],
    n_figures: int,
) -> tuple[float, int]:
    """(punteggio smorzato, numero di termini forti in comune)."""
    ubiquity_cut = n_figures >= UBIQUITY_MIN_ENTRIES
    raw = 0.0
    strong = 0
    for weight_factor, bag in ((1.0, main), (CONTEXT_WEIGHT, context)):
        for term in bag:
            w = profile.get(term)
            if not w:
                continue
            d = df.get(term, 1)
            if ubiquity_cut and d / n_figures > UBIQUITY_RATIO:
                continue
            raw += weight_factor * w / (1.0 + math.log1p(d))
            if weight_factor == 1.0 and w >= WEIGHT_SECTION_TITLE:
                strong += 1
    if raw == 0.0:
        return 0.0, 0
    size = len(main) + len(context) * CONTEXT_WEIGHT
    return raw / (1.0 + math.log1p(size / 10.0)), strong


def _catalog_line(ref: str, fig: FigureLike) -> str:
    parts = [f"- {ref}"]
    if fig.kind:
        parts.append(f"tipo: {fig.kind}")
    if fig.source_caption:
        # Senza la coda «Fonte: …» del documento: la fonte non entra mai nel
        # catalogo (la scrive il render).
        caption, _trimmed = clean_caption(fig.source_caption)
        parts.append(f"didascalia originale: {_clip(caption, CAPTION_MAX_CHARS)}")
    if fig.description:
        parts.append(f"descrizione: {_clip(fig.description, DESCRIPTION_MAX_CHARS)}")
    keywords = [_clip(k, 60) for k in _keywords(fig)[:KEYWORDS_MAX]]
    if keywords:
        parts.append("parole chiave: " + ", ".join(k for k in keywords if k))
    return " | ".join(parts)


def select_figure_candidates(
    figures: Sequence[FigureLike],
    lesson: CourseLesson,
    *,
    max_items: int,
    max_chars: int,
    demote: Callable[[Any], bool] | None = None,
) -> FigureCatalog:
    """Candidate della lezione in ordine di pertinenza, entro i tetti.

    `demote` (risoluzione effettiva, doc 18 §22): le figure per cui è vero
    (classe `low`) vanno in coda, dopo tutte le altre pertinenti — una
    figura a bassa risoluzione solo senza alternativa migliore."""
    if lesson.is_assessment or not figures or max_items <= 0:
        return FigureCatalog(stats={"figures": len(figures), "candidates": 0})
    profile = build_query_profile(lesson)
    bags = [(fig, *figure_terms(fig)) for fig in figures]
    df: dict[str, int] = {}
    for _fig, main, context in bags:
        for term in main | context:
            df[term] = df.get(term, 0) + 1
    scored = []
    for fig, main, context in bags:
        score, strong = score_figure(main, context, profile, df, len(figures))
        if score >= CANDIDATE_MIN_SCORE and strong >= 1:
            scored.append((score, strong, fig))
    scored.sort(
        key=lambda s: (
            bool(demote(s[2])) if demote is not None else False,
            -s[0],
            str(s[2].document_id),
            s[2].page or 0,
            s[2].locator,
        )
    )

    candidates: list[FigureCandidate] = []
    used_refs: set[str] = set()
    total = 0
    for score, strong, fig in scored:
        if len(candidates) >= max_items:
            break
        ref = figure_ref(fig.id)
        length = 8
        while ref in used_refs and length < 32:
            length += 4
            ref = figure_ref(fig.id, length)
        line = _catalog_line(ref, fig)
        if total + len(line) + 1 > max_chars:
            continue
        used_refs.add(ref)
        total += len(line) + 1
        candidates.append(
            FigureCandidate(
                figure_id=fig.id,
                ref=ref,
                score=round(score, 3),
                strong_terms=strong,
                relevant=score >= RELEVANT_MIN_SCORE and strong >= 2,
                line=line,
            )
        )
    return FigureCatalog(
        candidates=candidates,
        text="\n".join(c.line for c in candidates),
        stats={
            "figures": len(figures),
            "above_threshold": len(scored),
            "candidates": len(candidates),
            "relevant": sum(1 for c in candidates if c.relevant),
            "chars": total,
        },
    )
