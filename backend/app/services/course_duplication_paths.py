"""Single-source-of-truth dei path JSON da tradurre.

Per ogni struttura JSONB (architecture_raw, content_raw, slides_raw,
speech_raw, glossary_raw, document.summary) c'è una tupla `*_PATHS`
che dichiara ESPLICITAMENTE quali foglie sono testo localizzabile.

Sintassi del path:
- `"a"`                  → `obj["a"]` (string)
- `"a.b"`                → `obj["a"]["b"]` (nested string)
- `"a[]"`                → `obj["a"][i]` per ogni `i` (array di stringhe)
- `"a[].b"`              → `obj["a"][i]["b"]` per ogni `i` (field di
                           un oggetto in array)
- `"a[].b[]"`            → idem ma annidando un array di stringhe

Tutti gli altri campi (lesson_id, section_id, asset_id, format,
year, latex, markdown, mermaid code, langauge_code, ecc.) NON vengono
toccati: la struttura JSON resta byte-perfect a parte le foglie nei
path.

Il test `tests/unit/test_course_duplication_paths.py` riconcilia i path
con gli schemi Pydantic e fallisce se nuovi campi `str` appaiono senza
essere classificati esplicitamente.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Course (campi diretti sulla tabella, NON un JSONB)
# ---------------------------------------------------------------------------

COURSE_METADATA_TRANSLATE_FIELDS: tuple[str, ...] = (
    "title",
    "objectives",
    "course_overview",
    "pedagogical_rationale",
)
"""Campi text/string del modello `Course` da tradurre (mantenuti separati
dai JSONB perché non si applica `_translate_jsonb_inplace`)."""

COURSE_METADATA_TRANSLATE_LIST_FIELDS: tuple[str, ...] = (
    "argomenti_chiave",  # JSONB array di stringhe
)


# ---------------------------------------------------------------------------
# CourseModule
# ---------------------------------------------------------------------------

MODULE_TRANSLATE_FIELDS: tuple[str, ...] = (
    "title",
    "description",
)


# ---------------------------------------------------------------------------
# CourseLesson (campi diretti)
# ---------------------------------------------------------------------------

LESSON_TRANSLATE_FIELDS: tuple[str, ...] = (
    "title",
    "summary",
)

LESSON_JSONB_TRANSLATE_PATHS: dict[str, tuple[str, ...]] = {
    # `learning_objectives`: JSONB array di stringhe
    "learning_objectives": ("[]",),
    # `mandatory_topics`: JSONB array di oggetti
    "mandatory_topics": ("[].topic", "[].rationale"),
    # `prerequisites`: JSONB array di stringhe
    "prerequisites": ("[]",),
    # `section_outline`: JSONB array di oggetti
    "section_outline": ("[].title", "[].purpose"),
    # `recommended_bibliography`: JSONB array di oggetti
    # `year` viene incluso anche se è una stringa: OpenAI saprà
    # preservare "2023" → "2023" (test rileverà eventuali drift).
    "recommended_bibliography": (
        "[].authors",
        "[].title",
        "[].publisher",
        "[].year",
        "[].note",
    ),
}


# ---------------------------------------------------------------------------
# Course.architecture_raw → ArchitectureOutput
# ---------------------------------------------------------------------------

ARCHITECTURE_TRANSLATE_PATHS: tuple[str, ...] = (
    "course_overview",
    "pedagogical_rationale",
    "modules[].title",
    "modules[].description",
    "modules[].lessons[].title",
    "modules[].lessons[].summary",
    "modules[].lessons[].recommended_bibliography[].authors",
    "modules[].lessons[].recommended_bibliography[].title",
    "modules[].lessons[].recommended_bibliography[].publisher",
    "modules[].lessons[].recommended_bibliography[].year",
    "modules[].lessons[].recommended_bibliography[].note",
)


# ---------------------------------------------------------------------------
# CourseLesson.content_raw → LessonContentOutput (lezione didattica)
# ---------------------------------------------------------------------------

CONTENT_RAW_TRANSLATE_PATHS: tuple[str, ...] = (
    "lesson_title",
    "introduction",
    "sections[].title",
    "sections[].content",
    "summary",
    "key_takeaways[]",
    "visual_assets[].caption",
    "visual_assets[].alt_text",
    # visual_assets[].content NON tradotto: è codice Mermaid o path
    # dell'immagine — preservare AS-IS.
    "tables[].caption",
    # tables[].markdown NON tradotto: rovinerebbe la struttura.
    "equations[].label",
    "equations[].explanation",
    "equations[].statement",
    "equations[].proof[].text",
    # equations[].latex, .kind, .proof[].latex NON tradotti: codice LaTeX /
    # identificatore di tipo → preservati AS-IS.
    "examples[].title",
    "examples[].content",
    "references[].citation",
    "coverage_check.objectives_covered[].objective",
)


# ---------------------------------------------------------------------------
# CourseLesson.content_raw → LessonAssessmentOutput (verifica competenze)
# ---------------------------------------------------------------------------

ASSESSMENT_RAW_TRANSLATE_PATHS: tuple[str, ...] = (
    "lesson_title",
    "multiple_choice_questions[].text",
    "multiple_choice_questions[].options[].text",
    "open_questions[].text",
    "open_questions[].expected_answer",
)


# ---------------------------------------------------------------------------
# CourseLesson.slides_raw → LessonSlidesOutput
# ---------------------------------------------------------------------------

SLIDES_RAW_TRANSLATE_PATHS: tuple[str, ...] = (
    "slides[].title",
    "slides[].body",
    "slides[].bullets[]",
    "new_assets[].caption",
    "new_assets[].alt_text",
    # new_assets[].content NON tradotto: Mermaid/path.
    "new_equations[].label",
    "new_equations[].explanation",
    "new_equations[].statement",
    "new_equations[].proof[].text",
    # new_equations[].latex / .kind / .proof[].latex NON tradotti.
)


# ---------------------------------------------------------------------------
# CourseLesson.speech_raw → LessonSpeechOutput
# ---------------------------------------------------------------------------

SPEECH_RAW_TRANSLATE_PATHS: tuple[str, ...] = (
    "speech_segments[].text",
    "speech_segments[].delivery_notes",
    # speech_segments[].slide_id NON tradotto: è un ID.
    # slide_to_segments_map NON tradotto: solo mapping di ID + numeri.
)


# ---------------------------------------------------------------------------
# Course.glossary_raw → GlossaryOutput
# ---------------------------------------------------------------------------

GLOSSARY_TRANSLATE_PATHS: tuple[str, ...] = (
    "terms[].term",
    "terms[].usage_note",
    # terms[].translation è già una traduzione: andrà AZZERATO nel
    # nuovo corso, non tradotto. Lo gestisce il service esplicitamente.
)


# ---------------------------------------------------------------------------
# CourseDocument.summary → DocumentSummaryOut
# ---------------------------------------------------------------------------

DOCUMENT_SUMMARY_TRANSLATE_PATHS: tuple[str, ...] = (
    "source_title",
    "abstract",
    "structure_outline[]",
    "key_concepts[].name",
    "key_concepts[].explanation",
    "definitions[].term",
    "definitions[].definition",
    "examples_or_cases[].title",
    "examples_or_cases[].synthesis",
    "formulas_or_rules[].label",
    "formulas_or_rules[].meaning",
    # formulas_or_rules[].latex_or_text NON tradotto: spesso è LaTeX.
    "authors_and_references[].value",
    "didactic_relevance_tags[]",
    # detected_language NON tradotto: codice ISO.
)
