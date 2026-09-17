"""WP6 — revisore AI figura ↔ testo e costo degli asset in `content_tokens`.

D15: dopo il fix, ogni figura valida passa da
`openai_figure_review_service.review_figure` con il testo integrale della
sezione che la cita e la misura di WP5. `coerente` è il verdetto
predefinito; una riscrittura (`correggi`) è accettata solo se supera la
validazione del percorso di generazione, non aumenta nodi e archi, non
cambia tipo, conserva tutti i nodi dell'originale senza isolarne nessuno
e non peggiora la misura (incroci e difetti); altrimenti l'originale resta
byte-identico e la cache SVG intatta. Ogni errore di una chiamata resta
nella sua figura. Kill-switch `figure_review_enabled`: nessuna chiamata
HTTP e nessuna resa.

D16: fix, localizzazione e revisore producono l'usage di
`openai_pricing.build_usage_dict` (con `cost_usd`);
`validate_and_fix_content_assets` ritorna `(output, assets_usage)` e il
worker lo fonde in `content_tokens.assets` / `assets_cost_usd`, che la
dashboard admin somma nella fase `content`. Un annullamento arrivato
durante la validazione degli asset non è sovrascritto da `ready`.

Nessuna chiamata OpenAI reale: i client dei servizi sono sostituiti da
risposte preconfezionate che registrano il corpo inviato.

Oracoli che fallivano prima di WP6: `openai_figure_review_service` non
esisteva, `validate_and_fix_content_assets` ritornava il solo output,
l'usage del fix non aveva `cost_usd` e `content_tokens` non aveva
`assets`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import structlog.testing
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import config
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_content import LessonContentOutput
from app.services import admin_metrics_service
from app.services import asset_validation_service as avs
from app.services import course_lesson_content_service as content_svc
from app.services import course_lesson_content_worker as worker
from app.services import course_lesson_pdf_service as pdf
from app.services import figure_geometry as fg
from app.services import figure_render_service as frs
from app.services import openai_asset_fix_service as fix_service
from app.services import openai_asset_localize_service as localize_service
from app.services import openai_figure_review_service as review
from app.services import openai_lesson_content_service as openai_svc
from app.services.figure_compute.graph_rules import GRAPH_FORMATS, graph_source_metrics
from app.services.figure_compute.vegalite_rules import vegalite_data_metrics
from app.services.openai_pricing import build_usage_dict, estimate_cost_usd
from scripts.check_prompts_md import _RENDERERS
from tests.course_builders import build_course, build_lesson_content_output, find_lesson

_ROOT = Path(__file__).resolve().parents[2]

# Due DOT reali con la stessa densità (4 nodi, 4 archi): `_STAR` ha 0
# incroci, `_K22` (bipartito completo su due strati) ne ha 1. Le misure
# sono ripetute nel test che le usa, così l'oracolo non dipende da un
# numero scritto a mano.
_STAR = "digraph G {\n  a -> x;\n  a -> y;\n  a -> b;\n  b -> y;\n}"
_K22 = "digraph G {\n  a -> x;\n  a -> y;\n  b -> x;\n  b -> y;\n}"
_USAGE = {"prompt_tokens": 3000, "completion_tokens": 300, "total_tokens": 3300}
_COST = estimate_cost_usd(model="gpt-4o-mini", prompt_tokens=3000, completion_tokens=300)


# ---------------------------------------------------------------------------
# Client OpenAI finto
# ---------------------------------------------------------------------------


class _Reply:
    status_code = 200
    text = ""

    def __init__(self, content: dict[str, Any], usage: dict[str, Any]) -> None:
        self._content = content
        self._usage = usage

    def json(self) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": json.dumps(self._content)}}],
            "usage": dict(self._usage),
        }


class _FakeOpenAI:
    """`get_client` finto: registra i corpi inviati e risponde con la coda
    `replies` (l'ultima risposta si ripete)."""

    def __init__(self, *replies: dict[str, Any], usage: dict[str, Any] | None = None) -> None:
        self.replies = list(replies)
        self.usage = usage if usage is not None else _USAGE
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, timeout: float = 600.0) -> Any:
        fake = self

        class _Client:
            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def post(
                self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
            ) -> _Reply:
                assert url == "/chat/completions"
                fake.bodies.append(json or {})
                reply = fake.replies.pop(0) if len(fake.replies) > 1 else fake.replies[0]
                return _Reply(reply, fake.usage)

        return _Client()

    def user_messages(self) -> list[str]:
        return [b["messages"][1]["content"] for b in self.bodies]


def _refuse_client(timeout: float = 600.0) -> Any:
    raise AssertionError("chiamata HTTP a OpenAI con il revisore spento")


def _coherent(reason: str = "La figura segue il testo.") -> dict[str, Any]:
    return {"verdict": "coerente", "reason": reason, "source": None}


def _fix(source: str | None, reason: str = "Riscrittura.") -> dict[str, Any]:
    return {"verdict": "correggi", "reason": reason, "source": source}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    # Il semaforo del revisore è per loop e i test ne condividono uno solo
    # (`asyncio_default_test_loop_scope = "session"`): va rifatto a ogni
    # test, così `figure_review_max_parallel` di `_settings` è effettivo.
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    avs._review_semaphores.clear()
    yield
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    avs._review_semaphores.clear()


def _settings(monkeypatch: pytest.MonkeyPatch, **updates: Any) -> config.Settings:
    """Stesso `Settings` per il validatore e per il servizio del revisore,
    con una chiave finta (il client è sostituito, nessuna rete)."""
    values: dict[str, Any] = {"openai_api_key": "sk-test-finta", **updates}
    settings = config.get_settings().model_copy(update=values)
    monkeypatch.setattr(avs, "get_settings", lambda: settings)
    monkeypatch.setattr(review, "get_settings", lambda: settings)
    return settings


def _review_client(monkeypatch: pytest.MonkeyPatch, *replies: dict[str, Any]) -> _FakeOpenAI:
    fake = _FakeOpenAI(*replies)
    monkeypatch.setattr(review, "get_client", fake)
    return fake


def _output(
    content: str,
    *,
    fmt: str = "dot",
    asset_id: str = "g1",
    section_text: str | None = None,
    **overrides: Any,
) -> LessonContentOutput:
    text = section_text or f"Il grafo mostra i passaggi da a verso x e y.\n\n[FIG:{asset_id}]\n"
    payload: dict[str, Any] = {
        "visual_assets": [
            {
                "asset_id": asset_id,
                "format": fmt,
                "content": content,
                "caption": "Relazioni fra i nodi",
                "alt_text": "Grafo diretto",
            }
        ],
        "sections": [
            ("S1", ["Comprendere l'argomento"], ["T1"]),
            ("S2", ["Comprendere l'argomento"], ["T1"]),
        ],
        **overrides,
    }
    out = build_lesson_content_output(**payload)
    out.sections[1].content = text
    return out


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == name]


def _dot_crossings(source: str) -> int | None:
    renderer = frs.DotRenderer()
    return fg.measure_dot_svg(renderer._render_or_raise(renderer.sanitize(source))).crossings


# ---------------------------------------------------------------------------
# Kill-switch e percorsi senza chiamata
# ---------------------------------------------------------------------------


async def test_kill_switch_off_makes_no_http_call_and_no_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch, figure_review_enabled=False)
    monkeypatch.setattr(review, "get_client", _refuse_client)
    renders: list[Any] = []

    async def _spy_map(assets: Any, *, language: str) -> dict[str, Any]:
        renders.append(assets)
        return {}

    monkeypatch.setattr(avs, "render_figure_map", _spy_map)
    output = _output(_K22)
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert out.visual_assets[0].content == _K22
    assert renders == []
    assert usage == []
    assert _events(logs, "figure_review_verdict") == []


async def test_zero_attempts_is_a_second_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, figure_review_max_attempts=0)
    monkeypatch.setattr(review, "get_client", _refuse_client)
    out, usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
    assert (out.visual_assets[0].content, usage) == (_K22, [])


