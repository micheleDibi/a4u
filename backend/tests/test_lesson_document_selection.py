"""PR-2 — selezione per lezione degli estratti documentali (grounding P3).

Test puri sul modulo `lesson_document_selection` (documenti e lezione in
memoria) + due test di integrazione su `build_user_prompt` con il
kill-switch acceso/spento.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core import config as config_module
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_content_service as content_svc
from app.services import lesson_document_selection as sel
from app.services import openai_lesson_content_service as openai_content
from app.services.course_architecture_service import (
    _build_documents_context,
    _format_document_summary_for_prompt,
)
from tests.course_builders import (
    build_course,
    build_course_document,
    build_document_summary,
    find_lesson,
)

COURSE_ID = uuid.uuid4()


def _lesson(**overrides) -> CourseLesson:
    base = {
        "lesson_code": "M2.L3",
        "title": "Il teorema di Weierstrass",
        "summary": "Massimi e minimi di funzioni continue su intervalli chiusi.",
        "is_introductory": False,
        "learning_objectives": [
            "Enunciare il teorema di Weierstrass",
            "Applicarlo a funzioni continue su un compatto",
        ],
        "mandatory_topics": [
            {
                "topic_id": "T1",
                "topic": "Teorema di Weierstrass",
                "rationale": "Esistenza di massimo e minimo per funzioni continue",
            },
            {"topic_id": "T2", "topic": "Compattezza di un intervallo chiuso e limitato"},
        ],
        "section_outline": [
            {"section_id": "S1", "title": "Enunciato del teorema", "purpose": "Ipotesi e tesi"},
            {"section_id": "S2", "title": "Controesempi", "purpose": "Quando cade un'ipotesi"},
        ],
    }
    base.update(overrides)
    return CourseLesson(**base)


def _weierstrass_doc(policy: str = "citable", **kw):
    summary = build_document_summary(
        title="Analisi Matematica Uno",
        authors=["Anna Verdi"],
        abstract="Manuale di analisi: limiti, continuità, teorema di Weierstrass, derivate.",
        key_concepts=[
            {
                "name": "Teorema di Weierstrass",
                "explanation": "Una funzione continua su un intervallo chiuso e limitato "
                "ammette massimo e minimo assoluti.",
            },
            {
                "name": "Funzione continua",
                "explanation": "Funzione priva di salti: il limite coincide con il valore.",
            },
        ],
        definitions=[
            {
                "term": "Intervallo compatto",
                "definition": "Intervallo chiuso e limitato della retta reale.",
            },
            {"term": "Derivata", "definition": "Limite del rapporto incrementale."},
        ],
        examples_or_cases=[
            {
                "title": "Controesempio sull'intervallo aperto",
                "synthesis": "La funzione 1/x su (0,1] è continua ma priva di massimo: "
                "cade l'ipotesi di intervallo chiuso e il teorema di Weierstrass non vale.",
            }
        ],
        formulas_or_rules=[
            {
                "label": "Tesi di Weierstrass",
                "latex_or_text": "\\exists x_M, x_m \\in [a,b]: f(x_m) \\le f(x) \\le f(x_M)",
                "meaning": "Esistono punti di massimo e di minimo assoluti sull'intervallo.",
            }
        ],
        didactic_relevance_tags=["continuità", "weierstrass", "compattezza"],
    )
    return build_course_document(
        COURSE_ID,
        filename="analisi_uno.pdf",
        policy=policy,
        summary=summary,
        created_at=kw.get("created_at", datetime(2026, 1, 1, tzinfo=UTC)),
    )


def _fourier_doc(**kw):
    summary = build_document_summary(
        title="Serie di Fourier",
        abstract="Dispense sulle serie di Fourier e sull'analisi armonica.",
        key_concepts=[
            {
                "name": "Coefficienti di Fourier",
                "explanation": "Integrali che proiettano il segnale sulle armoniche.",
            }
        ],
        definitions=[
            {"term": "Armonica", "definition": "Componente sinusoidale di frequenza multipla."}
        ],
        didactic_relevance_tags=["fourier", "armoniche"],
    )
    return build_course_document(
        COURSE_ID,
        filename="fourier.pdf",
        summary=summary,
        created_at=kw.get("created_at", datetime(2026, 1, 2, tzinfo=UTC)),
    )


def _select(docs, lesson=None, **kw):
    return sel.select_documents_context_for_lesson(
        docs,
        lesson or _lesson(),
        total_max_chars=kw.get("total", 40_000),
        per_doc_max_chars=kw.get("per_doc", 12_000),
        course_language="it",
    )


# ---------------------------------------------------------------------------
# Normalizzazione e profilo
# ---------------------------------------------------------------------------


def test_tokenize_and_bigrams():
    assert sel.tokenize("Continuità e differenziabilità") == ["conti", "diffe"]
    assert sel.tokenize("La lezione sui numeri 42") == ["numer"]
    assert "teore weier" in sel.terms("Teorema di Weierstrass")


def test_query_profile_weights_and_topic_title_fallback():
    profile = sel.build_query_profile(_lesson())
    assert profile["weier"] == sel.WEIGHT_TITLE
    assert profile["teore weier"] == sel.WEIGHT_TITLE * sel.BIGRAM_FACTOR
    # `title` come fallback di `topic` (tests/course_builders.py).
    legacy = _lesson(mandatory_topics=[{"topic_id": "T1", "title": "Compattezza"}])
    assert sel.build_query_profile(legacy)["compa"] == sel.WEIGHT_TOPIC


# ---------------------------------------------------------------------------
# Selezione
# ---------------------------------------------------------------------------


def test_relevant_document_gets_examples_and_formulas_other_gets_abstract_only():
    ctx = _select([_fourier_doc(), _weierstrass_doc()])
    text = ctx.text
    assert "### Esempi e casi" in text
    assert "### Formule e regole" in text
    assert "Controesempio sull'intervallo aperto" in text
    assert "Tesi di Weierstrass" in text
    assert "Fonte: Analisi Matematica Uno — Anna Verdi" in text
    # Il documento non pertinente compare solo con l'abstract (compatto).
    assert "## Documento: fourier.pdf" in text
    assert "Coefficienti di Fourier" not in text
    # Il pertinente viene prima del non pertinente.
    assert text.index("analisi_uno.pdf") < text.index("fourier.pdf")
    assert ctx.stats["docs_relevant"] == 1
    assert ctx.stats["entries_selected"] >= 4
    assert ctx.stats["fallback_overview"] is False
    assert not ctx.stats.get("truncated")


def test_deterministic_and_order_invariant():
    a = _select([_weierstrass_doc(), _fourier_doc()]).text
    b = _select([_fourier_doc(), _weierstrass_doc()]).text
    c = _select([_fourier_doc(), _weierstrass_doc()]).text
    assert a == b == c


def test_frequency_damping_prefers_specific_entry_over_long_generic_one():
    generic_expl = " ".join(["funzione continua su un intervallo di numeri reali"] * 12)
    summary = build_document_summary(
        key_concepts=[
            {"name": "Funzione continua su intervallo", "explanation": generic_expl},
            {
                "name": "Weierstrass",
                "explanation": "Esistenza di massimo e minimo su un compatto.",
            },
        ]
        + [
            {"name": f"Funzione continua {i}", "explanation": "Funzione continua reale."}
            for i in range(12)
        ],
        definitions=[],
        structure_outline=[],
    )
    doc = build_course_document(COURSE_ID, filename="corso.pdf", summary=summary)
    entries = sel._entries_from_summary(doc.summary, reserved=False)
    df = sel.compute_document_frequency(entries)
    profile = sel.build_query_profile(_lesson())
    scored = sel.score_summary_entries(entries, profile, df, len(entries))
    names = [s.entry.match_text.split(" ")[0] for s in scored]
    assert names and names[0] == "Weierstrass"


def test_total_and_per_doc_budgets_are_respected_without_broken_entries():
    ctx = _select([_weierstrass_doc(), _fourier_doc()], total=2_200, per_doc=1_000)
    assert len(ctx.text) <= 2_200
    for line in ctx.text.splitlines():
        if line.startswith("- **"):
            assert line.rstrip().endswith((".", "…", "`", "»", ")")), line


def test_formula_is_never_truncated_only_skipped():
    long_latex = "x" * 900
    summary = build_document_summary(
        formulas_or_rules=[
            {"label": "Weierstrass", "latex_or_text": long_latex, "meaning": "Tesi."}
        ]
    )
    doc = build_course_document(COURSE_ID, filename="f.pdf", summary=summary)
    # Con un solo documento rilevante il budget estratti è l'intero residuo:
    # 1.000 - identità (~150) < ~930 caratteri della voce → la voce si salta.
    ctx = _select([doc], total=1_000, per_doc=800)
    assert long_latex not in ctx.text
    assert "x" * 200 not in ctx.text  # nessuna formula spezzata
    assert "### Abstract" in ctx.text


def test_reserved_document_stays_anonymous_and_after_framing():
    reserved = _weierstrass_doc(policy="content_only")
    ctx = _select([_fourier_doc(), reserved])
    text = ctx.text
    assert "## Materiale di contesto 1" in text
    assert "non citabile" in text
    for identity in ("analisi_uno", "Analisi Matematica Uno", "Anna Verdi"):
        assert identity not in text
    assert text.index("non citabile") < text.index("Materiale di contesto 1")
    # Gli estratti del riservato entrano comunque (contenuto usato, non citato).
    assert "Tesi di Weierstrass" in text
    assert ctx.stats["docs_reserved"] == 1


def test_excluded_document_is_absent():
    ctx = _select([_weierstrass_doc(policy="excluded"), _fourier_doc()])
    assert "analisi_uno" not in ctx.text and "Weierstrass" not in ctx.text
    assert ctx.stats["docs_ready"] == 1


def test_fallback_overview_when_nothing_matches():
    lesson = _lesson(
        title="Storia del Risorgimento",
        summary="Unità d'Italia",
        learning_objectives=["Descrivere le fasi dell'unificazione"],
        mandatory_topics=[{"topic_id": "T1", "topic": "Cavour e Garibaldi"}],
        section_outline=[{"section_id": "S1", "title": "Le guerre d'indipendenza"}],
    )
    ctx = _select([_weierstrass_doc(), _fourier_doc()], lesson=lesson)
    assert ctx.text.startswith(sel.OVERVIEW_NOTE)
    assert ctx.stats["fallback_overview"] is True
    assert "### Concetti chiave" in ctx.text
    assert "### Esempi e casi" not in ctx.text


def test_introductory_lesson_gets_overview_with_structure_and_fonte():
    lesson = _lesson(is_introductory=True)
    ctx = _select([_weierstrass_doc()], lesson=lesson)
    assert "### Struttura" in ctx.text
    assert "Fonte: Analisi Matematica Uno" in ctx.text
    assert "### Esempi e casi" not in ctx.text
    assert ctx.stats["introductory"] is True
    assert ctx.stats["fallback_overview"] is False
    assert sel.OVERVIEW_NOTE not in ctx.text


def test_no_ready_documents():
    doc = _weierstrass_doc()
    doc.summary_status = "pending"
    ctx = _select([doc])
    assert ctx.text == sel.NO_DOCS_TEXT


def test_language_mismatch_is_annotated():
    doc = _fourier_doc()
    doc.summary["detected_language"] = "en"
    ctx = _select([doc, _weierstrass_doc()])
    assert sel.LANGUAGE_NOTE in ctx.text


def test_shared_formatter_unchanged_no_examples_section():
    doc = _weierstrass_doc()
    legacy = _format_document_summary_for_prompt(doc, 10_000)
    assert legacy is not None
    assert "### Esempi" not in legacy and "### Formule" not in legacy
    assert legacy.startswith("## Documento: analisi_uno.pdf\nLingua rilevata: it\nFonte: ")


# ---------------------------------------------------------------------------
# Integrazione con build_user_prompt (kill-switch)
# ---------------------------------------------------------------------------


def _patched_settings(monkeypatch, **overrides):
    base = config_module.get_settings()
    settings = base.model_copy(update=overrides)
    monkeypatch.setattr(content_svc, "get_settings", lambda: settings)
    monkeypatch.setattr(openai_content, "get_settings", lambda: settings)
    return settings


async def test_user_prompt_grounding_on_order_and_task_lines(seeded_db, monkeypatch):
    _patched_settings(monkeypatch, course_lesson_content_documents_selection_enabled=True)
    course_id, _org, _user = await build_course(seeded_db)
    doc = _weierstrass_doc()
    doc.course_id = course_id
    seeded_db.add(doc)
    await seeded_db.commit()
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    # Profilo lessicale realistico (build_course crea una lezione generica).
    lesson.title = "Il teorema di Weierstrass"
    lesson.mandatory_topics = [
        {
            "topic_id": "T1",
            "topic": "Teorema di Weierstrass",
            "rationale": "Esistenza di massimo e minimo assoluti su un intervallo "
            "chiuso e limitato",
        }
    ]
    lesson.section_outline = [
        {"section_id": "S1", "title": "Enunciato e tesi", "purpose": "Ipotesi e tesi"}
    ]

    prompt = content_svc.build_user_prompt(course, lesson)
    i_gloss = prompt.index("## Glossario del corso")
    i_lesson = prompt.index("## Lezione da generare")
    i_docs = prompt.index("## Documenti di riferimento (estratti selezionati per questa lezione)")
    i_task = prompt.index("## Compito")
    assert i_gloss < i_lesson < i_docs < i_task
    assert "registralo in `references` come `suggerimento_generale`" in prompt
    assert "Tesi di Weierstrass" in prompt


async def test_user_prompt_grounding_off_is_historical(seeded_db, monkeypatch):
    settings = _patched_settings(
        monkeypatch, course_lesson_content_documents_selection_enabled=False
    )
    course_id, _org, _user = await build_course(seeded_db)
    doc = _weierstrass_doc()
    doc.course_id = course_id
    seeded_db.add(doc)
    await seeded_db.commit()
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")

    prompt = content_svc.build_user_prompt(course, lesson)
    i_lesson = prompt.index("## Lezione da generare")
    i_docs = prompt.index("## Documenti di riferimento (estratti rilevanti)")
    i_gloss = prompt.index("## Glossario del corso")
    assert i_lesson < i_docs < i_gloss
    assert "suggerimento_generale" not in prompt
    legacy = _build_documents_context(
        list(course.documents), settings.course_lesson_content_documents_context_max_chars
    )
    assert legacy in prompt
    assert "### Esempi e casi" not in prompt


def test_system_prompt_grounding_blocks_follow_the_switch():
    on = openai_content._system_prompt("it", grounding_enabled=True)
    off = openai_content._system_prompt("it", grounding_enabled=False)
    assert "FONTI E ANCORAGGIO — REGOLA FORTE" in on
    assert on.index("FONTI E ANCORAGGIO") < on.index("REQUISITI — TESTO")
    assert "\nRIFERIMENTI\n" not in on
    assert "seguono la REGOLA 1 del REGISTRO" in on
    assert "FONTI E ANCORAGGIO" not in off
    assert "\nRIFERIMENTI\n" in off
