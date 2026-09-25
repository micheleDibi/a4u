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
import uuid
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


@pytest.mark.parametrize("path", [None, ""])
async def test_ready_figure_requires_file(db: AsyncSession, path: str | None) -> None:
    doc = await _document(db)
    figure = build_document_figure(doc.course_id, doc.id, license="unknown")
    figure.storage_path = path
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


# Oggetti che non identificano la fonte: vuoto, chiavi estranee, sola
# licenza o pagina.
_NON_IDENTIFYING = [None, {}, {"foo": 1}, {"license": "cc_by", "page": 3}]


@pytest.mark.parametrize("attribution", _NON_IDENTIFYING)
async def test_detached_figure_needs_frozen_attribution(
    db: AsyncSession, attribution: dict[str, object] | None
) -> None:
    doc = await _document(db)
    detached = build_document_figure(
        doc.course_id,
        None,
        license="unknown",
        detached_at=datetime.now(UTC),
        attribution=attribution,
    )
    await _expect_integrity_error(db, detached)


@pytest.mark.parametrize("attribution", _NON_IDENTIFYING)
async def test_external_figure_needs_identifying_attribution(
    db: AsyncSession, attribution: dict[str, object] | None
) -> None:
    doc = await _document(db)
    figure = build_document_figure(
        doc.course_id, None, license="cc_by", source_kind="openalex", attribution=attribution
    )
    await _expect_integrity_error(db, figure)


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


