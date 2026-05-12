"""Aggregazione dei PDF per modulo (concatena tutte le lezioni).

Espone due operazioni per ciascuna delle tre pipeline PDF (contenuti, slide,
discorso):

- ``merge_module_pdfs(...)`` → un singolo PDF concatenato di tutte le lezioni
  del modulo, in ordine di ``lesson_code``.
- ``zip_module_pdfs(...)`` → uno ``.zip`` che contiene un PDF per ogni
  lezione (filename con prefisso ``{lesson_code} - ...`` per ordinarli).

Pre-condizione: TUTTE le lezioni del modulo devono avere il PDF della
pipeline richiesta in stato ``ready`` con ``*_pdf_path`` valorizzato. Se
manca anche solo una lezione → ``ConflictError`` (l'utente deve aspettare
che i worker abbiano completato l'export).

Pipeline supportate: ``"content"``, ``"slides"``, ``"speech"``.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Literal

from pypdf import PdfWriter

from app.core.errors import ConflictError, NotFoundError
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.services import (
    course_lesson_pdf_service,
    course_lesson_slides_pdf_service,
    course_lesson_speech_pdf_service,
)
from app.services.course_lesson_pdf_service import pdf_absolute_path


PdfKind = Literal["content", "slides", "speech"]


# === Helpers per-kind =======================================================
# Ogni kind ha tre primitive distinte (status, path, absolute path resolver,
# filename per download). Le centralizziamo qui per evitare branch in tutto
# il file.


def _kind_label(kind: PdfKind) -> str:
    """Etichetta human-readable per messaggi di errore e nomi file."""
    return {
        "content": "Contenuti",
        "slides": "Slide",
        "speech": "Discorso",
    }[kind]


def _lesson_pdf_status(kind: PdfKind, lesson: CourseLesson) -> str:
    if kind == "content":
        return lesson.pdf_status
    if kind == "slides":
        return lesson.slides_pdf_status
    return lesson.speech_pdf_status


def _lesson_pdf_path(kind: PdfKind, lesson: CourseLesson) -> str | None:
    if kind == "content":
        return lesson.pdf_path
    if kind == "slides":
        return lesson.slides_pdf_path
    return lesson.speech_pdf_path


def _lesson_pdf_absolute_path(kind: PdfKind, rel: str) -> Path:
    # Tutti e tre i kind condividono la stessa root: il `kind` viene già
    # codificato nel suffisso del nome file (`{lesson_id}.pdf`,
    # `_slides.pdf`, `_speech.pdf`). Quindi un solo resolver basta.
    del kind  # parametro mantenuto per coerenza API
    return pdf_absolute_path(rel)


def _lesson_pdf_filename(kind: PdfKind, course_title: str, lesson: CourseLesson) -> str:
    if kind == "content":
        return course_lesson_pdf_service.pdf_filename_for_download(
            course_title, lesson
        )
    if kind == "slides":
        return course_lesson_slides_pdf_service.slides_pdf_filename_for_download(
            course_title, lesson
        )
    return course_lesson_speech_pdf_service.speech_pdf_filename_for_download(
        course_title, lesson
    )


# === Module PDF presence check ==============================================


def _ordered_lessons(module: CourseModule) -> list[CourseLesson]:
    """Lezioni del modulo ordinate per `lesson_code` (M1.L1, M1.L2, ...).

    Fallback all'ordine inserito se il code non matcha il pattern atteso.
    """

    def _key(l: CourseLesson) -> tuple[int, str]:
        m = re.match(r"^M\d+\.L(\d+)$", l.lesson_code or "")
        if m:
            return (0, m.group(1).zfill(6))
        return (1, l.lesson_code or "")

    return sorted(list(module.lessons), key=_key)


def _ensure_all_pdfs_ready(
    kind: PdfKind, module: CourseModule
) -> list[CourseLesson]:
    """Verifica che tutte le lezioni del modulo abbiano il PDF della
    pipeline `kind` in stato `ready` + path valorizzato + file presente.

    Ritorna la lista delle lezioni ordinate. Solleva `ConflictError` o
    `NotFoundError` con codici diagnostici se la pre-condizione non è
    soddisfatta.
    """
    lessons = _ordered_lessons(module)
    if not lessons:
        raise ConflictError(
            f"Modulo senza lezioni: niente da esportare ({_kind_label(kind)}).",
            code="module_has_no_lessons",
        )

    not_ready = [
        l for l in lessons if _lesson_pdf_status(kind, l) != "ready"
        or not _lesson_pdf_path(kind, l)
    ]
    if not_ready:
        codes = ", ".join(l.lesson_code for l in not_ready)
        raise ConflictError(
            f"PDF {_kind_label(kind)} non ancora pronti per: {codes}. "
            "Esporta prima i PDF mancanti.",
            code="module_pdfs_not_ready",
            meta={"missing_lessons": [str(l.id) for l in not_ready]},
        )

    # Verifica filesystem (rilevante in caso di pulizia volume / restore).
    for l in lessons:
        rel = _lesson_pdf_path(kind, l)
        if not rel:
            continue
        abs_path = _lesson_pdf_absolute_path(kind, rel)
        if not abs_path.is_file():
            raise NotFoundError(
                f"File PDF mancante sul filesystem per la lezione "
                f"{l.lesson_code} ({_kind_label(kind)}).",
                code="module_pdf_file_missing",
            )

    return lessons


# === Filename helpers per il bundle modulo ==================================


def _safe_segment(text: str, max_len: int = 60) -> str:
    safe = re.sub(r"[^\w\-. ]+", "_", text or "")[:max_len].strip("_ ")
    return safe or "untitled"


def module_merged_filename(
    kind: PdfKind, course: Course, module: CourseModule
) -> str:
    return (
        f"{_safe_segment(course.title)} — {module.module_code} "
        f"{_safe_segment(module.title)} ({_kind_label(kind)}).pdf"
    )


def module_zip_filename(
    kind: PdfKind, course: Course, module: CourseModule
) -> str:
    return (
        f"{_safe_segment(course.title)} — {module.module_code} "
        f"{_safe_segment(module.title)} ({_kind_label(kind)}).zip"
    )


# === Public API =============================================================


def merge_module_pdfs(
    *, kind: PdfKind, course: Course, module: CourseModule
) -> bytes:
    """Concatena tutti i PDF della pipeline `kind` del modulo in ordine
    lezione. Ritorna i bytes del PDF risultante.

    Usa ``pypdf.PdfWriter.append(...)`` che preserva metadati, font,
    immagini incorporate e segnalibri di partenza di ogni PDF lezione.
    """
    lessons = _ensure_all_pdfs_ready(kind, module)
    writer = PdfWriter()
    for lesson in lessons:
        rel = _lesson_pdf_path(kind, lesson)
        # rel non può essere None qui (validato in _ensure_all_pdfs_ready)
        assert rel is not None
        abs_path = _lesson_pdf_absolute_path(kind, rel)
        writer.append(str(abs_path), outline_item=lesson.title or lesson.lesson_code)

    buf = io.BytesIO()
    writer.write(buf)
    writer.close()
    return buf.getvalue()


def zip_module_pdfs(
    *, kind: PdfKind, course: Course, module: CourseModule
) -> bytes:
    """Crea uno ZIP con un PDF per ogni lezione del modulo, con il nome
    file user-friendly (uguale a quello del download per-lezione)."""
    lessons = _ensure_all_pdfs_ready(kind, module)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for lesson in lessons:
            rel = _lesson_pdf_path(kind, lesson)
            assert rel is not None
            abs_path = _lesson_pdf_absolute_path(kind, rel)
            filename = _lesson_pdf_filename(kind, course.title, lesson)
            zf.write(str(abs_path), arcname=filename)
    return buf.getvalue()
