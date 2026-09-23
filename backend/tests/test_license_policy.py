"""Politica di licenza delle figure di fonte (garanzia G3, parte pura).

Classificazione esaustiva delle licenze per `open_only`, politica effettiva
(override dell'organizzazione sul setting globale, in entrambe le direzioni)
e mappatura licenza del documento → licenza della figura.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import CheckConstraint

from app.models.course_document import CourseDocument
from app.models.course_document_figure import FIGURE_LICENSES
from app.services.source_figure_policy import (
    OPEN_LICENSES,
    document_license_to_figure,
    effective_license_policy,
    is_open_license,
)

# Classificazione attesa, scritta a mano: una licenza nuova nel vocabolario
# fa fallire il test finché non viene classificata qui.
_EXPECTED_OPEN = {
    "cc0": True,
    "public_domain": True,
    "cc_by": True,
    "cc_by_sa": True,
    "cc_by_nc": False,
    "cc_by_nd": False,
    "cc_by_nc_sa": False,
    "cc_by_nc_nd": False,
    "all_rights_reserved": False,
    "other": False,
    "unknown": False,
}


def test_classification_covers_the_whole_vocabulary() -> None:
    assert set(_EXPECTED_OPEN) == set(FIGURE_LICENSES)
    assert set(FIGURE_LICENSES) >= OPEN_LICENSES


@pytest.mark.parametrize("license", FIGURE_LICENSES)
def test_open_license_classification(license: str) -> None:
    assert is_open_license(license, is_own_work=False) is _EXPECTED_OPEN[license]
    # Il materiale proprio del docente è sempre riproducibile.
    assert is_open_license(license, is_own_work=True) is True


def test_missing_license_is_not_open() -> None:
    assert is_open_license(None, is_own_work=False) is False
    assert is_open_license("", is_own_work=False) is False


@pytest.mark.parametrize(
    ("org", "settings_policy", "expected"),
    [
        (None, "cite_all", "cite_all"),
        (None, "open_only", "open_only"),
        ("open_only", "cite_all", "open_only"),
        ("cite_all", "open_only", "cite_all"),
        ("open_only", "open_only", "open_only"),
        ("cite_all", "cite_all", "cite_all"),
        ("bogus", "open_only", "open_only"),
        (None, "bogus", "cite_all"),
    ],
)
def test_effective_policy_org_override_both_directions(
    org: str | None, settings_policy: str, expected: str
) -> None:
    assert effective_license_policy(org, settings_policy) == expected


def _document_licenses() -> set[str]:
    for constraint in CourseDocument.__table__.constraints:
        if isinstance(constraint, CheckConstraint) and str(constraint.name).endswith(
            "license_valid"
        ):
            return set(re.findall(r"'([a-z_0-9]+)'", str(constraint.sqltext)))
    raise AssertionError("CHECK della licenza del documento non trovato")


def test_document_license_maps_to_figure_license() -> None:
    document_licenses = _document_licenses()
    # Sul documento la licenza sconosciuta è NULL, mai 'unknown'.
    assert document_licenses == set(FIGURE_LICENSES) - {"unknown"}
    assert document_license_to_figure(None) == "unknown"
    for license in document_licenses:
        assert document_license_to_figure(license) == license
