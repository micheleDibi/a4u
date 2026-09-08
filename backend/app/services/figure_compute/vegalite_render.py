"""Bersaglio del processo figlio per il render Vega-Lite (vl-convert).

`vl_convert` esegue Vega in un runtime Deno in-process, non
interrompibile: il render gira quindi in `isolated.run_isolated`, che
importa questo modulo dal disco. Solo libreria standard a livello di
modulo; `vl_convert` è importato dentro la funzione.

Il modulo NON impone `$schema` né inietta il tema: lo fa il registro
(`figure_render_service.VegaLiteRenderer`), che passa nel payload la spec
già completata e `config=VEGALITE_THEME_CONFIG`.

`allowed_base_urls=[]` è una difesa in profondità, non il gate: vl-convert
rifiuta con «External data url not allowed» solo gli URL http(s) (anche
quelli relativi, risolti sul base URL dei vega-datasets), mentre un
`file://…` non viene letto ma non solleva (join vuoto, verificato il 7
settembre: nessun byte del file finisce nell'SVG). L'assenza di `data.url`
in ogni punto della spec, `transform[].lookup.from.data` compreso, è
garantita prima del render da `vegalite_rules.check_vegalite_rules` (D5).
"""

from __future__ import annotations

import json
from typing import Any


def render_svg_batch(payload: dict[str, Any]) -> list[str | None]:
    """`payload = {"specs": [dict, ...], "config": dict}` → lista parallela
    di SVG; `None` per la spec che fallisce, senza motivo (un fallimento
    non ferma il batch: le altre figure della lezione vengono comunque
    rese; il motivo si ottiene con `render_svg` sulla singola spec, che
    è il percorso della validazione profonda)."""
    import vl_convert

    config = payload.get("config") or None
    out: list[str | None] = []
    for spec in payload.get("specs") or []:
        try:
            svg = vl_convert.vegalite_to_svg(
                json.dumps(spec, ensure_ascii=False),
                config=config,
                allowed_base_urls=[],
            )
            out.append(svg if isinstance(svg, str) and svg.strip() else None)
        except Exception:  # un fallimento non ferma il batch
            out.append(None)
    return out


def render_svg(payload: dict[str, Any]) -> str:
    """`payload = {"spec": dict, "config": dict}` → SVG. Solleva con il
    messaggio di vl-convert (usato dalla validazione profonda, che deve
    riportare il motivo del fallimento)."""
    import vl_convert

    svg = vl_convert.vegalite_to_svg(
        json.dumps(payload["spec"], ensure_ascii=False),
        config=payload.get("config") or None,
        allowed_base_urls=[],
    )
    if not isinstance(svg, str) or not svg.strip():
        raise ValueError("vl-convert ha restituito un SVG vuoto")
    return svg


__all__ = ["render_svg", "render_svg_batch"]
