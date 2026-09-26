"""Setting delle figure di fonte: default, superfici di deploy e listino.

Ogni variabile nuova sta in `Settings`, `.env.example`,
`docker-compose.prod.yml` e `docs/04-configuration.md` (CLAUDE.md). Ogni
modello AI di default ha un prezzo in `MODEL_PRICING` (G9: costo mai
«non stimato»).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.openai_pricing import MODEL_PRICING, estimate_cost_usd

_ROOT = Path(__file__).resolve().parents[2]

_DEFAULTS: dict[str, object] = {
    "figure_source_enabled": True,
    "figure_source_license_policy": "cite_all",
    "figure_source_catalog_max_items": 8,
    "figure_source_catalog_max_chars": 4000,
    "figure_source_max_per_lesson": 4,
    "figure_source_max_per_intro_lesson": 1,
    "figure_source_min_per_lesson": 1,
    "figure_source_max_lessons_per_figure": 2,
    "figure_resolution_rules_enabled": True,
    "figure_extraction_native_crop_enabled": True,
    "figure_wait_max_minutes": 15,
    "figure_extraction_enabled": True,
    "figure_extraction_engine": "docling",
    "figure_extraction_threads": 1,
    "figure_extraction_block_pages": 10,
    "figure_extraction_pages_per_child": 40,
    "figure_extraction_max_pages": 0,
    "figure_extraction_total_timeout_seconds": 0,
    "figure_extraction_probe_timeout_seconds": 180,
    "figure_extraction_max_rss_mb": 2048,
    "figure_extraction_min_available_mb": 1800,
    "figure_extraction_max_defer_minutes": 180,
    "figure_extraction_auto_retry_max": 3,
    "figure_extraction_attempts_max": 6,
    "figure_extraction_poll_interval_seconds": 5,
    "figure_docling_artifacts_path": "/opt/docling-models",
    "figure_min_quality_score": 3,
    "figure_describe_max_per_document": 0,
    "openai_figure_describe_model": "gpt-4.1-mini",
    "openai_figure_describe_reasoning_effort": None,
    "openai_figure_describe_max_tokens": 800,
    "openai_figure_describe_detail": "high",
    "openai_figure_describe_timeout_seconds": 60,
    "openai_figure_describe_concurrency": 3,
    "figure_redundancy_enabled": True,
    "figure_redundancy_max_attempts": 2,
    "figure_redundancy_timeout_seconds": 120,
    "openai_figure_redundancy_model": "gpt-4o-mini",
    "openai_figure_redundancy_reasoning_effort": None,
    "openai_figure_redundancy_max_tokens": 1500,
    "figure_slides_coverage_repair_enabled": True,
    "figure_literature_enabled": True,
    "figure_literature_max_candidates_per_lesson": 15,
    "figure_literature_max_candidates_per_need": 3,
    "figure_literature_max_cost_usd_per_check": 0.08,
    "figure_literature_max_paid_pdf_per_lesson": 3,
    "figure_literature_max_per_course": 40,
    "figure_literature_timeout_seconds": 300,
    "figure_literature_max_image_mb": 20,
    "figure_literature_max_pdf_mb": 30,
    "figure_literature_max_image_pixels": 16_000_000,
    "figure_literature_max_pdf_pages": 40,
    "figure_literature_image_width": 1920,
    "figure_literature_auto_retry_max": 2,
    "figure_literature_poll_interval_seconds": 5,
    "wikimedia_api_url": "https://commons.wikimedia.org/w/api.php",
    "openalex_api_key": None,
    "openai_figure_relevance_model": "gpt-4.1-mini",
    "openai_figure_relevance_reasoning_effort": None,
    "openai_figure_relevance_max_tokens": 800,
    "openai_figure_relevance_timeout_seconds": 60,
    "figure_plan_enabled": True,
    "openai_figure_needs_model": "gpt-5.5",
    "openai_figure_needs_reasoning_effort": "none",
    "openai_figure_needs_max_tokens": 4500,
    "openai_figure_needs_timeout_seconds": 90,
    "figure_needs_concurrency": 4,
    "figure_needs_auto_retry_max": 3,
    "figure_needs_poll_interval_seconds": 5,
    "figure_needs_max_per_lesson": 10,
    "figure_needs_max_per_intro_lesson": 3,
    "figure_plan_max_per_lesson": 8,
    "figure_source_minutes_per_figure": 4,
    "figure_plan_legacy_match_enabled": False,
    "figure_plan_in_prompt_enabled": True,
    "figure_tikz_enabled": False,
    "figure_tikz_propose_enabled": False,
    "figure_tikz_max_chars": 8000,
    "figure_tikz_timeout_seconds": 10,
    "figure_tikz_queue_timeout_seconds": 30,
    "figure_tikz_fix_max_attempts": 1,
    "figure_tikz_render_review_enabled": True,
    "openai_tikz_review_model": "gpt-4.1-mini",
    "openai_tikz_review_max_tokens": 1500,
    "figure_tikz_preview_per_minute": 10,
    "tex_bin_dir": None,
}

# In compose ed .env.example estrazione e letteratura aperta restano spente
# finché non le si accende (M0; rete esterna e costo Vision).
_DEPLOY_OVERRIDES = {"FIGURE_EXTRACTION_ENABLED": "false", "FIGURE_LITERATURE_ENABLED": "false"}


def _code_defaults() -> dict[str, object]:
    fields = Settings.model_fields
    return {name: fields[name].default for name in _DEFAULTS}


def test_code_defaults() -> None:
    assert _code_defaults() == _DEFAULTS


@pytest.mark.parametrize(
    "rel", [".env.example", "docker-compose.prod.yml", "docs/04-configuration.md"]
)
def test_every_setting_is_on_every_deploy_surface(rel: str) -> None:
    text = (_ROOT / rel).read_text(encoding="utf-8")
    missing = [n.upper() for n in _DEFAULTS if n.upper() not in text]
    assert missing == [], rel


def _render(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def test_compose_defaults_match_the_code() -> None:
    text = (_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for name, default in _DEFAULTS.items():
        env = name.upper()
        match = re.search(rf"^\s+{env}: \$\{{{env}:-([^}}]*)\}}\s*$", text, re.MULTILINE)
        assert match, env
        assert match.group(1) == _DEPLOY_OVERRIDES.get(env, _render(default)), env


def test_env_example_defaults_match_the_code() -> None:
    text = (_ROOT / ".env.example").read_text(encoding="utf-8")
    for name, default in _DEFAULTS.items():
        env = name.upper()
        match = re.search(rf"^{env}=(.*)$", text, re.MULTILINE)
        assert match, env
        assert match.group(1).strip() == _DEPLOY_OVERRIDES.get(env, _render(default)), env


@pytest.mark.parametrize(
    "setting",
    [
        "openai_figure_describe_model",
        "openai_figure_redundancy_model",
        "openai_figure_relevance_model",
        "openai_figure_needs_model",
        "openai_tikz_review_model",
    ],
)
def test_default_models_are_priced(setting: str) -> None:
    model = str(_DEFAULTS[setting])
    assert model in MODEL_PRICING
    cost = estimate_cost_usd(model=model, prompt_tokens=1000, completion_tokens=200)
    assert cost is not None and cost > 0
