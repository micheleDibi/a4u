from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("ENV", "test")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault(
    "JWT_SECRET", "test-secret-with-at-least-32-bytes-padding-here"
)
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "")

# Database test (sovrascrivibile via env)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://a4u:a4u_dev_password@localhost:5432/a4u_test",
)


@pytest_asyncio.fixture(scope="session")
async def _engine():
    from app.core.config import get_settings
    from app.db.base import Base
    import app.models  # noqa: F401  registra i metadata di tutti i modelli

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(_engine) -> AsyncIterator[AsyncSession]:
    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def seeded_db(_engine) -> AsyncIterator[AsyncSession]:
    from app.db.seed import ensure_seed

    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        await ensure_seed(session)
        await session.commit()
        yield session


@pytest_asyncio.fixture
async def client(_engine) -> AsyncIterator[AsyncClient]:
    """Client HTTPX collegato all'app FastAPI con un session-factory che usa il test engine."""
    from app.core.deps import get_db
    from app.db import session as session_mod
    from app.db.seed import ensure_seed
    from app.main import create_app

    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    session_mod.async_session_factory = SessionLocal  # type: ignore[assignment]
    session_mod.engine = _engine  # type: ignore[assignment]

    async with SessionLocal() as session:
        await ensure_seed(session)
        await session.commit()

    app = create_app()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with SessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:5173"},
    ) as c:
        yield c


@pytest.fixture
def random_email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@a4u-tests.it"


@pytest.fixture(autouse=True)
def _offline_figure_intro(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il PROMPT 23 (frase che introduce una figura) non esce mai in rete nei
    test: vale il ripiego senza chiamata. I test del servizio usano la
    funzione vera con un trasporto simulato."""
    from app.services import openai_figure_intro_service as intro

    async def offline(item: object) -> tuple[str, dict[str, object]]:
        raise intro.OpenAIFigureIntroError(None, "PROMPT 23 non simulato nel test")

    monkeypatch.setattr(intro, "write_intro", offline)
