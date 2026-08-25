"""Test puri (senza DB) per `scripts/measure_register.py`.

L'import `from scripts.measure_register import ...` funziona perché pytest ha
rootdir=backend e `tests/` è un package: `backend/` finisce in sys.path e
`scripts/` è importabile come namespace package (stesso meccanismo di
`tests.course_builders`).
"""

from __future__ import annotations

from scripts.measure_register import (
    SCRIPT_VERSION,
    FlaggedSentence,
    LessonRow,
    TextMetrics,
    aggregate_rows,
    analyze_text,
    extract_texts,
    normalize_prose,
    split_sentences,
)

# Frase "lunga" (>= 25 parole) usata come precedente delle punchline.
LONG = (
    "Quando si analizza il comportamento di una successione di funzioni su un "
    "intervallo chiuso e limitato, occorre distinguere con cura tra convergenza "
    "puntuale e convergenza uniforme, perché le due nozioni hanno conseguenze "
    "molto diverse sulla continuità del limite."
)


def _kinds(metrics: TextMetrics) -> list[str]:
    return [f.kind for f in metrics.flagged]


def _flagged(metrics: TextMetrics, kind: str) -> list[str]:
    return [f.sentence for f in metrics.flagged if f.kind == kind]


# ---------------------------------------------------------------------------
# normalize_prose / split_sentences
# ---------------------------------------------------------------------------


def test_normalize_prose_strips_markdown_math_tags_and_bibliography():
    raw = (
        "## Titolo\n\n"
        "Testo con **grassetto** e `codice` e la formula $$\\int f$$ qui.\n"
        "- Rossi, M. (2019). Un libro. Editore.\n"
        "- Un bullet normale\n"
        "Vedi [FIG:schema_1] e [eq:e1].\n"
    )
    norm = normalize_prose(raw)
    assert "#" not in norm and "**" not in norm and "`" not in norm
    assert "$" not in norm and "MATH" in norm
    assert "[FIG" not in norm and "[eq" not in norm
    assert "Rossi" not in norm  # riga bibliografica scartata
    assert "Un bullet normale" in norm  # bullet non bibliografico conservato
    assert "Titolo" in norm
    assert normalize_prose(None) == "" and normalize_prose("") == ""


def test_split_sentences_protects_abbreviations_decimals_math_and_tags():
    text = (
        "Alcuni autori, es. Newton e Leibniz, usano notazioni diverse, ecc. Il "
        "tasso vale 3.14 per cento. La formula $x^2$ compare in [FIG:abc] e nel "
        "testo. Fine."
    )
    sentences = split_sentences(text)
    # "es. Newton" e "ecc. Il" NON spezzano: la prima frase resta unita.
    assert sentences == [
        "Alcuni autori, es. Newton e Leibniz, usano notazioni diverse, ecc. "
        "Il tasso vale 3.14 per cento.",
        "La formula MATH compare in e nel testo.",
        "Fine.",
    ]
    assert "3.14" in sentences[0]
    assert all("$" not in s and "[FIG" not in s for s in sentences)


def test_split_sentences_handles_quotes_parens_and_newlines():
    text = 'Prima frase. "Seconda frase." (Terza frase.) Quarta frase\nQuinta riga'
    assert split_sentences(text) == [
        "Prima frase.",
        '"Seconda frase."',
        "(Terza frase.)",
        "Quarta frase",
        "Quinta riga",
    ]


# ---------------------------------------------------------------------------
# analyze_text — punchline e formule
# ---------------------------------------------------------------------------


def test_punchline_and_formulas_on_real_sentences():
    text = (
        f"{LONG} Tutto cambia. "
        "Se una funzione non è continua in un punto, non può essere differenziabile "
        "lì. Punto. "
        "La guerra non per questo divenne meno distruttiva. Anzi. "
        "La domanda produce quasi sempre un sì generico o un silenzio prudente. "
        "Non basta. "
        "Questo errore spesso produce numeri plausibili, ed è proprio per questo "
        "pericoloso."
    )
    m = analyze_text(text, language="it")

    # Punchline: solo "Tutto cambia." segue una frase >= 25 parole.
    assert m.punchline_after_long == 1
    assert _flagged(m, "punchline") == ["Tutto cambia."]

    # Formule: tre frasi intere + una sottostringa.
    assert m.formula_hits == 4
    assert _flagged(m, "formula") == [
        "Punto.",
        "Anzi.",
        "Non basta.",
        "Questo errore spesso produce numeri plausibili, ed è proprio per questo pericoloso.",
    ]
    for f in m.flagged:
        assert f.sentence and f.prev  # tutte hanno una frase precedente


