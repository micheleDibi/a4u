"""Misura M7: le figure di fonte non sostituiscono le figure generate.

Chiama DAVVERO il PROMPT 3 (costo: ~0,3 $ a lezione e braccio con il
modello di default) su un corso di prova creato in un DATABASE USA-E-GETTA
da uno spec JSON (lezioni con struttura di Fase 2, documenti, figure già
descritte), e confronta per ogni lezione:

- A1, A2: prompt nuovo SENZA catalogo (due estrazioni: variabilità);
- B: prompt nuovo CON il catalogo delle figure di fonte;
- A0 (sulle prime 4 lezioni ordinarie): prompt di prima della funzione (senza
  la riga statica «FIGURE DI FONTE»), per misurare la deriva della riga.

La statistica è `source_figure_substitution.substitution_report` (regole
M7-S1…S4 del piano). Gli output grezzi finiscono in `--out`.

Uso (dalla cartella `backend/`, con un DB creato apposta e OPENAI_API_KEY):

    docker exec a4u-postgres createdb -U a4u a4u_m7
    DATABASE_URL=postgresql+asyncpg://a4u:a4u_dev_password@localhost:5432/a4u_m7 \\
        python -m scripts.measure_source_figures --spec spec.json --out out/ --apply

Senza `--apply` stampa il piano delle chiamate e non chiama OpenAI. Non va
mai puntato al DB di sviluppo o di produzione: lo script crea le tabelle
con `create_all` e scrive dati di prova.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SPEC_HELP = """Spec JSON: {"course": {"title", "objectives", "language"},
"lessons": [{"code", "title", "introductory", "topics": [..], "sections":
[{"title", "purpose"}], "objectives": [..], "summary"}], "documents": [{"key",
"filename", "license"}], "figures": [{"document", "kind", "caption",
"description", "keywords": {"course": [..], "en": [..]}, "quality"}]}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0], epilog=SPEC_HELP)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Chiama davvero OpenAI.")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--a0-lessons", type=int, default=4)
    return parser


async def _setup(db: Any, spec: dict[str, Any]) -> tuple[Any, list[Any]]:
    from app.core.security import hash_password
    from app.models.course import Course
    from app.models.course_document import CourseDocument
    from app.models.course_document_figure import CourseDocumentFigure
    from app.models.course_lesson import CourseLesson
    from app.models.course_module import CourseModule
    from app.models.organization import Organization
    from app.models.user import User

    now = datetime.now(UTC)
    user = User(
        email=f"m7-{uuid.uuid4().hex[:8]}@example.org",
        password_hash=hash_password("Password123!"),
        full_name="Misura M7",
        is_active=True,
    )
    org = Organization(name=f"M7-{uuid.uuid4().hex[:6]}", email="m7@example.org")
    db.add_all([user, org])
    await db.flush()
    info = spec["course"]
    course = Course(
        organization_id=org.id,
        title=info["title"],
        objectives=info.get("objectives", ""),
        language_code=info.get("language", "it"),
        cfu=6,
        modules_count=1,
        lessons_per_module=len(spec["lessons"]),
        lesson_duration_minutes=45,
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
        title="Modulo unico",
        lessons_structure_status="approved",
        lessons_structure_approved_at=now,
    )
    db.add(module)
    await db.flush()
    for position, raw in enumerate(spec["lessons"], start=1):
        db.add(
            CourseLesson(
                module_id=module.id,
                course_id=course.id,
                position=position,
                lesson_code=raw["code"],
                title=raw["title"],
                summary=raw.get("summary", ""),
                is_assessment=False,
                is_introductory=bool(raw.get("introductory")),
                learning_objectives=raw.get("objectives", []),
                mandatory_topics=[
                    {"topic_id": f"T{i}", "title": t}
                    for i, t in enumerate(raw.get("topics", []), 1)
                ],
                prerequisites=[],
                section_outline=[
                    {
                        "section_id": f"S{i}",
                        "title": s["title"],
                        "purpose": s.get("purpose", ""),
                        "covers_topic_ids": [f"T{i}"] if i <= len(raw.get("topics", [])) else [],
                    }
                    for i, s in enumerate(raw.get("sections", []), 1)
                ],
                content_status="empty",
                slides_status="empty",
                speech_status="empty",
            )
        )
    docs: dict[str, CourseDocument] = {}
    for raw in spec.get("documents", []):
        doc = CourseDocument(
            course_id=course.id,
            filename_original=raw["filename"],
            filename_stored=f"{uuid.uuid4().hex}.pdf",
            file_path=f"/uploads/courses/{course.id}/{raw['key']}.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            uploaded_by_user_id=user.id,
            summary_status="ready",
            citation_policy="citable",
            license=raw.get("license"),
        )
        db.add(doc)
        docs[raw["key"]] = doc
    await db.flush()
    for index, raw in enumerate(spec.get("figures", []), start=1):
        doc = docs[raw["document"]]
        db.add(
            CourseDocumentFigure(
                course_id=course.id,
                document_id=doc.id,
                source_kind="uploaded",
                locator=f"m7-{index:04d}",
                extraction_version=1,
                engine="docling",
                page=index,
                source_caption=raw.get("caption"),
                storage_path=f"/uploads/courses/{course.id}/document_figures/{doc.id}/m7-{index:04d}.png",
                mime_type="image/png",
                kind=raw.get("kind"),
                description=raw.get("description"),
                keywords=raw.get("keywords"),
                quality_score=int(raw.get("quality", 4)),
                is_useful_for_teaching=True,
                status="ready",
                license=raw.get("license") or doc.license or "unknown",
                license_source="document",
            )
        )
    await db.commit()
    from app.services import course_lesson_content_service as content_svc

    course_full = await content_svc.load_course_full(db, course_id=course.id)
    assert course_full is not None
    lessons = sorted(course_full.modules[0].lessons, key=lambda lesson: lesson.position)
    return course_full, lessons


