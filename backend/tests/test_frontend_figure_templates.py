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
ammessi da D8 siano coperti tutti, che il menu sia ordinato per famiglia
(gruppi contigui, non alfabetici) e che il CATALOGO del prompt di Fase 3
nomini ogni modello Vega-Lite provato qui: il modello che genera i
contenuti e il menu dell'editor devono offrire gli stessi tipi di
grafico, e nessuno che il validatore rifiuterebbe.

Salta con motivo esplicito se l'albero `frontend/` manca; la parte Mermaid
salta senza Chromium o senza la CDN (verifica locale o nel container, come
`test_mermaid_no_foreignobject`).
"""

from __future__ import annotations

import json
import math
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.services import figure_theme as theme
from app.services import openai_lesson_content_service as content
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


# Termine con cui il CATALOGO del prompt di Fase 3 nomina ciascun modello
# provato qui sotto. La mappa lega le due metà della richiesta del docente
# («tutti i diagrammi, anche in produzione di contenuti e non solo in
# modifica»): il prompt non promette al modello nessun tipo di grafico che
# il validatore rifiuterebbe, e un modello nuovo negli editor obbliga ad
# aggiornare il prompt.
_P3_CATALOGUE_TERMS: dict[str, str] = {
    "barsVertical": "barre verticali",
    "barsHorizontal": "orizzontali",
    "barsGrouped": "barre raggruppate",
    "barsStacked": "barre impilate",
    "barsNormalized": "barre impilate normalizzate",
    "pie": "torta",
    "donut": "ciambella",
    "histogram": "istogramma",
    "boxplot": "diagramma a scatola",
    "dotPlot": "punti impilati",
    "violin": "violino",
    "line": "linea singola",
    "multiLine": "linee multiple",
    "area": "area e aree impilate",
    "stackedArea": "aree impilate",
    "stepLine": "linea a gradini",
    "timeSeries": "serie temporale",
    "scatter": "dispersione",
    "bubble": "bolle",
    "heatmap": "mappa di calore",
    "barsWithError": "barre di errore",
    "confidenceBand": "banda di confidenza",
    "barsRanked": "barre ordinate",
    "lollipop": "bastoncini",
}


def _p3_vegalite_catalogue() -> str:
    prompt = content._system_prompt("it")
    block = prompt[prompt.index("CATALOGO VEGA-LITE") : prompt.index("Le FUNZIONI MATEMATICHE")]
    return " ".join(block.split())


def test_the_p3_catalogue_names_every_proven_vegalite_template() -> None:
    """Il catalogo del prompt di Fase 3 e il menu degli editor dicono la
    stessa cosa: ogni tipo di grafico suggerito al modello è uno dei
    modelli che passano validatore e renderer poco più sotto."""
    assert set(_P3_CATALOGUE_TERMS) == set(_ids(VEGALITE)), sorted(
        set(_P3_CATALOGUE_TERMS) ^ set(_ids(VEGALITE))
    )
    catalogue = _p3_vegalite_catalogue()
    for template_id, term in sorted(_P3_CATALOGUE_TERMS.items()):
        assert term in catalogue, f"{template_id}: «{term}» assente dal catalogo di Fase 3"
    italian = _locale("it")
    for tpl in VEGALITE:
        family = italian[tpl.group_key].lower()
        assert family in catalogue, f"{tpl.group_key}: famiglia «{family}» assente dal catalogo"


# Lo stesso legame per DOT: il paragrafo del prompt di Fase 3 nomina i tipi
# di grafo che l'editor offre già provati. Senza, il modello continua a
# produrre solo alberi e flussi, e un automa esce senza la notazione degli
# stati accettanti perché il prompt vietava lo «stile».
_P3_DOT_TERMS: dict[str, str] = {
    "tree": "albero",
    "binaryTree": "albero binario",
    "digraph": "grafo diretto",
    "pipeline": "dipendenze",
    "undirected": "non orientato (`--`)",
    "automaton": "automa (`shape=circle`, `doublecircle` sugli stati accettanti",
    "record": "record di una struttura dati (`shape=Mrecord` con le porte)",
    "cluster": "raggruppamenti (`subgraph cluster_*`)",
}


def test_the_p3_paragraph_names_every_proven_dot_template() -> None:
    """Il paragrafo DOT del prompt di Fase 3 e il menu dell'editor dicono la
    stessa cosa: ogni tipo di grafo suggerito al modello è uno degli otto
    modelli che passano validatore e binario `dot` poco più sotto."""
    assert set(_P3_DOT_TERMS) == set(_ids(DOT)), sorted(set(_P3_DOT_TERMS) ^ set(_ids(DOT)))
    prompt = content._system_prompt("it")
    dot = " ".join(prompt[prompt.index("DOT (Graphviz)") : prompt.index("FUNCTION (")].split())
    for template_id, term in sorted(_P3_DOT_TERMS.items()):
        assert term in dot, f"{template_id}: «{term}» assente dal paragrafo DOT di Fase 3"
    # Le forme su cui si reggono cinque modelli su otto devono essere
    # ammesse dal prompt, non solo dal validatore.
    assert "`shape` SOLO quando porta significato" in dot
    for tpl in DOT:
        for shape in re.findall(r"shape=(\w+)", tpl.code):
            assert REGISTRY["dot"].validate(tpl.code)[0], tpl.id
            assert shape in {"circle", "doublecircle", "point", "diamond", "Mrecord", "cylinder"}


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


@pytest.fixture(scope="module")
def vegalite_rendered() -> dict[str, str]:
    renderer = REGISTRY["vegalite"]
    if not renderer.available():  # pragma: no cover - dipendenza assente
        pytest.skip("vl_convert o lo schema Vega-Lite non sono disponibili")
    out: dict[str, str] = {}
    for tpl in VEGALITE:
        svg = renderer.render_svg(tpl.code, asset_id=tpl.id)
        assert svg is not None, tpl.id
        out[tpl.id] = svg
    return out


def _svg_texts(svg: str) -> list[str]:
    return [t for t in re.findall(r"<text[^>]*>([^<]*)</text>", svg) if t.strip()]


def test_no_vegalite_label_is_truncated_by_the_theme(
    vegalite_rendered: dict[str, str],
) -> None:
    """Il tema tronca le etichette d'asse oltre `labelLimit` e ci mette
    un'ellissi: «Esercitazion…». Colpiva proprio le barre orizzontali, il
    tipo che esiste apposta per le etichette lunghe. Nessun testo reso deve
    portare l'ellissi."""
    troncati = {
        tid: [t for t in _svg_texts(svg) if "…" in t] for tid, svg in vegalite_rendered.items()
    }
    assert not any(troncati.values()), {k: v for k, v in troncati.items() if v}


