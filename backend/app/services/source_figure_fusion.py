"""Fusione delle figure di fonte scelte dal PROMPT 3 negli asset della lezione.

Il modello sceglie le figure del catalogo in un array separato
(`source_figures`: id `SRC-…`, didascalia e testo alternativo nella lingua
del corso) e le cita nel testo con `[FIG:SRC-…]` come ogni altra figura.
Qui, lato server e prima della validazione degli asset:

- ogni scelta valida diventa un asset `format="source_figure"` con
  `content` = UUID della riga `course_document_figure` (riferimento vivo:
  byte e riga «Fonte» li risolve il render);
- scelte sconosciute, ripetute o non citate nel testo si scartano; oltre il
  budget (b) si tengono le prime citate e i tag delle altre spariscono;
- i tag `[FIG:SRC-…]` rimasti senza asset si tolgono dal testo;
- un asset GENERATO con id `SRC-…` (collisione con lo spazio dei nomi delle
  figure di fonte) viene rinominato in modo deterministico, tag compresi,
  invece di far fallire la lezione;
- la didascalia non può portare la fonte (la scrive il render): una CODA
  «Fonte: …», «Source: …», «Quelle: …», «出典» (anche con i due punti a
  larghezza piena), «(Rossi et al., 2019)», «©», «Courtesy of», «tratto
  da …» dopo un separatore viene tolta (`source_caption`); una
  didascalia che è soltanto la fonte resta com'è (meglio una fonte
  ripetuta che una figura senza didascalia), e il testo che precede non si
  tocca («Energy sources: solar…», «nel tratto da A a B» restano interi).

Il resto del contenuto non cambia (budget (a) delle figure generate intatto).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.schemas.course_lesson_content import (
    SOURCE_FIGURE_FORMAT,
    LessonContentOutput,
    LessonContentVisualAsset,
)
from app.services.lesson_figure_selection import REF_PREFIX
from app.services.source_caption import clean_caption

__all__ = [
    "FusionReport",
    "clean_caption",
    "fuse_source_figures",
    "remove_source_figures",
    "rename_generated_src_ids",
]

# `FIG` sensibile alle maiuscole come `figure_numbering.FIG_REF_RE`: un
# `[fig:…]` non è un rimando per la numerazione, quindi non lo è neanche per
# la fusione (Fase D).
_TAG_RE = re.compile(r"\[FIG:\s*([^\]\s]+)\s*\]")


@dataclass
class FusionReport:
    added: list[str] = field(default_factory=list)
    dropped_unknown: list[str] = field(default_factory=list)
    dropped_uncited: list[str] = field(default_factory=list)
    dropped_over_budget: list[str] = field(default_factory=list)
    dropped_duplicate: list[str] = field(default_factory=list)
    removed_tags: list[str] = field(default_factory=list)
    renamed_generated: dict[str, str] = field(default_factory=dict)
    captions_trimmed: list[str] = field(default_factory=list)
    # Id generati uguali a un id del catalogo: rinominati con il loro tag,
    # la figura di fonte omonima resta senza citazione (caso ambiguo).
    renamed_catalog_collisions: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {k: v for k, v in self.__dict__.items() if v}


def _body_fields(output: LessonContentOutput) -> list[str]:
    return [output.introduction or "", *(s.content for s in output.sections), output.summary or ""]


def _map_text(output: LessonContentOutput, fn: object) -> None:
    """Applica `fn(str) -> str` a tutti i campi di testo che portano tag."""
    assert callable(fn)
    output.introduction = fn(output.introduction)
    for section in output.sections:
        section.content = fn(section.content)
    output.summary = fn(output.summary)
    for example in output.examples:
        example.content = fn(example.content)
    for table in output.tables:
        table.markdown = fn(table.markdown)


def _rename_tags(text: str, renames: Mapping[str, str]) -> str:
    def sub(match: re.Match[str]) -> str:
        new = renames.get(match.group(1).lower())
        return f"[FIG:{new}]" if new else match.group(0)

    return _TAG_RE.sub(sub, text)


def _drop_tags(text: str, ids: set[str]) -> str:
    """Toglie i tag degli id dati: una riga che contiene solo il tag sparisce
    e, SOLO in quel punto, le righe vuote che la circondavano si riducono a
    una; un tag in mezzo al testo sparisce da solo. Un campo senza quei tag
    torna identico (anche le sue righe vuote multiple, per esempio nel
    codice)."""
    if not ids or not any(m.group(1).lower() in ids for m in _TAG_RE.finditer(text)):
        return text
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        match = _TAG_RE.fullmatch(line.strip())
        if match and match.group(1).lower() in ids:
            # Riga di solo tag tolta: se sopra c'è già una riga vuota, anche
            # quella vuota che seguirà verrà assorbita.
            if out and out[-1].strip() == "":
                out.append("\x00")
            continue
        if line.strip() == "" and out and out[-1] == "\x00":
            out.pop()
            continue
        out.append(line)
    text = "\n".join(line for line in out if line != "\x00")
    return _TAG_RE.sub(lambda m: "" if m.group(1).lower() in ids else m.group(0), text)


def rename_generated_src_ids(output: LessonContentOutput) -> dict[str, str]:
    """Asset generati con id nello spazio `SRC-`: nuovi id `fig-src-N`."""
    taken = {a.asset_id.strip().lower() for a in output.visual_assets}
    renames: dict[str, str] = {}
    counter = 1
    for asset in output.visual_assets:
        # Id con spazi ai bordi (` SRC-x`): stesso trattamento (Fase D).
        if asset.format == SOURCE_FIGURE_FORMAT or not asset.asset_id.strip().upper().startswith(
            REF_PREFIX
        ):
            continue
        while f"fig-src-{counter}" in taken:
            counter += 1
        new_id = f"fig-src-{counter}"
        taken.add(new_id)
        renames[asset.asset_id.strip().lower()] = new_id
        asset.asset_id = new_id
    if renames:
        _map_text(output, lambda text: _rename_tags(text, renames))
    return renames


def fuse_source_figures(
    output: LessonContentOutput,
    refs: Mapping[str, uuid.UUID],
    *,
    max_items: int,
) -> FusionReport:
    """Porta le scelte di `output.source_figures` in `output.visual_assets`
    e svuota `source_figures`. `refs`: id del catalogo → id della figura."""
    report = FusionReport(renamed_generated=rename_generated_src_ids(output))
    catalog = {ref.lower(): (ref, fid) for ref, fid in refs.items()}
    report.renamed_catalog_collisions = sorted(set(report.renamed_generated) & set(catalog))
    body = "\n".join(_body_fields(output))
    first_citation: dict[str, int] = {}
    for match in _TAG_RE.finditer(body):
        first_citation.setdefault(match.group(1).lower(), match.start())

    chosen: list[tuple[int, str, uuid.UUID, str, str]] = []
    seen: set[str] = set()
    for choice in output.source_figures:
        key = choice.figure.strip().lower()
        if key not in catalog:
            report.dropped_unknown.append(choice.figure)
            continue
        if key in seen:
            report.dropped_duplicate.append(choice.figure)
            continue
        seen.add(key)
        if key not in first_citation:
            report.dropped_uncited.append(catalog[key][0])
            continue
        ref, fid = catalog[key]
        chosen.append((first_citation[key], ref, fid, choice.caption, choice.alt_text))
    chosen.sort(key=lambda item: item[0])
    kept = chosen[: max(0, max_items)]
    report.dropped_over_budget = [ref for _, ref, *_ in chosen[max(0, max_items) :]]

    for _, ref, fid, caption, alt_text in kept:
        caption, trimmed = clean_caption(caption)
        alt_text, _ = clean_caption(alt_text)
        if trimmed:
            report.captions_trimmed.append(ref)
        output.visual_assets.append(
            LessonContentVisualAsset(
                asset_id=ref,
                format=SOURCE_FIGURE_FORMAT,
                content=str(fid),
                caption=caption[:600],
                alt_text=alt_text[:400],
            )
        )
        report.added.append(ref)

    # Tag `SRC-` senza asset (scelte scartate o mai dichiarate): via dal testo.
    valid = {a.asset_id.strip().lower() for a in output.visual_assets}
    orphan = {
        m.group(1).lower()
        for field_text in _body_fields(output)
        + [ex.content for ex in output.examples]
        + [t.markdown for t in output.tables]
        for m in _TAG_RE.finditer(field_text)
        if m.group(1).upper().startswith(REF_PREFIX) and m.group(1).lower() not in valid
    }
    if orphan:
        report.removed_tags = sorted(orphan)
        _map_text(output, lambda text: _drop_tags(text, orphan))
    output.source_figures = []
    return report


def remove_source_figures(output: LessonContentOutput, asset_ids: set[str]) -> None:
    """Toglie asset di fonte già fusi (ricontrollo TOCTOU) e i loro tag."""
    lowered = {a.lower() for a in asset_ids}
    output.visual_assets = [a for a in output.visual_assets if a.asset_id.lower() not in lowered]
    _map_text(output, lambda text: _drop_tags(text, lowered))
