"""Lettura read-only delle tassonomie corso da parte di qualsiasi utente
autenticato — necessaria per popolare i Select del form di creazione corso.

Le tassonomie sono platform-wide (non per-org), quindi un utente loggato
in qualsiasi organizzazione può leggere i term attivi.

La gestione (CRUD) resta esclusiva del platform admin via `/admin/course-taxonomy`
(vedi `admin_course_taxonomy.py`); le mutazioni invalidano la cache
in-memory di :mod:`app.services.course_taxonomy_cache`.

Performance:
- Cache server-side per-tipo (TTL 60s + invalidation esplicita) — riduce
  da ~1s/query a <10ms su hit.
- Endpoint `bulk` per popolare 8 tassonomie in una sola request — riduce
  i 7-8 roundtrip della pagina CourseEditor a 1.
- Header HTTP `Cache-Control: public, max-age=60, stale-while-revalidate=300`
  + `ETag` → revalidation con 304 senza ri-emettere il body.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app.core.deps import CurrentUser, DbSession
from app.core.errors import ValidationAppError
from app.schemas.course_taxonomy import TaxonomyTermOut, TaxonomyType
from app.services import course_taxonomy_cache

router = APIRouter(prefix="/course-taxonomy", tags=["course-taxonomy"])


_CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=300"


@router.get("/bulk", response_model=None)
async def list_active_terms_bulk(
    db: DbSession,
    _: CurrentUser,
    response: Response,
    types: Annotated[
        str, Query(description="Tipi separati da virgola: t1,t2,t3")
    ],
    only_active: bool = Query(default=True),
    if_none_match: Annotated[str | None, Header(alias="if-none-match")] = None,
) -> dict[str, list[TaxonomyTermOut]] | Response:
    """Batch lookup di più tassonomie in una sola richiesta.

    Esempio: `GET /course-taxonomy/bulk?types=category,eqf_level,teaching_style`
    → `{ "category": [...], "eqf_level": [...], "teaching_style": [...] }`.

    Header `ETag` è la hash della concatenazione degli ETag dei tipi
    richiesti; se il client invia `If-None-Match` con lo stesso valore,
    risposta 304 (niente body).
    """
    requested = [t.strip() for t in types.split(",") if t.strip()]
    valid_types = set(TaxonomyType.__args__)  # type: ignore[attr-defined]
    unknown = [t for t in requested if t not in valid_types]
    if unknown:
        raise ValidationAppError(
            f"Tipi tassonomia sconosciuti: {', '.join(unknown)}",
            code="unknown_taxonomy_types",
        )
    if not requested:
        raise ValidationAppError(
            "Parametro `types` vuoto.", code="empty_taxonomy_types"
        )

    # Lookup nella cache (lazy population). Ordine stabile: come passati.
    result_map: dict[str, list[TaxonomyTermOut]] = {}
    etags: list[str] = []
    for t in sorted(set(requested)):  # ordina per ETag stabile
        terms, etag = await course_taxonomy_cache.get_active_terms(
            db, t, only_active=only_active
        )
        result_map[t] = terms
        etags.append(f"{t}:{etag}")
    aggregate_etag = (
        f'W/"{course_taxonomy_cache.compute_aggregate_etag(etags)}"'
    )

    # 304 se il client ha già il payload aggiornato.
    if if_none_match and if_none_match.strip() == aggregate_etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={
                "ETag": aggregate_etag,
                "Cache-Control": _CACHE_CONTROL,
            },
        )

    response.headers["ETag"] = aggregate_etag
    response.headers["Cache-Control"] = _CACHE_CONTROL
    # Mantiene l'ordine richiesto dal client (utile per loop sul frontend).
    return {t: result_map[t] for t in requested}


@router.get("/{taxonomy_type}", response_model=None)
async def list_active_terms(
    taxonomy_type: TaxonomyType,
    db: DbSession,
    _: CurrentUser,
    response: Response,
    only_active: bool = Query(default=True),
    if_none_match: Annotated[str | None, Header(alias="if-none-match")] = None,
) -> list[TaxonomyTermOut] | Response:
    terms, etag = await course_taxonomy_cache.get_active_terms(
        db, taxonomy_type, only_active=only_active
    )
    quoted_etag = f'W/"{etag}"'
    if if_none_match and if_none_match.strip() == quoted_etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={
                "ETag": quoted_etag,
                "Cache-Control": _CACHE_CONTROL,
            },
        )
    response.headers["ETag"] = quoted_etag
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return terms
