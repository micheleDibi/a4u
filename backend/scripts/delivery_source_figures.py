"""Prova di consegna §7.3: lo schema del vibrometro dai documenti del corso
arriva, con didascalia e fonte, in dispensa, PDF dispensa, slide, PDF slide
e frame video.

Tutto in-process, nessun server avviato, su un DATABASE USA-E-GETTA
(`a4u_delivery…`) e uno storage locale in `--out/uploads`:

1. corso di prova «Misure meccaniche» con due lezioni (catena di misura;
   vibrometria laser Doppler) e due PDF citabili: l'articolo arXiv
   2304.11054 (CC BY 4.0, figura 1: il banco del vibrometro) e la dispensa
   di prova del repo (`tests/fixtures/source_figures`, «Figura 2.1. Schema
   di principio di un vibrometro laser Doppler eterodina»);
2. estrazione delle figure con il worker vero (Vision reale, PROMPT 18);
   con `--engine docling` il SOLO sottoprocesso di estrazione gira nel
   container `--docling-image` (dipendenze e modelli montati: Docling non è
   installato sul Mac); `--engine heuristic` resta tutto locale;
3. «prima»: PROMPT 3 come su main (niente catalogo, niente riga statica);
   «dopo»: la funzione attiva (catalogo, fusione, revisore PROMPT 19);
4. slide (PROMPT 5, 8c), PDF dispensa, PDF slide, frame video 1980×1400;
5. controlli (`report.json`): figura di fonte presente, stessa riga «Fonte»
   e stesso ritaglio (pHash) nei due PDF, fascia della fonte nel frame della
   slide con la figura e fuori dall'avatar, budget (a) rispettato, costi.

Uso (dalla cartella `backend/`, OPENAI_API_KEY nell'ambiente; costo ~2-4 $):

    docker exec a4u-postgres createdb -U a4u a4u_delivery
    DATABASE_URL=postgresql+asyncpg://a4u:a4u_dev_password@localhost:5432/a4u_delivery \\
    UPLOAD_DIR=<out>/uploads GENERATED_PDFS_DIR=<out>/generated_pdfs JWT_SECRET=<40 caratteri> \\
        python -m scripts.delivery_source_figures --arxiv-pdf <pdf> --out <out> --apply

Senza `--apply` non chiama OpenAI e si ferma dopo aver creato il corso.
Rifiuta ogni DB il cui nome non sia `a4u_delivery…`.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import shutil
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DB_RE = r"^a4u_delivery[a-z0-9_]*$"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--arxiv-pdf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Chiama davvero OpenAI.")
    parser.add_argument("--engine", choices=("docling", "heuristic"), default="docling")
    parser.add_argument("--docling-image", default="a4u-explore-docling")
    parser.add_argument("--docling-deps", type=Path, default=None)
    parser.add_argument("--docling-models", type=Path, default=None)
    parser.add_argument(
        "--resume-course",
        type=uuid.UUID,
        default=None,
        help="Riprende dalle slide un corso già creato ed estratto (niente nuove chiamate "
        "di estrazione né del PROMPT 3).",
    )
    return parser


def _docker_spawn(image: str, backend_dir: Path, deps: Path, models: Path) -> Any:
    """`ChildSession._spawn` che avvia il figlio nel container (solo per la
    prova: in produzione il figlio gira nell'immagine del backend)."""
    from app.services.document_figures import runner

    async def spawn(self: Any, *extra_args: str) -> asyncio.subprocess.Process:
        workdir = str(self.workdir)
        env = runner.child_env(
            self.workdir, threads=self.config.threads, artifacts_path="/opt/docling-models"
        )
        env_args: list[str] = []
        for key, value in env.items():
            if key not in ("PATH", "PYTHONPATH"):
                env_args += ["-e", f"{key}={value}"]
        cmd = [
            "docker",
            "run",
            "-i",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{backend_dir}:/app:ro",
            "-v",
            f"{deps}:/deps:ro",
            "-v",
            f"{models}:/opt/docling-models:ro",
            "-v",
            f"{workdir}:{workdir}",
            "-w",
            workdir,
            "-e",
            "PYTHONPATH=/app:/deps",
            *env_args,
            image,
            "python",
            "-m",
            runner.CHILD_MODULE,
            *extra_args,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=4 * 1024 * 1024,
        )
        self.proc = proc
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc))
        return proc

    return spawn


