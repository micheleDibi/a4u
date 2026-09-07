"""`FunctionFigureSpec`: la spec JSON del formato `function` (D9, Q4).

`content` di un asset `format="function"` è la stringa JSON di questo
oggetto. La validazione ha due livelli:

1. struttura (Pydantic, `extra="forbid"` ovunque): tipi, lunghezze,
   intervalli numerici, discriminatore `kind` delle annotazioni;
2. semantica (`check_function_spec`): dominio e range finiti con
   ampiezza in `[1e-3, 1e4]`, coerenza fra `kind`, annotazioni, parametro
   e curve di livello, label univoche e ogni `expr` accettata dal passo 1
   di `figure_compute.function_parse.check_expression` con i soli simboli
   dichiarati.

Il secondo livello NON è un `model_validator`: un `ValueError` sollevato
dal modello collasserebbe ogni errore semantico in una sola voce con
`loc` radice, mentre il payload 422 dell'endpoint `render-function` e il
frontend richiedono la `loc` del campo (`["expressions", 0, "expr"]`).
`parse_function_spec(content)` esegue entrambi i livelli ed è il punto
di ingresso del renderer e del gate del PATCH; l'endpoint riceve il
modello già validato nella struttura da FastAPI e applica
`check_function_spec`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.services.figure_compute.function_parse import CONSTANTS, ExprError, check_expression

Var = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z]$")]
Interval = tuple[float, float]

FunctionKind = Literal["function_study", "tangent", "area", "family", "level_curves"]
ShowItem = Literal[
    "zeros", "critical_points", "inflection_points", "asymptotes", "discontinuities", "formula"
]

MAX_EXPRESSIONS = 4
MAX_ANNOTATIONS = 6
MAX_SHOW = 6
MAX_PARAMETER_VALUES = 6
MIN_WIDTH = 1e-3
MAX_WIDTH = 1e4
MIN_LEVELS = 2
MAX_LEVELS = 12
MIN_POINTS = 100
MAX_POINTS = 2000

# Analisi eseguite di default quando `show` è omesso: uno studio di
# funzione senza zeri, punti critici e asintoti sarebbe un grafico nudo.
DEFAULT_SHOW: tuple[ShowItem, ...] = ("zeros", "critical_points", "asymptotes", "formula")
ANALYSIS_ITEMS: frozenset[str] = frozenset(
    {"zeros", "critical_points", "inflection_points", "asymptotes", "discontinuities"}
)
# Etichette assegnate al render alle espressioni senza `label`.
DEFAULT_LABELS: tuple[str, ...] = ("f", "g", "h", "k")

SPEC_INVALID = "spec_invalid"


class SpecIssue(TypedDict):
    """Voce di `meta.errors` del 422: stessa forma `loc/msg/type` del
    handler Pydantic (`core/errors.py`)."""

    loc: list[str | int]
    msg: str
    type: str


class ExpressionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expr: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=24)


class TangentAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tangent"]
    at: float
    expr_index: int = Field(default=0, ge=0, le=MAX_EXPRESSIONS - 1)
    label: str = Field(default="", max_length=40)


class AreaAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["area"]
    between: Interval
    expr_index: int = Field(default=0, ge=0, le=MAX_EXPRESSIONS - 1)
    against: int | None = Field(default=None, ge=0, le=MAX_EXPRESSIONS - 1)
    label: str = Field(default="", max_length=40)


class PointAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["point"]
    at: float
    expr_index: int = Field(default=0, ge=0, le=MAX_EXPRESSIONS - 1)
    label: str = Field(default="", max_length=40)


Annotation = Annotated[
    TangentAnnotation | AreaAnnotation | PointAnnotation, Field(discriminator="kind")
]


class ParameterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Var
    values: list[float] = Field(min_length=1, max_length=MAX_PARAMETER_VALUES)


class SamplingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: int = Field(default=800, ge=MIN_POINTS, le=MAX_POINTS)


class FunctionFigureSpec(BaseModel):
    """Spec di una figura matematica calcolata (vedi la docstring del
    modulo per i due livelli di validazione)."""

    model_config = ConfigDict(extra="forbid")

    kind: FunctionKind
    expressions: list[ExpressionSpec] = Field(min_length=1, max_length=MAX_EXPRESSIONS)
    variable: Var = "x"
    variables: tuple[Var, Var] | None = None
    domain: Interval
    range: Interval | None = None
    show: list[ShowItem] = Field(default_factory=lambda: list(DEFAULT_SHOW), max_length=MAX_SHOW)
    annotations: list[Annotation] = Field(default_factory=list, max_length=MAX_ANNOTATIONS)
    parameter: ParameterSpec | None = None
    sampling: SamplingSpec = Field(default_factory=SamplingSpec)
    levels: int | list[float] | None = None

    def declared_symbols(self) -> tuple[str, ...]:
        """Simboli liberi ammessi nelle espressioni: la variabile (o le due
        variabili delle curve di livello) più l'eventuale parametro."""
        symbols: list[str] = list(self.variables) if self.variables else [self.variable]
        if self.parameter is not None:
            symbols.append(self.parameter.name)
        return tuple(dict.fromkeys(symbols))

    def expression_label(self, index: int) -> str:
        label = self.expressions[index].label.strip()
        if label:
            return label
        return DEFAULT_LABELS[index % len(DEFAULT_LABELS)]

    def canonical_json(self) -> str:
        """Serializzazione canonica (chiavi ordinate, default inclusi):
        due spec equivalenti producono la stessa stringa."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Livello 2: controlli semantici con `loc` per campo
# ---------------------------------------------------------------------------


def _issue(loc: Sequence[str | int], msg: str, type_: str = SPEC_INVALID) -> SpecIssue:
    return {"loc": list(loc), "msg": msg, "type": type_}


def _check_interval(
    value: Interval | None, loc: Sequence[str | int], *, required: bool, issues: list[SpecIssue]
) -> bool:
    """`True` se l'intervallo è utilizzabile (finito, `lo < hi`, ampiezza
    entro i limiti)."""
    if value is None:
        if required:
            issues.append(_issue(loc, "intervallo obbligatorio"))
        return False
    lo, hi = value
    if not (math.isfinite(lo) and math.isfinite(hi)):
        issues.append(_issue(loc, "estremi non finiti"))
        return False
    if not lo < hi:
        issues.append(_issue(loc, "il minimo deve essere minore del massimo"))
        return False
    width = hi - lo
    if width < MIN_WIDTH or width > MAX_WIDTH:
        issues.append(_issue(loc, f"ampiezza fuori da [{MIN_WIDTH:g}, {MAX_WIDTH:g}]"))
        return False
    return True


def _check_levels(spec: FunctionFigureSpec, issues: list[SpecIssue]) -> None:
    loc = ("levels",)
    if spec.kind != "level_curves":
        if spec.levels is not None:
            issues.append(_issue(loc, "`levels` è ammesso solo con kind=level_curves"))
        return
    if spec.levels is None:
        issues.append(_issue(loc, "kind=level_curves richiede `levels`"))
        return
    if isinstance(spec.levels, int):
        if not MIN_LEVELS <= spec.levels <= MAX_LEVELS:
            issues.append(_issue(loc, f"numero di livelli fuori da [{MIN_LEVELS}, {MAX_LEVELS}]"))
        return
    values = spec.levels
    if not MIN_LEVELS <= len(values) <= MAX_LEVELS:
        issues.append(_issue(loc, f"numero di livelli fuori da [{MIN_LEVELS}, {MAX_LEVELS}]"))
        return
    if any(not math.isfinite(v) for v in values):
        issues.append(_issue(loc, "livelli non finiti"))
        return
    if len(set(values)) != len(values):
        issues.append(_issue(loc, "livelli duplicati"))


def _check_annotations(
    spec: FunctionFigureSpec, *, domain_ok: bool, issues: list[SpecIssue]
) -> None:
    lo, hi = spec.domain
    n_expr = len(spec.expressions)
    labels: set[str] = set()
    for i, ann in enumerate(spec.annotations):
        loc: tuple[str | int, ...] = ("annotations", i)
        if ann.expr_index >= n_expr:
            issues.append(
                _issue((*loc, "expr_index"), f"indice fuori da expressions (0..{n_expr - 1})")
            )
        if isinstance(ann, AreaAnnotation):
            a, b = ann.between
            if not (math.isfinite(a) and math.isfinite(b)) or not a < b:
                issues.append(_issue((*loc, "between"), "intervallo non valido (a < b finiti)"))
            elif domain_ok and (a < lo or b > hi):
                issues.append(_issue((*loc, "between"), "intervallo fuori dal dominio"))
            if ann.against is not None:
                if ann.against >= n_expr:
                    issues.append(
                        _issue((*loc, "against"), f"indice fuori da expressions (0..{n_expr - 1})")
                    )
                elif ann.against == ann.expr_index:
                    issues.append(_issue((*loc, "against"), "deve differire da expr_index"))
        else:
            if not math.isfinite(ann.at):
                issues.append(_issue((*loc, "at"), "valore non finito"))
            elif domain_ok and not lo <= ann.at <= hi:
                issues.append(_issue((*loc, "at"), "punto fuori dal dominio"))
        label = ann.label.strip()
        if label:
            if label in labels:
                issues.append(_issue((*loc, "label"), f"label duplicata: {label}"))
            labels.add(label)


def _constant_issue(loc: Sequence[str | int], name: str) -> SpecIssue:
    what = "la costante di Nepero" if name == "E" else "una costante"
    return _issue(loc, f"{name} indica {what}: scegli un'altra lettera")


def _check_symbols(spec: FunctionFigureSpec, issues: list[SpecIssue]) -> None:
    """Variabili e parametro non possono chiamarsi come una costante
    (`E`): il valutatore numpy e il passo 1 la risolverebbero come numero
    di Nepero, sympy come simbolo, e la figura sarebbe incoerente con la
    formula disegnata."""
    if spec.variable in CONSTANTS:
        issues.append(_constant_issue(("variable",), spec.variable))
    if spec.variables is not None:
        for i, name in enumerate(spec.variables):
            if name in CONSTANTS:
                issues.append(_constant_issue(("variables", i), name))
    if spec.parameter is not None and spec.parameter.name in CONSTANTS:
        issues.append(_constant_issue(("parameter", "name"), spec.parameter.name))


def _check_kind(spec: FunctionFigureSpec, issues: list[SpecIssue]) -> None:
    kind = spec.kind
    kinds = [a.kind for a in spec.annotations]
    if kind == "tangent" and "tangent" not in kinds:
        issues.append(_issue(("annotations",), "kind=tangent richiede un'annotazione tangent"))
    if kind == "area" and "area" not in kinds:
        issues.append(_issue(("annotations",), "kind=area richiede un'annotazione area"))
    if kind == "family":
        if spec.parameter is None:
            issues.append(_issue(("parameter",), "kind=family richiede `parameter`"))
        if len(spec.expressions) != 1:
            issues.append(_issue(("expressions",), "kind=family richiede una sola espressione"))
        if spec.annotations:
            issues.append(_issue(("annotations",), "kind=family non ammette annotazioni"))
    elif spec.parameter is not None:
        issues.append(_issue(("parameter",), "`parameter` è ammesso solo con kind=family"))
    if kind == "level_curves":
        if spec.variables is None:
            issues.append(_issue(("variables",), "kind=level_curves richiede `variables`"))
        elif spec.variables[0] == spec.variables[1]:
            issues.append(_issue(("variables",), "le due variabili devono essere distinte"))
        if len(spec.expressions) != 1:
            issues.append(
                _issue(("expressions",), "kind=level_curves richiede una sola espressione")
            )
        if spec.annotations:
            issues.append(_issue(("annotations",), "kind=level_curves non ammette annotazioni"))
    elif spec.variables is not None:
        issues.append(_issue(("variables",), "`variables` è ammesso solo con kind=level_curves"))
    if spec.parameter is not None:
        names = set(spec.variables) if spec.variables else {spec.variable}
        if spec.parameter.name in names:
            issues.append(
                _issue(("parameter", "name"), "il parametro non può coincidere con la variabile")
            )
        if any(not math.isfinite(v) for v in spec.parameter.values):
            issues.append(_issue(("parameter", "values"), "valori non finiti"))
        elif len(set(spec.parameter.values)) != len(spec.parameter.values):
            issues.append(_issue(("parameter", "values"), "valori duplicati"))
    if len(set(spec.show)) != len(spec.show):
        issues.append(_issue(("show",), "voci duplicate"))


def _check_expressions(spec: FunctionFigureSpec, issues: list[SpecIssue]) -> None:
    declared = spec.declared_symbols()
    labels: set[str] = set()
    for i, item in enumerate(spec.expressions):
        try:
            check_expression(item.expr, free_symbols=declared)
        except ExprError as exc:
            issues.append(_issue(("expressions", i, *exc.loc_suffix), exc.msg, exc.type))
        label = item.label.strip()
        if label:
            if label in labels:
                issues.append(_issue(("expressions", i, "label"), f"label duplicata: {label}"))
            labels.add(label)


def check_function_spec(spec: FunctionFigureSpec) -> list[SpecIssue]:
    """Controlli semantici (livello 2): lista vuota se la spec è valida."""
    issues: list[SpecIssue] = []
    domain_ok = _check_interval(spec.domain, ("domain",), required=True, issues=issues)
    _check_interval(spec.range, ("range",), required=False, issues=issues)
    _check_symbols(spec, issues)
    _check_kind(spec, issues)
    _check_levels(spec, issues)
    _check_annotations(spec, domain_ok=domain_ok, issues=issues)
    _check_expressions(spec, issues)
    return issues


def issues_from_validation_error(exc: ValidationError) -> list[SpecIssue]:
    """Voci `loc/msg/type` dagli errori strutturali Pydantic (senza `input`
    né `ctx`, che possono essere grandi o non serializzabili)."""
    return [
        _issue(tuple(e["loc"]), str(e["msg"]), str(e["type"]))
        for e in exc.errors(include_url=False, include_input=False, include_context=False)
    ]


def parse_function_spec(content: str) -> tuple[FunctionFigureSpec | None, list[SpecIssue]]:
    """Entrambi i livelli su una stringa JSON: `(spec, [])` oppure
    `(None, issues)`."""
    text = (content or "").strip()
    if not text:
        return (None, [_issue((), "spec vuota")])
    try:
        data: Any = json.loads(text)
    except ValueError as exc:
        return (None, [_issue((), f"JSON non valido: {exc}"[:300], "json_invalid")])
    try:
        spec = FunctionFigureSpec.model_validate(data)
    except ValidationError as exc:
        return (None, issues_from_validation_error(exc))
    issues = check_function_spec(spec)
    if issues:
        return (None, issues)
    return (spec, [])


def format_issues(issues: Sequence[SpecIssue]) -> str:
    """`loc: msg; loc: msg` per i messaggi di `validate` del renderer."""
    parts: list[str] = []
    for issue in issues:
        loc = ".".join(str(p) for p in issue["loc"])
        parts.append(f"{loc}: {issue['msg']}" if loc else issue["msg"])
    return "; ".join(parts)


__all__ = [
    "ANALYSIS_ITEMS",
    "DEFAULT_LABELS",
    "DEFAULT_SHOW",
    "MAX_ANNOTATIONS",
    "MAX_EXPRESSIONS",
    "MAX_LEVELS",
    "MAX_WIDTH",
    "MIN_LEVELS",
    "MIN_WIDTH",
    "AreaAnnotation",
    "ExpressionSpec",
    "FunctionFigureSpec",
    "FunctionKind",
    "ParameterSpec",
    "PointAnnotation",
    "SamplingSpec",
    "ShowItem",
    "SpecIssue",
    "TangentAnnotation",
    "Var",
    "check_function_spec",
    "format_issues",
    "issues_from_validation_error",
    "parse_function_spec",
]