def test_punchline_not_counted_when_short_sentence_ends_with_colon():
    m = analyze_text(f"{LONG} In sintesi: la convergenza uniforme conserva la continuità.")
    assert m.punchline_after_long == 0
    m2 = analyze_text(f"{LONG} In sintesi.")
    assert m2.punchline_after_long == 1


def test_custom_formulas_and_punch_thresholds():
    m = analyze_text(
        "Una frase di sei parole esatte qui. Poi la magia.",
        formulas=["Poi la magia."],
        punch_max_words=3,
        long_min_words=6,
    )
    assert m.formula_hits == 1
    assert m.punchline_after_long == 1
    # "magia" come sottostringa NON è più cercata: solo la forma intera.
    assert analyze_text("La magia dei numeri.", formulas=["Poi la magia."]).formula_hits == 0


# ---------------------------------------------------------------------------
# analyze_text — asserzioni valutative
# ---------------------------------------------------------------------------


def test_evaluative_unjustified_without_markers():
    m = analyze_text("Questa interpretazione geometrica è più che elegante.")
    assert m.evaluative_total_a == 1
    assert m.evaluative_unjustified_a == 1
    assert _kinds(m) == ["valutativa_A"]


def test_evaluative_justified_in_same_sentence():
    m = analyze_text(
        "È elegante perché riduce tre casi a uno: la dimostrazione usa una sola disuguaglianza."
    )
    assert m.evaluative_total_a == 1
    assert m.evaluative_unjustified_a == 0
    assert m.flagged == []


def test_evaluative_justified_by_next_sentence():
    m = analyze_text(
        "Il metodo è potente. Permette infatti di trattare il caso generale con un solo lemma."
    )
    assert m.sentences == 2
    assert m.evaluative_total_a == 1
    assert m.evaluative_unjustified_a == 0


def test_evaluative_tier_b_counted_separately_and_math_marker():
    # Tier A giustificato nella stessa frase (':' e MATH); tier B in coda,
    # senza frase successiva che lo sostenga.
    m = analyze_text("La stima è potente: vale $x<1$. Questo lemma è fondamentale.")
    assert m.evaluative_total_a == 1
    assert m.evaluative_unjustified_a == 0
    assert m.evaluative_total_b == 1
    assert m.evaluative_unjustified_b == 1
    assert _kinds(m) == ["valutativa_B"]
    # Invertendo l'ordine, il tier B è sostenuto dalla frase successiva.
    m2 = analyze_text("Questo lemma è fondamentale. La stima è potente: vale $x<1$.")
    assert m2.evaluative_unjustified_b == 0


def test_evaluative_english_patterns():
    m = analyze_text(
        "This proof is elegant. The bound is powerful because it is tight.",
        language="en",
    )
    assert m.evaluative_total_a == 2
    # "elegant" è giustificato dalla frase successiva ("because").
    assert m.evaluative_unjustified_a == 0


# ---------------------------------------------------------------------------
# rhetorical_questions / antithesis_openers / statistiche
# ---------------------------------------------------------------------------


def test_rhetorical_questions_and_antithesis_openers():
    text = (
        "Ma questo non basta a spiegare tutto. Eppure la teoria regge. "
        "Il resto segue? Proprio qui nasce il problema."
    )
    m = analyze_text(text)
    assert m.sentences == 4
    assert m.questions_count == 1
    assert m.rhetorical_questions == 25.0
    assert m.antithesis_count == 3
    assert m.antithesis_openers == 75.0


def test_antithesis_prefixes_require_word_boundary():
    m = analyze_text("Anziché arrendersi, insistette. Mangiare bene aiuta. Non è vero.")
    assert m.antithesis_count == 1  # solo "Non è "


def test_sentence_stats_and_short_ratio():
    m = analyze_text("Uno due tre. Uno due tre quattro cinque sei sette.")
    assert m.words == 10
    assert m.sentences == 2
    assert m.sent_len_mean == 5.0
    assert m.sent_len_std == 2.0
    assert m.short_ratio == 0.5
    single = analyze_text("Solo una frase qui.")
    assert single.sent_len_std == 0.0
    empty = analyze_text("")
    assert empty == TextMetrics()


# ---------------------------------------------------------------------------
# extract_texts
# ---------------------------------------------------------------------------


