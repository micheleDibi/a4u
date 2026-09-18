"""Dispatch per kind di `asset_validation_service` (D2, Q1).

- `_validate_slots` con slot misti `latex` / `mermaid` / `vegalite` / `dot`
  e `_validate_js_batch` sostituito in memoria: nel batch JS entrano SOLO
  latex e mermaid, i risultati sono riallineati con `js_pos`, il kind negli
  `AssetCheck` è quello dello slot;
- formato non disponibile → check `fixable=False`, mai pass-through, e
  `_validate_and_fix` alza `AssetFixUnresolvedError` senza chiamare il fix
  AI;
- `_collect_content_slots` / `_collect_slides_slots` raccolgono ogni formato
  renderizzabile con `kind=asset.format`; i campi localizzabili passano dal
  renderer; `_sanitize` non corrompe ```` ```vega-lite ````;
- grep che vieta nuovi confronti letterali `== "mermaid"` fuori dai siti
  dichiarati in `docs/courses/17-visual-figures.md` §9.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from app.schemas.course_lesson_slides import LessonSlidesOutput
from app.services import asset_validation_service as avs
from app.services import figure_render_service as frs
from app.services import openai_asset_fix_service as fix
from tests.course_builders import build_lesson_content_output

_APP_DIR = Path(__file__).resolve().parents[1] / "app"


class _FakeRenderer:
    def __init__(self, fmt: str, *, ok: bool = True, translatable: dict[str, str] | None = None):
        self.fmt = fmt
        self.ok = ok
        self.translatable = translatable or {}
        self.validated: list[tuple[str, bool]] = []

    def available(self) -> bool:
        return True

    def sanitize(self, content: str) -> str:
        return content.strip()

    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
        self.validated.append((content, deep))
        return (True, "") if self.ok else (False, f"{self.fmt}: errore del renderer")

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        return "<svg/>"

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        return ["<svg/>" for _ in contents]

    def extract_translatable(self, content: str) -> dict[str, str]:
        return dict(self.translatable)

    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str:
        return content + "".join(f"|{k}={v}" for k, v in tr.items())


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    frs.available_formats.cache_clear()
    frs.clear_svg_cache()
    yield
    frs.available_formats.cache_clear()
    frs.clear_svg_cache()


@pytest.fixture
def fake_js(monkeypatch: pytest.MonkeyPatch) -> list[list[tuple[str, str]]]:
    """Sostituisce il batch Playwright: registra gli item e risponde con esiti
    distinguibili per posizione (`KaTeX`/`mermaid` + indice)."""
    calls: list[list[tuple[str, str]]] = []

    async def _batch(items: list[tuple[str, str]]) -> list[tuple[bool, str]] | None:
        calls.append(list(items))
        return [(False, f"js-{i}") for i, _ in enumerate(items)]

    monkeypatch.setattr(avs, "_validate_js_batch", _batch)
    return calls


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeRenderer]:
    fakes = {"vegalite": _FakeRenderer("vegalite"), "dot": _FakeRenderer("dot", ok=False)}
    for fmt, r in fakes.items():
        monkeypatch.setitem(frs.REGISTRY, fmt, r)
    monkeypatch.setattr(avs, "available_formats", lambda: ("mermaid", "vegalite", "dot"))
    return fakes


# ---------------------------------------------------------------------------
# _validate_slots: rimappatura js_pos e kind
# ---------------------------------------------------------------------------


async def test_validate_slots_remaps_js_results_and_keeps_kinds(
    fake_js: list[list[tuple[str, str]]], fake_registry: dict[str, _FakeRenderer]
):
    checks = await avs.validate_assets_for_test(
        [
            ("eq:1", "latex", "x^2"),
            ("asset:A1", "vegalite", '{"mark": "bar"}'),
            ("asset:A2", "mermaid", "flowchart LR\n A --> B"),
            ("asset:A3", "dot", "digraph { a -> b }"),
            ("eq:2", "latex", "\\frac{a}{b}"),
            ("asset:A4", "mermaid", "journey\n title x"),  # gate statico: mai nel JS
        ]
    )
    # Nel batch JS solo latex e mermaid che superano il gate statico.
    assert fake_js == [
        [("latex", "x^2"), ("mermaid", "flowchart LR\n A --> B"), ("latex", "\\frac{a}{b}")]
    ]
    by_id = {c.id: c for c in checks}
    assert [c.kind for c in checks] == ["latex", "vegalite", "mermaid", "dot", "latex", "mermaid"]
    # Riallineamento: lo slot 4 (latex) legge la posizione 2 del batch, non la 4.
    assert by_id["eq:1"].error_message == "KaTeX: js-0"
    assert by_id["asset:A2"].error_message == "mermaid: js-1"
    assert by_id["eq:2"].error_message == "KaTeX: js-2"
    assert by_id["asset:A1"].ok is True and by_id["asset:A1"].fixable is True
    assert by_id["asset:A3"].ok is False and "renderer" in by_id["asset:A3"].error_message
    assert by_id["asset:A4"].ok is False
    assert by_id["asset:A4"].error_message.startswith("mermaid_type_not_allowed")
    # I renderer sono chiamati in profondità (prova di render) e mai per Mermaid.
    assert fake_registry["vegalite"].validated == [('{"mark": "bar"}', True)]
    assert fake_registry["dot"].validated == [("digraph { a -> b }", True)]


async def test_validate_slots_without_js_degrades_only_latex_and_mermaid(
    monkeypatch: pytest.MonkeyPatch, fake_registry: dict[str, _FakeRenderer]
):
    async def _none(items: list[tuple[str, str]]) -> list[tuple[bool, str]] | None:
        return None

    monkeypatch.setattr(avs, "_validate_js_batch", _none)
    checks = await avs.validate_assets_for_test(
        [
            ("eq:1", "latex", "x^2"),
            ("asset:A2", "mermaid", "flowchart LR\n A --> B"),
            ("asset:A3", "dot", "digraph { a -> b }"),
        ]
    )
    assert [(c.kind, c.ok) for c in checks] == [("latex", True), ("mermaid", True), ("dot", False)]


async def test_unavailable_format_is_not_fixable(
    monkeypatch: pytest.MonkeyPatch, fake_js: list[list[tuple[str, str]]]
):
    monkeypatch.setattr(frs.shutil, "which", lambda *_a, **_k: None)
    frs.available_formats.cache_clear()
    checks = await avs.validate_assets_for_test([("asset:A3", "dot", "digraph { a -> b }")])
    assert checks == [avs.AssetCheck("asset:A3", "dot", False, "dot_unavailable", fixable=False)]
    assert fake_js == [[]]


async def test_validate_slots_bounds_a_slow_renderer_with_the_render_timeout(
    monkeypatch: pytest.MonkeyPatch, fake_js: list[list[tuple[str, str]]]
):
    """`validate(deep=True)` gira in un thread non interrompibile: senza
    `wait_for` una spec costosa bloccava la validazione della lezione per
    minuti. Il timeout produce un check invalido (riparabile), non un
    pass-through."""
    import time

    from app.core import config

    class _SlowRenderer(_FakeRenderer):
        def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
            time.sleep(0.6)
            return (True, "")

    settings = config.get_settings().model_copy(update={"figure_render_timeout_seconds": 0.2})
    monkeypatch.setattr(avs, "get_settings", lambda: settings)
    monkeypatch.setitem(frs.REGISTRY, "function", _SlowRenderer("function"))
    monkeypatch.setattr(avs, "available_formats", lambda: ("mermaid", "function"))
    slot = avs._Slot(
        id="asset:A1", kind="function", current="{}", context="", commit=lambda v: None
    )
    t0 = time.perf_counter()
    [check] = await avs._validate_slots([slot])
    assert time.perf_counter() - t0 < 0.55
    assert check.ok is False and check.fixable is True
    assert check.error_message == "function: validazione oltre 0.2 s"


async def test_validate_and_fix_never_calls_the_ai_for_unfixable_checks(
    monkeypatch: pytest.MonkeyPatch, fake_js: list[list[tuple[str, str]]]
):
    monkeypatch.setattr(frs.shutil, "which", lambda *_a, **_k: None)
    frs.available_formats.cache_clear()

    async def _fix(**_kw: Any) -> Any:
        raise AssertionError("il fix AI non deve essere chiamato per un check non fixable")

    monkeypatch.setattr(fix, "fix_asset", _fix)
    slot = avs._Slot(
        id="asset:A3", kind="dot", current="digraph { a -> b }", context="", commit=lambda v: None
    )
    with pytest.raises(avs.AssetFixUnresolvedError, match="dot_unavailable"):
        await avs._validate_and_fix([slot], [], language_code="it")


async def test_validate_and_fix_repairs_a_renderer_kind_through_the_ai(
    monkeypatch: pytest.MonkeyPatch, fake_js: list[list[tuple[str, str]]]
):
    class _FlipRenderer(_FakeRenderer):
        def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
            self.validated.append((content, deep))
            return (True, "") if content == "fixed" else (False, "vegalite: rotto")

    monkeypatch.setitem(frs.REGISTRY, "vegalite", _FlipRenderer("vegalite"))
    monkeypatch.setattr(avs, "available_formats", lambda: ("mermaid", "vegalite"))
    seen: list[dict[str, Any]] = []

    async def _fix(**kw: Any) -> tuple[fix.AssetFixOut, dict[str, Any]]:
        seen.append(kw)
        return fix.AssetFixOut(fixed_content="```vega-lite\nfixed\n```"), {}

    monkeypatch.setattr(fix, "fix_asset", _fix)
    committed: list[str] = []
    slot = avs._Slot(
        id="asset:A1", kind="vegalite", current="rotto", context="c", commit=committed.append
    )
    assert await avs._validate_and_fix([slot], [], language_code="it") == 1
    assert committed == ["fixed"]
    assert seen[0]["kind"] == "vegalite" and seen[0]["error_message"] == "vegalite: rotto"


# ---------------------------------------------------------------------------
# Raccolta degli slot e dei campi localizzabili
# ---------------------------------------------------------------------------


def _content_output() -> Any:
    return build_lesson_content_output(
        visual_assets=[
            {
                "asset_id": "A1",
                "format": "mermaid",
                "content": "flowchart LR\n A --> B",
                "caption": "c1",
            },
            {"asset_id": "A2", "format": "vegalite", "content": '{"mark": "bar"}', "caption": "c2"},
            {"asset_id": "A3", "format": "dot", "content": "digraph { a -> b }", "caption": "c3"},
            {"asset_id": "A4", "format": "function", "content": '{"kind": "x"}', "caption": "c4"},
            {"asset_id": "A5", "format": "image", "content": "/uploads/x.png", "caption": "c5"},
            {"asset_id": "A6", "format": "description", "content": "legacy", "caption": "c6"},
            {"asset_id": "A7", "format": "dot", "content": "   ", "caption": "vuoto"},
        ]
    )


def test_collect_content_slots_uses_the_format_as_kind():
    slots, _inline = avs._collect_content_slots(_content_output())
    assert [(s.id, s.kind) for s in slots] == [
        ("asset:A1", "mermaid"),
        ("asset:A2", "vegalite"),
        ("asset:A3", "dot"),
        ("asset:A4", "function"),
    ]


def test_collect_slides_slots_uses_the_format_as_kind():
    output = LessonSlidesOutput.model_validate(
        {
            "lesson_id": "M1.L1",
            "total_slides": 1,
            "slides": [
                {
                    "slide_number": 1,
                    "slide_id": "S1",
                    "type": "concept",
                    "title": "T",
                    "body": "b",
                    "bullets": [],
                    "references_assets": [],
                    "source_section_id": "",
                }
            ],
            "new_assets": [
                {"asset_id": "N1", "format": "dot", "content": "digraph { a }"},
                {"asset_id": "N2", "format": "image_prompt", "content": "legacy"},
                {"asset_id": "N3", "format": "mermaid", "content": "flowchart LR\n A"},
            ],
        }
    )
    slots, _inline = avs._collect_slides_slots(output)
    assert [(s.id, s.kind) for s in slots] == [("new_asset:N1", "dot"), ("new_asset:N3", "mermaid")]


def test_loc_fields_come_from_the_renderer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(
        frs.REGISTRY, "vegalite", _FakeRenderer("vegalite", translatable={"title": "Esempio"})
    )
    monkeypatch.setitem(
        frs.REGISTRY, "dot", _FakeRenderer("dot", translatable={"label.0": "a", "label.1": "b"})
    )
    output = _content_output()
    fields = avs._collect_content_loc_fields(output)
    by_key = {f.key: f for f in fields}
    # Mermaid: l'intero sorgente con la chiave storica `va.<i>.content`.
    assert by_key["va.0.content"].kind == "mermaid"
    assert by_key["va.0.content"].text == "flowchart LR\n A --> B"
    assert by_key["va.1.content.title"].kind == "vegalite"
    assert by_key["va.2.content.label.0"].kind == "dot" and "va.2.content.label.1" in by_key
    assert "va.4.content" not in by_key  # image: solo caption/alt_text
    assert by_key["va.4.caption"].kind == "text"
    # `function`: la spec `{"kind": "x"}` non ha label (né è valida): nessun
    # campo del contenuto (le label di espressioni e annotazioni sono in
    # `test_function_figure_service`).
    assert not any(k.startswith("va.3.content") for k in by_key)
    by_key["va.0.content"].apply("flowchart LR\n A --> C")
    assert output.visual_assets[0].content == "flowchart LR\n A --> C"
    by_key["va.2.content.label.0"].apply("alpha")
    by_key["va.2.content.label.1"].apply("beta")
    assert output.visual_assets[2].content == "digraph { a -> b }|label.0=alpha|label.1=beta"


async def test_localize_marks_every_non_text_kind_as_structural(monkeypatch: pytest.MonkeyPatch):
    async def _localize(
        *, items: dict[str, str], language_code: str
    ) -> tuple[dict[str, str], dict]:
        return {k: v + "訳" for k, v in items.items()}, {}

    monkeypatch.setattr(avs.openai_asset_localize_service, "localize_texts", _localize)
    applied: list[tuple[str, str]] = []
    fields = [
        avs._LocField("va.0.caption", "Didascalia", "text", lambda v: applied.append(("text", v))),
        avs._LocField(
            "va.1.content.title", "Titolo", "vegalite", lambda v: applied.append(("vl", v))
        ),
    ]
    assert await avs._localize_fields(fields[:1], language_code="ja") is False
    assert await avs._localize_fields(fields, language_code="ja") is True
    assert ("vl", "Titolo訳") in applied


def test_sanitize_keeps_the_body_of_a_vega_lite_fence():
    body = '{"mark": "bar"}'
    assert avs._sanitize("vegalite", f"```vega-lite\n{body}\n```") == body
    assert avs._sanitize("dot", "```dot\ndigraph { a }\n```") == "digraph { a }"
    assert avs._sanitize("function", f"```json5\n{body}\n```") == body
    assert avs._sanitize("latex", "```latex\n$x^2$\n```") == "x^2"


def test_asset_check_is_frozen_with_fixable_default():
    check = avs.AssetCheck("id", "dot", False, "e")
    assert check.fixable is True
    with pytest.raises(AttributeError):
        check.ok = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Grep: nessun nuovo confronto letterale con il formato Mermaid
# ---------------------------------------------------------------------------

# Siti dichiarati in `docs/courses/17-visual-figures.md` §9 (HEAD) con il
# numero massimo di occorrenze ammesse per file. `asset_validation_service`:
# i due confronti sono sul KIND dello slot in `_validate_slots` (gate
# statico prima del batch e lettura del risultato JS: dispatch prescritto
# da Q1, Mermaid ha il parse JS nel batch), non sul formato.
# I due servizi PDF restano letterali per il body byte-identico (A11-L3);
# `course_lesson_pdf_service:641` sparisce con `render_svg_map` (WP4).
# `figure_render_service.render_chain_variants` (D15, §21): la direzione di
# un grafo è una nozione del solo Mermaid — `chain_layout` legge la sintassi
# di `flowchart`, DOT ha il suo `rankdir`, Vega-Lite e `function` non hanno
# direzione. Non c'è un metodo del protocollo da chiamare al suo posto.
_ALLOWED_MERMAID_LITERALS: dict[str, int] = {
    "services/asset_validation_service.py": 2,
    "services/course_lesson_pdf_service.py": 2,
    "services/course_lesson_slides_pdf_service.py": 1,
    "services/figure_render_service.py": 1,
}
_MERMAID_LITERAL_RE = re.compile(r'[=!]=\s*"mermaid"')


def test_no_new_literal_mermaid_format_comparisons():
    found: dict[str, int] = {}
    for path in sorted(_APP_DIR.rglob("*.py")):
        rel = path.relative_to(_APP_DIR).as_posix()
        hits = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith(('"""', "'''", "- ", "`")):
                continue
            if _MERMAID_LITERAL_RE.search(line) and not line.lstrip().startswith("#"):
                hits += 1
        if hits:
            found[rel] = hits
    unexpected = {k: v for k, v in found.items() if k not in _ALLOWED_MERMAID_LITERALS}
    assert not unexpected, f"nuovi confronti letterali con il formato mermaid: {unexpected}"
    for rel, count in found.items():
        assert count <= _ALLOWED_MERMAID_LITERALS[rel], (rel, count)
