"""WP3 — le quattro famiglie di figure nei prompt di Fase 3/4/5.

Test puri (nessun DB, nessuna rete): il blocco «FORMATI DELLE FIGURE» di
P3 elenca i tipi Mermaid ammessi ed esclusi (D8), le regole D5, lo schema
compatto di `FunctionFigureSpec` (D9) e tre esempi minimi che devono
superare i validatori reali del registro; P4 rinvia a Fase 3 senza
graffe; P5 vieta la lettura a voce delle sorgenti. Il testo dei prompt è
statico (A19): nessun test dipende da `available_formats()`.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import typing
from typing import Any

import pytest

from app.schemas import figure_function as ff
from app.services import openai_lesson_content_service as content
from app.services import openai_lesson_slides_service as slides
from app.services import openai_lesson_speech_service as speech
from app.services.figure_compute.function_parse import FUNCTIONS
from app.services.figure_render_service import REGISTRY
from app.services.figure_theme import MERMAID_D8_TYPES, MERMAID_EXCLUDED_TYPES

FORMATS = ("mermaid", "vegalite", "dot", "function")


def _p3(**kwargs: object) -> str:
    return content._system_prompt("it", **kwargs)  # type: ignore[arg-type]


def _flat(text: str) -> str:
    """Testo con gli a capo dell'impaginazione collassati in spazi: le
    asserzioni sulle frasi non dipendono dalla larghezza delle righe."""
    return " ".join(text.split())


def _figure_block(prompt: str) -> str:
    return prompt[prompt.index("FORMATI DELLE FIGURE") : prompt.index("EQUAZIONI — ENUNCIATO")]


# ---------------------------------------------------------------------------
# P3 — tabella D8, tipi Mermaid, regole D5/D3
# ---------------------------------------------------------------------------


def test_p3_figure_block_names_the_four_formats_and_maps_content_to_them():
    block = _figure_block(_p3())
    for fmt in FORMATS:
        assert f"→ `{fmt}`" in block, fmt
    # Il blocco sta fra i requisiti degli asset e le equazioni, prima di
    # LINGUA (ordine verificato da test_prompt_register).
    prompt = _p3()
    assert prompt.index("REQUISITI — ASSET VISIVI") < prompt.index("FORMATI DELLE FIGURE")
    assert prompt.index("FORMATI DELLE FIGURE") < prompt.index("LINGUA — REGOLA TASSATIVA")


def test_p3_use_case_table_names_every_allowed_mermaid_type():
    """La tabella «dal contenuto al formato» nomina TUTTI i tipi D8, non i
    soli otto storici: senza il caso d'uso accanto al nome il modello non
    produce mai gantt, sankey-beta, quadrantChart e compagnia, anche se la
    riga «Tipi ammessi» li elenca."""
    block = _figure_block(_p3())
    table = _flat(block[: block.index("MERMAID 11.")])
    for name in MERMAID_D8_TYPES:
        assert name in table, name
    # Ogni tipo porta il proprio criterio di scelta fra parentesi.
    for criterion in (
        "flowchart (processo, decisione)",
        "gantt (pianificazione e dipendenze temporali)",
        "sankey-beta (flussi che si ripartiscono fra stadi;",
        "quadrantChart (posizionamento su due criteri)",
        "radar-beta (profilo su più criteri, etichette brevi)",
        "treemap-beta (gerarchia con quantità confrontabili)",
        "block-beta (architettura a blocchi e livelli)",
        "pie (ripartizione a poche voci)",
        "xychart-beta (serie breve su assi, senza pretesa quantitativa)",
    ):
        assert criterion in table, criterion


def test_p3_vegalite_catalogue_lists_the_eight_use_families():
    """Il catalogo Vega-Lite: una riga per famiglia d'uso, con i tipi e il
    costrutto che li produce. È la risposta al difetto misurato in
    produzione (solo barre e linee) e ogni tipo nominato è uno dei modelli
    provati dagli editor (`test_frontend_figure_templates`)."""
    block = _flat(_figure_block(_p3()))
    assert "CATALOGO VEGA-LITE — famiglia d'uso: tipi (costrutto):" in block
    for family in (
        "confronto fra categorie:",
        "parte sul tutto:",
        "distribuzione:",
        "andamento nel tempo:",
        "correlazione:",
        "matrice:",
        "incertezza:",
        "graduatoria:",
    ):
        assert family in block, family
    for construct in (
        '`stack: "zero"`',
        '`stack: "normalize"`',
        "`arc` con `theta`",
        "`innerRadius`",
        "`bar` con `bin`",
        "`boxplot`",
        "`transform.density`",
        '`interpolate: "step-after"`',
        '`type: "temporal"`',
        "`point` con `size`",
        "`rect`, `color` con `scale.scheme`",
        "`errorbar`",
        '`sort: "-x"`',
        "`layer` di `rule` e `point`",
    ):
        assert construct in block, construct


def test_p3_names_the_limits_of_the_quantitative_mermaid_types():
    """I sei tipi Mermaid che portano NUMERI non sono raccomandati a scatola
    chiusa: `sankey-beta` è l'unico tipo con colori propri (schemeTableau10
    di d3, non esposto come variabile di tema, `figure_theme`), il radar ha
    una tela fissa che non contiene etichette lunghe e il treemap dimensiona
    il testo sull'altezza della piastrella."""
    table = _flat(_figure_block(_p3()))
    assert "unico tipo con colori propri, non del tema" in table
    assert "solo quando il flusso è il contenuto" in table
    assert "radar-beta (profilo su più criteri, etichette brevi)" in table
    assert "treemap-beta (gerarchia con quantità confrontabili)" in table


