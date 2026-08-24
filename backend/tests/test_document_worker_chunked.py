from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.course_document import CourseDocument
from app.models.course_document_chunk import CourseDocumentChunk
from app.schemas.document_summary import ChunkFactsOut, DocumentSummaryOut
from app.services import (
    course_document_worker as worker,
)
from app.services import (
    openai_summarize_service,
    remote_storage,
)
from tests.course_builders import build_course

pytestmark = pytest.mark.asyncio


class _FakeStorage:
    """Storage finto: 'scarica' sempre lo stesso contenuto testuale."""

    def __init__(self, content: str) -> None:
        self.content = content

    def download_to(self, _key: str, dest: Path) -> None:
        Path(dest).write_text(self.content, encoding="utf-8")


def _patched_settings(**overrides):
    base = get_settings()
    return base.model_copy(update=overrides)


def _summary(abstract: str = "Sintesi finale.") -> DocumentSummaryOut:
    return DocumentSummaryOut(
        source_title="Titolo",
        detected_language="it",
        abstract=abstract,
        structure_outline=["Capitolo 1"],
    )


def _facts(label: str) -> ChunkFactsOut:
    return ChunkFactsOut(
        chunk_abstract=f"Contenuto del {label}.",
        detected_language="it",
        definitions=[
            {"term": f"Termine {label}", "definition": f"Definizione {label}."}
        ],
    )


def _usage(total: int = 100) -> dict:
    return {"prompt": total // 2, "completion": total // 2, "total": total,
            "model": "fake"}


async def _make_doc(db, *, status: str = "pending", attempts: int = 0):
    course_id, _org, _user = await build_course(
        db, status="draft", modules=1, lessons_per_module=1
    )
    doc = CourseDocument(
        course_id=course_id,
        filename_original="documento_lungo.txt",
        filename_stored=f"{uuid.uuid4().hex}.txt",
        file_path=f"/uploads/courses/{course_id}/{uuid.uuid4().hex}.txt",
        mime_type="text/plain",
        size_bytes=1,
        summary_status=status,
        summary_attempts=attempts,
    )
    db.add(doc)
    await db.commit()
    return doc


