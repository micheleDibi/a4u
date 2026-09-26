"""Frontend delle figure di fonte (G1, WP4c): controlli statici sul sorgente.

- punto unico: la riga «Fonte» (`attribution` del catalogo, calcolata dal
  backend) la mostrano solo `SourceFigure`, il selettore del catalogo e la
  sezione «Figure» del riassunto strutturato; il frontend non compone mai
  una fonte («Fonte:», «Source:»);
- l'immagine arriva solo dall'endpoint autenticato (`documentFigures.image`),
  mai da `mediaUrl`/`/uploads` per un asset `source_figure`;
- ogni chiave i18n nuova usata dai componenti esiste in `it.json` e
  `en.json` (le altre lingue si completano dalla UI).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_LOCALES = _FRONTEND / "i18n" / "locales"
_DISPLAY_FILES = {"SourceFigure.tsx", "SourceFigurePicker.tsx", "DocumentFiguresSection.tsx"}


def _sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in _FRONTEND.rglob("*")
        if path.suffix in {".ts", ".tsx"} and "i18n" not in path.parts
    }


def test_attribution_is_shown_only_by_the_source_figure_components() -> None:
    users = {
        path.name
        for path, text in _sources().items()
        if re.search(r"\.attribution\b", text) and path.name != "courses.ts"
    }
    assert users <= _DISPLAY_FILES, users
    assert "SourceFigure.tsx" in users


def test_the_frontend_never_writes_a_source_line() -> None:
    offenders = [
        path.name
        for path, text in _sources().items()
        if re.search(r"[\"'`](?:Fonte|Source|Fuente|Quelle)\s*:", text)
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "relative",
    [
        "components/shared/SourceFigure.tsx",
        "pages/org/courses/components/DocumentFiguresSection.tsx",
    ],
)
def test_source_figure_images_come_from_the_authenticated_endpoint(relative: str) -> None:
    source = (_FRONTEND / relative).read_text(encoding="utf-8")
    assert "documentFigures.image" in source
    assert "mediaUrl" not in source and "/uploads" not in source


def _flatten(node: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in node.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys |= _flatten(value, full)
        else:
            keys.add(re.sub(r"_(one|other|many|few|zero|two)$", "", full))
    return keys


def test_new_i18n_keys_exist_in_italian_and_english() -> None:
    pattern = re.compile(
        r"t\(\s*[\"'`]((?:courses\.sourceFigures|courses\.figureNeeds|courses\.docs\.figures\.meta|"
        r"courses\.docs\.figures\.licenses|courseSettings\.figureLicensePolicy|"
        r"courseSettings\.fields\.figureLicensePolicy)[\w.]*)[\"'`]"
    )
    used = {m.group(1) for text in _sources().values() for m in pattern.finditer(text)}
    assert used, "nessuna chiave delle figure di fonte trovata nei componenti"
    for language in ("it", "en"):
        keys = _flatten(json.loads((_LOCALES / f"{language}.json").read_text(encoding="utf-8")))
        missing = sorted(k for k in used if k not in keys)
        assert not missing, (language, missing)
    # Anche le chiavi composte (licenze, fonti della bibliografia).
    for language in ("it", "en"):
        keys = _flatten(json.loads((_LOCALES / f"{language}.json").read_text(encoding="utf-8")))
        for code in (
            "cc0",
            "public_domain",
            "cc_by",
            "cc_by_sa",
            "cc_by_nc",
            "cc_by_nd",
            "cc_by_nc_sa",
            "cc_by_nc_nd",
            "all_rights_reserved",
            "other",
        ):
            assert f"courses.docs.figures.licenses.{code}" in keys, (language, code)
        for source in ("user", "openalex", "pdf_metadata", "crossref"):
            assert f"courses.docs.figures.meta.sourceLabel.{source}" in keys, (language, source)
        # Motivi di «non proponibile» (chiavi dinamiche del riassunto) e
        # badge della risoluzione effettiva (doc 18 §22).
        for reason in (
            "resolution_unusable",
            "not_useful",
            "low_quality",
            "excluded_kind",
            "excluded_by_user",
            "document_excluded",
            "license_not_open",
            "attribution_missing",
            "file_missing",
            "superseded",
            "unknown",
        ):
            assert f"courses.sourceFigures.reasons.{reason}" in keys, (language, reason)
        for level in ("low", "unusable", "detail"):
            assert f"courses.sourceFigures.resolution.{level}" in keys, (language, level)
        # Piano delle figure (doc 18 §23.7): stati, motivi ed esiti della
        # letteratura sono chiavi dinamiche del pannello.
        for status in ("placed", "misplaced", "missing", "uncovered", "dismissed"):
            assert f"courses.figureNeeds.status.{status}" in keys, (language, status)
        for reason in ("no_candidate", "reuse_cap", "budget", "not_planned"):
            assert f"courses.figureNeeds.reasons.{reason}" in keys, (language, reason)
        for outcome in ("found", "not_found", "not_searched"):
            assert f"courses.figureNeeds.literature.{outcome}" in keys, (language, outcome)
        assert "courses.figureNeeds.chipOptional" in keys, language


def test_resolution_badge_shows_only_backend_values() -> None:
    """Il badge mostra classe, misura e ppi calcolati dal backend: nessuna
    soglia di ppi ricalcolata nel frontend."""
    badge = (_FRONTEND / "components/shared/SourceFigureResolutionBadge.tsx").read_text(
        encoding="utf-8"
    )
    assert "resolution.print_ppi" in badge and "resolution.print_width_mm" in badge
    code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", badge, flags=re.S))
    assert not re.search(r"\b(100|150|200)\b", code)
    for relative in (
        "components/shared/VisualAssetEditor.tsx",
        "components/shared/SourceFigurePicker.tsx",
        "pages/org/courses/components/DocumentFiguresSection.tsx",
    ):
        assert "ResolutionBadge" in (_FRONTEND / relative).read_text(encoding="utf-8"), relative


def test_image_rev_is_in_every_image_cache_key_and_request() -> None:
    """Dopo un ri-ritaglio o un ripristino l'editor non mostra l'immagine
    vecchia: `image_rev` sta nella chiave di TanStack (staleTime infinito) e
    nell'URL (cache HTTP di 300 s)."""
    for relative in (
        "components/shared/SourceFigure.tsx",
        "components/shared/SourceFigurePicker.tsx",
        "pages/org/courses/components/DocumentFiguresSection.tsx",
    ):
        source = (_FRONTEND / relative).read_text(encoding="utf-8")
        for key in re.finditer(r'queryKey:\s*\[\s*"document-figure-image"(.*?)\]', source, re.S):
            assert "image_rev" in key.group(1), relative
        calls = re.findall(r"documentFigures\.image\(([^;]*?)\)", source, re.S)
        assert calls and all("image_rev" in call for call in calls), relative
    api = (_FRONTEND / "api" / "courses.ts").read_text(encoding="utf-8")
    assert re.search(r"params\.rev\s*=\s*rev", api)


def test_figure_needs_panel_shows_only_backend_state() -> None:
    """Il pannello delle figure consigliate mostra stato e motivo calcolati
    dal backend e scrive solo i collegamenti (mai il contenuto)."""
    panel = (_FRONTEND / "pages/org/courses/components/LessonFigureNeedsPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "figure_needs_view" in panel and "updateFigureNeedLink" in panel
    assert "lessonContent.updateLesson" not in panel
    view = (_FRONTEND / "pages/org/courses/components/CourseLessonContentView.tsx").read_text(
        encoding="utf-8"
    )
    assert "LessonFigureNeedsPanel" in view and "LessonFigureNeedsChip" in view
