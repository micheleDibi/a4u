"""WP3 — le quattro famiglie di figure nei prompt di Fase 3/4/5.

Test puri (nessun DB, nessuna rete): il blocco «FORMATI DELLE FIGURE» di
P3 elenca i tipi Mermaid ammessi ed esclusi (D8), le regole D5, lo schema
compatto di `FunctionFigureSpec` (D9) e tre esempi minimi che devono
superare i validatori reali del registro; P4 rinvia a Fase 3 senza
graffe; P5 vieta la lettura a voce delle sorgenti. Il testo dei prompt è
statico (A19): nessun test dipende da `available_formats()`.

WP5 (D17): la regola POSIZIONE DEI TAG di P3 (un tag per asset su riga
propria, richiamo a parole), i rimandi da DIVIETI e dal suffisso di
rigenerazione, e l'assenza della regola da P4.

Monocultura e numerosità (18 settembre 2026, doc 17 § 22): la REGOLA DI
SCELTA guidata dal contenuto (con il flowchart come ultima scelta e
l'ordine di lettura come parte della regola), i blocchi REALTÀ e
VARIETÀ, la regola di numerosità che sostituisce il tetto «1-3 figure
per lezione», e il budget delle slide di Fase 4 ritarato su 4-8 figure —
con la controprova lato codice sul range di `_expected_slide_range`.
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
from app.services import course_lesson_slides_service as slides_svc
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
    block = _flat(_figure_block(_p3()))
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
    # Ogni tipo è RAGGIUNTO dal proprio criterio: si legge «contenuto →
    # tipo», non «tipo (contenuto)». L'ordine conta quanto le parole — è il
    # senso della freccia che decide da dove parte il modello.
    for criterion in (
        "processo con passi ORDINATI, dove l'ordine è il contenuto → `mermaid` flowchart",
        "interazione fra attori nel tempo → `mermaid` sequenceDiagram",
        "stati e transizioni → stateDiagram-v2",
        "entità e cardinalità → erDiagram",
        "classi e relazioni → classDiagram",
        "scomposizione di un tema → mindmap",
        "cronologia → timeline",
        "pianificazione e dipendenze temporali → gantt",
        "architettura a blocchi e livelli → block-beta",
        "flusso che si ripartisce fra stadi → sankey-beta",
        "posizionamento su due criteri → quadrantChart",
        "profilo su più criteri, etichette brevi → radar-beta",
        "gerarchia con quantità confrontabili → treemap-beta",
        "ripartizione a poche voci → pie",
        "serie breve su assi, senza pretesa quantitativa → xychart-beta",
    ):
        assert criterion in table, criterion


def test_p3_selection_rule_puts_the_content_first_and_the_flowchart_last():
    """Il difetto misurato il 18 settembre 2026 sull'export reale (13
    flowchart su 14 figure generate, zero `vegalite`, zero `dot`) non
    nasceva da un catalogo mancante — c'era già — ma dall'ORDINE del
    ragionamento: il blocco apriva su `mermaid` con il criterio più largo
    possibile («struttura, processo o relazione qualitativa»), e il primo
    tipo nominato era il flowchart. Qui si verifica la forma nuova: prima
    si dichiara che cosa la figura deve far vedere, il flowchart è
    l'ultima scelta e i formati non-Mermaid vengono PRIMA di lui."""
    block = _figure_block(_p3())
    table = _flat(block[: block.index("MERMAID 11.")])
    assert "REGOLA DI SCELTA — per OGNI figura decidi prima CHE COSA deve far vedere" in table
    assert "non partire dal formato che sai già scrivere" in table
    assert "Il flowchart è l'ULTIMA scelta, non la prima" in table
    assert "un elenco di concetti collegati da frecce NON è un processo" in table
    # I tre formati che il modello non sceglieva mai stanno prima del
    # flowchart: l'ordine di lettura è parte della regola.
    for fmt in ("`function`", "`vegalite`", "`dot`"):
        assert table.index(fmt) < table.index("`mermaid` flowchart"), fmt
    # La vecchia apertura, che apriva su Mermaid, non c'è più.
    assert "struttura, processo o relazione qualitativa → `mermaid`" not in table


