"""PR-1 — registro accademico condiviso nei prompt di Fase 3/4/5.

Test puri (nessun DB): composizione dei system prompt e del blocco
`prompt_register`. Le asserzioni negative sono sulle STRINGHE ORIGINALI
COMPLETE rimosse dai prompt, non su singole parole (che possono
legittimamente comparire dentro le regole del registro).
"""

from __future__ import annotations

import pytest

from app.services import (
    openai_lesson_content_service as content,
)
from app.services import (
    openai_lesson_slides_service as slides,
)
from app.services import (
    openai_lesson_speech_service as speech,
)
from app.services import prompt_register as reg

# Guardie di lunghezza (caratteri) derivate dal testo finale + ~10%.
MAX_BLOCK_CONTENT = 7_800
MAX_BLOCK_REDUCED = 6_400
# Misure dell'8 settembre 2026, dopo il catalogo dei tipi di grafico nel
# blocco «FORMATI DELLE FIGURE» (tabella D8 con i quindici tipi Mermaid,
# catalogo Vega-Lite per famiglia d'uso, criterio della torta, regole D5,
# stile D3, esempi Vega-Lite/DOT, schema compatto ed esempio di
# `function`): P3 26.142 con grounding e 24.690 senza (26.109 con gli
# argomenti di default); P4 14.583 con tutti gli argomenti e 14.653 con i
# default; P5 11.074 e 11.169 con i default. Prima del catalogo erano
# 24.155 / 22.703 (P3) e 13.747 / 13.817 (P4): il catalogo pesa 1.987
# caratteri in P3 e 836 in P4. Il testo dei prompt è statico e
# indipendente dall'ambiente (A19: i quattro formati sono sempre
# descritti, solo l'`enum` dello schema strict segue
# `available_formats()`), quindi le guardie sono deterministiche e restano
# fissate alla misura reale della variante più lunga + ~5%.
#
# Misure del 9 settembre 2026, dopo le correzioni della revisione del
# catalogo (regole di dominio e ordine delle categorie, legenda
# obbligatoria su `arc`/`rect`, onestà dei dati estesa ai quattro
# formati, tipi DOT con `shape`, `data.values` nel blocco LINGUA, tipi
# Mermaid esclusi anche in Fase 4): P3 27.236 con grounding, 25.817
# senza, 27.289 con ruolo/stile/EQF interpolati (la variante più lunga);
# P4 15.100 in entrambe le varianti; P5 invariato. Le correzioni pesano
# 1.127 caratteri in P3 e 447 in P4, quindi la guardia di P3 sale da
# 27.400 a 27.700 (~1,5% di margine sulla misura reale).
#
# Misure del 9 settembre 2026, dopo i dieci tipi di grafo aggiunti
# all'editor DOT (albero di derivazione, tassonomia, grafo delle chiamate,
# grafo pesato, bipartito, topologia di rete, tabella hash, architettura a
# livelli, cammino minimo, rete di flusso): P3 27.517 con grounding,
# 26.098 senza, 27.550 con ruolo/stile/EQF interpolati (la variante più
# lunga); P4 e P5 invariati, il paragrafo DOT sta solo in Fase 3. I tipi
# nuovi sono NOMINATI e non spiegati: il paragrafo passa da 620 a 901
# caratteri, +281 in P3. Con la guardia a 27.700 il margine scendeva a 150
# caratteri (0,5%), troppo poco per la prossima riga: sale a 28.900, cioè
# la misura reale + ~5%.
#
# Misure del 17 settembre 2026, dopo la regola POSIZIONE DEI TAG (D17: un
# tag per asset su riga propria, richiamo a parole, divieto di numerare a
# mano, `[EX:]` nel processo in due fasi): P3 27.890 con grounding, 26.471
# senza, 27.923 con ruolo/stile/EQF interpolati (+373 sulla variante più
# lunga); `REGENERATION_SUFFIX` da 824 a 896. La guardia resta a 28.900 e
# vale anche per la variante di rigenerazione (prompt + suffisso, 28.819:
# prima di D17 era 28.374), che è quella davvero più lunga. P4 e P5 non
# sono toccati (la regola sta solo in Fase 3).
#
# Misure del 18 settembre 2026, dopo la REGOLA DI SCELTA guidata dal
# contenuto (il formato viene dopo aver dichiarato che cosa la figura deve
# far vedere; flowchart come ULTIMA scelta; vincoli di realtà sui numeri;
# regola editoriale di varietà) e la regola di NUMEROSITÀ (la figura segue
# il contenuto sezione per sezione, 4-8 per lezione ordinaria, 0-2 per
# l'introduttiva, al posto del vecchio tetto «1-3 figure per lezione»).
# L'export reale del docente misurava 13 flowchart su 14 figure generate.
# Il tetto «1-3» NON era il vincolo che teneva basso il conteggio — tre
# lezioni ordinarie su quattro ne avevano già quattro, sopra il tetto
# dichiarato: la leva è il criterio sezione per sezione, e l'elenco
# «formati disponibili» non bastava perché apriva su `mermaid` con il
# criterio più largo possibile. P3 29.404 con grounding, 27.985 senza,
# 29.437 con ruolo/stile/EQF interpolati, 30.333 con il suffisso di
# rigenerazione (la variante più lunga), da 27.970 / 26.551 / 28.003 /
# 28.899. Le due regole pesano +1.434 caratteri netti: la scelta +1.227
# lordi meno i 111 recuperati comprimendo l'apertura di VEGA-LITE, la
# numerosità +318. Con la guardia a 28.900 il margine della variante più
# lunga era di UN carattere (28.899 su 28.900): sale a 31.500, cioè la
# misura reale + ~4%, la stessa convenzione delle righe sopra.
# P4 riceve la regola di scelta in forma breve (senza `function`, che la
# Fase 4 non offre al modello per A1: una relazione fra grandezze si
# REFERENZIA da Fase 3, non si ricrea) e la nota che le slide dedicate
# seguono le 4-8 figure di Fase 3 invece delle 5 di prima: 16.246 con i
# default e 16.192 con tutti gli argomenti, da 15.186 e 15.132.
#
# Misure del 20 settembre 2026 (correzione della revisione). Due conti
# erano sbagliati e la guardia di P4 non teneva:
# - la variante DAVVERO più lunga di P4 è quella di RIGENERAZIONE, non
#   quella con i default: `REGENERATION_SUFFIX` (803 caratteri) si
#   concatena al system prompt, e le etichette reali della tassonomia
#   (migrazione 0009: ruolo «Ruoli di supporto e Tutoraggio», EQF
#   «Diploma di licenza conclusiva del I ciclo di istruzione») più il
#   codice lingua più lungo (`zh-cn`) aggiungono il resto. Con la guardia
#   a 16.900 il prompt realmente inviato misurava 17.049 caratteri: la
#   guardia non copriva il caso che si verifica a ogni rigenerazione. P3
#   aveva già il test della variante col suffisso, P4 no: ora ce l'ha;
# - il numero «28.003 per P4» del messaggio di commit precedente è il
#   VECCHIO P3 con ruolo/stile/EQF interpolati (quarto valore della serie
#   qui sopra), non una misura di P4: P4 valeva 16.246.
# Con la riconciliazione fra REALTÀ e ONESTÀ DEI DATI, la scoping dei
# polinomi ausiliari di Vega-Lite e l'elenco Mermaid unificato di P4
# (`classDiagram` compreso): P3 29.777 con grounding, 28.358 senza,
# 30.783 nella variante più lunga (zh-cn, etichette reali, suffisso di
# rigenerazione) — la guardia resta 31.500, margine 2,3%. P4 16.630 con
# i default, 17.433 con il suffisso, 17.474 nella variante più lunga: la
# guardia sale da 16.900 a 18.200, cioè la misura reale + ~4%. P5 non è
# toccato.
MAX_SYSTEM_P3 = 31_500
MAX_SYSTEM_P4 = 18_200
MAX_SYSTEM_P5 = 12_500

