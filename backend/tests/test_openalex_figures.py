"""OpenAlex per l'import dei paper e le figure della letteratura (W5-T5, W5-T6).

- import: l'URL del PDF arrivato dal client non si scarica mai; il lavoro
  si rilegge dal server (`get_work`) e si scarica il PDF del server, con la
  licenza della location open access sul documento; se la rilettura
  fallisce l'import ripiega sui metadati (.md) senza alcun download;
- `download_pdf` passa da `safe_http`: un indirizzo interno è rifiutato
  senza richieste (la SSRF storica dell'import è chiusa);
- `get_work` rifiuta un id non OpenAlex senza richieste; `api_key` in ogni
  richiesta quando configurata;
- `search_open_works` chiede solo lavori open access con licenza CC o
  pubblico dominio e scarta quelli senza PDF o con licenza non
  riconosciuta.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.schemas.paper_search import PaperOut
from app.services import file_service, openalex_client, paper_import_service
from app.services.openalex_client import OpenAlexError, _to_work
from tests.course_builders import build_course


def _work(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "https://openalex.org/W42",
        "doi": "https://doi.org/10.1000/ldv",
        "title": "Laser Doppler vibrometry",
        "authorships": [{"author": {"display_name": "Mario Rossi"}}],
        "publication_year": 2021,
        "primary_location": {"source": {"display_name": "Measurement"}},
        "open_access": {"is_oa": True},
        "best_oa_location": {
            "pdf_url": "https://arxiv.org/pdf/2304.11054",
            "landing_page_url": "https://arxiv.org/abs/2304.11054",
            "license": "cc-by",
        },
    }
    data.update(overrides)
    return data


def _paper(**overrides: Any) -> PaperOut:
    data: dict[str, Any] = {
        "id": "https://openalex.org/W42",
        "doi": "10.1000/forged",
        "title": "Titolo del client",
        "abstract": None,
        "authors": ["Eve"],
        "year": 1999,
        "journal": None,
        "citations": 0,
        "is_oa": True,
        "oa_pdf_url": "http://169.254.169.254/latest/meta-data",
        "doi_url": None,
        "work_type": None,
        "keywords": [],
        "relevance_score": None,
    }
    data.update(overrides)
    return PaperOut(**data)


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_save(payload: bytes, *, subdir: str, mime_type: str, **_: Any):
        ext = "pdf" if mime_type == "application/pdf" else "md"
        return f"/uploads/{subdir}/fake.{ext}", f"fake.{ext}", len(payload)

    monkeypatch.setattr(file_service, "save_document_from_bytes", fake_save)


async def _course(db: AsyncSession) -> tuple[Course, Any]:
    course_id, _org, user = await build_course(db, modules=1, lessons_per_module=1)
    course = await db.get(Course, course_id)
    assert course is not None
    return course, user


async def test_import_downloads_only_the_server_pdf(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded: list[str] = []

    async def fake_get_work(work_id: str) -> Any:
        assert work_id == "https://openalex.org/W42"
        return _to_work(_work())

    async def fake_download(url: str, *, max_bytes: int) -> bytes:
        downloaded.append(url)
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(paper_import_service, "get_work", fake_get_work)
    monkeypatch.setattr(paper_import_service, "download_pdf", fake_download)
    course, user = await _course(seeded_db)
    result = await paper_import_service.import_paper(
        seeded_db, course=course, paper=_paper(), actor_id=user.id
    )
    assert downloaded == ["https://arxiv.org/pdf/2304.11054"]
    doc = result.document
    assert result.mode == "pdf"
    assert doc.license == "cc_by" and doc.license_source == "openalex"
    # Metadati del server, non del client.
    assert doc.bibliography["title"] == "Laser Doppler vibrometry"
    assert doc.bibliography["authors"] == ["Mario Rossi"]
    assert doc.bibliography["doi"] == "10.1000/ldv"


async def test_reread_failure_falls_back_to_metadata_without_download(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_get_work(work_id: str) -> Any:
        raise OpenAlexError(status=403, message="api key mancante")

    async def never(url: str, *, max_bytes: int) -> bytes:
        raise AssertionError(f"download non atteso: {url}")

    monkeypatch.setattr(paper_import_service, "get_work", failing_get_work)
    monkeypatch.setattr(paper_import_service, "download_pdf", never)
    course, user = await _course(seeded_db)
    result = await paper_import_service.import_paper(
        seeded_db, course=course, paper=_paper(), actor_id=user.id
    )
    assert result.mode == "metadata" and result.document.license is None


async def test_download_pdf_blocks_internal_addresses_without_requests() -> None:
    with pytest.raises(OpenAlexError) as excinfo:
        await openalex_client.download_pdf("http://127.0.0.1/admin.pdf", max_bytes=1000)
    assert "blocked_address" in str(excinfo.value)


async def test_get_work_rejects_foreign_ids_without_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_client(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nessuna richiesta attesa")

    monkeypatch.setattr(openalex_client, "_client", no_client)
    for bad in ("../../admin", "https://evil.example/W1", "W1/../../x", ""):
        with pytest.raises(OpenAlexError) as excinfo:
            await openalex_client.get_work(bad)
        assert excinfo.value.status == 400


def _mock_client(handler: Any) -> Any:
    def factory(timeout: float = 30.0) -> httpx.AsyncClient:
        settings = openalex_client.get_settings()
        key = (settings.openalex_api_key or "").strip()
        return httpx.AsyncClient(
            base_url="https://api.openalex.org",
            transport=httpx.MockTransport(handler),
            params={"api_key": key} if key else None,
        )

    return factory


async def test_api_key_and_open_works_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = openalex_client.get_settings().model_copy(update={"openalex_api_key": "k-123"})
    monkeypatch.setattr(openalex_client, "get_settings", lambda: patched)
    real_client = openalex_client._client
    client = real_client()
    assert client.params["api_key"] == "k-123"
    await client.aclose()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        results = [
            _work(),
            _work(id="https://openalex.org/W43", best_oa_location={"license": "cc-by"}),
            _work(
                id="https://openalex.org/W44",
                best_oa_location={"pdf_url": "https://x.org/a.pdf", "license": "other-oa"},
            ),
        ]
        return httpx.Response(200, json={"results": results})

    monkeypatch.setattr(openalex_client, "_client", _mock_client(handler))
    works = await openalex_client.search_open_works("laser doppler vibrometer", per_page=5)
    assert [w.id for w in works] == ["https://openalex.org/W42"]
    assert openalex_client.oa_location_license(works[0]) == "cc_by"
    params = dict(seen[0].url.params)
    assert params["api_key"] == "k-123"
    assert params["filter"].startswith("is_oa:true,best_oa_location.license:cc-by|")
    assert "cc-by-nc-nd" in params["filter"]
