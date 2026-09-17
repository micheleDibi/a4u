"""Fase 3 — normalizzazione di `key_takeaways` e `references` (WP5, B5/D18).

La dedup vive SOLO al confine di scrittura, nello schema
(`app/schemas/course_lesson_content.py`): validatori in mode "after" su
`LessonContentOutput` (output AI) e `LessonContentUpdateInput` (PATCH del
docente). Nessuna dedup in lettura: il PDF rende lo storico com'è
(`test_lesson_pdf_figures.py`).

- trim, voci vuote scartate, dedup case-insensitive con ordine e grafia
  della prima occorrenza; references a parità di `source`;
- `min_length`/`max_length` contano l'elenco grezzo: la lista persistita
  può degradare a 1-2 punti chiave senza rigenerare la lezione, e il
  worker lo segnala (`lesson_content_key_takeaways_below_min`);
- PATCH: `None` = campo non toccato, `[]` = azzeramento ammesso; tetto a
  12 come l'output AI (domanda aperta 14);
- lo storico si normalizza al primo salvataggio dall'editor, che invia
  sempre le due liste (ispezione di `handleSubmit`), o alla rigenerazione.

Oracolo che falliva prima di WP5: `build_lesson_content_output` con
`['Uno', 'uno', 'Uno ']` restituiva tre punti chiave e tre references
identiche restavano tre.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
import structlog.testing
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit_log import AuditLog
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_content import (
    KEY_TAKEAWAYS_MAX,
    KEY_TAKEAWAYS_MIN,
    LessonContentOutput,
    LessonContentReference,
    LessonContentUpdateInput,
)
from app.services import asset_validation_service
from app.services import course_lesson_content_crud as content_crud
from app.services import course_lesson_content_service as content_svc
from app.services import course_lesson_content_worker as worker
from app.services import openai_lesson_content_service as openai_svc
from tests.course_builders import build_course, build_lesson_content_output, find_lesson

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_EDIT_DIALOG = (
    _FRONTEND / "pages" / "org" / "courses" / "components" / "LessonContentEditDialog.tsx"
)

DOC = "documento_caricato"
GEN = "suggerimento_generale"


def _refs(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"citation": citation, "source": source} for citation, source in pairs]


def _pairs(refs: list[LessonContentReference] | None) -> list[tuple[str, str]]:
    return [(r.source, r.citation) for r in refs or []]


def _historical_raw() -> dict[str, Any]:
    """`content_raw` scritto prima di WP5: tre punti chiave uguali a meno di
    maiuscole e spazi, tre references identiche."""
    return {
        "introduction": "x",
        "sections": [],
        "summary": "s",
        "key_takeaways": ["Uno", "uno", "Uno "],
        "references": _refs(*[("Rossi 2020", GEN)] * 3),
    }


# ---------------------------------------------------------------------------
# Output AI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            ["Primo punto", "primo punto", " PRIMO PUNTO "], ["Primo punto"], id="tre_identiche"
        ),
        pytest.param(["b", "A", "", "a", "B ", "  ", "c"], ["b", "A", "c"], id="ordine_e_vuoti"),
        pytest.param(
            ["Primo punto", "Secondo punto", "Terzo punto"],
            ["Primo punto", "Secondo punto", "Terzo punto"],
            id="gia_pulite_invariate",
        ),
        pytest.param(["Müller", "müller", "Altro"], ["Müller", "Altro"], id="unicode_lower"),
        pytest.param(["Uno", " uno ", "UNO", "Due"], ["Uno", "Due"], id="degrado_sotto_tre"),
    ],
)
def test_output_key_takeaways_normalized(raw: list[str], expected: list[str]) -> None:
    """Il caso `degrado_sotto_tre` pinna il mode "after": in mode "before" la
    lista deduplicata darebbe `too_short` e la lezione verrebbe rigenerata."""
    assert build_lesson_content_output(key_takeaways=raw).key_takeaways == expected


@pytest.mark.parametrize(
    ("raw", "error_type"),
    [
        pytest.param(["A", "B"], "too_short", id="too_short"),
        pytest.param(["A"] * KEY_TAKEAWAYS_MAX + ["B"], "too_long", id="too_long"),
        pytest.param(["", " ", "\t"], "value_error", id="solo_vuoti"),
    ],
)
def test_output_key_takeaways_constraints_count_raw_list(raw: list[str], error_type: str) -> None:
    with pytest.raises(ValidationError) as exc:
        build_lesson_content_output(key_takeaways=raw)
    errors = exc.value.errors()
    assert [e["type"] for e in errors] == [error_type]
    assert errors[0]["loc"] == ("key_takeaways",)
    if error_type == "value_error":
        assert "key_takeaways: lista vuota dopo cleanup" in str(exc.value)


def test_output_raw_bounds_are_the_shared_constants() -> None:
    """Tre voci grezze distinte sono il minimo; dodici uguali sono ammesse e
    collassano a una (il vincolo guarda l'elenco del modello)."""
    assert (KEY_TAKEAWAYS_MIN, KEY_TAKEAWAYS_MAX) == (3, 12)
    out = build_lesson_content_output(key_takeaways=["Uguale"] * KEY_TAKEAWAYS_MAX)
    assert out.key_takeaways == ["Uguale"]


def test_output_references_dedup_per_source_with_trim() -> None:
    out = build_lesson_content_output(
        references=_refs(
            ("Rossi (2020) ", DOC),
            ("rossi (2020)", DOC),
            ("ROSSI (2020)", DOC),
            ("Rossi (2020)", GEN),
            ("   ", DOC),
            ("Bianchi", DOC),
        )
    )
    assert _pairs(out.references) == [
        (DOC, "Rossi (2020)"),
        (GEN, "Rossi (2020)"),
        (DOC, "Bianchi"),
    ]
    assert all(isinstance(r, LessonContentReference) for r in out.references)
    # Il worker persiste `model_dump()`: la citation vi arriva già trimmata.
    assert out.model_dump()["references"][0] == {"citation": "Rossi (2020)", "source": DOC}


def test_output_references_same_citation_different_source_both_kept() -> None:
    both = build_lesson_content_output(
        references=_refs(("Rossi (2019)", DOC), ("Rossi (2019)", GEN))
    )
    assert _pairs(both.references) == [(DOC, "Rossi (2019)"), (GEN, "Rossi (2019)")]
    same = build_lesson_content_output(
        references=_refs(("Rossi (2019)", GEN), ("rossi (2019)", GEN), ("  Rossi (2019) ", GEN))
    )
    assert _pairs(same.references) == [(GEN, "Rossi (2019)")]


def test_output_references_already_clean_keep_their_instances() -> None:
    out = build_lesson_content_output(references=_refs(("Rossi", DOC), ("Bianchi", GEN)))
    again = LessonContentOutput._dedup_references(list(out.references))
    assert [id(r) for r in again] == [id(r) for r in out.references]


def test_output_reference_blank_citation_item_vs_list() -> None:
    """`citation=""` è respinta dall'item (prima del validatore di lista,
    comportamento invariato); `citation="   "` supera `min_length=1` ed è
    scartata dalla lista. Asimmetria dichiarata."""
    with pytest.raises(ValidationError) as exc:
        build_lesson_content_output(references=_refs(("", DOC)))
    (error,) = exc.value.errors()
    assert error["loc"] == ("references", 0, "citation")
    assert error["type"] == "string_too_short"

    out = build_lesson_content_output(references=_refs(("   ", DOC), ("Bianchi (2020)", GEN)))
    assert _pairs(out.references) == [(GEN, "Bianchi (2020)")]


def test_output_empty_references_stay_empty() -> None:
    assert build_lesson_content_output(references=[]).references == []


# ---------------------------------------------------------------------------
# PATCH del docente
# ---------------------------------------------------------------------------


def test_update_input_none_stays_none() -> None:
    """`None` = campo assente: il CRUD usa `is not None`, un `[]` spurio
    azzererebbe le liste della lezione."""
    for payload in (
        LessonContentUpdateInput(),
        LessonContentUpdateInput(key_takeaways=None, references=None),
    ):
        assert payload.key_takeaways is None
        assert payload.references is None


def test_update_input_empty_and_duplicates() -> None:
    cleared = LessonContentUpdateInput(key_takeaways=[], references=[])
    assert (cleared.key_takeaways, cleared.references) == ([], [])

    dup = {"citation": "Rossi", "source": DOC}
    deduped = LessonContentUpdateInput.model_validate(
        {"key_takeaways": ["x", "X"], "references": [dup, dup]}
    )
    assert deduped.key_takeaways == ["x"]
    assert _pairs(deduped.references) == [(DOC, "Rossi")]

    # Diversamente dall'output AI, le sole voci bianche danno [] senza errore.
    assert LessonContentUpdateInput(key_takeaways=[" ", "", "\t"]).key_takeaways == []


def test_update_input_accepts_as_many_key_takeaways_as_the_ai_output() -> None:
    """Domanda aperta 14: con `max_length=10` una lezione AI da 11-12 punti
    chiave non era salvabile dall'editor."""
    eleven = [f"Punto {i}" for i in range(11)]
    assert LessonContentUpdateInput(key_takeaways=eleven).key_takeaways == eleven
    twelve = [f"Punto {i}" for i in range(KEY_TAKEAWAYS_MAX)]
    assert build_lesson_content_output(key_takeaways=twelve).key_takeaways == twelve
    assert LessonContentUpdateInput(key_takeaways=twelve).key_takeaways == twelve
    for model in (LessonContentUpdateInput, LessonContentOutput):
        assert model.model_fields["key_takeaways"].metadata[-1].max_length == KEY_TAKEAWAYS_MAX
    with pytest.raises(ValidationError) as exc:
        LessonContentUpdateInput(key_takeaways=[*twelve, "Punto 12"])
    assert [e["type"] for e in exc.value.errors()] == ["too_long"]


def test_update_input_is_idempotent_on_output_lists() -> None:
    out = build_lesson_content_output(
        key_takeaways=["Uno", "uno", "Due", " Tre "],
        references=_refs(("Rossi", DOC), ("rossi ", DOC), ("Rossi", GEN)),
    )
    upd = LessonContentUpdateInput.model_validate(
        {"key_takeaways": out.key_takeaways, "references": out.model_dump()["references"]}
    )
    assert upd.key_takeaways == out.key_takeaways == ["Uno", "Due", "Tre"]
    assert _pairs(upd.references) == _pairs(out.references) == [(DOC, "Rossi"), (GEN, "Rossi")]


async def _rows(db: Any, lesson_id: uuid.UUID) -> tuple[dict[str, Any], list[AuditLog]]:
    raw = (
        await db.execute(select(CourseLesson.content_raw).where(CourseLesson.id == lesson_id))
    ).scalar_one()
    audits = (
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.action == "course.lesson.content.updated",
                    AuditLog.target_id == str(lesson_id),
                )
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    return raw, list(audits)


async def test_patch_normalizes_only_the_fields_it_carries(seeded_db: Any) -> None:
    """Terza via di B5: un PATCH che non porta le liste le lascia com'erano
    (nessun backfill); il salvataggio dall'editor, che le invia sempre, le
    normalizza. Il conteggio di audit è post-normalizzazione."""
    course_id, _org, user = await build_course(
        seeded_db, modules=1, lessons_per_module=1, content_status="ready"
    )
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    historical = _historical_raw()
    lesson.content_raw = historical
    await seeded_db.flush()

    await content_crud.update_lesson_content(
        seeded_db,
        course=course,
        lesson=lesson,
        payload=LessonContentUpdateInput(introduction="Nuova intro"),
        actor_id=user.id,
    )
    raw, audits = await _rows(seeded_db, lesson.id)
    assert raw["introduction"] == "Nuova intro"
    assert raw["key_takeaways"] == ["Uno", "uno", "Uno "]
    assert len(raw["references"]) == 3
    assert audits[-1].payload["fields"] == {"introduction": len("Nuova intro")}

    # Ciò che invia l'editor: entrambe le liste, così come le mostrava.
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    await content_crud.update_lesson_content(
        seeded_db,
        course=course,
        lesson=lesson,
        payload=LessonContentUpdateInput.model_validate(
            {
                "key_takeaways": historical["key_takeaways"],
                "references": historical["references"],
            }
        ),
        actor_id=user.id,
    )
    raw, audits = await _rows(seeded_db, lesson.id)
    assert raw["key_takeaways"] == ["Uno"]
    assert raw["references"] == [{"citation": "Rossi 2020", "source": GEN}]
    assert raw["introduction"] == "Nuova intro"
    assert audits[-1].payload["fields"] == {"key_takeaways": 1, "references": 1}
    modified_at = (
        await seeded_db.execute(
            select(CourseLesson.content_modified_at).where(CourseLesson.id == lesson.id)
        )
    ).scalar_one()
    assert modified_at is not None


def _submit_block() -> str:
    if not _EDIT_DIALOG.exists():  # pragma: no cover - albero FE assente
        pytest.skip(f"sorgente frontend assente: {_EDIT_DIALOG}")
    src = _EDIT_DIALOG.read_text(encoding="utf-8")
    match = re.search(r"const payload: LessonContentUpdateInput = \{(.*?)\};", src, re.S)
    assert match, "payload di handleSubmit non trovato"
    return match.group(1)


def test_editor_submit_sends_both_lists_on_every_save() -> None:
    """La promessa «lo storico si normalizza al primo salvataggio» regge solo
    se l'editor invia SEMPRE le due liste. Le righe vuote aggiunte e non
    compilate sono scartate prima dell'invio (una citation vuota darebbe
    422 `string_too_short`, domanda aperta 13)."""
    block = _submit_block()
    assert "key_takeaways: keyTakeaways.filter((kt) => kt.trim().length > 0)," in block
    assert "references: references.filter((ref) => ref.citation.trim().length > 0)," in block
    assert not re.search(r"^\s*references,\s*$", block, re.M)


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return {
            "choices": [
                {"message": {"content": json.dumps(self._payload)}, "finish_reason": "stop"}
            ],
            "usage": {},
        }


def _fake_client_for(payload: dict[str, Any]) -> Any:
    class _FakeClient:
        def __init__(self, timeout: float = 600.0) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any] | None = None) -> _FakeResponse:
            assert url == "/chat/completions"
            return _FakeResponse(payload)

    return _FakeClient


