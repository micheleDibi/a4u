"""Specchio fra `figure_theme.FIGURE_I18N` (etichette e frasi delle figure
nel backend, chiavi pienamente qualificate `courses.figures.*`) e i locale
del frontend `it.json` / `en.json` (JSON annidato, appiattito qui).

Le chiavi devono coincidere in entrambe le direzioni, così «Figura N.»,
«Tabella N.», «Lemma N.», il rimando «Figura N» e la didascalia calcolata di
`function` sono identici in editor, vista lezione, slide, PDF e frame video
(D4, D5, A4). Un sottoalbero `courses.figures` vuoto o parziale nei locale
è un fallimento, non uno skip; salta solo se l'albero `frontend/` manca
(convenzione dei test fratelli). Le altre 22 lingue sono fuori perimetro e
non sono confrontate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.services import figure_theme as theme

_LOCALES_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"
_PREFIX = "courses.figures."
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}{key}."))
    elif isinstance(node, str):
        out[prefix[:-1]] = node
    return out


def _frontend_figure_keys(language: str) -> dict[str, str]:
    path = _LOCALES_DIR / f"{language}.json"
    if not path.is_file():
        pytest.skip(f"locale frontend assente: {path}")
    flat = _flatten(json.loads(path.read_text(encoding="utf-8")))
    subset = {k: v for k, v in flat.items() if k.startswith(_PREFIX)}
    assert subset, f"`courses.figures` assente o vuoto in {path.name}"
    expected = len(theme.FIGURE_I18N[language])
    assert len(subset) == expected, (
        f"{path.name}: {len(subset)} chiavi `courses.figures.*`, backend {expected}"
    )
    return subset


@pytest.mark.parametrize("language", ["it", "en"])
def test_backend_keys_mirror_frontend_locale(language: str) -> None:
    frontend = _frontend_figure_keys(language)
    backend = theme.FIGURE_I18N[language]
    missing_in_frontend = sorted(set(backend) - set(frontend))
    missing_in_backend = sorted(set(frontend) - set(backend))
    assert not missing_in_frontend, (
        f"chiavi backend assenti da {language}.json: {missing_in_frontend}"
    )
    assert not missing_in_backend, (
        f"chiavi di {language}.json assenti dal backend: {missing_in_backend}"
    )


@pytest.mark.parametrize("language", ["it", "en"])
def test_placeholders_match_between_backend_and_frontend(language: str) -> None:
    """Stessi segnaposto i18next (`{{n}}`, `{{var}}`, …) per ogni chiave: il
    frontend interpola con `t(key, {n})`, il backend con `_interpolate`."""
    frontend = _frontend_figure_keys(language)
    for key, backend_text in theme.FIGURE_I18N[language].items():
        assert key in frontend, key
        assert set(_PLACEHOLDER_RE.findall(backend_text)) == set(
            _PLACEHOLDER_RE.findall(frontend[key])
        ), key


@pytest.mark.parametrize(
    "figures",
    [{}, {"label": "Figura {{n}}."}],
    ids=["sottoalbero vuoto", "sottoalbero parziale"],
)
def test_empty_or_partial_subtree_fails_instead_of_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, figures: dict[str, str]
) -> None:
    """Un locale con `courses.figures` vuoto o parziale fa fallire lo
    specchio: la guardia non deve tornare a essere uno skip."""
    (tmp_path / "it.json").write_text(
        json.dumps({"courses": {"figures": figures}}), encoding="utf-8"
    )
    monkeypatch.setattr("tests.test_figure_i18n_mirrors_frontend._LOCALES_DIR", tmp_path)
    with pytest.raises(AssertionError):
        _frontend_figure_keys("it")


def test_backend_languages_share_the_same_keys() -> None:
    assert set(theme.FIGURE_I18N["it"]) == set(theme.FIGURE_I18N["en"])
    for key in theme.FIGURE_I18N["it"]:
        assert key.startswith(_PREFIX), key