def test_p3_selection_rule_binds_each_format_to_a_discipline_example():
    """Un criterio senza esempio resta astratto. La riga `function` è la
    più importante: in una lezione sui limiti il modello aveva prodotto un
    flowchart di «successioni che convergono» invece dei grafici che il
    contenuto chiedeva."""
    table = _flat(_figure_block(_p3()))
    table = table[: table.index("MERMAID 11.")]
    funzione = table[table.index("→ `function`") : table.index("→ `vegalite`")]
    for esempio in ("sin(1/x) vicino a zero", "x**2", "asintoti", "l'area fra due curve"):
        assert esempio in funzione, esempio
    assert "tangente" in table and "curve di livello" in table
    dot = table[table.index("→ `dot`") : table.index("→ `mermaid` flowchart")]
    for esempio in ("automa a stati finiti", "albero di derivazione", "topologia di una rete"):
        assert esempio in dot, esempio


def test_p3_reality_and_variety_rules_are_stated_with_the_same_force():
    """I vincoli di realtà valgono quanto la regola di scelta: senza, la
    spinta alla varietà diventa una spinta a INVENTARE dati per poter
    disegnare un Vega-Lite. La varietà è una regola editoriale, non un
    obbligo cieco: «se il contenuto lo consente»."""
    table = _flat(_figure_block(_p3()))
    realta = table[table.index("REALTÀ —") : table.index("VARIETÀ")]
    assert "mai inventare numeri per avere un grafico" in realta
    assert "dati che stanno nei documenti o notori e verificabili nel testo" in realta
    assert "Mai una figura decorativa" in realta
    assert "se il contenuto non chiede una figura, non la fai" in realta
    # Il freno alla figura inventata NON è un numero: un tetto in prosa
    # («meglio tre figure giuste che sei riempitive») rimetterebbe in
    # piedi, dentro il blocco REALTÀ, il tappo che REQUISITI ha appena
    # tolto. Il freno è sulla verità della figura, non sul conteggio.
    assert "meglio una figura in meno che una inventata" in realta
    assert "tre figure" not in realta
    varieta = table[table.index("VARIETÀ") : table.index("Niente prompt per immagini")]
    assert "regola editoriale, non obbligo cieco" in varieta
    assert "in una lezione con almeno tre figure" in varieta
    assert "se il contenuto lo consente, non più di due flowchart" in varieta
    assert "In matematica, fisica e ingegneria" in varieta
    assert "valuta esplicitamente una figura `function`" in varieta


# ---------------------------------------------------------------------------
# P3 — NUMEROSITÀ (quante figure, non solo quali)
# ---------------------------------------------------------------------------


def _requirements_block(prompt: str) -> str:
    """«REQUISITI — ASSET VISIVI» fino alla regola dei tag. Il marcatore di
    coda è cercato DOPO l'inizio del blocco: «POSIZIONE DEI TAG» è citata
    anche nei DIVIETI, trenta righe più in alto."""
    start = prompt.index("REQUISITI — ASSET VISIVI")
    return _flat(prompt[start : prompt.index("POSIZIONE DEI TAG — REGOLA RIGIDA", start)])


