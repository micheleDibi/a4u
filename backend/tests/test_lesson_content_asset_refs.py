"""Fase 3 — riferimenti agli asset in materializzazione (WP5) e tetto A1.

- `_count_asset_refs` conta le occorrenze per `(kind, id)` (al posto
  dell'insieme di `_collect_asset_refs`): un tag ripetuto produce il
  warning `lesson_content_duplicate_asset_refs` e la lezione resta
  materializzata;
- il corpus dei warning unused/dangling comprende `examples[].content` e
  `tables[].markdown`;
- A1: `content` di un asset visivo ha un tetto di risorsa
  (`VISUAL_ASSET_CONTENT_MAX_CHARS`) coerente con i tetti per formato,
  sugli asset generati (output AI di Fase 3 e 4) e, nel PATCH, solo sugli
  asset cambiati: un Mermaid storico più lungo e invariato non blocca la
  correzione di un refuso (giro 1 della verifica, V1-F2).

Oracolo che falliva prima di WP5: `[FIG:a]` ripetuto tre volte non lasciava
traccia nei log (l'insieme collassava le occorrenze).
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.schemas.course_lesson_content import (
    VISUAL_ASSET_CONTENT_MAX_CHARS,
    LessonContentOutput,
    LessonContentUpdateInput,
    LessonContentVisualAsset,
)
from app.schemas.course_lesson_slides import (
    LessonSlideNewAsset,
    LessonSlidesOutput,
    LessonSlidesUpdateInput,
)
from app.services import course_lesson_content_crud as content_crud
from app.services import course_lesson_content_service as content_svc
from app.services import figure_render_service as frs
from app.services.figure_compute.graph_rules import MAX_MERMAID_SOURCE_CHARS
from tests.course_builders import build_course, build_lesson_content_output, find_lesson

USAGE = {"total": 1, "prompt": 1, "completion": 0, "model": "gpt-5.5"}
_FLOW = "flowchart LR\n  A --> B"


def test_count_asset_refs_counts_occurrences_case_insensitively() -> None:
    text = "Vedi [FIG:A], poi [FIG:a] e [TAB:t1].\n[FIG:A]\n[EQ:e\n1] [fig:A]"
    counts = content_svc._count_asset_refs(text)
    assert counts == {("FIG", "a"): 3, ("TAB", "t1"): 1}
    assert not hasattr(content_svc, "_collect_asset_refs")


async def _lesson(db: Any) -> tuple[Any, Any]:
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    course = await content_svc.load_course_full(db, course_id=course_id)
    lesson = find_lesson(course, "M1.L1")
    lesson.learning_objectives = ["Comprendere l'argomento"]
    lesson.mandatory_topics = [{"topic_id": "T1", "topic": "Argomento", "rationale": "x"}]
    await db.flush()
    return course, lesson


def _output(**overrides: Any) -> LessonContentOutput:
    base = build_lesson_content_output(
        visual_assets=[
            {"asset_id": "A", "format": "mermaid", "content": _FLOW, "caption": "Flusso"},
        ],
    ).model_dump()
    return LessonContentOutput.model_validate({**base, **overrides})


async def _materialize(db: Any, output: LessonContentOutput) -> tuple[Any, list[dict[str, Any]]]:
    course, lesson = await _lesson(db)
    with structlog.testing.capture_logs() as logs:
        await content_svc.materialize_lesson_content(
            db, course=course, lesson=lesson, output=output, raw=output.model_dump(), usage=USAGE
        )
    return lesson, logs


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == name]


async def test_repeated_tag_warns_without_failing(seeded_db: Any) -> None:
    output = _output(
        introduction="Come mostra [FIG:A] e ancora [FIG:a].",
        summary="In sintesi:\n\n[FIG:A]\n",
    )
    lesson, logs = await _materialize(seeded_db, output)
    assert lesson.content_status == "ready"
    assert [e["duplicated"] for e in _events(logs, "lesson_content_duplicate_asset_refs")] == [
        {"FIG:a": 3}
    ]
    assert _events(logs, "lesson_content_unused_assets") == []


async def test_single_tag_does_not_warn(seeded_db: Any) -> None:
    lesson, logs = await _materialize(seeded_db, _output(introduction="Figura:\n\n[FIG:A]\n"))
    assert lesson.content_status == "ready"
    assert _events(logs, "lesson_content_duplicate_asset_refs") == []


async def test_examples_and_tables_are_part_of_the_reference_corpus(seeded_db: Any) -> None:
    """Un tag nell'esempio è un uso (niente falso «unused»); un id
    inesistente nella tabella è un ref pendente."""
    output = _output(
        introduction="Nessun tag nel corpo.",
        examples=[{"example_id": "x1", "title": "Esempio", "content": "Si veda [FIG:A]."}],
        tables=[{"table_id": "t1", "markdown": "| a |\n|---|\n| [FIG:fantasma] |"}],
    )
    _lesson_row, logs = await _materialize(seeded_db, output)
    unused = _events(logs, "lesson_content_unused_assets")
    assert [e["unused_visuals"] for e in unused] == [[]]
    assert [e["unused_tables"] for e in unused] == [["t1"]]
    assert [e["unknown_fig"] for e in _events(logs, "lesson_content_dangling_asset_refs")] == [
        ["fantasma"]
    ]


# ---------------------------------------------------------------------------
# A1: tetto di risorsa sul sorgente
# ---------------------------------------------------------------------------


def test_visual_asset_content_cap_covers_every_format_cap() -> None:
    assert MAX_MERMAID_SOURCE_CHARS <= VISUAL_ASSET_CONTENT_MAX_CHARS
    assert frs.VEGALITE_MAX_CHARS <= VISUAL_ASSET_CONTENT_MAX_CHARS
    default_dot = Settings.model_fields["figure_dot_max_chars"].default
    assert default_dot == VISUAL_ASSET_CONTENT_MAX_CHARS


def _asset(size: int, fmt: str = "dot") -> dict[str, Any]:
    return {"asset_id": "A", "format": fmt, "content": "x" * size}


def _slides_output(asset: dict[str, Any]) -> dict[str, Any]:
    slide = {"slide_number": 1, "slide_id": "S1", "type": "concept", "title": "T"}
    return {"lesson_id": "M1.L1", "total_slides": 1, "slides": [slide], "new_assets": [asset]}


def test_generated_visual_assets_have_the_resource_cap() -> None:
    """Output AI di Fase 3 e 4: `value_error` sull'elemento della lista
    oltre il tetto, nessun errore al tetto."""
    base = build_lesson_content_output().model_dump()
    cap = VISUAL_ASSET_CONTENT_MAX_CHARS
    ok = LessonContentOutput.model_validate({**base, "visual_assets": [_asset(cap)]})
    assert len(ok.visual_assets[0].content) == cap
    assert type(ok.visual_assets[0]) is LessonContentVisualAsset
    with pytest.raises(ValidationError) as err:
        LessonContentOutput.model_validate({**base, "visual_assets": [_asset(cap + 1)]})
    assert f"content oltre {cap} caratteri ({cap + 1}" in str(err.value)
    assert [(e["type"], e["loc"]) for e in err.value.errors()] == [
        ("value_error", ("visual_assets", 0))
    ]
    assert LessonSlidesOutput.model_validate(_slides_output(_asset(cap))).new_assets
    with pytest.raises(ValidationError) as err:
        LessonSlidesOutput.model_validate(_slides_output(_asset(cap + 1)))
    assert [(e["type"], e["loc"]) for e in err.value.errors()] == [
        ("value_error", ("new_assets", 0))
    ]


_LEGACY_GANTT = "gantt\n  title Piano\n" + "".join(
    f"  Attivita {i:04d} lunga descrizione :a{i}, 2026-01-01, 3d\n" for i in range(260)
)


@pytest.mark.parametrize("model", [LessonContentVisualAsset, LessonSlideNewAsset])
def test_shared_asset_models_accept_a_longer_legacy_source(model: type) -> None:
    """V1-F2: l'editor invia sempre tutti gli asset; lo schema del PATCH non
    rifiuta più un Mermaid storico oltre il tetto (nessun tetto prima di
    WP5, nessun backfill)."""
    assert len(_LEGACY_GANTT) > VISUAL_ASSET_CONTENT_MAX_CHARS
    asset = model.model_validate(_asset(len(_LEGACY_GANTT), "mermaid") | {"content": _LEGACY_GANTT})
    assert asset.content == _LEGACY_GANTT
    legacy = {"asset_id": "g", "format": "mermaid", "content": _LEGACY_GANTT}
    patch = LessonContentUpdateInput(introduction="Refuso corretto", visual_assets=[legacy])
    assert patch.visual_assets is not None
    assert LessonSlidesUpdateInput.model_validate({"new_assets": [legacy]}).new_assets


async def test_patch_gate_caps_only_the_changed_assets() -> None:
    """Il gate del PATCH applica il tetto ai soli asset cambiati, per ogni
    formato (anche non renderizzabile), prima di `validate`."""
    cap = VISUAL_ASSET_CONTENT_MAX_CHARS
    legacy = {"asset_id": "g", "format": "mermaid", "content": _LEGACY_GANTT}
    await frs.validate_visual_assets_or_raise(
        [legacy], previous=[legacy], loc_root="visual_assets", code="x"
    )
    changed = [
        {**legacy, "content": _LEGACY_GANTT + "  Altra :b1, 2026-02-01, 1d\n"},
        {"asset_id": "i", "format": "image", "content": "/" + "p" * cap},
    ]
    with pytest.raises(ValidationAppError) as err:
        await frs.validate_visual_assets_or_raise(
            changed, previous=[legacy], loc_root="visual_assets", code="x"
        )
    errors = err.value.meta["errors"]
    assert [(e["asset_id"], e["type"], e["loc"]) for e in errors] == [
        ("g", frs.FIGURE_INVALID, ["visual_assets", 0, "content"]),
        ("i", frs.FIGURE_INVALID, ["visual_assets", 1, "content"]),
    ]
    assert errors[0]["msg"] == f"contenuto oltre {cap} caratteri ({len(changed[0]['content'])})"


async def test_text_only_patch_on_a_lesson_with_a_long_legacy_figure(seeded_db: Any) -> None:
    """Il caso del docente: un refuso nell'introduzione di una lezione con
    un gantt storico di 14.470 caratteri, salvato dall'editor che invia
    tutti gli asset. Prima di V1-F2: 422 `string_too_long`."""
    course_id, _org, user = await build_course(
        seeded_db, modules=1, lessons_per_module=1, content_status="ready"
    )
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    lesson = find_lesson(course, "M1.L1")
    legacy = {"asset_id": "g", "format": "mermaid", "content": _LEGACY_GANTT, "caption": ""}
    lesson.content_raw = {
        "introduction": "Refuso",
        "sections": [],
        "summary": "s",
        "visual_assets": [legacy],
    }
    await seeded_db.commit()
    payload = LessonContentUpdateInput.model_validate(
        {"introduction": "Refuso corretto", "visual_assets": [legacy]}
    )
    await content_crud.update_lesson_content(
        seeded_db, course=course, lesson=lesson, payload=payload, actor_id=user.id
    )
    raw = lesson.content_raw
    assert raw["introduction"] == "Refuso corretto"
    assert raw["visual_assets"][0]["content"] == _LEGACY_GANTT


def test_a_long_mermaid_source_below_the_cap_reaches_the_renderer_gate() -> None:
    """Fra la soglia editoriale Mermaid e il tetto dello schema il sorgente
    è accettato dallo schema e rifiutato da `validate` con un messaggio
    che il fix AI può usare (non un errore Pydantic sull'intera lezione)."""
    source = _FLOW + "\n%% " + "x" * MAX_MERMAID_SOURCE_CHARS
    asset = LessonContentVisualAsset.model_validate(
        {"asset_id": "A", "format": "mermaid", "content": source}
    )
    ok, msg = frs.REGISTRY["mermaid"].validate(asset.content)
    assert ok is False and msg.startswith("graph_too_dense: caratteri del sorgente")