def _strip_static_line(prompt: str) -> str:
    marker = "- FIGURE DI FONTE — solo se il messaggio offre un catalogo: vanno in\n"
    start = prompt.find(marker)
    if start < 0:
        return prompt
    end = prompt.find("\n", start + len(marker)) + 1
    return prompt[:start] + prompt[end:]


async def run(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    from app.db.base import Base
    from app.db.seed import ensure_seed
    from app.db.session import async_session_factory, engine
    from app.services import course_lesson_content_service as content_svc
    from app.services import openai_lesson_content_service as openai_svc
    from app.services import source_figure_catalog, source_figure_fusion
    from app.services.source_figure_substitution import (
        LessonTriple,
        stats_from_output,
        substitution_report,
    )

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        # Come nel conftest: lo schema dei modelli usa CITEXT.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as db:
        await ensure_seed(db)
        await db.commit()
        course, lessons = await _setup(db, spec)
        plans: list[tuple[Any, str, Any]] = []
        ordinary = [lesson for lesson in lessons if not lesson.is_introductory]
        a0_codes = {lesson.lesson_code for lesson in ordinary[: args.a0_lessons]}
        for lesson in lessons:
            catalog = await source_figure_catalog.build_catalog(db, course, lesson)
            for arm in ("A1", "A2", "B") + (("A0",) if lesson.lesson_code in a0_codes else ()):
                plans.append((lesson, arm, catalog if arm == "B" else None))
        print(f"lezioni: {len(lessons)}; chiamate previste: {len(plans)}")
        for lesson, arm, catalog in plans:
            size = len(catalog.catalog.candidates) if catalog else 0
            print(f"  {lesson.lesson_code} {arm} catalogo={size}")
        if not args.apply:
            return 0

        original_system_prompt = openai_svc._system_prompt
        sem = asyncio.Semaphore(max(1, args.concurrency))
        style = content_svc.didactic_style_labels(course)

        async def one(lesson: Any, arm: str, catalog: Any) -> dict[str, Any]:
            user_prompt = content_svc.build_user_prompt(
                course,
                lesson,
                figure_catalog=catalog.catalog if catalog else None,
                source_figures_max=catalog.budget if catalog else 0,
            )
            async with sem:
                output, usage = await openai_svc.generate_lesson_content(
                    user_prompt=user_prompt,
                    language_code=course.language_code,
                    is_regeneration=False,
                    ruolo_docente=style["ruolo_docente"],
                    stile_insegnamento=style["stile_insegnamento"],
                    livello_eqf=style["livello_eqf"],
                    objective_ids=content_svc.objective_ids_for_lesson(lesson),
                    source_figure_refs=list(catalog.catalog.refs) if catalog else (),
                )
            report = source_figure_fusion.fuse_source_figures(
                output,
                catalog.catalog.refs if catalog else {},
                max_items=catalog.budget if catalog else 0,
            )
            record = {
                "lesson": lesson.lesson_code,
                "introductory": lesson.is_introductory,
                "arm": arm,
                "output": output.model_dump(),
                "fusion": report.as_json(),
                "usage": usage,
            }
            path = args.out / f"{lesson.lesson_code}_{arm}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  fatto {lesson.lesson_code} {arm} costo={usage.get('cost_usd')}", flush=True)
            return record

        # Prima i bracci con il prompt nuovo, poi A0 con il prompt di prima:
        # la variante del system prompt è globale e non deve toccare le
        # chiamate degli altri bracci.
        results: list[Any] = list(
            await asyncio.gather(
                *(one(lesson, arm, catalog) for lesson, arm, catalog in plans if arm != "A0"),
                return_exceptions=True,
            )
        )
        openai_svc._system_prompt = lambda *a, **k: _strip_static_line(
            original_system_prompt(*a, **k)
        )
        try:
            results += await asyncio.gather(
                *(one(lesson, arm, catalog) for lesson, arm, catalog in plans if arm == "A0"),
                return_exceptions=True,
            )
        finally:
            openai_svc._system_prompt = original_system_prompt
        records = [r for r in results if isinstance(r, dict)]
        errors = [str(r) for r in results if not isinstance(r, dict)]
        by_key = {(r["lesson"], r["arm"]): r for r in records}
        triples = []
        for lesson in lessons:
            keys = [(lesson.lesson_code, arm) for arm in ("A1", "A2", "B")]
            if all(k in by_key for k in keys):
                a1, a2, b = (stats_from_output(by_key[k]["output"]) for k in keys)
                triples.append(LessonTriple(lesson.lesson_code, lesson.is_introductory, a1, a2, b))
        report = substitution_report(triples)
        drift = {
            code: {
                "A0": stats_from_output(by_key[(code, "A0")]["output"]).generated_figures,
                "A1": stats_from_output(by_key[(code, "A1")]["output"]).generated_figures,
                "A2": stats_from_output(by_key[(code, "A2")]["output"]).generated_figures,
            }
            for code in sorted(a0_codes)
            if all((code, arm) in by_key for arm in ("A0", "A1", "A2"))
        }
        total_cost = sum(float(r["usage"].get("cost_usd") or 0.0) for r in records)
        summary = {
            "passed": report.passed,
            "checks": report.checks,
            "drift_a0": drift,
            "calls": len(records),
            "errors": errors,
            "cost_usd": round(total_cost, 4),
        }
        (args.out / "report.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