def test_p3_figure_count_follows_the_content_section_by_section():
    """Il secondo difetto misurato il 18 settembre 2026, accanto alla
    monocultura: la NUMEROSITÀ. Sull'export reale tre lezioni su quattro
    avevano esattamente quattro figure, e M2.L2 ne aveva 14 solo perché il
    docente ne aveva inserite a mano 12 dal catalogo. Il vecchio «1-3
    figure per lezione» non era il vincolo che le teneva basse — tre
    lezioni ordinarie su quattro stavano GIÀ sopra quel tetto: mancava il
    criterio che lega la figura al contenuto. Qui si verifica la regola
    nuova — il criterio è la sezione, non un tetto per lezione."""
    block = _requirements_block(_p3())
    assert "la figura segue il contenuto, sezione per sezione" in block
    assert "merita la SUA figura" in block
    for innesco in ("una struttura", "un andamento", "una relazione fra grandezze", "un processo"):
        assert innesco in block, innesco
    # L'ordine di grandezza c'è, ma come indicazione, non come tetto.
    assert "Indicativamente 4-8 per lezione ordinaria, 0-2 per la lezione introduttiva" in block
    # Il vecchio tappo non c'è più in NESSUNA variante del prompt.
    for prompt in (_p3(), _p3(grounding_enabled=False), content._system_prompt("en")):
        assert "1-3 figure per lezione" not in prompt


def test_p3_figure_count_is_not_a_quota_to_fill():
    """La numerosità senza freno è peggio della monocultura: produce
    figure inventate. La regola porta il proprio antidoto, nella stessa
    voce, così il modello non deve cercarlo trenta righe più in basso."""
    block = _requirements_block(_p3())
    assert "Non è una quota da riempire" in block
    assert "non inventare contenuto per arrivare al numero" in block
    assert "una sezione puramente discorsiva resta senza figura" in block


def test_p4_slide_budget_accounts_for_the_new_figure_count():
    """Una figura per slide (punto 2): con 4-8 figure di Fase 3 il conteggio
    delle slide deve crescere, altrimenti il modello sceglie fra stare nel
    range e dare una slide a ogni figura, e scarta le figure. Il codice lo
    sa già (`course_lesson_slides_service` somma gli asset al tetto): qui
    si verifica che lo dica anche il prompt, con l'ordine di grandezza
    giusto."""
    for prompt in (
        slides._system_prompt("it"),
        slides._system_prompt("it", minuti_per_lezione=45, livello_eqf="EQF 6"),
    ):
        flat = _flat(prompt)
        assert "una lezione ordinaria ne porta 4-8 di Fase 3" in flat
        assert "con 8 asset visivi e 2 tabelle il totale cresce di ~10 slide" in flat
        assert "Il tetto della durata NON è un motivo per saltare un asset" in flat
        assert "ogni figura di Fase 3 ha la sua slide, sempre" in flat
        # L'esempio vecchio, tarato sul tetto 1-3, non c'è più.
        assert "con 5 asset visivi e 2 tabelle" not in flat


# Linee guida del prompt (§ NUMERO DI SLIDE), estremo alto di
# contenuto + struttura, prima di qualunque slide dedicata agli asset.
GUIDELINE_HIGH = {15: 10, 30: 15, 45: 23, 60: 30, 90: 42}


@pytest.mark.parametrize("minuti", sorted(GUIDELINE_HIGH))
def test_slide_range_has_room_for_eight_dedicated_figure_slides(minuti: int):
    """Controprova lato codice della riga di prompt sopra.
    `materialize_lesson_slides` somma al tetto una slide per asset visivo,
    una per tabella e una per `new_asset`. Se quel tetto non bastasse, una
    lezione con otto figure uscirebbe dal range e il modello imparerebbe a
    scartarne qualcuna per stare nei numeri — esattamente il tappo che la
    regola di numerosità toglie. Qui: slide di contenuto e struttura al
    massimo delle linee guida, più una slide per ciascuna delle otto figure
    e delle due tabelle, dentro il range allargato e mai vicino al
    fallimento duro (oltre il doppio del tetto)."""
    slide_asset = 8 + 2
    low, high = slides_svc._expected_slide_range(minuti)
    allargato = high + slide_asset  # come in `materialize_lesson_slides`
    realistico = GUIDELINE_HIGH[minuti] + slide_asset
    assert low <= realistico <= allargato, (minuti, realistico, low, allargato)
    assert realistico < allargato * 2


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
    assert "profilo su più criteri, etichette brevi → radar-beta" in table
    assert "gerarchia con quantità confrontabili → treemap-beta" in table


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