async def _chunk_rows(db, doc_id):
    return (
        (
            await db.execute(
                select(CourseDocumentChunk)
                .where(CourseDocumentChunk.document_id == doc_id)
                .order_by(CourseDocumentChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )


@pytest.fixture
def chunked_env(monkeypatch):
    """Ambiente chunked: soglie basse, storage finto con testo ~3k."""
    text = " ".join(f"parola{i}" for i in range(400))  # ~3.4k char
    monkeypatch.setattr(
        remote_storage, "get_storage", lambda: _FakeStorage(text)
    )
    settings = _patched_settings(
        course_document_full_coverage_enabled=True,
        course_document_singleshot_max_chars=1_000,
        course_document_chunk_chars=800,
        course_document_chunk_overlap_chars=100,
        course_document_max_chars_hard=1_000_000,
        course_document_chunk_concurrency=2,
        course_document_summary_attempts_max=10,
        course_document_reduce_input_max_chars=80_000,
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    return text


async def test_chunked_happy_path(seeded_db, monkeypatch, chunked_env):
    doc = await _make_doc(seeded_db)
    map_labels: list[str] = []

    async def fake_map(*, chunk_text, source_filename, position_label):
        map_labels.append(position_label)
        return _facts(position_label), _usage(100)

    async def fake_reduce(*, facts_block, abstracts_block, source_filename,
                          language_hint):
        assert "Termine" in facts_block
        assert language_hint == "it"
        return _summary(), _usage(300)

    monkeypatch.setattr(
        openai_summarize_service, "extract_chunk_facts", fake_map
    )
    monkeypatch.setattr(
        openai_summarize_service, "reduce_summary", fake_reduce
    )

    await worker._process_one(seeded_db, doc)
    await seeded_db.refresh(doc)
    assert doc.summary_status == "ready"
    assert doc.summary_coverage == "full"
    assert doc.summary["abstract"] == "Sintesi finale."
    assert doc.summary_chunks_total == doc.summary_chunks_done
    assert doc.summary_chunks_total == len(map_labels)
    assert doc.summary_tokens["calls"] == len(map_labels) + 1
    # Chunk effimeri: cancellati al successo del reduce.
    assert await _chunk_rows(seeded_db, doc.id) == []


async def test_chunked_failure_then_resume(seeded_db, monkeypatch,
                                           chunked_env):
    doc = await _make_doc(seeded_db)
    fail_once = {"armed": True}
    called_labels: list[str] = []

    async def fake_map(*, chunk_text, source_filename, position_label):
        called_labels.append(position_label)
        if fail_once["armed"] and position_label.startswith("blocco 1 "):
            raise openai_summarize_service.OpenAISummarizeError(
                status=400, message="schema non valido"
            )
        return _facts(position_label), _usage(100)

    async def fake_reduce(**_kwargs):
        return _summary(), _usage(300)

    monkeypatch.setattr(
        openai_summarize_service, "extract_chunk_facts", fake_map
    )
    monkeypatch.setattr(
        openai_summarize_service, "reduce_summary", fake_reduce
    )

    await worker._process_one(seeded_db, doc)
    await seeded_db.refresh(doc)
    assert doc.summary_status == "failed"
    assert "Analisi fallita al blocco" in (doc.summary_error or "")
    persisted = await _chunk_rows(seeded_db, doc.id)
    total = doc.summary_chunks_total
    assert total is not None and total > 2
    assert len(persisted) < total  # il blocco fallito non è persistito

    # Reprocess manuale: riparte SOLO dai blocchi mancanti.
    persisted_indices = {row.chunk_index for row in persisted}
    fail_once["armed"] = False
    called_labels.clear()
    doc.summary_status = "pending"
    await seeded_db.commit()

    await worker._process_one(seeded_db, doc)
    await seeded_db.refresh(doc)
    assert doc.summary_status == "ready"
    assert doc.summary_coverage == "full"
    called_indices = {
        int(label.split(" ")[1]) - 1 for label in called_labels
    }
    assert called_indices == set(range(total)) - persisted_indices
    assert await _chunk_rows(seeded_db, doc.id) == []


async def test_killswitch_uses_singleshot_and_wipes_chunks(
    seeded_db, monkeypatch
):
    text = "contenuto breve " * 400  # ~6.4k
    monkeypatch.setattr(
        remote_storage, "get_storage", lambda: _FakeStorage(text)
    )
    settings = _patched_settings(
        course_document_full_coverage_enabled=False,
        course_document_singleshot_max_chars=1_000,
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)

    doc = await _make_doc(seeded_db)
    seeded_db.add(
        CourseDocumentChunk(
            document_id=doc.id,
            chunk_index=0,
            char_start=0,
            char_end=10,
            result={"stray": True},
        )
    )
    await seeded_db.commit()

    async def fake_singleshot(*, text, source_filename):
        return _summary("Sintesi single-shot."), _usage(200)

    monkeypatch.setattr(
        openai_summarize_service, "summarize_document", fake_singleshot
    )

    await worker._process_one(seeded_db, doc)
    await seeded_db.refresh(doc)
    assert doc.summary_status == "ready"
    assert doc.summary_coverage is None  # legacy: nessun badge
    assert doc.summary_fingerprint is None
    # Delete incondizionata: nessuna riga chunk orfana dopo il kill-switch.
    assert await _chunk_rows(seeded_db, doc.id) == []


async def test_small_doc_singleshot_full_coverage(seeded_db, monkeypatch):
    text = "breve"
    monkeypatch.setattr(
        remote_storage, "get_storage", lambda: _FakeStorage(text)
    )
    settings = _patched_settings(
        course_document_full_coverage_enabled=True,
        course_document_singleshot_max_chars=1_000,
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    doc = await _make_doc(seeded_db)

    async def fake_singleshot(*, text, source_filename):
        return _summary(), _usage(50)

    monkeypatch.setattr(
        openai_summarize_service, "summarize_document", fake_singleshot
    )
    await worker._process_one(seeded_db, doc)
    await seeded_db.refresh(doc)
    assert doc.summary_status == "ready"
    assert doc.summary_coverage == "full"


async def test_requeue_guard_marks_failed(seeded_db, monkeypatch,
                                          chunked_env):
    doc = await _make_doc(seeded_db, status="processing", attempts=10)

    async def boom(**_kwargs):  # non deve mai essere chiamato
        raise AssertionError("non doveva partire")

    monkeypatch.setattr(
        openai_summarize_service, "extract_chunk_facts", boom
    )
    await worker._process_one(seeded_db, doc)
    await seeded_db.refresh(doc)
    assert doc.summary_status == "failed"
    assert "tentativi" in (doc.summary_error or "")


async def test_superseded_run_does_not_overwrite(seeded_db, monkeypatch,
                                                 chunked_env):
    doc = await _make_doc(seeded_db)

    async def fake_map(*, chunk_text, source_filename, position_label):
        return _facts(position_label), _usage(100)

    async def fake_reduce(**_kwargs):
        # Un reprocess arriva mid-run: reset a pending PRIMA del commit
        # finale del worker.
        doc.summary_status = "pending"
        await seeded_db.commit()
        return _summary(), _usage(300)

    monkeypatch.setattr(
        openai_summarize_service, "extract_chunk_facts", fake_map
    )
    monkeypatch.setattr(
        openai_summarize_service, "reduce_summary", fake_reduce
    )
    await worker._process_one(seeded_db, doc)
    await seeded_db.refresh(doc)
    # Guard ottimistico: il run superato NON scrive ready.
    assert doc.summary_status == "pending"
    assert doc.summary is None
    # I chunk restano per la ripresa del run successivo.
    assert len(await _chunk_rows(seeded_db, doc.id)) > 0


async def test_deleted_doc_mid_run_aborts_cleanly(seeded_db, monkeypatch,
                                                  chunked_env):
    doc = await _make_doc(seeded_db)

    async def fake_map(*, chunk_text, source_filename, position_label):
        return _facts(position_label), _usage(100)

    monkeypatch.setattr(
        openai_summarize_service, "extract_chunk_facts", fake_map
    )

    async def gone(_db, _doc_id):
        return False

    monkeypatch.setattr(worker, "_doc_still_exists", gone)
    await worker._process_one(seeded_db, doc)
    await seeded_db.refresh(doc)
    # Abort pulito: nessun ready, nessuna riga chunk scritta.
    assert doc.summary_status == "processing"
    assert doc.summary is None
    assert await _chunk_rows(seeded_db, doc.id) == []
