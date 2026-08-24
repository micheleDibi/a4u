"""course: normalizzazione one-shot dello status-indicatore

Col gating per-unità `course.status` è un INDICATORE di avanzamento
(milestone monotona), non più un lock: i gate di generazione leggono gli
stati per-lezione/per-modulo. Questa migrazione data-only riallinea i
corsi il cui status è rimasto "indietro" rispetto ai dati (es. dopo una
regressione esplicita da bulk-regenerate mai risalita: i ricalcoli
scattano solo alla successiva azione della fase giusta).

Regole (replicano i 6 `_recompute_course_*_status` — vedi anche
`normalize_course_status_from_data` in core/course_phase_order.py, il
riferimento di equivalenza logica testato dalla suite):
- UPDATE in ordine di rank crescente; ognuno applicabile solo da stati
  di rank inferiore → mai regressioni, l'ultimo soddisfatto vince.
- `published`/`archived` mai toccati; `architecture_pending` escluso
  (worker P1 in volo).
- Ogni statement richiede ESPLICITAMENTE >=1 riga rilevante (un
  NOT EXISTS naive è vacuamente vero sugli insiemi vuoti: un corso di
  sole lezioni-verifica salterebbe a `speech_approved`).
- Content conta TUTTE le lezioni (assessment incluse); slides/speech/
  video/avatar escludono le assessment — asimmetria dei recompute.
- Skip dei target di duplicazione con job non `ready` (pending/
  processing: sarà `_finalize` ad allinearli; failed: cloni parziali
  committati a metà, da non normalizzare).

Idempotente e rieseguibile senza danni. `downgrade` = no-op documentato
(normalizzazione irreversibile: lo stato precedente non è ricostruibile).
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "0034"
down_revision: str | None = "0033"
branch_labels = None
depends_on = None

# Ordine di rank (mirror di core/course_phase_order.COURSE_STATUS_RANK).
_RANK_ORDER = [
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
    "speech_pending",
    "speech_ready",
    "speech_approved",
    "video_pending",
    "video_ready",
    "avatar_video_pending",
    "avatar_video_ready",
    "published",
    "archived",
]

_NO_ACTIVE_DUPLICATION = (
    "NOT EXISTS (SELECT 1 FROM course_duplication_job j "
    "WHERE j.target_course_id = c.id AND j.status <> 'ready')"
)


def _from_set(target: str) -> str:
    """Stati di rank inferiore al target da cui l'avanzamento è ammesso
    (esclusi `architecture_pending`, `published`, `archived`)."""
    lower = _RANK_ORDER[: _RANK_ORDER.index(target)]
    allowed = [s for s in lower if s != "architecture_pending"]
    return ", ".join(f"'{s}'" for s in allowed)


def _lesson_milestone(column: str, target: str, *, non_assessment: bool) -> str:
    """Condizioni per le milestone ready/approved delle fasi 3-5."""
    scope = "AND l.is_assessment = false" if non_assessment else ""
    base = f"SELECT 1 FROM course_lesson l WHERE l.course_id = c.id {scope}"
    if target.endswith("_approved"):
        return (
            f"EXISTS ({base}) "
            f"AND NOT EXISTS ({base} AND l.{column} <> 'approved')"
        )
    # *_ready: tutte in ready|approved con almeno una `ready` (se sono
    # tutte approved interviene lo statement successivo).
    return (
        f"EXISTS ({base} AND l.{column} = 'ready') "
        f"AND NOT EXISTS ({base} "
        f"AND l.{column} NOT IN ('ready', 'approved'))"
    )


def _media_milestone(column: str) -> str:
    """video_ready / avatar_video_ready: >=1 `ready` non-assessment e
    nessuna in pending/processing/failed (regola dei recompute media)."""
    base = (
        "SELECT 1 FROM course_lesson l "
        "WHERE l.course_id = c.id AND l.is_assessment = false"
    )
    return (
        f"EXISTS ({base} AND l.{column} = 'ready') "
        f"AND NOT EXISTS ({base} "
        f"AND l.{column} NOT IN ('ready', 'cancelled', 'empty'))"
    )


def _statements() -> list[tuple[str, str]]:
    module_base = "SELECT 1 FROM course_module m WHERE m.course_id = c.id"
    return [
        (
            "lessons_structure_approved",
            f"EXISTS ({module_base}) "
            f"AND NOT EXISTS ({module_base} "
            f"AND m.lessons_structure_status <> 'approved')",
        ),
        (
            "content_ready",
            _lesson_milestone(
                "content_status", "content_ready", non_assessment=False
            ),
        ),
        (
            "content_approved",
            _lesson_milestone(
                "content_status", "content_approved", non_assessment=False
            ),
        ),
        (
            "slides_ready",
            _lesson_milestone(
                "slides_status", "slides_ready", non_assessment=True
            ),
        ),
        (
            "slides_approved",
            _lesson_milestone(
                "slides_status", "slides_approved", non_assessment=True
            ),
        ),
        (
            "speech_ready",
            _lesson_milestone(
                "speech_status", "speech_ready", non_assessment=True
            ),
        ),
        (
            "speech_approved",
            _lesson_milestone(
                "speech_status", "speech_approved", non_assessment=True
            ),
        ),
        ("video_ready", _media_milestone("video_status")),
        ("avatar_video_ready", _media_milestone("avatar_video_status")),
    ]


def upgrade() -> None:
    for target, milestone in _statements():
        op.execute(
            sa.text(
                f"UPDATE course c SET status = '{target}' "
                f"WHERE c.status IN ({_from_set(target)}) "
                f"AND {_NO_ACTIVE_DUPLICATION} "
                f"AND {milestone}"
            )
        )


def downgrade() -> None:
    # No-op: la normalizzazione è irreversibile (lo status precedente non
    # è ricostruibile) e idempotente — rieseguirla non produce danni.
    pass
