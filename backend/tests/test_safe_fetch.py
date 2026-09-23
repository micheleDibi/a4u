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


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate"])
async def test_compressed_responses_are_refused(encoding: str) -> None:
    """Una bomba gzip/brotli si decomprimerebbe prima del tetto di byte."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-encoding": encoding}, stream=httpx.ByteStream(b"\x1f\x8b")
        )

    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch("https://ok.example/a.pdf", {"ok.example": ["93.184.216.34"]}, handler)
    assert excinfo.value.code == "unexpected_encoding"


@pytest.mark.parametrize(
    "address", ["64:ff9b::7f00:1", "::a9fe:a9fe", "::ffff:0:7f00:1", "fec0::1", "64:ff9b:1::1"]
)
def test_embedded_or_site_local_ipv6_is_not_public(address: str) -> None:
    assert not is_public_address(address)


async def test_idn_host_is_sent_as_punycode_and_bad_labels_are_invalid() -> None:
    result, seen = await _fetch(
        "https://müller.example/a.pdf",
        {"xn--mller-kva.example": ["93.184.216.34"]},
        lambda r: httpx.Response(200, content=PDF),
    )
    assert result.kind == "pdf" and seen[0].headers["host"] == "xn--mller-kva.example"
    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch(f"https://{'a' * 70}.example/x.pdf", {}, lambda r: httpx.Response(200))
    assert excinfo.value.code in ("invalid_url", "dns_failed")


async def test_dns_is_inside_the_total_deadline() -> None:
    import asyncio
    import time

    async def slow(host: str, port: int) -> list[str]:
        await asyncio.sleep(3)
        return ["93.184.216.34"]

    transport, _seen = _transport(lambda r: httpx.Response(200, content=PDF))
    started = time.monotonic()
    with pytest.raises(SafeFetchError) as excinfo:
        await fetch(
            "https://slow.example/a.pdf",
            max_bytes=10_000,
            timeout=0.5,
            allowed_kinds=frozenset({"pdf"}),
            resolver=slow,
            transport=transport,
        )
    assert excinfo.value.code == "timeout" and time.monotonic() - started < 2


async def test_caller_headers_do_not_follow_a_redirect_to_another_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "ok.example":
            return httpx.Response(302, headers={"location": "https://other.example/b.pdf"})
        return httpx.Response(200, content=PDF)

    _result, seen = await _fetch(
        "https://ok.example/a.pdf",
        {"ok.example": ["93.184.216.34"], "other.example": ["93.184.216.35"]},
        handler,
        headers={"User-Agent": "a4u-test", "X-Secret": "s3cr3t"},
    )
    first, second = seen
    assert first.headers["x-secret"] == "s3cr3t"
    assert "x-secret" not in second.headers and second.headers["user-agent"] == "a4u-test"


async def test_allowed_hosts_are_rechecked_on_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://cdn.other.example/x.png"})

    with pytest.raises(SafeFetchError) as excinfo:
        await _fetch(
            "https://upload.wikimedia.org/x.png",
            {"upload.wikimedia.org": ["198.35.26.112"], "cdn.other.example": ["93.184.216.34"]},
            handler,
            allowed_kinds=frozenset({"png"}),
            allowed_hosts=frozenset({"upload.wikimedia.org"}),
        )
    assert excinfo.value.code == "blocked_host"


async def test_wrong_kind_and_server_errors() -> None:
    table = {"ok.example": ["93.184.216.34"]}
    with pytest.raises(SafeFetchError) as kind:
        await _fetch("https://ok.example/a.pdf", table, lambda r: httpx.Response(200, content=PNG))
    assert kind.value.code == "unexpected_type"
    with pytest.raises(SafeFetchError) as server:
        await _fetch("https://ok.example/a.pdf", table, lambda r: httpx.Response(503))
    assert server.value.code == "server_error" and server.value.recoverable