def test_p3_data_honesty_rule_covers_all_four_formats_not_only_vegalite():
    """La clausola della caption stava dentro il paragrafo VEGA-LITE, ma la
    tabella raccomanda sei tipi Mermaid quantitativi (pie, xychart-beta,
    sankey-beta, treemap-beta, radar-beta, quadrantChart): la stessa
    ripartizione 60/40 inventata non può essere dichiarata illustrativa solo
    se disegnata con `arc` e muta se disegnata con `pie` di Mermaid."""
    block = _flat(_figure_block(_p3()))
    regola = "ONESTÀ DEI DATI, per TUTTI e quattro i formati:"
    assert regola in block
    clausola = block[block.index(regola) : block.index("MERMAID 11.")]
    assert "dichiara la fonte nella caption" in clausola
    assert "Dati illustrativi, non sperimentali" in clausola
    # Sta PRIMA dei paragrafi per formato, non dentro quello di Vega-Lite.
    assert block.index(regola) < block.index("MERMAID 11.") < block.index("VEGA-LITE")


def test_p3_pie_carries_the_professional_criterion_not_just_the_permission():
    """La torta è autorizzata ma guidata: poche categorie che compongono un
    intero, altrimenti barre. Il prodotto ha un registro accademico."""
    block = _flat(_figure_block(_p3()))
    rule = block[block.index("La TORTA") : block.index("Le FUNZIONI MATEMATICHE")]
    assert "poche categorie" in rule
    assert "fino a sei" in rule
    assert "compongono un intero" in rule
    assert "le barre ordinate si leggono meglio" in rule
    assert "Mai per confrontare grandezze che non sommano a un tutto" in rule


def test_p3_lists_mermaid_allowed_and_excluded_types():
    block = _figure_block(_p3())
    allowed = block[block.index("Tipi ammessi:") : block.index("Esclusi:")]
    excluded = block[block.index("Esclusi:") : block.index("Label in testo semplice")]
    for name in MERMAID_D8_TYPES:
        assert name in allowed, name
    for name in MERMAID_EXCLUDED_TYPES:
        assert name in excluded, name
    assert "journey" in excluded and "journey" not in allowed
    assert "%%{init}%%" in block