# Etichette più lunghe della tassonomia seeddata (migrazione 0009) e
# codice lingua più lungo fra quelli TTS: la variante più lunga dei
# prompt interpolati si misura con questi, non con i default.
LONGEST_ROLE = "Ruoli di supporto e Tutoraggio"
LONGEST_STYLE = "Collaborativo"
LONGEST_EQF = "Diploma di licenza conclusiva del I ciclo di istruzione"
LONGEST_LANGUAGE = "zh-cn"

# Formule più frequenti nel corpus: devono comparire nei prompt SOLO come
# esempi negativi (righe "DA EVITARE:") o dentro la REGOLA 2.
CORPUS_TICS = ("Proprio per questo", "Non basta.", "Punto.")


def _p3(**kwargs: str) -> str:
    return content._system_prompt(
        "it",
        ruolo_docente=kwargs.get("ruolo", "Professore ordinario"),
        stile_insegnamento=kwargs.get("stile", "Frontale"),
        livello_eqf=kwargs.get("eqf", "EQF 6"),
    )


# ---------------------------------------------------------------------------
# Helper `academic_register_block`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flow", ["content", "slides", "speech"])
def test_block_has_core_rules_and_language(flow):
    block = reg.academic_register_block(flow, "en")
    assert block.count(reg.REGISTER_HEADER) == 1
    for rule in ("REGOLA 1 —", "REGOLA 2 —", "REGOLA 3 —"):
        assert rule in block
    assert "nella lingua en," in block
    assert "{language_code}" not in block
    assert "{{" not in block and "}}" not in block
    assert "\\" not in block and "$" not in block
    assert reg.FLOW_ADDENDA[flow] in block
    for other, text in reg.FLOW_ADDENDA.items():
        if other != flow:
            assert text not in block


