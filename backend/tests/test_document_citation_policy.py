from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.schemas.course_architecture import (
    ArchitectureLesson,
    ArchitectureModule,
    ArchitectureOutput,
    RecommendedBibliographyItem,
)
from app.services import course_service
from app.services import document_citation_guard as guard
from app.services.course_architecture_service import (
    _build_documents_context,
    filter_reserved_bibliography,
)
from app.services.course_lesson_content_service import (
    _format_recommended_bibliography,
    load_course_full,
)
from tests.course_builders import (
    build_course,
    build_course_document,
    build_document_summary,
    find_lesson,
)

pytestmark = pytest.mark.asyncio

# Helper condivisi con tests/test_lesson_document_selection.py.
_summary = build_document_summary
_doc = build_course_document


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


async def test_matcher_containment_ratio_and_paper_stem():
    docs = [
        _doc(
            uuid.uuid4(),
            filename="rossi_2021_manuale_di_termodinamica_ab12cd.pdf",
            policy="content_only",
            summary=_summary(
                title="Manuale di Termodinamica Applicata",
                authors=["Mario Rossi"],
            ),
        )
    ]
    index = guard.build_identity_index(docs)
    assert index and index[0].titles
    # Containment sul source_title.
    assert guard.match_reserved(
        "M. Rossi, Manuale di Termodinamica Applicata, 2021", index
    )
    # Stem del filename paper (senza suffisso uuid, _ → spazio).
    assert guard.match_reserved("manuale di termodinamica", index)
    # Ratio + cognome autore (titolo abbreviato/parafrasato).
    assert guard.match_reserved(
        "Rossi — Manuale di Termodinamica Applic.", index
    )
    # MAI match per solo autore.
    assert guard.match_reserved("Mario Rossi, Altra Opera Diversa", index) is None
    # Titolo non correlato.
    assert guard.match_reserved("Fisica dei Semiconduttori, Bianchi", index) is None


async def test_matcher_ignores_citable_and_short_titles():
    docs = [
        _doc(uuid.uuid4(), filename="dispensa.pdf", policy="citable"),
        _doc(
            uuid.uuid4(),
            filename="x.pdf",
            policy="content_only",
            summary=_summary(title="Reti"),  # troppo corto per containment
        ),
    ]
    index = guard.build_identity_index(docs)
    # Il citabile non entra nell'indice.
    assert all(e.filename_original != "dispensa.pdf" for e in index)
    # "Reti" (4 char) non fa containment dentro frasi lunghe.
    assert (
        guard.match_reserved(
            "Introduzione alle reti di calcolatori moderne", index
        )
        is None
    )


# ---------------------------------------------------------------------------
# Anonimizzazione del contesto documenti
# ---------------------------------------------------------------------------


async def test_context_golden_all_citable_without_identity_fields():
    """Con tutti i doc citabili e campi identitari VUOTI il contesto è
    byte-identico al formato storico (nessuna riga Fonte)."""
    course_id = uuid.uuid4()
    doc = _doc(course_id, filename="dispensa.pdf", summary=_summary())
    ctx = _build_documents_context([doc], 60_000)
    expected = (
        "## Documento: dispensa.pdf\n"
        "Lingua rilevata: it\n"
        "\n"
        "### Abstract\n"
        "Un abstract sul tema.\n"
        "\n"
        "### Struttura\n"
        "- Capitolo 1\n"
        "\n"
        "### Concetti chiave\n"
        "- **Concetto**: Spiegazione.\n"
        "\n"
        "### Definizioni\n"
        "- **Termine**: Definizione.\n"
        "\n"
        "### Tag rilevanza didattica: tag1"
    )
    assert ctx == expected


