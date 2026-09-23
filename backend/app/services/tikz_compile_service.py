"""Compilazione delle figure `tikz` (WP6): XeLaTeX → PDF → SVG.

Unico punto che esegue TeX. Il documento arriva già costruito da
`tikz_preamble.document` su un corpo che ha passato `tikz_lexer.check`;
qui la difesa è a livello di processo (SBX-2, decisione J-Q4):

- un processo figlio per compilazione, lanciato da `tex_sandbox.py` con
  `python -I` (limiti CPU, memoria, file scritti, file aperti; core dump a
  zero) in una cartella temporanea propria, con `start_new_session` e
  kill del gruppo al timeout;
- ambiente SOLO dall'elenco (`_env`): niente segreti dell'applicazione
  (`/proc/self/environ` del figlio non li contiene), TeX paranoico
  (`openin_any=p`, `openout_any=p`, `shell_escape=f`, nessun `mktex*`),
  `-no-shell-escape`, `-halt-on-error`;
- autotest una volta per processo: `openin_any` deve valere `p` e un
  documento fidato che prova a leggere `/etc/hostname`, `../x` e
  `/proc/self/environ` non deve riuscirci. Se fallisce, `available()` è
  False e il formato non si offre (guasto chiuso);
- una compilazione alla volta (`TEX_LOCK`, attesa massima
  `FIGURE_TIKZ_QUEUE_TIMEOUT_SECONDS`, poi `TikzBusyError`) e mai mentre
  gira un'estrazione Docling (`HEAVY_JOB_LOCK` occupato: si aspetta, entro
  lo stesso tetto);
- output: PDF di una pagina entro 60 × 60 cm, SVG di `pdftocairo`.

Gli errori di TeX tornano con la riga DEL CORPO (riga del log meno le righe
del preambolo) e senza percorsi.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.tikz_compile")

SANDBOX = Path(__file__).resolve().parent / "figure_compute" / "tex_sandbox.py"
MAX_PAGE_MM = 600.0
PDFTOCAIRO_TIMEOUT_S = 8.0
_ERROR_CAP = 600
_LINE_RE = re.compile(r"^[^:\n]*fig\.tex:(\d+): (.*)$", re.MULTILINE)
_PATH_RE = re.compile(r"(/[\w.\-]+){2,}")

TEX_LOCK = threading.BoundedSemaphore(1)


class TikzCompileError(Exception):
    def __init__(self, line: int | None, message: str) -> None:
        self.line = line
        self.message = message
        where = f"riga {line}: " if line else ""
        super().__init__(f"tikz_compile_failed: {where}{message}")


class TikzEngineUnavailableError(Exception):
    """XeLaTeX o pdftocairo assenti, o sandbox non verificata."""


class TikzBusyError(Exception):
    """Coda della compilazione piena oltre il tetto di attesa."""


class TikzTimeoutError(TikzCompileError):
    def __init__(self) -> None:
        super().__init__(None, "tempo massimo della compilazione superato")


@dataclass(frozen=True)
class CompileResult:
    pdf: bytes
    svg: str


def tex_binary(name: str) -> str | None:
    base = (get_settings().tex_bin_dir or "").strip()
    if base:
        candidate = Path(base) / name
        return str(candidate) if candidate.is_file() else None
    return shutil.which(name)


def _env(workdir: Path, xelatex: str) -> dict[str, str]:
    bin_dir = str(Path(xelatex).parent)
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "TEXMFOUTPUT": str(workdir),
        "TEXMFVAR": str(workdir / "texmf-var"),
        "TEXMFCONFIG": str(workdir / "texmf-config"),
        "XDG_CACHE_HOME": str(workdir / "cache"),
        "openin_any": "p",
        "openout_any": "p",
        "shell_escape": "f",
        "MKTEXPK": "0",
        "MKTEXTFM": "0",
        "MKTEXMF": "0",
        "MKTEXFMT": "0",
        "MKTEXTEX": "0",
        "LANG": "C.UTF-8",
        "SOURCE_DATE_EPOCH": "0",
    }


def _sandboxed(command: list[str]) -> list[str]:
    settings = get_settings()
    cpu = max(2, int(settings.figure_tikz_timeout_seconds))
    return [
        sys.executable,
        "-I",
        str(SANDBOX),
        "--cpu",
        str(cpu),
        "--as-mb",
        "1536",
        "--fsize-mb",
        "16",
        "--nofile",
        "256",
        "--",
        *command,
    ]


def _run(command: list[str], *, workdir: Path, env: dict[str, str], timeout: float) -> str:
    """Esegue il comando nella sandbox; ritorna l'output o solleva."""
    proc = subprocess.Popen(
        _sandboxed(command),
        cwd=workdir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise TikzTimeoutError() from exc
    text = (out or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, command[0], output=text)
    return text


def _clean(message: str) -> str:
    return _PATH_RE.sub("…", " ".join(message.split()))[:_ERROR_CAP]


def _tex_error(log_text: str, preamble_lines: int) -> TikzCompileError:
    match = _LINE_RE.search(log_text)
    if match is None:
        tail = [ln for ln in log_text.splitlines() if ln.startswith("!")]
        return TikzCompileError(None, _clean(tail[0] if tail else "compilazione fallita"))
    line = int(match.group(1)) - preamble_lines
    return TikzCompileError(line if line > 0 else None, _clean(match.group(2)))


def _check_pdf(pdf: bytes) -> None:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        if len(document) != 1:
            raise TikzCompileError(None, f"la figura deve stare in una pagina ({len(document)})")
        width, height = document[0].get_size()
    finally:
        document.close()
    if max(width, height) * 25.4 / 72 > MAX_PAGE_MM:
        raise TikzCompileError(None, "figura oltre 60 × 60 cm")


def _wait_heavy_jobs(deadline: float) -> None:
    """Mai insieme a un'estrazione Docling (stesso processo): si aspetta
    che finisca, entro il tetto della coda."""
    from app.services.heavy_job_lock import HEAVY_JOB_LOCK

    while HEAVY_JOB_LOCK.locked():
        if time.monotonic() > deadline:
            raise TikzBusyError("estrazione delle figure in corso")
        time.sleep(0.5)


def compile_document(document: str, *, preamble_lines: int) -> CompileResult:
    """PDF e SVG del documento. Solleva `TikzCompileError`,
    `TikzEngineUnavailableError` o `TikzBusyError`."""
    settings = get_settings()
    xelatex = tex_binary("xelatex")
    pdftocairo = tex_binary("pdftocairo")
    if xelatex is None or pdftocairo is None:
        raise TikzEngineUnavailableError("xelatex o pdftocairo assenti")
    queue = float(settings.figure_tikz_queue_timeout_seconds)
    deadline = time.monotonic() + queue
    if not TEX_LOCK.acquire(timeout=queue):
        raise TikzBusyError("compilazione TikZ occupata")
    try:
        _wait_heavy_jobs(deadline)
        return _compile_locked(document, preamble_lines, xelatex, pdftocairo)
    finally:
        TEX_LOCK.release()


def _compile_locked(
    document: str, preamble_lines: int, xelatex: str, pdftocairo: str
) -> CompileResult:
    settings = get_settings()
    workdir = Path(tempfile.mkdtemp(prefix="a4u-tikz-"))
    try:
        (workdir / "fig.tex").write_text(document, encoding="utf-8")
        env = _env(workdir, xelatex)
        try:
            _run(
                [
                    xelatex,
                    "-no-shell-escape",
                    "-halt-on-error",
                    "-interaction=nonstopmode",
                    "-file-line-error",
                    "fig.tex",
                ],
                workdir=workdir,
                env=env,
                timeout=float(settings.figure_tikz_timeout_seconds),
            )
        except subprocess.CalledProcessError as exc:
            log_path = workdir / "fig.log"
            log_text = (
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path.is_file()
                else str(exc.output or "")
            )
            raise _tex_error(log_text, preamble_lines) from exc
        pdf_path = workdir / "fig.pdf"
        if not pdf_path.is_file():
            raise TikzCompileError(None, "nessun PDF prodotto")
        pdf = pdf_path.read_bytes()
        _check_pdf(pdf)
        try:
            _run(
                [pdftocairo, "-svg", "-f", "1", "-l", "1", "fig.pdf", "fig.svg"],
                workdir=workdir,
                env=env,
                timeout=PDFTOCAIRO_TIMEOUT_S,
            )
        except subprocess.CalledProcessError as exc:
            raise TikzCompileError(None, "conversione in SVG fallita") from exc
        svg = (workdir / "fig.svg").read_text(encoding="utf-8", errors="replace")
        return CompileResult(pdf=pdf, svg=svg)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --- autotest della sandbox ----------------------------------------------------

_PROBE = r"""\documentclass{article}
\begin{document}
\newread\probe
\openin\probe=/etc/hostname \ifeof\probe \message{A4U-BLOCKED-1}\else\message{A4U-OPENED-1}\fi
\closein\probe
\openin\probe=../x \ifeof\probe \message{A4U-BLOCKED-2}\else\message{A4U-OPENED-2}\fi
\closein\probe
\openin\probe=/proc/self/environ \ifeof\probe \message{A4U-BLOCKED-3}\else\message{A4U-OPENED-3}\fi
\closein\probe
x
\end{document}
"""


def self_test() -> str | None:
    """None se la sandbox fa quello che promette, altrimenti il motivo."""
    xelatex = tex_binary("xelatex")
    kpsewhich = tex_binary("kpsewhich")
    if xelatex is None or kpsewhich is None or tex_binary("pdftocairo") is None:
        return "binari TeX assenti"
    workdir = Path(tempfile.mkdtemp(prefix="a4u-tikz-probe-"))
    try:
        env = _env(workdir, xelatex)
        try:
            value = _run(
                [kpsewhich, "-var-value=openin_any"], workdir=workdir, env=env, timeout=10
            ).strip()
        except (subprocess.CalledProcessError, TikzCompileError) as exc:
            return f"kpsewhich: {exc}"
        if value != "p":
            return f"openin_any={value!r}"
        (workdir / "fig.tex").write_text(_PROBE, encoding="utf-8")
        try:
            output = _run(
                [xelatex, "-no-shell-escape", "-interaction=nonstopmode", "fig.tex"],
                workdir=workdir,
                env=env,
                timeout=30,
            )
        except (subprocess.CalledProcessError, TikzCompileError) as exc:
            return f"sonda non compilata: {exc}"
        opened = re.findall(r"A4U-OPENED-(\d)", output)
        blocked = re.findall(r"A4U-BLOCKED-(\d)", output)
        if opened or sorted(blocked) != ["1", "2", "3"]:
            return f"letture non bloccate: {opened or 'sonda incompleta'}"
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@lru_cache(maxsize=1)
def available() -> bool:
    """Binari presenti e autotest superato (una volta per processo)."""
    problem = self_test()
    if problem is not None:
        log.warning("tikz_sandbox_self_test_failed", reason=problem)
        return False
    log.info("tikz_sandbox_self_test_ok")
    return True