def test_block_content_has_all_pairs_reduced_has_three():
    full = reg.academic_register_block("content", "it")
    for bad, good in reg.CONTRASTIVE_PAIRS:
        # Le coppie sono impaginate con textwrap: si verifica l'incipit di
        # entrambi i lati, non la stringa intera.
        assert bad.split(" ")[0] in full
        assert good.split(" ")[0] in full
    assert full.count(reg.PAIR_LABEL_BAD) == len(reg.CONTRASTIVE_PAIRS)
    assert full.count(reg.PAIR_LABEL_GOOD) == len(reg.CONTRASTIVE_PAIRS)
    assert len(full) <= MAX_BLOCK_CONTENT

    for flow in ("slides", "speech"):
        reduced = reg.academic_register_block(flow, "it")
        # Lo speech ha una coppia extra dentro l'addendum.
        extra = 1 if flow == "speech" else 0
        assert reduced.count(reg.PAIR_LABEL_BAD) == len(reg.REDUCED_PAIR_INDEXES) + extra
        assert len(reduced) <= MAX_BLOCK_REDUCED
        # La coppia con la correzione per omissione entra anche nella ridotta.
        assert "frase omessa" in reduced


def test_pairs_are_real_corpus_sentences_and_correct_is_justified():
    bad_sentences = [bad for bad, _ in reg.CONTRASTIVE_PAIRS]
    assert any(b.endswith("Punto.") for b in bad_sentences)
    assert any(b.endswith("Anzi.") for b in bad_sentences)
    assert any(b.endswith("Non basta.") for b in bad_sentences)
    assert any("Proprio per questo pericolosi." in b for b in bad_sentences)
    # Almeno una correzione è l'omissione del giudizio, non un allungamento.
    assert any("frase omessa" in good for _bad, good in reg.CONTRASTIVE_PAIRS)
    # "potente" sopravvive se argomentato: la coppia del controesempio
    # non elimina il concetto, lo giustifica.
    controesempio = next(g for b, g in reg.CONTRASTIVE_PAIRS if "controesempio" in b)
    assert "una sola istanza" in controesempio


def test_unknown_flow_raises():
    with pytest.raises(ValueError):
        reg.academic_register_block("video", "it")  # type: ignore[arg-type]


def test_block_is_deterministic():
    assert reg.academic_register_block("content", "it") == reg.academic_register_block(
        "content", "it"
    )


# ---------------------------------------------------------------------------
# P3 — dispense
# ---------------------------------------------------------------------------


def test_p3_block_once_and_in_order():
    prompt = _p3()
    assert prompt.count(reg.REGISTER_HEADER) == 1
    order = [
        prompt.index("REQUISITI — TESTO"),
        prompt.index(reg.REGISTER_HEADER),
        prompt.index("- STILE —"),
        prompt.index("Campione — registro didattico"),
        prompt.index("PROCESSO DI SCRITTURA"),
        prompt.index("VERIFICA FINALE DEL REGISTRO"),
        prompt.index("LINGUA — REGOLA TASSATIVA"),
    ]
    assert order == sorted(order)
    assert len(prompt) <= MAX_SYSTEM_P3