async def _setup(db: Any, *, arxiv: Path, fixture_pdf: Path) -> dict[str, Any]:
    from app.core.security import hash_password
    from app.models.course import Course
    from app.models.course_document import CourseDocument
    from app.models.course_lesson import CourseLesson
    from app.models.course_module import CourseModule
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import remote_storage

    now = datetime.now(UTC)
    user = User(
        email=f"delivery-{uuid.uuid4().hex[:8]}@example.org",
        password_hash=hash_password("Password123!"),
        full_name="Docente di Prova",
        is_active=True,
    )
    org = Organization(name=f"Consegna-{uuid.uuid4().hex[:6]}", email="consegna@example.org")
    db.add_all([user, org])
    await db.flush()
    course = Course(
        organization_id=org.id,
        title="Misure meccaniche e termiche",
        objectives="Progettare ed eseguire misure meccaniche, anche senza contatto.",
        language_code="it",
        cfu=6,
        modules_count=1,
        lessons_per_module=2,
        lesson_duration_minutes=20,
        assessment_lesson_enabled=False,
        multiple_choice_questions_count=0,
        open_questions_count=0,
        assignee_user_id=user.id,
        created_by_user_id=user.id,
        status="lessons_structure_approved",
        didactic_setup_confirmed_at=now,
        glossary_status="ready",
    )
    db.add(course)
    await db.flush()
    module = CourseModule(
        course_id=course.id,
        position=1,
        module_code="M1",
        title="Misura delle vibrazioni",
        lessons_structure_status="approved",
        lessons_structure_approved_at=now,
    )
    db.add(module)
    await db.flush()
    lessons = [
        CourseLesson(
            module_id=module.id,
            course_id=course.id,
            position=1,
            lesson_code="M1.L1",
            title="Catena di misura e sensori di vibrazione",
            summary="Blocchi della catena di misura, accelerometri e condizionamento.",
            is_assessment=False,
            is_introductory=False,
            learning_objectives=["Descrivere i blocchi di una catena di misura"],
            mandatory_topics=[{"topic_id": "T1", "title": "Catena di misura"}],
            prerequisites=[],
            section_outline=[
                {
                    "section_id": "S1",
                    "title": "La catena di misura",
                    "purpose": "Blocchi funzionali",
                    "covers_topic_ids": ["T1"],
                },
                {"section_id": "S2", "title": "Accelerometri", "purpose": "Principio"},
                {"section_id": "S3", "title": "Condizionamento", "purpose": "Dal sensore al dato"},
            ],
            content_status="empty",
            slides_status="empty",
            speech_status="empty",
        ),
        CourseLesson(
            module_id=module.id,
            course_id=course.id,
            position=2,
            lesson_code="M1.L2",
            title="Vibrometria laser Doppler",
            summary=(
                "Principio del vibrometro laser Doppler: effetto Doppler, interferometro, "
                "cella di Bragg ed eterodina; banco di misura e confronto con l'accelerometro."
            ),
            is_assessment=False,
            is_introductory=False,
            learning_objectives=["Descrivere lo schema di un vibrometro laser Doppler"],
            mandatory_topics=[
                {"topic_id": "T1", "title": "Effetto Doppler e interferometria"},
                {"topic_id": "T2", "title": "Schema del vibrometro laser Doppler"},
                {"topic_id": "T3", "title": "Banco di misura del vibrometro"},
            ],
            prerequisites=[],
            section_outline=[
                {
                    "section_id": "S1",
                    "title": "Effetto Doppler e interferometria",
                    "purpose": "Principio fisico",
                    "covers_topic_ids": ["T1"],
                },
                {
                    "section_id": "S2",
                    "title": "Lo schema del vibrometro laser Doppler",
                    "purpose": "Componenti: laser, beam splitter, cella di Bragg, fotorivelatore",
                    "covers_topic_ids": ["T2"],
                },
                {
                    "section_id": "S3",
                    "title": "Il banco di misura",
                    "purpose": "Montaggio sperimentale e confronto con l'accelerometro",
                    "covers_topic_ids": ["T3"],
                },
                {
                    "section_id": "S4",
                    "title": "Elaborazione del segnale",
                    "purpose": "Dal segnale del fotorivelatore alla velocità",
                },
            ],
            content_status="empty",
            slides_status="empty",
            speech_status="empty",
        ),
    ]
    db.add_all(lessons)
    await db.flush()

    storage = remote_storage.get_storage()
    docs: dict[str, CourseDocument] = {}
    for key, source, meta in (
        (
            "arxiv",
            arxiv,
            {
                "filename": "2304.11054_laser_doppler_vibrometer.pdf",
                "license": "cc_by",
                "bibliography": None,
                "own": False,
            },
        ),
        (
            "dispensa",
            fixture_pdf,
            {
                "filename": "Dispensa_vibrometria.pdf",
                "license": None,
                "bibliography": {
                    "title": "Dispensa di vibrometria laser",
                    "authors": ["Docente di Prova"],
                },
                "own": True,
            },
        ),
    ):
        data = source.read_bytes()
        rel = f"/uploads/courses/{course.id}/{uuid.uuid4().hex}.pdf"
        await asyncio.to_thread(storage.upload_bytes, remote_storage.uploads_key(rel), data)
        doc = CourseDocument(
            course_id=course.id,
            filename_original=str(meta["filename"]),
            filename_stored=rel.rsplit("/", 1)[-1],
            file_path=rel,
            mime_type="application/pdf",
            size_bytes=len(data),
            uploaded_by_user_id=user.id,
            summary_status="ready",
            summary={"source_title": "", "abstract": "", "authors_and_references": []},
            citation_policy="citable",
            license=meta["license"],
            license_source="user" if meta["license"] else None,
            bibliography=meta["bibliography"],
            bibliography_source="user" if meta["bibliography"] else None,
            is_own_work=bool(meta["own"]),
        )
        db.add(doc)
        docs[key] = doc
    await db.commit()
    return {"course_id": course.id, "user_id": user.id, "lessons": lessons, "docs": docs}


