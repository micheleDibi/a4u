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
    remote_storage,
)


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


def _read_lesson_pdf(kind: PdfKind, rel: str, lesson: CourseLesson) -> bytes:
    """Scarica i bytes del PDF di una lezione dallo storage attivo (RETR su
    OVH, read locale altrimenti). Tutti e tre i `kind` condividono la stessa
    root: il `kind` è già nel suffisso del nome file."""
    del kind  # parametro mantenuto per coerenza API
    try:
        return remote_storage.get_storage().download_bytes(
            remote_storage.pdf_key(rel)
        )
    except remote_storage.StorageFileNotFound as exc:
        raise NotFoundError(
            f"File PDF mancante sullo storage per la lezione {lesson.lesson_code}.",
            code="module_pdf_file_missing",
        ) from exc


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

    # La lezione-verifica non ha PDF: esclusa dal merge/zip di modulo.
    return sorted(
        [l for l in module.lessons if not l.is_assessment], key=_key
    )


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

    # L'esistenza effettiva sullo storage è verificata al momento della
    # lettura (RETR), che solleva NotFoundError per la lezione mancante.
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
        data = _read_lesson_pdf(kind, rel, lesson)
        writer.append(
            io.BytesIO(data), outline_item=lesson.title or lesson.lesson_code
        )

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
            data = _read_lesson_pdf(kind, rel, lesson)
            filename = _lesson_pdf_filename(kind, course.title, lesson)
            zf.writestr(filename, data)
    return buf.getvalue()


# === Aggregazione a livello CORSO (tutti i moduli) ==========================


def _ordered_modules(course: Course) -> list[CourseModule]:
    """Moduli del corso ordinati per `module_code` (M1, M2, ...)."""

    def _key(m: CourseModule) -> tuple[int, object]:
        mm = re.match(r"^M(\d+)$", m.module_code or "")
        if mm:
            return (0, int(mm.group(1)))
        return (1, m.module_code or "")

    return sorted(course.modules, key=_key)


def _ensure_course_pdfs_ready(
    kind: PdfKind, course: Course
) -> list[tuple[CourseModule, list[CourseLesson]]]:
    """Verifica che TUTTE le lezioni (non-verifica) di TUTTI i moduli
    abbiano il PDF `kind` pronto + path + file su disco. Ritorna la lista
    ordinata di (modulo, lezioni). Solleva ConflictError/NotFoundError con
    codici diagnostici se la pre-condizione non è soddisfatta."""
    groups: list[tuple[CourseModule, list[CourseLesson]]] = []
    not_ready: list[CourseLesson] = []
    total = 0
    for module in _ordered_modules(course):
        lessons = _ordered_lessons(module)
        if not lessons:
            continue
        total += len(lessons)
        not_ready.extend(
            l
            for l in lessons
            if _lesson_pdf_status(kind, l) != "ready" or not _lesson_pdf_path(kind, l)
        )
        groups.append((module, lessons))

    if total == 0:
        raise ConflictError(
            f"Nessuna lezione con PDF da scaricare ({_kind_label(kind)}).",
            code="course_has_no_lessons",
        )
    if not_ready:
        codes = ", ".join(l.lesson_code for l in not_ready)
        raise ConflictError(
            f"PDF {_kind_label(kind)} non ancora pronti per: {codes}. "
            "Esporta prima i PDF mancanti.",
            code="course_pdfs_not_ready",
            meta={"missing_lessons": [str(l.id) for l in not_ready]},
        )

    # L'esistenza effettiva sullo storage è verificata in lettura (RETR).
    return groups


def course_merged_filename(kind: PdfKind, course: Course) -> str:
    return f"{_safe_segment(course.title)} — corso completo ({_kind_label(kind)}).pdf"


def course_zip_filename(kind: PdfKind, course: Course) -> str:
    return f"{_safe_segment(course.title)} — corso completo ({_kind_label(kind)}).zip"


def merge_course_pdfs(*, kind: PdfKind, course: Course) -> bytes:
    """Concatena in un unico PDF tutte le lezioni di tutti i moduli del
    corso, in ordine modulo → lezione."""
    groups = _ensure_course_pdfs_ready(kind, course)
    writer = PdfWriter()
    for module, lessons in groups:
        for lesson in lessons:
            rel = _lesson_pdf_path(kind, lesson)
            assert rel is not None
            data = _read_lesson_pdf(kind, rel, lesson)
            outline = f"{module.module_code} — {lesson.title or lesson.lesson_code}"
            writer.append(io.BytesIO(data), outline_item=outline)

    buf = io.BytesIO()
    writer.write(buf)
    writer.close()
    return buf.getvalue()


def zip_course_pdfs(*, kind: PdfKind, course: Course) -> bytes:
    """Crea uno ZIP di tutto il corso: una sottocartella per modulo, un
    PDF per ogni lezione (stesso filename del download per-lezione)."""
    groups = _ensure_course_pdfs_ready(kind, course)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for module, lessons in groups:
            folder = f"{module.module_code} {_safe_segment(module.title)}"
            for lesson in lessons:
                rel = _lesson_pdf_path(kind, lesson)
                assert rel is not None
                data = _read_lesson_pdf(kind, rel, lesson)
                filename = _lesson_pdf_filename(kind, course.title, lesson)
                zf.writestr(f"{folder}/{filename}", data)
    return buf.getvalue()
