"""Predicato unico `figure_visibility` nei due modi (U1: non retroattivo).

`select` (catalogo, PATCH di asset nuovi, ricontrollo in generazione)
applica politica del documento, esclusione e licenza; `render` (figure già
collocate) verifica solo le condizioni strutturali.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.source_figure_policy import (
    REASONS,
    VISIBLE,
    Visibility,
    figure_visibility,
)

COURSE = uuid.UUID("00000000-0000-0000-0000-00000000c0de")


@dataclass
class _Fig:
    course_id: uuid.UUID = COURSE
    document_id: uuid.UUID | None = field(default_factory=uuid.uuid4)
    status: str = "ready"
    source_kind: str = "uploaded"
    excluded_by_user: bool = False
    license: str = "all_rights_reserved"
    detached_at: object = None
    attribution: dict[str, Any] | None = None
    storage_path: str | None = "/uploads/courses/x/document_figures/y/p1-f1-abc.png"


@dataclass
class _Doc:
    citation_policy: str = "citable"
    is_own_work: bool = False


def _vis(fig: _Fig | None, doc: _Doc | None, *, mode: str, policy: str = "cite_all", **kw: Any):
    return figure_visibility(
        fig,
        doc,
        course_id=COURSE,
        license_policy=policy,
        mode=mode,
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("mode", ["select", "render"])
def test_ready_citable_figure_is_visible(mode: str) -> None:
    assert _vis(_Fig(), _Doc(), mode=mode) == VISIBLE


# (figura, documento, politica, extra) → motivo atteso in select / in render
_CASES: list[tuple[str, _Fig | None, _Doc | None, str, dict[str, Any], str | None, str | None]] = [
    ("missing", None, None, "cite_all", {}, "not_found", "not_found"),
    (
        "other course",
        _Fig(course_id=uuid.uuid4()),
        _Doc(),
        "cite_all",
        {},
        "wrong_course",
        "wrong_course",
    ),
    ("extracted", _Fig(status="extracted"), _Doc(), "cite_all", {}, "not_ready", "not_ready"),
    ("rejected", _Fig(status="rejected"), _Doc(), "cite_all", {}, "not_ready", "not_ready"),
    ("failed", _Fig(status="failed"), _Doc(), "cite_all", {}, "not_ready", "not_ready"),
    (
        "excluded by teacher",
        _Fig(excluded_by_user=True),
        _Doc(),
        "cite_all",
        {},
        "excluded_by_user",
        None,
    ),
    (
        "document excluded",
        _Fig(),
        _Doc(citation_policy="excluded"),
        "cite_all",
        {},
        "document_excluded",
        None,
    ),
    (
        "document content_only",
        _Fig(),
        _Doc(citation_policy="content_only"),
        "cite_all",
        {},
        "document_content_only",
        None,
    ),
    (
        "open_only, closed license",
        _Fig(license="all_rights_reserved"),
        _Doc(),
        "open_only",
        {},
        "license_not_open",
        None,
    ),
    (
        "open_only, unknown license",
        _Fig(license="unknown"),
        _Doc(),
        "open_only",
        {},
        "license_not_open",
        None,
    ),
    ("open_only, CC BY", _Fig(license="cc_by"), _Doc(), "open_only", {}, None, None),
    (
        "open_only, own work",
        _Fig(license="unknown"),
        _Doc(is_own_work=True),
        "open_only",
        {},
        None,
        None,
    ),
    (
        "detached with frozen attribution",
        _Fig(document_id=None, detached_at="2026-09-23", attribution={"title": "T"}),
        None,
        "cite_all",
        {},
        "document_excluded",
        None,
    ),
    (
        "detached without attribution",
        _Fig(document_id=None, detached_at="2026-09-23", attribution=None),
        None,
        "cite_all",
        {},
        "document_excluded",
        "attribution_missing",
    ),
    (
        "external without attribution",
        _Fig(source_kind="wikimedia", document_id=None, license="cc_by"),
        None,
        "cite_all",
        {},
        "attribution_missing",
        "attribution_missing",
    ),
    (
        "external with attribution",
        _Fig(source_kind="wikimedia", document_id=None, license="cc_by", attribution={"a": 1}),
        None,
        "open_only",
        {},
        None,
        None,
    ),
    (
        "external own-work flag in attribution",
        _Fig(
            source_kind="openalex",
            document_id=None,
            license="unknown",
            attribution={"is_own_work": True},
        ),
        None,
        "open_only",
        {},
        None,
        None,
    ),
    (
        "no file path",
        _Fig(storage_path=None),
        _Doc(),
        "cite_all",
        {},
        "file_missing",
        "file_missing",
    ),
    (
        "file gone from storage",
        _Fig(),
        _Doc(),
        "cite_all",
        {"file_present": False},
        "file_missing",
        "file_missing",
    ),
]


@pytest.mark.parametrize(
    ("fig", "doc", "policy", "extra", "select_reason", "render_reason"),
    [c[1:] for c in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_visibility_reasons_in_both_modes(
    fig: _Fig | None,
    doc: _Doc | None,
    policy: str,
    extra: dict[str, Any],
    select_reason: str | None,
    render_reason: str | None,
) -> None:
    for mode, expected in (("select", select_reason), ("render", render_reason)):
        result = _vis(fig, doc, mode=mode, policy=policy, **extra)
        assert isinstance(result, Visibility)
        assert result.reason == expected, (mode, result)
        assert result.renderable is (expected is None)
        if expected is not None:
            assert expected in REASONS


def test_render_mode_is_not_retroactive() -> None:
    """Cambio di policy del documento, esclusione e open_only non ritirano
    una figura già collocata (U1)."""
    fig = _Fig(excluded_by_user=True, license="unknown")
    for doc in (_Doc(citation_policy="excluded"), _Doc(citation_policy="content_only")):
        assert _vis(fig, doc, mode="render", policy="open_only") == VISIBLE
        assert not _vis(fig, doc, mode="select", policy="open_only").renderable


def test_reasons_are_all_reachable() -> None:
    reached = {c[5] for c in _CASES} | {c[6] for c in _CASES}
    assert set(REASONS) <= reached