async def test_without_an_api_key_the_review_is_skipped_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch, openai_api_key=None)
    monkeypatch.setattr(review, "get_client", _refuse_client)

    async def _no_map(assets: Any, *, language: str) -> dict[str, Any]:
        raise AssertionError("resa senza chiave OpenAI")

    monkeypatch.setattr(avs, "render_figure_map", _no_map)
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
    assert (out.visual_assets[0].content, usage) == (_K22, [])
    assert [e["reason"] for e in _events(logs, "figure_review_skipped")] == [
        "openai_not_configured"
    ]


# ---------------------------------------------------------------------------
# Verdetti e accettazione
# ---------------------------------------------------------------------------


async def test_coherent_verdict_keeps_the_source_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    fake = _review_client(monkeypatch, _coherent())
    output = _output(_K22)
    before = output.model_dump()
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert out.model_dump() == before
    assert out.visual_assets[0].content == _K22
    assert len(fake.bodies) == 1
    assert _events(logs, "figure_review_rejected") == []
    (verdict,) = _events(logs, "figure_review_verdict")
    assert (verdict["asset_id"], verdict["verdict"], verdict["accepted"]) == (
        "asset:g1",
        "coerente",
        False,
    )
    assert verdict["reason"] == "La figura segue il testo."
    assert verdict["cost_usd"] == pytest.approx(_COST)
    (entry,) = usage
    assert (entry["phase"], entry["asset_id"], entry["model"]) == (
        "review",
        "asset:g1",
        "gpt-4o-mini",
    )
    assert entry["cost_usd"] == pytest.approx(_COST)
    assert set(build_usage_dict(
        model="gpt-4o-mini", reasoning_effort_setting=None, openai_usage={}, duration_ms=0
    )) <= set(entry)  # fmt: skip
    # Il prompt porta la misura di WP5 dell'originale.
    (message,) = fake.user_messages()
    assert "- nodi: 4; archi: 4" in message
    assert "- incroci fra archi: 1" in message
    assert "- difetti di lettura: nessuno" in message
    assert re.search(r"- corpo minimo del testo nella dispensa: [\d.]+ pt", message)


async def test_invalid_rewrite_is_discarded_and_the_original_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch, figure_review_max_attempts=2)
    # Stessi nodi e archi dell'originale (passa la guardia), graffa di
    # chiusura mancante: lo scarta la validazione profonda.
    unclosed = _K22.removesuffix("}")
    guard = graph_source_metrics("dot", unclosed)
    assert guard is not None and (guard.nodes, guard.edges) == (4, 4)
    fake = _review_client(monkeypatch, _fix(unclosed))
    key = frs.cache_key("dot", frs.DotRenderer().sanitize(_K22))
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
        cached = frs._cache_get_figure(key)
    assert out.visual_assets[0].content == _K22
    rejected = _events(logs, "figure_review_rejected")
    assert [e["attempt"] for e in rejected] == [1, 2]
    assert all(e["reason"].startswith("invalid: ") for e in rejected)
    assert [e["accepted"] for e in _events(logs, "figure_review_verdict")] == [False, False]
    # Il secondo tentativo riceve il motivo del rifiuto.
    first, second = fake.user_messages()
    assert "RISCRITTURA PRECEDENTE RESPINTA" not in first
    assert "RISCRITTURA PRECEDENTE RESPINTA DALLA VALIDAZIONE:\ninvalid: " in second
    assert [u["phase"] for u in usage] == ["review", "review"]
    # La cache dell'originale è quella della validazione, non riscritta.
    assert cached is not None and frs._cache_get_figure(key) is cached


async def test_rewrite_that_adds_crossings_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (_dot_crossings(_STAR), _dot_crossings(_K22)) == (0, 1)
    star, k22 = (graph_source_metrics("dot", s) for s in (_STAR, _K22))
    assert star is not None and k22 is not None
    assert (star.nodes, star.edges) == (k22.nodes, k22.edges)
    frs.clear_svg_cache()
    _settings(monkeypatch, figure_review_max_attempts=1)
    _review_client(monkeypatch, _fix(_K22))
    output = _output(_STAR)
    key = frs.cache_key("dot", _STAR)
    with structlog.testing.capture_logs() as logs:
        out, _usage = await avs.validate_and_fix_content_assets(output, language_code="it")
        cached = frs._cache_get_figure(key)
    assert out.visual_assets[0].content == _STAR
    (rejected,) = _events(logs, "figure_review_rejected")
    assert rejected["reason"] == "crossings: 1 > 0"
    assert (rejected["crossings_before"], rejected["crossings_after"]) == (0, 1)
    assert cached is not None and cached.metrics is not None
    assert cached.metrics.crossings == 0
    assert frs._cache_get_figure(key) is cached


async def test_valid_rewrite_that_does_not_worsen_is_accepted_and_revalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    _review_client(monkeypatch, _fix(f"```dot\n{_STAR}\n```"))
    validated: list[tuple[str, str]] = []
    real_validate = avs._validate_slots

    async def _spy(slots: list[Any]) -> list[Any]:
        validated.extend((s.id, s.current) for s in slots)
        return await real_validate(slots)

    monkeypatch.setattr(avs, "_validate_slots", _spy)
    with structlog.testing.capture_logs() as logs:
        out, _usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
    assert out.visual_assets[0].content == _STAR  # fence tolto da `_sanitize`
    assert ("asset:g1#review", _STAR) in validated
    (verdict,) = _events(logs, "figure_review_verdict")
    assert (verdict["verdict"], verdict["accepted"]) == ("correggi", True)
    assert _events(logs, "figure_review_rejected") == []
    (applied,) = _events(logs, "figure_review_applied")
    assert applied["accepted"] == 1
    # La riscrittura accettata è in cache con la sua geometria.
    fig = frs._cache_get_figure(frs.cache_key("dot", _STAR))
    assert fig is not None and fig.metrics is not None and fig.metrics.crossings == 0


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        pytest.param(None, "missing_source", id="senza-sorgente"),
        pytest.param("digraph G {\n  a -> x; [FIG:g1]\n}", "placeholder", id="placeholder"),
        pytest.param(
            "digraph G {\n  a -> x;\n  a -> y;\n  b -> x;\n  b -> y;\n  x -> y;\n}",
            "density_increased: nodi 4 → 4, archi 4 → 5",
            id="piu-archi",
        ),
        pytest.param(
            "digraph G {\n  a -> x;\n}",
            "nodes_removed: nodi 4 → 2 (mancanti: b, y)",
            id="riduzione-drastica",
        ),
        pytest.param(
            "digraph G {\n  a -> x;\n  a -> y;\n  c -> x;\n  c -> y;\n}",
            "nodes_removed: nodi 4 → 4 (mancanti: b)",
            id="nodo-rinominato",
        ),
        pytest.param(
            "digraph G {\n  b;\n  a -> x;\n  a -> y;\n}",
            "nodes_isolated: senza archi b",
            id="nodo-isolato",
        ),
    ],
)
async def test_deterministic_guards_reject_before_validation(
    monkeypatch: pytest.MonkeyPatch, candidate: str | None, reason: str
) -> None:
    _settings(monkeypatch, figure_review_max_attempts=1)
    _review_client(monkeypatch, _fix(candidate))
    real = avs._validate_slots

    async def _no_validation(slots: list[Any]) -> list[Any]:
        assert all(not s.id.endswith("#review") for s in slots), slots
        return await real(slots)

    monkeypatch.setattr(avs, "_validate_slots", _no_validation)
    with structlog.testing.capture_logs() as logs:
        out, _usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
    assert out.visual_assets[0].content == _K22
    assert [e["reason"] for e in _events(logs, "figure_review_rejected")] == [reason]


# ---------------------------------------------------------------------------
# Conservazione dei dati: Vega-Lite e `function` (nessuna misura geometrica)
# ---------------------------------------------------------------------------


def _bar(values: list[dict[str, Any]], *, xf: str = "a", yf: str = "b") -> str:
    """Spec Vega-Lite valida: mark `bar` e dominio dichiarato sul canale
    quantitativo, con i dati inline."""
    return json.dumps(
        {
            "mark": "bar",
            "data": {"values": values},
            "encoding": {
                "x": {"field": xf, "type": "nominal"},
                "y": {"field": yf, "type": "quantitative", "scale": {"domain": [0, 100]}},
            },
        },
        indent=2,
    )