def test_extract_texts_tolerates_none_and_missing_fields():
    assert extract_texts(None, None, None) == {
        "dispensa": "",
        "slide": "",
        "slide_bullets": "",
        "discorso": "",
    }
    out = extract_texts(
        {"introduction": "Intro.", "sections": [{"content": "Sez."}, {}, None], "examples": None},
        {"slides": [{"title": "T1", "body": "B1", "bullets": ["p1", "p2"]}, {"title": "T2"}, {}]},
        {"speech_segments": [{"text": "Ciao."}, {}, "spazzatura"]},
    )
    assert out["dispensa"] == "Intro.\n\nSez."
    assert out["slide"] == "T1\nB1\n\nT2"
    assert out["slide_bullets"] == "p1\np2"
    assert out["discorso"] == "Ciao."


def test_extract_texts_full_dispensa_order_and_assessment():
    content = {
        "introduction": "I",
        "sections": [{"content": "S1"}, {"content": "S2"}],
        "summary": "R",
        "examples": [{"content": "E"}],
    }
    assert extract_texts(content, None, None)["dispensa"] == "I\n\nS1\n\nS2\n\nR\n\nE"
    assessment = {
        "is_assessment": True,
        "multiple_choice_questions": [{"text": "Q1?", "options": [{"text": "A"}]}],
        "open_questions": [{"text": "Q2?", "expected_answer": "R2"}],
    }
    assert extract_texts(assessment, None, None)["dispensa"] == "Q1?\n\nA\n\nQ2?\n\nR2"


# ---------------------------------------------------------------------------
# Determinismo e serializzazione
# ---------------------------------------------------------------------------


def test_analyze_text_is_deterministic():
    text = f"{LONG} Punto. Questa idea è elegante. Ma perché? Non è così."
    assert analyze_text(text) == analyze_text(text)
    assert analyze_text(text, language="en") == analyze_text(text, language="en")


def test_lesson_row_json_roundtrip_and_aggregate():
    row = LessonRow(
        course_id="c1",
        course="Corso",
        lesson_code="M1.L1",
        lesson_title="Lezione",
        kind="dispensa",
        language="it",
        is_introductory=True,
        is_assessment=False,
        generated_at="2026-08-01T00:00:00+00:00",
        cost_usd=0.5,
        duration_ms=1000,
        reasoning_effort="high",
        cached_tokens=10,
        estimated_word_count=1200,
        metrics=analyze_text(f"{LONG} Punto."),
    )
    data = row.to_json()
    assert data["script_version"] == SCRIPT_VERSION
    assert "flagged" not in data["metrics"] and data["flagged"]
    back = LessonRow.from_json(data)
    assert back == row
    # "Punto." dopo la frase lunga è sia punchline sia formula (due flag).
    assert back.metrics.flagged == [
        FlaggedSentence("punchline", LONG, "Punto."),
        FlaggedSentence("formula", LONG, "Punto."),
    ]

    agg = aggregate_rows([row], group_introductory=True)
    assert len(agg) == 1  # un solo corso: nessuna riga TOTALE
    assert agg[0]["gruppo"] == "intro"
    assert agg[0]["formule_tot"] == 1
    assert agg[0]["formule_1k"] == round(1000 / row.metrics.words, 3)


# ---------------------------------------------------------------------------
# --grounding (PR-2): copertura degli estratti selezionati e references
# ---------------------------------------------------------------------------


def test_grounding_coverage_counts_entries_with_two_stems_in_text():
    from scripts.measure_register import grounding_coverage, references_by_source

    selected = [
        "Teorema di Weierstrass Una funzione continua su un compatto ha massimo",
        "Coefficienti di Fourier Integrali sulle armoniche",
        "Derivata",
    ]
    text = (
        "Il teorema di Weierstrass garantisce il massimo di una funzione continua. "
        "La derivata misura la pendenza."
    )
    # 1ª voce: weier+funzi+conti+massi presenti; 2ª: nessuno; 3ª: unico stem presente.
    assert grounding_coverage(selected, text) == round(2 / 3, 4)
    assert grounding_coverage([], text) is None

    refs = references_by_source(
        {
            "references": [
                {"citation": "A", "source": "documento_caricato"},
                {"citation": "B", "source": "suggerimento_generale"},
                {"citation": "C", "source": "documento_caricato"},
                {"citation": "D", "source": "altro"},
            ]
        }
    )
    assert refs == {"documento_caricato": 2, "suggerimento_generale": 1}
    assert references_by_source(None) == {"documento_caricato": 0, "suggerimento_generale": 0}