def test_the_truncation_check_would_catch_a_label_longer_than_the_limit() -> None:
    """Controprova: il limite esiste ancora (non è stato tolto), e oltre ~40
    caratteri l'etichetta viene troncata — è il numero scritto nel catalogo
    del prompt di Fase 3."""
    renderer = REGISTRY["vegalite"]
    if not renderer.available():  # pragma: no cover - dipendenza assente
        pytest.skip("vl_convert o lo schema Vega-Lite non sono disponibili")
    lunga = "Attività didattica integrativa con tutorato disciplinare in piccoli gruppi"
    spec = json.dumps(
        {
            "data": {"values": [{"attivita": lunga, "ore": 12}]},
            "mark": "bar",
            "encoding": {
                "y": {"field": "attivita", "type": "nominal"},
                "x": {
                    "field": "ore",
                    "type": "quantitative",
                    "scale": {"domain": [0, 20]},
                    "axis": {"title": "Ore"},
                },
            },
        }
    )
    svg = renderer.render_svg(spec, asset_id="troncatura")
    assert svg is not None
    assert any("…" in t for t in _svg_texts(svg))


# Modelli le cui categorie hanno un ordine PROPRIO (cronologico, logico o
# per quota): senza `sort` Vega-Lite le dispone in ordine alfabetico e la
# figura dice una cosa falsa — un orario delle lezioni con «Giovedì» prima
# di «Lunedì», una torta con gli spicchi in ordine di nome.
_ORDINE_ATTESO: dict[str, tuple[str, ...]] = {
    "barsStacked": ("Invernale", "Estiva", "Autunnale"),
    "barsNormalized": ("Invernale", "Estiva", "Autunnale"),
    "heatmap": ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì"),
    "pie": ("Lezioni", "Esercitazioni", "Laboratorio", "Verifiche"),
    "donut": ("Prova scritta", "Prova orale", "Relazione di laboratorio"),
}


@pytest.mark.parametrize("template_id", sorted(_ORDINE_ATTESO))
def test_ordered_categories_render_in_the_declared_order(
    vegalite_rendered: dict[str, str], template_id: str
) -> None:
    """L'ordine si verifica sull'SVG RESO, non sulla presenza di `sort`
    nella sorgente: è quello che il lettore vede su assi e legenda."""
    atteso = _ORDINE_ATTESO[template_id]
    testi = _svg_texts(vegalite_rendered[template_id])
    posizioni = [testi.index(voce) for voce in atteso if voce in testi]
    assert len(posizioni) == len(atteso), f"{template_id}: {atteso} vs {testi}"
    assert posizioni == sorted(posizioni), f"{template_id}: ordine reso {testi}"


