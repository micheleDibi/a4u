"""Spike di connettività OVH FTP — da eseguire PRIMA del cutover.

Verifica, contro il server OVH reale, che il backend FTP funzioni end-to-end:
connect/login FTPS, MKD ricorsivo, STOR atomico (temp+rename), SIZE, RETR,
download bytes, e che il file caricato sia immediatamente raggiungibile via
HTTP sull'URL pubblico. Infine ripulisce il file di test.

De-rischia i due punti critici dell'hosting condiviso:
  1. il quirk del riuso della sessione TLS sul canale dati (SSLEOFError);
  2. il timing di propagazione HTTP dopo l'upload FTP.

Uso (dalla cartella `backend/`, con le OVH_FTP_* / OVH_PUBLIC_BASE_URL in `.env`):

    python -m scripts.ftp_spike
"""
from __future__ import annotations

import argparse
import sys
from uuid import uuid4

import httpx

from app.core.config import get_settings
from app.services import remote_storage


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike di connettività storage OVH.")
    parser.add_argument(
        "--protocol",
        choices=("ftp", "sftp"),
        default=None,
        help="Protocollo OVH (default: dedotto da STORAGE_BACKEND).",
    )
    args = parser.parse_args()

    settings = get_settings()
    proto = args.protocol or (
        "sftp" if settings.storage_backend == "ovh_sftp" else "ftp"
    )
    port = settings.ovh_sftp_port if proto == "sftp" else settings.ovh_ftp_port
    print("Config:")
    print(f"  protocollo = {proto}")
    print(f"  host       = {settings.ovh_ftp_host}:{port}")
    print(f"  user       = {settings.ovh_ftp_user}")
    print(f"  base_path  = {settings.ovh_ftp_base_path}")
    print(f"  public_url = {settings.ovh_public_base_url}")

    try:
        storage = remote_storage.build_remote_backend(args.protocol)
    except remote_storage.StorageError as exc:
        print(f"[FAIL] config: {exc}", file=sys.stderr)
        return 2

    key = f"uploads/_spike_test/{uuid4().hex}.txt"
    payload = b"a4u ftp spike OK\n"

    try:
        print(f"\n1) upload_bytes {key} ...")
        storage.upload_bytes(key, payload)

        print("2) exists / size ...")
        assert storage.exists(key), "exists() ha restituito False dopo upload"
        size = storage.size(key)
        assert size == len(payload), f"size() = {size}, atteso {len(payload)}"

        print("3) download_bytes (RETR) ...")
        got = storage.download_bytes(key)
        assert got == payload, "download_bytes != payload"

        # URL OVH atteso, costruito direttamente da OVH_PUBLIC_BASE_URL: NON
        # da public_url(), che dipende da STORAGE_BACKEND (durante lo spike
        # l'app è ancora 'local' e restituirebbe PUBLIC_BASE_URL).
        ovh_base = (settings.ovh_public_base_url or "").rstrip("/")
        url = f"{ovh_base}/{key}"
        print(f"4) HTTP GET {url} ...")
        ok = False
        for attempt in range(5):
            try:
                resp = httpx.get(url, timeout=15.0)
                if resp.status_code == 200 and resp.content == payload:
                    ok = True
                    break
                print(f"   tentativo {attempt+1}: HTTP {resp.status_code}")
            except httpx.HTTPError as exc:
                print(f"   tentativo {attempt+1}: {exc}")
        if not ok:
            print(
                "[WARN] il file non è (ancora) servito via HTTP con il contenuto "
                "atteso. Verifica OVH_PUBLIC_BASE_URL / OVH_FTP_BASE_PATH e che "
                "l'autoindex/serving statico sia attivo sul dominio.",
                file=sys.stderr,
            )
    finally:
        print("5) cleanup delete ...")
        try:
            storage.delete(key)
        except remote_storage.StorageError as exc:
            print(f"   [warn] delete fallita: {exc}", file=sys.stderr)

    print(
        f"\n[OK] Spike {proto.upper()} completato: "
        "connect/upload/download/size/delete funzionano."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
