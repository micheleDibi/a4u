"""Servizio orchestrazione della duplicazione corso in altra lingua.

Espone:
- `request_course_duplication` — crea un job pending (validato).
- `cancel_duplication` — pending|processing → failed (idempotente).
- `list_duplications_for_course` — lista job per un corso (qualsiasi
  stato, source o target).
- `_clone_course_structure` — clona shell del corso target (chiamato
  dal worker, phase 2). Documenti copiati fisicamente, video/avatar/
  PDF resettati, contenuti JSONB copiati AS-IS (saranno tradotti
  in-place dalle fasi successive del worker).
- `_translate_jsonb_inplace` — engine generico per tradurre i campi
  testuali di una struttura JSONB rispettando i path declarati in
  `course_duplication_paths.py`.
- 7 funzioni `_translate_*` granulari (architecture, content, slides,
  speech, glossary, document_summaries, course_metadata).
- `_finalize` — allinea `target.status = source.status` rispettando
  la monotonia (video/avatar restano `empty`).
"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.course_phase_order import advance_course_status
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_duplication_job import (
    DUPLICATION_JOB_STATUSES,
    CourseDuplicationJob,
)
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.language import Language
from app.services import course_duplication_paths as _paths
from app.services.openai_translate_service import (
    OpenAITranslateError,
    translate_batch,
)

log = get_logger("app.course_duplication.service")


# ---------------------------------------------------------------------------
# Public API — request / cancel / list
# ---------------------------------------------------------------------------


async def request_course_duplication(
    db: AsyncSession,
    *,
    source_course: Course,
    target_language_code: str,
    actor_id: uuid.UUID,
) -> CourseDuplicationJob:
    """Crea un nuovo job di duplicazione in stato `pending`.

    Vincoli:
    - `target_language_code` deve essere diverso dalla lingua corrente
      del corso sorgente.
    - La lingua di destinazione deve esistere e essere attiva.
    - Non deve già esistere un job attivo (`pending` o `processing`)
      per la stessa coppia `(source, target_language)`.
    """
    if target_language_code == source_course.language_code:
        raise ConflictError(
            "La lingua di destinazione deve essere diversa da quella del "
            "corso sorgente.",
            code="duplicate_same_language",
        )

    lang = await db.get(Language, target_language_code)
    if lang is None or not lang.is_active:
        raise NotFoundError(
            "Lingua di destinazione non disponibile.",
            code="language_not_available",
        )

    # Controllo applicativo (il DB ha anche un unique parziale come
    # safety net contro race condition).
    existing_active = (
        await db.execute(
            select(CourseDuplicationJob).where(
                CourseDuplicationJob.source_course_id == source_course.id,
                CourseDuplicationJob.target_language_code
                == target_language_code,
                CourseDuplicationJob.status.in_(("pending", "processing")),
            )
        )
    ).scalar_one_or_none()
    if existing_active is not None:
        raise ConflictError(
            "Una duplicazione in questa lingua è già in corso per questo corso.",
            code="duplicate_already_in_progress",
        )

    # Blocco extra: se esiste già un target course di una duplicazione
    # precedente per la stessa coppia (source, target_lang), rifiuta la
    # nuova richiesta. Questo evita che dopo un fallimento del worker
    # l'utente possa generare più corsi-stub duplicati in DB cliccando
    # ripetutamente. L'utente deve prima eliminare il target esistente.
    prior_with_target = (
        await db.execute(
            select(CourseDuplicationJob)
            .where(
                CourseDuplicationJob.source_course_id == source_course.id,
                CourseDuplicationJob.target_language_code
                == target_language_code,
                CourseDuplicationJob.target_course_id.is_not(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior_with_target is not None:
        raise ConflictError(
            "Esiste già un corso duplicato in questa lingua per questo "
            "corso sorgente. Elimina il corso target prima di richiedere "
            "una nuova duplicazione.",
            code="duplicate_target_already_exists",
        )

    job = CourseDuplicationJob(
        source_course_id=source_course.id,
        target_language_code=target_language_code,
        status="pending",
        progress=0,
        attempts=0,
        requested_by_user_id=actor_id,
    )
    db.add(job)
    await db.flush()

    await write_audit(
        db,
        action="course.duplicate.request",
        actor_user_id=actor_id,
        organization_id=source_course.organization_id,
        target_type="course",
        target_id=str(source_course.id),
        metadata={
            "job_id": str(job.id),
            "target_language_code": target_language_code,
            "source_language_code": source_course.language_code,
        },
    )
    await db.commit()
    await db.refresh(job)
    return job


async def cancel_duplication(
    db: AsyncSession,
    *,
    job: CourseDuplicationJob,
    actor_id: uuid.UUID,
) -> CourseDuplicationJob:
    """Mette il job in `failed` se è ancora `pending` o `processing`.
    Idempotente: se è già `ready`/`failed`, no-op.

    Il corso target (se già creato dalla phase 2) resta in DB con i
    contenuti parzialmente tradotti. Sarà compito dell'utente decidere
    se eliminarlo o riprenderlo manualmente.
    """
    if job.status not in ("pending", "processing"):
        return job

    job.status = "failed"
    job.error = "Annullata dall'utente"
    job.finished_at = datetime.now(UTC)

    source_course = await db.get(Course, job.source_course_id)
    organization_id = (
        source_course.organization_id if source_course is not None else None
    )

    if organization_id is not None:
        await write_audit(
            db,
            action="course.duplicate.cancelled",
            actor_user_id=actor_id,
            organization_id=organization_id,
            target_type="course_duplication_job",
            target_id=str(job.id),
            metadata={
                "source_course_id": str(job.source_course_id),
                "target_course_id": (
                    str(job.target_course_id) if job.target_course_id else None
                ),
                "target_language_code": job.target_language_code,
            },
        )
    await db.commit()
    await db.refresh(job)
    return job


async def list_duplications_for_course(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
) -> list[CourseDuplicationJob]:
    """Lista tutti i job (di qualsiasi stato) in cui il corso è source
    O target. Ordinato per `created_at` DESC."""
    res = await db.execute(
        select(CourseDuplicationJob)
        .where(
            (CourseDuplicationJob.source_course_id == course_id)
            | (CourseDuplicationJob.target_course_id == course_id)
        )
        .order_by(CourseDuplicationJob.created_at.desc())
    )
    return list(res.scalars().all())


async def get_job_or_404(
    db: AsyncSession, *, job_id: uuid.UUID
) -> CourseDuplicationJob:
    job = await db.get(CourseDuplicationJob, job_id)
    if job is None:
        raise NotFoundError(
            "Job di duplicazione non trovato.",
            code="duplication_job_not_found",
        )
    return job


async def load_source_full(
    db: AsyncSession, *, course_id: uuid.UUID
) -> Course | None:
    """Eager-load del corso sorgente con modules + lessons + documents.
    Usato dal worker prima di clonare la struttura.
    """
    res = await db.execute(
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.modules).selectinload(CourseModule.lessons),
            selectinload(Course.documents),
        )
    )
    return res.scalar_one_or_none()


async def load_target_full(
    db: AsyncSession, *, course_id: uuid.UUID
) -> Course | None:
    """Eager-load del corso target (post-clone)."""
    return await load_source_full(db, course_id=course_id)


# ---------------------------------------------------------------------------
# Translation engine — `_translate_jsonb_inplace`
# ---------------------------------------------------------------------------


def _walk_translate_path(
    root: Any, path: str
) -> list[tuple[Any, str | int]]:
    """Cammina `root` seguendo `path` e ritorna la lista delle posizioni
    foglia come (parent, key_or_index). Salta silenziosamente i punti
    in cui la struttura non corrisponde (campo mancante, tipo sbagliato).

    Sintassi:
    - `"a"`       → root["a"] (foglia stringa)
    - `"a.b"`     → root["a"]["b"]
    - `"a[]"`     → ogni elemento di root["a"] (per array di stringhe)
    - `"a[].b"`   → root["a"][i]["b"] per ogni i
    """
    # Caso speciale: il path è "[]" o "[]..." → l'oggetto root è già
    # una lista. Usato per `JSONB array di stringhe` come
    # `learning_objectives`.
    segments = path.split(".")
    leaves: list[tuple[Any, str | int]] = []

    def _step(node: Any, idx: int) -> None:
        if idx >= len(segments):
            return
        seg = segments[idx]
        is_last = idx == len(segments) - 1

        if seg == "[]":
            if not isinstance(node, list):
                return
            for i in range(len(node)):
                if is_last:
                    # Array di stringhe terminale.
                    if isinstance(node[i], str):
                        leaves.append((node, i))
                else:
                    _step(node[i], idx + 1)
            return

        if seg.endswith("[]"):
            key = seg[:-2]
            if not isinstance(node, dict) or key not in node:
                return
            arr = node[key]
            if not isinstance(arr, list):
                return
            for i in range(len(arr)):
                if is_last:
                    if isinstance(arr[i], str):
                        leaves.append((arr, i))
                else:
                    _step(arr[i], idx + 1)
            return

        # Plain object field.
        if not isinstance(node, dict) or seg not in node:
            return
        if is_last:
            if isinstance(node[seg], str):
                leaves.append((node, seg))
        else:
            _step(node[seg], idx + 1)

    _step(root, 0)
    return leaves


# Cap di stringhe per ogni chiamata OpenAI. Batch più grandi fanno
# scattare il timeout HTTP del client (default 120s) — `gpt-4o-mini`
# può impiegare 2-3 min per tradurre 100+ items con output JSON. Con
# chunk da 25 ogni chiamata finisce in 15-30s, sotto soglia.
_TRANSLATE_CHUNK_SIZE = 25

# Retry su errori transient (5xx, connection reset, timeout). OpenAI /
# Cloudflare restituiscono spesso 520/502/None in modo intermittente:
# senza retry, una percentuale non trascurabile di lezioni resta
# parzialmente tradotta. Backoff esponenziale (1s, 3s, 9s) per dare
# tempo al servizio remoto di recuperare.
_TRANSLATE_RETRY_MAX_ATTEMPTS = 4  # 1 initial + 3 retry
_TRANSLATE_RETRY_BACKOFF_BASE_SECONDS = 1.0

# Cap globale di chiamate OpenAI translate concorrenti. Tutte le
# chiamate di duplicazione corso (architecture, moduli, lezioni)
# passano da questo semaforo. Evita di superare il rate-limit OpenAI
# e tiene sotto controllo la pressione su Cloudflare (che ci risponde
# con 520 quando saturiamo). 40 è ben sotto i 5000 RPM di gpt-4o-mini
# tier 2 ma sufficiente per parallelizzare aggressivamente le phase.
_TRANSLATE_GLOBAL_CONCURRENCY_LIMIT = 40
_translate_global_sem: asyncio.Semaphore | None = None


def _get_translate_global_sem() -> asyncio.Semaphore:
    """Lazy init del semaforo globale (deve essere creato dentro un
    event loop attivo, non a module-load time)."""
    global _translate_global_sem
    if _translate_global_sem is None:
        _translate_global_sem = asyncio.Semaphore(
            _TRANSLATE_GLOBAL_CONCURRENCY_LIMIT
        )
    return _translate_global_sem


async def _translate_batch_resilient(
    *,
    items: dict[str, str],
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
    op_label: str = "",
) -> dict[str, str]:
    """Wrapper attorno a `translate_batch` con retry esponenziale sui
    soli errori transient (status >= 500 o status None = httpx error).
    Errori 4xx (auth/validation) NON vengono ritentati.

    `op_label` è una stringa di contesto opzionale per i log
    (es. "content lesson_id=…" o "architecture").

    Tutte le chiamate sono serializzate da `_translate_global_sem`
    (cap 40 concorrenti) per evitare di saturare OpenAI/Cloudflare.
    """
    last_exc: OpenAITranslateError | None = None
    sem = _get_translate_global_sem()
    for attempt in range(1, _TRANSLATE_RETRY_MAX_ATTEMPTS + 1):
        try:
            async with sem:
                return await translate_batch(
                    items=items,
                    source_lang_code=source_lang_code,
                    source_lang_name=source_lang_name,
                    target_lang_code=target_lang_code,
                    target_lang_name=target_lang_name,
                )
        except OpenAITranslateError as exc:
            # 4xx: errore applicativo (auth, validation, ecc.) — non
            # ritentabile, fail subito.
            if exc.status is not None and 400 <= exc.status < 500:
                raise
            last_exc = exc
            if attempt < _TRANSLATE_RETRY_MAX_ATTEMPTS:
                sleep_s = _TRANSLATE_RETRY_BACKOFF_BASE_SECONDS * (
                    3 ** (attempt - 1)
                )
                log.warning(
                    "course_duplication_translate_retry",
                    attempt=attempt,
                    max_attempts=_TRANSLATE_RETRY_MAX_ATTEMPTS,
                    status=exc.status,
                    error=str(exc)[:200],
                    op=op_label[:80],
                    sleep_seconds=sleep_s,
                )
                await asyncio.sleep(sleep_s)
                continue
            # Esauriti i retry: rilancia l'ultima eccezione.
            raise
    # Non dovrebbe arrivarci, ma per type-checker:
    if last_exc is not None:
        raise last_exc
    return {}


async def _translate_jsonb_inplace(
    data: Any,
    *,
    paths: tuple[str, ...],
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
    key_prefix: str = "",
) -> dict[str, Any]:
    """Estrae tutte le foglie stringa dichiarate in `paths`, le traduce
    a chunk via `translate_batch`, e le riapplica in-place su `data`.

    Chunking: i batch grandi (es. `architecture_raw` con 100+ items)
    fanno scattare il timeout HTTP del client OpenAI. Splittiamo in
    chunk da `_TRANSLATE_CHUNK_SIZE` items e facciamo chiamate
    sequenziali.

    Stringhe vuote o non-stringhe vengono ignorate. Il `key_prefix`
    serve a evitare collisioni di chiavi tra chiamate concorrenti
    (es. lezioni diverse tradotte in parallelo).

    Ritorna un dict con statistiche aggregate per audit job.
    """
    if data is None:
        return {"strings_translated": 0}
    items: dict[str, str] = {}
    addresses: list[tuple[Any, str | int]] = []  # parent + key
    for path in paths:
        for parent, key in _walk_translate_path(data, path):
            text = parent[key] if isinstance(parent, list) else parent[key]
            if not isinstance(text, str) or not text.strip():
                continue
            unique_key = f"{key_prefix}{len(items)}"
            items[unique_key] = text
            addresses.append((parent, key))
    if not items:
        return {"strings_translated": 0}

    # Chunking PARALLELO: i chunk vengono lanciati tutti insieme via
    # `asyncio.gather`. Il `_translate_global_sem` interno limita la
    # concorrenza effettiva a 40 chiamate OpenAI simultanee.
    keys = list(items.keys())
    translated: dict[str, str] = {}

    async def _translate_one_chunk(chunk_start: int) -> dict[str, str]:
        chunk_keys = keys[chunk_start : chunk_start + _TRANSLATE_CHUNK_SIZE]
        chunk_items = {k: items[k] for k in chunk_keys}
        chunk_translated = await _translate_batch_resilient(
            items=chunk_items,
            source_lang_code=source_lang_code,
            source_lang_name=source_lang_name,
            target_lang_code=target_lang_code,
            target_lang_name=target_lang_name,
            op_label=f"jsonb chunk={chunk_start} prefix={key_prefix[:30]}",
        )
        log.info(
            "course_duplication_chunk_translated",
            chunk_start=chunk_start,
            chunk_size=len(chunk_items),
            received=len(chunk_translated),
            total_items=len(keys),
        )
        return chunk_translated

    chunk_starts = list(range(0, len(keys), _TRANSLATE_CHUNK_SIZE))
    chunk_results = await asyncio.gather(
        *[_translate_one_chunk(cs) for cs in chunk_starts],
        return_exceptions=True,
    )
    for cs, result in zip(chunk_starts, chunk_results):
        if isinstance(result, Exception):
            log.warning(
                "course_duplication_chunk_failed",
                chunk_start=cs,
                error=str(result)[:200],
                prefix=key_prefix[:30],
            )
            continue
        translated.update(result)

    applied = 0
    for (parent, key), unique_key in zip(addresses, items.keys()):
        new = translated.get(unique_key)
        if new is None or not isinstance(new, str):
            continue
        parent[key] = new
        applied += 1
    return {"strings_translated": applied}


# ---------------------------------------------------------------------------
# Clone shell del corso target (phase 2 del worker)
# ---------------------------------------------------------------------------


def _new_filename_stored(original: str) -> str:
    """Genera un nuovo `filename_stored` univoco rispettando l'estensione
    del file originale. Pattern: `{uuid_hex}.{ext}`."""
    suffix = Path(original).suffix.lower() or ""
    return f"{uuid.uuid4().hex}{suffix}"


async def _clone_course_structure(
    db: AsyncSession,
    *,
    source: Course,
    target_language_code: str,
    job: CourseDuplicationJob,
) -> Course:
    """Crea il corso target come clone della shell di `source`. Tutti i
    JSONB testuali vengono copiati AS-IS: il worker li tradurrà
    in-place nelle phase successive.

    Video / Avatar / PDF resettati a `empty`. Documenti copiati
    fisicamente su filesystem con nuovo `filename_stored` univoco.
    """
    settings = get_settings()
    target = Course(
        organization_id=source.organization_id,
        title=source.title,
        objectives=source.objectives,
        language_code=target_language_code,
        video_language_code=target_language_code,
        # Tassonomia: copiare gli FK term_id (riferiscono righe globali)
        categoria_term_id=source.categoria_term_id,
        stile_insegnamento_term_id=source.stile_insegnamento_term_id,
        profondita_contenuto_term_id=source.profondita_contenuto_term_id,
        ruolo_docente_term_id=source.ruolo_docente_term_id,
        dimensione_pubblico_term_id=source.dimensione_pubblico_term_id,
        livello_conoscenza_term_id=source.livello_conoscenza_term_id,
        destinatari_term_id=source.destinatari_term_id,
        livello_eqf_term_id=source.livello_eqf_term_id,
        argomenti_chiave=list(source.argomenti_chiave or []),
        cfu=source.cfu,
        modules_count=source.modules_count,
        lessons_per_module=source.lessons_per_module,
        lesson_duration_minutes=source.lesson_duration_minutes,
        assessment_lesson_enabled=source.assessment_lesson_enabled,
        multiple_choice_questions_count=source.multiple_choice_questions_count,
        open_questions_count=source.open_questions_count,
        assignee_user_id=source.assignee_user_id,
        created_by_user_id=job.requested_by_user_id,
        status="draft",
        course_overview=source.course_overview,
        pedagogical_rationale=source.pedagogical_rationale,
        architecture_raw=_deepcopy_json(source.architecture_raw),
        # I metadati di run AI sono *_attempts/error/generated_at/etc.
        # Resettati per il nuovo corso (deriva: il corso target NON ha
        # ancora vissuto la pipeline).
        architecture_attempts=0,
        architecture_tokens=None,
        architecture_error=None,
        architecture_generated_at=source.architecture_generated_at,
        # Glossary: copiato AS-IS, sarà tradotto in phase 6.
        glossary_status=source.glossary_status,
        glossary_raw=_deepcopy_json(source.glossary_raw),
        glossary_tokens=None,
        glossary_error=None,
        glossary_generated_at=source.glossary_generated_at,
        didactic_setup_confirmed_at=source.didactic_setup_confirmed_at,
    )
    db.add(target)
    await db.flush()  # need target.id per documenti

    # --- Documenti: copia file su disco + nuovo CourseDocument --------
    upload_root: Path = settings.upload_root
    target_doc_dir = upload_root / "courses" / str(target.id)
    target_doc_dir.mkdir(parents=True, exist_ok=True)
    for src_doc in source.documents:
        new_stored = _new_filename_stored(src_doc.filename_stored)
        src_path = upload_root / src_doc.file_path.lstrip("/")
        # `file_path` può iniziare con "/" o "uploads/..."; normalizza.
        if not src_path.exists():
            # Path alternativo: src_doc.file_path è già relativo a upload_root
            src_path = upload_root / src_doc.file_path
        new_rel = f"courses/{target.id}/{new_stored}"
        new_abs = upload_root / new_rel
        if src_path.is_file():
            shutil.copy2(src_path, new_abs)
        else:
            log.warning(
                "course_duplication_source_doc_missing",
                source_path=str(src_path),
                doc_id=str(src_doc.id),
            )
        new_doc = CourseDocument(
            course_id=target.id,
            filename_original=src_doc.filename_original,
            filename_stored=new_stored,
            file_path=new_rel,
            mime_type=src_doc.mime_type,
            size_bytes=src_doc.size_bytes,
            uploaded_by_user_id=src_doc.uploaded_by_user_id,
            # Summary: copiato AS-IS, sarà tradotto in phase 6.
            summary=_deepcopy_json(src_doc.summary),
            summary_status=src_doc.summary_status,
            summary_error=None,
            summary_generated_at=src_doc.summary_generated_at,
            summary_tokens=None,
            summary_attempts=0,
            text_extracted_at=src_doc.text_extracted_at,
            text_chars_extracted=src_doc.text_chars_extracted,
        )
        db.add(new_doc)

    # --- Moduli + lezioni --------------------------------------------
    for src_mod in source.modules:
        new_mod = CourseModule(
            course_id=target.id,
            position=src_mod.position,
            module_code=src_mod.module_code,
            title=src_mod.title,
            description=src_mod.description,
            # Status uguale al source — sarà aggiornato in _finalize
            lessons_structure_status=src_mod.lessons_structure_status,
            lessons_structure_raw=_deepcopy_json(src_mod.lessons_structure_raw),
            lessons_structure_attempts=0,
            lessons_structure_tokens=None,
            lessons_structure_error=None,
            lessons_structure_generated_at=src_mod.lessons_structure_generated_at,
            lessons_structure_approved_at=src_mod.lessons_structure_approved_at,
        )
        db.add(new_mod)
        await db.flush()  # need new_mod.id per le lezioni

        for src_lesson in src_mod.lessons:
            new_lesson = CourseLesson(
                module_id=new_mod.id,
                course_id=target.id,
                position=src_lesson.position,
                lesson_code=src_lesson.lesson_code,
                title=src_lesson.title,
                summary=src_lesson.summary,
                is_introductory=src_lesson.is_introductory,
                is_assessment=src_lesson.is_assessment,
                recommended_bibliography=_deepcopy_json(
                    src_lesson.recommended_bibliography
                ),
                # Fase 2 — struttura formativa
                learning_objectives=_deepcopy_json(src_lesson.learning_objectives),
                mandatory_topics=_deepcopy_json(src_lesson.mandatory_topics),
                prerequisites=_deepcopy_json(src_lesson.prerequisites),
                section_outline=_deepcopy_json(src_lesson.section_outline),
                # Fase 3 — content
                content_status=src_lesson.content_status,
                content_raw=_deepcopy_json(src_lesson.content_raw),
                content_attempts=0,
                content_tokens=None,
                content_error=None,
                content_generated_at=src_lesson.content_generated_at,
                content_approved_at=src_lesson.content_approved_at,
                # PDF lezione — RESET
                pdf_status="empty",
                pdf_path=None,
                pdf_attempts=0,
                pdf_error=None,
                pdf_generated_at=None,
                pdf_template_id=src_lesson.pdf_template_id,
                # Fase 4 — slides
                slides_status=src_lesson.slides_status,
                slides_raw=_deepcopy_json(src_lesson.slides_raw),
                slides_attempts=0,
                slides_tokens=None,
                slides_error=None,
                slides_generated_at=src_lesson.slides_generated_at,
                slides_approved_at=src_lesson.slides_approved_at,
                # PDF slides — RESET
                slides_pdf_status="empty",
                slides_pdf_path=None,
                slides_pdf_attempts=0,
                slides_pdf_error=None,
                slides_pdf_generated_at=None,
                slides_pdf_template_id=src_lesson.slides_pdf_template_id,
                # Fase 5 — speech
                speech_status=src_lesson.speech_status,
                speech_raw=_deepcopy_json(src_lesson.speech_raw),
                speech_attempts=0,
                speech_tokens=None,
                speech_error=None,
                speech_generated_at=src_lesson.speech_generated_at,
                speech_approved_at=src_lesson.speech_approved_at,
                # PDF speech — RESET
                speech_pdf_status="empty",
                speech_pdf_path=None,
                speech_pdf_attempts=0,
                speech_pdf_error=None,
                speech_pdf_generated_at=None,
                speech_pdf_template_id=src_lesson.speech_pdf_template_id,
                # Fase 6 — video MP4 — RESET (decisione utente)
                video_status="empty",
                video_path=None,
                video_attempts=0,
                video_tokens=None,
                video_error=None,
                video_generated_at=None,
                # Fase 6b — avatar video — RESET (decisione utente)
                avatar_video_status="empty",
                avatar_video_path=None,
                avatar_video_attempts=0,
                avatar_video_tokens=None,
                avatar_video_error=None,
                avatar_video_generated_at=None,
            )
            db.add(new_lesson)

    await db.flush()
    job.target_course_id = target.id
    await db.commit()
    await db.refresh(target)
    return target


def _deepcopy_json(value: Any) -> Any:
    """Deep copy di un valore JSONB (None/dict/list/str/numeric/bool).
    SQLAlchemy può restituire lo stesso oggetto Python tra source e
    target — clonare evita di mutare il source quando traduciamo in-place."""
    import copy

    if value is None:
        return None
    return copy.deepcopy(value)


# ---------------------------------------------------------------------------
# Funzioni di traduzione granulari (chiamate dal worker)
# ---------------------------------------------------------------------------


async def _translate_course_metadata(
    db: AsyncSession,
    *,
    target: Course,
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
) -> dict[str, Any]:
    """Traduce i campi text direttamente su `Course` (title, objectives,
    course_overview, pedagogical_rationale) + `argomenti_chiave[]`.
    Modifica `target` in-place; il commit è del chiamante.
    """
    items: dict[str, str] = {}
    for field in _paths.COURSE_METADATA_TRANSLATE_FIELDS:
        value = getattr(target, field, None)
        if isinstance(value, str) and value.strip():
            items[field] = value
    # argomenti_chiave: lista di stringhe → key separato per index.
    if isinstance(target.argomenti_chiave, list):
        for i, val in enumerate(target.argomenti_chiave):
            if isinstance(val, str) and val.strip():
                items[f"argomenti_chiave_{i}"] = val
    if not items:
        return {"strings_translated": 0}
    translated = await _translate_batch_resilient(
        items=items,
        source_lang_code=source_lang_code,
        source_lang_name=source_lang_name,
        target_lang_code=target_lang_code,
        target_lang_name=target_lang_name,
        op_label="course_metadata",
    )
    for field in _paths.COURSE_METADATA_TRANSLATE_FIELDS:
        if field in translated:
            setattr(target, field, translated[field])
    if isinstance(target.argomenti_chiave, list):
        new_list = list(target.argomenti_chiave)
        for i in range(len(new_list)):
            key = f"argomenti_chiave_{i}"
            if key in translated:
                new_list[i] = translated[key]
        target.argomenti_chiave = new_list
    return {"strings_translated": len(items)}


async def _translate_architecture(
    db: AsyncSession,
    *,
    target: Course,
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
) -> dict[str, Any]:
    """Traduce `target.architecture_raw` + modules + lessons (campi
    diretti)."""
    stats = {"strings_translated": 0}
    # 1) architecture_raw
    if target.architecture_raw:
        s = await _translate_jsonb_inplace(
            target.architecture_raw,
            paths=_paths.ARCHITECTURE_TRANSLATE_PATHS,
            source_lang_code=source_lang_code,
            source_lang_name=source_lang_name,
            target_lang_code=target_lang_code,
            target_lang_name=target_lang_name,
        )
        stats["strings_translated"] += s["strings_translated"]
        # Mark JSONB dirty per SQLAlchemy.
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(target, "architecture_raw")

    # 2) Moduli — campi diretti + lessons_structure_raw, IN PARALLELO.
    # Ogni modulo è una task indipendente: traduce i suoi campi
    # (`title`/`description`) e il proprio `lessons_structure_raw`
    # (~250 items in 11 chunk paralleli). Il `_translate_global_sem`
    # interno serializza al massimo 40 chiamate OpenAI simultanee, il
    # resto aspetta.
    from sqlalchemy.orm.attributes import flag_modified

    async def _translate_one_module(module: Any) -> int:
        local_count = 0
        items: dict[str, str] = {}
        for field in _paths.MODULE_TRANSLATE_FIELDS:
            value = getattr(module, field, None)
            if isinstance(value, str) and value.strip():
                items[f"m{module.position}_{field}"] = value
        if items:
            translated = await _translate_batch_resilient(
                items=items,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
                op_label=f"module m{module.position}",
            )
            for field in _paths.MODULE_TRANSLATE_FIELDS:
                key = f"m{module.position}_{field}"
                if key in translated:
                    setattr(module, field, translated[key])
            local_count += len(items)

        if module.lessons_structure_raw:
            s = await _translate_jsonb_inplace(
                module.lessons_structure_raw,
                paths=_LESSONS_STRUCTURE_RAW_PATHS,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
                # Prefisso univoco per evitare collisioni di chiavi fra
                # moduli che corrono in parallelo (chunk keys condivisi
                # nello stesso global sem).
                key_prefix=f"m{module.position}_",
            )
            local_count += s["strings_translated"]
            flag_modified(module, "lessons_structure_raw")
        return local_count

    module_results = await asyncio.gather(
        *[_translate_one_module(m) for m in target.modules],
        return_exceptions=True,
    )
    for module, result in zip(target.modules, module_results):
        if isinstance(result, Exception):
            log.warning(
                "course_duplication_module_translate_error",
                module_position=module.position,
                error=str(result)[:200],
            )
            continue
        stats["strings_translated"] += result
    return stats


# Path interno per il JSONB di `module.lessons_structure_raw` (output
# di Fase 2): mirror dello schema `LessonStructureModuleOutput`.
_LESSONS_STRUCTURE_RAW_PATHS: tuple[str, ...] = (
    "lessons[].title",
    "lessons[].learning_objectives[]",
    "lessons[].mandatory_topics[].topic",
    "lessons[].mandatory_topics[].rationale",
    "lessons[].prerequisites[]",
    "lessons[].section_outline[].title",
    "lessons[].section_outline[].purpose",
)


async def _translate_lesson(
    db: AsyncSession,
    *,
    lesson: CourseLesson,
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
    phase: str,
) -> dict[str, Any]:
    """Traduce una singola lezione per la fase indicata.

    `phase` ∈ {"meta", "content", "slides", "speech"}.
    - `meta`: campi diretti CourseLesson (title, summary, learning_objectives,
      mandatory_topics, prerequisites, section_outline, recommended_bibliography)
    - `content`: lesson.content_raw (rispettando is_assessment)
    - `slides`: lesson.slides_raw
    - `speech`: lesson.speech_raw
    """
    from sqlalchemy.orm.attributes import flag_modified

    stats = {"strings_translated": 0}

    if phase == "meta":
        items: dict[str, str] = {}
        for field in _paths.LESSON_TRANSLATE_FIELDS:
            value = getattr(lesson, field, None)
            if isinstance(value, str) and value.strip():
                items[field] = value
        if items:
            translated = await _translate_batch_resilient(
                items=items,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
                op_label=f"lesson_meta {lesson.lesson_code or lesson.id}",
            )
            for field in _paths.LESSON_TRANSLATE_FIELDS:
                if field in translated:
                    setattr(lesson, field, translated[field])
            stats["strings_translated"] += len(items)
        # JSONB diretti (learning_objectives, ecc.)
        for field, sub_paths in _paths.LESSON_JSONB_TRANSLATE_PATHS.items():
            value = getattr(lesson, field, None)
            if not value:
                continue
            s = await _translate_jsonb_inplace(
                value,
                paths=sub_paths,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
            )
            stats["strings_translated"] += s["strings_translated"]
            flag_modified(lesson, field)

    elif phase == "content":
        if lesson.content_raw:
            paths = (
                _paths.ASSESSMENT_RAW_TRANSLATE_PATHS
                if lesson.is_assessment
                else _paths.CONTENT_RAW_TRANSLATE_PATHS
            )
            s = await _translate_jsonb_inplace(
                lesson.content_raw,
                paths=paths,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
            )
            stats["strings_translated"] += s["strings_translated"]
            flag_modified(lesson, "content_raw")

    elif phase == "slides":
        if lesson.slides_raw:
            s = await _translate_jsonb_inplace(
                lesson.slides_raw,
                paths=_paths.SLIDES_RAW_TRANSLATE_PATHS,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
            )
            stats["strings_translated"] += s["strings_translated"]
            flag_modified(lesson, "slides_raw")

    elif phase == "speech":
        if lesson.speech_raw:
            s = await _translate_jsonb_inplace(
                lesson.speech_raw,
                paths=_paths.SPEECH_RAW_TRANSLATE_PATHS,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
            )
            stats["strings_translated"] += s["strings_translated"]
            flag_modified(lesson, "speech_raw")
            # Aggiorna anche `lesson.speech_raw.language` se presente.
            if isinstance(lesson.speech_raw, dict):
                lesson.speech_raw["language"] = target_lang_code
                flag_modified(lesson, "speech_raw")
    return stats


async def _translate_glossary(
    db: AsyncSession,
    *,
    target: Course,
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
) -> dict[str, Any]:
    """Traduce `target.glossary_raw` (term + usage_note). `translation`
    viene AZZERATO perché era già una traduzione DALLA lingua del
    corso a un'altra (es. EN per un corso IT) — non più valida nel
    nuovo contesto."""
    from sqlalchemy.orm.attributes import flag_modified

    if not target.glossary_raw:
        return {"strings_translated": 0}
    s = await _translate_jsonb_inplace(
        target.glossary_raw,
        paths=_paths.GLOSSARY_TRANSLATE_PATHS,
        source_lang_code=source_lang_code,
        source_lang_name=source_lang_name,
        target_lang_code=target_lang_code,
        target_lang_name=target_lang_name,
    )
    # Azzera `terms[].translation` (era traduzione contestuale alla
    # lingua sorgente, non più valida).
    terms = target.glossary_raw.get("terms") if isinstance(
        target.glossary_raw, dict
    ) else None
    if isinstance(terms, list):
        for t in terms:
            if isinstance(t, dict):
                t["translation"] = ""
    flag_modified(target, "glossary_raw")
    return s


async def _translate_document_summaries(
    db: AsyncSession,
    *,
    target: Course,
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
) -> dict[str, Any]:
    """Traduce `summary` JSONB di ogni `CourseDocument` di `target`."""
    from sqlalchemy.orm.attributes import flag_modified

    total = 0
    for doc in target.documents:
        if not doc.summary:
            continue
        s = await _translate_jsonb_inplace(
            doc.summary,
            paths=_paths.DOCUMENT_SUMMARY_TRANSLATE_PATHS,
            source_lang_code=source_lang_code,
            source_lang_name=source_lang_name,
            target_lang_code=target_lang_code,
            target_lang_name=target_lang_name,
        )
        total += s["strings_translated"]
        # Aggiorna `detected_language` al nuovo target.
        if isinstance(doc.summary, dict):
            doc.summary["detected_language"] = target_lang_code
        flag_modified(doc, "summary")
    return {"strings_translated": total}


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------


# Stati del corso fino a 'slides_approved' compreso. Il corso target
# duplicato non oltrepassa mai questa soglia: il discorso tradotto
# richiede riapprovazione manuale; video / avatar vengono rigenerati.
_SLIDES_APPROVED_OR_BELOW: frozenset[str] = frozenset({
    "draft",
    "architecture_pending",
    "architecture_ready",
    "architecture_approved",
    "lessons_structure_pending",
    "lessons_structure_ready",
    "lessons_structure_approved",
    "content_pending",
    "content_ready",
    "content_approved",
    "slides_pending",
    "slides_ready",
    "slides_approved",
})


def _cap_status_for_duplication(source_status: str) -> str:
    """Cap dello status del corso target a `slides_approved`.

    Se la sorgente è già nelle fasi `speech_*`, `video_*`,
    `avatar_video_*`, `published` o `archived`, il target torna a
    `slides_approved` (l'utente dovrà ri-generare/riapprovare lo
    speech e i video nel corso target).
    """
    if source_status in _SLIDES_APPROVED_OR_BELOW:
        return source_status
    return "slides_approved"


async def _finalize(
    db: AsyncSession,
    *,
    source: Course,
    target: Course,
) -> None:
    """Allinea `target.status` al source CAPPATO a `slides_approved`,
    e downgrada lo `speech_status` di ogni lezione del target a
    `ready` (forza riapprovazione del discorso tradotto)."""
    target_status = _cap_status_for_duplication(source.status)
    advance_course_status(target, target_status)
    # Per-lesson: downgrade speech_status='approved' → 'ready'. La
    # traduzione AI dello speech è quella più sensibile (testo lungo
    # con vincoli TTS): meglio che l'utente riapprovi manualmente.
    for module in target.modules:
        for lesson in module.lessons:
            if lesson.speech_status == "approved":
                lesson.speech_status = "ready"
                lesson.speech_approved_at = None