def test_p3_removed_instructions_are_gone_and_sane_ones_stay():
    prompt = _p3()
    removed = (
        "frasi brevissime, anche di poche parole",
        "passaggi che rovesciano la prospettiva",
        "spezza il ritmo delle frasi",
        "potrebbe farlo anche un hacker",
        "Campione A",
        "intuizioni maturate nella pratica",
        "deve aggiungere una prospettiva",
        "Tono di benvenuto, accessibile, motivante",
        "RISCRIVI integralmente",
        "in stile capitolo di manuale",
    )
    for text in removed:
        assert text not in prompt, text
    kept = (
        "Un file system è quella parte",
        "Evita le triadi automatiche",
        "connettivi standard",
        "DELIMITATORI MATH",
        "DIVIETI ASSOLUTI",
        "coverage_check",
        "Output: SOLO JSON valido",
        "\\begin{aligned}",
    )
    for text in kept:
        assert text in prompt, text


def test_p3_interpolates_labels_and_no_residual_placeholders():
    prompt = _p3(ruolo="Ricercatore", stile="Seminariale", eqf="EQF 7")
    assert 'ruolo "Ricercatore"' in prompt
    assert 'stile\n  "Seminariale"' in prompt
    assert "livello EQF EQF 7" in prompt
    for residue in ("{register_block}", "{language_code}", "{ruolo_docente}"):
        assert residue not in prompt
    assert _p3() == _p3()


def test_p3_length_guard_holds_with_and_without_grounding():
    """Le due varianti del kill-switch del grounding restano sotto la
    guardia (quella con grounding è la più lunga)."""
    with_grounding = content._system_prompt("it", grounding_enabled=True)
    without_grounding = content._system_prompt("it", grounding_enabled=False)
    assert len(without_grounding) < len(with_grounding) <= MAX_SYSTEM_P3


def test_p3_regeneration_suffix_carries_register_note():
    suffix = content.REGENERATION_SUFFIX
    assert reg.REGENERATION_REGISTER_NOTE in suffix
    assert "Mantieni stile, lessico e registro coerenti" not in suffix
    composed = _p3() + suffix
    assert composed.index(reg.REGISTER_HEADER) < composed.index("stai RIGENERANDO")


def test_p3_regeneration_variant_stays_under_guard():
    """Il prompt di una rigenerazione (`_system_prompt` + suffisso) è il
    più lungo di Fase 3: la guardia vale anche per lui, in entrambe le
    varianti del grounding."""
    suffix = content.REGENERATION_SUFFIX
    assert len(_p3() + suffix) <= MAX_SYSTEM_P3
    for grounding in (True, False):
        prompt = content._system_prompt("it", grounding_enabled=grounding)
        assert len(prompt + suffix) <= MAX_SYSTEM_P3


def test_assessment_prompt_is_untouched():
    assert reg.REGISTER_HEADER not in content._assessment_system_prompt("it")


# ---------------------------------------------------------------------------
# P4 — slide
# ---------------------------------------------------------------------------


def test_p4_block_once_before_principles_and_labels():
    prompt = slides._system_prompt(
        "it",
        minuti_per_lezione=45,
        livello_eqf="EQF 6",
        ruolo_docente="Professore ordinario",
        stile_insegnamento="Frontale",
    )
    assert prompt.count(reg.REGISTER_HEADER) == 1
    assert prompt.index(reg.REGISTER_HEADER) < prompt.index("PRINCIPI")
    assert 'ruolo\n"Professore ordinario" e stile "Frontale"' in prompt
    assert "evocativo ma chiaro" not in prompt
    assert "descrittivo" in prompt
    assert "UNA SLIDE DEDICATA PER OGNI ASSET VISIVO" in prompt
    assert "VINCOLI DI VALIDAZIONE" in prompt
    assert len(prompt) <= MAX_SYSTEM_P4
    assert reg.REGENERATION_REGISTER_NOTE in slides.REGENERATION_SUFFIX


def test_p4_defaults_are_valid_strings():
    prompt = slides._system_prompt("it")
    assert 'ruolo\n"indicato nel messaggio"' in prompt
    assert "None" not in prompt
    assert len(prompt) <= MAX_SYSTEM_P4
    # I rinvii al messaggio NON sono la variante più lunga: le etichette
    # reali della tassonomia pesano di più (vedi il test qui sotto).
    interpolato = slides._system_prompt(
        LONGEST_LANGUAGE,
        minuti_per_lezione=45,
        livello_eqf=LONGEST_EQF,
        ruolo_docente=LONGEST_ROLE,
        stile_insegnamento=LONGEST_STYLE,
    )
    assert len(interpolato) > len(prompt)


