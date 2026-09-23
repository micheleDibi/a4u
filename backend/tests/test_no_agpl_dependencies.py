"""Guardia AGPL (G11): nessuna dipendenza AGPL nel backend.

PyMuPDF (`fitz`) è AGPL ed è escluso dal brief; Ghostscript (AGPL) entra
con `dvisvgm` e `libgs` di Debian, esclusi dal motore TikZ (J-Q4). Il test
controlla il codice (AST), i moduli caricati dall'app, il Dockerfile, il
`pyproject.toml` e la chiusura delle dipendenze Python installate.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata as md
import re
import sys
import tomllib
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]

_FORBIDDEN_MODULES = frozenset({"fitz", "pymupdf", "pymupdf4llm", "ghostscript"})
_FORBIDDEN_DISTRIBUTIONS = frozenset({"pymupdf", "pymupdfb", "pymupdf4llm", "ghostscript"})
_FORBIDDEN_SYSTEM = re.compile(r"\b(dvisvgm|ghostscript|libgs\d*|pymupdf)\b", re.IGNORECASE)
_AGPL = re.compile(r"\bAGPL|Affero", re.IGNORECASE)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in ("app", "scripts"):
        files.extend(p for p in (_BACKEND / root).rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0].lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0].lower())
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            roots.add(str(node.args[0].value).split(".")[0].lower())
    return roots


def test_no_forbidden_import_in_code() -> None:
    offenders = {
        str(path.relative_to(_BACKEND)): sorted(found)
        for path in _python_files()
        if (
            found := _imported_roots(ast.parse(path.read_text(encoding="utf-8")))
            & _FORBIDDEN_MODULES
        )
    }
    assert offenders == {}


def test_app_import_does_not_load_forbidden_modules() -> None:
    importlib.import_module("app.main")
    importlib.import_module("app.services.figure_attribution")
    importlib.import_module("app.services.source_figure_policy")
    loaded = {name.split(".")[0].lower() for name in sys.modules}
    assert loaded & _FORBIDDEN_MODULES == set()


def test_dockerfile_installs_no_agpl_system_package() -> None:
    text = (_BACKEND / "Dockerfile").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert _FORBIDDEN_SYSTEM.findall(code) == []


def _declared_requirements() -> list[str]:
    data = tomllib.loads((_BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    reqs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        reqs.extend(extra)
    return reqs


def _requirement_name(req: str) -> str:
    return re.split(r"[\s\[<>=!~;@]", req.strip(), maxsplit=1)[0].lower().replace("_", "-")


def test_pyproject_declares_no_agpl_distribution() -> None:
    names = {_requirement_name(r) for r in _declared_requirements()}
    assert names & _FORBIDDEN_DISTRIBUTIONS == set()


def _is_agpl(dist: md.Distribution) -> bool:
    meta = dist.metadata
    fields = [meta.get("License") or "", meta.get("License-Expression") or ""]
    fields.extend(meta.get_all("Classifier") or [])
    return any(_AGPL.search(f) for f in fields if f)


def test_installed_dependency_closure_has_no_agpl() -> None:
    """Chiusura delle dipendenze dichiarate fra quelle installate qui: le
    distribuzioni assenti (extra non installati) si saltano, e il container
    ripete il controllo nello stage `test`."""
    seen: set[str] = set()
    queue = [_requirement_name(r) for r in _declared_requirements()]
    offenders: list[str] = []
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        if name in _FORBIDDEN_DISTRIBUTIONS or _is_agpl(dist):
            offenders.append(name)
        for req in dist.requires or []:
            if "extra ==" in req:
                continue
            queue.append(_requirement_name(req))
    assert offenders == []