_ROWS = [{"a": "A", "b": 28}, {"a": "B", "b": 55}, {"a": "C", "b": 43}, {"a": "D", "b": 91}]
_FUNCTION = json.dumps(
    {
        "kind": "function_study",
        "expressions": [{"expr": "x**2 - 1"}],
        "domain": [-3, 3],
        "show": ["zeros"],
    }
)


def _function_spec(**updates: Any) -> str:
    spec = json.loads(_FUNCTION)
    spec.update(updates)
    return json.dumps(spec)


@pytest.mark.parametrize(
    ("fmt", "original", "candidate", "reason"),
    [
        pytest.param(
            "vegalite",
            _bar(_ROWS),
            _bar(_ROWS[:1]),
            "rows_removed: righe 4 → 1",
            id="righe-cancellate",
        ),
        pytest.param(
            "vegalite",
            _bar(_ROWS[:2]),
            _bar([{"z": "Alfa", "w": 99}, {"z": "Beta", "w": 12}], xf="z", yf="w"),
            "fields_removed: campi a, b",
            id="dati-inventati",
        ),
        pytest.param(
            "function",
            _FUNCTION,
            _function_spec(expressions=[{"expr": "x**3 - 1"}]),
            "expressions_changed: espressioni x**2-1",
            id="altra-funzione",
        ),
        pytest.param(
            "function",
            _FUNCTION,
            _function_spec(domain=[-1, 1]),
            "domain_reduced: dominio [-3, 3] → [-1, 1]",
            id="dominio-ristretto",
        ),
    ],
)
async def test_a_rewrite_that_loses_the_data_is_rejected(
    monkeypatch: pytest.MonkeyPatch, fmt: str, original: str, candidate: str, reason: str
) -> None:
    """Senza archi non c'è misura geometrica: al suo posto decide la
    conservazione dei dati. Una riscrittura anche valida che cancella righe,
    sostituisce i campi o riscrive la matematica è respinta, e l'originale
    resta byte-identico."""
    _settings(monkeypatch, figure_review_max_attempts=1)
    _review_client(monkeypatch, _fix(candidate))
    output = _output(original, fmt=fmt, asset_id="v1")
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert out.visual_assets[0].content == original
    assert [e["reason"] for e in _events(logs, "figure_review_rejected")] == [reason]
    assert [(e["outcome"], e["accepted"]) for e in _events(logs, "figure_review_verdict")] == [
        ("rejected", False)
    ]
    assert [u["phase"] for u in usage] == ["review"]


@pytest.mark.parametrize(
    ("fmt", "candidate"),
    [
        pytest.param("vegalite", _bar([*_ROWS[:3], {"a": "D", "b": 79}]), id="valore-corretto"),
        pytest.param(
            "function",
            _function_spec(
                expressions=[{"expr": "x**2 - 1", "label": "f(x)"}],
                show=["zeros", "critical_points"],
            ),
            id="etichette-e-show",
        ),
    ],
)
async def test_a_rewrite_that_keeps_the_data_is_accepted(
    monkeypatch: pytest.MonkeyPatch, fmt: str, candidate: str
) -> None:
    """La guardia è sui conteggi e sui campi, non sull'identità delle righe:
    correggere un valore o le etichette resta una riscrittura legittima."""
    _settings(monkeypatch, figure_review_max_attempts=1)
    _review_client(monkeypatch, _fix(candidate))
    original = _bar(_ROWS) if fmt == "vegalite" else _FUNCTION
    output = _output(original, fmt=fmt, asset_id="v1")
    with structlog.testing.capture_logs() as logs:
        out, _usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert out.visual_assets[0].content == candidate
    assert _events(logs, "figure_review_rejected") == []
    assert [e["accepted"] for e in _events(logs, "figure_review_verdict")] == [True]


def test_the_data_guards_cover_the_formats_without_edges() -> None:
    """Tabella per formato (D2), sequenze contate come le righe e sorgenti
    illeggibili senza misura: decide la validazione."""
    assert set(avs._DATA_GUARDS) == set(frs.RENDERABLE_FORMATS) - set(GRAPH_FORMATS)
    seq = json.dumps({"data": {"sequence": {"start": 0, "stop": 10, "step": 1}}, "mark": "line"})
    flat = json.dumps({"data": {"values": []}, "mark": "line"})
    assert avs._vegalite_guard(seq, flat) == ("rejected", "sequence_removed: sequenze 1 → 0")
    assert avs._vegalite_guard(_bar(_ROWS), "non JSON") == ("", "")
    assert avs._vegalite_guard(_bar(_ROWS), "[1, 2]") == ("", "")
    assert avs._function_guard(_FUNCTION, "{}") == ("", "")
    # Aggiungere righe o campi è ammesso: la guardia è a senso unico.
    assert avs._vegalite_guard(_bar(_ROWS[:2]), _bar(_ROWS)) == ("", "")


def test_vegalite_data_metrics_counts_every_level() -> None:
    """Righe, sequenze e campi di ogni vista della composizione, `datasets`
    compresi (la riscrittura non può nasconderli in un `layer`)."""
    spec = {
        "datasets": {"d": [{"a": 1}, {"a": 2}]},
        "layer": [
            {"data": {"values": [{"a": 1}]}, "encoding": {"x": {"field": "a"}}},
            {
                "data": {"sequence": {"start": 0, "stop": 5, "step": 1}},
                "encoding": {"y": {"field": "b", "sort": {"field": "c"}}},
            },
        ],
    }
    metrics = vegalite_data_metrics(spec)
    assert (metrics.rows, metrics.sequences) == (3, 1)
    assert metrics.fields == frozenset({"a", "b", "c"})
    assert vegalite_data_metrics({}) == vegalite_data_metrics({"mark": "bar"})


def test_graph_metrics_expose_node_and_linked_ids() -> None:
    """Gli id per ogni lettura di `graph_rules`: nominati e, fra questi,
    estremi di almeno un arco; vuoti per i tipi contati senza id."""
    cases: list[tuple[str, str, set[str], set[str]]] = [
        ("dot", "strict digraph { {a b} -> {c d} -> e; f }", set("abcdef"), set("abcde")),
        ("mermaid", "flowchart LR\n  A[Uno] --> B & C\n  D", set("ABCD"), set("ABC")),
        ("mermaid", "block-beta\n  a b\n  a --> b", {"a", "b"}, {"a", "b"}),
        (
            "mermaid",
            "sequenceDiagram\n  participant P as Pippo\n  P->>+Q: ciao",
            {"P", "Q"},
            {"P", "Q"},
        ),
        ("mermaid", "stateDiagram-v2\n  [*] --> S1\n  S2", {"[*]", "S1", "S2"}, {"[*]", "S1"}),
        ("mermaid", "classDiagram\n  A <|-- B\n  class C", set("ABC"), {"A", "B"}),
        ("mermaid", "erDiagram\n  A ||--o{ B : ha\n  C {\n    int x\n  }", set("ABC"), {"A", "B"}),
        ("mermaid", "sankey-beta\nx,y,3\n", {"x", "y"}, {"x", "y"}),
        ("mermaid", "mindmap\n  root\n    A", set(), set()),
        (
            "mermaid",
            "gantt\n  section S\n  T1 :a1, 2024-01-01, 3d\n  T2 :after a1, 2d",
            set(),
            set(),
        ),
    ]
    for kind, source, ids, linked in cases:
        metrics = graph_source_metrics(kind, source)
        assert metrics is not None, source
        assert (set(metrics.node_ids), set(metrics.linked_ids)) == (ids, linked), source
        assert metrics.linked_ids <= metrics.node_ids


def _guard(fmt: str, original: str, candidate: str) -> tuple[str, str]:
    item = avs._ReviewItem(
        asset=None,
        key="asset:g1",
        fmt=fmt,
        original=original,
        context=review.ReviewContext(title="", text="", cited=False),
        measure=review.FigureMeasure(),
    )
    return avs._review_guard(item, candidate)


