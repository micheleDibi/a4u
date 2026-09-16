"""Guardia i18n sui componenti frontend delle figure (WP5, criterio i18n di
Fase D): nessuna stringa italiana hard-coded nei file dell'inventario e ogni
chiave `t("…")` letterale risolta in `it.json` e `en.json`.

Il test legge i sorgenti TypeScript come testo: prima toglie i commenti,
poi estrae i letterali stringa (`"…"`, `'…'`) e il testo JSX fra tag, e
cerca un piccolo lessico italiano (parole che compaiono solo in frasi di
interfaccia: «vuoto», «sorgente», «deve», «errore», …). I template literal
(`\\`…\\``) non sono esaminati: contengono i template didattici degli editor
(sorgenti DOT e Vega-Lite con etichette in italiano, come i template di
`MermaidEditor`, A22), che sono contenuto e non interfaccia.

Salta con motivo esplicito se l'albero `frontend/` manca.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_LOCALES = _FRONTEND / "i18n" / "locales"

# Inventario WP5: componenti nuovi, componenti migrati a i18n e moduli lib
# che producono messaggi mostrati nel `FigureErrorBox`; esteso (WP1 dei
# rimandi) con la vista lezione, che chiude la callable del rimando su
# chiavi `t()` letterali, e con il normalizzatore, che non deve avere
# stringhe di interfaccia (le regole stanno nei commenti).
_INVENTORY = [
    "components/shared/FigureFrame.tsx",
    "components/shared/VegaLiteDiagram.tsx",
    "components/shared/DotDiagram.tsx",
    "components/shared/FunctionFigure.tsx",
    "components/shared/FigureSourceEditor.tsx",
    "components/shared/VegaLiteEditor.tsx",
    "components/shared/DotEditor.tsx",
    "components/shared/FunctionEditor.tsx",
    "components/shared/VisualAssetEditor.tsx",
    "components/shared/AddVisualAssetMenu.tsx",
    "components/shared/MermaidDiagram.tsx",
    "components/shared/MarkdownRenderer.tsx",
    "pages/org/courses/components/LessonSlidesView.tsx",
    "pages/org/courses/components/LessonContentView.tsx",
    "lib/functionSpec.ts",
    "lib/figureFormats.ts",
    "lib/figureNumbering.ts",
    "lib/assetRefNormalize.ts",
]

# Lessico: parole italiane che in questi file possono comparire solo in una
# frase di interfaccia scritta a mano (mai in un identificatore, in una
# classe CSS o in una chiave i18n). «tabella», «equazione» ed «esempio»
# intercettano un'etichetta di blocco o un rimando scritti a mano invece
# che con le chiavi `courses.figures.*`.
_ITALIAN_RE = re.compile(
    r"\b(vuot[oa]|sorgente|deve|errore|caricamento|anteprima|impossibile|"
    r"didascalia|non valid[oa]|non disponibile|figura non|tabella|equazione|"
    r"esempio)\b",
    re.IGNORECASE,
)

# Chiavi letterali delle etichette di blocco (D5) attese nel renderer e
# nella vista slide (forme non numerate).
_RENDERER_LABEL_KEYS = (
    "courses.figures.table.label",
    "courses.figures.table.labelUnnumbered",
    "courses.figures.equation.label",
    "courses.figures.equation.labelUnnumbered",
    "courses.figures.example.label",
    "courses.figures.example.labelUnnumbered",
    "courses.figures.theorem.label",
)
_SLIDES_LABEL_KEYS = (
    "courses.figures.table.labelUnnumbered",
    "courses.figures.example.labelUnnumbered",
)

_LINE_COMMENT_RE = re.compile(r"(?<![:\"'])//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
_TEMPLATE_RE = re.compile(r"`(?:[^`\\]|\\.)*`", re.DOTALL)
_JSX_TEXT_RE = re.compile(r">\s*([^<>{}]*?[A-Za-zÀ-ÿ][^<>{}]*?)\s*<")
_T_CALL_RE = re.compile(r"\bt\(\s*([\"'])((?:courses|common)\.[^\"']+)\1")


def _read(rel: str) -> str:
    path = _FRONTEND / rel
    if not path.is_file():
        pytest.skip(f"sorgente frontend assente: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments_and_templates(source: str) -> str:
    text = _BLOCK_COMMENT_RE.sub(" ", source)
    text = _LINE_COMMENT_RE.sub(" ", text)
    return _TEMPLATE_RE.sub('""', text)


def _ui_texts(source: str) -> list[str]:
    """Letterali stringa e testo JSX del sorgente, senza commenti né
    template literal."""
    text = _strip_comments_and_templates(source)
    out = [m.group(0)[1:-1] for m in _STRING_RE.finditer(text)]
    out.extend(m.group(1) for m in _JSX_TEXT_RE.finditer(text))
    return out


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}{key}."))
    elif isinstance(node, str):
        out[prefix[:-1]] = node
    return out


def _locale(language: str) -> dict[str, str]:
    path = _LOCALES / f"{language}.json"
    if not path.is_file():
        pytest.skip(f"locale frontend assente: {path}")
    return _flatten(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("rel", _INVENTORY)
def test_no_hardcoded_italian_ui_strings(rel: str) -> None:
    offenders = [s for s in _ui_texts(_read(rel)) if _ITALIAN_RE.search(s)]
    assert not offenders, f"{rel}: stringhe italiane hard-coded {offenders}"


@pytest.mark.parametrize("language", ["it", "en"])
def test_literal_t_keys_resolve_in_locale(language: str) -> None:
    flat = _locale(language)
    missing: list[str] = []
    for rel in _INVENTORY:
        text = _strip_comments_and_templates(_read(rel))
        for m in _T_CALL_RE.finditer(text):
            key = m.group(2)
            if key in flat or f"{key}_one" in flat or f"{key}_other" in flat:
                continue
            missing.append(f"{rel}: {key}")
    assert not missing, f"chiavi assenti da {language}.json: {missing}"


def test_asset_blocks_use_the_figures_keys() -> None:
    """Tabelle, equazioni ed esempi portano l'etichetta come le figure
    (D5): `MarkdownRenderer` compone «Tabella N.» / «Equazione N.» /
    «Lemma N.» / «Esempio N.» con le chiavi letterali
    `courses.figures.{table,equation,example,theorem}.*` e la mappa
    `assetNumbers` (chiave `KIND:id_lower`, nessun residuo di
    `figureNumbers`); la vista slide usa le forme non numerate. Le chiavi
    esistono in entrambi i locale con i segnaposto attesi."""
    renderer = _strip_comments_and_templates(_read("components/shared/MarkdownRenderer.tsx"))
    for key in _RENDERER_LABEL_KEYS:
        assert f't("{key}"' in renderer, key
    assert "assetNumbers" in renderer
    assert "figureNumbers" not in renderer
    slides = _strip_comments_and_templates(
        _read("pages/org/courses/components/LessonSlidesView.tsx")
    )
    for key in _SLIDES_LABEL_KEYS:
        assert f't("{key}"' in slides, key
    for language in ("it", "en"):
        flat = _locale(language)
        for key in _RENDERER_LABEL_KEYS:
            assert key in flat, f"{key} assente da {language}.json"
            if key.endswith(".label"):
                assert "{{n}}" in flat[key], (language, key)
        assert "{{kind}}" in flat["courses.figures.theorem.label"], language


def test_parse_error_keys_exist_in_both_locales() -> None:
    """Le due chiavi che sostituiscono le frasi hard-coded del parser
    («spec vuota», «la spec deve essere un oggetto JSON»)."""
    for language in ("it", "en"):
        flat = _locale(language)
        for key in (
            "courses.lessonsContent.render.figure.emptySource",
            "courses.lessonsContent.render.figure.notAnObject",
        ):
            assert key in flat, f"{key} assente da {language}.json"


def test_scanner_catches_the_original_defects() -> None:
    """Il rilevatore deve intercettare le forme trovate dal verificatore
    (guardia contro un lessico troppo stretto)."""
    samples = [
        'if (!text) return { spec: null, error: "spec vuota" };',
        'return { spec: null, error: "la spec deve essere un oggetto JSON" };',
        'setError("sorgente vuoto");',
        "<div>Caricamento della figura</div>",
    ]
    for sample in samples:
        assert any(_ITALIAN_RE.search(s) for s in _ui_texts(sample)), sample
    # Un template didattico (DOT/Vega-Lite in backtick) non è interfaccia.
    template = 'code: `digraph g { a -> b [label="non valido"]; }`,'
    assert not any(_ITALIAN_RE.search(s) for s in _ui_texts(template))


def test_engine_warning_codes_are_localized_not_shown_raw() -> None:
    """L'anteprima dell'editor mostrava i codici del motore tal quali
    (`integral_undefined; symbolic_latex_too_long`), in it come in en:
    ora passano da `describeFunctionWarning` e ogni chiave esiste nei due
    locale (I18N-6)."""
    editor = _read("components/shared/FunctionEditor.tsx")
    assert "warnings.join" not in editor, "codici del motore mostrati tal quali"
    assert "describeFunctionWarning" in editor
    formats = _read("lib/figureFormats.ts")
    keys = set(re.findall(r'return "([A-Za-z]+)";', formats))
    mapped = set(re.findall(r'^\s*[a-z_]+: "([A-Za-z]+)",$', formats, re.M))
    expected = (keys | mapped) - {"invalid"}
    assert {"expressionUndefined", "symbolic", "formulaTooWide"} <= expected
    for language in ("it", "en"):
        flat = _locale(language)
        root = "courses.lessonsContent.editorUI.function.warnings"
        assert f"{root}.label" in flat
        for key in expected:
            assert f"{root}.{key}" in flat, f"{root}.{key} assente da {language}.json"


def test_legacy_asset_banner_names_every_figure_family() -> None:
    """Il banner dell'asset legacy suggeriva solo «diagramma Mermaid o
    immagine» mentre il menu offre quattro famiglie di figure (I18N-8)."""
    for language, needles in (
        ("it", ("Mermaid", "Vega-Lite", "DOT")),
        ("en", ("Mermaid", "Vega-Lite", "DOT")),
    ):
        banner = _locale(language)["courses.lessonsContent.editor.legacyAssetBanner"]
        for needle in needles:
            assert needle in banner, (language, banner)