# Un arco di Vega-Lite è `M x0,y0 A r,r 0 <large> <sweep> x1,y1 L0,0Z`: il
# punto dopo `A` è il bordo ANTIORARIO dello spicchio (l'inizio, leggendo in
# senso orario da ore 12) e il punto di `M` è quello orario. Da qui angolo
# iniziale e ampiezza di ogni spicchio, senza rasterizzare.
_ARC_RE = re.compile(
    r'<path[^>]*\bd="M\s*(-?[\d.]+),(-?[\d.]+)'
    r"A\s*([\d.]+),[\d.]+,0,([01]),([01]),\s*(-?[\d.]+),(-?[\d.]+)"
)


def _slice_widths_clockwise(svg: str) -> list[float]:
    """Ampiezze degli spicchi nell'ordine in cui il lettore li incontra
    girando in senso orario da ore 12."""
    settori: list[tuple[float, float]] = []
    for match in _ARC_RE.finditer(svg):
        x0, y0, _r, _large, _sweep, x1, y1 = (float(v) for v in match.groups())

        def angolo(x: float, y: float) -> float:
            return (math.degrees(math.atan2(x, -y)) + 360) % 360

        inizio, fine = angolo(x1, y1), angolo(x0, y0)
        settori.append((inizio, (fine - inizio) % 360 or 360.0))
    return [ampiezza for _inizio, ampiezza in sorted(settori)]


@pytest.mark.parametrize("template_id", ["pie", "donut"])
def test_the_pie_slices_run_by_descending_share(
    vegalite_rendered: dict[str, str], template_id: str
) -> None:
    """La torta si legge se lo spicchio più grande parte da ore 12 e gli
    altri seguono per quota decrescente. Non basta `color.sort` (che ordina
    la legenda): serve il canale `order`, verificato qui sulla GEOMETRIA
    degli archi resi, non sulla sorgente."""
    ampiezze = _slice_widths_clockwise(vegalite_rendered[template_id])
    assert len(ampiezze) >= 3, ampiezze
    assert ampiezze == sorted(ampiezze, reverse=True), ampiezze


def test_the_slice_order_check_would_catch_a_pie_without_the_order_channel() -> None:
    """Controprova misurata: `color.sort` da solo mette la legenda in ordine
    di quota ma lascia gli spicchi in ordine sparso (86,4° → 57,6° → 172,8°
    → 43,2°). È il motivo per cui i due modelli portano anche `order`."""
    renderer = REGISTRY["vegalite"]
    if not renderer.available():  # pragma: no cover - dipendenza assente
        pytest.skip("vl_convert o lo schema Vega-Lite non sono disponibili")
    spec = json.loads(next(t.code for t in VEGALITE if t.id == "pie"))
    del spec["encoding"]["order"]
    ok, err = renderer.validate(json.dumps(spec), deep=True)
    assert ok, err
    svg = renderer.render_svg(json.dumps(spec), asset_id="pie-senza-order")
    assert svg is not None
    ampiezze = _slice_widths_clockwise(svg)
    assert ampiezze != sorted(ampiezze, reverse=True), ampiezze


def test_the_order_check_would_catch_a_missing_sort() -> None:
    """Controprova: la stessa mappa di calore senza `sort` passa il
    validatore e rende i giorni in ordine alfabetico."""
    renderer = REGISTRY["vegalite"]
    if not renderer.available():  # pragma: no cover - dipendenza assente
        pytest.skip("vl_convert o lo schema Vega-Lite non sono disponibili")
    spec = json.loads(next(t.code for t in VEGALITE if t.id == "heatmap"))
    del spec["encoding"]["x"]["sort"]
    ok, err = renderer.validate(json.dumps(spec), deep=True)
    assert ok, err
    svg = renderer.render_svg(json.dumps(spec), asset_id="heatmap-senza-sort")
    assert svg is not None
    testi = _svg_texts(svg)
    assert testi.index("Giovedì") < testi.index("Lunedì")


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


# ---------------------------------------------------------------------------
# GEOMETRIA: il testo che esce dalla tela lo taglia WeasyPrint
#
# Il gate D8 e il pre-render non guardano dove FINISCE il testo. I tipi
# Mermaid a tela fissa (radar 600 px più i margini del tema, treemap con il
# corpo del testo dedotto dall'altezza della piastrella) non ridimensionano
# il riquadro sulle etichette: quello che esce dal `viewBox` sparisce nel
# PDF («ittura» al posto di «Scrittura», due voci di legenda entrambe
# «Rilevazione»). Qui si misura in Chromium il bbox reale di ogni `<text>`
# reso e lo si confronta con il viewBox.
# ---------------------------------------------------------------------------