def test_p3_illustrative_label_is_not_a_licence_to_invent_numbers():
    """`REALTÀ` vieta di inventare numeri per avere un grafico; otto righe
    dopo, la clausola dell'onestà offriva l'alternativa: dichiarare la
    fonte OPPURE chiudere con «Dati illustrativi, non sperimentali». Letta
    da un modello, la seconda è il permesso esplicito di produrre numeri
    non documentali purché etichettati, cioè il contrario della prima — ed
    è proprio la regola che decide se oserà una `vegalite`, il formato che
    nell'export non compariva mai. La chiusa ora etichetta i soli valori
    schematici e rimanda a `REALTÀ`."""
    block = _flat(_figure_block(_p3()))
    clausola = block[block.index("ONESTÀ DEI DATI") : block.index("MERMAID 11.")]
    assert "non autorizza a inventare dati (vale il blocco REALTÀ)" in clausola
    assert "etichetta i soli valori schematici, che non affermano una misura" in clausola
    assert "Numeri che sembrano misurati e non lo sono non si scrivono" in clausola
    # L'alternativa alla fonte, che era il permesso, non c'è più.
    assert "oppure la chiude con «Dati illustrativi" not in clausola


def test_p3_auxiliary_polynomials_do_not_contradict_the_function_rule():
    """«rette e polinomi ausiliari con `data.sequence`» e «le potenze su
    una `sequence` NON si tracciano in Vega-Lite» si annullavano a
    quarantasei righe di distanza: un polinomio è una potenza. L'ausilio è
    ora dichiarato per quello che è — un livello sopra i dati — e la riga
    delle funzioni dice fin dove arriva."""
    block = _flat(_figure_block(_p3()))
    apertura = block[block.index("VEGA-LITE (spec JSON") : block.index("Dati inline")]
    assert "per i DATI" in apertura
    assert "rette di tendenza e polinomi ausiliari SOVRAPPOSTI ai dati" in apertura
    assert "mai come figura a sé" in apertura
    funzioni = block[block.index("Le FUNZIONI MATEMATICHE") :]
    assert "NON si tracciano in Vega-Lite: usa `function`" in funzioni
    assert "qui restano solo come livello ausiliario su un grafico di dati" in funzioni


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


def _tag_rule(prompt: str) -> str:
    return _flat(
        prompt[prompt.index("POSIZIONE DEI TAG — REGOLA RIGIDA") : prompt.index("FORMATI DELLE")]
    )


def test_p3_tag_position_rule_places_each_tag_once_on_its_own_line():
    """D17: il tag sta su una riga propria fra righe vuote, UNA volta per
    asset, e nel testo l'asset si richiama a parole. È la forma che il
    renderer rende come blocco dopo il paragrafo senza toccare la frase
    (`test_lesson_pdf_figures.py`, forma insegnata dal prompt). Mai tag nei
    campi in cui il PDF non li sostituisce (esempi, tabelle)."""
    prompt = _p3()
    assert prompt.count("POSIZIONE DEI TAG — REGOLA RIGIDA") == 1
    rule = _tag_rule(prompt)
    for text in (
        "Per ogni asset: id stabile e UN tag nel testo",
        "Il tag compare UNA sola volta",
        "da solo su una riga propria fra due righe vuote",
        "dopo il paragrafo che introduce l'asset",
        '"come mostra la figura", "nella tabella seguente"',
        "senza ripetere il tag",
        'senza "Figura", "Tabella", "Equazione" o "Esempio" davanti al tag',
        "Mai tag in codice, formule, `caption`, `key_takeaways`, `references`, "
        "`examples[].content` o `tables[].markdown`",
    ):
        assert text in rule, text