def test_p3_vegalite_rules_and_style():
    block = _flat(_figure_block(_p3()))
    for text in (
        "4000 caratteri",
        "≤ 200 righe",
        "`data.url`",
        "`data.name`",
        '`mark: "image"`',
        "`config`",
        "`params`",
        "`selection`",
        "`tooltip`",
        '`"clip": true`',
        "`scale.domain`",
        "una `title`",
        "Dati illustrativi, non sperimentali",
        "`axis.title` con l'unità di misura",
        "legenda solo con più serie",
        "NON si tracciano in Vega-Lite: usa `function`",
    ):
        assert text in block, text


def test_p3_dot_rules():
    block = _flat(_figure_block(_p3()))
    dot = block[block.index("DOT (Graphviz)") : block.index("FUNCTION (")]
    for text in ("`graph`, `digraph` o `strict`", "label brevi", "nessun colore", "`image`"):
        assert text in dot, text


def test_p3_dot_paragraph_allows_the_shapes_that_carry_meaning():
    """Il divieto di «stile» tolse a DOT la notazione: un automa senza
    `doublecircle` non è impoverito, è sbagliato. Il validatore ammette
    `shape` (rifiuta solo gli attributi che leggono file) e nove dei
    diciotto modelli dell'editor ci si reggono."""
    dot = _flat(_figure_block(_p3()))
    dot = dot[dot.index("DOT (Graphviz)") : dot.index("FUNCTION (")]
    assert "nessun colore né font (li impone il renderer)" in dot
    assert "`shape` SOLO quando porta significato" in dot
    # Il divieto storico, che copriva anche la notazione, non c'è più.
    assert "nessun colore, font o stile" not in dot
    for tipo in (
        "albero, albero binario",
        "grafo diretto e non orientato (`--`)",
        "dipendenze",
        "automa (`shape=circle`, `doublecircle` sugli stati accettanti",
        "ingresso `shape=point`",
        "record di una struttura dati (`shape=Mrecord` con le porte)",
        "raggruppamenti (`subgraph cluster_*`)",
    ):
        assert tipo in dot, tipo


def test_p3_caption_rule_forbids_figure_prefix():
    prompt = _p3()
    divieti = _flat(prompt[prompt.index("DIVIETI ASSOLUTI") : prompt.index("CASO SPECIALE")])
    assert '"Figura 1"/"Fig. 1"' in divieti
    assert "il numero lo mette il renderer" in divieti


def test_p3_lingua_covers_the_new_formats():
    prompt = _p3()
    lingua = _flat(prompt[prompt.index("LINGUA — REGOLA TASSATIVA") :])
    for text in (
        "`axis.title`",
        "`legend.title`",
        "`label` dei sorgenti DOT",
        "`expressions[].label`",
        "`annotations[].label`",
        "di Vega-Lite (chiavi JSON, `field`, `type`, espressioni `datum.*`)",
        "di DOT (ID dei nodi, `->`/`--`",
        "di `function` (chiavi JSON, `expr`, `kind`, `show`)",
        # Nei tipi che il catalogo raccomanda (torta, ciambella, barre
        # ordinate, mappa di calore) le CATEGORIE sono l'etichettatura, e
        # stanno in `data.values`: senza il campo nominato qui, in un corso
        # tradotto due terzi del testo di una torta resterebbero in italiano.
        "VALORI TESTUALI dentro `data.values`",
        "le categorie che si leggono su assi e legenda",
    ):
        assert text in lingua, text


