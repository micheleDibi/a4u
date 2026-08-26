"""Risoluzione dei riferimenti di contabilità di Fase 3.

Il guasto di produzione: la dispensa veniva scartata perché la sezione
dichiarava l'obiettivo con una variante tipografica del testo di Fase 2.
Qui si fissa il contratto del risolutore — cosa deve risolvere e,
soprattutto, cosa NON deve risolvere (un match sbagliato attaccherebbe la
sezione all'obiettivo di un fratello, in silenzio).
"""

from __future__ import annotations

from app.services import document_citation_guard
from app.services import lesson_coverage_resolver as resolver

# asyncio_mode = "auto" (pyproject): questo file è tutto sincrono.

OBJECTIVES = [
    "Lo studente sarà in grado di riconoscere e utilizzare un linguaggio "
    "clinico appropriato nella descrizione del caso",
    "Lo studente sarà in grado di riconoscere i principali quadri "
    "sintomatologici del manuale diagnostico",
    "Lo studente sarà in grado di distinguere fra colloquio clinico strutturato e non strutturato",
    "Lo studente sarà in grado di applicare i criteri diagnostici a un caso clinico semplice",
]

TOPICS = [
    {"topic_id": "T1", "topic": "Il colloquio clinico", "rationale": "x"},
    {"topic_id": "T2", "topic": "I criteri diagnostici", "rationale": "y"},
]


def _index() -> resolver.ObjectiveIndex:
    return resolver.build_objective_index(OBJECTIVES)


# ---- codici -------------------------------------------------------------


def test_objective_ids_are_one_based_and_match_the_index():
    index = _index()
    assert list(index.ids) == ["O1", "O2", "O3", "O4"]
    assert resolver.objective_prompt_ids(OBJECTIVES) == list(index.ids)


def test_id_reference_resolves_to_canonical_text():
    index = _index()
    for raw, position in (("O1", 0), ("[O2]", 1), ("o3", 2), (" [O4] ", 3)):
        outcome = resolver.resolve_objective(raw, index)
        assert outcome.method == "id"
        assert outcome.value == OBJECTIVES[position]


def test_out_of_range_id_is_unresolved_and_never_falls_back():
    """Un token che è palesemente un codice non va confrontato come prosa."""
    assert resolver.resolve_objective("O9", _index()).value is None


# ---- testo --------------------------------------------------------------


def test_verbatim_text_still_resolves():
    """Retro-compatibilità: ciò che oggi passa continua a passare."""
    outcome = resolver.resolve_objective(OBJECTIVES[1], _index())
    assert outcome.method == "exact"
    assert outcome.value == OBJECTIVES[1]


def test_typographic_variants_resolve():
    """Il guasto reale: accento sciolto, apostrofo tipografico, NBSP,
    punto finale."""
    mangled = (
        "Lo studente sara' in grado di riconoscere e utilizzare\u00a0un "
        "linguaggio clinico appropriato nella descrizione del caso."
    )
    outcome = resolver.resolve_objective(mangled, _index())
    assert outcome.method == "normalized"
    assert outcome.value == OBJECTIVES[0]


def test_truncated_objective_resolves_by_containment():
    outcome = resolver.resolve_objective(OBJECTIVES[0][:80], _index())
    assert outcome.method == "containment"
    assert outcome.value == OBJECTIVES[0]


def test_short_fragment_is_unresolved():
    assert resolver.resolve_objective("linguaggio", _index()).value is None


def test_fragment_shared_by_two_objectives_is_unresolved():
    """Il contenimento richiede un candidato UNICO: senza, vincerebbe il
    primo della lista."""
    index = resolver.build_objective_index(
        [
            "Lo studente sarà in grado di calcolare la trasformata di "
            "Fourier di un segnale periodico",
            "Lo studente sarà in grado di calcolare la trasformata di "
            "Fourier di un segnale non periodico",
        ]
    )
    fragment = "Lo studente sarà in grado di calcolare la trasformata di Fourier"
    assert resolver.resolve_objective(fragment, index).value is None


def test_paraphrase_of_a_sibling_is_never_matched():
    """Nessun ripiego fuzzy: «aperiodico» ha ratio 0.947 su «periodico» e
    0.783 su «non periodico», quindi qualunque soglia sceglierebbe
    l'opposto semantico."""
    index = resolver.build_objective_index(
        [
            "Lo studente sarà in grado di calcolare la trasformata di "
            "Fourier di un segnale periodico",
            "Lo studente sarà in grado di calcolare la trasformata di "
            "Fourier di un segnale non periodico",
        ]
    )
    paraphrase = (
        "Lo studente sarà in grado di calcolare la trasformata di Fourier di un segnale aperiodico"
    )
    assert resolver.resolve_objective(paraphrase, index).value is None


def test_unknown_objective_is_unresolved():
    assert resolver.resolve_objective("Tutt'altro argomento", _index()).value is None


# ---- temi ---------------------------------------------------------------


def test_topic_id_is_case_and_bracket_insensitive():
    index = resolver.build_topic_index(TOPICS)
    for raw in ("T1", "t1", "[T1]", " T1 "):
        assert resolver.resolve_topic(raw, index).value == "T1"


def test_topic_resolves_from_its_title():
    index = resolver.build_topic_index(TOPICS)
    outcome = resolver.resolve_topic("I criteri diagnostici", index)
    assert outcome.method == "topic_text"
    assert outcome.value == "T2"


def test_unknown_topic_is_unresolved_and_never_fuzzy():
    index = resolver.build_topic_index(TOPICS)
    assert resolver.resolve_topic("T9", index).value is None


# ---- liste --------------------------------------------------------------


def test_resolve_objectives_dedups_and_preserves_order():
    resolved, unresolved = resolver.resolve_objectives(["O2", "O2", "O1"], _index())
    assert resolved == [OBJECTIVES[1], OBJECTIVES[0]]
    assert unresolved == []


def test_resolve_objectives_reports_the_unresolvable_ones():
    resolved, unresolved = resolver.resolve_objectives(
        ["O1", "Obiettivo inventato dal modello"], _index()
    )
    assert resolved == [OBJECTIVES[0]]
    assert unresolved == ["Obiettivo inventato dal modello"]


def test_empty_index_resolves_nothing():
    empty = resolver.build_objective_index([])
    assert empty.ids == ()
    assert resolver.resolve_objective("O1", empty).value is None


# ---- forma canonica -----------------------------------------------------


def test_index_collapses_inner_whitespace():
    index = resolver.build_objective_index(["Primo\n  obiettivo  formativo"])
    assert index.objectives == ("Primo obiettivo formativo",)


def test_normalize_agrees_with_the_citation_guard_copy():
    """Guardia alla deriva fra le copie della stessa ricetta."""
    for sample in ("Perché l'idea è «forte»?", "  Ampère-Maxwell  ", "AbC 123"):
        assert resolver.normalize(sample) == document_citation_guard.normalize(sample)
