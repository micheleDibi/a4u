"""Vista dei fabbisogni di figure della lezione per l'editor (WP9, doc 18 §23.7).

Funzione pura, calcolata alla LETTURA (mai salvata): combina i fabbisogni
pronti (`figure_needs`), l'offerta o la fotografia dell'assegnazione
(`figure_assignment`), il contenuto attuale (`content_raw`, che il docente può
aver modificato), gli esiti della letteratura (`figures_gap_stats.needs`) e i
collegamenti manuali del docente (`figure_need_links`: «Non serve» o una
figura della lezione collegata al fabbisogno).

Stati:
- `placed`: una figura del fabbisogno è nel contenuto, citata nella sua
  sezione;
- `misplaced`: nel contenuto ma citata in un'altra sezione;
- `missing`: il piano aveva una figura, che nel contenuto non c'è;
- `uncovered`: nessuna figura (motivo: `no_candidate`, `reuse_cap`,
  `budget`, esito della letteratura);
- `dismissed`: il docente ha detto che non serve.

Solo testo già neutralizzato dalla validazione del PROMPT 22; nessun nome di
documento (la «Fonte» la scrive il render).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TAG_RE = re.compile(r"\[FIG:\s*([^\]\s]+)\s*\]")


def _tag_sections(content_raw: Mapping[str, Any]) -> dict[str, str]:
    """asset_id (minuscolo) → sezione della prima citazione ("" = fuori sezione)."""
    out: dict[str, str] = {}
    for section in content_raw.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        sid = str(section.get("section_id") or "")
        for match in _TAG_RE.finditer(str(section.get("content") or "")):
            out.setdefault(match.group(1).lower(), sid)
    for key in ("introduction", "summary"):
        for match in _TAG_RE.finditer(str(content_raw.get(key) or "")):
            out.setdefault(match.group(1).lower(), "")
    return out


def figure_needs_view(lesson: Any) -> list[dict[str, Any]] | None:
    """Stato dei fabbisogni della lezione; None senza fabbisogni pronti."""
    data = lesson.figure_needs if isinstance(lesson.figure_needs, Mapping) else None
    if lesson.figure_needs_status != "ready" or data is None:
        return None
    needs = [n for n in data.get("needs") or [] if isinstance(n, Mapping)]
    if not needs:
        return []
    assignment = lesson.figure_assignment if isinstance(lesson.figure_assignment, Mapping) else {}
    offers = assignment.get("offers") or {}
    unassigned = assignment.get("unassigned") or {}
    gap_stats = lesson.figures_gap_stats if isinstance(lesson.figures_gap_stats, Mapping) else {}
    raw_gap_needs = gap_stats.get("needs")
    gap_needs: Mapping[str, Any] = raw_gap_needs if isinstance(raw_gap_needs, Mapping) else {}
    links = lesson.figure_need_links if isinstance(lesson.figure_need_links, Mapping) else {}
    raw = lesson.content_raw if isinstance(lesson.content_raw, Mapping) else {}
    assets = [a for a in raw.get("visual_assets") or [] if isinstance(a, Mapping)]
    by_asset = {str(a.get("asset_id") or "").lower(): a for a in assets}
    by_figure = {
        str(a.get("content")): str(a.get("asset_id") or "")
        for a in assets
        if a.get("format") == "source_figure"
    }
    cited = _tag_sections(raw)
    outline = [
        str(s.get("section_id")) for s in lesson.section_outline or [] if isinstance(s, Mapping)
    ]
    order = {sid: i for i, sid in enumerate(outline)}
    ordered = sorted(
        enumerate(needs), key=lambda item: (order.get(str(item[1].get("section_id")), 99), item[0])
    )
    out: list[dict[str, Any]] = []
    for position, (_i, need) in enumerate(ordered, start=1):
        need_id = str(need.get("need_id") or "")
        section_id = str(need.get("section_id") or "")
        entry: dict[str, Any] = {
            "need_id": need_id,
            "label": f"N{position}",
            "section_id": section_id,
            "subject": str(need.get("subject") or ""),
            "priority": need.get("priority"),
            "representation": need.get("representation"),
            "sequence_group": need.get("sequence_group") or None,
            "sequence_index": need.get("sequence_index") or None,
        }
        link = links.get(need_id) if isinstance(links.get(need_id), Mapping) else None
        if link is not None and link.get("state") == "dismissed":
            out.append({**entry, "status": "dismissed"})
            continue
        asset_id: str | None = None
        linked = False
        if link is not None and link.get("state") == "linked":
            candidate = str(link.get("asset_id") or "")
            if candidate.lower() in by_asset:
                asset_id, linked = by_asset[candidate.lower()].get("asset_id"), True
        offer = offers.get(need_id) if isinstance(offers.get(need_id), Mapping) else None
        figure_id = str(offer.get("figure_id")) if offer and offer.get("figure_id") else None
        if asset_id is None and figure_id and figure_id in by_figure:
            asset_id = by_figure[figure_id]
        if asset_id is not None:
            where = cited.get(str(asset_id).lower())
            status = "placed" if where == section_id else "misplaced"
            out.append(
                {
                    **entry,
                    "status": status,
                    "asset_id": asset_id,
                    "cited_in": where,
                    "linked": linked,
                }
            )
            continue
        if figure_id:
            out.append({**entry, "status": "missing", "figure_id": figure_id})
            continue
        reason = unassigned.get(need_id)
        literature = gap_needs.get(need_id) if isinstance(gap_needs.get(need_id), Mapping) else None
        out.append(
            {
                **entry,
                "status": "uncovered",
                "reason": reason or ("not_planned" if not assignment else "no_candidate"),
                "literature": (literature or {}).get("status"),
            }
        )
    return out


def summary(view: list[dict[str, Any]] | None) -> dict[str, int] | None:
    """Conteggi per l'etichetta della lezione (must coperti su must attivi)."""
    if view is None:
        return None
    musts = [v for v in view if v.get("priority") == "must" and v["status"] != "dismissed"]
    return {
        "needs": len(view),
        "musts": len(musts),
        "musts_placed": sum(1 for v in musts if v["status"] in ("placed", "misplaced")),
        "uncovered": sum(1 for v in view if v["status"] in ("uncovered", "missing")),
        "misplaced": sum(1 for v in view if v["status"] == "misplaced"),
        "dismissed": sum(1 for v in view if v["status"] == "dismissed"),
    }


def figure_sequences(lesson: Any) -> list[tuple[str, list[str]]]:
    """Gruppi di sequenza del piano con almeno due figure nel contenuto:
    (gruppo, asset_id in ordine di sequenza). Per i blocchi delle Fasi 4 e
    5 (una slide per figura, nell'ordine; tempi delle slide di sequenza)."""
    view = figure_needs_view(lesson) or []
    groups: dict[str, list[tuple[int, str]]] = {}
    for entry in view:
        group = entry.get("sequence_group")
        if group and entry.get("asset_id") and entry["status"] in ("placed", "misplaced"):
            groups.setdefault(str(group), []).append(
                (int(entry.get("sequence_index") or 0), str(entry["asset_id"]))
            )
    return [
        (group, [asset for _i, asset in sorted(items)])
        for group, items in groups.items()
        if len(items) >= 2
    ]
