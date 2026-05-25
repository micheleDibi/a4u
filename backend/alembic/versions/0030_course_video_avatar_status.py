"""course video/avatar status

Estende il CHECK constraint `ck_course_status_valid` con 4 nuovi
valori per le Fasi 6 (Video) e 6b (Video con Avatar):
  - video_pending
  - video_ready
  - avatar_video_pending
  - avatar_video_ready

Niente "approved" perché `video_status`/`avatar_video_status` a
livello lezione non hanno uno stato di approvazione (pipeline
solo: empty | pending | processing | ready | failed | cancelled),
quindi due stati per fase sono sufficienti — simmetrico a come si
comportano slides/speech ma senza il terzo step "approved".

Lo status del corso viene aggiornato automaticamente dai service
`course_lesson_video_service` / `course_lesson_avatar_video_service`
(e dai relativi worker) tramite helper `_recompute_course_*_status`,
con la stessa convenzione di slides/speech.

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-25
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # course.status — estendi CHECK constraint per aggiungere
    # `video_pending`, `video_ready`, `avatar_video_pending`,
    # `avatar_video_ready` (Fase 6 + 6b).
    op.drop_constraint("ck_course_status_valid", "course", type_="check")
    op.create_check_constraint(
        "ck_course_status_valid",
        "course",
        "status IN ("
        "'draft','architecture_pending','architecture_ready',"
        "'architecture_approved','lessons_structure_pending',"
        "'lessons_structure_ready','lessons_structure_approved',"
        "'content_pending','content_ready','content_approved',"
        "'slides_pending','slides_ready','slides_approved',"
        "'speech_pending','speech_ready','speech_approved',"
        "'video_pending','video_ready',"
        "'avatar_video_pending','avatar_video_ready',"
        "'published','archived')",
    )


def downgrade() -> None:
    # Ripristina il CHECK constraint senza i 4 nuovi stati.
    op.drop_constraint("ck_course_status_valid", "course", type_="check")
    op.create_check_constraint(
        "ck_course_status_valid",
        "course",
        "status IN ("
        "'draft','architecture_pending','architecture_ready',"
        "'architecture_approved','lessons_structure_pending',"
        "'lessons_structure_ready','lessons_structure_approved',"
        "'content_pending','content_ready','content_approved',"
        "'slides_pending','slides_ready','slides_approved',"
        "'speech_pending','speech_ready','speech_approved',"
        "'published','archived')",
    )
