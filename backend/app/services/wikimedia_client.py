"""Client di Wikimedia Commons per le figure della letteratura aperta (WP5).

- `search_files(query)`: file di Commons (namespace 6) che corrispondono
  alla ricerca, con i metadati di licenza e attribuzione (`extmetadata`) e
  l'URL del PNG/JPEG reso da Commons alla larghezza chiesta (anche per gli
  SVG: il vettoriale lo rasterizza Commons, niente SVG salvati da noi);
- `download_image(file)`: scarica quel rendering, solo da
  `upload.wikimedia.org`, con `safe_http`.

Tengo solo i file con una licenza libera riconosciuta (CC0, pubblico
dominio, CC BY, CC BY-SA) e senza restrizioni aggiuntive (marchi, diritti
della persona): un file con licenza non riconosciuta si salta, mai una
figura con licenza ignota da una fonte esterna. L'attribuzione (autore,
titolo, licenza, pagina del file) viene dai metadati, ripuliti dall'HTML.

User-Agent con contatto come chiede la policy di Wikimedia
(`PAPERS_POLITE_EMAIL`); i 429 diventano `SafeFetchError("rate_limited")`,
recuperabile.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import safe_http

log = get_logger("app.wikimedia")

IMAGE_HOSTS = frozenset({"upload.wikimedia.org"})
IMAGE_KINDS = frozenset({"png", "jpeg", "gif", "webp"})
_MAX_API_BYTES = 4 * 1024 * 1024
_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"\s+")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")
_EXTMETADATA_FIELDS = (
    "LicenseShortName",
    "License",
    "LicenseUrl",
    "UsageTerms",
    "AttributionRequired",
    "Artist",
    "Attribution",
    "Credit",
    "ObjectName",
    "ImageDescription",
    "Restrictions",
)


@dataclass(frozen=True)
class CommonsFile:
    page_id: int
    title: str
    description_url: str
    image_url: str
    mime: str
    width: int | None
    height: int | None
    license: str
    license_url: str | None
    author: str | None
    object_name: str | None
    description: str | None
    # Attribuzione nella forma voluta dal licenziante (campo `Attribution`
    # di Commons), quando c'è: sostituisce l'autore nella riga «Fonte».
    credit: str | None = None
    license_version: str | None = None

    @property
    def external_id(self) -> str:
        return f"commons:{self.page_id}"

    def attribution(self) -> dict[str, Any]:
        """Attribuzione congelata (forma di `AttributionSource.to_json`)."""
        data: dict[str, Any] = {
            "title": self.object_name or _file_label(self.title),
            "container": "Wikimedia Commons",
            "license": self.license,
            "license_version": self.license_version,
            "license_url": self.license_url,
            "url": self.description_url,
        }
        if self.credit:
            data["credit"] = self.credit
        elif self.author:
            data["authors"] = [self.author]
        return {k: v for k, v in data.items() if v}


def plain_text(value: Any, *, limit: int = 300) -> str | None:
    """Testo di un campo `extmetadata` (spesso HTML): senza tag né spazi
    doppi, tagliato a `limit`."""
    if not isinstance(value, str):
        return None
    text = _SPACES_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _file_label(title: str) -> str:
    name = title.split(":", 1)[1] if ":" in title else title
    return _EXT_RE.sub("", name).replace("_", " ").strip()


def license_code(meta: dict[str, Any]) -> str | None:
    """Licenza di Commons → codice della figura, None se non riconosciuta
    come libera (il file si salta)."""
    raw = str(_value(meta, "License") or "").strip().lower()
    short = str(_value(meta, "LicenseShortName") or "").strip().lower()
    if raw.startswith("cc0") or short.startswith("cc0"):
        return "cc0"
    if raw in ("pd", "public domain") or raw.startswith("pd-") or short == "public domain":
        return "public_domain"
    if re.match(r"^cc-by-sa-\d", raw):
        return "cc_by_sa"
    if re.match(r"^cc-by-\d", raw):
        return "cc_by"
    return None


_VERSION_RE = re.compile(r"\b(\d+\.\d+)\b")


def _license_version(short_name: Any) -> str | None:
    """Versione della licenza dal nome breve di Commons («CC BY-SA 4.0»)."""
    match = _VERSION_RE.search(str(short_name or ""))
    return match.group(1) if match else None


def _http_url(value: str | None) -> str | None:
    """Solo URL http/https (finiscono come link nel PDF)."""
    if value and value.startswith("//"):
        value = "https:" + value
    return value if value and value.startswith(("https://", "http://")) else None


def _value(meta: dict[str, Any], key: str) -> Any:
    entry = meta.get(key)
    return entry.get("value") if isinstance(entry, dict) else None


def _user_agent() -> str:
    email = (get_settings().papers_polite_email or "").strip()
    contact = f"; mailto:{email}" if email else ""
    return f"a4u/1.0 (Avatar4University, figure di fonte{contact})"


def parse_files(payload: Any) -> list[CommonsFile]:
    """File utilizzabili dalla risposta `query` (formatversion=2)."""
    pages = (payload.get("query") or {}).get("pages") if isinstance(payload, dict) else None
    out: list[tuple[int, CommonsFile]] = []
    for page in pages or []:
        if not isinstance(page, dict) or page.get("ns") != 6:
            continue
        infos = page.get("imageinfo") or []
        info = infos[0] if infos and isinstance(infos[0], dict) else None
        if info is None:
            continue
        meta = info.get("extmetadata") or {}
        code = license_code(meta)
        if code is None:
            continue
        if plain_text(_value(meta, "Restrictions")):
            # Marchi, diritti della persona, ecc.: fuori.
            continue
        image_url = info.get("thumburl") or info.get("url")
        description_url = info.get("descriptionurl")
        if not isinstance(image_url, str) or not isinstance(description_url, str):
            continue
        if (urlsplit(image_url).hostname or "") not in IMAGE_HOSTS:
            continue
        mime = str(info.get("thumbmime") or info.get("mime") or "")
        width = info.get("thumbwidth") or info.get("width")
        height = info.get("thumbheight") or info.get("height")
        out.append(
            (
                int(page.get("index") or 0),
                CommonsFile(
                    page_id=int(page.get("pageid") or 0),
                    title=str(page.get("title") or ""),
                    description_url=description_url,
                    image_url=image_url,
                    mime=mime,
                    width=width if isinstance(width, int) else None,
                    height=height if isinstance(height, int) else None,
                    license=code,
                    license_url=_http_url(plain_text(_value(meta, "LicenseUrl"), limit=500)),
                    author=plain_text(_value(meta, "Artist"), limit=200),
                    credit=plain_text(_value(meta, "Attribution"), limit=200),
                    license_version=_license_version(_value(meta, "LicenseShortName")),
                    object_name=plain_text(_value(meta, "ObjectName"), limit=300),
                    description=plain_text(_value(meta, "ImageDescription"), limit=600),
                ),
            )
        )
    out.sort(key=lambda item: item[0])
    return [f for _index, f in out if f.page_id > 0]


async def search_files(
    query: str,
    *,
    limit: int,
    language: str,
    fetch_kwargs: dict[str, Any] | None = None,
) -> list[CommonsFile]:
    """Cerca su Commons disegni e immagini per `query`."""
    settings = get_settings()
    api = settings.wikimedia_api_url
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap|drawing",
        "gsrnamespace": "6",
        "gsrlimit": str(max(1, min(limit, 20))),
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": str(int(settings.figure_literature_image_width)),
        "iiextmetadatafilter": "|".join(_EXTMETADATA_FIELDS),
        "iiextmetadatalanguage": language,
    }
    result = await safe_http.fetch(
        api,
        params=params,
        max_bytes=_MAX_API_BYTES,
        timeout=30.0,
        allowed_kinds=frozenset({"json"}),
        allowed_hosts=frozenset({(urlsplit(api).hostname or "").lower()}),
        headers={"User-Agent": _user_agent(), "Accept": "application/json"},
        **(fetch_kwargs or {}),
    )
    import json

    try:
        payload = json.loads(result.content)
    except ValueError as exc:
        raise safe_http.SafeFetchError("invalid_response", "risposta non JSON") from exc
    files = parse_files(payload)
    log.info("wikimedia_search", query=query[:100], files=len(files))
    return files


async def download_image(
    file: CommonsFile, *, fetch_kwargs: dict[str, Any] | None = None
) -> safe_http.FetchResult:
    settings = get_settings()
    return await safe_http.fetch(
        file.image_url,
        max_bytes=int(settings.figure_literature_max_image_mb) * 1024 * 1024,
        timeout=60.0,
        allowed_kinds=IMAGE_KINDS,
        allowed_hosts=IMAGE_HOSTS,
        headers={"User-Agent": _user_agent()},
        **(fetch_kwargs or {}),
    )
