"""Garanzia G4 e vincoli del catalogo delle figure di fonte (migrazione 0037).

- la licenza è sempre valorizzata: NOT NULL **senza** default, così un
  percorso di creazione che la dimentica fallisce invece di ricevere
  'unknown' in silenzio;
- i CHECK del modello (usati da `create_all` nei test) coincidono con
  quelli della migrazione 0037 (letti dall'AST del file, non importando
  il modulo di Alembic);
- la cancellazione del documento non può lasciare una figura `uploaded`
  né staccata né collegata: il servizio deve staccarla o cancellarla prima.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_document import (
    BIBLIOGRAPHY_SOURCES,
    DOCUMENT_ORIGINS,
    FIGURES_ERROR_CODES,
    FIGURES_STATUSES,
    CourseDocument,
)
from app.models.course_document_figure import (
    FIGURE_LICENSES,
    FIGURE_REJECT_REASONS,
    FIGURE_SOURCE_KINDS,
    FIGURE_STATUSES,
    CourseDocumentFigure,
)
from app.models.organization_course_settings import OrganizationCourseSettings
from tests.course_builders import build_course, build_course_document
from tests.source_figure_builders import build_document_figure

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0037_literature_figures.py"
)


@pytest.fixture
def db(seeded_db: AsyncSession) -> AsyncSession:
    """Sessione con le lingue di seed (richieste da `build_course`)."""
    return seeded_db


async def _document(db: AsyncSession) -> CourseDocument:
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="ldv.pdf")
    db.add(doc)
    await db.flush()
    return doc


async def _expect_integrity_error(db: AsyncSession, obj: object) -> None:
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(obj)
            await db.flush()


# --- licenza sempre valorizzata ------------------------------------------


def test_license_column_has_no_default() -> None:
    column = CourseDocumentFigure.__table__.c.license
    assert column.nullable is False
    assert column.server_default is None
    assert column.default is None


async def test_insert_without_license_fails(db: AsyncSession) -> None:
    doc = await _document(db)
    figure = build_document_figure(doc.course_id, doc.id, license="unknown")
    figure.license = None  # type: ignore[assignment]
    await _expect_integrity_error(db, figure)


async def test_license_outside_vocabulary_fails(db: AsyncSession) -> None:
    doc = await _document(db)
    await _expect_integrity_error(db, build_document_figure(doc.course_id, doc.id, license="bogus"))


async def test_every_license_value_is_accepted(db: AsyncSession) -> None:
    doc = await _document(db)
    for value in FIGURE_LICENSES:
        db.add(build_document_figure(doc.course_id, doc.id, license=value))
    await db.flush()
    rows = (
        (
            await db.execute(
                select(CourseDocumentFigure.license).where(
                    CourseDocumentFigure.document_id == doc.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert sorted(rows) == sorted(FIGURE_LICENSES)


# --- CHECK di dominio ----------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_kind", "scraped"),
        ("status", "almost"),
        ("reject_reason", "ugly"),
        ("mime_type", "image/gif"),
        ("license_source", "guess"),
        ("quality_score", 9),
        ("page", 0),
    ],
)
async def test_figure_domain_checks(db: AsyncSession, field: str, value: object) -> None:
    doc = await _document(db)
    figure = build_document_figure(doc.course_id, doc.id, license="unknown")
    setattr(figure, field, value)
    await _expect_integrity_error(db, figure)


async def test_ready_figure_requires_file(db: AsyncSession) -> None:
    doc = await _document(db)
    figure = build_document_figure(doc.course_id, doc.id, license="unknown")
    figure.storage_path = None
    await _expect_integrity_error(db, figure)


async def test_uploaded_figure_needs_document_or_detachment(db: AsyncSession) -> None:
    doc = await _document(db)
    orphan = build_document_figure(doc.course_id, None, license="unknown")
    await _expect_integrity_error(db, orphan)

    detached = build_document_figure(
        doc.course_id,
        None,
        license="unknown",
        detached_at=datetime.now(UTC),
        attribution={"title": "Manuale", "authors": ["Rossi"]},
    )
    db.add(detached)
    await db.flush()


async def test_json_null_attribution_is_not_attribution(db: AsyncSession) -> None:
    """Un JSON `null` non vale come attribuzione (né per le fonti esterne
    né per le figure staccate)."""
    from sqlalchemy import text

    doc = await _document(db)
    figure = build_document_figure(
        doc.course_id,
        None,
        license="cc_by",
        source_kind="openalex",
        attribution={"title": "Articolo"},
    )
    db.add(figure)
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "UPDATE course_document_figure SET attribution = 'null'::jsonb WHERE id = :id"
                ),
                {"id": figure.id},
            )


async def test_detached_figure_needs_frozen_attribution(db: AsyncSession) -> None:
    doc = await _document(db)
    detached = build_document_figure(
        doc.course_id, None, license="unknown", detached_at=datetime.now(UTC)
    )
    await _expect_integrity_error(db, detached)


async def test_external_figure_needs_attribution(db: AsyncSession) -> None:
    doc = await _document(db)
    without = build_document_figure(
        doc.course_id, None, license="cc_by_sa", source_kind="wikimedia"
    )
    await _expect_integrity_error(db, without)

    with_tasl = build_document_figure(
        doc.course_id,
        None,
        license="cc_by_sa",
        source_kind="wikimedia",
        attribution={"title": "File:LDV.svg", "authors": ["Autore"], "license": "CC BY-SA 4.0"},
    )
    db.add(with_tasl)
    await db.flush()


async def test_unique_locator_per_document(db: AsyncSession) -> None:
    doc = await _document(db)
    db.add(build_document_figure(doc.course_id, doc.id, license="unknown", locator="p0001-a"))
    await db.flush()
    await _expect_integrity_error(
        db, build_document_figure(doc.course_id, doc.id, license="unknown", locator="p0001-a")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("origin", "scanner"),
        ("license", "unknown"),  # sul documento la licenza sconosciuta è NULL
        ("license_source", "summary"),
        ("bibliography_source", "llm"),
        ("figures_status", "done"),
        ("figures_error_code", "boom"),
        ("figures_coverage", "most"),
    ],
)
async def test_document_domain_checks(db: AsyncSession, field: str, value: object) -> None:
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="x.pdf")
    setattr(doc, field, value)
    await _expect_integrity_error(db, doc)


async def test_document_defaults_leave_extraction_unrequested(db: AsyncSession) -> None:
    doc = await _document(db)
    await db.refresh(doc)
    assert doc.origin == "upload"
    assert doc.is_own_work is False
    assert doc.license is None
    # NULL = estrazione mai richiesta: nessun backfill implicito.
    assert doc.figures_status is None
    assert doc.figures_attempts == 0


async def test_org_license_policy_override_check(db: AsyncSession) -> None:
    _course_id, org, _user = await build_course(db, modules=1, lessons_per_module=1)
    settings = OrganizationCourseSettings(
        organization_id=org.id, figure_source_license_policy="whatever"
    )
    await _expect_integrity_error(db, settings)


# --- cancellazione del documento -----------------------------------------


async def test_deleting_document_with_attached_figure_is_refused(db: AsyncSession) -> None:
    """ON DELETE SET NULL + CHECK: una figura né staccata né cancellata
    blocca la cancellazione (rete di sicurezza del servizio)."""
    doc = await _document(db)
    db.add(build_document_figure(doc.course_id, doc.id, license="unknown"))
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(delete(CourseDocument).where(CourseDocument.id == doc.id))


async def test_deleting_document_keeps_detached_figure(db: AsyncSession) -> None:
    doc = await _document(db)
    figure = build_document_figure(doc.course_id, doc.id, license="cc_by")
    db.add(figure)
    await db.flush()
    figure.detached_at = datetime.now(UTC)
    figure.attribution = {"title": "Articolo", "authors": ["Bianchi"]}
    await db.flush()
    await db.execute(delete(CourseDocument).where(CourseDocument.id == doc.id))
    await db.flush()
    await db.refresh(figure)
    assert figure.document_id is None
    assert figure.license == "cc_by"


async def test_duplicate_reference_is_cleared_when_target_is_deleted(
    db: AsyncSession,
) -> None:
    doc_a = await _document(db)
    doc_b = build_course_document(doc_a.course_id, filename="b.pdf")
    db.add(doc_b)
    await db.flush()
    original = build_document_figure(doc_a.course_id, doc_a.id, license="unknown")
    db.add(original)
    await db.flush()
    copy = build_document_figure(
        doc_b.course_id, doc_b.id, license="unknown", duplicate_of_id=original.id
    )
    db.add(copy)
    await db.flush()
    await db.execute(delete(CourseDocumentFigure).where(CourseDocumentFigure.id == original.id))
    await db.flush()
    await db.refresh(copy)
    assert copy.duplicate_of_id is None


# --- parità modello ↔ migrazione 0037 ------------------------------------


def _migration_checks() -> dict[str, str]:
    """CHECK dichiarati nella migrazione: {nome: condizione}.

    Le stringhe adiacenti sono già unite dal parser (un solo Constant),
    quindi non serve importare il modulo di Alembic.
    """
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if fname == "CheckConstraint" and node.args:
                name = next(k.value for k in node.keywords if k.arg == "name")
                assert isinstance(name, ast.Constant)
                condition = node.args[0]
                assert isinstance(condition, ast.Constant)
                found[name.value] = condition.value
            if fname == "create_check_constraint":
                name_node, _table, cond_node = node.args[:3]
                if isinstance(name_node, ast.Constant) and isinstance(cond_node, ast.Constant):
                    found[name_node.value] = cond_node.value
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_DOC_CHECKS" for t in node.targets
        ):
            assert isinstance(node.value, ast.Tuple)
            for item in node.value.elts:
                assert isinstance(item, ast.Tuple)
                name_node, cond_node = item.elts
                assert isinstance(name_node, ast.Constant)
                assert isinstance(cond_node, ast.Constant)
                found[name_node.value] = cond_node.value
    return found


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def test_migration_checks_match_models() -> None:
    """Ogni CHECK della migrazione ha la stessa condizione nel modello, e
    viceversa (i nomi passano dalla naming convention in entrambi i casi,
    quindi si confrontano le condizioni)."""
    migration = _migration_checks()
    figure_model = {
        _norm(str(c.sqltext))
        for c in CourseDocumentFigure.__table__.constraints
        if isinstance(c, CheckConstraint)
    }
    figure_migration = {
        _norm(cond)
        for name, cond in migration.items()
        if name.startswith("ck_course_document_figure_")
    }
    assert figure_model == figure_migration

    doc_model = {
        _norm(str(c.sqltext))
        for c in CourseDocument.__table__.constraints
        if isinstance(c, CheckConstraint)
    }
    doc_migration = {
        _norm(cond)
        for name, cond in migration.items()
        if name.startswith("ck_course_document_")
        and not name.startswith("ck_course_document_figure_")
    }
    assert doc_migration <= doc_model

    org_model = {
        _norm(str(c.sqltext))
        for c in OrganizationCourseSettings.__table__.constraints
        if isinstance(c, CheckConstraint)
    }
    assert _norm(migration["figure_source_license_policy_valid"]) in org_model


@pytest.mark.parametrize(
    "values",
    [
        FIGURE_LICENSES,
        FIGURE_SOURCE_KINDS,
        FIGURE_STATUSES,
        FIGURE_REJECT_REASONS,
        DOCUMENT_ORIGINS,
        BIBLIOGRAPHY_SOURCES,
        FIGURES_STATUSES,
        FIGURES_ERROR_CODES,
    ],
)
def test_migration_lists_every_value(values: tuple[str, ...]) -> None:
    text = _MIGRATION.read_text(encoding="utf-8")
    for value in values:
        assert f"'{value}'" in text, f"valore {value!r} assente dalla migrazione"


def test_migration_license_not_null_without_default() -> None:
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "Column"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "license"
        ):
            keywords = {k.arg: k.value for k in node.keywords}
            if "server_default" in keywords:
                raise AssertionError("license non deve avere server_default")
            nullable = keywords.get("nullable")
            if isinstance(nullable, ast.Constant) and nullable.value is False:
                return
    raise AssertionError("colonna license NOT NULL non trovata nella migrazione")


def test_migration_downgrade_drops_every_added_column() -> None:
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    added: set[tuple[str, str]] = set()
    dropped: set[tuple[str, str]] = set()
    downgrade_tuple: list[str] = []
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", "")
            if fn.name == "upgrade" and attr == "add_column":
                table = node.args[0]
                column = node.args[1]
                assert isinstance(table, ast.Constant) and isinstance(column, ast.Call)
                name = column.args[0]
                assert isinstance(name, ast.Constant)
                added.add((table.value, name.value))
            if fn.name == "downgrade" and attr == "drop_column":
                table, col = node.args[:2]
                if isinstance(col, ast.Constant):
                    assert isinstance(table, ast.Constant)
                    dropped.add((table.value, col.value))
        if fn.name == "downgrade":
            for node in ast.walk(fn):
                if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                    downgrade_tuple.extend(
                        e.value for e in node.iter.elts if isinstance(e, ast.Constant)
                    )
    dropped |= {("course_document", c) for c in downgrade_tuple}
    assert added == dropped