async def test_context_citable_gets_fonte_line_reserved_is_anonymous():
    course_id = uuid.uuid4()
    citable = _doc(
        course_id,
        filename="manuale.pdf",
        summary=_summary(title="Il Manuale", authors=["Anna Verdi"]),
    )
    reserved = _doc(
        course_id,
        filename="segreto_interno.pdf",
        policy="content_only",
        summary=_summary(
            title="Dispensa Interna Riservata", authors=["Bruno Neri"]
        ),
    )
    excluded = _doc(
        course_id,
        filename="fuori.pdf",
        policy="excluded",
        summary=_summary(title="Documento Fuori"),
    )
    ctx = _build_documents_context([citable, reserved, excluded], 60_000)
    # Citabile: riga Fonte dai campi dedicati.
    assert "Fonte: Il Manuale — Anna Verdi" in ctx
    # Riservato: header anonimo, NESSUN identificativo.
    assert "Materiale di contesto 1" in ctx
    assert "non citabile" in ctx  # framing presente
    assert "segreto_interno" not in ctx
    assert "Dispensa Interna Riservata" not in ctx
    assert "Bruno Neri" not in ctx
    # Escluso: del tutto assente.
    assert "Documento Fuori" not in ctx and "fuori.pdf" not in ctx


async def test_context_no_framing_without_reserved_docs():
    course_id = uuid.uuid4()
    ctx = _build_documents_context(
        [_doc(course_id, filename="a.pdf")], 60_000
    )
    assert "non citabile" not in ctx


# ---------------------------------------------------------------------------
# Filtro bibliografia P1 + filtro in lettura P3
# ---------------------------------------------------------------------------


def _arch(bibliography: list[RecommendedBibliographyItem]) -> ArchitectureOutput:
    return ArchitectureOutput(
        course_overview="Overview.",
        pedagogical_rationale="Razionale.",
        modules=[
            ArchitectureModule(
                module_id="M1",
                title="Modulo 1",
                description="",
                lessons=[
                    ArchitectureLesson(
                        lesson_id="M1.L1",
                        title="Intro",
                        summary="Sintesi.",
                        is_introductory=True,
                        recommended_bibliography=bibliography,
                    )
                ],
            )
        ],
    )


def _bib(title: str, authors: str, source: str) -> RecommendedBibliographyItem:
    return RecommendedBibliographyItem(
        authors=authors,
        title=title,
        publisher="Editore",
        year="2021",
        note="",
        source=source,
        confidence="to_verify",
    )


async def test_filter_reserved_bibliography_drops_matching(seeded_db):
    course_id, _org, _user = await build_course(
        seeded_db, status="draft", modules=1, lessons_per_module=1
    )
    course = (
        await seeded_db.execute(select(Course).where(Course.id == course_id))
    ).scalar_one()
    seeded_db.add(
        _doc(
            course_id,
            filename="riservato.pdf",
            policy="content_only",
            summary=_summary(title="Trattato di Economia Politica"),
        )
    )
    await seeded_db.commit()
    course = await load_course_full(seeded_db, course_id=course_id)

    arch = _arch(
        [
            _bib("Trattato di Economia Politica", "Rossi", "from_uploaded_documents"),
            _bib("Economia per Principianti", "Bianchi", "general_knowledge_suggestion"),
        ]
    )
    dropped = filter_reserved_bibliography(course, arch)
    assert len(dropped) == 1
    remaining = arch.modules[0].lessons[0].recommended_bibliography
    assert [b.title for b in remaining] == ["Economia per Principianti"]


async def test_filter_raises_when_intro_bibliography_emptied(seeded_db):
    from app.core.errors import ConflictError

    course_id, _org, _user = await build_course(
        seeded_db, status="draft", modules=1, lessons_per_module=1
    )
    seeded_db.add(
        _doc(
            course_id,
            filename="unico.pdf",
            policy="content_only",
            summary=_summary(title="Unico Testo di Riferimento"),
        )
    )
    await seeded_db.commit()
    course = await load_course_full(seeded_db, course_id=course_id)
    arch = _arch(
        [_bib("Unico Testo di Riferimento", "Rossi", "from_uploaded_documents")]
    )
    with pytest.raises(ConflictError) as exc:
        filter_reserved_bibliography(course, arch)
    assert exc.value.code == "architecture_bibliography_all_non_citable"