async def test_openai_parse_path_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unico test sul call-site di produzione (`generate_lesson_content` →
    `LessonContentOutput.model_validate`), con il client OpenAI sostituito
    da una risposta preconfezionata: nessuna rete, nessuna API key."""
    payload = build_lesson_content_output().model_dump()
    payload["key_takeaways"] = ["Uno", "uno", "UNO", "Due"]
    payload["references"] = _refs(*[("Rossi 2020", GEN)] * 3)
    monkeypatch.setattr(openai_svc, "get_client", _fake_client_for(payload))

    content, usage = await openai_svc.generate_lesson_content(
        user_prompt="x", language_code="it", is_regeneration=False
    )
    assert content.key_takeaways == ["Uno", "Due"]
    assert _pairs(content.references) == [(GEN, "Rossi 2020")]
    assert usage["total"] == 0


# ---------------------------------------------------------------------------
# Worker: warning quando la dedup scende sotto il minimo
# ---------------------------------------------------------------------------


def _warnings(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == "lesson_content_key_takeaways_below_min"]


def test_worker_warns_only_when_dedup_goes_below_the_minimum() -> None:
    lesson = CourseLesson(id=uuid.uuid4(), lesson_code="M1.L1")
    degraded = build_lesson_content_output(key_takeaways=["Uno", "uno", "Due"])
    full = build_lesson_content_output()
    with structlog.testing.capture_logs() as logs:
        worker._warn_on_degraded_key_takeaways(lesson, degraded)
        worker._warn_on_degraded_key_takeaways(lesson, full)
        worker._warn_on_degraded_key_takeaways(lesson, object())
    (event,) = _warnings(logs)
    assert event["log_level"] == "warning"
    assert event["lesson_code"] == "M1.L1"
    assert event["lesson_id"] == str(lesson.id)
    assert (event["key_takeaways"], event["minimum"]) == (2, KEY_TAKEAWAYS_MIN)


@pytest.mark.parametrize(
    ("takeaways", "persisted", "warned"),
    [
        pytest.param(["Uno", "uno", "UNO", "Due"], ["Uno", "Due"], True, id="degradata"),
        pytest.param(["Uno", "Due", "Tre"], ["Uno", "Due", "Tre"], False, id="integra"),
    ],
)
async def test_worker_persists_the_deduplicated_lesson_and_warns(
    seeded_db: Any,
    _engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    takeaways: list[str],
    persisted: list[str],
    warned: bool,
) -> None:
    """Giro completo di `_process_one` con la chiamata OpenAI e il fix degli
    asset sostituiti: la lezione degradata arriva a `ready` (nessuna
    rigenerazione) con la lista deduplicata in `content_raw`, e il warning
    compare solo in quel caso."""
    course_id, _org, _user = await build_course(
        seeded_db, modules=1, lessons_per_module=1, content_status="pending"
    )
    await seeded_db.execute(
        update(Course).where(Course.id == course_id).values(glossary_status="ready")
    )
    await seeded_db.commit()
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson_id = find_lesson(course, "M1.L1").id

    output = build_lesson_content_output(key_takeaways=takeaways)
    usage = {"total": 1, "prompt": 1, "completion": 0, "model": "gpt-5.5"}

    async def _generate(**_kwargs: Any) -> tuple[LessonContentOutput, dict[str, Any]]:
        return output, usage

    async def _no_fix(content: LessonContentOutput, *, language_code: str) -> LessonContentOutput:
        return content

    monkeypatch.setattr(
        worker, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    monkeypatch.setattr(openai_svc, "generate_lesson_content", _generate)
    monkeypatch.setattr(asset_validation_service, "validate_and_fix_content_assets", _no_fix)

    with structlog.testing.capture_logs() as logs:
        await worker._process_one(lesson_id)

    row = (
        await seeded_db.execute(
            select(CourseLesson.content_status, CourseLesson.content_raw).where(
                CourseLesson.id == lesson_id
            )
        )
    ).one()
    assert row.content_status == "ready", [e for e in logs if e["log_level"] != "info"]
    assert row.content_raw["key_takeaways"] == persisted
    assert [e["key_takeaways"] for e in _warnings(logs)] == ([len(persisted)] if warned else [])
