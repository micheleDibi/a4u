"""Formato `tikz` (W6-T1): controllo statico e preambolo, senza TeX.

- sorgenti didattici ammessi: catena di misura, circuito (circuitikz),
  grafico pgfplots, `\\foreach` con le sue variabili;
- rifiutati PRIMA di TeX, con regola, riga e colonna: lettura di file
  (`\\input`, `\\openin`, `\\include`), scrittura e shell (`\\write18`,
  `\\immediate`), catcode e notazione `^^`, `\\csname`, `\\scantokens`,
  `\\def`, chiavi che eseguono codice (`.code`, `/utils/exec`), pagina
  intera (`remember picture`, `overlay`), `\\addplot table`/`file`,
  immagini esterne, ambienti non ammessi, contenuto dopo `\\end`, tetti;
- preambolo: font scelto dai caratteri (latino, giapponese, coreano,
  cinese), script senza font rifiutati, pacchetti solo se usati, colori
  della palette, riga del corpo = riga del documento − righe del preambolo.
"""

from __future__ import annotations

import pytest

from app.services.figure_compute import tikz_preamble as pre
from app.services.figure_compute.tikz_lexer import TikzSourceError, check
from app.services.figure_theme import PALETTE

CHAIN = r"""\begin{tikzpicture}[node distance=10mm,
  box/.style={draw, rounded corners, minimum height=9mm, fill=a4uC0!12}]
  % catena di misura
  \node[box] (s) {Sensore};
  \node[box, right=of s] (c) {Condizionamento};
  \node[box, right=of c] (a) {ADC};
  \draw[->] (s) -- node[above] {$v(t)$} (c);
  \draw[->] (c) -- (a);
  \foreach \x/\y in {1/a, 2/b} { \node at (\x, -1) {\y}; }
\end{tikzpicture}"""
CIRCUIT = r"""\begin{circuitikz}
  \draw (0,0) to[R=$R_1$] (2,0) to[V, l=$V_s$] (2,2) -- (0,2) -- (0,0);
\end{circuitikz}"""
PLOT = r"""\begin{tikzpicture}
  \begin{axis}[xlabel={$f$ (Hz)}, ylabel={$|H|$}]
    \addplot[domain=0:10, samples=100] {1/sqrt(1+x^2)};
    \addplot coordinates {(0,1) (1,0.7) (10,0.1)};
    \addlegendentry{misura}
  \end{axis}
\end{tikzpicture}"""


@pytest.mark.parametrize("source", [CHAIN, CIRCUIT, PLOT], ids=["chain", "circuit", "plot"])
def test_teaching_sources_are_accepted(source: str) -> None:
    check(source, max_chars=8000)


