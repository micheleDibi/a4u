# Backend 09 — `app/utils/`

Helper di utilità che non appartengono a un dominio specifico.

## `app/utils/__init__.py`

Vuoto.

---

## `app/utils/pagination.py`

**Scopo**: dipendenza FastAPI per parametri di paginazione comuni.

### Classi

#### `class PaginationParams(BaseModel)`

- `page: int` (≥1, default 1).
- `page_size: int` (1..200, default 25).

### Funzioni

#### `pagination_query(page, page_size) -> PaginationParams`

Dependency: legge `?page=...&page_size=...` con vincoli (`Query(ge=1)` e
`Query(ge=1, le=200)`). Restituisce un `PaginationParams`.

> Attualmente i router definiscono manualmente `page`/`page_size` come
> `Annotated[int, Query(...)]` direttamente; questa utility è disponibile
> per refactor futuri se la duplicazione cresce.
