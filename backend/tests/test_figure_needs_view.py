"""Vista dei fabbisogni per l'editor e collegamenti del docente (WP9, doc 18 §23.7).

- stati calcolati alla lettura: placed, misplaced, missing, uncovered (con il
  motivo), dismissed; figura collegata a mano; conteggi dell'etichetta;
- PUT dei collegamenti: 422 con `loc` per fabbisogno sconosciuto e figura non
  nella lezione, «Non serve» e ritorno allo stato calcolato, `content_raw` e
  `content_modified_at` invariati; esposto nel DTO della lezione;
- migrazione 0043 aggiunge e toglie la stessa colonna.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_lesson import CourseLesson
from app.services.figure_needs_view import figure_needs_view, summary

F1, F2 = "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002"


def _lesson(**over: Any) -> SimpleNamespace:
    base = {
        "figure_needs_status": "ready",
        "figure_needs": {
            "needs": [
                {"need_id": "n2", "section_id": "S2", "subject": "Scansione", "priority": "must"},
                {"need_id": "n1", "section_id": "S1", "subject": "Base", "priority": "must"},
                {"need_id": "n3", "section_id": "S3", "subject": "Diff", "priority": "should"},
                {"need_id": "n4", "section_id": "S3", "subject": "Rot", "priority": "must"},
            ]
        },
        "figure_assignment": {
            "state": "settled",
            "offers": {"n1": {"figure_id": F1}, "n2": {"figure_id": F2}},
            "unassigned": {"n3": "no_candidate", "n4": "reuse_cap"},
        },
        "figures_gap_stats": {"needs": {"n3": {"status": "not_found"}}},
        "figure_need_links": None,
        "section_outline": [{"section_id": s} for s in ("S1", "S2", "S3")],
        "content_raw": {
            "sections": [
                {"section_id": "S1", "content": "Testo [FIG:SRC-a]."},
                {"section_id": "S2", "content": "Altro."},
                {"section_id": "S3", "content": "Qui [FIG:SRC-b] e [FIG:img-1]."},
            ],
            "visual_assets": [
                {"asset_id": "SRC-a", "format": "source_figure", "content": F1},
                {"asset_id": "SRC-b", "format": "source_figure", "content": F2},
                {"asset_id": "img-1", "format": "image", "content": "x.png"},
            ],
        },
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_states_are_computed_at_read_time() -> None:
    view = figure_needs_view(_lesson())
    assert view is not None
    by = {v["need_id"]: v for v in view}
    assert [v["label"] for v in view] == ["N1", "N2", "N3", "N4"]
    assert [v["need_id"] for v in view] == ["n1", "n2", "n3", "n4"]
    assert by["n1"]["status"] == "placed" and by["n1"]["asset_id"] == "SRC-a"
    assert by["n2"]["status"] == "misplaced" and by["n2"]["cited_in"] == "S3"
    assert by["n3"] == {**by["n3"], "status": "uncovered", "reason": "no_candidate"}
    assert by["n3"]["literature"] == "not_found"
    assert by["n4"]["reason"] == "reuse_cap"
    counts = summary(view)
    assert counts == {
        "needs": 4,
        "musts": 3,
        "musts_placed": 2,
        "uncovered": 2,
        "misplaced": 1,
        "dismissed": 0,
    }


def test_a_removed_figure_is_missing_and_links_win() -> None:
    lesson = _lesson(
        figure_need_links={
            "n4": {"state": "linked", "asset_id": "img-1"},
            "n3": {"state": "dismissed"},
        }
    )
    lesson.content_raw["visual_assets"] = [
        a for a in lesson.content_raw["visual_assets"] if a["asset_id"] != "SRC-a"
    ]
    view = figure_needs_view(lesson)
    assert view is not None
    by = {v["need_id"]: v for v in view}
    assert by["n1"]["status"] == "missing"
    assert by["n3"]["status"] == "dismissed"
    assert by["n4"]["status"] == "placed" and by["n4"]["linked"] is True


def test_no_ready_needs_means_no_view() -> None:
    assert figure_needs_view(_lesson(figure_needs_status="pending")) is None
    assert figure_needs_view(_lesson(figure_needs={"needs": []})) == []


_MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0043_figure_need_links.py"
)


def test_migration_0043_adds_and_drops_the_links() -> None:
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    added, dropped = set(), set()
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if (
                fn.name == "upgrade"
                and isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_column"
            ):
                column = node.args[1]
                assert isinstance(column, ast.Call) and isinstance(column.args[0], ast.Constant)
                added.add(column.args[0].value)
            if fn.name == "downgrade" and isinstance(node, ast.For):
                assert isinstance(node.iter, ast.Tuple)
                dropped |= {e.value for e in node.iter.elts if isinstance(e, ast.Constant)}
    assert added == dropped == {"figure_need_links"}
    assert "figure_need_links" in CourseLesson.__table__.columns


# --- API ------------------------------------------------------------------------------


async def _api_setup(db: AsyncSession) -> dict[str, Any]:
    from app.core.permissions import R
    from app.models.course import Course
    from tests.course_builders import build_course
    from tests.test_permissions import _setup_user_membership

    user, org, _m = await _setup_user_membership(db, role_code=R.MANAGER)
    course_id, _o, _u = await build_course(
        db, modules=1, lessons_per_module=1, content_status="ready"
    )
    course = await db.get(Course, course_id)
    assert course is not None
    course.organization_id = org.id
    course.assignee_user_id = user.id
    lesson = (
        (await db.execute(select(CourseLesson).where(CourseLesson.course_id == course_id)))
        .scalars()
        .one()
    )
    lesson.figure_needs_status = "ready"
    lesson.figure_needs = {
        "needs": [{"need_id": "n1", "section_id": "S1", "subject": "Base", "priority": "must"}]
    }
    lesson.content_raw = {
        "introduction": "x",
        "sections": [{"section_id": "S1", "content": "Testo [FIG:img-1]."}],
        "visual_assets": [{"asset_id": "img-1", "format": "image", "content": "x.png"}],
    }
    await db.commit()
    return {
        "user": user.id,
        "lesson": lesson,
        "url": f"/api/v1/orgs/{org.id}/courses/{course_id}/lessons/{lesson.id}/figure-needs",
    }


def _lesson_out(body: dict[str, Any], lesson_id: Any) -> dict[str, Any]:
    for module in body["modules"]:
        for lesson in module["lessons"]:
            if lesson["id"] == str(lesson_id):
                return lesson
    raise AssertionError("lezione assente")


async def test_links_api(client: AsyncClient, seeded_db: AsyncSession) -> None:
    from tests.test_admin_user_management import _bearer

    s = await _api_setup(seeded_db)
    headers = _bearer(s["user"])
    before = (s["lesson"].content_raw, s["lesson"].content_modified_at)
    res = await client.put(f"{s['url']}/nX", json={"state": "dismissed"}, headers=headers)
    assert res.status_code == 422 and res.json()["code"] == "figure_need_unknown"
    res = await client.put(
        f"{s['url']}/n1", json={"state": "linked", "asset_id": "nope"}, headers=headers
    )
    assert res.status_code == 422
    assert res.json()["meta"]["errors"][0]["loc"] == ["body", "asset_id"]
    res = await client.put(
        f"{s['url']}/n1", json={"state": "linked", "asset_id": "img-1"}, headers=headers
    )
    assert res.status_code == 200, res.text
    out = _lesson_out(res.json(), s["lesson"].id)
    (need,) = out["figure_needs_view"]
    assert need["status"] == "placed" and need["linked"] is True
    assert out["figure_needs_summary"]["musts_placed"] == 1
    res = await client.put(f"{s['url']}/n1", json={"state": "dismissed"}, headers=headers)
    assert _lesson_out(res.json(), s["lesson"].id)["figure_needs_view"][0]["status"] == "dismissed"
    res = await client.put(f"{s['url']}/n1", json={"state": None}, headers=headers)
    out = _lesson_out(res.json(), s["lesson"].id)
    assert out["figure_need_links"] is None
    assert out["figure_needs_view"][0]["status"] == "uncovered"
    fresh = await seeded_db.get(CourseLesson, s["lesson"].id, populate_existing=True)
    assert fresh is not None and (fresh.content_raw, fresh.content_modified_at) == before


async def test_extra_fields_are_rejected(client: AsyncClient, seeded_db: AsyncSession) -> None:
    from tests.test_admin_user_management import _bearer

    s = await _api_setup(seeded_db)
    res = await client.put(
        f"{s['url']}/n1", json={"state": "dismissed", "x": 1}, headers=_bearer(s["user"])
    )
    assert res.status_code == 422


F3 = "33333333-0000-4000-8000-000000000003"


def test_the_snapshot_binding_wins_over_the_offer() -> None:
    """Il modello ha scelto l'alternativa F3 per n1 (verifica WP9): la vista
    segue il legame della fotografia, non la figura offerta."""
    lesson = _lesson()
    lesson.figure_assignment = {
        **lesson.figure_assignment,
        "offers": {"n1": {"figure_id": F1}, "n2": {"figure_id": F2}},
        "alternatives": {"n1": [{"figure_id": F3}]},
        "bound": {"n1": F3, "n2": F2},
    }
    lesson.content_raw["sections"][0]["content"] = "Testo [FIG:SRC-c]."
    lesson.content_raw["visual_assets"][0] = {
        "asset_id": "SRC-c",
        "format": "source_figure",
        "content": F3,
    }
    by = {v["need_id"]: v for v in figure_needs_view(lesson) or []}
    assert by["n1"]["status"] == "placed" and by["n1"]["asset_id"] == "SRC-c"
    # Anche senza `bound` (fotografia di prima): l'alternativa nel contenuto.
    lesson.figure_assignment.pop("bound")
    by = {v["need_id"]: v for v in figure_needs_view(lesson) or []}
    assert by["n1"]["status"] == "placed"


def test_an_uncited_figure_is_not_counted_as_placed() -> None:
    lesson = _lesson(figure_need_links={"n4": {"state": "linked", "asset_id": "img-2"}})
    lesson.content_raw["visual_assets"].append(
        {"asset_id": "img-2", "format": "image", "content": "y.png"}
    )
    view = figure_needs_view(lesson)
    assert view is not None
    by = {v["need_id"]: v for v in view}
    assert by["n4"]["status"] == "missing" and by["n4"]["reason"] == "not_cited"
    assert summary(view)["musts_placed"] == 2  # n1 e n2, non n4


def test_a_figure_cited_inside_an_example_belongs_to_its_section() -> None:
    lesson = _lesson()
    lesson.content_raw["sections"][0]["content"] = "Testo con [EX:ex_1]."
    lesson.content_raw["examples"] = [{"example_id": "ex_1", "content": "Vedi [FIG:SRC-a]."}]
    by = {v["need_id"]: v for v in figure_needs_view(lesson) or []}
    assert by["n1"]["status"] == "placed" and by["n1"]["cited_in"] == "S1"


def test_a_dismissed_must_leaves_the_label_denominator() -> None:
    view = figure_needs_view(_lesson(figure_need_links={"n4": {"state": "dismissed"}}))
    counts = summary(view)
    assert counts is not None
    assert counts["musts"] == 2 and counts["musts_placed"] == 2 and counts["dismissed"] == 1


def _sequence_lesson(**over: Any) -> SimpleNamespace:
    needs = [
        {
            "need_id": "n1",
            "section_id": "S1",
            "subject": "A",
            "priority": "must",
            "sequence_group": "tipologie",
            "sequence_index": 1,
        },
        {
            "need_id": "n2",
            "section_id": "S2",
            "subject": "B",
            "priority": "must",
            "sequence_group": "tipologie",
            "sequence_index": 2,
        },
    ]
    lesson = _lesson(figure_needs={"needs": needs}, **over)
    lesson.content_raw["sections"][1]["content"] = "Scansione [FIG:SRC-b]."
    return lesson


def test_sequences_need_two_distinct_cited_figures() -> None:
    from app.services.figure_needs_view import figure_sequences

    assert figure_sequences(_sequence_lesson()) == [("tipologie", ["SRC-a", "SRC-b"])]
    # Un gruppo con una sola figura nel contenuto: nessuna sequenza.
    lesson = _sequence_lesson()
    lesson.content_raw["sections"][1]["content"] = "Nessuna figura."
    lesson.content_raw["sections"][2]["content"] = "Nessuna figura."
    assert figure_sequences(lesson) == []
    # Lo stesso asset collegato a due voci della sequenza: una sola figura.
    lesson = _sequence_lesson(figure_need_links={"n2": {"state": "linked", "asset_id": "SRC-a"}})
    assert figure_sequences(lesson) == []


def test_sequences_follow_the_plan_switch(monkeypatch: Any) -> None:
    from app.core.config import get_settings
    from app.services import figure_plan_service as plan
    from app.services.figure_needs_view import figure_sequences

    off = get_settings().model_copy(update={"figure_plan_enabled": False})
    monkeypatch.setattr(plan, "get_settings", lambda: off)
    assert figure_sequences(_sequence_lesson()) == []
