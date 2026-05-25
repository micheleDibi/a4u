"""Ordine totale delle fasi di `Course.status`.

Lo stato del corso avanza per fasi sequenziali (architecture → lessons
structure → content → slides → speech → video → avatar_video →
published/archived). Ogni service AI ricalcola lo stato del corso in
base allo stato delle lezioni della propria fase, ma DEVE essere
monotono: una volta che il corso è avanzato (es. slides approvate), il
service del Content non deve riportarlo indietro a `content_approved`.

`advance_course_status` setta il nuovo stato solo se non significa
regressione (rank del nuovo ≥ rank dell'attuale). Usato dai 6
`_recompute_course_*_status` per evitare il bug "approvo slide → poi
approvo un contenuto → corso torna a content_approved → non posso più
generare discorso".
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.course import Course


# Rank totale degli stati di Course.status. L'ordine riflette le fasi
# della pipeline: dentro la stessa fase, pending < ready < approved.
# Video/avatar_video non hanno `approved` (vedi schemas/course.py).
COURSE_STATUS_RANK: dict[str, int] = {
    "draft": 0,
    "architecture_pending": 1,
    "architecture_ready": 2,
    "architecture_approved": 3,
    "lessons_structure_pending": 4,
    "lessons_structure_ready": 5,
    "lessons_structure_approved": 6,
    "content_pending": 7,
    "content_ready": 8,
    "content_approved": 9,
    "slides_pending": 10,
    "slides_ready": 11,
    "slides_approved": 12,
    "speech_pending": 13,
    "speech_ready": 14,
    "speech_approved": 15,
    "video_pending": 16,
    "video_ready": 17,
    "avatar_video_pending": 18,
    "avatar_video_ready": 19,
    "published": 20,
    "archived": 21,
}


def advance_course_status(course: "Course", new_status: str) -> None:
    """Aggiorna `course.status` a `new_status` SOLO se non è una
    regressione di fase (rank monotono ≥).

    Esempi:
    - corso `slides_approved`, service Content computa `content_approved`
      → no-op (slides_approved è dopo content_approved).
    - corso `content_ready`, service Content computa `content_approved`
      → avanza a content_approved.
    - corso `slides_pending`, service Slides computa `slides_approved`
      → avanza.
    """
    current_rank = COURSE_STATUS_RANK.get(course.status, 0)
    new_rank = COURSE_STATUS_RANK.get(new_status, 0)
    if new_rank >= current_rank:
        course.status = new_status