def test_p3_every_asset_kind_has_its_tag_and_the_old_minimum_is_gone():
    """I quattro kind validati in materializzazione (FIG/TAB/EQ/EX) sono
    nominati con il campo id del proprio array dentro la regola, che vale
    quindi per figure, tabelle, equazioni ed esempi; la vecchia formula
    «referenziato almeno una volta» ammetteva le ripetizioni."""
    prompt = _p3()
    rule = _tag_rule(prompt)
    for tag in ("[FIG:asset_id]", "[TAB:table_id]", "[EQ:equation_id]", "[EX:example_id]"):
        assert tag in rule, tag
    assert "referenziato almeno una volta" not in _flat(prompt)
    assert "tag asset ([FIG:], [EQ:], [TAB:], [EX:])" in _flat(prompt)
    # La didascalia resta regolata da DIVIETI (una sola regola, non due).
    divieti = _flat(prompt[prompt.index("DIVIETI ASSOLUTI") : prompt.index("CASO SPECIALE")])
    assert "devono essere brevi descrizioni semantiche" in divieti


def test_p3_divieti_and_regeneration_point_to_the_position_rule():
    """Il blocco DIVIETI vieta la numerazione a mano e rimanda alla regola;
    il suffisso di rigenerazione chiede di riscrivere i tag di una
    versione precedente che la viola (il modello la rilegge nel prompt)."""
    prompt = _p3()
    divieti = _flat(prompt[prompt.index("DIVIETI ASSOLUTI") : prompt.index("CASO SPECIALE")])
    assert '- NON numerare gli asset ("Figura 2"): vedi POSIZIONE DEI TAG.' in divieti
    suffix = _flat(content.REGENERATION_SUFFIX)
    assert "Tag ripetuti o dentro le frasi: riscrivili secondo POSIZIONE DEI TAG." in suffix


def test_p3_tag_rule_stays_out_of_phase4():
    """D17 tocca solo P3: il margine di P4 (300 caratteri) non lo regge."""
    for prompt in (slides._system_prompt("it"), slides.REGENERATION_SUFFIX):
        assert "POSIZIONE DEI TAG" not in prompt


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


def test_p4_selection_rule_mirrors_phase3_without_offering_function():
    """La stessa regola di scelta di Fase 3, in forma breve, perché anche
    qui il modello ricadeva sul flowchart. Con una differenza obbligata:
    `function` NON è fra i formati offerti in Fase 4 (A1,
    `_SLIDES_EXCLUDED_FORMATS`), quindi la riga sulle relazioni fra
    grandezze rimanda alla figura di Fase 3 da REFERENZIARE invece di
    proporre un formato che lo schema strict rifiuterebbe."""
    for prompt in (
        slides._system_prompt("it"),
        slides._system_prompt("it", minuti_per_lezione=45, livello_eqf="EQF 6"),
    ):
        flat = _flat(prompt)
        assert "SCELTA — decidi prima CHE COSA la figura deve far vedere, poi il formato" in flat
        assert "Il flowchart è l'ULTIMA scelta, non la prima" in flat
        assert "un elenco di concetti collegati da frecce non è un processo" in flat
        assert "Mai inventare numeri per avere un grafico e mai una figura decorativa" in flat
        scelta = flat[flat.index("SCELTA —") : flat.index("CATALOGO —")]
        for criterion in (
            "PRESENTI nel testo della lezione → `vegalite`",
            "rete, automa, albero, gruppi → `dot`",
            "processo con passi ORDINATI → `mermaid` flowchart",
            "interazione fra attori nel tempo → sequenceDiagram",
            "stati e transizioni → stateDiagram-v2",
            "entità e cardinalità → erDiagram",
            "scomposizione di un tema → mindmap",
            "cronologia → timeline",
        ):
            assert criterion in scelta, criterion
        # Nessuna riga «→ `function`»: la Fase 4 non lo offre al modello.
        assert "→ `function`" not in flat
        assert "è una figura `function` di Fase 3: qui la REFERENZI, non la ricrei" in scelta
        assert (
            "function"
            not in slides.build_lesson_slides_json_schema(
                visual_formats=("mermaid", "vegalite", "dot", "function")
            )["schema"]["properties"]["new_assets"]["items"]["properties"]["format"]["enum"]
        )