# ---------------------------------------------------------------------------
# P3 — le clausole del catalogo provate sulla LETTURA LETTERALE
#
# Verificare che una stringa sia nel prompt non dice se la figura che ne
# nasce si legge. I quattro tipi a valore DERIVATO (normalizzate,
# istogramma, scatola, violino) hanno il valore dell'asse quantitativo
# FUORI dai dati che il modello scrive, e `bar` non è fra i mark con `clip`
# obbligatorio: né il validatore né il render segnalano nulla. Qui si
# costruisce la spec come il catalogo la prescrive, si rende con il
# renderer di produzione e si guarda il risultato; la controprova mostra
# che senza la clausola la stessa spec è valida e illeggibile.
# ---------------------------------------------------------------------------


def _svg_texts(svg: str) -> list[str]:
    return [t for t in re.findall(r"<text[^>]*>([^<]*)</text>", svg or "") if t.strip()]


def _min_path_y(svg: str) -> float:
    """Ordinata minima dei tracciati: negativa = marca fuori dal riquadro."""
    ys: list[float] = []
    for d in re.findall(r'<path[^>]*d="([^"]+)"', svg or ""):
        numbers = [float(n) for n in re.findall(r"-?\d+\.?\d*", d)]
        ys += numbers[1::2]
    assert ys, "nessun tracciato nell'SVG"
    return min(ys)


def _render(spec: dict[str, object]) -> str:
    renderer = REGISTRY["vegalite"]
    source = json.dumps(spec, ensure_ascii=False)
    ok, error = renderer.validate(source, deep=True)
    assert ok, error
    svg = renderer.render_svg(source)
    assert svg is not None and svg.lstrip().startswith("<svg")
    return svg


_NORMALIZED: dict[str, Any] = {
    "title": "Composizione per anno (dati illustrativi)",
    "data": {
        "values": [
            {"anno": "2024", "esito": "A", "n": 30},
            {"anno": "2024", "esito": "B", "n": 70},
            {"anno": "2025", "esito": "A", "n": 45},
            {"anno": "2025", "esito": "B", "n": 55},
        ]
    },
    "mark": "bar",
    "encoding": {
        "x": {"field": "anno", "type": "nominal"},
        "y": {
            "field": "n",
            "type": "quantitative",
            "stack": "normalize",
            "scale": {"domain": [0, 1]},
            "axis": {"title": "Quota sul totale", "format": ".0%"},
        },
        "color": {"field": "esito", "type": "nominal"},
    },
}

_HISTOGRAM: dict[str, Any] = {
    "title": "Distribuzione dei valori (dati illustrativi)",
    "data": {"values": [{"v": v} for v in (1.2, 2.4, 2.9, 3.1, 3.8, 4.0, 4.3, 4.7)]},
    "mark": "bar",
    "encoding": {
        "x": {
            "field": "v",
            "type": "quantitative",
            "bin": {"maxbins": 4},
            "scale": {"domain": [0, 8]},
            "axis": {"title": "Valore (unità)"},
        },
        "y": {
            "aggregate": "count",
            "type": "quantitative",
            "scale": {"domain": [0, 4]},
            "axis": {"title": "Frequenza"},
        },
    },
}


def _skip_without_vegalite() -> None:
    pytest.importorskip("jsonschema")
    pytest.importorskip("altair")
    if not REGISTRY["vegalite"].available():  # pragma: no cover - dipendenza assente
        pytest.skip("vl_convert o lo schema Vega-Lite non sono disponibili")


def test_p3_domain_clause_is_written_for_derived_values():
    block = _flat(_figure_block(_p3()))
    assert "`scale.domain` sul valore che si VEDE" in block
    assert '`stack: "normalize"` è [0, 1]' in block
    assert '`"format": ".0%"`' in block
    assert "con `aggregate`, `bin` o `density` contiene il massimo effettivo" in block
    assert "escono dal riquadro" in block


def test_the_normalized_bars_of_the_catalogue_render_a_percent_axis():
    """Lettura letterale del catalogo: `stack: "normalize"` con dominio
    [0, 1] e `format` ".0%" dà un asse 0%…100%."""
    _skip_without_vegalite()
    testi = _svg_texts(_render(_NORMALIZED))
    assert "100%" in testi and "0%" in testi
    assert not any(t.endswith("000%") for t in testi), testi


