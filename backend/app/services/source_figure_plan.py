"""Blocco del piano delle figure nel PROMPT 3 e collocazione (WP8, doc 18 §23.6).

Con il piano attivo e un'offerta per la lezione (`figure_assignment`), il
catalogo delle figure di fonte del messaggio user non è più la lista
lessicale ma il **catalogo del piano**, ordinato per sezione:

- per ogni fabbisogno COPERTO: la figura assegnata e al più
  `MAX_ALTERNATIVES` alternative dello stesso fabbisogno; le opzioni sono
  disgiunte (una figura compare una volta sola nel catalogo);
- in coda al più `MAX_RESIDUAL` figure facoltative del catalogo lessicale;
- i fabbisogni scoperti non compaiono mai (il modello non deve inventare);
- al taglio (`max_chars`) si toglie prima il residuo, poi le alternative
  degli should, poi quelle dei must, poi gli should: mai la figura
  assegnata a un must.

Dopo la fusione (`source_figure_fusion`), `apply_placement` confronta le
scelte del modello col piano, SENZA spostare nulla nel testo:

- `placed`: una figura del fabbisogno è citata nella sua sezione;
- `misplaced`: citata in un'altra sezione (avviso in editor);
- `auto_placed`: must non scelto, inserito dal sistema in fondo alla sua
  sezione (ancora sicura: la sezione c'è) se il budget lo consente;
- `missing`: offerto ma non collocato;
- `order_warning` sui gruppi di sequenza citati fuori ordine;
- due figure per lo stesso fabbisogno: resta la prima citata.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.prompt_safety import neutralize_third_party_text
from app.schemas.course_lesson_content import (
    SOURCE_FIGURE_FORMAT,
    LessonContentOutput,
    LessonContentVisualAsset,
)
from app.services.lesson_figure_selection import (
    FigureCandidate,
    FigureCatalog,
    _catalog_line,
    figure_ref,
)
from app.services.source_caption import clean_caption

MAX_ALTERNATIVES = 2
MAX_RESIDUAL = 2
MAX_CHARS = 8000
_SUBJECT_CAP = 240
_TAG_RE = re.compile(r"\[FIG:\s*([^\]\s]+)\s*\]")


@dataclass
class PlanCatalog(FigureCatalog):
    """Catalogo del piano: stessa interfaccia del catalogo lessicale (id
    `SRC-…`, righe, testo) più i legami con i fabbisogni."""

    need_by_ref: dict[str, str] = field(default_factory=dict)
    role_by_ref: dict[str, str] = field(default_factory=dict)
    needs: list[dict[str, Any]] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def figure_need(self) -> dict[uuid.UUID, str]:
        refs = self.refs
        return {refs[ref]: need for ref, need in self.need_by_ref.items() if ref in refs}


def _subject(need: Mapping[str, Any]) -> str:
    return " ".join(
        neutralize_third_party_text(str(need.get("subject") or ""), _SUBJECT_CAP).split()
    )


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def build_plan_catalog(
    needs: Sequence[Mapping[str, Any]],
    offer: Mapping[str, Any],
    figures: Mapping[uuid.UUID, Any],
    residual: Sequence[Any],
    outline: Sequence[Mapping[str, Any]],
    *,
    max_chars: int = MAX_CHARS,
) -> PlanCatalog | None:
    """Catalogo del piano; None se nessun fabbisogno ha una figura offerta."""
    offers = offer.get("offers") or {}
    alternatives = offer.get("alternatives") or {}
    titles = {
        str(s.get("section_id")): str(s.get("title") or "")
        for s in outline
        if isinstance(s, Mapping)
    }
    order = {sid: i for i, sid in enumerate(titles)}
    covered = [
        dict(n)
        for n in needs
        if isinstance(offers.get(str(n.get("need_id"))), Mapping)
        and _uuid(offers[str(n.get("need_id"))].get("figure_id")) in figures
    ]
    if not covered:
        return None
    covered.sort(
        key=lambda n: (order.get(str(n.get("section_id")), 99), needs.index(n) if n in needs else 0)
    )
    labels = {str(n["need_id"]): f"N{i}" for i, n in enumerate(covered, start=1)}
    group_sizes: dict[str, int] = {}
    for n in covered:
        group = str(n.get("sequence_group") or "")
        if group:
            group_sizes[group] = group_sizes.get(group, 0) + 1

    listed: set[uuid.UUID] = set()
    used_refs: set[str] = set()
    # Le figure assegnate si riservano prima: un'alternativa di un fabbisogno
    # non si prende la figura assegnata a un altro.
    assigned_ids = {
        fid
        for n in covered
        if (fid := _uuid(offers[str(n["need_id"])].get("figure_id"))) is not None
    }

    def ref_for(fid: uuid.UUID) -> str:
        length = 8
        ref = figure_ref(fid)
        while ref in used_refs and length < 32:
            length += 4
            ref = figure_ref(fid, length)
        used_refs.add(ref)
        return ref

    # (priorità di taglio, sezione, riga, ref, figura, fabbisogno, ruolo)
    entries: list[dict[str, Any]] = []
    for need in covered:
        need_id = str(need["need_id"])
        must = need.get("priority") == "must"
        assigned = _uuid(offers[need_id].get("figure_id"))
        options = [assigned] + [
            _uuid(a.get("figure_id"))
            for a in alternatives.get(need_id) or []
            if isinstance(a, Mapping)
        ]
        header_added = False
        for position, fid in enumerate(o for o in options if o is not None):
            if fid in listed or fid not in figures or position > MAX_ALTERNATIVES:
                continue
            if position > 0 and fid in assigned_ids:
                continue
            listed.add(fid)
            ref = ref_for(fid)
            role = "assigned" if position == 0 else "alternative"
            drop_rank = (
                4 if role == "assigned" and must else 3 if role == "assigned" else 2 if must else 1
            )
            entries.append(
                {
                    "section": str(need.get("section_id")),
                    "need": need,
                    "need_id": need_id,
                    "header": not header_added,
                    "ref": ref,
                    "fid": fid,
                    "role": role,
                    "rank": drop_rank,
                    "line": _catalog_line(ref, figures[fid]),
                }
            )
            header_added = True
    for fig in residual:
        if len([e for e in entries if e["role"] == "residual"]) >= MAX_RESIDUAL:
            break
        if fig.id in listed:
            continue
        listed.add(fig.id)
        ref = ref_for(fig.id)
        entries.append(
            {
                "section": "",
                "need": None,
                "need_id": "",
                "header": False,
                "ref": ref,
                "fid": fig.id,
                "role": "residual",
                "rank": 0,
                "line": _catalog_line(ref, fig),
            }
        )

    def render(kept: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        current_section: str | None = None
        seen_needs: set[str] = set()
        for entry in [e for e in kept if e["role"] != "residual"]:
            if entry["section"] != current_section:
                current_section = entry["section"]
                title = titles.get(current_section, "")
                lines.append(f"### Sezione {current_section}" + (f" — {title}" if title else ""))
            if entry["need_id"] not in seen_needs:
                seen_needs.add(entry["need_id"])
                need = entry["need"]
                group = str(need.get("sequence_group") or "")
                kind = "obbligatoria" if need.get("priority") == "must" else "facoltativa"
                sequence = (
                    f"; sequenza {group}, {need.get('sequence_index')} di {group_sizes[group]}"
                    if group and group_sizes.get(group, 0) > 1
                    else ""
                )
                lines.append(f"- {labels[entry['need_id']]} ({kind}{sequence}): {_subject(need)}")
            tag = "assegnata" if entry["role"] == "assigned" else "alternativa"
            lines.append(f"  {entry['line']} [{tag}]")
        residual_entries = [e for e in kept if e["role"] == "residual"]
        if residual_entries:
            lines.append("### Figure facoltative (pertinenti alla lezione)")
            lines += [e["line"] for e in residual_entries]
        return "\n".join(lines)

    kept = list(entries)
    dropped: list[str] = []
    while len(render(kept)) > max_chars:
        # Taglio: residuo, alternative degli should, alternative dei must,
        # should interi; mai la figura assegnata a un must.
        removable = [e for e in kept if e["rank"] < 4]
        if not removable:
            break
        victim = min(removable, key=lambda e: (e["rank"], -kept.index(e)))
        if victim["role"] == "assigned":
            same = [e for e in kept if e["need_id"] == victim["need_id"]]
            for e in same:
                kept.remove(e)
                dropped.append(e["ref"])
        else:
            kept.remove(victim)
            dropped.append(victim["ref"])
    text = render(kept)
    candidates = [
        FigureCandidate(
            figure_id=e["fid"],
            ref=e["ref"],
            score=0.0,
            strong_terms=0,
            relevant=e["role"] != "residual",
            line=e["line"],
        )
        for e in kept
    ]
    return PlanCatalog(
        candidates=candidates,
        text=text,
        stats={
            "plan": True,
            "needs": len({e["need_id"] for e in kept if e["need_id"]}),
            "candidates": len(candidates),
            "residual": sum(1 for e in kept if e["role"] == "residual"),
            "truncated": dropped,
            "chars": len(text),
        },
        need_by_ref={e["ref"]: e["need_id"] for e in kept if e["need_id"]},
        role_by_ref={e["ref"]: e["role"] for e in kept},
        needs=[n for n in covered if any(e["need_id"] == n["need_id"] for e in kept)],
        labels={k: v for k, v in labels.items() if any(e["need_id"] == k for e in kept)},
    )


# --- collocazione -------------------------------------------------------------------


def _cited_sections(output: LessonContentOutput) -> dict[str, tuple[str, int]]:
    """asset_id (minuscolo) → (sezione, posizione globale) della prima citazione;
    sezione "" per introduzione e sintesi."""
    out: dict[str, tuple[str, int]] = {}
    position = 0
    fields: list[tuple[str, str]] = [("", output.introduction or "")]
    fields += [(s.section_id, s.content or "") for s in output.sections]
    fields.append(("", output.summary or ""))
    for section_id, text in fields:
        for match in _TAG_RE.finditer(text):
            out.setdefault(match.group(1).lower(), (section_id, position + match.start()))
        position += len(text) + 1
    return out


def apply_placement(
    output: LessonContentOutput, plan: PlanCatalog, *, max_items: int
) -> dict[str, Any]:
    """Esito della collocazione per fabbisogno; modifica `output` solo per
    togliere il secondo figura dello stesso fabbisogno e per inserire i must
    mancanti con ancora sicura."""
    from app.services.source_figure_fusion import remove_source_figures

    need_of = {ref.lower(): need for ref, need in plan.need_by_ref.items()}
    cited = _cited_sections(output)
    placed_assets = [a for a in output.visual_assets if a.format == SOURCE_FIGURE_FORMAT]
    by_need: dict[str, list[tuple[int, str, str]]] = {}
    for asset in placed_assets:
        key = asset.asset_id.lower()
        need_id = need_of.get(key)
        if need_id and key in cited:
            section, pos = cited[key]
            by_need.setdefault(need_id, []).append((pos, asset.asset_id, section))
    # Una figura sola per fabbisogno: resta la prima citata.
    extra = {aid for items in by_need.values() for _pos, aid, _s in sorted(items)[1:]}
    if extra:
        remove_source_figures(output, extra)
    status: dict[str, dict[str, Any]] = {}
    for need in plan.needs:
        need_id = str(need["need_id"])
        cited_items = sorted(by_need.get(need_id, []))
        if cited_items:
            _pos, aid, where = cited_items[0]
            state = "placed" if where == str(need.get("section_id")) else "misplaced"
            status[need_id] = {"status": state, "asset_id": aid, "section": where}
        else:
            status[need_id] = {"status": "missing"}
    # Must mancanti: inserimento in fondo alla loro sezione se c'è e se c'è posto.
    used = len([a for a in output.visual_assets if a.format == SOURCE_FIGURE_FORMAT])
    sections = {s.section_id: s for s in output.sections}
    refs = plan.refs
    for need in plan.needs:
        need_id = str(need["need_id"])
        if need.get("priority") != "must" or status[need_id]["status"] != "missing":
            continue
        target = sections.get(str(need.get("section_id")))
        assigned = next(
            (
                ref
                for ref, nid in plan.need_by_ref.items()
                if nid == need_id and plan.role_by_ref.get(ref) == "assigned"
            ),
            None,
        )
        if target is None or assigned is None or used >= max_items:
            status[need_id]["reason"] = (
                "no_anchor" if target is None else "budget" if used >= max_items else "no_figure"
            )
            continue
        caption, _trimmed = clean_caption(_subject(need))
        output.visual_assets.append(
            LessonContentVisualAsset(
                asset_id=assigned,
                format=SOURCE_FIGURE_FORMAT,
                content=str(refs[assigned]),
                caption=caption[:600],
                alt_text=caption[:400],
            )
        )
        target.content = (target.content or "").rstrip() + f"\n\n[FIG:{assigned}]"
        used += 1
        status[need_id] = {
            "status": "auto_placed",
            "asset_id": assigned,
            "section": target.section_id,
        }
    # Ordine delle sequenze (solo avviso).
    cited = _cited_sections(output)
    warnings: list[str] = []
    groups: dict[str, list[tuple[int, int]]] = {}
    for need in plan.needs:
        info = status[str(need["need_id"])]
        if info.get("asset_id") and need.get("sequence_group"):
            pos = cited.get(str(info["asset_id"]).lower(), ("", 0))[1]
            groups.setdefault(str(need["sequence_group"]), []).append(
                (int(need.get("sequence_index") or 0), pos)
            )
    for group, members in groups.items():
        positions = [pos for _idx, pos in sorted(members)]
        if positions != sorted(positions):
            warnings.append(group)
    return {
        "needs": status,
        "order_warning": warnings,
        "dropped_same_need": sorted(extra),
        "counts": {
            s: sum(1 for v in status.values() if v["status"] == s)
            for s in ("placed", "misplaced", "auto_placed", "missing")
        },
    }
