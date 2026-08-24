"""Merge deterministico dei fatti per-chunk (Blocco 1, fase "merge").

Nessuna chiamata LLM: dedup per chiave normalizzata + near-dup
(difflib con BUCKETING per chiave/banda di lunghezza — mai il pairwise
O(n²) puro), conteggio occorrenze, ordinamento per prima occorrenza,
cap per categoria dimensionati sull'OUTPUT del reduce (~30k char totali
di fatti, così l'output JSON sta nel max_tokens del reduce senza
troncamenti). I campi-lista dell'Appendice A non passano mai per una
riscrittura in prosa: zero diluizione da parafrasi ripetuta.

CPU-bound: il chiamante DEVE eseguirlo in `asyncio.to_thread` (il
backend è un singolo processo/event-loop condiviso con l'API).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Cap per categoria (selezione per occorrenze poi prima posizione;
# presentazione in ordine di prima occorrenza). Costanti di modulo: non
# c'è motivo di variarle per ambiente.
DEFINITIONS_CAP = 60
CONCEPTS_CAP = 50
EXAMPLES_CAP = 25
FORMULAS_CAP = 40
REFERENCES_CAP = 40
OUTLINE_CAP = 80
TAGS_CAP = 12
# Budget totale (char) del rendering dei fatti nel prompt reduce:
# dimensionato perché l'output plausibile stia nei 12k token del reduce.
FACTS_RENDER_MAX_CHARS = 30_000
NEAR_DUP_RATIO = 0.92


def _normalize(value: str) -> str:
    s = unicodedata.normalize("NFKD", (value or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else " " for c in s)
    return " ".join(s.split())


@dataclass
class _Entry:
    data: dict
    key: str
    count: int = 1
    first: int = 0


def _dedup(
    items: list[tuple[dict, int]],
    key_field: str,
    *,
    text_field: str,
) -> list[_Entry]:
    """Dedup esatta per chiave normalizzata + near-dup con bucketing.

    `items` = [(item_dict, posizione_di_scoperta)]. A parità si tiene la
    versione col testo più lungo (più informativa) e si sommano le
    occorrenze.
    """
    by_key: dict[str, _Entry] = {}
    order: list[_Entry] = []
    for data, pos in items:
        key = _normalize(str(data.get(key_field, "")))
        if not key:
            continue
        entry = by_key.get(key)
        if entry is not None:
            entry.count += 1
            if len(str(data.get(text_field, ""))) > len(
                str(entry.data.get(text_field, ""))
            ):
                entry.data = data
            continue
        entry = _Entry(data=data, key=key, first=pos)
        by_key[key] = entry
        order.append(entry)

    # Near-dup: confronto SOLO dentro bucket (banda di lunghezza della
    # chiave + primo carattere) per evitare il pairwise completo.
    buckets: dict[tuple[int, str], list[_Entry]] = {}
    survivors: list[_Entry] = []
    for entry in order:
        bucket_key = (len(entry.key) // 8, entry.key[:1])
        merged = False
        for near in (
            buckets.get(bucket_key, [])
            + buckets.get((bucket_key[0] - 1, bucket_key[1]), [])
            + buckets.get((bucket_key[0] + 1, bucket_key[1]), [])
        ):
            if (
                SequenceMatcher(None, entry.key, near.key).ratio()
                >= NEAR_DUP_RATIO
            ):
                near.count += entry.count
                if len(str(entry.data.get(text_field, ""))) > len(
                    str(near.data.get(text_field, ""))
                ):
                    near.data = entry.data
                merged = True
                break
        if not merged:
            buckets.setdefault(bucket_key, []).append(entry)
            survivors.append(entry)
    return survivors


def _select(entries: list[_Entry], cap: int) -> list[_Entry]:
    """Selezione prioritaria (occorrenze desc, prima posizione asc) poi
    presentazione in ordine di prima occorrenza."""
    prioritized = sorted(entries, key=lambda e: (-e.count, e.first))[:cap]
    return sorted(prioritized, key=lambda e: e.first)


@dataclass
class MergedFacts:
    definitions: list[_Entry] = field(default_factory=list)
    key_concepts: list[_Entry] = field(default_factory=list)
    examples_or_cases: list[_Entry] = field(default_factory=list)
    formulas_or_rules: list[_Entry] = field(default_factory=list)
    authors_and_references: list[_Entry] = field(default_factory=list)
    structure_outline: list[str] = field(default_factory=list)
    didactic_relevance_tags: list[str] = field(default_factory=list)
    detected_language: str = ""
    # (chunk_index, pagine "p. A-B" | "blocco N", testo)
    abstracts: list[tuple[int, str, str]] = field(default_factory=list)


def merge_chunk_facts(chunk_results: list[dict]) -> MergedFacts:
    """Merge dei risultati map (dict `ChunkFactsOut`, in ordine di
    chunk_index)."""
    pos = 0

    def _collect(category: str) -> list[tuple[dict, int]]:
        nonlocal pos
        collected: list[tuple[dict, int]] = []
        for result in chunk_results:
            for item in result.get(category) or []:
                collected.append((item, pos))
                pos += 1
        return collected

    merged = MergedFacts()
    merged.definitions = _select(
        _dedup(_collect("definitions"), "term", text_field="definition"),
        DEFINITIONS_CAP,
    )
    merged.key_concepts = _select(
        _dedup(_collect("key_concepts"), "name", text_field="explanation"),
        CONCEPTS_CAP,
    )
    merged.examples_or_cases = _select(
        _dedup(
            _collect("examples_or_cases"), "title", text_field="synthesis"
        ),
        EXAMPLES_CAP,
    )
    merged.formulas_or_rules = _select(
        _dedup(
            _collect("formulas_or_rules"),
            "latex_or_text",
            text_field="meaning",
        ),
        FORMULAS_CAP,
    )
    merged.authors_and_references = _select(
        _dedup(
            _collect("authors_and_references"), "value", text_field="value"
        ),
        REFERENCES_CAP,
    )

    seen_outline: set[str] = set()
    for result in chunk_results:
        for item in result.get("outline_items") or []:
            key = _normalize(str(item))
            if key and key not in seen_outline:
                seen_outline.add(key)
                merged.structure_outline.append(str(item))
                if len(merged.structure_outline) >= OUTLINE_CAP:
                    break
        if len(merged.structure_outline) >= OUTLINE_CAP:
            break

    tag_counts: dict[str, tuple[int, str]] = {}
    for result in chunk_results:
        for tag in result.get("candidate_tags") or []:
            key = _normalize(str(tag))
            if not key:
                continue
            count, original = tag_counts.get(key, (0, str(tag)))
            tag_counts[key] = (count + 1, original)
    merged.didactic_relevance_tags = [
        original
        for _key, (_count, original) in sorted(
            tag_counts.items(), key=lambda kv: -kv[1][0]
        )[:TAGS_CAP]
    ]

    lang_counts: dict[str, int] = {}
    for result in chunk_results:
        lang = str(result.get("detected_language") or "").strip().lower()
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    if lang_counts:
        merged.detected_language = max(
            sorted(lang_counts), key=lambda k: lang_counts[k]
        )

    for i, result in enumerate(chunk_results):
        pages = str(result.get("_pages_label") or f"blocco {i + 1}")
        abstract = str(result.get("chunk_abstract") or "").strip()
        if abstract:
            merged.abstracts.append((i, pages, abstract))

    return merged


def _render_entry_line(category: str, entry: _Entry) -> str:
    d = entry.data
    marker = f"[x{entry.count}] " if entry.count > 1 else ""
    if category == "definitions":
        return f"- {marker}{d.get('term', '')}: {d.get('definition', '')}"
    if category == "key_concepts":
        return f"- {marker}{d.get('name', '')}: {d.get('explanation', '')}"
    if category == "examples_or_cases":
        return f"- {marker}{d.get('title', '')}: {d.get('synthesis', '')}"
    if category == "formulas_or_rules":
        return (
            f"- {marker}{d.get('label', '')} | {d.get('latex_or_text', '')}"
            f" | {d.get('meaning', '')}"
        )
    return f"- {marker}[{d.get('type', '')}] {d.get('value', '')}"


def render_merged_facts(merged: MergedFacts) -> str:
    """Rendering compatto dei fatti per il prompt reduce, con trim al
    budget globale: se si eccede, si scartano le voci a priorità più
    bassa (meno occorrenze, più tardive) round-robin tra le categorie."""
    sections: list[tuple[str, str, list[_Entry]]] = [
        ("definitions", "DEFINIZIONI", list(merged.definitions)),
        ("key_concepts", "CONCETTI CHIAVE", list(merged.key_concepts)),
        ("examples_or_cases", "ESEMPI E CASI", list(merged.examples_or_cases)),
        ("formulas_or_rules", "FORMULE E REGOLE", list(merged.formulas_or_rules)),
        (
            "authors_and_references",
            "AUTORI E RIFERIMENTI CITATI",
            list(merged.authors_and_references),
        ),
    ]

    def _total_chars() -> int:
        total = 0
        for category, _title, entries in sections:
            for entry in entries:
                total += len(_render_entry_line(category, entry)) + 1
        return total

    while _total_chars() > FACTS_RENDER_MAX_CHARS:
        candidates = [s for s in sections if len(s[2]) > 1]
        if not candidates:
            break
        # Scarta la voce a priorità minima della categoria più lunga.
        category, _title, entries = max(candidates, key=lambda s: len(s[2]))
        weakest = min(entries, key=lambda e: (e.count, -e.first))
        entries.remove(weakest)

    parts: list[str] = []
    for category, title, entries in sections:
        if not entries:
            continue
        parts.append(f"### {title} ({len(entries)})")
        for entry in entries:
            parts.append(_render_entry_line(category, entry))
        parts.append("")
    if merged.structure_outline:
        parts.append("### STRUTTURA DEL DOCUMENTO (in ordine)")
        for item in merged.structure_outline:
            parts.append(f"- {item}")
        parts.append("")
    if merged.didactic_relevance_tags:
        parts.append(
            "### TAG CANDIDATI: "
            + ", ".join(merged.didactic_relevance_tags)
        )
    return "\n".join(parts).strip()