def test_without_the_domain_clause_the_same_spec_is_valid_and_unreadable():
    """Controprova: il dominio [0, 100] — la lettura naturale di «quota in
    percentuale» — passa `validate(deep=True)` e rende un asse che arriva a
    10000%, con le barre schiacciate nell'1% dell'altezza. Il validatore non
    può accorgersene: la clausola del prompt è l'unica difesa."""
    _skip_without_vegalite()
    sbagliato = copy.deepcopy(_NORMALIZED)
    sbagliato["encoding"]["y"]["scale"]["domain"] = [0, 100]
    del sbagliato["encoding"]["y"]["axis"]["format"]
    ok, error = REGISTRY["vegalite"].validate(json.dumps(sbagliato), deep=True)
    assert ok and error == "", error
    assert "10000%" in _svg_texts(_render(sbagliato))


def test_the_histogram_of_the_catalogue_keeps_the_bars_inside_the_frame():
    """`aggregate: "count"`: il valore dell'asse y non è nei dati scritti dal
    modello. Con il dominio che contiene il massimo effettivo le barre
    restano dentro; con un dominio più corto escono e nessuno se ne accorge
    (`bar` non è fra i mark con `clip` obbligatorio)."""
    _skip_without_vegalite()
    assert _min_path_y(_render(_HISTOGRAM)) >= -1.0
    corto = copy.deepcopy(_HISTOGRAM)
    corto["encoding"]["y"]["scale"]["domain"] = [0, 2]
    ok, error = REGISTRY["vegalite"].validate(json.dumps(corto), deep=True)
    assert ok and error == "", error
    assert _min_path_y(_render(corto)) < -50.0


def test_p3_order_clause_names_sort_for_categories_with_an_order():
    """Senza `sort` Vega-Lite ordina i domini nominali e ordinali in modo
    alfabetico: un orario delle lezioni esce «Giovedì, Lunedì, Martedì…» e
    una torta mostra gli spicchi in ordine di nome invece che di quota."""
    block = _flat(_figure_block(_p3()))
    regola = block[block.index("Senza `sort`") : block.index("CATALOGO VEGA-LITE")]
    assert "ordine ALFABETICO" in regola
    assert "elenco esplicito" in regola
    assert "cronologico o logico" in regola
    assert '`{"field": …, "order": "descending"}`' in regola


def test_p3_legend_rule_keeps_the_only_channel_that_names_the_data():
    """«Legenda solo con più serie» è scritta per i grafici a x/y, dove le
    categorie stanno già sull'asse. Su torta, ciambella e mappa di calore la
    legenda è l'UNICO canale che nomina i dati: senza, restano spicchi
    colorati anonimi."""
    block = _flat(_figure_block(_p3()))
    assert "legenda solo con più serie, ma OBBLIGATORIA quando il colore è" in block
    assert "l'unico canale che nomina i dati (`arc`, `rect`)" in block


def test_a_pie_without_legend_is_valid_but_says_nothing():
    """La misura che motiva la regola: la stessa torta senza legenda passa il
    validatore e rende un solo testo (il titolo), con la legenda ne rende
    sei."""
    _skip_without_vegalite()
    torta: dict[str, Any] = {
        "title": "Ripartizione del monte ore (dati illustrativi)",
        "data": {
            "values": [
                {"componente": "Lezioni", "ore": 48},
                {"componente": "Esercitazioni", "ore": 24},
                {"componente": "Laboratorio", "ore": 16},
            ]
        },
        "mark": {"type": "arc"},
        "encoding": {
            "theta": {"field": "ore", "type": "quantitative", "stack": True},
            "color": {
                "field": "componente",
                "type": "nominal",
                "legend": {"title": "Componente"},
            },
        },
    }
    con_legenda = _svg_texts(_render(torta))
    muta = copy.deepcopy(torta)
    muta["encoding"]["color"]["legend"] = None
    ok, error = REGISTRY["vegalite"].validate(json.dumps(muta), deep=True)
    assert ok and error == "", error
    senza_legenda = _svg_texts(_render(muta))
    assert {"Lezioni", "Esercitazioni", "Laboratorio"} <= set(con_legenda)
    assert not {"Lezioni", "Esercitazioni", "Laboratorio"} & set(senza_legenda)


