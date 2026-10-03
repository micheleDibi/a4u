from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()

# Parametri di connessione asyncpg comuni a tutti gli engine dell'app (test compresi).
# `jit=off`: in produzione il JIT di Postgres faceva durare 610 ms la query di poll del
# content worker (3,8 ms senza JIT). Le query dell'app sono brevi e il costo di
# compilazione del JIT non si ripaga mai.
ENGINE_CONNECT_ARGS: dict[str, Any] = {"server_settings": {"jit": "off"}}

engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.database_pool_size,
    max_overflow=_settings.database_max_overflow,
    pool_pre_ping=True,
    echo=False,
    future=True,
    connect_args=ENGINE_CONNECT_ARGS,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