def test_p4_regeneration_variant_stays_under_guard():
    """Il prompt di una rigenerazione (`_system_prompt` + suffisso) è il
    più lungo di Fase 4, come in Fase 3: è quello che parte davvero verso
    il modello ogni volta che il docente rigenera le slide, e la guardia
    vale per lui. Senza questo test la guardia misurava una variante che
    nessuna chiamata invia: con `MAX_SYSTEM_P4` a 16.900 il prompt reale
    di una rigenerazione ne misurava 17.049."""
    suffix = slides.REGENERATION_SUFFIX
    assert len(slides._system_prompt("it") + suffix) <= MAX_SYSTEM_P4
    # Variante più lunga: la si CERCA, non la si sceglie a mano. Lo stile
    # più lungo non è l'etichetta più lunga della tassonomia
    # («Collaborativo», 13 caratteri) ma il ripiego del servizio quando il
    # termine manca o è assente: «indicato nel messaggio» (22) e «(non
    # specificato)» (17), che il `Course` senza `stile_insegnamento_term_id`
    # produce davvero.
    styles = (LONGEST_STYLE, "", "(non specificato)")
    lengths = [
        len(
            slides._system_prompt(
                LONGEST_LANGUAGE,
                livello_eqf=LONGEST_EQF,
                ruolo_docente=LONGEST_ROLE,
                stile_insegnamento=stile,
                **({} if minuti is None else {"minuti_per_lezione": minuti}),
            )
            + suffix
        )
        for minuti in (None, 45, 90)
        for stile in styles
    ]
    assert max(lengths) <= MAX_SYSTEM_P4, max(lengths)


# ---------------------------------------------------------------------------
# P5 — discorso
# ---------------------------------------------------------------------------


def test_p5_block_once_before_tts_rules_and_oral_register():
    prompt = speech._system_prompt("it", minuti_per_lezione=45, ruolo_docente="Prof.")
    assert prompt.count(reg.REGISTER_HEADER) == 1
    assert prompt.index(reg.REGISTER_HEADER) < prompt.index("REGOLE — TTS-FRIENDLY")
    for removed in (
        "domande retoriche occasionali",
        "più narrativo",
        "tono di benvenuto, accogliente",
    ):
        assert removed not in prompt, removed
    for kept in (
        "transizione esplicita",
        "ridondanza controllata",
        "varia la formula",
        "APPLICAZIONE AL PARLATO",
        "Passiamo ora a",
        "TTS-FRIENDLY",
    ):
        assert kept in prompt, kept
    assert len(prompt) <= MAX_SYSTEM_P5
    assert reg.REGENERATION_REGISTER_NOTE in speech.REGENERATION_SUFFIX


def test_p5_defaults_stay_under_guard():
    """Con i default (durata e ruolo rimandati al messaggio) il prompt è
    più lungo della variante con gli argomenti: guardia anche qui."""
    prompt = speech._system_prompt("it")
    assert len(prompt) <= MAX_SYSTEM_P5


# ---------------------------------------------------------------------------
# Trasversale: i tic del corpus compaiono solo come esempi negativi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        _p3(),
        slides._system_prompt("it", minuti_per_lezione=45),
        speech._system_prompt("it", minuti_per_lezione=45),
    ],
    ids=["P3", "P4", "P5"],
)
def test_corpus_tics_only_in_negative_examples(prompt):
    """Ogni riga che contiene un tic del corpus deve stare dentro il lato
    «DA EVITARE» di una coppia (anche nelle righe di continuazione
    impaginate da textwrap) oppure dentro la REGOLA 2."""
    in_rule_2 = prompt[prompt.index("REGOLA 2 —") : prompt.index("REGOLA 3 —")]
    in_bad_side = False
    for line in prompt.splitlines():
        if reg.PAIR_LABEL_BAD in line:
            in_bad_side = True
        elif reg.PAIR_LABEL_GOOD in line:
            in_bad_side = False
        for tic in CORPUS_TICS:
            if tic in line:
                assert in_bad_side or line in in_rule_2, (
                    f"{tic!r} fuori da un esempio negativo: {line!r}"
                )
