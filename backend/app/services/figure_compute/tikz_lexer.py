"""Controllo statico del sorgente `tikz` (WP6, solo libreria standard).

Prima difesa: il corpo arriva dal modello o dal docente e verrà compilato
da XeLaTeX. Si accetta solo un sottoinsieme di TikZ, pgfplots e
circuitikz per disegnare figure, con i catcode di default:

- caratteri: niente `^^` (notazione dei caratteri di TeX), `@`, `#` (salvo
  `\\#`) e caratteri di controllo;
- struttura: un solo ambiente esterno `tikzpicture` o `circuitikz`, niente
  dopo `\\end`; ambienti interni solo `scope`, `axis` (e varianti
  logaritmiche), `pgfonlayer`; graffe e begin/end bilanciati;
- control word SOLO dall'allowlist (TikZ, testo, matematica, pgfplots,
  circuitikz) più le variabili dichiarate dai `\\foreach`; control symbol
  solo quelli tipografici. Tutto il resto (`\\input`, `\\openin`,
  `\\write`, `\\catcode`, `\\csname`, `\\def`, `\\includegraphics`, …) è
  rifiutato perché non è nell'elenco;
- chiavi vietate: esecuzione di codice (`.code`, `/utils/exec`,
  `/handlers/`, `execute at`, `store in`), sovrapposizioni alla pagina
  (`remember picture`, `overlay`), sfumature e trasparenze, `external`;
  dopo `\\addplot` solo coordinate o espressioni (niente `table`, `file`,
  `gnuplot`, `shell`, `graphics`);
- tetti: caratteri, profondità delle graffe, nodi, comandi di tracciato,
  `samples`, iterazioni dei `\\foreach`.

`check()` solleva `TikzSourceError` con regola, riga e colonna: il
messaggio va al fix AI e al 422 del PATCH (`tikz_source_invalid: …`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_BRACE_DEPTH = 20
MAX_NODES = 60
MAX_PATH_COMMANDS = 400
MAX_SAMPLES = 200
MAX_FOREACH_ITERATIONS = 500
MAX_FOREACH_PER_STATEMENT = 3

OUTER_ENVIRONMENTS = frozenset({"tikzpicture", "circuitikz"})
INNER_ENVIRONMENTS = frozenset(
    {"scope", "axis", "semilogxaxis", "semilogyaxis", "loglogaxis", "pgfonlayer"}
)

_TIKZ_WORDS = {
    "begin", "end", "draw", "path", "fill", "filldraw", "node", "coordinate",
    "clip", "matrix", "pic", "foreach", "useasboundingbox", "tikzset",
    "ctikzset", "pgfdeclarelayer", "pgfsetlayers",
}  # fmt: skip
_TEXT_WORDS = {
    "textbf", "textit", "emph", "textrm", "textsf", "texttt", "textup",
    "textsubscript", "textsuperscript", "small", "footnotesize",
    "scriptsize", "normalsize", "bfseries", "itshape", "rmfamily",
    "sffamily", "ttfamily", "upshape", "mdseries", "color", "textcolor",
    "ldots", "dots", "textdegree", "textmu", "euro", "par", "newline",
    "hspace", "vspace", "quad", "qquad", "centering",
}  # fmt: skip
_MATH_WORDS = {
    "frac", "dfrac", "tfrac", "sqrt", "cdot", "times", "div", "pm", "mp",
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta",
    "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi",
    "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau", "upsilon",
    "phi", "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta",
    "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega",
    "partial", "nabla", "infty", "int", "iint", "oint", "sum", "prod",
    "lim", "sin", "cos", "tan", "arcsin", "arccos", "arctan", "sinh",
    "cosh", "tanh", "log", "ln", "exp", "max", "min", "arg", "det",
    "mathrm", "mathit", "mathbf", "mathsf", "mathcal", "mathbb", "boldsymbol",
    "vec", "hat", "bar", "dot", "ddot", "tilde", "widehat", "widetilde",
    "overline", "underline", "overrightarrow", "left", "right", "big",
    "Big", "bigl", "bigr", "Bigl", "Bigr", "rightarrow", "leftarrow",
    "Rightarrow", "Leftarrow", "leftrightarrow", "Leftrightarrow", "to",
    "mapsto", "uparrow", "downarrow", "approx", "sim", "simeq", "cong",
    "equiv", "neq", "ne", "leq", "le", "geq", "ge", "ll", "gg", "propto",
    "circ", "degree", "cdots", "vdots", "ddots", "text", "operatorname",
    "angle", "perp", "parallel", "in", "notin", "subset", "cup", "cap",
    "forall", "exists", "Re", "Im", "hbar", "ell", "prime", "star", "ast",
    "bullet", "oplus", "otimes", "langle", "rangle", "lfloor", "rfloor",
    "lceil", "rceil", "vert", "Vert", "mid", "cdotp", "colon", "limits",
    "displaystyle", "textstyle", "scriptstyle",
}  # fmt: skip
_PLOT_WORDS = {"addplot", "addlegendentry", "legend"}
ALLOWED_CONTROL_WORDS: frozenset[str] = frozenset(
    _TIKZ_WORDS | _TEXT_WORDS | _MATH_WORDS | _PLOT_WORDS
)
ALLOWED_CONTROL_SYMBOLS = frozenset(
    ["\\\\", "\\,", "\\;", "\\:", "\\!", "\\ ", "\\{", "\\}", "\\%", "\\$", "\\&", "\\_", "\\#"]
)

_FORBIDDEN_KEYS: tuple[tuple[str, str], ...] = (
    # Handler di pgfkeys che attaccano codice a una chiave: `.code`,
    # `.ecode`, `.code n args`, ma anche `.append code`, `.prefix code` e
    # `.add code` (la parola `code` non segue il punto).
    ("code_key", r"\.(?:e?code|(?:append|prefix|add)\s+code)\b"),
    ("store_in", r"\bstore\s+in\b"),
    # Handler che scrivono il valore in una macro (`.estore in`, `.get`).
    ("value_to_macro", r"\.(?:estore\s+in|get)\b"),
    ("utils_exec", r"/utils/exec"),
    ("handlers", r"/handlers/"),
    ("execute_at", r"\bexecute\s+at\b"),
    ("remember_picture", r"\bremember\s+picture\b"),
    ("overlay", r"\boverlay\b"),
    ("transform_shape", r"\btransform\s+shape\b"),
    ("shading", r"\bshad(?:e|ing)\b"),
    ("ball_color", r"\bball\s+color\b"),
    ("gradient_color", r"\b(?:left|right|top|bottom|inner|outer|middle|upper|lower)\s+color\b"),
    ("fading", r"\bfading\b"),
    ("pattern", r"\bpattern\b"),
    ("transparency_group", r"\btransparency\s+group\b"),
    ("external", r"\bexternal\b"),
)
_PLOT_FORBIDDEN_RE = re.compile(r"\b(?:table|file|gnuplot|shell|graphics)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(
    r"(?P<cw>\\[A-Za-z]+)|(?P<cs>\\[^A-Za-z])|(?P<lb>\{)|(?P<rb>\})"
    r"|(?P<comment>%[^\n]*)|(?P<text>[^\\{}%]+)",
    re.DOTALL,
)
_ENV_NAME_RE = re.compile(r"\s*\{([A-Za-z*]+)\}")
_NODE_IN_PATH_RE = re.compile(r"\bnode\s*[\[({]")
_SAMPLES_RE = re.compile(r"\bsamples\s*=\s*(\d+)")
_RANGE_RE = re.compile(
    r"\{\s*(-?\d+(?:\.\d+)?)\s*,\s*(?:(-?\d+(?:\.\d+)?)\s*,\s*)?\.\.\.\s*,\s*(-?\d+(?:\.\d+)?)\s*\}"
)
_FOREACH_VARS_RE = re.compile(r"\s*((?:\\[A-Za-z]+\s*/?\s*)+)")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATH_COMMANDS = frozenset(
    {"draw", "path", "fill", "filldraw", "node", "coordinate", "clip", "matrix", "pic", "addplot"}
)


class TikzSourceError(ValueError):
    """Sorgente `tikz` rifiutato dal controllo statico."""

    def __init__(self, rule: str, line: int, column: int, detail: str) -> None:
        self.rule = rule
        self.line = line
        self.column = column
        self.detail = detail
        super().__init__(f"tikz_source_invalid: {rule}: {line}:{column} {detail}")


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    offset: int


def _position(source: str, offset: int) -> tuple[int, int]:
    line = source.count("\n", 0, offset) + 1
    column = offset - (source.rfind("\n", 0, offset) + 1) + 1
    return line, column


def _fail(source: str, offset: int, rule: str, detail: str) -> TikzSourceError:
    line, column = _position(source, offset)
    return TikzSourceError(rule, line, column, detail[:120])


def strip_comments(source: str) -> str:
    """Il sorgente senza commenti `%…` (un `\\%` resta)."""
    out: list[str] = []
    for match in _TOKEN_RE.finditer(source):
        if match.lastgroup != "comment":
            out.append(match.group(0))
    return "".join(out)


def _tokens(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    for match in _TOKEN_RE.finditer(source):
        if match.start() != position:  # pragma: no cover - la regex copre tutto
            raise _fail(source, position, "unexpected_char", source[position])
        position = match.end()
        kind = match.lastgroup or "text"
        if kind != "comment":
            tokens.append(_Token(kind, match.group(0), match.start()))
    if position != len(source):  # pragma: no cover
        raise _fail(source, position, "unexpected_char", source[position])
    return tokens


def _check_characters(source: str) -> None:
    for rule, needle in (("caret_notation", "^^"), ("at_sign", "@")):
        index = source.find(needle)
        if index >= 0:
            raise _fail(source, index, rule, needle)
    for match in re.finditer(r"#", source):
        if match.start() == 0 or source[match.start() - 1] != "\\":
            raise _fail(source, match.start(), "hash_sign", "#")
    control = _CONTROL_CHARS_RE.search(source)
    if control is not None:
        raise _fail(source, control.start(), "control_char", repr(control.group(0)))


def _check_foreach_ranges(source: str) -> None:
    for match in _RANGE_RE.finditer(source):
        start = float(match.group(1))
        second = float(match.group(2)) if match.group(2) is not None else None
        end = float(match.group(3))
        step = (second - start) if second is not None else (1.0 if end >= start else -1.0)
        if step == 0 or (end - start) / step < 0:
            raise _fail(source, match.start(), "foreach_range", match.group(0))
        if (end - start) / step + 1 > MAX_FOREACH_ITERATIONS:
            raise _fail(source, match.start(), "foreach_too_long", match.group(0))


def check(source: str, *, max_chars: int) -> None:
    """Solleva `TikzSourceError` se il sorgente non è ammesso."""
    if len(source) > max_chars:
        raise TikzSourceError("too_long", 1, 1, f"{len(source)} caratteri oltre {max_chars}")
    _check_characters(source)
    body = strip_comments(source)
    lowered = body.lower()
    for rule, pattern in _FORBIDDEN_KEYS:
        found = re.search(pattern, lowered)
        if found is not None:
            raise _fail(body, found.start(), f"forbidden_key_{rule}", found.group(0))
    for match in _SAMPLES_RE.finditer(body):
        if int(match.group(1)) > MAX_SAMPLES:
            raise _fail(body, match.start(), "too_many_samples", match.group(0))
    _check_foreach_ranges(body)

    tokens = _tokens(body)
    significant = [t for t in tokens if not (t.kind == "text" and not t.value.strip())]
    if not significant or significant[0].value != "\\begin":
        raise TikzSourceError("structure", 1, 1, "il contenuto deve iniziare con \\begin{…}")
    stack: list[tuple[str, int]] = []
    depth = 0
    nodes = 0
    path_commands = 0
    foreach_vars: set[str] = set()
    closed_outer_at: int | None = None
    statement_foreach = 0
    plot_statement: list[str] | None = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if closed_outer_at is not None and not (token.kind == "text" and not token.value.strip()):
            raise _fail(body, token.offset, "content_after_end", token.value[:40])
        if plot_statement is not None:
            plot_statement.append(token.value)
        if token.kind == "lb":
            depth += 1
            if depth > MAX_BRACE_DEPTH:
                raise _fail(body, token.offset, "too_deep", f"profondità {depth}")
        elif token.kind == "rb":
            depth -= 1
            if depth < 0:
                raise _fail(body, token.offset, "unbalanced_braces", "}")
        elif token.kind == "cs":
            if token.value not in ALLOWED_CONTROL_SYMBOLS:
                raise _fail(body, token.offset, "control_symbol", token.value)
        elif token.kind == "text":
            nodes += len(_NODE_IN_PATH_RE.findall(token.value))
            if ";" in token.value:
                statement_foreach = 0
                if plot_statement is not None:
                    _check_plot(body, token.offset, plot_statement)
                    plot_statement = None
        elif token.kind == "cw":
            name = token.value[1:]
            if name in ("begin", "end"):
                env_match = _ENV_NAME_RE.match(body, token.offset + len(token.value))
                if env_match is None:
                    raise _fail(body, token.offset, "environment", "nome dell'ambiente assente")
                env = env_match.group(1)
                if name == "begin":
                    allowed = OUTER_ENVIRONMENTS if not stack else INNER_ENVIRONMENTS
                    if env not in allowed:
                        raise _fail(body, token.offset, "environment", env)
                    stack.append((env, depth))
                else:
                    if not stack or stack[-1][0] != env:
                        raise _fail(body, token.offset, "environment_mismatch", env)
                    stack.pop()
                    if not stack:
                        closed_outer_at = token.offset
                # Salta `{nome}`: non è testo del disegno.
                while i + 1 < len(tokens) and tokens[i + 1].offset < env_match.end():
                    i += 1
                i += 1
                continue
            if name not in ALLOWED_CONTROL_WORDS and name not in foreach_vars:
                raise _fail(body, token.offset, "control_word", token.value)
            if name in _PATH_COMMANDS:
                path_commands += 1
                if path_commands > MAX_PATH_COMMANDS:
                    raise _fail(body, token.offset, "too_many_paths", str(path_commands))
            if name == "node":
                nodes += 1
            if name == "addplot":
                plot_statement = []
            if name == "foreach":
                statement_foreach += 1
                if statement_foreach > MAX_FOREACH_PER_STATEMENT:
                    raise _fail(body, token.offset, "foreach_nesting", str(statement_foreach))
                declared = _FOREACH_VARS_RE.match(body, token.offset + len(token.value))
                if declared is not None:
                    foreach_vars.update(re.findall(r"\\([A-Za-z]+)", declared.group(1)))
        if nodes > MAX_NODES:
            raise _fail(body, token.offset, "too_many_nodes", str(nodes))
        i += 1
    if stack:
        raise TikzSourceError("environment_unclosed", 1, 1, stack[-1][0])
    if depth != 0:
        raise TikzSourceError("unbalanced_braces", 1, 1, f"profondità finale {depth}")
    if closed_outer_at is None:
        raise TikzSourceError("structure", 1, 1, "ambiente esterno non chiuso")


def _check_plot(source: str, offset: int, statement: list[str]) -> None:
    text = "".join(statement)
    found = _PLOT_FORBIDDEN_RE.search(text)
    if found is not None:
        raise _fail(source, offset, "plot_source", found.group(0))
    rest = re.sub(r"^\s*\+?\s*(\[[^\]]*\])?\s*", "", text)
    if not rest.startswith(("coordinates", "{", "(")):
        raise _fail(source, offset, "plot_source", rest[:30])
