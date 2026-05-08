# Backend overview

Applicazione FastAPI Python 3.12, distribuita in monorepo sotto `backend/`.
Tutti i moduli sotto `app/` sono parte del package importabile `app`.

## Struttura ad albero

```
backend/
├── pyproject.toml              # progetto + dipendenze + tool config
├── .python-version             # 3.12 (hint per pyenv / strumenti compatibili)
├── alembic.ini                 # config Alembic
├── alembic/
│   ├── env.py                  # bootstrap async migrations
│   ├── script.py.mako          # template revision
│   └── versions/0001_initial.py … 0006_pdf_bg_opacity.py
├── uploads/                    # filesystem upload (gitignored, montato come StaticFiles)
├── tests/
│   ├── conftest.py             # fixture httpx async + DB transaction
│   ├── test_health.py
│   ├── test_permissions.py
│   └── test_auth_flow.py
└── app/
    ├── __init__.py             # __version__
    ├── main.py                 # app factory, lifespan, middleware
    ├── core/                   # config, security, deps, errors, audit, permissions
    ├── middleware/             # request_id, access_log, security_headers, csrf
    ├── db/                     # base (DeclarativeBase), session, seed
    ├── models/                 # 13 modelli SQLAlchemy 2
    ├── schemas/                # Pydantic v2 DTOs
    ├── services/               # logica di business
    ├── api/v1/                 # router REST
    └── utils/
```

## Documentazione per file

- [01 — Entry: `__init__.py` + `main.py`](01-entry.md)
- [02 — `core/` (config, logging, security, deps, errors, audit, rate_limit, permissions)](02-core.md)
- [03 — `middleware/`](03-middleware.md)
- [04 — `db/`](04-db.md)
- [05 — `models/`](05-models.md)
- [06 — `schemas/`](06-schemas.md)
- [07 — `services/`](07-services.md)
- [08 — `api/v1/`](08-api.md)
- [09 — `utils/`](09-utils.md)
- [10 — `alembic/`](10-alembic.md)
- [11 — `tests/`](11-tests.md)

## Convenzioni interne

- **Async ovunque**: route, services, sessione SQLAlchemy.
- **Type hints**: `Mapped[T]` per modelli, `Annotated[T, Depends(...)]` per dep.
- **Errori di dominio**: sottoclassi di `AppError` in `core/errors.py`.
  I service alzano `NotFoundError`, `ConflictError`, `PermissionDeniedError`,
  `ValidationAppError`, `RateLimitedError`, `AuthenticationError`.
- **Audit**: ogni operazione mutating chiama `core.audit.write_audit(...)`.
- **Sessione DB**: gestita dal dep `get_db`. I services ricevono `AsyncSession`
  come parametro, non aprono mai una sessione propria (eccezioni: lifespan
  e seed).
- **Permission**: `require(*codes)` come dep nei router; il resolver legge
  `org_id` dal path.

## Dipendenze interne (mappa import)

```
api/v1/*
  ├─ schemas/*
  ├─ services/*
  └─ core/{deps, permissions, audit, errors, rate_limit, security, config, logging}

services/*
  ├─ models/*
  ├─ schemas/*
  ├─ core/{audit, errors, permissions, security, logging, config}
  └─ services/file_service (per upload/delete)

models/*
  └─ db/base

db/*
  ├─ models/*
  └─ core/{config, logging, permissions, security}

middleware/*
  └─ core/{config, logging}

main.py
  ├─ api/v1
  ├─ db/{seed, session}
  ├─ middleware/*
  └─ core/{config, logging, errors, rate_limit}
```