def test_p4_repeats_the_reconciliation_of_the_illustrative_label():
    """La stessa contraddizione di P3, in P4 stava nello stesso paragrafo:
    la licenza precedeva il divieto di due righe."""
    for prompt in (
        slides._system_prompt("it"),
        slides._system_prompt("it", minuti_per_lezione=45, livello_eqf="EQF 6"),
    ):
        flat = _flat(prompt)
        assert "etichetta i valori schematici, non autorizza numeri inventati" in flat
        assert "Mai inventare numeri per avere un grafico" in flat
        assert "o la chiude con «Dati illustrativi" not in flat


def test_p4_names_every_mermaid_type_once_with_its_own_criterion():
    """L'elenco dei tipi Mermaid di Fase 4 era spezzato in due — sei tipi
    con il criterio accanto dentro `SCELTA`, otto nudi dentro `CATALOGO` —
    e `classDiagram` non stava in nessuno dei due, pur essendo in
    `MERMAID_D8_TYPES`. Un nome senza criterio non viene scelto: è la tesi
    stessa della campagna, e vale anche per la lista breve di Fase 4."""
    for prompt in (
        slides._system_prompt("it"),
        slides._system_prompt("it", minuti_per_lezione=45, livello_eqf="EQF 6"),
    ):
        flat = _flat(prompt)
        scelta = flat[flat.index("SCELTA —") : flat.index("CATALOGO —")]
        for name in MERMAID_D8_TYPES:
            assert name in scelta, name
        assert "classi e relazioni → classDiagram" in scelta
        # Nel catalogo resta il rinvio, non un secondo elenco.
        catalogo = flat[flat.index("CATALOGO —") :]
        rinvio = "Per `mermaid` i tipi ammessi sono quelli elencati sopra, uno per criterio"
        assert rinvio in catalogo
        ammessi = catalogo[: catalogo.index("esclusi ")]
        for name in ("gantt", "quadrantChart", "sankey-beta", "block-beta", "treemap-beta"):
            assert name not in ammessi, name


def test_p5_forbids_reading_sources_aloud():
    prompt = _flat(speech._system_prompt("it", minuti_per_lezione=45))
    assert "MAI leggere codice Mermaid, spec JSON Vega-Lite/function, sorgente DOT" in prompt


def test_the_prompt_does_not_promote_a_format_that_is_switched_off() -> None:
    """La REGOLA DI SCELTA promuove `function`, `vegalite` e `dot`, ma l'enum
    dello schema offre solo i formati accesi (`available_formats`): con un
    kill-switch spento il prompt chiederebbe proprio cio' che il modello non
    puo' produrre. Con tutti i formati accesi il blocco non compare."""
    acceso = content._system_prompt("it")
    assert "NON DISPONIBILI" not in acceso
    spento = content._system_prompt("it", visual_formats=("mermaid", "function"))
    riga = [ln for ln in spento.splitlines() if "NON DISPONIBILI" in ln]
    assert len(riga) == 1
    assert "`vegalite`" in riga[0] and "`dot`" in riga[0]
    assert "`function`" not in riga[0] and "`mermaid`" not in riga[0]
    # Il resto del prompt non cambia: il blocco si aggiunge, non sostituisce.
    assert spento.replace(riga[0] + "\n", "").replace("\n\n\n", "\n\n") != ""
    solo_mermaid = content._system_prompt("it", visual_formats=("mermaid",))
    riga_solo = next(ln for ln in solo_mermaid.splitlines() if "NON DISPONIBILI" in ln)
    assert "`function`" in riga_solo
