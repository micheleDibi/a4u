"""Sandbox di TeX (W6-T2, SBX-2): con il filtro statico DISATTIVATO.

I documenti qui vanno direttamente a `compile_document`, senza
`tikz_lexer`: l'isolamento del processo deve reggere da solo.

- autotest: `openin_any=p` attivo, letture di `/etc/hostname`, `../x` e
  `/proc/self/environ` bloccate;
- l'ambiente del figlio contiene solo le variabili ammesse: nessun segreto
  dell'applicazione (anche se presente nell'ambiente del padre);
- `\\input` di un percorso assoluto, `\\write18` e `\\openout` assoluto non
  hanno effetto;
- un ciclo infinito viene ucciso al timeout (gruppo di processi), senza
  processi superstiti;
- errori con la riga del corpo e senza percorsi.

Richiede xelatex, kpsewhich e pdftocairo (container `test` con TeX):
altrove il modulo è saltato con `[dep:tex]`.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services import tikz_compile_service as tex
from app.services.figure_compute import tikz_preamble as pre
from tests.dep_guard import require_binary


@pytest.fixture(autouse=True)
def _tex() -> None:
    require_binary("tex", "xelatex", "kpsewhich", "pdftocairo")


def _document(body: str) -> tuple[str, int]:
    head = pre.preamble(font=pre.FONT_LATIN, pgfplots=False, circuit=False)
    return head + body + "\n\\end{document}\n", head.count("\n")


def test_self_test_passes() -> None:
    assert tex.self_test() is None


def test_child_environment_has_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-canary-should-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://canary")
    workdir = Path(tempfile.mkdtemp())
    xelatex = tex.tex_binary("xelatex")
    assert xelatex is not None
    output = tex._run(["/usr/bin/env"], workdir=workdir, env=tex._env(workdir, xelatex), timeout=10)
    names = {line.split("=", 1)[0] for line in output.splitlines() if "=" in line}
    assert "canary" not in output
    assert names <= set(tex._env(workdir, xelatex)) | {"PWD", "SHLVL", "_"}


def test_absolute_input_is_not_read() -> None:
    document, lines = _document(r"\input{/proc/self/environ}")
    with pytest.raises(tex.TikzCompileError) as excinfo:
        tex.compile_document(document, preamble_lines=lines)
    assert "/proc" not in str(excinfo.value)


def test_shell_escape_and_absolute_writes_do_nothing() -> None:
    marker = Path(tempfile.gettempdir()) / f"a4u-pwned-{os.getpid()}"
    body = (
        rf"\immediate\write18{{touch {marker}}}"
        "\n\\newwrite\\out\\immediate\\openout\\out=" + str(marker) + ".tex"
        "\n\\immediate\\write\\out{x}\\immediate\\closeout\\out"
        "\n\\begin{tikzpicture}\\node{ok};\\end{tikzpicture}"
    )
    document, lines = _document(body)
    # openout_any=p può anche far fallire la compilazione: conta l'effetto.
    with contextlib.suppress(tex.TikzCompileError):
        tex.compile_document(document, preamble_lines=lines)
    assert not marker.exists()
    assert not Path(f"{marker}.tex").exists()


def test_infinite_loop_is_killed_at_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = get_settings().model_copy(update={"figure_tikz_timeout_seconds": 2})
    monkeypatch.setattr(tex, "get_settings", lambda: patched)
    document, lines = _document(r"\loop\iftrue\repeat")
    started = time.monotonic()
    with pytest.raises(tex.TikzTimeoutError):
        tex.compile_document(document, preamble_lines=lines)
    assert time.monotonic() - started < 8
    time.sleep(0.5)
    alive = subprocess.run(["pgrep", "-f", "xelatex"], capture_output=True, text=True)
    assert alive.stdout.strip() == ""


def test_errors_point_at_the_body_line() -> None:
    body = "\\begin{tikzpicture}\n\\node (a) {x};\n\\draw (a) -- (nessuno);\n\\end{tikzpicture}"
    document, lines = _document(body)
    with pytest.raises(tex.TikzCompileError) as excinfo:
        tex.compile_document(document, preamble_lines=lines)
    assert excinfo.value.line == 3
    assert "nessuno" in excinfo.value.message
    assert "/tmp" not in excinfo.value.message
