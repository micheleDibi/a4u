"""API delle figure di fonte (WP4b): G1 (payload, round-trip), G3, G7, G8.

- catalogo `GET …/document-figures`: riga «Fonte» identica a
  `figure_attribution_line`, resa (non retroattiva) e proponibilità;
  nessuna figura di un altro corso;
- immagine `GET …/document-figures/{id}/image`: byte dallo storage,
  `Cache-Control: private`; 404 per figure di altri corsi o inventate,
  senza leggere il loro file (G7);
- esclusione `PATCH …/document-figures/{id}` (non retroattiva);
- uso `GET …/documents/{id}/figure-usage`;
- PATCH della dispensa: figura nuova di un altro corso → 422
  `source_figure_not_in_course`; di un documento escluso o non aperta
  con open_only → 422 `source_figure_not_available`; cambio di famiglia →
  422 `source_figure_format_locked`; asset invariato dopo un cambio di
  politica → 200 (U1); round-trip GET → PATCH invariato; verdetto del
  revisore potato quando la figura esce;
- PATCH delle slide: `source_figure` fra i `new_assets` → 422;
- PATCH parziale del documento: licenza, bibliografia `user` e materiale
  proprio con audit distinto; la licenza nuova passa alle figure che la
  ereditano.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.permissions import R
from app.models.audit_log import AuditLog
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.models.organization_course_settings import OrganizationCourseSettings
from app.services import remote_storage
from app.services.document_figures import storage as figure_storage
from app.services.figure_attribution import figure_attribution_line
from tests.course_builders import build_course, build_course_document
from tests.source_figure_builders import build_document_figure
from tests.test_admin_user_management import _bearer
from tests.test_permissions import _setup_user_membership


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "navy").save(buf, format="PNG")
    return buf.getvalue()


class _Storage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.reads: list[str] = []

    def download_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        if key not in self.files:
            raise remote_storage.StorageFileNotFound(key)
        return self.files[key]

    def delete(self, key: str) -> None:
        self.files.pop(key, None)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    fake = _Storage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


async def _setup(db: AsyncSession, storage: _Storage) -> dict[str, Any]:
    user, org, _m = await _setup_user_membership(db, role_code=R.MANAGER)
    course_id, _o, _u = await build_course(
        db, modules=1, lessons_per_module=1, content_status="ready"
    )
    course = await db.get(Course, course_id)
    assert course is not None
    course.organization_id = org.id
    course.assignee_user_id = user.id
    other_id, _o2, _u2 = await build_course(db, modules=1, lessons_per_module=1)
    docs = {
        "citable": build_course_document(course_id, filename="Dispense_misure.pdf"),
        "reserved": build_course_document(
            course_id, filename="riservato.pdf", policy="content_only"
        ),
        "foreign": build_course_document(other_id, filename="altro.pdf"),
    }
    docs["citable"].license = "cc_by"
    docs["citable"].bibliography = {"title": "Vibrometria laser", "authors": ["Mario Rossi"]}
    docs["citable"].bibliography_source = "user"
    db.add_all(docs.values())
    await db.flush()
    figs = {
        "good": build_document_figure(course_id, docs["citable"].id, license="cc_by"),
        "closed": build_document_figure(
            course_id, docs["citable"].id, license="all_rights_reserved"
        ),
        "reserved": build_document_figure(course_id, docs["reserved"].id, license="cc_by"),
        "foreign": build_document_figure(other_id, docs["foreign"].id, license="cc_by"),
    }
    figs["good"].preview_path = str(figs["good"].storage_path).replace(".png", "-preview.jpg")
    # Come dopo l'estrazione: la licenza viene dal documento; `closed` ha
    # una licenza propria (non ereditata).
    figs["good"].license_source = "document"
    figs["closed"].license_source = "user"
    db.add_all(figs.values())
    await db.commit()
    for fig in figs.values():
        storage.files[remote_storage.uploads_key(str(fig.storage_path))] = _png()
    storage.files[remote_storage.uploads_key(str(figs["good"].preview_path))] = b"\xff\xd8jpeg"
    lesson = (
        (await db.execute(select(CourseLesson).where(CourseLesson.course_id == course_id)))
        .scalars()
        .first()
    )
    assert lesson is not None
    return {
        "user": user.id,
        "course": course,
        "other": other_id,
        "docs": docs,
        "figs": figs,
        "lesson": lesson,
        "base": f"/api/v1/orgs/{course.organization_id}/courses/{course.id}",
    }


def _asset(asset_id: str, fig: CourseDocumentFigure | str, **kw: Any) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "format": "source_figure",
        "content": str(fig.id) if isinstance(fig, CourseDocumentFigure) else fig,
        "caption": kw.get("caption", "Schema del vibrometro."),
        "alt_text": "schema",
    }


async def test_catalog_payload_and_course_scope(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    s = await _setup(seeded_db, storage)
    res = await client.get(f"{s['base']}/document-figures", headers=_bearer(s["user"]))
    assert res.status_code == 200, res.text
    items = {item["id"]: item for item in res.json()}
    figs, docs = s["figs"], s["docs"]
    assert str(figs["foreign"].id) not in items
    good = items[str(figs["good"].id)]
    expected = figure_attribution_line(figs["good"], docs["citable"], language="it")
    assert good["attribution"] == expected and expected.startswith("Fonte: Mario Rossi")
    assert good["renderable"] and good["selectable"] and good["reason"] is None
    assert good["document_filename"] == "Dispense_misure.pdf"
    reserved = items[str(figs["reserved"].id)]
    # Fonte riservata = materiale del docente: resa e proposta, con una riga
    # che non nomina il documento.
    assert reserved["renderable"] and reserved["selectable"] and reserved["reason"] is None
    assert reserved["attribution"] == "Fonte: materiale del docente"
    # Nessun percorso dello storage nel payload.
    assert "uploads" not in res.text
    # Revisione dell'immagine: 12 cifre esadecimali, diversa fra figure con
    # file diversi; cambia quando cambia il file (ri-ritaglio v2).
    assert len(good["image_rev"]) == 12 and good["image_rev"] != reserved["image_rev"]
    figs["good"].storage_path = str(figs["good"].storage_path).replace(".png", "-v2.png")
    await seeded_db.commit()
    again = await client.get(f"{s['base']}/document-figures", headers=_bearer(s["user"]))
    changed = {item["id"]: item for item in again.json()}[str(figs["good"].id)]
    assert changed["image_rev"] != good["image_rev"]


async def test_catalog_filtered_by_document(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    """Riassunto strutturato: solo le figure del documento chiesto; il
    documento di un altro corso non restituisce le sue figure."""
    s = await _setup(seeded_db, storage)
    figs, docs = s["figs"], s["docs"]
    url, headers = f"{s['base']}/document-figures", _bearer(s["user"])
    res = await client.get(url, params={"document_id": str(docs["citable"].id)}, headers=headers)
    assert res.status_code == 200, res.text
    ids = {item["id"] for item in res.json()}
    assert str(figs["good"].id) in ids and str(figs["reserved"].id) not in ids
    assert {item["document_id"] for item in res.json()} == {str(docs["citable"].id)}
    foreign_doc = figs["foreign"].document_id
    res = await client.get(url, params={"document_id": str(foreign_doc)}, headers=headers)
    assert res.status_code == 200 and res.json() == []
    res = await client.get(url, params={"document_id": "non-un-uuid"}, headers=headers)
    assert res.status_code == 422


async def test_image_endpoint_and_idor(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    s = await _setup(seeded_db, storage)
    figs = s["figs"]
    url = f"{s['base']}/document-figures/{figs['good'].id}/image"
    res = await client.get(url, headers=_bearer(s["user"]))
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.headers["cache-control"].startswith("private")
    assert res.content == _png()
    preview = await client.get(url, params={"preview": "true"}, headers=_bearer(s["user"]))
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    reads_before = list(storage.reads)
    for fid in (figs["foreign"].id, uuid.uuid4()):
        res = await client.get(
            f"{s['base']}/document-figures/{fid}/image", headers=_bearer(s["user"])
        )
        assert res.status_code == 404
    # G7: il file della figura dell'altro corso non viene mai letto.
    assert storage.reads == reads_before
    # Figura di un documento content_only: resta visibile dove è collocata (U1).
    res = await client.get(
        f"{s['base']}/document-figures/{figs['reserved'].id}/image", headers=_bearer(s["user"])
    )
    assert res.status_code == 200
    # Senza autenticazione: mai l'immagine.
    assert (await client.get(url)).status_code in (401, 403)


async def test_exclusion_and_usage(client: Any, seeded_db: AsyncSession, storage: _Storage) -> None:
    s = await _setup(seeded_db, storage)
    figs, lesson = s["figs"], s["lesson"]
    res = await client.patch(
        f"{s['base']}/document-figures/{figs['good'].id}",
        json={"excluded_by_user": True},
        headers=_bearer(s["user"]),
    )
    assert res.status_code == 200, res.text
    assert res.json()["selectable"] is False and res.json()["reason"] == "excluded_by_user"
    assert res.json()["renderable"] is True
    res = await client.patch(
        f"{s['base']}/document-figures/{figs['foreign'].id}",
        json={"excluded_by_user": True},
        headers=_bearer(s["user"]),
    )
    assert res.status_code == 404
    lesson.content_raw = {"visual_assets": [_asset("fig-src-1", figs["good"])]}
    await seeded_db.commit()
    res = await client.get(
        f"{s['base']}/documents/{s['docs']['citable'].id}/figure-usage",
        headers=_bearer(s["user"]),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["figures_total"] == 2 and body["figures_used"] == 1
    assert body["lessons"][0]["asset_ids"] == ["fig-src-1"]


async def _patch_content(client: Any, s: dict[str, Any], assets: list[dict[str, Any]]) -> Any:
    return await client.patch(
        f"{s['base']}/lessons/{s['lesson'].id}/content",
        json={"visual_assets": assets},
        headers=_bearer(s["user"]),
    )


async def test_lesson_patch_guards_new_or_changed_source_figures(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    s = await _setup(seeded_db, storage)
    figs, lesson = s["figs"], s["lesson"]
    lesson.content_raw = {
        "introduction": "Intro [FIG:fig-src-1].",
        "sections": [],
        "visual_assets": [_asset("fig-src-1", figs["good"])],
    }
    lesson.content_figure_review = {
        "version": 1,
        "figures": {"fig-src-1": {"coherence": "coerente", "pairs": []}},
    }
    await seeded_db.commit()
    good = _asset("fig-src-1", figs["good"])

    res = await _patch_content(client, s, [good, _asset("fig-src-2", figs["foreign"])])
    assert res.status_code == 422 and res.json()["code"] == "source_figure_not_in_course"
    res = await _patch_content(client, s, [good, _asset("fig-src-2", str(uuid.uuid4()))])
    assert res.json()["code"] == "source_figure_not_in_course"
    # Fonte riservata = materiale del docente: la figura nuova è ammessa…
    res = await _patch_content(client, s, [good, _asset("fig-src-2", figs["reserved"])])
    assert res.status_code == 200, res.text
    assert (await _patch_content(client, s, [good])).status_code == 200
    # …quella di un documento escluso no.
    s["docs"]["reserved"].citation_policy = "excluded"
    await seeded_db.commit()
    res = await _patch_content(client, s, [good, _asset("fig-src-2", figs["reserved"])])
    assert res.status_code == 422 and res.json()["code"] == "source_figure_not_available"
    assert res.json()["meta"]["reason"] == "document_excluded"
    locked = {**good, "format": "image", "content": "lesson_assets/x.png"}
    res = await _patch_content(client, s, [locked])
    assert res.status_code == 422 and res.json()["code"] == "source_figure_format_locked"

    # Round-trip: lo stesso contenuto torna indietro invariato.
    res = await _patch_content(client, s, [good])
    assert res.status_code == 200, res.text
    await seeded_db.refresh(lesson)
    assert lesson.content_raw["visual_assets"] == [good]

    # U1: il documento cambia politica, l'asset invariato resta ammesso.
    s["docs"]["citable"].citation_policy = "excluded"
    await seeded_db.commit()
    res = await _patch_content(client, s, [{**good, "caption": "Nuova didascalia."}])
    assert res.status_code == 200, res.text
    # …ma non si può ri-puntare a un'altra figura del documento escluso.
    res = await _patch_content(client, s, [_asset("fig-src-1", figs["closed"])])
    assert res.json()["code"] == "source_figure_not_available"

    # Il verdetto del revisore si pota quando la figura esce.
    res = await _patch_content(client, s, [])
    assert res.status_code == 200
    await seeded_db.refresh(lesson)
    assert lesson.content_figure_review is None


async def test_lesson_patch_duplicate_in_lesson_and_over_the_reuse_cap(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    """Mai due volte la stessa figura nella stessa lezione: un doppione nuovo
    dà 422, uno già salvato passa (U1). Il tetto di riuso non blocca il
    docente: la figura già in K altre lezioni entra, con un audit."""
    s = await _setup(seeded_db, storage)
    figs, lesson = s["figs"], s["lesson"]
    good = _asset("fig-src-1", figs["good"])
    lesson.content_raw = {"introduction": "Intro.", "sections": [], "visual_assets": [good]}
    await seeded_db.commit()

    dup = _asset("fig-src-2", figs["good"])
    res = await _patch_content(client, s, [good, dup])
    assert res.status_code == 422 and res.json()["code"] == "source_figure_duplicate_in_lesson"
    assert res.json()["meta"]["index"] == 1
    # Il doppione nuovo PRIMA di quello salvato: l'errore resta sulla card
    # nuova, non su quella già in lezione.
    res = await _patch_content(client, s, [dup, good])
    assert res.status_code == 422 and res.json()["code"] == "source_figure_duplicate_in_lesson"
    assert (res.json()["meta"]["index"], res.json()["meta"]["asset_id"]) == (0, "fig-src-2")

    lesson.content_raw = {"introduction": "Intro.", "sections": [], "visual_assets": [good, dup]}
    await seeded_db.commit()
    assert (await _patch_content(client, s, [good, dup])).status_code == 200

    for position, code in ((2, "M1.L2"), (3, "M1.L3")):
        seeded_db.add(
            CourseLesson(
                module_id=lesson.module_id,
                course_id=lesson.course_id,
                position=position,
                lesson_code=code,
                title="Altra lezione",
                summary="Altra.",
                learning_objectives=[],
                mandatory_topics=[],
                prerequisites=[],
                section_outline=[],
                content_status="ready",
                content_raw={"visual_assets": [_asset("fig-src-9", figs["reserved"])]},
            )
        )
    await seeded_db.commit()
    res = await _patch_content(client, s, [good, _asset("fig-src-3", figs["reserved"])])
    assert res.status_code == 200, res.text
    audits = (
        (
            await seeded_db.execute(
                select(AuditLog).where(
                    AuditLog.action == "course.lesson.content.source_figure_over_cap",
                    AuditLog.target_id == str(lesson.id),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].payload["figure_id"] == str(figs["reserved"].id)
    assert audits[0].payload["used_in"] == ["M1.L2", "M1.L3"]
    assert audits[0].payload["cap"] == 2
    # Rimandare gli stessi asset (auto-save dell'editor) non registra di
    # nuovo: la figura oltre il tetto è ormai della lezione.
    res = await _patch_content(client, s, [good, _asset("fig-src-3", figs["reserved"])])
    assert res.status_code == 200, res.text
    again = await seeded_db.execute(
        select(AuditLog).where(
            AuditLog.action == "course.lesson.content.source_figure_over_cap",
            AuditLog.target_id == str(lesson.id),
        )
    )
    assert len(again.scalars().all()) == 1


async def test_lesson_patch_placed_figure_rename_quality_and_format_lock(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    s = await _setup(seeded_db, storage)
    figs, lesson, course = s["figs"], s["lesson"], s["course"]
    citable = s["docs"]["citable"]
    useless = build_document_figure(
        course.id, citable.id, license="cc_by", is_useful_for_teaching=False
    )
    logo = build_document_figure(course.id, citable.id, license="cc_by", kind="logo_or_decoration")
    seeded_db.add_all([useless, logo])
    lesson.content_raw = {
        "introduction": "Intro [FIG:fig-src-1] e [FIG:gen-1].",
        "sections": [],
        "visual_assets": [
            _asset("fig-src-1", figs["good"]),
            {"asset_id": "gen-1", "format": "dot", "content": "digraph{a->b}", "caption": "C"},
        ],
    }
    # U1: il documento diventa riservato dopo la collocazione.
    citable.citation_policy = "content_only"
    await seeded_db.commit()
    generated = lesson.content_raw["visual_assets"][1]

    # Rinominata (anche con l'UUID in maiuscolo): resta la stessa figura già
    # collocata → ammessa, e il riferimento si salva in forma canonica.
    renamed = _asset("SRC-rinominata", str(figs["good"].id).upper())
    res = await _patch_content(client, s, [renamed, generated])
    assert res.status_code == 200, res.text
    await seeded_db.refresh(lesson)
    saved = lesson.content_raw["visual_assets"][0]
    assert saved["asset_id"] == "SRC-rinominata" and saved["content"] == str(figs["good"].id)

    # Un asset generato non diventa di fonte con lo stesso asset_id.
    res = await _patch_content(client, s, [saved, {**_asset("gen-1", figs["good"])}])
    assert res.status_code == 422 and res.json()["code"] == "source_figure_format_locked"

    # Figura NUOVA: stessi filtri del catalogo (utilità, qualità, loghi) e
    # l'errore porta la posizione della card.
    citable.citation_policy = "citable"
    await seeded_db.commit()
    for fig, reason in ((useless, "not_useful"), (logo, "excluded_kind")):
        res = await _patch_content(client, s, [saved, generated, _asset("fig-new", fig)])
        body = res.json()
        assert res.status_code == 422 and body["code"] == "source_figure_not_available"
        assert body["meta"]["reason"] == reason
        assert body["meta"]["errors"][0]["loc"] == ["visual_assets", 2, "content"]


async def test_open_only_organization_policy(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    s = await _setup(seeded_db, storage)
    figs = s["figs"]
    seeded_db.add(
        OrganizationCourseSettings(
            organization_id=s["course"].organization_id,
            figure_source_license_policy="open_only",
        )
    )
    await seeded_db.commit()
    res = await _patch_content(client, s, [_asset("fig-src-1", figs["closed"])])
    assert res.status_code == 422 and res.json()["meta"]["reason"] == "license_not_open"
    res = await _patch_content(client, s, [_asset("fig-src-1", figs["good"])])
    assert res.status_code == 200, res.text
    catalog = await client.get(f"{s['base']}/document-figures", headers=_bearer(s["user"]))
    closed = next(i for i in catalog.json() if i["id"] == str(figs["closed"].id))
    assert closed["selectable"] is False and closed["reason"] == "license_not_open"


async def test_slides_patch_rejects_source_figure_new_assets(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    s = await _setup(seeded_db, storage)
    res = await client.patch(
        f"{s['base']}/lessons/{s['lesson'].id}/slides",
        json={"new_assets": [_asset("fig-src-9", s["figs"]["good"])]},
        headers=_bearer(s["user"]),
    )
    assert res.status_code == 422


async def test_partial_document_patch_metadata_and_license_propagation(
    client: Any, seeded_db: AsyncSession, storage: _Storage
) -> None:
    s = await _setup(seeded_db, storage)
    doc, figs = s["docs"]["citable"], s["figs"]
    url = f"{s['base']}/documents/{doc.id}"
    assert (await client.patch(url, json={}, headers=_bearer(s["user"]))).status_code == 422
    res = await client.patch(url, json={"unknown": 1}, headers=_bearer(s["user"]))
    assert res.status_code == 422
    res = await client.patch(
        url,
        json={
            "license": "cc_by_sa",
            "bibliography": {"title": "Appunti di vibrometria", "authors": ["Anna Bianchi"]},
            "is_own_work": True,
        },
        headers=_bearer(s["user"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["license"] == "cc_by_sa" and body["license_source"] == "user"
    assert body["bibliography_source"] == "user" and body["is_own_work"] is True
    assert body["citation_policy"] == "citable"
    fresh = (
        await seeded_db.execute(
            select(CourseDocumentFigure)
            .where(CourseDocumentFigure.document_id == doc.id)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    licenses = {f.id: f.license for f in fresh}
    assert licenses[figs["good"].id] == "cc_by_sa"
    assert licenses[figs["closed"].id] == "all_rights_reserved"
    actions = set(
        (await seeded_db.execute(select(AuditLog.action).where(AuditLog.target_id == str(doc.id))))
        .scalars()
        .all()
    )
    assert "course.document.metadata.update" in actions
    # L'audit registra il valore precedente e il nuovo (dichiarazioni del
    # docente che decidono la riga «Fonte» e la politica open_only).
    payloads = (
        (
            await seeded_db.execute(
                select(AuditLog.payload).where(
                    AuditLog.target_id == str(doc.id),
                    AuditLog.action == "course.document.metadata.update",
                )
            )
        )
        .scalars()
        .all()
    )
    changes = payloads[0]["changes"]
    assert changes["license"] == {"from": "cc_by", "to": "cc_by_sa"}
    assert changes["is_own_work"] == {"from": False, "to": True}
    assert changes["bibliography"] == {"from_source": "user", "to": "set"}
    # Solo la politica: il vecchio contratto resta valido.
    res = await client.patch(
        url, json={"citation_policy": "content_only"}, headers=_bearer(s["user"])
    )
    assert res.status_code == 200 and res.json()["citation_policy"] == "content_only"
    # Cancellazione della bibliografia e della licenza.
    res = await client.patch(
        url, json={"bibliography": None, "license": None}, headers=_bearer(s["user"])
    )
    assert res.status_code == 200
    assert res.json()["bibliography"] is None and res.json()["license"] is None
    refreshed = await seeded_db.get(CourseDocument, doc.id, populate_existing=True)
    assert refreshed is not None and refreshed.bibliography_source is None
    assert figure_storage.belongs_to_course(figs["good"].storage_path, s["course"].id)


def test_suggested_caption_drops_the_source_tail_and_fits_the_asset() -> None:
    from app.services.source_figure_api_service import suggested_caption

    fig = build_document_figure(uuid.uuid4(), uuid.uuid4(), license="cc_by")
    fig.source_caption = "Schema del vibrometro. Fonte: Rossi, 2020"
    assert suggested_caption(fig) == "Schema del vibrometro."
    fig.source_caption = None
    fig.description = "parola " * 200
    long = suggested_caption(fig)
    assert len(long) <= 600 and long.endswith("…")


# --- risoluzione effettiva (doc 18 §22) -------------------------------------------

_TINY_DIMS = {
    "width": 300,
    "height": 200,
    "dpi": 150,
    "native_ppi": 60.0,
    "is_vector": False,
    "bbox": {"l": 72.0, "t": 100.0, "r": 216.0, "b": 196.0, "page_w": 595.0, "page_h": 842.0},
}


def _rules(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    from app.services import source_figure_api_service, source_figure_catalog

    patched = get_settings().model_copy(update={"figure_resolution_rules_enabled": enabled})
    for module in (source_figure_api_service, source_figure_catalog):
        monkeypatch.setattr(module, "get_settings", lambda: patched)


@pytest.mark.parametrize("enabled", [True, False])
async def test_resolution_in_the_catalog_and_the_patch(
    client: Any,
    seeded_db: AsyncSession,
    storage: _Storage,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    _rules(monkeypatch, enabled)
    s = await _setup(seeded_db, storage)
    course, lesson = s["course"], s["lesson"]
    tiny = build_document_figure(course.id, s["docs"]["citable"].id, license="cc_by", **_TINY_DIMS)
    seeded_db.add(tiny)
    await seeded_db.commit()
    storage.files[remote_storage.uploads_key(str(tiny.storage_path))] = _png()
    res = await client.get(f"{s['base']}/document-figures", headers=_bearer(s["user"]))
    items = {item["id"]: item for item in res.json()}
    good, small = items[str(s["figs"]["good"].id)], items[str(tiny.id)]
    if not enabled:
        assert good["resolution"] is None and small["resolution"] is None
        assert small["selectable"] and small["reason"] is None
        return
    assert good["resolution"]["class"] == "good" and good["resolution"]["print_ppi"] >= 200
    assert good["resolution"]["basis"] == "bbox" and good["resolution"]["slide_class"]
    assert small["resolution"]["class"] == "unusable"
    assert small["resolution"]["print_ppi"] >= 100  # una collocata esce comunque a 100 ppi
    assert not small["selectable"] and small["reason"] == "resolution_unusable"
    # PATCH: una figura NUOVA sotto il minimo → 422; già collocata passa (U1).
    res = await _patch_content(client, s, [_asset("fig-small", tiny)])
    assert res.status_code == 422 and res.json()["code"] == "source_figure_not_available"
    assert res.json()["meta"]["reason"] == "resolution_unusable"
    lesson.content_raw = {
        "introduction": "Intro.",
        "sections": [],
        "visual_assets": [_asset("fig-small", tiny)],
    }
    await seeded_db.commit()
    res = await _patch_content(client, s, [_asset("fig-small", tiny, caption="Nuova.")])
    assert res.status_code == 200, res.text