async def test_deleting_course_with_attached_figures(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`delete_course` cancella le figure prima dei documenti: senza, il SET
    NULL su una figura agganciata violerebbe `uploaded_has_document`."""
    from app.models.course import Course
    from app.services import course_service, file_service

    course_id, _org, user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="ldv.pdf")
    db.add(doc)
    await db.flush()
    db.add(build_document_figure(course_id, doc.id, license="unknown"))
    await db.flush()

    async def no_disk(path: str) -> None:
        return None

    monkeypatch.setattr(file_service, "delete_upload", no_disk)
    course = await course_service._refresh_full(db, course_id)
    await course_service.delete_course(db, course=course, actor_id=user.id)
    db.expunge_all()
    assert await db.get(Course, course_id) is None
    remaining = await db.execute(
        select(CourseDocumentFigure).where(CourseDocumentFigure.course_id == course_id)
    )
    assert remaining.scalars().all() == []


async def test_duplicate_reference_survives_deleting_both_documents(db: AsyncSession) -> None:
    """Figura del documento B duplicato di una del documento A: cancellati
    figura e documento A, il riferimento si azzera; poi si cancellano anche
    figura e documento B senza errori."""
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
    await db.execute(delete(CourseDocument).where(CourseDocument.id == doc_a.id))
    await db.flush()
    await db.refresh(copy)
    assert copy.duplicate_of_id is None
    await db.execute(delete(CourseDocumentFigure).where(CourseDocumentFigure.id == copy.id))
    await db.execute(delete(CourseDocument).where(CourseDocument.id == doc_b.id))
    await db.flush()
    left = await db.execute(
        select(CourseDocument).where(CourseDocument.id.in_([doc_a.id, doc_b.id]))
    )
    assert left.scalars().all() == []


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


def _migration_checks(path: Path = _MIGRATION) -> dict[str, str]:
    """CHECK dichiarati nella migrazione: {nome: condizione}.

    Le stringhe adiacenti sono già unite dal parser (un solo Constant),
    quindi non serve importare il modulo di Alembic. Le tuple di coppie
    (nome, condizione) si leggono da `_DOC_CHECKS` (0037) e
    `_FIGURE_CHECKS` (0039).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
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
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if any(
            isinstance(t, ast.Name) and t.id in ("_DOC_CHECKS", "_FIGURE_CHECKS") for t in targets
        ):
            assert isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None
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
    # La 0039 aggiunge i CHECK degli ingressi di risoluzione: il modello li
    # dichiara tutti, quindi si confronta con l'unione delle due.
    migration = {**_migration_checks(), **_migration_checks(_MIGRATION_0039)}
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
    # I 7 CHECK nuovi di `course_document` (gli altri sono preesistenti).
    assert len(doc_migration) == 7
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


# --- migrazione 0038: letteratura aperta (WP5) ------------------------------


async def _two_courses(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    first, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    second, _org2, _user2 = await build_course(db, modules=1, lessons_per_module=1)
    return first, second


_MIGRATION_0038 = _MIGRATION.parent / "0038_literature_figures_gaps.py"
_MIGRATION_0039 = _MIGRATION.parent / "0039_figure_resolution.py"


def test_migration_0038_matches_models() -> None:
    """CHECK dello stato dei buchi e indice unico parziale delle figure
    esterne: stessa definizione nella migrazione e nei modelli."""
    from app.models.course_lesson import CourseLesson

    text = _MIGRATION_0038.read_text(encoding="utf-8")
    tree = ast.parse(text)
    checks: dict[str, str] = {}
    indexes: dict[str, tuple[list[str], str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "create_check_constraint":
            name, _table, cond = node.args[:3]
            assert isinstance(name, ast.Constant) and isinstance(cond, ast.Constant)
            checks[name.value] = cond.value
        if node.func.attr == "create_index":
            name, _table, cols = node.args[:3]
            assert isinstance(name, ast.Constant) and isinstance(cols, ast.List)
            where = next(k.value for k in node.keywords if k.arg == "postgresql_where")
            assert isinstance(where, ast.Call) and isinstance(where.args[0], ast.Constant)
            indexes[name.value] = (
                [c.value for c in cols.elts if isinstance(c, ast.Constant)],
                where.args[0].value,
            )
    # La naming convention (`ck_%(table_name)s_%(constraint_name)s`) vale per
    # modello e migrazione: si confrontano le condizioni, come per la 0037.
    lesson_checks = {
        _norm(str(c.sqltext))
        for c in CourseLesson.__table__.constraints
        if isinstance(c, CheckConstraint) and str(c.name).endswith("figures_gap_status")
    }
    assert lesson_checks == {_norm(checks["ck_course_lesson_figures_gap_status"])}
    (index,) = [
        i
        for i in CourseDocumentFigure.__table__.indexes
        if i.name == "uq_course_document_figure_external"
    ]
    columns, where = indexes["uq_course_document_figure_external"]
    assert index.unique and [c.name for c in index.columns] == columns
    assert _norm(str(index.dialect_options["postgresql"]["where"])) == _norm(where)


async def test_external_figure_is_unique_per_course(db: AsyncSession) -> None:
    course_id, other_id = await _two_courses(db)

    def external(course: uuid.UUID) -> CourseDocumentFigure:
        return build_document_figure(
            course,
            None,
            license="cc_by_sa",
            source_kind="wikimedia",
            attribution={"title": "LDV", "authors": ["Jane Doe"]},
            external_id="commons:101",
        )

    db.add_all([external(course_id), external(other_id)])
    await db.commit()
    db.add(external(course_id))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_gap_status_domain(db: AsyncSession) -> None:
    from app.models.course_lesson import CourseLesson

    course_id, _other = await _two_courses(db)
    lesson = (
        (await db.execute(select(CourseLesson).where(CourseLesson.course_id == course_id)))
        .scalars()
        .first()
    )
    assert lesson is not None
    lesson.figures_gap_status = "chissà"
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# --- migrazione 0039: ingressi della risoluzione e ri-ritaglio ------------


def test_migration_0039_lists_every_crop_mode_and_drops_what_it_adds() -> None:
    from app.models.course_document_figure import FIGURE_CROP_MODES

    text = _MIGRATION_0039.read_text(encoding="utf-8")
    for value in FIGURE_CROP_MODES:
        assert f"'{value}'" in text, f"crop_mode {value!r} assente dalla migrazione"
    tree = ast.parse(text)
    added: set[tuple[str, str]] = set()
    dropped: set[tuple[str, str]] = set()
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if (
                fn.name == "upgrade"
                and isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_column"
            ):
                table, column = node.args[:2]
                assert isinstance(table, ast.Constant) and isinstance(column, ast.Call)
                name = column.args[0]
                assert isinstance(name, ast.Constant)
                added.add((table.value, name.value))
            # Nel downgrade: `for column in (...): op.drop_column("tabella", column)`.
            if fn.name == "downgrade" and isinstance(node, ast.For):
                (stmt,) = node.body
                assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                if getattr(stmt.value.func, "attr", "") != "drop_column":
                    continue
                table_node = stmt.value.args[0]
                assert isinstance(node.iter, ast.Tuple) and isinstance(table_node, ast.Constant)
                names = [e.value for e in node.iter.elts if isinstance(e, ast.Constant)]
                dropped |= {(table_node.value, name) for name in names}
    assert added and added == dropped


async def test_figure_resolution_inputs_domain(db: AsyncSession) -> None:
    """Default dei ritagli v1 e CHECK degli ingressi di risoluzione."""
    doc = await _document(db)
    figure = build_document_figure(doc.course_id, doc.id, license="unknown")
    db.add(figure)
    await db.flush()
    await db.refresh(figure)
    assert figure.crop_version == 1
    assert figure.crop_mode is None and figure.native_ppi is None
    for field, value in (
        ("crop_mode", "upscaled"),
        ("crop_version", 0),
        ("native_ppi", 0.0),
        ("natural_width_mm", -1.0),
    ):
        bad = build_document_figure(doc.course_id, doc.id, license="unknown")
        setattr(bad, field, value)
        await _expect_integrity_error(db, bad)