async def test_persisted_bibliography_filtered_in_prompt(seeded_db):
    """Filtro IN LETTURA: la bibliografia PERSISTITA che cita un
    documento reso riservato DOPO P1 non entra più nei prompt P3."""
    course_id, _org, _user = await build_course(
        seeded_db, status="content_pending", modules=1, lessons_per_module=1
    )
    seeded_db.add(
        _doc(
            course_id,
            filename="riservato.pdf",
            policy="content_only",
            summary=_summary(title="Il Libro Segreto Aziendale"),
        )
    )
    await seeded_db.commit()
    course = await load_course_full(seeded_db, course_id=course_id)
    lesson = find_lesson(course, "M1.L1")
    lesson.is_introductory = True
    lesson.recommended_bibliography = [
        {
            "authors": "Rossi",
            "title": "Il Libro Segreto Aziendale",
            "publisher": "E",
            "year": "2020",
            "note": "",
            "source": "from_uploaded_documents",
            "confidence": "confirmed",
        },
        {
            "authors": "Bianchi",
            "title": "Testo Pubblico di Base",
            "publisher": "E",
            "year": "2021",
            "note": "",
            "source": "general_knowledge_suggestion",
            "confidence": "to_verify",
        },
    ]
    await seeded_db.flush()
    formatted = _format_recommended_bibliography(course, lesson)
    assert "Testo Pubblico di Base" in formatted
    assert "Il Libro Segreto Aziendale" not in formatted


# ---------------------------------------------------------------------------
# PATCH policy: scrub + ri-analisi
# ---------------------------------------------------------------------------


async def test_update_policy_to_content_only_scrubs_and_requeues(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="draft", modules=1, lessons_per_module=1
    )
    doc = _doc(
        course_id,
        filename="doc.pdf",
        summary=_summary(title="Titolo Identitario", authors=["Rossi"]),
    )
    seeded_db.add(doc)
    await seeded_db.commit()
    course = (
        await seeded_db.execute(select(Course).where(Course.id == course_id))
    ).scalar_one()

    updated = await course_service.update_document_citation_policy(
        seeded_db,
        course=course,
        doc=doc,
        citation_policy="content_only",
        actor_id=user.id,
    )
    assert updated.citation_policy == "content_only"
    # Scrub immediato dei campi identitari + ri-accodamento al worker.
    assert updated.summary["source_title"] == ""
    assert updated.summary["authors_and_references"] == []
    assert updated.summary_status == "pending"


async def test_update_policy_same_value_is_noop(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="draft", modules=1, lessons_per_module=1
    )
    doc = _doc(course_id, filename="doc.pdf")
    seeded_db.add(doc)
    await seeded_db.commit()
    course = (
        await seeded_db.execute(select(Course).where(Course.id == course_id))
    ).scalar_one()
    updated = await course_service.update_document_citation_policy(
        seeded_db,
        course=course,
        doc=doc,
        citation_policy="citable",
        actor_id=user.id,
    )
    assert updated.summary_status == "ready"  # nessun ri-accodamento


# ---------------------------------------------------------------------------
# Integrazione B1xB2: i prompt map/digest/reduce della pipeline a
# copertura totale ereditano il vincolo "identità solo nei campi
# dedicati" (senza, un documento lungo riservato riaprirebbe la falla
# sul percorso chunked).
# ---------------------------------------------------------------------------


async def test_chunked_prompts_inherit_identity_hardening():
    from app.services.openai_summarize_service import (
        CHUNK_FACTS_SYSTEM_PROMPT,
        REDUCE_SYSTEM_PROMPT,
        SUMMARIZE_SYSTEM_PROMPT,
    )

    assert "authors_and_references" in CHUNK_FACTS_SYSTEM_PROMPT
    assert "non nominarli" in CHUNK_FACTS_SYSTEM_PROMPT.lower() or (
        "SOLO nel campo" in CHUNK_FACTS_SYSTEM_PROMPT
    )
    assert "source_title" in REDUCE_SYSTEM_PROMPT
    assert "IDENTITÀ SOLO NEI CAMPI DEDICATI" in SUMMARIZE_SYSTEM_PROMPT
