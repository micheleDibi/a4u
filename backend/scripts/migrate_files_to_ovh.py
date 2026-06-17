"""Migrazione one-shot dei file locali → server OVH (FTP).

Mirrora l'albero locale (`upload_root` + `generated_pdfs_dir`) sullo storage
OVH, preservando la stessa struttura di key. **Solo upload**: non riscrive il
DB (i path logici nelle colonne restano invariati e si risolvono sul nuovo
backend grazie al layer `remote_storage`).

Esclude i file transitori/cache che restano sul filesystem locale:
  - `lesson_audio/...`            (cache TTS)
  - `musetalk_manifests/...`      (cache preprocessing MuseTalk)
  - `*/clips_musetalk_*/...`      (clip avatar pre-processate per MuseTalk)
  - `*/.tmp_work_*` `*/.tmp_avatar_work_*`  (work dir ffmpeg)
  - file temporanei `*.part-*`

Idempotente: salta i file già presenti su OVH con la stessa dimensione
(usa `--force` per ri-caricare). `--dry-run` non tocca nulla. `--verify`
ricontrolla a fine corsa che ogni file locale abbia il corrispettivo remoto
con la stessa dimensione.

Uso (dalla cartella `backend/`, con le OVH_FTP_* valorizzate in `.env`):

    python -m scripts.migrate_files_to_ovh --dry-run
    python -m scripts.migrate_files_to_ovh
    python -m scripts.migrate_files_to_ovh --verify
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.config import get_settings
from app.services import remote_storage

# Parti di path che identificano contenuto transitorio/cache (mai migrato).
_EXCLUDE_EXACT = {"lesson_audio", "musetalk_manifests"}


def _is_excluded(parts: tuple[str, ...]) -> bool:
    for p in parts:
        if p in _EXCLUDE_EXACT:
            return True
        if p.startswith((".tmp_work_", ".tmp_avatar_work_", "clips_musetalk_")):
            return True
        if ".part-" in p:
            return True
    return False


def _generated_pdfs_root() -> Path:
    settings = get_settings()
    p = Path(settings.generated_pdfs_dir)
    return p if p.is_absolute() else (Path.cwd() / p).resolve()


def _iter_files(root: Path, key_prefix: str):
    """Genera (key, local_path) per ogni file sotto `root`, escludendo i
    transitori. `key_prefix` è `uploads` o `generated_pdfs`."""
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _is_excluded(rel.parts):
            continue
        key = f"{key_prefix}/" + "/".join(rel.parts)
        yield key, path


def main() -> int:
    parser = argparse.ArgumentParser(description="Migra i file locali su OVH.")
    parser.add_argument("--dry-run", action="store_true", help="Non carica nulla.")
    parser.add_argument(
        "--force", action="store_true", help="Ri-carica anche i file già presenti."
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="A fine corsa, verifica che le dimensioni remote combacino.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.storage_backend != "ovh_ftp":
        print(
            "[!] STORAGE_BACKEND non è 'ovh_ftp' nel .env. La migrazione usa "
            "comunque il backend OVH (forzato) per la destinazione.",
            file=sys.stderr,
        )
    # Destinazione: backend OVH "puro" (niente fallback locale).
    storage = remote_storage._build_ovh_storage()

    roots = [
        (settings.upload_root, "uploads"),
        (_generated_pdfs_root(), "generated_pdfs"),
    ]

    uploaded = skipped = failed = total = 0
    bytes_up = 0
    for root, prefix in roots:
        print(f"== Sorgente: {root}  →  key '{prefix}/...'")
        for key, path in _iter_files(root, prefix):
            total += 1
            local_size = path.stat().st_size
            try:
                remote_size = None if args.force else storage.size(key)
                if remote_size == local_size:
                    skipped += 1
                    continue
                if args.dry_run:
                    print(f"  [dry-run] UPLOAD {key} ({local_size} B)")
                    uploaded += 1
                    continue
                storage.upload_file(key, path)
                uploaded += 1
                bytes_up += local_size
                print(f"  ok  {key} ({local_size} B)")
            except remote_storage.StorageError as exc:
                failed += 1
                print(f"  ERR {key}: {exc}", file=sys.stderr)

    print(
        f"\nTotale file: {total} | caricati: {uploaded} | saltati: {skipped} "
        f"| falliti: {failed} | byte: {bytes_up}"
    )

    if args.verify and not args.dry_run:
        print("\n== Verifica dimensioni remote ==")
        mismatch = 0
        for root, prefix in roots:
            for key, path in _iter_files(root, prefix):
                remote_size = storage.size(key)
                local_size = path.stat().st_size
                if remote_size != local_size:
                    mismatch += 1
                    print(
                        f"  MISMATCH {key}: locale={local_size} remoto={remote_size}",
                        file=sys.stderr,
                    )
        print("Verifica OK." if mismatch == 0 else f"Verifica: {mismatch} discrepanze.")
        if mismatch:
            return 2

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