# ---------------------------------------------------------------------------
# P3 — schema compatto di FunctionFigureSpec (D9)
# ---------------------------------------------------------------------------


def _literal_values(alias: object) -> tuple[str, ...]:
    return typing.get_args(alias)


def test_p3_function_schema_lists_every_field_and_enum():
    schema = content._FUNCTION_SPEC_COMPACT
    assert schema in _p3()
    for name in ff.FunctionFigureSpec.model_fields:
        assert f'"{name}"' in schema, name
    for name in ff.ExpressionSpec.model_fields:
        assert f'"{name}"' in schema, name
    nested = (
        set(ff.TangentAnnotation.model_fields)
        | set(ff.AreaAnnotation.model_fields)
        | set(ff.PointAnnotation.model_fields)
        | set(ff.ParameterSpec.model_fields)
        | set(ff.SamplingSpec.model_fields)
    )
    for name in nested:
        assert f'"{name}"' in schema, name
    for value in _literal_values(ff.FunctionKind):
        assert value in schema, value
    for value in _literal_values(ff.ShowItem):
        assert f'"{value}"' in schema, value
    for kind in ("tangent", "area", "point"):
        assert f'"kind":"{kind}"' in schema or f'"{kind}"' in schema, kind
    prose = _flat(_figure_block(_p3()))
    for fn in FUNCTIONS:
        assert fn in prose, fn
    for text in ("`**` (mai `^`)", "`2*x` (mai `2x`)", "NON scrivere numeri calcolati"):
        assert text in prose, text


# ---------------------------------------------------------------------------
# P3 — gli esempi passano i validatori del registro
# ---------------------------------------------------------------------------


def test_p3_examples_are_in_the_prompt_and_within_budget():
    prompt = _p3()
    assert len(content._VEGALITE_EXAMPLE) <= 300
    assert len(content._DOT_EXAMPLE) <= 150
    for example in (content._VEGALITE_EXAMPLE, content._DOT_EXAMPLE, content._FUNCTION_EXAMPLE):
        assert example in prompt
    # Esempio Vega-Lite: barre con dati inline, clip e scale.domain.
    assert '"values":[' in content._VEGALITE_EXAMPLE
    assert '"type":"bar","clip":true' in content._VEGALITE_EXAMPLE
    assert '"scale":{"domain":[' in content._VEGALITE_EXAMPLE
    assert '"axis":{"title":"Precipitazioni (mm)"}' in content._VEGALITE_EXAMPLE


def test_p3_vegalite_example_passes_schema_and_d5_rules():
    pytest.importorskip("jsonschema")
    pytest.importorskip("altair")
    ok, error = REGISTRY["vegalite"].validate(content._VEGALITE_EXAMPLE)
    assert ok, error


@pytest.mark.skipif(shutil.which("dot") is None, reason="binario graphviz `dot` assente")
def test_p3_dot_example_passes_the_static_gate():
    ok, error = REGISTRY["dot"].validate(content._DOT_EXAMPLE)
    assert ok, error


def test_p3_function_example_is_a_valid_spec():
    spec, issues = ff.parse_function_spec(content._FUNCTION_EXAMPLE)
    assert spec is not None, issues
    assert spec.kind == "function_study"
    assert "zeros" in spec.show and "formula" in spec.show
    assert spec.annotations and spec.annotations[0].kind == "point"


