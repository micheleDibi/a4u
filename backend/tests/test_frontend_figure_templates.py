"""Ogni modello del menu «Inserisci template…» è PROVATO, non presunto.

Il test legge i sorgenti TypeScript dei tre editor di figure testuali
(`MermaidEditor.tsx`, `VegaLiteEditor.tsx`, `DotEditor.tsx`), ne estrae i
modelli e li fa passare dal validatore e dal renderer DI PRODUZIONE:

* Vega-Lite — `REGISTRY["vegalite"].validate(code, deep=True)`: schema JSON
  v6, regole D5, euristica del criterio 10 e prova di render con
  `vl_convert`;
* Mermaid — `mermaid_static_gate` (il gate D8 del salvataggio) e il
  pre-render Chromium di produzione, con ZERO `<foreignObject>`: un
  `foreignObject` non è renderizzabile da WeasyPrint e la figura sparirebbe
  dal PDF;
* DOT — `REGISTRY["dot"].validate(code, deep=True)`: limiti, attributi che
  leggono file e prova di render con il binario `dot`.

Motivo: un modello che il docente sceglie dal menu e che poi viene
rifiutato al salvataggio (422 per asset) è un difetto peggiore della sua
assenza. Qui la garanzia è un oracolo reale, non una rilettura del codice.

Si verifica inoltre che ogni modello e ogni famiglia d'uso abbiano la
propria chiave in `it.json` e `en.json`, che i quindici tipi Mermaid
ammessi da D8 siano coperti tutti e che il menu sia ordinato per famiglia
(gruppi contigui, non alfabetici).

Salta con motivo esplicito se l'albero `frontend/` manca; la parte Mermaid
salta senza Chromium o senza la CDN (verifica locale o nel container, come
`test_mermaid_no_foreignobject`).
"""

from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.services import figure_theme as theme
from app.services.figure_render_service import REGISTRY, mermaid_static_gate

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_SHARED = _FRONTEND / "components" / "shared"
_LOCALES = _FRONTEND / "i18n" / "locales"

if not _SHARED.is_dir():  # pragma: no cover - albero frontend assente
    pytest.skip(f"sorgenti frontend assenti: {_SHARED}", allow_module_level=True)

_PREFIX_RE = re.compile(r'^const PREFIX = "([^"]+)";', re.M)
# Una voce del catalogo: `{ id: "x", …, code: `…` }`. I sorgenti dei
# modelli non contengono backtick, quindi il terminatore non è ambiguo.
_ENTRY_RE = re.compile(
    r"\{\s*id:\s*\"(?P<id>[^\"]+)\",(?P<body>.*?)code:\s*`(?P<code>[^`]*)`",
    re.DOTALL,
)
_LABEL_RE = re.compile(r"labelKey:\s*(?:`\$\{PREFIX\}(?P<suffix>[^`]*)`|\"(?P<full>[^\"]+)\")")
_GROUP_CONST_RE = re.compile(r"^\s*(?P<name>\w+):\s*`\$\{PREFIX\}(?P<suffix>[^`]*)`,", re.M)
_GROUP_REF_RE = re.compile(r"groupKey:\s*GROUP\.(?P<name>\w+)")


@dataclass(frozen=True)
class Template:
    editor: str
    id: str
    label_key: str
    group: str
    group_key: str
    code: str


def _extract(rel: str) -> list[Template]:
    """Modelli dichiarati in un editor, nell'ordine del menu."""
    text = (_SHARED / rel).read_text(encoding="utf-8")
    prefix_match = _PREFIX_RE.search(text)
    assert prefix_match is not None, f"{rel}: `const PREFIX` assente"
    prefix = prefix_match.group(1)
    groups = {m.group("name"): prefix + m.group("suffix") for m in _GROUP_CONST_RE.finditer(text)}
    out: list[Template] = []
    for entry in _ENTRY_RE.finditer(text):
        body = entry.group("body")
        label = _LABEL_RE.search(body)
        assert label is not None, f"{rel}: modello {entry.group('id')} senza labelKey"
        label_key = prefix + label.group("suffix") if label.group("suffix") else label.group("full")
        ref = _GROUP_REF_RE.search(body)
        name = ref.group("name") if ref else ""
        assert not name or name in groups, f"{rel}: GROUP.{name} non dichiarato"
        out.append(
            Template(
                editor=rel,
                id=entry.group("id"),
                label_key=label_key,
                group=name,
                group_key=groups.get(name, ""),
                code=entry.group("code"),
            )
        )
    return out


MERMAID = _extract("MermaidEditor.tsx")
VEGALITE = _extract("VegaLiteEditor.tsx")
DOT = _extract("DotEditor.tsx")
ALL = [*MERMAID, *VEGALITE, *DOT]