def test_node_guards_cover_every_graph_kind() -> None:
    """Tipi con id (flowchart, sequence, ER, sankey) e tipi contati senza
    id (pie): i nodi dell'originale restano, nessun nodo collegato resta
    isolato; togliere archi ridondanti resta ammesso."""
    # K4,4 → abbinamento: 12 archi in meno, tutti i nodi ancora collegati.
    assert _guard("mermaid", _CROSSED, _PAIRED) == ("", "")
    assert _guard(
        "mermaid",
        "sequenceDiagram\n  A->>B: uno\n  B->>C: due",
        "sequenceDiagram\n  A->>B: uno",
    ) == ("rejected", "nodes_removed: nodi 3 → 2 (mancanti: C)")
    assert _guard(
        "mermaid",
        "erDiagram\n  A ||--o{ B : ha\n  B ||--o{ C : usa",
        "erDiagram\n  A ||--o{ B : ha\n  C {\n    int x\n  }",
    ) == ("rejected", "nodes_isolated: senza archi C")
    assert _guard(
        "mermaid",
        "sankey-beta\nx,y,3\ny,z,2\n",
        "sankey-beta\nx,y,3\n",
    ) == ("rejected", "nodes_removed: nodi 3 → 2 (mancanti: z)")
    assert _guard(
        "mermaid",
        'pie title Quote\n  "A": 3\n  "B": 2\n  "C": 1',
        'pie title Quote\n  "A": 3\n  "B": 3',
    ) == ("rejected", "nodes_removed: nodi 3 → 2")
    # Il motivo elenca al più `_REVIEW_IDS_SHOWN` id, in ordine.
    many = "digraph G {\n" + "".join(f"  n{i:02d} -> hub;\n" for i in range(12)) + "}"
    kept = "digraph G {\n  n00 -> hub;\n}"
    outcome, reason = _guard("dot", many, kept)
    assert outcome == "rejected"
    assert reason == (
        "nodes_removed: nodi 13 → 2 (mancanti: n01, n02, n03, n04, n05, n06, n07, n08, …)"
    )


async def test_unchanged_rewrite_is_not_a_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    _review_client(monkeypatch, _fix(_K22 + "\n"))
    with structlog.testing.capture_logs() as logs:
        out, _usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
    assert out.visual_assets[0].content == _K22
    (verdict,) = _events(logs, "figure_review_verdict")
    assert (verdict["accepted"], verdict["outcome"]) == (False, "unchanged")
    assert _events(logs, "figure_review_rejected") == []


def _m(crossings: int | None, *defects: str, rendered: bool = True) -> review.FigureMeasure:
    return review.FigureMeasure(
        nodes=4, edges=4, rendered=rendered, crossings=crossings, defects=defects
    )


@pytest.mark.parametrize(
    ("fmt", "original", "candidate", "expected"),
    [
        pytest.param("dot", _m(1), _m(1), (True, ""), id="uguali"),
        pytest.param("dot", _m(3), _m(0), (True, ""), id="meno-incroci"),
        pytest.param("dot", _m(0), _m(1), (False, "crossings: 1 > 0"), id="piu-incroci"),
        pytest.param(
            "dot",
            _m(0),
            _m(0, "labels_overlap: a / b"),
            (False, "new_defects: labels_overlap"),
            id="difetto-nuovo",
        ),
        pytest.param(
            "dot",
            _m(0, "labels_overlap: a / b"),
            _m(0, "labels_overlap: c / d"),
            (True, ""),
            id="stesso-difetto-altre-etichette",
        ),
        pytest.param(
            "mermaid", _m(2), _m(None), (False, "measure_skipped"), id="riscrittura-saltata"
        ),
        pytest.param("mermaid", _m(None), _m(0), (True, ""), id="originale-saltato"),
        pytest.param(
            "mermaid",
            _m(None),
            _m(0, "text_outside_canvas: x"),
            (False, "new_defects: text_outside_canvas"),
            id="originale-saltato-difetto",
        ),
        pytest.param("dot", _m(None), _m(None), (False, "measure_skipped"), id="entrambe-saltate"),
        pytest.param("mermaid", _m(1), None, (False, "measure_unavailable"), id="non-resa"),
        pytest.param("mermaid", None, _m(0), (True, ""), id="originale-non-reso"),
        pytest.param("vegalite", None, None, (True, ""), id="senza-misura"),
        pytest.param("function", _m(None), _m(None), (True, ""), id="function"),
    ],
)
def test_acceptance_rule(
    fmt: str,
    original: review.FigureMeasure | None,
    candidate: review.FigureMeasure | None,
    expected: tuple[bool, str],
) -> None:
    assert avs.review_acceptance(fmt, original, candidate) == expected


async def test_mermaid_rewrite_without_chromium_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chromium assente a runtime: il parse degrada a pass-through, ma la
    riscrittura non ha misura e non è mai accettata."""
    original = "flowchart LR\n  A --> B\n  B --> C"
    candidate = "flowchart LR\n  A --> C\n  B --> C"
    _settings(monkeypatch, figure_review_max_attempts=1)
    _review_client(monkeypatch, _fix(candidate))

    async def _no_js(items: list[tuple[str, str]]) -> None:
        return None

    def _no_chromium(codes: list[str]) -> list[Any]:
        raise RuntimeError("chromium non disponibile")

    monkeypatch.setattr(avs, "_validate_js_batch", _no_js)
    monkeypatch.setattr(frs, "_prerender_mermaid_batch_sync", _no_chromium)
    with structlog.testing.capture_logs() as logs:
        out, _usage = await avs.validate_and_fix_content_assets(
            _output(original, fmt="mermaid"), language_code="it"
        )
    assert out.visual_assets[0].content == original
    assert [e["reason"] for e in _events(logs, "figure_review_rejected")] == ["measure_unavailable"]


async def test_mermaid_type_change_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    original = "flowchart LR\n  A --> B\n  B --> C"
    _settings(monkeypatch, figure_review_max_attempts=1)
    _review_client(monkeypatch, _fix("mindmap\n  root\n    A\n    B"))

    async def _ok_js(items: list[tuple[str, str]]) -> list[tuple[bool, str]]:
        return [(True, "")] * len(items)

    rendered: list[list[str]] = []

    def _fake_batch(codes: list[str]) -> list[Any]:
        rendered.append(list(codes))
        return [None for _ in codes]

    monkeypatch.setattr(avs, "_validate_js_batch", _ok_js)
    monkeypatch.setattr(frs, "_prerender_mermaid_batch_sync", _fake_batch)
    with structlog.testing.capture_logs() as logs:
        out, _usage = await avs.validate_and_fix_content_assets(
            _output(original, fmt="mermaid"), language_code="it"
        )
    assert out.visual_assets[0].content == original
    assert [e["reason"] for e in _events(logs, "figure_review_rejected")] == [
        "type_changed: flowchart → mindmap"
    ]
    assert rendered == [[original]]  # solo la misura dell'originale per il prompt


_CROSSED = "flowchart TD\n" + "\n".join(
    f"  {s} --> {t}" for s in ("A", "B", "C", "D") for t in ("W", "X", "Y", "Z")
)
_PAIRED = "flowchart TD\n" + "\n".join(
    f"  {s} --> {t}" for s, t in zip("ABCD", "WXYZ", strict=True)
)


async def test_mermaid_rewrite_is_measured_in_chromium_with_the_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Percorso reale (Chromium e CDN): l'originale è reso una volta per il
    prompt; la decisione legge originale e riscrittura dalla stessa
    `render_figure_map`, dove l'originale è un hit della cache."""
    _settings(monkeypatch)
    fake = _review_client(monkeypatch, _fix(_PAIRED))
    batches: list[list[str]] = []
    maps: list[list[str]] = []
    speculative: list[bool] = []
    real_batch = frs._prerender_mermaid_batch_sync
    real_map = avs.render_figure_map

    def _spy_batch(codes: list[str]) -> list[Any]:
        batches.append(list(codes))
        return real_batch(codes)

    async def _spy_map(
        assets: Any, *, language: str, cache_failures: bool = True
    ) -> dict[str, Any]:
        maps.append([str(a["asset_id"]) for a in assets])
        speculative.append(cache_failures)
        return await real_map(assets, language=language, cache_failures=cache_failures)

    monkeypatch.setattr(frs, "_prerender_mermaid_batch_sync", _spy_batch)
    monkeypatch.setattr(avs, "render_figure_map", _spy_map)
    started = time.monotonic()
    with structlog.testing.capture_logs() as logs:
        out, _usage = await avs.validate_and_fix_content_assets(
            _output(_CROSSED, fmt="mermaid"), language_code="it"
        )
    elapsed = time.monotonic() - started
    assert out.visual_assets[0].content == _PAIRED, _events(logs, "figure_review_rejected")
    assert maps == [["asset:g1"], ["asset:g1", "asset:g1#review"]]
    assert batches == [[_CROSSED], [_PAIRED]]
    # Rese per misurare, non per pubblicare: un guasto non deve mettere in
    # cache negativa le figure originali dell'export.
    assert speculative == [False, False]
    (message,) = fake.user_messages()
    found = re.search(r"- incroci fra archi: (\d+)", message)
    assert found is not None and int(found.group(1)) > 0
    measured = _events(logs, "figure_review_measured")
    assert [e["stage"] for e in measured] == ["original", "candidates"]
    assert all(e["duration_ms"] >= 0 for e in measured)
    assert elapsed < 120


