"""Download sicuro da URL esterni (letteratura aperta, import dei paper).

Gli URL arrivano da API di terzi (Wikimedia Commons, OpenAlex) o dal
client: senza difese un URL verso la rete interna (metadati del cloud,
servizi su localhost) diventa una SSRF. Difese, tutte a guasto chiuso:

- solo `http`/`https` sulle porte 80/443, niente credenziali nell'URL;
  facoltativo un elenco di host ammessi;
- l'host si risolve qui e ogni indirizzo deve essere pubblico (niente
  loopback, reti private, link-local, CGNAT, multicast, riservati, anche
  se scritti come IPv4 dentro IPv6); la connessione va all'indirizzo
  appena verificato, con `Host` e SNI dell'host originale, così un DNS che
  cambia risposta fra controllo e connessione (rebinding) non serve;
- redirect seguiti a mano (al più `max_redirects`), ognuno ricontrollato;
- risposta letta in streaming con tetto di byte e scadenza totale;
- tipo del contenuto deciso dai primi byte (sniffing), mai dall'header.

`fetch` solleva `SafeFetchError(code)`; `recoverable` distingue gli errori
per cui ha senso riprovare (rete, 429, 5xx) da quelli definitivi.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.logging import get_logger

log = get_logger("app.safe_http")

Resolver = Callable[[str, int], Awaitable[list[str]]]

ALLOWED_PORTS = frozenset({80, 443})
_RECOVERABLE = frozenset({"network", "timeout", "rate_limited", "server_error", "dns_failed"})
_CHUNK = 64 * 1024


class SafeFetchError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after

    @property
    def recoverable(self) -> bool:
        return self.code in _RECOVERABLE

    def __str__(self) -> str:
        return f"[{self.code}] {self.args[0]}"


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    kind: str
    final_url: str
    status: int
    content_type: str | None


def is_public_address(ip: str) -> bool:
    """True solo per un indirizzo instradabile su Internet."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = addr.ipv4_mapped or addr.sixtofour
        if embedded is not None:
            addr = embedded
        elif addr.teredo is not None:
            return False
    return bool(addr.is_global) and not addr.is_multicast


def sniff(data: bytes) -> str | None:
    """Tipo del contenuto dai primi byte: pdf, png, jpeg, gif, webp, json."""
    head = data[:1024]
    if b"%PDF-" in head:
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if head.lstrip()[:1] in (b"{", b"["):
        return "json"
    return None


async def _system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _check_url(url: str, allowed_hosts: frozenset[str] | None) -> tuple[str, str, int]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise SafeFetchError("invalid_url", f"URL non valido: {exc}") from exc
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if scheme not in ("http", "https") or not host:
        raise SafeFetchError("invalid_url", "solo URL http/https con un host")
    if parts.username or parts.password:
        raise SafeFetchError("invalid_url", "credenziali nell'URL non ammesse")
    port = port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise SafeFetchError("invalid_url", f"porta {port} non ammessa")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise SafeFetchError("blocked_host", f"host {host} non ammesso")
    return scheme, host, port


async def _pinned_address(host: str, port: int, resolver: Resolver) -> str:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = await resolver(host, port)
        except OSError as exc:
            raise SafeFetchError("dns_failed", f"risoluzione di {host} fallita") from exc
    if not addresses:
        raise SafeFetchError("dns_failed", f"nessun indirizzo per {host}")
    blocked = [a for a in addresses if not is_public_address(a)]
    if blocked:
        # Anche un solo indirizzo interno basta a rifiutare l'host.
        raise SafeFetchError("blocked_address", f"{host} risolve in un indirizzo non pubblico")
    return addresses[0]


def _pinned_url(url: str, scheme: str, address: str, port: int) -> str:
    parts = urlsplit(url)
    ip_host = f"[{address}]" if ":" in address else address
    netloc = ip_host if port == (443 if scheme == "https" else 80) else f"{ip_host}:{port}"
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{netloc}{path}{query}"


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


async def fetch(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    allowed_kinds: frozenset[str],
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    allowed_hosts: frozenset[str] | None = None,
    max_redirects: int = 3,
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    """Scarica `url` con le difese del modulo; il contenuto deve essere di
    uno dei tipi `allowed_kinds` (vedi :func:`sniff`)."""
    resolve = resolver or _system_resolver
    deadline = time.monotonic() + timeout
    current = url
    query = dict(params or {})
    async with httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        timeout=httpx.Timeout(min(timeout, 30.0)),
    ) as client:
        for _hop in range(max_redirects + 1):
            scheme, host, port = _check_url(current, allowed_hosts)
            address = await _pinned_address(host, port, resolve)
            request_headers = {
                # Niente compressione: il tetto vale sui byte veri (e una
                # risposta compressa comunque arriva decompressa a pezzi).
                "Accept-Encoding": "identity",
                **(headers or {}),
                "Host": urlsplit(current).netloc.split("@")[-1],
            }
            request = client.build_request(
                "GET",
                _pinned_url(current, scheme, address, port),
                headers=request_headers,
                params=query or None,
            )
            if scheme == "https":
                request.extensions["sni_hostname"] = host
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SafeFetchError("timeout", "tempo massimo del download superato")
            try:
                async with asyncio.timeout(remaining):
                    response = await client.send(request, stream=True)
                    try:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                raise SafeFetchError(
                                    "http_error", "redirect senza destinazione", status=302
                                )
                            current = urljoin(current, location)
                            query = {}
                            continue
                        if response.status_code == 429:
                            raise SafeFetchError(
                                "rate_limited",
                                "troppe richieste (429)",
                                status=429,
                                retry_after=_retry_after(response),
                            )
                        if response.status_code >= 500:
                            raise SafeFetchError(
                                "server_error",
                                f"HTTP {response.status_code}",
                                status=response.status_code,
                            )
                        if response.status_code >= 400:
                            raise SafeFetchError(
                                "http_error",
                                f"HTTP {response.status_code}",
                                status=response.status_code,
                            )
                        declared = response.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > max_bytes:
                            raise SafeFetchError("too_large", f"{declared} byte > {max_bytes}")
                        buffer = bytearray()
                        async for chunk in response.aiter_bytes(_CHUNK):
                            buffer.extend(chunk)
                            if len(buffer) > max_bytes:
                                raise SafeFetchError("too_large", f"oltre {max_bytes} byte")
                    finally:
                        await response.aclose()
            except TimeoutError as exc:
                raise SafeFetchError("timeout", "tempo massimo del download superato") from exc
            except httpx.HTTPError as exc:
                raise SafeFetchError("network", f"errore di rete: {exc}") from exc
            data = bytes(buffer)
            kind = sniff(data)
            if kind not in allowed_kinds:
                raise SafeFetchError(
                    "unexpected_type",
                    f"contenuto di tipo {kind or 'sconosciuto'}, attesi {sorted(allowed_kinds)}",
                )
            return FetchResult(
                content=data,
                kind=kind,
                final_url=current,
                status=response.status_code,
                content_type=response.headers.get("content-type"),
            )
    raise SafeFetchError("too_many_redirects", f"più di {max_redirects} redirect")
