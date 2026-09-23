"""`safe_http.fetch` (W5-T1): download da URL di terzi senza SSRF.

Trasporto e risoluzione DNS finti (`httpx.MockTransport`, resolver a
dizionario): nessuna rete. Oracoli:

- indirizzi interni rifiutati PRIMA di ogni richiesta (loopback, privati,
  link-local e metadati del cloud, CGNAT, IPv6 locali, IPv4 dentro IPv6,
  multicast), anche se uno solo dei record DNS lo è;
- la richiesta va all'IP verificato con `Host` e SNI dell'host originale
  (niente seconda risoluzione: il rebinding non serve);
- redirect ricontrollati (verso un indirizzo interno → rifiutato), tetto
  dei redirect;
- tetto di byte (dichiarato e reale, in streaming), tipo dai primi byte
  (HTML con 200 → rifiutato), 429 recuperabile con `retry_after`;
- schemi, porte, credenziali e host non ammessi.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services import safe_http
from app.services.safe_http import SafeFetchError, fetch, is_public_address, sniff

PDF = b"%PDF-1.7\n" + b"x" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _resolver(table: dict[str, list[str]]) -> Any:
    calls: list[str] = []

    async def resolve(host: str, port: int) -> list[str]:
        calls.append(host)
        if host not in table:
            raise OSError("NXDOMAIN")
        return table[host]

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


def _transport(handler: Any) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped), seen


async def _fetch(url: str, table: dict[str, list[str]], handler: Any, **kw: Any) -> Any:
    transport, seen = _transport(handler)
    kw.setdefault("allowed_kinds", frozenset({"pdf"}))
    result = await fetch(
        url,
        max_bytes=kw.pop("max_bytes", 10_000),
        timeout=5.0,
        resolver=_resolver(table),
        transport=transport,
        **kw,
    )
    return result, seen


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "2002:7f00:1::",
    ],
)
async def test_internal_addresses_are_blocked_before_any_request(address: str) -> None:
    assert not is_public_address(address)
    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch(
            "https://evil.example/a.pdf",
            {"evil.example": [address]},
            lambda r: httpx.Response(200, content=PDF),
        )
    assert excinfo.value.code == "blocked_address"


async def test_one_internal_record_is_enough_to_block() -> None:
    transport, seen = _transport(lambda r: httpx.Response(200, content=PDF))
    with pytest.raises(SafeFetchError) as excinfo:
        await fetch(
            "https://mixed.example/a.pdf",
            max_bytes=10_000,
            timeout=5.0,
            allowed_kinds=frozenset({"pdf"}),
            resolver=_resolver({"mixed.example": ["93.184.216.34", "10.0.0.7"]}),
            transport=transport,
        )
    assert excinfo.value.code == "blocked_address" and seen == []


async def test_literal_internal_ip_in_url_is_blocked() -> None:
    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch("http://169.254.169.254/latest/meta-data", {}, lambda r: httpx.Response(200))
    assert excinfo.value.code == "blocked_address"


async def test_request_goes_to_the_checked_ip_with_host_and_sni() -> None:
    result, seen = await _fetch(
        "https://papers.example/dir/a.pdf?x=1",
        {"papers.example": ["93.184.216.34"]},
        lambda r: httpx.Response(200, content=PDF, headers={"content-type": "text/html"}),
    )
    assert result.kind == "pdf" and result.content == PDF
    (request,) = seen
    assert request.url.host == "93.184.216.34"
    assert request.url.path == "/dir/a.pdf" and request.url.query == b"x=1"
    assert request.headers["host"] == "papers.example"
    assert request.extensions["sni_hostname"] == "papers.example"
    # Il tipo viene dai byte, non dall'header (qui text/html).
    assert result.content_type == "text/html"


async def test_redirect_to_an_internal_address_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.example/secret"})

    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch(
            "https://ok.example/a.pdf",
            {"ok.example": ["93.184.216.34"], "internal.example": ["10.0.0.2"]},
            handler,
        )
    assert excinfo.value.code == "blocked_address"


async def test_relative_redirect_is_followed_and_rechecked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/final.pdf"})
        return httpx.Response(200, content=PDF)

    result, seen = await _fetch(
        "https://ok.example/start", {"ok.example": ["93.184.216.34"]}, handler
    )
    assert result.final_url == "https://ok.example/final.pdf"
    assert [r.url.path for r in seen] == ["/start", "/final.pdf"]


async def test_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch("https://ok.example/", {"ok.example": ["93.184.216.34"]}, handler)
    assert excinfo.value.code == "too_many_redirects"


async def test_size_limits_declared_and_streamed() -> None:
    table = {"ok.example": ["93.184.216.34"]}
    with pytest.raises(SafeFetchError) as declared:
        await _fetch(
            "https://ok.example/a.pdf",
            table,
            lambda r: httpx.Response(200, content=PDF, headers={"content-length": "999999"}),
            max_bytes=100,
        )
    assert declared.value.code == "too_large"

    def streamed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(PDF * 50))

    with pytest.raises(SafeFetchError) as real:
        await _fetch("https://ok.example/a.pdf", table, streamed, max_bytes=500)
    assert real.value.code == "too_large"


async def test_html_landing_page_is_not_a_pdf() -> None:
    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch(
            "https://ok.example/a.pdf",
            {"ok.example": ["93.184.216.34"]},
            lambda r: httpx.Response(200, content=b"<!doctype html><title>Paywall</title>"),
        )
    assert excinfo.value.code == "unexpected_type" and not excinfo.value.recoverable


async def test_rate_limit_is_recoverable_with_retry_after() -> None:
    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch(
            "https://ok.example/a.pdf",
            {"ok.example": ["93.184.216.34"]},
            lambda r: httpx.Response(429, headers={"retry-after": "30"}),
        )
    assert excinfo.value.code == "rate_limited" and excinfo.value.recoverable
    assert excinfo.value.retry_after == 30.0


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://ok.example/a.pdf", "invalid_url"),
        ("file:///etc/passwd", "invalid_url"),
        ("https://ok.example:8443/a.pdf", "invalid_url"),
        ("https://user:pw@ok.example/a.pdf", "invalid_url"),
        ("https://other.example/a.png", "blocked_host"),
    ],
)
async def test_urls_that_are_never_fetched(url: str, code: str) -> None:
    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch(
            url,
            {"ok.example": ["93.184.216.34"], "other.example": ["93.184.216.35"]},
            lambda r: httpx.Response(200, content=PNG),
            allowed_kinds=frozenset({"png"}),
            allowed_hosts=frozenset({"ok.example"}) if "other" in url else None,
        )
    assert excinfo.value.code == code


def test_sniff() -> None:
    assert sniff(PDF) == "pdf" and sniff(PNG) == "png"
    assert sniff(b"\xff\xd8\xff\xe0") == "jpeg" and sniff(b"GIF89a....") == "gif"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff(b'  {"a": 1}') == "json" and sniff(b"<svg/>") is None


def test_module_has_no_follow_redirects_client() -> None:
    """Il client di `fetch` non segue i redirect da solo (li ricontrolla)."""
    import inspect

    assert "follow_redirects=False" in inspect.getsource(safe_http.fetch)