# ---------------------------------------------------------------------------
# Contesto: la sezione che cita la figura
# ---------------------------------------------------------------------------


async def test_context_is_the_first_citing_section_in_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    fake = _review_client(monkeypatch, _coherent())
    filler = "Il nodo a precede x e y secondo la relazione d'ordine della sezione. " * 30
    closing = "Frase finale della sezione che il revisore deve leggere."
    section = f"{filler}\n\n[FIG:G1]\n\n{closing}"
    assert len(section) > 1_500
    output = _output(_K22, section_text=section)
    output.summary = "La sintesi cita di nuovo [FIG:g1] ma non è la prima sezione."
    await avs.validate_and_fix_content_assets(output, language_code="it")
    (message,) = fake.user_messages()
    assert "SEZIONE CHE CITA LA FIGURA: Sezione S2" in message
    assert section.strip() in message
    assert closing in message
    assert "La sintesi cita di nuovo" not in message
    assert "Introduzione della lezione." not in message
    assert "DIDASCALIA: Relazioni fra i nodi" in message
    assert message.rstrip().endswith(_K22)


async def test_context_covers_examples_and_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una figura citata solo in un esempio o in una tabella è citata: il
    revisore riceve quel testo, non il corpo di ripiego (finding V3-02)."""
    for kind in ("examples", "tables"):
        _settings(monkeypatch)
        fake = _review_client(monkeypatch, _coherent())
        spia = "La frase che cita il grafo [FIG:g1] e lo commenta."
        extra: dict[str, Any] = (
            {"examples": [{"example_id": "ex_1", "title": "Caso pratico", "content": spia}]}
            if kind == "examples"
            else {"tables": [{"table_id": "tab_1", "caption": "Confronto", "markdown": spia}]}
        )
        output = _output(_K22, section_text="Sezione senza tag.\n", **extra)
        await avs.validate_and_fix_content_assets(output, language_code="it")
        (message,) = fake.user_messages()
        assert "LA FIGURA NON E' CITATA NEL TESTO" not in message, kind
        assert spia in message, kind
        assert "SEZIONE CHE CITA LA FIGURA: " in message, kind


async def test_uncited_figure_gets_the_lesson_body_within_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    fake = _review_client(monkeypatch, _coherent())
    long_text = "Parola " * (review.BODY_MAX_CHARS // 3)
    output = _output(_K22, section_text=long_text)
    await avs.validate_and_fix_content_assets(output, language_code="en")
    (message,) = fake.user_messages()
    assert "LA FIGURA NON E' CITATA NEL TESTO" in message
    assert "Introduzione della lezione." in message
    body = message.split("caratteri):\n", 1)[1].split("\n\nFIGURA DA REVISIONARE:", 1)[0]
    assert len(body) <= review.BODY_MAX_CHARS + len("\n[…]")
    assert body.endswith("[…]")
    assert "LINGUA DEL CORSO: en" in message
    (body_sent,) = fake.bodies
    assert body_sent["messages"][0]["content"] == review._SYSTEM_PROMPTS["en"]


def test_section_cap_is_declared_and_applied() -> None:
    context = review.ReviewContext(
        title="S", text="x" * (review.SECTION_MAX_CHARS + 50), cited=True
    )
    message = review.build_user_message(
        fmt="dot",
        source=_K22,
        caption="",
        alt_text="",
        context=context,
        measure=review.FigureMeasure(),
        language_code="it",
    )
    assert "x" * review.SECTION_MAX_CHARS + "\n[…]" in message
    assert "x" * (review.SECTION_MAX_CHARS + 1) not in message
    assert review.SECTION_MAX_CHARS > 600


# ---------------------------------------------------------------------------
# Errori: la revisione non fa mai fallire la lezione
# ---------------------------------------------------------------------------


async def test_a_failing_call_keeps_the_original(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)

    class _Broken:
        def __call__(self, timeout: float = 600.0) -> Any:
            import httpx

            raise httpx.ConnectError("rete giù")

    monkeypatch.setattr(review, "get_client", _Broken())
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
    assert (out.visual_assets[0].content, usage) == (_K22, [])
    assert len(_events(logs, "figure_review_call_failed")) == 2  # due tentativi


class _RoutedOpenAI:
    """`get_client` finto che risponde per figura (l'ultima riga del
    messaggio user è il sorgente): `handlers[sorgente](body)` è una
    coroutine che ritorna la risposta o solleva."""

    def __init__(self, handlers: dict[str, Any]) -> None:
        self.handlers = handlers
        self.sources: list[str] = []

    def __call__(self, timeout: float = 600.0) -> Any:
        fake = self

        class _Client:
            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def post(
                self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
            ) -> Any:
                user = (json or {})["messages"][1]["content"]
                source = user.split("FIGURA DA REVISIONARE:\n", 1)[1]
                fake.sources.append(source)
                return await fake.handlers[source]()

        return _Client()


class _NotJson:
    status_code = 200
    text = "<html>proxy</html>"

    def json(self) -> Any:
        return json.loads(self.text)


def _two_figures() -> tuple[LessonContentOutput, str, str]:
    """Due figure DOT distinte con 1 incrocio ciascuna (g1, g2); la
    riscrittura a 0 incroci di g2 è `_STAR` con lo stesso nome del grafo."""
    k22_h = _K22.replace("G {", "H {")
    output = _output(_K22)
    second = output.visual_assets[0].model_copy(update={"asset_id": "g2", "content": k22_h})
    output.visual_assets.append(second)
    output.sections[0].content = "Il secondo grafo.\n\n[FIG:g2]\n"
    return output, k22_h, _STAR.replace("G {", "H {")


async def test_a_non_json_body_is_a_lost_attempt_of_that_figure_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un 200 con un corpo non JSON è un errore della chiamata: il giro
    prosegue, la riscrittura di g2 è accettata e il suo costo contato."""
    _settings(monkeypatch)
    output, k22_h, star_h = _two_figures()

    async def _broken() -> Any:
        return _NotJson()

    async def _rewrite() -> Any:
        return _Reply(_fix(star_h), _USAGE)

    fake = _RoutedOpenAI({_K22: _broken, k22_h: _rewrite})
    monkeypatch.setattr(review, "get_client", fake)
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert [a.content for a in out.visual_assets] == [_K22, star_h]
    assert _events(logs, "figure_review_failed") == []
    failed = _events(logs, "figure_review_call_failed")
    assert [(e["asset_id"], e["attempt"]) for e in failed] == [("asset:g1", 1), ("asset:g1", 2)]
    assert all("Corpo della risposta OpenAI non JSON" in e["error"] for e in failed)
    assert [(u["phase"], u["asset_id"]) for u in usage] == [("review", "asset:g2")]
    assert usage[0]["cost_usd"] == pytest.approx(_COST)
    assert fake.sources == [_K22, k22_h, _K22]


