"""Passo 1 del parsing delle espressioni del formato `function` (Q4).

Un'espressione (`"(x**2 - 1)/(x - 2)"`) è accettata solo se il suo AST
Python contiene esclusivamente i nodi di una formula: operatori
aritmetici, potenze con esponente costante limitato, chiamate alle
funzioni della whitelist, i simboli dichiarati e le costanti `pi` ed `E`.
Nessun `eval`: questo modulo non valuta nulla. Il passo 2 (sympy con
`global_dict` ristretto) gira SOLO nel processo figlio
(`function_symbolic.py`) sulla stessa stringa già filtrata: le due difese
sono indipendenti e il passo 1 è quella che ferma attributi, lambda,
subscript e chiamate arbitrarie (`docs/courses/17-visual-figures.md` §4.2).

Solo libreria standard: importabile ovunque, anche dallo schema Pydantic.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

FUNCTIONS: frozenset[str] = frozenset(
    {
        "sin",
        "cos",
        "tan",
        "exp",
        "log",
        "sqrt",
        "abs",
        "asin",
        "acos",
        "atan",
        "sinh",
        "cosh",
        "tanh",
        "floor",
    }
)
CONSTANTS: frozenset[str] = frozenset({"pi", "E"})

MAX_EXPR_CHARS = 200
MAX_NODES = 80
MAX_DEPTH = 12
MAX_POW_EXPONENT = 12.0

# `type` delle voci `meta.errors` del 422 (`loc` = ["expressions", i, "expr"]).
EXPR_SYNTAX = "expr_syntax"
EXPR_FORBIDDEN = "expr_forbidden"
EXPR_SYMBOL = "expr_symbol"
EXPR_LIMIT = "expr_limit"

# Due operandi giustapposti (`2x`, `2 x`, `x y`, `2(x+1)`): Python li
# rifiuta come sintassi o li legge come chiamata; per il docente sono
# moltiplicazioni implicite.
_JUXTAPOSED_RE = re.compile(r"(?:\d[a-zA-Z(]|[\w)]\s+[\w(])")

_BINOPS: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Pow: "**",
}


class ExprError(ValueError):
    """Espressione rifiutata dal passo 1. `loc_suffix` prosegue la `loc`
    dell'espressione nel payload 422 (`("expr",)`), `type` è uno dei
    codici `EXPR_*`, `msg` il messaggio per il docente."""

    def __init__(
        self,
        msg: str,
        *,
        type: str = EXPR_FORBIDDEN,
        loc_suffix: tuple[str | int, ...] = ("expr",),
    ) -> None:
        super().__init__(msg)
        self.msg = msg
        self.type = type
        self.loc_suffix = loc_suffix


@dataclass(frozen=True)
class ParsedExpr:
    """Espressione accettata: sorgente normalizzato, AST, simboli liberi
    effettivamente usati (sottoinsieme di quelli dichiarati), misura."""

    source: str
    tree: ast.Expression
    names: frozenset[str]
    node_count: int
    depth: int


class _Walker:
    def __init__(self, free_symbols: frozenset[str]) -> None:
        self.free_symbols = free_symbols
        self.names: set[str] = set()
        self.count = 0
        self.max_depth = 0

    def visit(self, node: ast.AST, depth: int) -> None:
        self.count += 1
        if self.count > MAX_NODES:
            raise ExprError(f"espressione troppo lunga (oltre {MAX_NODES} nodi)", type=EXPR_LIMIT)
        if depth > MAX_DEPTH:
            raise ExprError(
                f"espressione troppo annidata (oltre {MAX_DEPTH} livelli)", type=EXPR_LIMIT
            )
        self.max_depth = max(self.max_depth, depth)
        if isinstance(node, ast.Expression):
            self.visit(node.body, depth + 1)
        elif isinstance(node, ast.Constant):
            self._constant(node)
        elif isinstance(node, ast.Name):
            self._name(node)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd)):
                raise ExprError(f"operatore non ammesso: {type(node.op).__name__}")
            self.visit(node.operand, depth + 1)
        elif isinstance(node, ast.BinOp):
            self._binop(node, depth)
        elif isinstance(node, ast.Call):
            self._call(node, depth)
        else:
            raise ExprError(f"costrutto non ammesso: {type(node).__name__}")

    @staticmethod
    def _constant(node: ast.Constant) -> None:
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExprError(f"costante non ammessa: {value!r} (solo numeri reali)")
        if isinstance(value, float) and not math.isfinite(value):
            raise ExprError("costante non finita")

    def _name(self, node: ast.Name) -> None:
        if node.id in FUNCTIONS:
            raise ExprError(f"funzione senza argomento: {node.id} (scrivi {node.id}(x))")
        if node.id in CONSTANTS:
            return
        if node.id in self.free_symbols:
            self.names.add(node.id)
            return
        raise ExprError(f"simbolo non dichiarato: {node.id}", type=EXPR_SYMBOL)

    def _binop(self, node: ast.BinOp, depth: int) -> None:
        if isinstance(node.op, ast.BitXor):
            raise ExprError("usa ** per la potenza (x^2 → x**2)", type=EXPR_SYNTAX)
        if type(node.op) not in _BINOPS:
            raise ExprError(f"operatore non ammesso: {type(node.op).__name__}")
        self.visit(node.left, depth + 1)
        self.visit(node.right, depth + 1)
        if isinstance(node.op, ast.Pow):
            exponent = constant_value(node.right)
            if exponent is not None and abs(exponent) > MAX_POW_EXPONENT:
                raise ExprError(f"esponente costante oltre ±{MAX_POW_EXPONENT:g}", type=EXPR_LIMIT)

    def _call(self, node: ast.Call, depth: int) -> None:
        func = node.func
        if isinstance(func, ast.Constant):
            raise ExprError(
                "moltiplicazione implicita non ammessa: scrivi 2*(...)", type=EXPR_SYNTAX
            )
        if not isinstance(func, ast.Name):
            raise ExprError("chiamata non ammessa: solo funzioni della whitelist")
        if func.id not in FUNCTIONS:
            if func.id in self.free_symbols or func.id in CONSTANTS:
                raise ExprError(
                    f"moltiplicazione implicita non ammessa: scrivi {func.id}*(...)",
                    type=EXPR_SYNTAX,
                )
            raise ExprError(
                f"funzione non ammessa: {func.id}; ammesse: " + ", ".join(sorted(FUNCTIONS))
            )
        if node.keywords:
            raise ExprError(f"argomenti con nome non ammessi in {func.id}(...)")
        if any(isinstance(a, ast.Starred) for a in node.args):
            raise ExprError(f"argomento non ammesso in {func.id}(...)")
        max_args = 2 if func.id == "log" else 1
        if not 1 <= len(node.args) <= max_args:
            raise ExprError(
                f"{func.id} accetta {'1 o 2 argomenti' if max_args == 2 else 'un argomento'}"
            )
        for arg in node.args:
            self.visit(arg, depth + 1)


def constant_value(node: ast.AST) -> float | None:
    """Valore di un sotto-albero fatto solo di costanti e operatori
    aritmetici (`3**2`, `-(1/3)`), `None` se contiene simboli o non è
    calcolabile. Le potenze interne rispettano lo stesso limite
    dell'esponente, così `2**12**12` è respinto prima di essere calcolato."""
    try:
        return _fold(node)
    except (ExprError, ZeroDivisionError, OverflowError, ValueError):
        return None