def _wrap(body: str) -> str:
    return "\\begin{tikzpicture}\n" + body + "\n\\end{tikzpicture}"


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        (_wrap(r"\input{/proc/self/environ}"), "control_word"),
        (_wrap(r"\openin1=/etc/passwd"), "control_word"),
        (_wrap(r"\include{x}"), "control_word"),
        (_wrap(r"\immediate\write18{ls}"), "control_word"),
        (_wrap(r"\catcode`\^=7"), "control_word"),
        (_wrap(r"^^5cinput{x}"), "caret_notation"),
        (_wrap(r"\csname input\endcsname{x}"), "control_word"),
        (_wrap(r"\scantokens{x}"), "control_word"),
        (_wrap(r"\def\x{1}"), "control_word"),
        (_wrap(r"\makeatletter"), "control_word"),
        (_wrap(r"\node{\includegraphics{/uploads/x.png}};"), "control_word"),
        (_wrap(r"\tikzset{a/.code={x}}"), "forbidden_key_code_key"),
        (_wrap(r"\draw[/utils/exec={x}] (0,0);"), "forbidden_key_utils_exec"),
        (_wrap(r"\draw[execute at begin node={x}] (0,0);"), "forbidden_key_execute_at"),
        (
            r"\begin{tikzpicture}[remember picture, overlay]\end{tikzpicture}",
            "forbidden_key_remember_picture",
        ),
        (_wrap(r"\shade[ball color=red] (0,0) circle (1);"), "forbidden_key_shading"),
        (
            "\\begin{tikzpicture}\\begin{axis}\\addplot table {/etc/passwd};"
            "\\end{axis}\\end{tikzpicture}",
            "plot_source",
        ),
        (
            "\\begin{tikzpicture}\\begin{axis}\\addplot gnuplot {x};\\end{axis}\\end{tikzpicture}",
            "plot_source",
        ),
        (_wrap(r"\begin{verbatim}x\end{verbatim}"), "environment"),
        (r"\begin{tikzpicture}\end{tikzpicture}\input{x}", "content_after_end"),
        (r"\node{x};", "structure"),
        (_wrap(r"\node{a@b};"), "at_sign"),
        (_wrap(r"\node{#1};"), "hash_sign"),
        (_wrap(r"\draw plot[samples=9999] (\x, \x);"), "too_many_samples"),
        (_wrap(r"\foreach \i in {1,...,100000} {\draw (0,0);}"), "foreach_too_long"),
        (_wrap("{" * 25 + "}" * 25), "too_deep"),
        (_wrap(r"\node{x}; }"), "unbalanced_braces"),
    ],
)
def test_dangerous_sources_are_rejected_before_tex(source: str, rule: str) -> None:
    with pytest.raises(TikzSourceError) as excinfo:
        check(source, max_chars=8000)
    assert excinfo.value.rule == rule
    assert str(excinfo.value).startswith(f"tikz_source_invalid: {rule}: ")


def test_limits_and_position() -> None:
    with pytest.raises(TikzSourceError) as long:
        check(CHAIN, max_chars=100)
    assert long.value.rule == "too_long"
    many = _wrap("\n".join(rf"\node at ({i},0) {{n}};" for i in range(61)))
    with pytest.raises(TikzSourceError) as nodes:
        check(many, max_chars=100_000)
    assert nodes.value.rule == "too_many_nodes"
    with pytest.raises(TikzSourceError) as positioned:
        check("\\begin{tikzpicture}\n\\node{ok};\n  \\input{x}\n\\end{tikzpicture}", max_chars=800)
    assert (positioned.value.line, positioned.value.column) == (3, 3)
    # Un comando dentro un commento non conta.
    check("\\begin{tikzpicture}\n% \\input{x}\n\\node{ok};\n\\end{tikzpicture}", max_chars=800)


def test_foreach_variables_are_allowed_only_when_declared() -> None:
    with pytest.raises(TikzSourceError) as excinfo:
        check(_wrap(r"\node at (\x, 0) {a};"), max_chars=800)
    assert excinfo.value.rule == "control_word"


@pytest.mark.parametrize(
    ("text", "font"),
    [
        ("Sensore e ADC", pre.FONT_LATIN),
        ("Датчик", pre.FONT_LATIN),
        ("センサー", pre.FONT_JP),
        ("센서", pre.FONT_KR),
        ("传感器", pre.FONT_SC),
    ],
)
def test_font_follows_the_content(text: str, font: str) -> None:
    assert pre.font_for(text) == font


def test_scripts_without_a_font_are_rejected() -> None:
    with pytest.raises(pre.TikzScriptUnsupportedError):
        pre.font_for("مستشعر")


def test_document_and_packages() -> None:
    document, lines = pre.document(CHAIN)
    head = document.split("\\begin{document}")[0]
    assert "circuitikz" not in head.split("\\PreviewEnvironment")[0]
    assert "pgfplots" not in head
    assert document.count("\\begin{tikzpicture}") == 1
    body_line = document.splitlines()[lines]
    assert body_line.startswith("\\begin{tikzpicture}")
    for index, color in enumerate(PALETTE):
        assert f"\\definecolor{{a4uC{index}}}{{HTML}}{{{color.lstrip('#').upper()}}}" in head
    assert "\\usepackage[european]{circuitikz}" in pre.document(CIRCUIT)[0]
    assert "\\pgfplotsset{compat=1.18}" in pre.document(PLOT)[0]
    for forbidden in ("external", "shadings", "fadings", "shell"):
        assert forbidden not in head
