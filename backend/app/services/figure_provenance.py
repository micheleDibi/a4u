"""Figure di fonte e `tikz` nei JSON che vanno ai prompt di Fase 4 e 5.

`content_raw.visual_assets` contiene gli asset `source_figure` con
`content` = UUID della riga `course_document_figure`: un identificativo
interno che non serve al modello e che non deve tornare indietro in un
output. `prompt_view` ne dà una copia con il contenuto sostituito da una
nota fissa; la fonte NON c'è (la scrive il sistema sulla slide e nel
discorso la dicono i dati del blocco dedicato).

Gli asset `tikz` (WP6) portano il sorgente TeX: al modello delle slide e
del discorso bastano le etichette dei nodi (`tikz_translate.extract`), e
così nessuna barra rovesciata del sorgente arriva al testo parlato
(`(schema TikZ; etichette: …)`). Senza figure di fonte né `tikz` la vista
è il dict originale: il prompt resta byte-identico a prima.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from app.core.prompt_safety import _THIRD_PARTY_MARK as THIRD_PARTY_MARK
from app.core.prompt_safety import neutralize_third_party_text
from app.schemas.course_lesson_content import SOURCE_FIGURE_FORMAT

PROMPT_PLACEHOLDER = "(figura tratta dai documenti del corso; la fonte la aggiunge il sistema)"
TIKZ_FORMAT = "tikz"
_TIKZ_LABELS_CAP = 400


def source_figure_ids_in(content_raw: Any) -> list[str]:
    """`asset_id` delle figure di fonte della dispensa, in ordine."""
    if not isinstance(content_raw, dict):
        return []
    return [
        str(a.get("asset_id") or "")
        for a in content_raw.get("visual_assets") or []
        if isinstance(a, dict) and a.get("format") == SOURCE_FIGURE_FORMAT
    ]


def tikz_prompt_text(source: str) -> str:
    """Il contenuto di un asset `tikz` come lo vedono i PROMPT 5 e 6."""
    from app.services.figure_compute.tikz_translate import extract

    labels = "; ".join(extract(source or "").values())[:_TIKZ_LABELS_CAP]
    return f"(schema TikZ; etichette: {labels})" if labels else "(schema TikZ)"


def _has_tikz(content_raw: Any) -> bool:
    return isinstance(content_raw, dict) and any(
        isinstance(a, dict) and a.get("format") == TIKZ_FORMAT
        for a in content_raw.get("visual_assets") or []
    )


def prompt_view(content_raw: Any) -> Any:
    if not source_figure_ids_in(content_raw) and not _has_tikz(content_raw):
        return content_raw
    view = copy.deepcopy(content_raw)
    for asset in view.get("visual_assets") or []:
        if not isinstance(asset, dict):
            continue
        if asset.get("format") == SOURCE_FIGURE_FORMAT:
            asset["content"] = PROMPT_PLACEHOLDER
        elif asset.get("format") == TIKZ_FORMAT:
            asset["content"] = tikz_prompt_text(str(asset.get("content") or ""))
    return view


def slides_with_source_figures(
    slides_raw: Any, source_asset_ids: list[str]
) -> dict[str, list[str]]:
    """{slide_id → asset_id delle figure di fonte che la slide mostra}."""
    wanted = {aid.strip().lower(): aid for aid in source_asset_ids}
    out: dict[str, list[str]] = {}
    if not isinstance(slides_raw, dict):
        return out
    for slide in slides_raw.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        hits = [
            wanted[ref.strip().lower()]
            for ref in slide.get("references_assets") or []
            if isinstance(ref, str) and ref.strip().lower() in wanted
        ]
        if hits:
            out[str(slide.get("slide_id") or "")] = hits
    return out


def safe_spoken_text(text: str) -> str:
    """Frase parlata pronta per il prompt: nomi e titoli vengono da terzi
    (metadati del PDF, Crossref, OpenAlex), quindi passano da
    `prompt_safety.neutralize_third_party_text`; una frase con un tentativo
    di istruzione si scarta del tutto (il discorso omette la fonte, la
    riga scritta resta), invece di chiedere al modello di leggere
    «[testo rimosso]»."""
    neutral = neutralize_third_party_text(text, max_length=400)
    if not neutral or THIRD_PARTY_MARK in neutral:
        return ""
    return " ".join(neutral.split())


def spoken_sources_block(slides_raw: Any, spoken_by_asset: Mapping[str, str]) -> list[str]:
    """Righe del blocco «Fonti delle figure da citare a voce» del PROMPT 6
    (vuoto senza figure di fonte sulle slide o senza frasi pronunciabili).
    Le frasi vengono da `figure_attribution` (variante parlata): mai dal
    modello, mai con pagine, numeri di figura o licenze; neutralizzate
    (`safe_spoken_text`) perché nomi e titoli sono testo di terzi."""
    safe = {aid: safe_spoken_text(text) for aid, text in spoken_by_asset.items()}
    lines: list[str] = []
    for slide_id, asset_ids in slides_with_source_figures(
        slides_raw, [aid for aid, text in safe.items() if text]
    ).items():
        for asset_id in asset_ids:
            lines.append(f"- slide {slide_id}: {safe[asset_id]}")
    return lines


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def spoken_keys(spoken: Mapping[str, Any] | None) -> list[str]:
    """Frasi che, dette nel discorso, nominano la fonte: i cognomi, il
    titolo breve intero, il nome di ripiego (da `spoken_source`)."""
    if not spoken:
        return []
    keys = [str(a) for a in spoken.get("authors") or [] if a]
    for field in ("title", "name"):
        if spoken.get(field):
            keys.append(str(spoken[field]))
    return [k for k in (_norm(k) for k in keys) if len(k) >= 3]


def unspoken_sources(
    slides_raw: Any, segments: list[tuple[str, str]], keys_by_asset: Mapping[str, list[str]]
) -> list[str]:
    """Slide con figure di fonte il cui parlato non nomina la fonte (nessun
    cognome né il titolo). Controllo SOFT: il chiamante logga, non blocca.
    `segments`: (slide_id, testo); `keys_by_asset`: da `spoken_keys`."""
    by_slide: dict[str, str] = {}
    for slide_id, text in segments:
        by_slide[slide_id] = f"{by_slide.get(slide_id, '')} {_norm(text)} "
    missing: list[str] = []
    for slide_id, asset_ids in slides_with_source_figures(
        slides_raw, [aid for aid, keys in keys_by_asset.items() if keys]
    ).items():
        spoken = f" {by_slide.get(slide_id, '')} "
        keys = [key for aid in asset_ids for key in keys_by_asset[aid]]
        if not any(f" {key} " in spoken for key in keys):
            missing.append(slide_id)
    return missing
