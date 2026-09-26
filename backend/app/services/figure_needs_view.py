"""Vista dei fabbisogni di figure della lezione per l'editor (WP9, doc 18 §23.7).

Funzione pura, calcolata alla LETTURA (mai salvata): combina i fabbisogni
pronti (`figure_needs`), l'offerta o la fotografia dell'assegnazione
(`figure_assignment`), il contenuto attuale (`content_raw`, che il docente può
aver modificato), gli esiti della letteratura (`figures_gap_stats.needs`) e i
collegamenti manuali del docente (`figure_need_links`: «Non serve» o una
figura della lezione collegata al fabbisogno).

Stati:
- `placed`: una figura del fabbisogno è nel contenuto, citata nella sua
  sezione (anche dentro un esempio o una tabella della sezione);
- `misplaced`: nel contenuto ma citata in un'altra sezione (`cited_in` ""
  = introduzione o sintesi);
- `missing`: il piano aveva una figura, che nel contenuto non c'è; oppure
  la figura c'è ma il testo non la cita (`reason: not_cited`);
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

_TAG_RE = re.compile(r"\[FIG:\s*([^\]\s]+)\s*\]", re.IGNORECASE)
_EMBED_RE = re.compile(r"\[(EX|TAB):\s*([^\]\s]+)\s*\]", re.IGNORECASE)
# Stati della collocazione con una figura nel contenuto.
_SETTLED_WITH = ("placed", "misplaced", "auto_placed")


def _embedded_texts(content_raw: Mapping[str, Any], text: str) -> list[str]:
    """Testi degli esempi e delle tabelle citati (`[EX:id]`, `[TAB:id]`) in
    `text`: le figure che contengono appartengono alla sezione del tag."""
    examples = {
        str(e.get("example_id") or "").lower(): str(e.get("content") or "")
        for e in content_raw.get("examples") or []
        if isinstance(e, Mapping)
    }
    tables = {
        str(t.get("table_id") or "").lower(): str(t.get("markdown") or "")
        for t in content_raw.get("tables") or []
        if isinstance(t, Mapping)
    }
    out: list[str] = []
    for match in _EMBED_RE.finditer(text):
        source = examples if match.group(1).upper() == "EX" else tables
        out.append(source.get(match.group(2).lower(), ""))
    return out


def _tag_sections(content_raw: Mapping[str, Any]) -> dict[str, str]:
    """asset_id (minuscolo) → sezione della prima citazione ("" =
    introduzione o sintesi); le figure non citate non compaiono."""
    out: dict[str, str] = {}
    fields: list[tuple[str, str]] = [
        (str(section.get("section_id") or ""), str(section.get("content") or ""))
        for section in content_raw.get("sections") or []
        if isinstance(section, Mapping)
    ]
    fields += [("", str(content_raw.get(key) or "")) for key in ("introduction", "summary")]
    for sid, text in fields:
        for chunk in [text, *_embedded_texts(content_raw, text)]:
            for match in _TAG_RE.finditer(chunk):
                out.setdefault(match.group(1).lower(), sid)
    return out


def _plan_asset(
    need_id: str,
    assignment: Mapping[str, Any],
    by_asset: Mapping[str, Mapping[str, Any]],
    by_figure: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """(asset nel contenuto, figura del piano) del fabbisogno. Prima il
    legame della fotografia (anche un'alternativa scelta dal modello), poi
    l'asset della collocazione, poi la figura offerta e le alternative."""
    bound = assignment.get("bound") if assignment.get("state") == "settled" else None
    if isinstance(bound, Mapping) and bound.get(need_id):
        figure = str(bound[need_id])
        if figure in by_figure:
            return by_figure[figure], figure
    placement = assignment.get("placement")
    needs = placement.get("needs") if isinstance(placement, Mapping) else None
    info = needs.get(need_id) if isinstance(needs, Mapping) else None
    if isinstance(info, Mapping) and info.get("status") in _SETTLED_WITH:
        asset = by_asset.get(str(info.get("asset_id") or "").lower())
        if asset is not None and asset.get("format") == "source_figure":
            return str(asset.get("asset_id")), str(asset.get("content"))
    offers = assignment.get("offers") or {}
    offer = offers.get(need_id) if isinstance(offers.get(need_id), Mapping) else None
    figure_id = str(offer.get("figure_id")) if offer and offer.get("figure_id") else None
    if figure_id and figure_id in by_figure:
        return by_figure[figure_id], figure_id
    alternatives = assignment.get("alternatives") or {}
    for alt in alternatives.get(need_id) or [] if isinstance(alternatives, Mapping) else []:
        if isinstance(alt, Mapping) and str(alt.get("figure_id")) in by_figure:
            return by_figure[str(alt.get("figure_id"))], str(alt.get("figure_id"))
    return None, figure_id


def figure_needs_view(lesson: Any) -> list[dict[str, Any]] | None:
    """Stato dei fabbisogni della lezione; None senza fabbisogni pronti."""
    data = lesson.figure_needs if isinstance(lesson.figure_needs, Mapping) else None
    if lesson.figure_needs_status != "ready" or data is None:
        return None
    needs = [n for n in data.get("needs") or [] if isinstance(n, Mapping)]
    if not needs:
        return []
    assignment = lesson.figure_assignment if isinstance(lesson.figure_assignment, Mapping) else {}
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
                asset_id, linked = str(by_asset[candidate.lower()].get("asset_id")), True
        planned, figure_id = _plan_asset(need_id, assignment, by_asset, by_figure)
        if asset_id is None:
            asset_id = planned
        if asset_id is not None:
            where = cited.get(asset_id.lower())
            if where is None:
                # Nel contenuto ma mai citata dal testo: non conta come
                # collocata (il PDF la accoda in fondo alla lezione).
                out.append(
                    {
                        **entry,
                        "status": "missing",
                        "reason": "not_cited",
                        "asset_id": asset_id,
                        "linked": linked,
                    }
                )
                continue
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
    """Gruppi di sequenza del piano con almeno due figure DISTINTE citate
    nel contenuto: (gruppo, asset_id in ordine di sequenza). Per i blocchi
    delle Fasi 4 e 5 (una slide per figura, nell'ordine; tempi delle slide
    di sequenza). Col piano spento nessuna sequenza: i messaggi dei PROMPT
    5 e 6 tornano quelli di prima."""
    from app.services.figure_plan_service import plan_active

    if not plan_active():
        return []
    view = figure_needs_view(lesson) or []
    groups: dict[str, list[tuple[int, str]]] = {}
    for entry in view:
        group = entry.get("sequence_group")
        if group and entry.get("asset_id") and entry["status"] in ("placed", "misplaced"):
            groups.setdefault(str(group), []).append(
                (int(entry.get("sequence_index") or 0), str(entry["asset_id"]))
            )
    out: list[tuple[str, list[str]]] = []
    for group, items in groups.items():
        assets: list[str] = []
        for _index, asset in sorted(items):
            if asset.lower() not in {a.lower() for a in assets}:
                assets.append(asset)
        if len(assets) >= 2:
            out.append((group, assets))
    return out