# Tolleranza: Mermaid disegna il titolo del radar a filo del bordo
# superiore (`y = -altezza/2`, `dominant-baseline: hanging`), quindi l'em
# box sporge di ~2,6 px anche nel campione ufficiale D8. L'inchiostro
# resta dentro (verificato rasterizzando: la prima riga di pixel è vuota),
# perciò si tollera l'em box ma non un vero sconfinamento.
_OVERFLOW_TOLLERATO_PX = 4.0

_MISURA_JS = """
(svg) => {
  document.body.innerHTML = svg;
  const root = document.querySelector('svg');
  const vb = root.viewBox.baseVal;
  const fuori = [];
  for (const t of root.querySelectorAll('text')) {
    if (getComputedStyle(t).display === 'none') continue;
    const testo = (t.textContent || '').trim();
    if (!testo) continue;
    const b = t.getBBox();
    const inv = root.getScreenCTM().inverse().multiply(t.getScreenCTM());
    const pt = (x, y) => { const p = root.createSVGPoint(); p.x = x; p.y = y;
                           return p.matrixTransform(inv); };
    const c = [pt(b.x, b.y), pt(b.x + b.width, b.y),
               pt(b.x, b.y + b.height), pt(b.x + b.width, b.y + b.height)];
    const xs = c.map(p => p.x), ys = c.map(p => p.y);
    const over = Math.max(vb.x - Math.min(...xs), Math.max(...xs) - (vb.x + vb.width),
                          vb.y - Math.min(...ys), Math.max(...ys) - (vb.y + vb.height));
    if (over > 0) fuori.push({testo, over});
  }
  return fuori;
}
"""


@pytest.fixture(scope="module")
def mermaid_overflow(mermaid_rendered: dict[str, str | None]) -> dict[str, list[dict[str, Any]]]:
    """Per ogni modello: i testi che escono dal viewBox e di quanto."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            for name, svg in mermaid_rendered.items():
                out[name] = page.evaluate(_MISURA_JS, svg) if svg else []
            browser.close()
    except Exception as exc:  # pragma: no cover - launch di Chromium
        pytest.skip(f"Chromium non disponibile per la misura: {exc!r}"[:200])
    return out


@pytest.mark.parametrize("tpl", MERMAID, ids=_ids(MERMAID))
def test_mermaid_template_keeps_its_text_inside_the_canvas(
    mermaid_overflow: dict[str, list[dict[str, Any]]], tpl: Template
) -> None:
    tagliati = [
        f"{riga['testo']!r} fuori di {riga['over']:.1f}px"
        for riga in mermaid_overflow[tpl.id]
        if riga["over"] > _OVERFLOW_TOLLERATO_PX
    ]
    assert not tagliati, f"{tpl.id}: {tagliati}"


def test_the_overflow_check_would_catch_a_label_too_long_for_the_radar(
    mermaid_overflow: dict[str, list[dict[str, Any]]],
) -> None:
    """Controprova dell'oracolo: sul radar, che ha la tela più stretta
    rispetto al testo, un'etichetta di legenda molto lunga esce davvero dal
    viewBox e la misura la vede."""
    from app.services import mermaid_prerender as mp

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    lungo = (
        "radar-beta\n  title Profilo\n"
        '  axis a["Analisi"], b["Sintesi"], c["Calcolo"]\n'
        '  curve x["Rilevazione iniziale delle competenze in ingresso"]{2, 3, 3}\n'
        "  max 5\n  min 0"
    )
    svg = mp._prerender_mermaid_to_svg_batch_sync([lungo])[0]
    assert svg is not None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content("<html><body></body></html>")
        fuori = page.evaluate(_MISURA_JS, svg)
        browser.close()
    assert any(riga["over"] > _OVERFLOW_TOLLERATO_PX for riga in fuori), fuori


def test_no_mermaid_text_carries_a_literal_newline(
    mermaid_rendered: dict[str, str | None],
) -> None:
    """`sankey-beta` scrive nome e valore del nodo in un solo `<text>`
    separati da un a capo: la specifica SVG dice di RIMUOVERE i fine riga,
    e WeasyPrint lo fa («Lezioni48»). Il post-processing del pre-render li
    porta a spazio, quindi nessun SVG reso deve più contenerne."""
    residui = {
        name: re.findall(r"<text\b[^>]*>[^<]*\n[^<]*</text>", svg or "")
        for name, svg in mermaid_rendered.items()
    }
    assert not any(residui.values()), {k: v for k, v in residui.items() if v}
    sankey = mermaid_rendered["sankey"]
    assert sankey is not None
    assert "Lezioni 48" in sankey and "Lezioni\n48" not in sankey
