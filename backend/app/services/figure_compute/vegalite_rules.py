"""Regole D5 sulle spec Vega-Lite ed euristica del criterio 10 (puro).

Chiamato dopo la validazione contro lo schema JSON di Vega-Lite (che
garantisce la forma) per imporre i vincoli di onestà dei dati e di
renderizzabilità offline decisi dalla specifica:

- vietati `data.url`, `data.name` senza `datasets`, `mark: "image"`,
  `selection | params | tooltip | interactive | config | usermeta`,
  `encoding.href` (niente interattività, niente rete, niente tema scritto
  dal modello: il `config` lo inietta il renderer);
- `values` ≤ 200 righe (anche in `datasets`); `sequence` ≤ 5.000 passi con
  `step > 0`;
- obbligatorio `clip: true` sui mark `line | area | point | trail` e
  `scale.domain` `[n, n]` sui canali `x`/`y` quantitativi (la figura non
  sborda mai dal riquadro e il modello dichiara l'intervallo che disegna);
- `axis.format` ⊆ `^[ ,.0-9a-z%$~+-]{0,12}$`; una sola `title` (stringa,
  ≤ 120 caratteri, solo al livello radice);
- ricorsione ≤ 4 livelli su `layer | hconcat | vconcat | concat | spec |
  facet | repeat`.

Euristica del criterio 10 (`vegalite_use_function_format`)
--------------------------------------------------------------
Le figure matematiche sono VIETATE in Vega-Lite (esiste `function`). Non
si può riconoscere «una funzione» in generale, ma il modo in cui Vega-Lite
la traccia è uno solo: `data.sequence` + `transform[].calculate` sul campo
della sequenza. Con `data.sequence` presente, ogni espressione `calculate`
che referenzia `datum.<campo>` e contiene
- (H1) una funzione trascendente o non lineare (`sin cos tan asin acos
  atan sinh cosh tanh exp log sqrt pow abs`), oppure
- (H2) una divisione con `datum` a denominatore (funzione razionale),
  oppure
- (H3) una potenza (`**` o `^`)
viene rifiutata con «usa format="function"». Rette e polinomi scritti con
`*` restano ammessi (es. una retta di regressione illustrativa): sono il
falso negativo accettato dall'euristica, documentata in
`docs/courses/17-visual-figures.md`.

I campi `sequence` sono EREDITATI dalle viste figlie (`layer`, `hconcat`,
`vconcat`, `concat`, `spec`): in Vega-Lite i dati della vista padre valgono
per i figli, quindi `data.sequence` alla radice e `calculate` dentro un
`layer` sono la stessa figura. `_walk` propaga l'insieme dei campi ai figli
e applica H1-H3 a ogni `transform.calculate` della sottovista.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, TypeGuard

MAX_VALUES_ROWS = 200
MAX_SEQUENCE_STEPS = 5_000
MAX_TITLE_CHARS = 120
MAX_DEPTH = 4

USE_FUNCTION_FORMAT = "vegalite_use_function_format"

_CLIP_MARKS = frozenset({"line", "area", "point", "trail"})
_FORBIDDEN_VIEW_KEYS = ("selection", "params", "interactive", "config", "usermeta", "tooltip")
_COMPOSITION_KEYS = ("layer", "hconcat", "vconcat", "concat")
_NESTED_SPEC_KEYS = ("spec",)
_AXIS_FORMAT_RE = re.compile(r"^[ ,.0-9a-z%$~+-]{0,12}$")

_H1_FUNCTIONS_RE = re.compile(
    r"\b(?:sin|cos|tan|asin|acos|atan|sinh|cosh|tanh|exp|log|sqrt|pow|abs)\s*\("
)
# Divisione con `datum` a denominatore: `/ datum.x`, `/ (datum.x - 2)`,
# `/(2*datum.x + 1)`.
_H2_DIVISION_RE = re.compile(r"/\s*(?:\(\s*)*(?:[-+]?\s*[0-9.]+\s*[*+-]\s*)*datum\b")
_H3_POWER_RE = re.compile(r"\*\*|\^")


def _is_mapping(value: Any) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping)


def _is_number(value: Any) -> TypeGuard[float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _rows(values: Any) -> int | None:
    if isinstance(values, list):
        return len(values)
    if isinstance(values, str):
        # `values` come stringa CSV/TSV: contiamo le righe.
        return values.count("\n") + 1
    return None


def _check_data(data: Any, *, has_datasets: bool, where: str, errors: list[str]) -> list[str]:
    """Regole su un blocco `data`. Ritorna i campi `as` delle sequenze
    trovate (per l'euristica del criterio 10)."""
    sequence_fields: list[str] = []
    if not _is_mapping(data):
        return sequence_fields
    if "url" in data:
        errors.append(f"{where}data.url non ammesso: i dati vanno dichiarati inline")
    if "name" in data and not has_datasets:
        errors.append(f"{where}data.name senza `datasets` al livello radice")
    rows = _rows(data.get("values"))
    if rows is not None and rows > MAX_VALUES_ROWS:
        errors.append(f"{where}data.values oltre {MAX_VALUES_ROWS} righe ({rows})")
    seq = data.get("sequence")
    if _is_mapping(seq):
        start, stop, step = seq.get("start"), seq.get("stop"), seq.get("step", 1)
        if not (_is_number(start) and _is_number(stop) and _is_number(step)):
            errors.append(f"{where}data.sequence richiede start/stop/step numerici")
        elif step <= 0:
            errors.append(f"{where}data.sequence.step deve essere > 0")
        elif (stop - start) / step > MAX_SEQUENCE_STEPS:
            errors.append(f"{where}data.sequence oltre {MAX_SEQUENCE_STEPS} passi")
        field = seq.get("as", "data")
        sequence_fields.append(str(field) if isinstance(field, str) else "data")
    return sequence_fields


def _check_mark(mark: Any, *, where: str, errors: list[str]) -> None:
    if isinstance(mark, str):
        if mark == "image":
            errors.append(f"{where}mark image non ammesso")
        elif mark in _CLIP_MARKS:
            errors.append(
                f'{where}mark {mark} richiede clip:true (usa {{"type": "{mark}", "clip": true}})'
            )
        return
    if not _is_mapping(mark):
        return
    mtype = mark.get("type")
    if mtype == "image":
        errors.append(f"{where}mark image non ammesso")
    elif mtype in _CLIP_MARKS and mark.get("clip") is not True:
        errors.append(f"{where}mark {mtype} richiede clip:true")
    if "tooltip" in mark:
        errors.append(f"{where}mark.tooltip non ammesso")


def _check_encoding(encoding: Any, *, where: str, errors: list[str]) -> None:
    if not _is_mapping(encoding):
        return
    if "href" in encoding:
        errors.append(f"{where}encoding.href non ammesso")
    if "tooltip" in encoding:
        errors.append(f"{where}encoding.tooltip non ammesso")
    for channel in ("x", "y"):
        ch = encoding.get(channel)
        if not _is_mapping(ch):
            continue
        if ch.get("type") == "quantitative":
            scale = ch.get("scale")
            domain = scale.get("domain") if _is_mapping(scale) else None
            ok = (
                isinstance(domain, list)
                and len(domain) == 2
                and all(_is_number(v) for v in domain)
                and domain[0] < domain[1]
            )
            if not ok:
                errors.append(
                    f"{where}encoding.{channel} quantitativo richiede scale.domain [min, max]"
                )
        axis = ch.get("axis")
        if _is_mapping(axis):
            fmt = axis.get("format")
            if fmt is not None and (not isinstance(fmt, str) or not _AXIS_FORMAT_RE.match(fmt)):
                errors.append(f"{where}encoding.{channel}.axis.format non ammesso ({fmt!r})")


def _check_transforms(
    transforms: Any, *, sequence_fields: frozenset[str], where: str, errors: list[str]
) -> None:
    """Euristica del criterio 10 su ogni `calculate` della vista, con i
    campi `sequence` della vista stessa e quelli ereditati dai padri."""
    if not isinstance(transforms, list) or not sequence_fields:
        return
    for i, tr in enumerate(transforms):
        if not _is_mapping(tr):
            continue
        expr = tr.get("calculate")
        if not isinstance(expr, str):
            continue
        refs_sequence = any(re.search(rf"\bdatum\.{re.escape(f)}\b", expr) for f in sequence_fields)
        if not refs_sequence and "datum[" in expr:
            refs_sequence = any(f in expr for f in sequence_fields)
        if not refs_sequence:
            continue
        reason = None
        if _H1_FUNCTIONS_RE.search(expr):
            reason = "funzione trascendente o non lineare"
        elif _H2_DIVISION_RE.search(expr):
            reason = "funzione razionale (datum a denominatore)"
        elif _H3_POWER_RE.search(expr):
            reason = "potenza"
        if reason:
            errors.append(
                f"{USE_FUNCTION_FORMAT}: {where}transform[{i}].calculate traccia una "
                f'{reason}: usa format="function"'
            )


def _walk(
    view: Any,
    *,
    depth: int,
    root: bool,
    has_datasets: bool,
    inherited: frozenset[str],
    errors: list[str],
) -> None:
    """Visita ricorsiva di una vista. `inherited` è l'insieme dei campi
    `sequence` dichiarati dalle viste padre: in Vega-Lite i dati del padre
    valgono per i figli, quindi l'euristica del criterio 10 deve vedere
    `data.sequence` della radice anche dentro `layer`, `vconcat`, `concat`
    e `spec`."""
    if not _is_mapping(view):
        return
    where = "" if root else f"(profondità {depth}) "
    if depth > MAX_DEPTH:
        errors.append(f"composizione oltre {MAX_DEPTH} livelli")
        return
    for key in _FORBIDDEN_VIEW_KEYS:
        if key in view:
            errors.append(f"{where}{key} non ammesso")
    title = view.get("title")
    if title is not None:
        if not root:
            errors.append(f"{where}title ammesso solo al livello radice")
        elif not isinstance(title, str):
            errors.append("title deve essere una stringa")
        elif len(title) > MAX_TITLE_CHARS:
            errors.append(f"title oltre {MAX_TITLE_CHARS} caratteri")
    own_fields = _check_data(
        view.get("data"), has_datasets=has_datasets, where=where, errors=errors
    )
    sequence_fields = inherited | frozenset(own_fields)
    _check_mark(view.get("mark"), where=where, errors=errors)
    _check_encoding(view.get("encoding"), where=where, errors=errors)
    _check_transforms(
        view.get("transform"), sequence_fields=sequence_fields, where=where, errors=errors
    )
    for key in _COMPOSITION_KEYS:
        items = view.get(key)
        if isinstance(items, list):
            for item in items:
                _walk(
                    item,
                    depth=depth + 1,
                    root=False,
                    has_datasets=has_datasets,
                    inherited=sequence_fields,
                    errors=errors,
                )
    for key in _NESTED_SPEC_KEYS:
        if key in view:
            _walk(
                view[key],
                depth=depth + 1,
                root=False,
                has_datasets=has_datasets,
                inherited=sequence_fields,
                errors=errors,
            )


def check_vegalite_rules(spec: Mapping[str, Any]) -> list[str]:
    """Lista di violazioni (vuota = conforme). L'eventuale rifiuto del
    criterio 10 è messo in testa con il prefisso
    `vegalite_use_function_format:` così il chiamante lo classifica."""
    errors: list[str] = []
    datasets = spec.get("datasets")
    has_datasets = _is_mapping(datasets) and bool(datasets)
    if _is_mapping(datasets):
        for name, values in datasets.items():
            rows = _rows(values)
            if rows is not None and rows > MAX_VALUES_ROWS:
                errors.append(f"datasets.{name} oltre {MAX_VALUES_ROWS} righe ({rows})")
    _walk(
        spec,
        depth=0,
        root=True,
        has_datasets=has_datasets,
        inherited=frozenset(),
        errors=errors,
    )
    errors.sort(key=lambda e: 0 if e.startswith(USE_FUNCTION_FORMAT) else 1)
    return errors


__all__ = ["USE_FUNCTION_FORMAT", "check_vegalite_rules"]