def _strip_static_line(prompt: str) -> str:
    marker = "- FIGURE DI FONTE — solo se il messaggio offre un catalogo: vanno in\n"
    start = prompt.find(marker)
    if start < 0:
        return prompt
    end = prompt.find("\n", start + len(marker)) + 1
    return prompt[:start] + prompt[end:]


def _phash_of(data: bytes) -> str | None:
    from PIL import Image

    from app.services.document_figures.phash import phash

    try:
        return phash(Image.open(io.BytesIO(data)).convert("RGB"))
    except Exception:
        return None


def _pdf_text_and_hashes(data: bytes) -> tuple[str, list[str]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    hashes: list[str] = []
    for page in reader.pages:
        for image in page.images:
            value = _phash_of(image.data)
            if value:
                hashes.append(value)
    return " ".join(text.split()), hashes


async def run(args: argparse.Namespace) -> int:
    from sqlalchemy import select, text
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings

    settings = get_settings()
    if not re.match(_DB_RE, make_url(settings.database_url).database or ""):
        print("DATABASE_URL non punta a un DB a4u_delivery…: nessuna scrittura.", file=sys.stderr)
        return 2

    from app.db.base import Base
    from app.db.seed import ensure_seed
    from app.db.session import async_session_factory, engine
    from app.models.course_document_figure import CourseDocumentFigure
    from app.models.course_lesson import CourseLesson
    from app.services import course_document_figures_worker as figures_worker
    from app.services import course_lesson_content_service as content_svc
    from app.services import course_lesson_content_worker as content_worker
    from app.services import course_lesson_pdf_service as pdf_svc
    from app.services import course_lesson_slides_pdf_service as slides_pdf_svc
    from app.services import course_lesson_slides_worker as slides_worker
    from app.services import document_figures_service, remote_storage, source_figure_catalog
    from app.services import lesson_slides_video_render_service as video_svc
    from app.services import openai_lesson_content_service as openai_svc
    from app.services.document_figures import runner
    from app.services.document_figures import storage as figure_storage
    from app.services.slide_geometry import SlideGeometry
    from app.services.source_figure_service import resolve_source_figures
    from tests.fixtures.source_figures.build import PDF_NAME, build_all

    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "tmp").mkdir(exist_ok=True)
    tempfile.tempdir = str(out / "tmp")
    fixtures = out / "fixtures"
    fixtures.mkdir(exist_ok=True)
    build_all(fixtures)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as db:
        await ensure_seed(db)
        await db.commit()
        if args.resume_course is not None:
            from app.models.course_document import CourseDocument

            docs = (
                (
                    await db.execute(
                        select(CourseDocument).where(CourseDocument.course_id == args.resume_course)
                    )
                )
                .scalars()
                .all()
            )
            lessons = (
                (
                    await db.execute(
                        select(CourseLesson).where(CourseLesson.course_id == args.resume_course)
                    )
                )
                .scalars()
                .all()
            )
            setup = {
                "course_id": args.resume_course,
                "user_id": None,
                "lessons": list(lessons),
                "docs": {d.filename_original: d for d in docs},
            }
        else:
            setup = await _setup(db, arxiv=args.arxiv_pdf, fixture_pdf=fixtures / PDF_NAME)
    print(f"corso {setup['course_id']} pronto; motore {args.engine}")
    if not args.apply:
        print("dry-run: nessuna chiamata OpenAI.")
        return 0

    patched = settings.model_copy(
        update={
            "figure_extraction_enabled": True,
            "figure_extraction_engine": args.engine,
            "figure_extraction_min_available_mb": 0,
        }
    )
    figures_worker.get_settings = lambda: patched  # type: ignore[assignment]
    document_figures_service.get_settings = lambda: patched  # type: ignore[assignment]
    if args.engine == "docling":
        backend_dir = Path(__file__).resolve().parents[1]
        runner.ChildSession._spawn = _docker_spawn(  # type: ignore[method-assign]
            args.docling_image,
            backend_dir,
            args.docling_deps.resolve(),
            args.docling_models.resolve(),
        )

    report: dict[str, Any] = {"engine": args.engine, "course_id": str(setup["course_id"])}
    # 1. Estrazione delle figure (worker vero, Vision reale).
    async with async_session_factory() as db:
        from app.models.course import Course

        course = await db.get(Course, setup["course_id"])
        assert course is not None
        if args.resume_course is None:
            for doc in setup["docs"].values():
                fresh = await db.get(type(doc), doc.id)
                assert fresh is not None
                await document_figures_service.request_extraction(
                    db, course=course, doc=fresh, actor_id=setup["user_id"]
                )
            while True:
                claimed = await figures_worker.claim_next(db)
                if claimed is None:
                    break
                await figures_worker.process_document(db, claimed)
                print(f"  estrazione {claimed.filename_original}: {claimed.figures_status}")
        rows = list(
            (
                await db.execute(
                    select(CourseDocumentFigure).where(
                        CourseDocumentFigure.course_id == setup["course_id"],
                        CourseDocumentFigure.status == "ready",
                    )
                )
            )
            .scalars()
            .all()
        )
        report["figures"] = [
            {
                "id": str(r.id),
                "document_id": str(r.document_id),
                "page": r.page,
                "kind": r.kind,
                "quality": r.quality_score,
                "useful": r.is_useful_for_teaching,
                "caption": r.source_caption,
                "description": r.description,
            }
            for r in rows
        ]
        report["vision_cost_usd"] = round(
            sum(float((r.vision_usage or {}).get("cost_usd") or 0) for r in rows), 5
        )
        docs_after = {
            key: await db.get(type(doc), doc.id, populate_existing=True)
            for key, doc in setup["docs"].items()
        }
        report["documents"] = {
            key: {
                "figures_status": d.figures_status,
                "figures_count": d.figures_count,
                "bibliography": d.bibliography,
                "bibliography_source": d.bibliography_source,
            }
            for key, d in docs_after.items()
            if d is not None
        }

    target = next(lesson for lesson in setup["lessons"] if lesson.lesson_code == "M1.L2")

    async def generate(label: str) -> dict[str, Any]:
        async with async_session_factory() as db:
            lesson = await db.get(CourseLesson, target.id, populate_existing=True)
            assert lesson is not None
            lesson.content_raw = None
            lesson.content_generated_at = None
            lesson.content_status = "pending"
            await db.commit()
        await content_worker._process_one(target.id)
        async with async_session_factory() as db:
            lesson = await db.get(CourseLesson, target.id, populate_existing=True)
            assert lesson is not None
            (out / f"content_raw_{label}.json").write_text(
                json.dumps(lesson.content_raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {
                "status": lesson.content_status,
                "error": lesson.content_error,
                "content_raw": lesson.content_raw,
                "tokens": lesson.content_tokens,
                "review": lesson.content_figure_review,
            }

    async def stored(label: str) -> dict[str, Any]:
        """Esito di una generazione già fatta (ripresa): JSON su disco e DB."""
        raw = json.loads((out / f"content_raw_{label}.json").read_text(encoding="utf-8"))
        async with async_session_factory() as db:
            lesson = await db.get(CourseLesson, target.id, populate_existing=True)
            assert lesson is not None
            current = label == "after"
            return {
                "status": "ready",
                "error": None,
                "content_raw": raw,
                "tokens": lesson.content_tokens if current else None,
                "review": lesson.content_figure_review if current else None,
            }

    if args.resume_course is not None:
        before = await stored("before")
        after = await stored("after")
    else:
        # 2. «Prima»: prompt e comportamento di main (niente catalogo, niente riga).
        original_prompt = openai_svc._system_prompt
        source_off = settings.model_copy(update={"figure_source_enabled": False})
        catalog_get = source_figure_catalog.get_settings
        source_figure_catalog.get_settings = lambda: source_off  # type: ignore[assignment]

        def main_prompt(*a: Any, **k: Any) -> str:
            return _strip_static_line(original_prompt(*a, **k))

        openai_svc._system_prompt = main_prompt
        try:
            before = await generate("before")
        finally:
            openai_svc._system_prompt = original_prompt
            source_figure_catalog.get_settings = catalog_get
        # 3. «Dopo»: la funzione attiva.
        after = await generate("after")

    def figure_stats(raw: dict[str, Any] | None) -> dict[str, Any]:
        assets = [a for a in (raw or {}).get("visual_assets") or [] if isinstance(a, dict)]
        return {
            "generated": len([a for a in assets if a.get("format") != "source_figure"]),
            "sources": [a for a in assets if a.get("format") == "source_figure"],
            "formats": sorted(a.get("format") for a in assets),
        }

    report["before"] = {
        "status": before["status"],
        **{k: v for k, v in figure_stats(before["content_raw"]).items() if k != "sources"},
    }
    after_stats = figure_stats(after["content_raw"])
    report["after"] = {
        "status": after["status"],
        "error": after["error"],
        "generated": after_stats["generated"],
        "formats": after_stats["formats"],
        "source_assets": after_stats["sources"],
        "review": after["review"],
        "content_tokens_source_figures": (after["tokens"] or {}).get("source_figures"),
        "cost_usd": (after["tokens"] or {}).get("cost_usd"),
        "assets_cost_usd": (after["tokens"] or {}).get("assets_cost_usd"),
    }

    # 4. Slide (PROMPT 5, 8c), PDF dispensa, PDF slide, frame video. Il
    # worker, su un errore recuperabile (per esempio `total_slides` diverso
    # dal numero di slide), rimette la lezione in coda: qui come lui, fino a
    # 4 tentativi.
    async with async_session_factory() as db:
        lesson = await db.get(CourseLesson, target.id, populate_existing=True)
        assert lesson is not None
        lesson.slides_status = "pending"
        await db.commit()
    slides_attempts = 0
    for _attempt in range(4):
        slides_attempts += 1
        await slides_worker._process_one(target.id)
        async with async_session_factory() as db:
            lesson = await db.get(CourseLesson, target.id, populate_existing=True)
            assert lesson is not None
            if lesson.slides_status != "pending":
                break
    report["slides_attempts"] = slides_attempts
    async with async_session_factory() as db:
        course_full = await content_svc.load_course_full(db, course_id=setup["course_id"])
        assert course_full is not None
        lesson = next(
            item for m in course_full.modules for item in m.lessons if item.id == target.id
        )
        report["slides"] = {
            "status": lesson.slides_status,
            "error": lesson.slides_error,
            "total": (lesson.slides_raw or {}).get("total_slides"),
            "cost_usd": (lesson.slides_tokens or {}).get("cost_usd"),
        }
        (out / "slides_raw.json").write_text(
            json.dumps(lesson.slides_raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        resolved = await resolve_source_figures(
            db,
            course_id=course_full.id,
            assets=(lesson.content_raw or {}).get("visual_assets") or [],
            language=course_full.language_code,
        )
        rel = await pdf_svc.materialize_lesson_pdf(db, course=course_full, lesson=lesson)
        slides_rel = await slides_pdf_svc.materialize_lesson_slides_pdf(
            db, course=course_full, lesson=lesson
        )
        await db.commit()
        storage = remote_storage.get_storage()
        lesson_pdf = await asyncio.to_thread(storage.download_bytes, remote_storage.pdf_key(rel))
        slides_pdf = await asyncio.to_thread(
            storage.download_bytes, remote_storage.pdf_key(slides_rel)
        )
        (out / "dispensa.pdf").write_bytes(lesson_pdf)
        (out / "slide.pdf").write_bytes(slides_pdf)
        frames_dir = out / "frames"
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
        frames, slide_ids = await video_svc.render_slides_to_png(
            db, course=course_full, lesson=lesson, output_dir=frames_dir
        )

        lesson_text, lesson_hashes = _pdf_text_and_hashes(lesson_pdf)
        slides_text, slides_hashes = _pdf_text_and_hashes(slides_pdf)
        from app.services.document_figures.phash import hamming

        checks: list[dict[str, Any]] = []
        geometry = SlideGeometry()
        for asset in after_stats["sources"]:
            asset_id = asset["asset_id"]
            item = resolved.get(asset_id)
            fig = await db.get(CourseDocumentFigure, uuid.UUID(asset["content"]))
            crop = (
                await asyncio.to_thread(figure_storage.read, str(fig.storage_path))
                if fig is not None and fig.storage_path
                else b""
            )
            crop_hash = _phash_of(crop) if crop else None
            line = " ".join((item.attribution_text if item else "").split())
            showing = [
                str(slide.get("slide_id"))
                for slide in (lesson.slides_raw or {}).get("slides") or []
                if isinstance(slide, dict)
                and asset_id.lower()
                in {str(r).strip().lower() for r in slide.get("references_assets") or []}
                and str(slide.get("slide_id")) in slide_ids
            ]
            band_ok = []
            import numpy as np
            from PIL import Image

            for sid in showing:
                frame = Image.open(frames[slide_ids.index(sid)]).convert("L")
                px = frame.width / 297.0
                x0, y0, x1, y1 = geometry.attribution_box_mm
                band = frame.crop((round(x0 * px), round(y0 * px), round(x1 * px), round(y1 * px)))
                dark = int((np.asarray(band) < 160).sum())
                band_ok.append(dark > 200)
                frame.save(out / f"frame_{sid}.png")
            checks.append(
                {
                    "asset_id": asset_id,
                    "caption": asset.get("caption"),
                    "attribution": line,
                    "in_lesson_pdf": bool(line) and line in lesson_text,
                    "in_slides_pdf": bool(line) and line in slides_text,
                    "crop_in_lesson_pdf": any(
                        hamming(crop_hash, h) <= 8 for h in lesson_hashes if crop_hash
                    ),
                    "crop_in_slides_pdf": any(
                        hamming(crop_hash, h) <= 8 for h in slides_hashes if crop_hash
                    ),
                    "slides_showing": showing,
                    "frame_band_has_text": band_ok,
                    "band_right_edge_mm": geometry.attribution_box_mm[2],
                    "avatar_reserve_from_mm": geometry.page_w_mm - 85.0,
                }
            )
        report["checks"] = checks
        report["budget_a_ok"] = 4 <= after_stats["generated"] <= 8 or (
            after_stats["generated"] >= report["before"]["generated"]
        )
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("after", "checks")}, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
