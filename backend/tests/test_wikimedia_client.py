"""Client di Wikimedia Commons (W5-T4): licenze, attribuzione, host.

- solo licenze libere riconosciute (CC0, pubblico dominio, CC BY, CC BY-SA);
  GFDL, licenze ignote e file con restrizioni (marchi, diritti della
  persona) si saltano: mai una figura esterna con licenza ignota;
- autore e titolo ripuliti dall'HTML; attribuzione congelata con titolo,
  autore, «Wikimedia Commons», licenza e pagina del file, e riga «Fonte»
  calcolata da `figure_attribution` come per le altre figure;
- immagine solo da `upload.wikimedia.org` (anche se l'API restituisse un
  altro host), via `safe_http`, con User-Agent che porta il contatto;
- la ricerca chiede il PNG reso da Commons alla larghezza configurata.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services import wikimedia_client
from app.services.figure_attribution import AttributionSource, attribution_line


def _page(
    page_id: int,
    *,
    license: str = "cc-by-sa-4.0",
    short: str = "CC BY-SA 4.0",
    artist: str = '<a href="//commons.wikimedia.org/wiki/User:Jane">Jane <b>Doe</b></a>',
    host: str = "upload.wikimedia.org",
    restrictions: str = "",
    index: int = 1,
) -> dict[str, Any]:
    return {
        "pageid": page_id,
        "ns": 6,
        "title": f"File:Laser_Doppler_vibrometer_{page_id}.svg",
        "index": index,
        "imageinfo": [
            {
                "url": f"https://{host}/wikipedia/commons/a/ab/LDV_{page_id}.svg",
                "thumburl": f"https://{host}/wikipedia/commons/thumb/a/ab/LDV_{page_id}.svg/2000px-LDV.png",
                "thumbwidth": 2000,
                "thumbheight": 1200,
                "thumbmime": "image/png",
                "mime": "image/svg+xml",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/File:LDV_{page_id}.svg",
                "extmetadata": {
                    "License": {"value": license},
                    "LicenseShortName": {"value": short},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                    "Artist": {"value": artist},
                    "ObjectName": {"value": "Laser Doppler vibrometer &amp; optics"},
                    "ImageDescription": {"value": "<p>Schematic of an <i>LDV</i></p>"},
                    "Restrictions": {"value": restrictions},
                },
            }
        ],
    }


def test_parse_keeps_only_free_licenses_without_restrictions() -> None:
    payload = {
        "query": {
            "pages": [
                _page(2, index=2),
                _page(1, license="cc0", short="CC0", index=1),
                _page(3, license="pd", short="Public domain", index=3),
                _page(4, license="cc-by-4.0", short="CC BY 4.0", index=4),
                _page(5, license="gfdl", short="GFDL", index=5),
                _page(10, license="cc-by-nc-sa-4.0", short="CC BY-NC-SA 4.0", index=10),
                _page(11, license="cc-by-nd-4.0", short="CC BY-ND 4.0", index=11),
                _page(6, license="", short="", index=6),
                _page(7, restrictions="trademarked", index=7),
                _page(8, host="evil.example", index=8),
                {"pageid": 9, "ns": 0, "title": "Not a file"},
            ]
        }
    }
    files = wikimedia_client.parse_files(payload)
    assert [(f.page_id, f.license) for f in files] == [
        (1, "cc0"),
        (2, "cc_by_sa"),
        (3, "public_domain"),
        (4, "cc_by"),
    ]
    first = files[1]
    assert first.author == "Jane Doe"
    assert first.object_name == "Laser Doppler vibrometer & optics"
    assert first.description == "Schematic of an LDV"
    assert first.external_id == "commons:2"
    assert first.image_url.startswith("https://upload.wikimedia.org/")


def test_frozen_attribution_gives_the_source_line() -> None:
    (item,) = wikimedia_client.parse_files({"query": {"pages": [_page(2)]}})
    attribution = item.attribution()
    assert attribution["container"] == "Wikimedia Commons"
    assert attribution["url"].startswith("https://commons.wikimedia.org/wiki/File:")
    src = AttributionSource.from_json(attribution, license=item.license)
    line = attribution_line(src, language="it")
    # Versione della licenza dal nome breve di Commons (CC BY-SA 4.0).
    assert line == (
        "Fonte: Jane Doe, «Laser Doppler vibrometer & optics», Wikimedia Commons (CC BY-SA 4.0)"
    )


async def test_search_and_download_go_through_safe_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config

    patched = config.get_settings().model_copy(
        update={"papers_polite_email": "docente@example.org"}
    )
    monkeypatch.setattr(wikimedia_client, "get_settings", lambda: patched)
    seen: list[httpx.Request] = []
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers["host"] == "commons.wikimedia.org":
            return httpx.Response(200, content=json.dumps({"query": {"pages": [_page(2)]}}))
        return httpx.Response(200, content=png)

    async def resolve(host: str, port: int) -> list[str]:
        return {
            "commons.wikimedia.org": ["198.35.26.96"],
            "upload.wikimedia.org": ["198.35.26.112"],
        }[host]

    fetch_kwargs = {"transport": httpx.MockTransport(handler), "resolver": resolve}
    files = await wikimedia_client.search_files(
        "laser doppler vibrometer", limit=5, language="it", fetch_kwargs=fetch_kwargs
    )
    got = await wikimedia_client.download_image(files[0], fetch_kwargs=fetch_kwargs)
    assert got.kind == "png"
    api, image = seen
    params = dict(api.url.params)
    assert params["gsrsearch"] == "laser doppler vibrometer filetype:bitmap|drawing"
    assert params["iiurlwidth"] == str(patched.figure_literature_image_width)
    assert params["gsrnamespace"] == "6"
    assert "mailto:docente@example.org" in api.headers["user-agent"]
    assert image.headers["host"] == "upload.wikimedia.org"


async def test_image_from_another_host_is_never_downloaded() -> None:
    (item,) = wikimedia_client.parse_files({"query": {"pages": [_page(2)]}})
    forged = item.__class__(**{**item.__dict__, "image_url": "https://evil.example/x.png"})
    from app.services.safe_http import SafeFetchError

    with pytest.raises(SafeFetchError) as excinfo:
        await wikimedia_client.download_image(forged)
    assert excinfo.value.code == "blocked_host"