def _ids(templates: list[Template]) -> list[str]:
    return [t.id for t in templates]


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
    if not path.is_file():  # pragma: no cover - albero frontend parziale
        pytest.skip(f"locale frontend assente: {path}")
    return _flatten(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Il catalogo: consistenza, i18n, ordinamento
# ---------------------------------------------------------------------------


def test_the_extractor_sees_the_whole_catalogue() -> None:
    """Guardia sull'estrattore: senza questa un'espressione regolare rotta
    farebbe passare l'intero test su ZERO modelli."""
    assert len(MERMAID) == len(theme.MERMAID_D8_TYPES), _ids(MERMAID)
    assert len(VEGALITE) >= 20, _ids(VEGALITE)
    assert len(DOT) >= 8, _ids(DOT)
    for tpl in ALL:
        assert tpl.code.strip(), f"{tpl.editor}: modello {tpl.id} vuoto"
        assert tpl.group, f"{tpl.editor}: modello {tpl.id} senza famiglia d'uso"


def test_template_ids_are_unique_per_editor() -> None:
    for templates in (MERMAID, VEGALITE, DOT):
        ids = _ids(templates)
        assert len(ids) == len(set(ids)), ids


@pytest.mark.parametrize("language", ["it", "en"])
def test_every_template_and_group_has_a_label_in_both_locales(language: str) -> None:
    flat = _locale(language)
    missing = [
        f"{tpl.editor}:{tpl.id} -> {key}"
        for tpl in ALL
        for key in (tpl.label_key, tpl.group_key)
        if key not in flat
    ]
    assert not missing, f"chiavi assenti da {language}.json: {missing}"


@pytest.mark.parametrize("language", ["it", "en"])
def test_no_locale_declares_a_template_that_no_editor_offers(language: str) -> None:
    """Il contrario del test precedente: una voce rimasta nel locale dopo la
    rimozione di un modello è un nome tradotto che nessun menu mostra."""
    flat = _locale(language)
    offered = {tpl.label_key for tpl in ALL}
    root = "courses.lessonsContent.editorUI"
    orphans = [
        key
        for key in flat
        if key.startswith(root)
        and ".templates." in key
        and key.rsplit(".", 1)[0].split(".")[-1] == "templates"
        and key not in offered
    ]
    assert not orphans, f"{language}.json: modelli tradotti ma non offerti {orphans}"


@pytest.mark.parametrize(
    ("editor", "templates"),
    [("mermaid", MERMAID), ("vegalite", VEGALITE), ("dot", DOT)],
)
def test_the_menu_is_ordered_by_family_not_alphabetically(
    editor: str, templates: list[Template]
) -> None:
    """Ogni famiglia d'uso occupa un blocco CONTIGUO del menu: è la
    condizione che rende il raggruppamento del `Select` corretto (i gruppi
    si formano sui modelli consecutivi)."""
    seen: list[str] = []
    for tpl in templates:
        if not seen or seen[-1] != tpl.group:
            assert tpl.group not in seen, f"{editor}: famiglia {tpl.group} spezzata nel menu"
            seen.append(tpl.group)
    assert len(seen) >= 2, f"{editor}: {seen}"


def test_mermaid_templates_cover_every_allowed_type() -> None:
    """«Vorrei che ci fossero proprio tutti»: un modello per ciascuno dei
    quindici tipi ammessi dal gate D8, nella forma canonica (nessun alias
    storico `graph`/`stateDiagram`)."""
    declared = {_mermaid_type(tpl.code) for tpl in MERMAID}
    assert declared == set(theme.MERMAID_D8_TYPES), sorted(declared)
    assert not declared & set(theme.MERMAID_LEGACY_ALIASES)


def test_vegalite_catalogue_covers_the_requested_families() -> None:
    """Le famiglie d'uso chieste dal docente, torta compresa."""
    groups = {tpl.group for tpl in VEGALITE}
    assert groups == {
        "comparison",
        "partToWhole",
        "distribution",
        "trend",
        "correlation",
        "matrix",
        "uncertainty",
        "ranking",
    }, sorted(groups)
    assert {"pie", "donut"} <= set(_ids(VEGALITE)), "la torta è stata chiesta esplicitamente"


def _mermaid_type(code: str) -> str:
    from app.services.figure_render_service import mermaid_declared_type

    return mermaid_declared_type(code)


# ---------------------------------------------------------------------------
# Vega-Lite — schema, regole D5 e render con vl_convert
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tpl", VEGALITE, ids=_ids(VEGALITE))
def test_vegalite_template_validates_and_renders(tpl: Template) -> None:
    renderer = REGISTRY["vegalite"]
    if not renderer.available():  # pragma: no cover - dipendenza assente
        pytest.skip("vl_convert o lo schema Vega-Lite non sono disponibili")
    ok, err = renderer.validate(tpl.code, deep=True)
    assert ok, f"{tpl.id}: {err}"
    svg = renderer.render_svg(tpl.code, asset_id=tpl.id)
    assert svg is not None and svg.lstrip().startswith("<svg"), tpl.id


def test_the_vegalite_check_would_catch_a_broken_template() -> None:
    """Controprova dell'oracolo: una spec con `line` senza `clip` e senza
    `scale.domain` — la forma che il validatore rifiuta più spesso — deve
    far fallire lo stesso controllo dei modelli."""
    renderer = REGISTRY["vegalite"]
    if not renderer.available():  # pragma: no cover - dipendenza assente
        pytest.skip("vl_convert o lo schema Vega-Lite non sono disponibili")
    broken = json.dumps(
        {
            "data": {"values": [{"a": 1, "b": 2}]},
            "mark": "line",
            "encoding": {
                "x": {"field": "a", "type": "quantitative"},
                "y": {"field": "b", "type": "quantitative"},
            },
        }
    )
    ok, err = renderer.validate(broken, deep=True)
    assert ok is False and "clip" in err, err


# ---------------------------------------------------------------------------
# DOT — validatore e binario `dot`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tpl", DOT, ids=_ids(DOT))
def test_dot_template_validates_and_renders(tpl: Template) -> None:
    renderer = REGISTRY["dot"]
    if not renderer.available():  # pragma: no cover - binario assente
        pytest.skip("il binario `dot` non è disponibile")
    ok, err = renderer.validate(tpl.code, deep=True)
    assert ok, f"{tpl.id}: {err}"
    svg = renderer.render_svg(tpl.code, asset_id=tpl.id)
    assert svg is not None and svg.lstrip().startswith("<svg"), tpl.id


def test_the_dot_check_would_catch_a_broken_template() -> None:
    """Controprova: un sorgente con un attributo che legge un file è
    rifiutato dallo stesso controllo dei modelli."""
    renderer = REGISTRY["dot"]
    if not renderer.available():  # pragma: no cover - binario assente
        pytest.skip("il binario `dot` non è disponibile")
    ok, err = renderer.validate('digraph g { a [image="/etc/passwd"]; a -> b; }', deep=True)
    assert ok is False and "image" in err, err


# ---------------------------------------------------------------------------
# Mermaid — gate statico D8 e pre-render Chromium (zero foreignObject)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tpl", MERMAID, ids=_ids(MERMAID))
def test_mermaid_template_passes_the_static_gate(tpl: Template) -> None:
    """Il gate del salvataggio, senza browser: nessun modello del menu può
    produrre il 422 «tipo non ammesso / HTML nelle label / risorsa
    esterna»."""
    assert REGISTRY["mermaid"].validate(tpl.code) == (True, ""), tpl.id
    assert mermaid_static_gate(tpl.code) == ("", ""), tpl.id


@pytest.fixture(scope="module")
def mermaid_rendered() -> dict[str, str | None]:
    from app.services import mermaid_prerender as mp

    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:  # pragma: no cover - verifica locale, non gate CI
        pytest.skip("cdn.jsdelivr.net non raggiungibile")
    try:
        svgs = mp._prerender_mermaid_to_svg_batch_sync([tpl.code for tpl in MERMAID])
    except Exception as exc:  # pragma: no cover - launch o rete
        pytest.skip(f"Chromium o CDN non disponibili: {exc!r}"[:300])
    if all(svg is None for svg in svgs):  # pragma: no cover - pagina non pronta
        pytest.skip("pagina di rendering non pronta (__mermaidReady) o CDN non caricata")
    return {tpl.id: svg for tpl, svg in zip(MERMAID, svgs, strict=True)}


@pytest.mark.parametrize("tpl", MERMAID, ids=_ids(MERMAID))
def test_mermaid_template_renders_without_foreignobject(
    mermaid_rendered: dict[str, str | None], tpl: Template
) -> None:
    svg = mermaid_rendered[tpl.id]
    assert svg is not None, f"{tpl.id}: pre-render fallito"
    assert "<foreignObject" not in svg, tpl.id
    assert "<text" in svg, f"{tpl.id}: nessun testo nell'SVG"


def test_angle_brackets_in_a_label_survive_as_text(
    mermaid_rendered: dict[str, str | None],
) -> None:
    """Difetto trovato in produzione: `<` e `>` scritti nudi in una label
    sono letti da Mermaid come tag e il testo sparisce. I modelli usano le
    entità (`&lt;`, `&gt;`) nel flowchart e la forma nativa `~T~` per i
    generici del diagramma delle classi: qui si verifica che il testo
    arrivi davvero nell'SVG."""
    flowchart = mermaid_rendered["flowchart"]
    assert flowchart is not None
    assert "&lt;" in flowchart, "la parentesi angolare della condizione è sparita"
    klass = mermaid_rendered["class"]
    assert klass is not None
    assert "List&lt;String&gt;" in klass, "il generico `List~String~` non è stato reso"


def test_the_mermaid_check_would_catch_a_broken_template(
    mermaid_rendered: dict[str, str | None],
) -> None:
    """Controprova dell'oracolo Mermaid: `journey` (escluso da D8) emette
    `<foreignObject>` e un tipo non ammesso non passa il gate — gli stessi
    due controlli applicati ai modelli."""
    from app.services import mermaid_prerender as mp

    code = "journey\n  title Percorso\n  section Inizio\n    Passo: 5: Studente"
    ok, err = REGISTRY["mermaid"].validate(code)
    assert ok is False and "journey" in err, err
    svg = mp._prerender_mermaid_to_svg_batch_sync([code])[0]
    assert svg is not None and "<foreignObject" in svg
