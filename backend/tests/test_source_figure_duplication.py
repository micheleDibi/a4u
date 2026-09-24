"""Duplicazione di un corso con figure di fonte (G6, WP4e).

- documenti con id nuovi e provenienza clonata (origine, licenza,
  bibliografia, materiale proprio, esito dell'estrazione);
- figure clonate (staccate comprese) con id nuovi e file copiati sotto il
  prefisso del corso nuovo, indipendenti da quelli del sorgente;
- nessun id di figura del corso sorgente nel JSON delle lezioni nuove;
- controprova: STESSA riga «Fonte» e stessa visibilità prima e dopo la
  duplicazione (resolver sui due corsi);
- costo Vision non copiato (niente doppio conteggio in dashboard);
- descrizione e parole chiave tradotte nella lingua del corso nuovo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_duplication_job import CourseDuplicationJob
from app.models.course_lesson import CourseLesson
from app.services import course_duplication_service as dup
from app.services import remote_storage
from app.services.document_figures import storage as figure_storage
from app.services.source_figure_service import resolve_source_figures
from tests.course_builders import build_course, build_course_document
from tests.source_figure_builders import build_document_figure


class _Storage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def download_bytes(self, key: str) -> bytes:
        if key not in self.files:
            raise remote_storage.StorageFileNotFound(key)
        return self.files[key]

    def upload_bytes(self, key: str, data: bytes) -> None:
        self.files[key] = data


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    fake = _Storage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


async def _source(db: AsyncSession, storage: _Storage) -> dict[str, Any]:
    course_id, _org, user = await build_course(
        db, modules=1, lessons_per_module=1, content_status="ready"
    )
    doc = build_course_document(course_id, filename="Dispense_misure.pdf")
    doc.license = "cc_by"
    doc.license_source = "user"
    doc.bibliography = {"title": "Vibrometria laser", "authors": ["Mario Rossi"], "year": 2021}
    doc.bibliography_source = "user"
    doc.is_own_work = False
    doc.figures_status = "ready"
    doc.figures_count = 1
    doc.figures_engine = "docling"
    doc.figures_coverage = "partial"
    db.add(doc)
    await db.flush()
    fig = build_document_figure(course_id, doc.id, license="cc_by")
    fig.preview_path = str(fig.storage_path).replace(".png", "-preview.jpg")
    fig.vision_usage = {"calls": 1, "cost_usd": 0.0007}
    fig.keywords = {"course": ["vibrometro laser", "cella di Bragg"], "en": ["laser vibrometer"]}
    detached = build_document_figure(
        course_id,
        None,
        license="unknown",
        storage_path=f"/uploads/courses/{course_id}/document_figures/external/p0009-f01.png",
        attribution={"title": "Documento cancellato", "authors": ["Anna Bianchi"]},
    )
    detached.detached_at = datetime.now(UTC)
    db.add_all([fig, detached])
    await db.flush()
    # Duplicato di `fig` (stesso phash) che ne riusa la descrizione Vision:
    # autoriferimenti da rimappare sui cloni.
    duplicate = build_document_figure(course_id, doc.id, license="cc_by", status="rejected", page=2)
    duplicate.reject_reason = "duplicate"
    duplicate.duplicate_of_id = fig.id
    duplicate.describe_source_id = fig.id
    # Figura della letteratura aperta (WP5): nessun documento.
    literature = build_document_figure(
        course_id,
        None,
        license="cc_by_sa",
        source_kind="wikimedia",
        storage_path=f"/uploads/courses/{course_id}/document_figures/external/wm-101-abc.png",
        attribution={"title": "LDV", "authors": ["Jane Doe"], "container": "Wikimedia Commons"},
        external_id="commons:101",
        source_url="https://commons.wikimedia.org/wiki/File:LDV.svg",
    )
    db.add_all([duplicate, literature])
    await db.flush()
    lesson = (
        (await db.execute(select(CourseLesson).where(CourseLesson.course_id == course_id)))
        .scalars()
        .one()
    )
    lesson.content_raw = {
        "introduction": "Intro [FIG:SRC-a] e [FIG:SRC-b].",
        "sections": [],
        "visual_assets": [
            {"asset_id": "SRC-a", "format": "source_figure", "content": str(fig.id)},
            {"asset_id": "SRC-b", "format": "source_figure", "content": str(detached.id)},
        ],
    }
    lesson.content_figure_review = {"version": 1, "figures": {"SRC-a": {"pairs": []}}}
    await db.commit()
    for path in (
        fig.storage_path,
        fig.preview_path,
        detached.storage_path,
        duplicate.storage_path,
        literature.storage_path,
        doc.file_path,
    ):
        storage.files[remote_storage.uploads_key(str(path))] = f"bytes:{path}".encode()
    return {
        "course_id": course_id,
        "user": user,
        "doc": doc,
        "fig": fig,
        "detached": detached,
        "duplicate": duplicate,
        "literature": literature,
    }


async def test_duplication_clones_figures_files_and_references(
    seeded_db: AsyncSession, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = seeded_db
    s = await _source(db, storage)
    source = await dup.load_source_full(db, course_id=s["course_id"])
    assert source is not None
    job = CourseDuplicationJob(
        source_course_id=source.id, target_language_code="en", requested_by_user_id=None
    )
    db.add(job)
    await db.flush()
    source_lesson = source.modules[0].lessons[0]
    before = await resolve_source_figures(
        db,
        course_id=source.id,
        assets=source_lesson.content_raw["visual_assets"],
        language="it",
    )

    target = await dup._clone_course_structure(
        db, source=source, target_language_code="en", job=job
    )
    target = await dup.load_target_full(db, course_id=target.id)
    assert target is not None

    (new_doc,) = target.documents
    old_doc = s["doc"]
    assert new_doc.id != old_doc.id
    for field in (
        "origin",
        "is_own_work",
        "license",
        "license_source",
        "bibliography",
        "bibliography_source",
        "figures_status",
        "figures_count",
        "figures_engine",
        "figures_coverage",
    ):
        assert getattr(new_doc, field) == getattr(old_doc, field), field

    rows = list(
        (
            await db.execute(
                select(CourseDocumentFigure).where(CourseDocumentFigure.course_id == target.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 4
    old_ids = {s["fig"].id, s["detached"].id, s["duplicate"].id, s["literature"].id}
    assert not ({r.id for r in rows} & old_ids)
    clone = next(r for r in rows if r.document_id is not None and r.status == "ready")
    clone_detached = next(r for r in rows if r.document_id is None and r.source_kind == "uploaded")
    clone_literature = next(r for r in rows if r.source_kind == "wikimedia")
    assert clone_literature.external_id == "commons:101"
    assert clone_literature.source_url == s["literature"].source_url
    assert clone_literature.attribution == s["literature"].attribution
    clone_dup = next(r for r in rows if r.status == "rejected")
    # Autoriferimenti rimappati sui cloni, mai verso il corso sorgente.
    assert clone_dup.duplicate_of_id == clone.id
    assert clone_dup.describe_source_id == clone.id
    assert clone.document_id == new_doc.id
    assert clone_detached.detached_at is not None
    assert clone_detached.attribution == s["detached"].attribution
    # File indipendenti, sotto il prefisso del corso nuovo.
    for row in rows:
        assert figure_storage.belongs_to_course(row.storage_path, target.id)
        assert remote_storage.uploads_key(str(row.storage_path)) in storage.files
    assert figure_storage.belongs_to_course(clone.preview_path, target.id)
    # Il costo Vision resta sul corso sorgente.
    assert clone.vision_usage is None and clone.vision_usage_at is None

    new_lesson = target.modules[0].lessons[0]
    raw_text = str(new_lesson.content_raw)
    for old in old_ids:
        assert str(old) not in raw_text
    assert new_lesson.content_figure_review == source_lesson.content_figure_review

    # Controprova: stessa riga «Fonte» e stessa visibilità.
    after = await resolve_source_figures(
        db,
        course_id=target.id,
        assets=new_lesson.content_raw["visual_assets"],
        language="it",
    )
    for asset_id in ("SRC-a", "SRC-b"):
        assert after[asset_id].renderable is before[asset_id].renderable is True
        assert after[asset_id].attribution_text == before[asset_id].attribution_text

    # Traduzione di descrizione e parole chiave (lingua del corso nuovo).
    async def fake_translate(*, items: dict[str, str], **kwargs: Any) -> dict[str, str]:
        return {key: f"EN:{value}" for key, value in items.items()}

    monkeypatch.setattr(dup, "_translate_batch_resilient", fake_translate)
    await dup._translate_document_figures(
        db,
        target=target,
        source_lang_code="it",
        source_lang_name="Italiano",
        target_lang_code="en",
        target_lang_name="English",
    )
    await db.commit()
    translated = await db.get(CourseDocumentFigure, clone.id, populate_existing=True)
    assert translated is not None
    assert translated.description == f"EN:{s['fig'].description}"
    assert translated.keywords == {
        "course": ["EN:vibrometro laser", "EN:cella di Bragg"],
        "en": ["laser vibrometer"],
    }
    # Il sorgente non cambia.
    original = await db.get(CourseDocumentFigure, s["fig"].id, populate_existing=True)
    assert original is not None and not str(original.description).startswith("EN:")


async def test_in_flight_extraction_is_not_cloned(
    seeded_db: AsyncSession, storage: _Storage
) -> None:
    db = seeded_db
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="in_corso.pdf")
    doc.figures_status = "processing"
    db.add(doc)
    await db.commit()
    storage.files[remote_storage.uploads_key(doc.file_path)] = b"pdf"
    source = await dup.load_source_full(db, course_id=course_id)
    assert source is not None
    job = CourseDuplicationJob(source_course_id=course_id, target_language_code="en")
    db.add(job)
    await db.flush()
    target = await dup._clone_course_structure(
        db, source=source, target_language_code="en", job=job
    )
    target = await dup.load_target_full(db, course_id=target.id)
    assert target is not None
    assert target.documents[0].figures_status is None
    assert uuid.UUID(str(target.documents[0].id)) != doc.id


async def test_tikz_asset_is_copied_verbatim(seeded_db: AsyncSession) -> None:
    """WP6.6: la duplicazione copia il sorgente `tikz` così com'è (anche con
    la traduzione: `visual_assets[].content` non è fra i percorsi
    tradotti), quindi il corso nuovo rende lo stesso SVG (stessa chiave di
    cache del renderer)."""
    from app.services import figure_render_service as frs
    from tests.test_tikz_validator import CHAIN

    db = seeded_db
    course_id, _org, _user = await build_course(
        db, modules=1, lessons_per_module=1, content_status="ready"
    )
    lesson = (
        (await db.execute(select(CourseLesson).where(CourseLesson.course_id == course_id)))
        .scalars()
        .one()
    )
    lesson.content_raw = {
        "introduction": "Vedi [FIG:t1].",
        "sections": [],
        "visual_assets": [
            {"asset_id": "t1", "format": "tikz", "content": CHAIN, "caption": "Catena."}
        ],
    }
    await db.commit()
    source = await dup.load_source_full(db, course_id=course_id)
    assert source is not None
    job = CourseDuplicationJob(
        source_course_id=source.id, target_language_code="en", requested_by_user_id=None
    )
    db.add(job)
    await db.flush()
    target = await dup._clone_course_structure(
        db, source=source, target_language_code="en", job=job
    )
    target = await dup.load_target_full(db, course_id=target.id)
    assert target is not None
    (copied,) = target.modules[0].lessons[0].content_raw["visual_assets"]
    assert copied["content"] == CHAIN and copied["format"] == "tikz"
    renderer = frs.REGISTRY["tikz"]
    assert frs.cache_key("tikz", renderer.sanitize(copied["content"])) == frs.cache_key(
        "tikz", renderer.sanitize(CHAIN)
    )


async def test_forward_references_survive_a_chunked_insert(
    seeded_db: AsyncSession, storage: _Storage
) -> None:
    """Fase D: con molte figure l'INSERT dei cloni è spezzato in blocchi; un
    duplicato che rimanda (`duplicate_of_id`, `describe_source_id`) a un
    originale inserito in un blocco successivo violava la FK. L'originale
    aggiornato dopo i duplicati (come fa la Vision) finisce in fondo
    all'heap, quindi in fondo alla SELECT."""
    from sqlalchemy import update

    db = seeded_db
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="Manuale.pdf")
    db.add(doc)
    await db.flush()
    original = build_document_figure(course_id, doc.id, license="cc_by", status="extracted")
    db.add(original)
    await db.flush()
    duplicates = []
    for page in range(900):
        dup_row = build_document_figure(
            course_id, doc.id, license="cc_by", status="rejected", page=page + 2
        )
        dup_row.reject_reason = "duplicate"
        dup_row.duplicate_of_id = original.id
        dup_row.describe_source_id = original.id
        duplicates.append(dup_row)
    db.add_all(duplicates)
    await db.flush()
    await db.execute(
        update(CourseDocumentFigure)
        .where(CourseDocumentFigure.id == original.id)
        .values(status="ready")
    )
    await db.commit()
    target_course_id, _o2, _u2 = await build_course(db, modules=1, lessons_per_module=1)
    target_doc = build_course_document(target_course_id, filename="Manuale.pdf")
    db.add(target_doc)
    await db.flush()
    fig_map = await dup._clone_document_figures(
        db,
        source_course_id=course_id,
        target_course_id=target_course_id,
        doc_map={doc.id: target_doc.id},
    )
    await db.commit()
    assert len(fig_map) == 901
    clones = (
        (
            await db.execute(
                select(CourseDocumentFigure).where(
                    CourseDocumentFigure.course_id == target_course_id,
                    CourseDocumentFigure.status == "rejected",
                )
            )
        )
        .scalars()
        .all()
    )
    assert {c.duplicate_of_id for c in clones} == {fig_map[original.id]}
    assert {c.describe_source_id for c in clones} == {fig_map[original.id]}
