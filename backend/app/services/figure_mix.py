"""Mix dei formati e dei tipi di figura di una lezione — MISURA, non gate.

Modulo puro (libreria standard + `figure_compute.graph_rules`): nessun
accesso al DB, nessuna rete, nessuna dipendenza dall'ambiente del
backend. Lo usano la materializzazione della Fase 3 (log strutturato
`lesson_content_figure_mix`) e `scripts/measure_asset_refs.py`, così la
misura in produzione e quella sull'export del docente sono la stessa.

Perché esiste: sull'export reale di quattro lezioni di quattro corsi
diversi (18 settembre 2026) le figure GENERATE dal modello erano 14, di
cui 13 `mermaid` flowchart e una `function`; zero `vegalite`, zero `dot`,
e dentro Mermaid zero sequence, state, class, er, mindmap, timeline. Il
problema non era la singola figura ma la monocultura. La REGOLA DI SCELTA
del prompt di Fase 3 è la correzione; questo modulo è il modo di sapere
se ha funzionato, senza rileggere le lezioni a mano.

Non blocca nulla: una lezione con tre flowchart resta valida e viene
materializzata: il warning serve al docente e ai log, non al gate.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.services.figure_compute.graph_rules import mermaid_source_metrics

# Sotto questo numero di figure il mix non dice nulla: una lezione con due
# flowchart non è una monocultura, è una lezione corta.
MONOCULTURE_MIN_FIGURES = 3

# Tipo attribuito a un sorgente Mermaid il cui primo token non è un tipo
# noto: è quello che `graph_rules._metrics` già usa, non un valore nuovo.
UNKNOWN_MERMAID_TYPE = "?"

# Figure non generate dal modello (figure di fonte dei documenti): contate a
# parte in `sources`, fuori da `total` e `formats`, così il mix misura solo
# le figure generate (budget (a)) e la monocultura non cambia significato.
SOURCE_FORMATS: frozenset[str] = frozenset({"source_figure"})


def _mermaid_type(source: str) -> str:
    return mermaid_source_metrics(source).kind or UNKNOWN_MERMAID_TYPE


# Formati che portano un TIPO dentro il formato, e come leggerlo dal
# sorgente. Solo `mermaid`: DOT, Vega-Lite e `function` hanno una nozione
# di tipo, ma non la dichiarano in un token iniziale confrontabile (il
# `kind` di `FunctionFigureSpec` sta nel JSON, il mark Vega-Lite pure), e
# la monocultura misurata era dentro Mermaid. Tabella e non `if` sul
# formato: D2 (§9 di doc 17) vieta il dispatch per confronto letterale.
_SUBTYPE_READERS: dict[str, Callable[[str], str]] = {"mermaid": _mermaid_type}


@dataclass(frozen=True)
class FigureMix:
    """Conteggio per formato e, dentro `mermaid`, per tipo di diagramma.

    `formats` e `mermaid_types` sono ordinati per conteggio decrescente e,
    a parità, per nome: il log di due lezioni con lo stesso mix è la stessa
    riga, e un diff fra due misure si legge.
    """

    total: int
    formats: dict[str, int]
    mermaid_types: dict[str, int]
    # Figure di fonte (budget (b)), per formato; non entrano in `total`.
    sources: dict[str, int] | None = None

    @property
    def monoculture(self) -> bool:
        """Vero quando una lezione con almeno `MONOCULTURE_MIN_FIGURES`
        figure usa UN SOLO formato e, se quel formato è `mermaid`, un solo
        tipo di diagramma. È la forma misurata in produzione: quattro
        flowchart e nient'altro."""
        if self.total < MONOCULTURE_MIN_FIGURES or len(self.formats) != 1:
            return False
        return len(self.mermaid_types) <= 1

    @property
    def single_format(self) -> str | None:
        """Il formato unico di una monocultura, `None` se ce n'è più d'uno."""
        return next(iter(self.formats)) if len(self.formats) == 1 else None

    @property
    def single_mermaid_type(self) -> str | None:
        """Il tipo Mermaid unico di una lezione fatta di soli Mermaid:
        `None` quando i tipi sono più d'uno o nessuno, e anche quando
        accanto ai Mermaid c'è un altro formato. Il vincolo sul formato
        non è pignoleria: senza, una lezione con quattro formati diversi
        e un solo flowchart risponderebbe «flowchart» a chi chiede il
        tipo unico, mentre `single_format` risponde `None` e
        `monoculture` è falsa. Sotto `monoculture` il valore non cambia:
        lì il formato unico c'è per definizione. La condizione sul formato
        passa dalla tabella `_SUBTYPE_READERS` e non da un confronto
        letterale con `mermaid` (D2, §9 di doc 17)."""
        if self.single_format not in _SUBTYPE_READERS or len(self.mermaid_types) != 1:
            return None
        return next(iter(self.mermaid_types))


def _sorted(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def compute_figure_mix(assets: Iterable[Mapping[str, Any] | Any]) -> FigureMix:
    """Mix di una lista di asset visivi.

    Accetta sia i modelli Pydantic della Fase 3 (`format` e `content` come
    attributi) sia i dizionari grezzi di `content_raw` esportato dal DB: la
    materializzazione ha i primi, lo script i secondi.

    Un asset senza `format` non è contato (non esiste: lo schema strict lo
    impone), un `content` vuoto conta come Mermaid di tipo ignoto.
    """
    formats: Counter[str] = Counter()
    mermaid_types: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    for asset in assets:
        if isinstance(asset, Mapping):
            fmt = asset.get("format")
            content = asset.get("content")
        else:
            fmt = getattr(asset, "format", None)
            content = getattr(asset, "content", None)
        if not isinstance(fmt, str) or not fmt:
            continue
        if fmt in SOURCE_FORMATS:
            sources[fmt] += 1
            continue
        formats[fmt] += 1
        read_subtype = _SUBTYPE_READERS.get(fmt)
        if read_subtype is not None:
            mermaid_types[read_subtype(content if isinstance(content, str) else "")] += 1
    return FigureMix(
        total=sum(formats.values()),
        formats=_sorted(formats),
        mermaid_types=_sorted(mermaid_types),
        sources=_sorted(sources),
    )
