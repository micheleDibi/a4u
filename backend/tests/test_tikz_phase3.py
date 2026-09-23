"""Formato `tikz` nella generazione della Fase 3 (WP6.3, senza TeX).

- formati offerti (`phase3_visual_formats`): `tikz` solo con la proposta
  accesa, uno script coperto dai font del preambolo e nessun
  `tikz_unresolved` al tentativo precedente; con `tikz` acceso solo per
  l'editor schema e system prompt sono quelli di sempre;
- messaggio user: blocco `## Formato aggiuntivo: tikz` e clausola del
  conteggio solo quando `tikz` è offerto, altrimenti byte-identico;
- fix (PROMPT 12): una figura `tikz` ha un solo fix, poi
  `AssetFixUnresolvedError(code="tikz_unresolved")`; sandbox occupata o
  motore assente non vanno al fix; gli altri formati mantengono il tetto
  globale;
- worker: dopo un `tikz_unresolved` il tentativo successivo non offre
  `tikz` (né schema né messaggio), quello dopo sì;
- PROMPT 5 e 6: al posto del sorgente TeX le sole etichette dei nodi.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.course import Course
from app.schemas.course_lesson_content import LessonContentOutput
from app.services import asset_validation_service as avs
from app.services import course_lesson_content_service as content_svc
from app.services import course_lesson_content_worker as worker
from app.services import figure_render_service as frs
from app.services import openai_asset_fix_service as fix
from app.services import openai_lesson_content_service as openai_svc
from app.services.figure_compute.tikz_lexer import check
from tests.course_builders import build_course, find_lesson
from tests.test_tikz_validator import CHAIN

ALL = ("mermaid", "vegalite", "dot", "function", "tikz")
WITHOUT = ALL[:-1]


def _offer(monkeypatch: pytest.MonkeyPatch, *, propose: bool) -> None:
    patched = openai_svc.get_settings().model_copy(
        update={"figure_tikz_enabled": True, "figure_tikz_propose_enabled": propose}
    )
    monkeypatch.setattr(openai_svc, "get_settings", lambda: patched)
    monkeypatch.setattr(openai_svc, "available_formats", lambda: ALL)


def test_offered_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_svc, "available_formats", lambda: WITHOUT)
    assert openai_svc.phase3_visual_formats("it") == WITHOUT
    _offer(monkeypatch, propose=False)
    assert openai_svc.phase3_visual_formats("it") == WITHOUT
    _offer(monkeypatch, propose=True)
    for lang in ("it", "en", "el", "ru", "ja", "zh-cn", "ko"):
        assert openai_svc.phase3_visual_formats(lang) == ALL, lang
    for lang in ("ar", "he", "hi", "th"):
        assert openai_svc.phase3_visual_formats(lang) == WITHOUT, lang
    assert openai_svc.phase3_visual_formats("it", withhold_tikz=True) == WITHOUT


class _Capture:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, timeout: float = 0.0) -> _Capture:
        return self

    async def __aenter__(self) -> _Capture:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        self.bodies.append(json)
        return httpx.Response(400, json={"error": {"message": "fermo qui"}})


async def _body(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> dict[str, Any]:
    capture = _Capture()
    monkeypatch.setattr(openai_svc, "get_client", capture)
    with pytest.raises(openai_svc.OpenAILessonContentError):
        await openai_svc.generate_lesson_content(
            user_prompt="x", language_code="it", is_regeneration=False, **kwargs
        )
    return capture.bodies[0]


def _enum(body: dict[str, Any]) -> list[str]:
    props = body["response_format"]["json_schema"]["schema"]["properties"]
    return list(props["visual_assets"]["items"]["properties"]["format"]["enum"])


async def test_schema_and_system_prompt_follow_the_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_svc, "available_formats", lambda: WITHOUT)
    baseline = await _body(monkeypatch)
    # `tikz` acceso solo per l'editor: richiesta identica a prima.
    _offer(monkeypatch, propose=False)
    assert await _body(monkeypatch) == baseline
    # Offerto: cambia solo l'enum dello schema, il system prompt no.
    _offer(monkeypatch, propose=True)
    offered = await _body(monkeypatch)
    assert _enum(offered) == list(ALL)
    assert offered["messages"][0] == baseline["messages"][0]
    # Il worker decide: la lista esplicita vince sul default.
    assert _enum(await _body(monkeypatch, visual_formats=WITHOUT)) == list(WITHOUT)


def test_tikz_block_example_passes_the_lexer() -> None:
    block = content_svc._TIKZ_BLOCK
    assert len(block) <= 1_500
    example = block[block.index("\\begin{tikzpicture}") :].strip()
    check(example, max_chars=8_000)


# ---------------------------------------------------------------------------
# Fix: un solo tentativo per `tikz`
# ---------------------------------------------------------------------------


class _TikzStub:
    fmt = "tikz"

    def __init__(self, error: str) -> None:
        self.error = error
        self.validated: list[str] = []

    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
        self.validated.append(content)
        return (True, "") if content == "valido" else (False, self.error)


@pytest.fixture
def tikz_on(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    async def _no_js(items: list[tuple[str, str]]) -> list[tuple[bool, str]]:
        return [(True, "") for _ in items]

    monkeypatch.setattr(avs, "_validate_js_batch", _no_js)
    monkeypatch.setattr(avs, "available_formats", lambda: ALL)
    calls: list[dict[str, Any]] = []

    async def _fix(**kw: Any) -> tuple[fix.AssetFixOut, dict[str, Any]]:
        calls.append(kw)
        return fix.AssetFixOut(fixed_content="ancora rotto"), {"cost_usd": 0.001}

    monkeypatch.setattr(fix, "fix_asset", _fix)
    return calls


def _slot(kind: str, asset_id: str = "T1") -> Any:
    return avs._Slot(
        id=f"asset:{asset_id}", kind=kind, current="rotto", context="", commit=lambda v: None
    )


async def test_tikz_gets_a_single_fix_then_unresolved(
    monkeypatch: pytest.MonkeyPatch, tikz_on: list[dict[str, Any]]
) -> None:
    monkeypatch.setitem(frs.REGISTRY, "tikz", _TikzStub("tikz_compile_failed: riga 2: x"))
    with pytest.raises(avs.AssetFixUnresolvedError) as excinfo:
        await avs._validate_and_fix([_slot("tikz")], [], language_code="it")
    assert excinfo.value.code == "tikz_unresolved"
    assert [c["kind"] for c in tikz_on] == ["tikz"]
    assert tikz_on[0]["error_message"] == "tikz_compile_failed: riga 2: x"


async def test_other_formats_keep_the_global_cap(
    monkeypatch: pytest.MonkeyPatch, tikz_on: list[dict[str, Any]]
) -> None:
    stub = _TikzStub("vegalite: rotto")
    stub.fmt = "vegalite"
    monkeypatch.setitem(frs.REGISTRY, "vegalite", stub)
    with pytest.raises(avs.AssetFixUnresolvedError) as excinfo:
        await avs._validate_and_fix([_slot("vegalite")], [], language_code="it")
    assert excinfo.value.code is None
    cap = avs.get_settings().asset_fix_max_attempts
    assert len(tikz_on) == cap and cap > 1


@pytest.mark.parametrize("error", ["tikz_busy: coda", "tikz_unavailable: xelatex assente"])
async def test_engine_errors_never_reach_the_fix(
    monkeypatch: pytest.MonkeyPatch, tikz_on: list[dict[str, Any]], error: str
) -> None:
    monkeypatch.setitem(frs.REGISTRY, "tikz", _TikzStub(error))
    with pytest.raises(avs.AssetFixUnresolvedError) as excinfo:
        await avs._validate_and_fix([_slot("tikz")], [], language_code="it")
    assert excinfo.value.code == "tikz_unresolved" and tikz_on == []


def test_tikz_fix_prompt_exists_in_both_languages() -> None:
    it_prompt = fix._system_prompt("tikz", "it")
    en_prompt = fix._system_prompt("tikz", "en")
    assert "UN solo ambiente" in it_prompt and "ONE `tikzpicture`" in en_prompt


# ---------------------------------------------------------------------------
# Worker: l'offerta segue il marcatore `tikz_unresolved`
# ---------------------------------------------------------------------------


def _output() -> LessonContentOutput:
    return LessonContentOutput.model_validate(
        {
            "lesson_id": "M1.L1",
            "lesson_title": "Catene di misura",
            "is_introductory": False,
            "estimated_word_count": 800,
            "introduction": "Introduzione.",
            "sections": [
                {
                    "section_id": "S1",
                    "title": "La catena",
                    "content": "Il principio.\n\n[FIG:T1]\n\nCommento.",
                    "objectives_addressed": ["O1"],
                    "topics_addressed": ["T1"],
                }
            ],
            "summary": "Sintesi.",
            "key_takeaways": ["uno", "due", "tre"],
            "visual_assets": [
                {
                    "asset_id": "T1",
                    "format": "tikz",
                    "content": CHAIN,
                    "caption": "Catena di misura.",
                    "alt_text": "catena",
                }
            ],
            "coverage_check": {
                "objectives_covered": [{"objective": "O1", "covered_in_section_ids": ["S1"]}],
                "topics_covered": [{"topic_id": "T1", "covered_in_section_ids": ["S1"]}],
            },
        }
    )


async def test_worker_withholds_tikz_right_after_unresolved(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch, _engine: Any
) -> None:
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

    _offer(monkeypatch, propose=True)
    calls: list[dict[str, Any]] = []
    outcomes = [avs.AssetFixUnresolvedError("T1 [tikz]: rotto", code="tikz_unresolved")]
    outcomes.append(avs.AssetFixUnresolvedError("A1 [dot]: rotto"))

    async def generate(**kwargs: Any) -> tuple[LessonContentOutput, dict[str, Any]]:
        calls.append(kwargs)
        return _output(), {"model": "gpt-5.5", "total": 10, "cost_usd": 0.1}

    async def validation(out: LessonContentOutput, *, language_code: str) -> Any:
        raise outcomes.pop(0)

    monkeypatch.setattr(
        worker, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    monkeypatch.setattr(openai_svc, "generate_lesson_content", generate)
    monkeypatch.setattr(avs, "validate_and_fix_content_assets", validation)
    worker._TIKZ_WITHHELD.clear()

    for _ in range(3):
        await worker._process_one(lesson_id)

    offered, withheld, again = calls
    assert list(offered["visual_formats"]) == list(ALL)
    assert list(withheld["visual_formats"]) == list(WITHOUT)
    assert list(again["visual_formats"]) == list(ALL)
    assert "## Formato aggiuntivo: tikz" in offered["user_prompt"]
    assert "## Formato aggiuntivo: tikz" not in withheld["user_prompt"]
    # Senza `tikz` il messaggio è quello di prima, byte per byte.
    stripped = (
        offered["user_prompt"]
        .replace(content_svc._TIKZ_BLOCK + "\n", "", 1)
        .replace("\n" + content_svc._TIKZ_COUNT_CLAUSE, "", 1)
    )
    assert stripped == withheld["user_prompt"]
    assert worker._TIKZ_WITHHELD == {}


# ---------------------------------------------------------------------------
# Fase 4 e 5: il sorgente TeX non entra nei prompt
# ---------------------------------------------------------------------------


def test_slides_and_speech_see_only_the_node_labels() -> None:
    from app.services.figure_provenance import prompt_view

    plain = {"visual_assets": [{"asset_id": "m1", "format": "mermaid", "content": "x"}]}
    assert prompt_view(plain) is plain
    raw = {
        "visual_assets": [
            {"asset_id": "t1", "format": "tikz", "content": CHAIN},
            {
                "asset_id": "t2",
                "format": "tikz",
                "content": r"\begin{tikzpicture}\end{tikzpicture}",
            },
            {"asset_id": "m1", "format": "mermaid", "content": "flowchart LR\n a-->b"},
        ]
    }
    view = prompt_view(raw)
    tikz, empty, mermaid = view["visual_assets"]
    assert tikz["content"] == "(schema TikZ; etichette: Sensore; Condizionamento; ADC)"
    assert empty["content"] == "(schema TikZ)"
    assert mermaid["content"] == "flowchart LR\n a-->b"
    assert "\\" not in json.dumps(view["visual_assets"][:2], ensure_ascii=False).replace("\\n", "")
    assert raw["visual_assets"][0]["content"] == CHAIN  # l'originale non cambia


async def test_worker_does_not_offer_tikz_during_an_extraction(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch, _engine: Any
) -> None:
    """La sandbox TeX non gira mentre Docling estrae (`HEAVY_JOB_LOCK`): una
    figura `tikz` resterebbe senza validazione e costerebbe una
    rigenerazione, quindi il tentativo non la offre (verifica WP6)."""
    from app.services.heavy_job_lock import HEAVY_JOB_LOCK

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
    _offer(monkeypatch, propose=True)
    calls: list[dict[str, Any]] = []

    async def generate(**kwargs: Any) -> tuple[LessonContentOutput, dict[str, Any]]:
        calls.append(kwargs)
        raise openai_svc.OpenAILessonContentError(status=500, message="fermo qui")

    monkeypatch.setattr(
        worker, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    monkeypatch.setattr(openai_svc, "generate_lesson_content", generate)
    await HEAVY_JOB_LOCK.acquire()
    try:
        await worker._process_one(lesson_id)
    finally:
        HEAVY_JOB_LOCK.release()
    await worker._process_one(lesson_id)
    during, after = calls
    assert list(during["visual_formats"]) == list(WITHOUT)
    assert list(after["visual_formats"]) == list(ALL)


async def test_user_prompt_default_follows_the_schema_default(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Senza `visual_formats` il messaggio segue lo stesso default dello
    schema (`phase3_visual_formats`): mai uno schema con `tikz` e un
    messaggio senza le sue istruzioni (verifica WP6, script di M7)."""
    course_id, _org, _user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    _offer(monkeypatch, propose=False)
    assert "## Formato aggiuntivo: tikz" not in content_svc.build_user_prompt(course, lesson)
    _offer(monkeypatch, propose=True)
    assert "## Formato aggiuntivo: tikz" in content_svc.build_user_prompt(course, lesson)