# ---------------------------------------------------------------------------
# P4 e P5 — rinvii testuali senza graffe, divieto di lettura
# ---------------------------------------------------------------------------


def test_p4_refers_to_phase3_formats_without_braces_or_image_prompt():
    for prompt in (
        slides._system_prompt("it"),
        slides._system_prompt("it", minuti_per_lezione=45, livello_eqf="EQF 6"),
    ):
        assert "{" not in prompt and "}" not in prompt
        assert "image_prompt" not in prompt
        flat = _flat(prompt)
        assert "figure Mermaid, Vega-Lite, DOT o `function` e immagini" in flat
        assert "STESSI formati, regole e limiti di Fase 3" in flat
        for fmt in ("`mermaid`", "`vegalite`", "`dot`"):
            assert fmt in flat, fmt
        assert "UNA SLIDE DEDICATA PER OGNI ASSET VISIVO" in prompt
        assert "VINCOLI DI VALIDAZIONE" in prompt
        lingua = _flat(prompt[prompt.index("LINGUA — REGOLA TASSATIVA") :])
        assert "`axis.title`" in lingua and "`label` dei sorgenti DOT" in lingua
        assert "di DOT (ID dei nodi, `->`/`--`" in lingua


def test_p4_new_assets_carry_the_same_catalogue_of_chart_types():
    """Fase 4 crea `new_assets` senza avere in contesto il prompt di Fase
    3: il catalogo va ripetuto in forma breve, altrimenti anche qui il
    modello produce solo barre e linee. Vale il vincolo di composizione:
    nessuna graffa nel prompt di Fase 4."""
    for prompt in (
        slides._system_prompt("it"),
        slides._system_prompt("it", minuti_per_lezione=45, livello_eqf="EQF 6"),
    ):
        assert "{" not in prompt and "}" not in prompt
        flat = _flat(prompt)
        assert "CATALOGO — per `vegalite` scegli il tipo dalla famiglia d'uso:" in flat
        for family in (
            "confronto fra categorie (barre verticali, orizzontali, raggruppate, impilate)",
            "parte sul tutto (barre normalizzate, torta, ciambella)",
            "distribuzione (istogramma, diagramma a scatola, punti impilati, violino)",
            "andamento nel tempo (linea, linea a gradini, area, aree impilate, serie temporale)",
            "correlazione (dispersione, bolle)",
            "matrice (mappa di calore)",
            "incertezza (barre di errore, banda di confidenza)",
            "graduatoria (barre ordinate, bastoncini)",
        ):
            assert family in flat, family
        assert "La torta solo con poche categorie che compongono un intero" in flat
        assert "altrimenti barre ordinate" in flat
        # I tipi Mermaid che la riga dei formati di Fase 4 non nominava.
        for name in ("gantt", "quadrantChart", "sankey-beta", "block-beta"):
            assert name in flat, name
        for name in ("radar-beta", "treemap-beta", "xychart-beta"):
            assert name in flat, name
        # Fase 4 non ha in contesto il prompt di Fase 3, l'altro punto in cui
        # gli esclusi sono scritti: `journey` è la scelta naturale per una
        # slide sul «percorso dello studente», emette `<foreignObject>` e
        # costa un giro di riparazione.
        esclusi = flat[flat.index("esclusi ") :]
        for name in MERMAID_EXCLUDED_TYPES:
            assert name in esclusi, name
        assert "journey" not in flat[: flat.index("esclusi ")]
        # L'ordine delle categorie e l'onestà dei dati valgono anche qui.
        assert "Dichiara `sort`" in flat and "ordine alfabetico" in flat
        assert "Dati illustrativi, non sperimentali" in flat
        assert "`shape` solo quando porta significato" in flat


def test_p5_forbids_reading_sources_aloud():
    prompt = _flat(speech._system_prompt("it", minuti_per_lezione=45))
    assert "MAI leggere codice Mermaid, spec JSON Vega-Lite/function, sorgente DOT" in prompt
