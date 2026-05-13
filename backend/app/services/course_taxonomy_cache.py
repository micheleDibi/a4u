"""Cache in-memory dei termini delle tassonomie corso.

Le tassonomie sono dati "platform-wide" che cambiano raramente (solo via
admin endpoint). Sono però richieste in batch da ogni CourseEditorPage:
8 chiamate `GET /course-taxonomy/{type}` in parallelo, ognuna che fa una
query DB + serializzazione Pydantic = ~1s di TTFB per pochi KB di JSON.

Questo modulo cache-a per-tipo (chiave: `(taxonomy_type, only_active)`)
con:
- TTL "backstop" (default 60s) — copre cambiamenti dimenticati.
- Invalidation esplicita via :func:`invalidate` chiamata dai service di
  mutazione (create/update/delete/move/auto_translate). È la via primaria
  per propagare modifiche in tempo reale.
- ETag stabile (hash sha256 troncato del payload serializzato) — usato
  dall'endpoint pubblico per emettere `If-None-Match` → 304 senza
  ri-emettere il body.

Concurrency: un lock per-tipo evita il "thundering herd" quando N richieste
arrivano in parallelo al cold start sullo stesso tipo; tipi diversi non
si bloccano a vicenda.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_taxonomy import CourseTaxonomyTerm
from app.schemas.course_taxonomy import TaxonomyTermOut

# (taxonomy_type, only_active) → (payload_list_dict, etag, monotonic_ts)
_CACHE: dict[tuple[str, bool], tuple[list[dict[str, Any]], str, float]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_TTL_SECONDS = 60.0


def _lock_for(taxonomy_type: str) -> asyncio.Lock:
    lock = _LOCKS.get(taxonomy_type)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[taxonomy_type] = lock
    return lock


def _compute_etag(payload: list[dict[str, Any]]) -> str:
    data = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


async def get_active_terms(
    db: AsyncSession,
    taxonomy_type: str,
    *,
    only_active: bool = True,
) -> tuple[list[TaxonomyTermOut], str]:
    """Ritorna `(terms, etag)`. Hit della cache se TTL non scaduto."""
    key = (taxonomy_type, only_active)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached is not None:
        payload, etag, ts = cached
        if now - ts < _TTL_SECONDS:
            return [TaxonomyTermOut.model_validate(p) for p in payload], etag

    lock = _lock_for(taxonomy_type)
    async with lock:
        # double-check dopo il lock: un altro task potrebbe averla appena popolata
        cached = _CACHE.get(key)
        now = time.monotonic()
        if cached is not None:
            payload, etag, ts = cached
            if now - ts < _TTL_SECONDS:
                return [TaxonomyTermOut.model_validate(p) for p in payload], etag

        q = select(CourseTaxonomyTerm).where(
            CourseTaxonomyTerm.taxonomy_type == taxonomy_type
        )
        if only_active:
            q = q.where(CourseTaxonomyTerm.is_active.is_(True))
        q = q.order_by(
            CourseTaxonomyTerm.parent_id.asc().nulls_first(),
            CourseTaxonomyTerm.sort_order.asc(),
        )
        rows = (await db.execute(q)).scalars().all()
        terms = [TaxonomyTermOut.model_validate(r) for r in rows]
        payload = [t.model_dump() for t in terms]
        etag = _compute_etag(payload)
        _CACHE[key] = (payload, etag, time.monotonic())
        return terms, etag


def invalidate(taxonomy_type: str) -> None:
    """Cancella le entry cached per il tipo (sia only_active=True che False)."""
    for k in list(_CACHE.keys()):
        if k[0] == taxonomy_type:
            _CACHE.pop(k, None)


def invalidate_all() -> None:
    _CACHE.clear()


def compute_aggregate_etag(etags: list[str]) -> str:
    """ETag combinato per response bulk: hash della concatenazione degli
    etag dei tipi inclusi, ordinati per tipo (stabilità). Cambia se uno
    qualsiasi dei sub-payload cambia."""
    joined = "|".join(etags).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:16]