def _fold(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _fold(node.operand)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        a = _fold(node.left)
        b = _fold(node.right)
        if a is None or b is None:
            return None
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        if abs(b) > MAX_POW_EXPONENT:
            raise ExprError("esponente costante oltre il limite", type=EXPR_LIMIT)
        return float(a**b)
    return None


def _syntax_message(src: str, exc: SyntaxError) -> str:
    detail = (exc.msg or "").lower()
    if "invalid decimal literal" in detail or _JUXTAPOSED_RE.search(src):
        return "moltiplicazione implicita non ammessa: scrivi 2*x"
    return f"sintassi non valida: {exc.msg}"


def check_expression(src: str, *, free_symbols: Iterable[str]) -> ParsedExpr:
    """Passo 1: accetta o rifiuta `src` senza valutarla.

    Solleva `ExprError` con il messaggio per il docente (moltiplicazione
    implicita `2x`, `^` al posto di `**`, simbolo non dichiarato, costrutto
    o funzione non ammessi, limiti di dimensione). I simboli liberi
    ammessi sono `free_symbols` (variabile, variabili delle curve di
    livello, nome del parametro).
    """
    declared = frozenset(free_symbols)
    text = (src or "").strip()
    if not text:
        raise ExprError("espressione vuota", type=EXPR_SYNTAX)
    if len(text) > MAX_EXPR_CHARS:
        raise ExprError(f"espressione oltre {MAX_EXPR_CHARS} caratteri", type=EXPR_LIMIT)
    if "^" in text:
        raise ExprError("usa ** per la potenza (x^2 → x**2)", type=EXPR_SYNTAX)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExprError(_syntax_message(text, exc), type=EXPR_SYNTAX) from None
    except (ValueError, RecursionError, MemoryError) as exc:
        raise ExprError(f"sintassi non valida: {exc}", type=EXPR_SYNTAX) from None
    # Conteggio prima della visita: una somma di 42 addendi è annidata a
    # sinistra (profondità 42) e deve essere rifiutata per dimensione, non
    # per profondità.
    total = sum(1 for node in ast.walk(tree) if not isinstance(node, ast.expr_context))
    if total > MAX_NODES:
        raise ExprError(f"espressione troppo lunga (oltre {MAX_NODES} nodi)", type=EXPR_LIMIT)
    walker = _Walker(declared)
    walker.visit(tree, 0)
    return ParsedExpr(
        source=text,
        tree=tree,
        names=frozenset(walker.names),
        node_count=walker.count,
        depth=walker.max_depth,
    )


__all__ = [
    "CONSTANTS",
    "EXPR_FORBIDDEN",
    "EXPR_LIMIT",
    "EXPR_SYMBOL",
    "EXPR_SYNTAX",
    "FUNCTIONS",
    "MAX_DEPTH",
    "MAX_EXPR_CHARS",
    "MAX_NODES",
    "MAX_POW_EXPONENT",
    "ExprError",
    "ParsedExpr",
    "check_expression",
    "constant_value",
]