async def test_review_figure_wraps_a_non_json_body() -> None:
    settings = config.get_settings().model_copy(update={"openai_api_key": "sk-test-finta"})

    async def _broken() -> Any:
        return _NotJson()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review, "get_settings", lambda: settings)
        mp.setattr(review, "get_client", _RoutedOpenAI({_K22: _broken}))
        with pytest.raises(review.OpenAIFigureReviewError) as info:
            await review.review_figure(
                fmt="dot",
                source=_K22,
                caption="",
                alt_text="",
                context=review.ReviewContext(title="", text="", cited=False),
                measure=review.FigureMeasure(),
                language_code="it",
            )
    assert info.value.status == 200
    assert "non JSON" in str(info.value)


async def test_an_unexpected_call_error_leaves_no_call_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un'eccezione fuori da `OpenAIError` in una chiamata non chiude il
    giro: la chiamata sorella, più lenta, finisce prima del ritorno della
    validazione e il suo usage è contato."""
    _settings(monkeypatch, figure_review_max_attempts=1)
    output, k22_h, _star_h = _two_figures()
    state = {"g2_done": False}

    async def _boom() -> Any:
        await asyncio.sleep(0.01)
        raise RuntimeError("guasto del client")

    async def _slow() -> Any:
        await asyncio.sleep(0.3)
        state["g2_done"] = True
        return _Reply(_coherent(), _USAGE)

    monkeypatch.setattr(review, "get_client", _RoutedOpenAI({_K22: _boom, k22_h: _slow}))
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert state["g2_done"] is True
    assert [a.content for a in out.visual_assets] == [_K22, k22_h]
    assert _events(logs, "figure_review_failed") == []
    assert [(e["asset_id"], e["error"]) for e in _events(logs, "figure_review_call_failed")] == [
        ("asset:g1", "RuntimeError: guasto del client")
    ]
    assert [(u["phase"], u["asset_id"]) for u in usage] == [("review", "asset:g2")]


async def test_an_unexpected_error_in_the_stage_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    _review_client(monkeypatch, _fix(_STAR))
    real = avs._validate_slots

    async def _boom(slots: list[Any]) -> list[Any]:
        if any(s.id.endswith("#review") for s in slots):
            raise RuntimeError("guasto imprevisto")
        return await real(slots)

    monkeypatch.setattr(avs, "_validate_slots", _boom)
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
    assert out.visual_assets[0].content == _K22
    assert [e["error"] for e in _events(logs, "figure_review_failed")] == [
        "RuntimeError: guasto imprevisto"
    ]
    assert [u["phase"] for u in usage] == ["review"]  # la chiamata pagata resta contata


# ---------------------------------------------------------------------------
# D16: usage con `cost_usd` per fix e localizzazione, fusione nel worker
# ---------------------------------------------------------------------------


async def test_fix_usage_comes_from_build_usage_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeOpenAI({"fixed_content": "x^2", "notes": ""})
    monkeypatch.setattr(fix_service, "get_client", fake)
    _out, usage = await fix_service.fix_asset(
        kind="latex", source="x^", error_message="e", language_code="it"
    )
    assert usage["cost_usd"] == pytest.approx(_COST)
    assert usage["model"] == config.get_settings().openai_asset_fix_model
    assert {"prompt", "completion", "total", "duration_ms", "reasoning_effort"} <= set(usage)
    assert (usage["prompt"], usage["completion"], usage["total"]) == (3000, 300, 3300)


async def test_localize_usage_comes_from_build_usage_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeOpenAI({"k": "日本語のキャプション"})
    monkeypatch.setattr(localize_service, "get_client", fake)
    out, usage = await localize_service.localize_texts(
        items={"k": "Didascalia"}, language_code="ja"
    )
    assert out == {"k": "日本語のキャプション"}
    assert usage["cost_usd"] == pytest.approx(_COST)
    assert usage["reasoning_effort"] is None
    assert "duration_ms" in usage


async def test_validation_collects_fix_and_localize_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch, figure_review_enabled=False)
    monkeypatch.setattr(
        fix_service, "get_client", _FakeOpenAI({"fixed_content": _K22, "notes": ""})
    )
    monkeypatch.setattr(localize_service, "get_client", _FakeOpenAI({"va.0.caption": "図の説明"}))
    output = _output("digraph G {\n  a -> x;\n")
    out, usage = await avs.validate_and_fix_content_assets(output, language_code="ja")
    assert out.visual_assets[0].content == _K22
    assert out.visual_assets[0].caption == "図の説明"
    assert [(u["phase"], u["asset_id"]) for u in usage] == [
        ("fix", "asset:g1"),
        ("localize", None),
    ]
    assert usage[1]["fields"] >= 1
    assert all(u["cost_usd"] == pytest.approx(_COST) for u in usage)


def test_merge_assets_usage_keeps_the_main_call() -> None:
    main = {"model": "gpt-5.5", "total": 10, "cost_usd": 0.25}
    assets = [
        {"phase": "fix", "asset_id": "asset:a", "cost_usd": 0.001},
        {"phase": "review", "asset_id": "asset:a", "cost_usd": 0.002},
        {"phase": "localize", "asset_id": None, "cost_usd": None},
    ]
    merged = avs.merge_assets_usage(main, assets)
    assert merged["cost_usd"] == 0.25 and merged["total"] == 10
    assert merged["assets"] == assets
    assert merged["assets_cost_usd"] == pytest.approx(0.003)
    assert "assets" not in main
    assert avs.merge_assets_usage(main, [])["assets_cost_usd"] == 0.0


async def test_worker_persists_review_and_fix_cost_in_content_tokens(
    seeded_db: Any,
    _engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Giro completo di `_process_one`: Fase 3 finta, un DOT invalido
    riparato dal fix e poi revisionato; `content_tokens` porta l'usage della
    chiamata principale, `assets` e `assets_cost_usd`. La dashboard admin
    somma nella fase `content` la chiamata principale e gli asset."""
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

    output = _output("digraph G {\n  a -> x;\n", asset_id="g9")
    main_usage = build_usage_dict(
        model="gpt-5.5",
        reasoning_effort_setting="high",
        openai_usage={"prompt_tokens": 1000, "completion_tokens": 2000, "total_tokens": 3000},
        duration_ms=1,
    )

    async def _generate(**_kwargs: Any) -> tuple[LessonContentOutput, dict[str, Any]]:
        return output, dict(main_usage)

    admin_metrics_service.invalidate_cache()
    before = await admin_metrics_service.get_admin_metrics(seeded_db)
    _settings(monkeypatch)
    review_fake = _review_client(monkeypatch, _coherent())
    monkeypatch.setattr(
        fix_service, "get_client", _FakeOpenAI({"fixed_content": _K22, "notes": "graffa"})
    )
    monkeypatch.setattr(
        worker, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    monkeypatch.setattr(openai_svc, "generate_lesson_content", _generate)

    with structlog.testing.capture_logs() as logs:
        await worker._process_one(lesson_id)

    row = (
        await seeded_db.execute(
            select(
                CourseLesson.content_status, CourseLesson.content_raw, CourseLesson.content_tokens
            ).where(CourseLesson.id == lesson_id)
        )
    ).one()
    assert row.content_status == "ready", [e for e in logs if e["log_level"] != "info"]
    assert row.content_raw["visual_assets"][0]["content"] == _K22
    tokens = row.content_tokens
    assert tokens["cost_usd"] == pytest.approx(main_usage["cost_usd"])
    assert tokens["model"] == "gpt-5.5"
    assert [(a["phase"], a["asset_id"]) for a in tokens["assets"]] == [
        ("fix", "asset:g9"),
        ("review", "asset:g9"),
    ]
    assert all(a["cost_usd"] == pytest.approx(_COST) for a in tokens["assets"])
    assert tokens["assets_cost_usd"] == pytest.approx(2 * _COST)
    assert len(review_fake.bodies) == 1

    admin_metrics_service.invalidate_cache()
    after = await admin_metrics_service.get_admin_metrics(seeded_db)
    phase = {p.phase: p.cost_usd for p in after.cost.by_phase}
    phase_before = {p.phase: p.cost_usd for p in before.cost.by_phase}
    assert phase["content"] - phase_before["content"] == pytest.approx(
        main_usage["cost_usd"] + 2 * _COST
    )
    assert after.cost.total_usd - before.cost.total_usd == pytest.approx(
        main_usage["cost_usd"] + 2 * _COST
    )
    admin_metrics_service.invalidate_cache()


async def test_admin_dashboard_counts_asset_cost_even_without_the_main_cost(
    seeded_db: Any,
) -> None:
    """`assets_cost_usd` entra nella fase `content` anche se `cost_usd`
    manca (modello fuori listino) e nelle finestre a 7 e 30 giorni; le
    altre fasi restano sul solo `cost_usd`."""
    course_id, _org, _user = await build_course(
        seeded_db, modules=1, lessons_per_module=2, content_status="ready"
    )
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    first, second = find_lesson(course, "M1.L1").id, find_lesson(course, "M1.L2").id
    admin_metrics_service.invalidate_cache()
    before = await admin_metrics_service.get_admin_metrics(seeded_db)
    stamp = datetime.now(UTC)
    tokens = {
        first: {"cost_usd": None, "assets": [], "assets_cost_usd": 0.5},
        second: {"cost_usd": 0.25, "assets_cost_usd": 0.125},
    }
    for lesson_id, value in tokens.items():
        await seeded_db.execute(
            update(CourseLesson)
            .where(CourseLesson.id == lesson_id)
            .values(
                content_tokens=value,
                content_generated_at=stamp,
                slides_tokens={"cost_usd": 0.0625, "assets_cost_usd": 100.0},
                slides_generated_at=stamp,
            )
        )
    await seeded_db.commit()
    admin_metrics_service.invalidate_cache()
    after = await admin_metrics_service.get_admin_metrics(seeded_db)
    admin_metrics_service.invalidate_cache()
    delta = {
        p.phase: p.cost_usd - q.cost_usd
        for p, q in zip(after.cost.by_phase, before.cost.by_phase, strict=True)
    }
    assert delta["content"] == pytest.approx(0.875)
    assert delta["slides"] == pytest.approx(0.125)
    assert after.cost.last_7d_usd - before.cost.last_7d_usd == pytest.approx(1.0)
    assert after.cost.last_30d_usd - before.cost.last_30d_usd == pytest.approx(1.0)


async def test_a_cancel_during_asset_validation_is_not_overwritten(
    seeded_db: Any,
    _engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'utente annulla mentre gli asset sono in validazione: il worker
    rilegge lo status prima di materializzare e scarta il risultato."""
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
    output = _output(_K22)
    main_usage = build_usage_dict(
        model="gpt-5.5",
        reasoning_effort_setting="high",
        openai_usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        duration_ms=1,
    )
    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _generate(**_kwargs: Any) -> tuple[LessonContentOutput, dict[str, Any]]:
        return output, dict(main_usage)

    async def _validate_then_cancel(
        out: LessonContentOutput, *, language_code: str
    ) -> tuple[LessonContentOutput, list[dict[str, Any]]]:
        async with factory() as other:
            await other.execute(
                update(CourseLesson)
                .where(CourseLesson.id == lesson_id)
                .values(
                    content_status="failed",
                    content_error="Generazione annullata dall'utente.",
                    content_progress=0,
                    content_progress_phase=None,
                )
            )
            await other.commit()
        return out, [{"phase": "review", "asset_id": "asset:g1", "cost_usd": _COST}]

    materialized: list[Any] = []

    async def _no_materialize(*_a: Any, **_k: Any) -> None:
        materialized.append(_k)

    monkeypatch.setattr(worker, "async_session_factory", factory)
    monkeypatch.setattr(openai_svc, "generate_lesson_content", _generate)
    monkeypatch.setattr(avs, "validate_and_fix_content_assets", _validate_then_cancel)
    monkeypatch.setattr(content_svc, "materialize_lesson_content", _no_materialize)
    with structlog.testing.capture_logs() as logs:
        await worker._process_one(lesson_id)
    row = (
        await seeded_db.execute(
            select(
                CourseLesson.content_status,
                CourseLesson.content_progress,
                CourseLesson.content_progress_phase,
                CourseLesson.content_raw,
            ).where(CourseLesson.id == lesson_id)
        )
    ).one()
    assert materialized == []
    assert tuple(row) == ("failed", 0, None, None)
    (cancelled,) = _events(logs, "lesson_content_cancelled_post_assets")
    assert cancelled["current_status"] == "failed"
    # Niente riga in `content_tokens` per una lezione annullata, ma il costo
    # già speso resta visibile nel log dell'annullamento.
    assert (cancelled["assets_calls"], cancelled["assets_cost_usd"]) == (1, pytest.approx(_COST))
    assert cancelled["cost_usd"] == pytest.approx(main_usage["cost_usd"])


async def test_the_cost_of_an_unresolved_validation_stays_in_the_logs(
    seeded_db: Any,
    _engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asset non risolti: la lezione viene rigenerata e nulla si
    materializza, ma le chiamate già pagate viaggiano con l'errore e il
    worker le logga invece di perderle."""
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
    spent = [
        {"phase": "fix", "asset_id": "asset:g1", "cost_usd": _COST},
        {"phase": "fix", "asset_id": "asset:g1", "cost_usd": None},
    ]

    async def _generate(**_kwargs: Any) -> tuple[LessonContentOutput, dict[str, Any]]:
        return _output(_K22), {"model": "gpt-5.5", "total": 30, "cost_usd": 0.1}

    async def _unresolved(
        out: LessonContentOutput, *, language_code: str
    ) -> tuple[LessonContentOutput, list[dict[str, Any]]]:
        raise avs.AssetFixUnresolvedError("asset:g1 [dot]: rotto", assets_usage=spent)

    monkeypatch.setattr(
        worker, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    monkeypatch.setattr(openai_svc, "generate_lesson_content", _generate)
    monkeypatch.setattr(avs, "validate_and_fix_content_assets", _unresolved)
    with structlog.testing.capture_logs() as logs:
        await worker._process_one(lesson_id)
    row = (
        await seeded_db.execute(
            select(CourseLesson.content_status, CourseLesson.content_raw).where(
                CourseLesson.id == lesson_id
            )
        )
    ).one()
    assert row.content_raw is None and row.content_status != "ready"
    (discarded,) = _events(logs, "lesson_content_assets_cost_discarded")
    assert (discarded["calls"], discarded["reason"]) == (2, "asset_validation_failed")
    assert discarded["assets_cost_usd"] == pytest.approx(_COST)


# ---------------------------------------------------------------------------
# Costo delle chiamate pagate senza risultato e tetto della concorrenza
# ---------------------------------------------------------------------------


class _PaidButUnusable:
    """`get_client` finto: 200 con JSON troncato (`finish_reason=length`),
    token conteggiati e nessun output leggibile."""

    def __init__(self, content: str = '{"verdict": "correggi", "sour') -> None:
        self.content = content
        self.calls = 0

    def __call__(self, timeout: float = 600.0) -> Any:
        fake = self

        class _Truncated:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {
                    "choices": [{"message": {"content": fake.content}, "finish_reason": "length"}],
                    "usage": dict(_USAGE),
                }

        class _Client:
            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def post(
                self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
            ) -> _Truncated:
                fake.calls += 1
                return _Truncated()

        return _Client()


async def test_review_figure_hands_the_usage_of_a_paid_call_to_the_error() -> None:
    """Il servizio non perde l'usage di una risposta 200 inutilizzabile:
    l'eccezione lo porta con sé (`OpenAIError.usage`)."""
    fake = _PaidButUnusable()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review, "get_client", fake)
        with pytest.raises(review.OpenAIFigureReviewError) as err:
            await review.review_figure(
                fmt="dot",
                source=_K22,
                caption="",
                alt_text="",
                context=review.ReviewContext(title="S", text="testo", cited=True),
                measure=review.FigureMeasure(),
                language_code="it",
            )
    usage = err.value.usage
    assert usage is not None and usage["cost_usd"] == pytest.approx(_COST)
    assert (usage["prompt"], usage["completion"], usage["total"]) == (3000, 300, 3300)


async def test_a_paid_review_call_without_a_verdict_still_counts_its_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tetto `max_completion_tokens` che tronca la risposta: tentativo perso
    e originale intatto, ma i token pagati entrano in `assets_usage` (D16)."""
    _settings(monkeypatch, figure_review_max_attempts=1)
    fake = _PaidButUnusable()
    monkeypatch.setattr(review, "get_client", fake)
    with structlog.testing.capture_logs() as logs:
        out, usage = await avs.validate_and_fix_content_assets(_output(_K22), language_code="it")
    assert (out.visual_assets[0].content, fake.calls) == (_K22, 1)
    assert [(u["phase"], u["asset_id"]) for u in usage] == [("review", "asset:g1")]
    assert usage[0]["cost_usd"] == pytest.approx(_COST)
    (failed,) = _events(logs, "figure_review_call_failed")
    assert failed["cost_usd"] == pytest.approx(_COST)
    assert avs.merge_assets_usage({}, usage)["assets_cost_usd"] == pytest.approx(_COST)


async def test_a_paid_fix_call_without_output_travels_with_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stesso buco chiuso nel gemello del fix: i tentativi già pagati
    viaggiano con `AssetFixUnresolvedError`, che il worker logga prima di
    far rigenerare la lezione."""
    _settings(monkeypatch, asset_fix_max_attempts=2)
    fake = _PaidButUnusable('{"fixed_content": "digraph G {')
    monkeypatch.setattr(fix_service, "get_client", fake)
    with pytest.raises(avs.AssetFixUnresolvedError) as err:
        await avs.validate_and_fix_content_assets(
            _output("digraph G {\n  a -> x;\n"), language_code="it"
        )
    spent = err.value.assets_usage
    assert [(u["phase"], u["asset_id"]) for u in spent] == [("fix", "asset:g1")] * 2
    assert fake.calls == 2
    assert avs.assets_cost_usd(spent) == pytest.approx(2 * _COST)


class _SlowReview:
    """`get_client` finto che misura le chiamate in volo."""

    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.in_flight = 0
        self.peak = 0
        self.calls = 0

    def __call__(self, timeout: float = 600.0) -> Any:
        fake = self

        class _Client:
            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def post(
                self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
            ) -> _Reply:
                fake.calls += 1
                fake.in_flight += 1
                fake.peak = max(fake.peak, fake.in_flight)
                await asyncio.sleep(0.01)
                fake.in_flight -= 1
                return _Reply(fake.reply, _USAGE)

        return _Client()


def _three_figures() -> LessonContentOutput:
    """Tre figure DOT distinte, citate dal testo della lezione."""
    triangle = "digraph T {\n  p -> q;\n  q -> r;\n}"
    output = _output(_K22)
    for i, source in enumerate((_STAR, triangle), start=2):
        output.visual_assets.append(
            output.visual_assets[0].model_copy(update={"asset_id": f"g{i}", "content": source})
        )
    output.sections[0].content = "Le altre due figure.\n\n[FIG:g2]\n[FIG:g3]\n"
    return output


@pytest.mark.parametrize(("cap", "peak"), [(1, 1), (3, 3)])
async def test_review_calls_are_capped_by_the_parallel_setting(
    monkeypatch: pytest.MonkeyPatch, cap: int, peak: int
) -> None:
    """Un giro lancia una chiamata per figura in attesa, ma in volo non ne
    sta mai più di `figure_review_max_parallel` (semaforo per loop): è il
    tetto di spesa quando molte lezioni corrono insieme."""
    _settings(monkeypatch, figure_review_max_attempts=1, figure_review_max_parallel=cap)
    fake = _SlowReview(_coherent())
    monkeypatch.setattr(review, "get_client", fake)
    out, usage = await avs.validate_and_fix_content_assets(_three_figures(), language_code="it")
    assert [a.content for a in out.visual_assets] == [_K22, _STAR, out.visual_assets[2].content]
    assert (fake.calls, len(usage)) == (3, 3)
    assert fake.peak == peak


async def test_speculative_renders_never_poison_the_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cache_failures=False`: un guasto del batch reso dalla revisione non
    mette in cache negativa la chiave dell'ORIGINALE (un export nei 60 s
    successivi salterebbe la figura)."""

    def _boom(renderer: Any, contents: list[str], ids: list[str]) -> list[Any]:
        raise RuntimeError("batch esploso")

    monkeypatch.setattr(frs, "_render_batch", _boom)
    entry = [{"format": "dot", "asset_id": "asset:g1", "content": _K22}]
    key = frs.cache_key("dot", frs.DotRenderer().sanitize(_K22))
    assert await frs.render_figure_map(entry, language="it", cache_failures=False) == {}
    assert frs._cache_is_negative(key) is False
    assert await frs.render_figure_map(entry, language="it") == {}
    assert frs._cache_is_negative(key) is True


# ---------------------------------------------------------------------------
# Prompt, schema, configurazione e documentazione
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "clauses"),
    [
        (
            "it",
            (
                "`coerente` e' la risposta predefinita",
                "NON\n  riscrivere una figura che corrisponde al testo",
                "Conserva TUTTI i nodi dell'originale con i loro identificativi",
                "cambia la\n  sua etichetta, non l'identificativo",
                "ne conserva almeno\n  uno",
                "La riscrittura riduce la densita'",
                "meno incroci",
                "NON aggiungere contenuti assenti dal testo della sezione",
                "registro\n  accademico",
            ),
        ),
        (
            "en",
            (
                "`coerente` is the default answer",
                "do NOT rewrite a figure\n  that matches the text",
                "Keep ALL the nodes of the original with their identifiers",
                "change its label,\n  not its identifier",
                "keeps at least one",
                "The rewrite reduces density",
                "fewer crossings",
                "Do NOT add content missing from the section text",
                "keep every row of `data.values`",
                "keep the expressions and the\n  domain",
                "academic register",
            ),
        ),
    ],
)
def test_prompts_state_the_contract(lang: str, clauses: tuple[str, ...]) -> None:
    prompt = review._SYSTEM_PROMPTS[lang]
    missing = [c for c in clauses if c not in prompt]
    assert missing == []
    assert review._system_prompt("it-IT" if lang == "it" else "de") == prompt


def test_schema_is_strict_with_a_nullable_source() -> None:
    schema = review.FIGURE_REVIEW_JSON_SCHEMA
    assert schema["strict"] is True
    body = schema["schema"]
    assert body["additionalProperties"] is False
    assert set(body["required"]) == set(body["properties"]) == {"verdict", "reason", "source"}
    assert body["properties"]["verdict"]["enum"] == ["coerente", "correggi"]
    assert body["properties"]["source"]["type"] == ["string", "null"]
    assert review.FigureReviewOut.model_validate({}).verdict == "coerente"


def test_prompt_17_is_checked_against_the_code() -> None:
    labels = {(number, variant) for _label, number, variant, _render in _RENDERERS}
    assert (17, None) in labels and (17, "_SYSTEM_REVIEW_EN") in labels


def test_settings_defaults_and_every_deployment_surface() -> None:
    fields = config.Settings.model_fields
    defaults = {
        name: fields[name].default
        for name in (
            "openai_figure_review_model",
            "openai_figure_review_reasoning_effort",
            "openai_figure_review_max_tokens",
            "figure_review_max_attempts",
            "figure_review_max_parallel",
            "figure_review_enabled",
        )
    }
    assert defaults == {
        "openai_figure_review_model": "gpt-4o-mini",
        "openai_figure_review_reasoning_effort": None,
        "openai_figure_review_max_tokens": 4000,
        "figure_review_max_attempts": 2,
        "figure_review_max_parallel": 4,
        "figure_review_enabled": True,
    }
    env_names = [name.upper() for name in defaults]
    for rel in (".env.example", "docker-compose.prod.yml", "docs/04-configuration.md"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert [n for n in env_names if n not in text] == [], rel


def test_the_module_docstring_names_the_review_phase() -> None:
    doc = avs.__doc__ or ""
    assert "openai_figure_review_service" in doc
    assert "fix → revisione → localizzazione" in doc


def test_fit_box_matches_the_default_lesson_template() -> None:
    margins = pdf._compute_template_margins_cm({})
    expected = (margins["figure_box_w_mm"], margins["figure_box_h_mm"])
    assert expected == avs._REVIEW_FIT_BOX_MM
    assert avs._REVIEW_BODY_PADDING_MM == {"mermaid": pdf._MERMAID_BODY_PADDING_MM}
