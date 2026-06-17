"""Astrazione di storage file (locale ⇄ OVH via FTP).

Tutto l'I/O persistente dei file dell'app passa da qui. Il DB salva un
*path logico opaco* (es. ``/uploads/courses/...`` o ``lesson_videos/...`` o,
per i PDF, ``{org}/{course}/{lesson}.pdf``); questo modulo lo mappa a una
**key** namespaced che rispecchia il layout su disco e la risolve sul backend
attivo:

- ``uploads/<rel>``        → upload utente + media (immagini, audio, video).
- ``generated_pdfs/<rel>`` → PDF generati (dispense, slide, discorso).

Backend selezionabile via ``settings.storage_backend``:

- ``local``   → filesystem locale (dev / fallback). Riproduce il comportamento
  storico byte-per-byte.
- ``ovh_ftp`` → server OVH via FTP/FTPS. I file sono poi serviti pubblicamente
  via HTTP da ``settings.ovh_public_base_url``.

Le *scritture* e le *cancellazioni* vanno sempre sul backend primario; le
*letture lato server* (merge/zip PDF, estrazione documenti, embed immagini,
input pipeline video) usano FTP RETR — deterministico, bypassa la cache HTTP.
Le letture del browser/servizi esterni usano invece l'URL pubblico
(``media_url`` / ``public_url``).
"""
from __future__ import annotations

import ftplib
import io
import shutil
import socket
import ssl
import stat as stat_module
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Protocol, TypeVar
from uuid import uuid4

try:  # opzionale: serve solo con storage_backend=ovh_sftp
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None  # type: ignore[assignment]

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.storage")

T = TypeVar("T")

# Namespace di primo livello validi per una key.
_NAMESPACES = {"uploads", "generated_pdfs"}


# ---------------------------------------------------------------------------
# Eccezioni
# ---------------------------------------------------------------------------


class StorageError(Exception):
    """Errore generico del layer di storage (I/O, rete, config)."""


class StorageFileNotFound(StorageError):
    """Il file richiesto non esiste sul backend (e nemmeno sul fallback)."""


# ---------------------------------------------------------------------------
# Key helpers — mappano i path logici del DB a key namespaced
# ---------------------------------------------------------------------------


def _normalize_segments(rel: str) -> str:
    """Normalizza separatori e rifiuta traversal (`..`)."""
    rel = rel.replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p]
    for p in parts:
        if p in {".", ".."}:
            raise StorageError(f"Path non consentito: {rel!r}")
    return "/".join(parts)


def uploads_key(db_path: str) -> str:
    """Key per un file di upload, tollerante alle convenzioni di prefisso
    incoerenti nelle colonne DB (`/uploads/courses/...` con prefisso vs
    `lesson_videos/...` senza)."""
    rel = _normalize_segments(db_path)
    if rel.startswith("uploads/"):
        rel = rel[len("uploads/") :]
    elif rel == "uploads":
        rel = ""
    return f"uploads/{rel}" if rel else "uploads"


def pdf_key(rel: str) -> str:
    """Key per un PDF generato (relativo a ``generated_pdfs_dir``)."""
    r = _normalize_segments(rel)
    if r.startswith("generated_pdfs/"):
        r = r[len("generated_pdfs/") :]
    return f"generated_pdfs/{r}" if r else "generated_pdfs"


def _split_key(key: str) -> tuple[str, str]:
    ns, _, rel = _normalize_segments(key).partition("/")
    if ns not in _NAMESPACES:
        raise StorageError(f"Namespace key non valido: {ns!r}")
    return ns, rel


# ---------------------------------------------------------------------------
# URL pubblici (browser + servizi esterni)
# ---------------------------------------------------------------------------


def _served_path(key: str) -> str:
    """Path same-origin servito in locale (mount `/uploads`). Per le key
    `generated_pdfs/*` ritorna comunque `/generated_pdfs/...` (non montato:
    usato solo come componente per `public_url`, non per il browser)."""
    return "/" + _normalize_segments(key)


def media_url(key: str) -> str:
    """URL che il browser deve usare per il file.

    - backend remoto (``ovh_ftp``/``ovh_sftp``) → URL assoluto OVH
      (``{ovh_public_base_url}/{key}``).
    - ``local`` → path relativo same-origin (``/uploads/...``), come oggi.
    """
    settings = get_settings()
    if settings.storage_backend in ("ovh_ftp", "ovh_sftp"):
        base = (settings.ovh_public_base_url or "").rstrip("/")
        return f"{base}/{_normalize_segments(key)}"
    return _served_path(key)


def public_url(key: str) -> str:
    """URL assoluto raggiungibile da servizi esterni (MiniMax, RunPod).

    Con backend remoto coincide con :func:`media_url`. In ``local`` antepone
    ``public_base_url`` al path servito (comportamento storico)."""
    settings = get_settings()
    if settings.storage_backend in ("ovh_ftp", "ovh_sftp"):
        return media_url(key)
    base = settings.public_base_url.rstrip("/")
    return f"{base}{_served_path(key)}"


# ---------------------------------------------------------------------------
# Protocollo
# ---------------------------------------------------------------------------


class Storage(Protocol):
    def upload_bytes(self, key: str, data: bytes) -> None: ...
    def upload_file(self, key: str, local_path: Path) -> None: ...
    def download_bytes(self, key: str) -> bytes: ...
    def download_to(self, key: str, dest_path: Path) -> None: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int | None: ...


# ---------------------------------------------------------------------------
# Backend locale (filesystem)
# ---------------------------------------------------------------------------


def _local_root_and_rel(key: str) -> tuple[Path, str]:
    ns, rel = _split_key(key)
    settings = get_settings()
    if ns == "uploads":
        root = settings.upload_root
    else:  # generated_pdfs
        p = Path(settings.generated_pdfs_dir)
        root = p if p.is_absolute() else (Path.cwd() / p).resolve()
    return root, rel


def _local_path(key: str) -> Path:
    root, rel = _local_root_and_rel(key)
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise StorageError(f"Path fuori dalla root: {key!r}") from exc
    return target


class LocalStorage:
    """Filesystem locale. Equivalente al comportamento storico."""

    def upload_bytes(self, key: str, data: bytes) -> None:
        target = _local_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        log.info("storage_local_write", key=key, size=len(data))

    def upload_file(self, key: str, local_path: Path) -> None:
        target = _local_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if Path(local_path).resolve() == target:
            return  # già nel posto giusto (compat con scritture dirette)
        shutil.copyfile(local_path, target)
        log.info("storage_local_copy", key=key)

    def download_bytes(self, key: str) -> bytes:
        target = _local_path(key)
        if not target.is_file():
            raise StorageFileNotFound(key)
        return target.read_bytes()

    def download_to(self, key: str, dest_path: Path) -> None:
        target = _local_path(key)
        if not target.is_file():
            raise StorageFileNotFound(key)
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, dest_path)

    def delete(self, key: str) -> None:
        target = _local_path(key)
        if target.exists():
            try:
                target.unlink()
                log.info("storage_local_delete", key=key)
            except OSError as exc:  # pragma: no cover
                log.warning("storage_local_delete_failed", key=key, error=str(exc))

    def delete_prefix(self, prefix: str) -> None:
        target = _local_path(prefix)
        if target.exists() and target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            log.info("storage_local_delete_prefix", prefix=prefix)

    def exists(self, key: str) -> bool:
        return _local_path(key).is_file()

    def size(self, key: str) -> int | None:
        p = _local_path(key)
        return p.stat().st_size if p.is_file() else None


# ---------------------------------------------------------------------------
# Backend OVH (FTP / FTPS)
# ---------------------------------------------------------------------------


class _ReusedSslFTP_TLS(ftplib.FTP_TLS):
    """`FTP_TLS` che riusa la sessione TLS del canale di controllo sul canale
    dati. Molti server (vsftpd/ProFTPD, tipici su OVH) lo richiedono con
    *"data connection requires TLS session reuse"*; senza, il trasferimento
    fallisce con ``SSLEOFError``."""

    def ntransfercmd(self, cmd, rest=None):  # type: ignore[override]
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:  # type: ignore[attr-defined]
            session = getattr(self.sock, "session", None)
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=session,
            )
        return conn, size


# Eccezioni considerate transitorie → retry con backoff.
_RETRYABLE = (
    ftplib.error_temp,
    ftplib.error_proto,
    socket.timeout,
    ssl.SSLError,
    ConnectionError,
    EOFError,
)
_BACKOFF_SECONDS = (0.5, 2.0, 8.0)


class OvhFtpStorage:
    """Storage su server OVH via FTP/FTPS.

    Una connessione **per-operazione** (connect→login→op→quit): FTP è stateful
    e non thread-safe, e gli hosting condivisi droppano le connessioni idle.
    Upload **atomici** (STOR su nome temporaneo + RNTO). MKD ricorsivo per i
    path annidati. Retry con backoff sugli errori transitori."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        base_path: str,
        use_tls: bool,
        timeout: int,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.base_path = base_path.rstrip("/")
        self.use_tls = use_tls
        self.timeout = timeout
        # Cache dei prefissi di directory già creati su questa istanza
        # (le dir persistono sul server tra connessioni → cache valida).
        self._made_dirs: set[str] = set()

    # --- low level -------------------------------------------------------

    def _remote_path(self, key: str) -> str:
        return f"{self.base_path}/{_normalize_segments(key)}"

    @contextmanager
    def _connect(self) -> Iterator[ftplib.FTP]:
        ftp: ftplib.FTP
        if self.use_tls:
            ftp = _ReusedSslFTP_TLS(timeout=self.timeout)
        else:
            ftp = ftplib.FTP(timeout=self.timeout)
        try:
            ftp.connect(self.host, self.port)
            ftp.login(self.user, self.password)
            if isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()
            ftp.set_pasv(True)
            yield ftp
        finally:
            try:
                ftp.quit()
            except Exception:  # pragma: no cover - best effort
                try:
                    ftp.close()
                except Exception:
                    pass

    def _run(self, op: Callable[[ftplib.FTP], T]) -> T:
        """Esegue `op` dentro una connessione fresca, con retry sugli errori
        transitori. ``error_perm`` (5xx, es. 550) NON viene ritentato e
        propaga al chiamante."""
        last: Exception | None = None
        for attempt in range(len(_BACKOFF_SECONDS)):
            try:
                with self._connect() as ftp:
                    return op(ftp)
            except ftplib.error_perm:
                raise
            except _RETRYABLE as exc:
                last = exc
                log.warning(
                    "storage_ftp_retry",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                time.sleep(_BACKOFF_SECONDS[attempt])
        raise StorageError(f"FTP fallito dopo {len(_BACKOFF_SECONDS)} tentativi") from last

    def _ensure_dirs(self, ftp: ftplib.FTP, remote_file: str) -> None:
        dir_path = remote_file.rsplit("/", 1)[0]
        segments = [s for s in dir_path.split("/") if s]
        cur = ""
        for seg in segments:
            cur = f"{cur}/{seg}"
            if cur in self._made_dirs:
                continue
            try:
                ftp.mkd(cur)
            except ftplib.error_perm:
                pass  # 550: directory già esistente
            self._made_dirs.add(cur)

    # --- API -------------------------------------------------------------

    def _stor(self, ftp: ftplib.FTP, key: str, fileobj: io.IOBase) -> None:
        remote = self._remote_path(key)
        self._ensure_dirs(ftp, remote)
        tmp = f"{remote}.part-{uuid4().hex}"
        ftp.storbinary(f"STOR {tmp}", fileobj)
        # Rename atomico sul nome finale; se il dest esiste (rigenerazione)
        # alcuni server rifiutano RNTO → cancella prima (ignora 550).
        try:
            ftp.delete(remote)
        except ftplib.error_perm:
            pass
        try:
            ftp.rename(tmp, remote)
        except ftplib.error_perm:
            # cleanup del temp se il rename fallisce, poi rilancia
            try:
                ftp.delete(tmp)
            except ftplib.error_perm:
                pass
            raise

    def upload_bytes(self, key: str, data: bytes) -> None:
        self._run(lambda ftp: self._stor(ftp, key, io.BytesIO(data)))
        log.info("storage_ftp_write", key=key, size=len(data))

    def upload_file(self, key: str, local_path: Path) -> None:
        def op(ftp: ftplib.FTP) -> None:
            with open(local_path, "rb") as fh:
                self._stor(ftp, key, fh)

        self._run(op)
        log.info("storage_ftp_write_file", key=key)

    def download_bytes(self, key: str) -> bytes:
        remote = self._remote_path(key)
        buf = io.BytesIO()

        def op(ftp: ftplib.FTP) -> None:
            buf.seek(0)
            buf.truncate(0)
            ftp.retrbinary(f"RETR {remote}", buf.write)

        try:
            self._run(op)
        except ftplib.error_perm as exc:
            raise StorageFileNotFound(key) from exc
        return buf.getvalue()

    def download_to(self, key: str, dest_path: Path) -> None:
        remote = self._remote_path(key)
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        def op(ftp: ftplib.FTP) -> None:
            with open(dest, "wb") as fh:
                ftp.retrbinary(f"RETR {remote}", fh.write)

        try:
            self._run(op)
        except ftplib.error_perm as exc:
            raise StorageFileNotFound(key) from exc

    def delete(self, key: str) -> None:
        remote = self._remote_path(key)

        def op(ftp: ftplib.FTP) -> None:
            try:
                ftp.delete(remote)
            except ftplib.error_perm:
                pass  # già assente

        self._run(op)
        log.info("storage_ftp_delete", key=key)

    def delete_prefix(self, prefix: str) -> None:
        remote = self._remote_path(prefix)
        self._run(lambda ftp: self._rmtree(ftp, remote))
        log.info("storage_ftp_delete_prefix", prefix=prefix)

    def _rmtree(self, ftp: ftplib.FTP, remote_dir: str) -> None:
        try:
            entries = list(ftp.mlsd(remote_dir))
        except (ftplib.error_perm, ftplib.error_proto):
            entries = self._rmtree_nlst_fallback(ftp, remote_dir)
            if entries is None:
                return  # dir assente o non listabile
        for name, facts in entries:
            if name in (".", ".."):
                continue
            full = f"{remote_dir}/{name}"
            if facts.get("type") == "dir":
                self._rmtree(ftp, full)
            else:
                try:
                    ftp.delete(full)
                except ftplib.error_perm:
                    pass
        try:
            ftp.rmd(remote_dir)
        except ftplib.error_perm:
            pass

    def _rmtree_nlst_fallback(
        self, ftp: ftplib.FTP, remote_dir: str
    ) -> list[tuple[str, dict]] | None:
        try:
            names = ftp.nlst(remote_dir)
        except ftplib.error_perm:
            return None
        result: list[tuple[str, dict]] = []
        for entry in names:
            name = entry.rsplit("/", 1)[-1]
            if name in (".", ""):
                continue
            full = f"{remote_dir}/{name}"
            # Distingue file da dir tentando un CWD.
            try:
                ftp.cwd(full)
                ftp.cwd("/")
                result.append((name, {"type": "dir"}))
            except ftplib.error_perm:
                result.append((name, {"type": "file"}))
        return result

    def exists(self, key: str) -> bool:
        return self.size(key) is not None

    def size(self, key: str) -> int | None:
        remote = self._remote_path(key)

        def op(ftp: ftplib.FTP) -> int | None:
            try:
                ftp.voidcmd("TYPE I")
                return ftp.size(remote)
            except ftplib.error_perm:
                return None

        return self._run(op)


# ---------------------------------------------------------------------------
# Backend OVH (SFTP / porta 22 su SSH)
# ---------------------------------------------------------------------------


class OvhSftpStorage:
    """Storage su server OVH via SFTP (porta 22).

    Stessa interfaccia/semantica di :class:`OvhFtpStorage` (connessione
    per-operazione, MKD ricorsivo, upload atomico temp+rename, retry) ma su
    canale SSH/SFTP — più robusto su hosting condiviso (cifrato di default,
    niente porte passive). Richiede `paramiko`."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        base_path: str,
        timeout: int,
    ) -> None:
        if paramiko is None:  # pragma: no cover
            raise StorageError(
                "storage_backend=ovh_sftp richiede paramiko (pip install paramiko)."
            )
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.base_path = base_path.rstrip("/")
        self.timeout = timeout
        self._made_dirs: set[str] = set()

    def _remote_path(self, key: str) -> str:
        return f"{self.base_path}/{_normalize_segments(key)}"

    @contextmanager
    def _connect(self) -> "Iterator[paramiko.SFTPClient]":
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            timeout=self.timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        try:
            sftp = client.open_sftp()
            sftp.get_channel().settimeout(self.timeout)
            yield sftp
        finally:
            client.close()

    def _run(self, op: Callable[["paramiko.SFTPClient"], T]) -> T:
        retryable = (paramiko.SSHException, socket.timeout, ConnectionError, EOFError)
        last: Exception | None = None
        for attempt in range(len(_BACKOFF_SECONDS)):
            try:
                with self._connect() as sftp:
                    return op(sftp)
            except retryable as exc:
                last = exc
                log.warning("storage_sftp_retry", attempt=attempt + 1, error=str(exc))
                time.sleep(_BACKOFF_SECONDS[attempt])
        raise StorageError(
            f"SFTP fallito dopo {len(_BACKOFF_SECONDS)} tentativi"
        ) from last

    def _ensure_dirs(self, sftp: "paramiko.SFTPClient", remote_file: str) -> None:
        dir_path = remote_file.rsplit("/", 1)[0]
        segments = [s for s in dir_path.split("/") if s]
        cur = ""
        for seg in segments:
            cur = f"{cur}/{seg}"
            if cur in self._made_dirs:
                continue
            try:
                sftp.mkdir(cur)
            except OSError:
                pass  # già esistente (o creata da un'altra connessione)
            self._made_dirs.add(cur)

    def _stor(self, sftp: "paramiko.SFTPClient", key: str, fileobj: io.IOBase) -> None:
        remote = self._remote_path(key)
        self._ensure_dirs(sftp, remote)
        tmp = f"{remote}.part-{uuid4().hex}"
        sftp.putfo(fileobj, tmp)
        try:
            sftp.posix_rename(tmp, remote)
        except (OSError, AttributeError):
            # Server senza estensione posix-rename: cancella il dest e rinomina.
            try:
                sftp.remove(remote)
            except OSError:
                pass
            sftp.rename(tmp, remote)

    def upload_bytes(self, key: str, data: bytes) -> None:
        self._run(lambda sftp: self._stor(sftp, key, io.BytesIO(data)))
        log.info("storage_sftp_write", key=key, size=len(data))

    def upload_file(self, key: str, local_path: Path) -> None:
        def op(sftp: "paramiko.SFTPClient") -> None:
            with open(local_path, "rb") as fh:
                self._stor(sftp, key, fh)

        self._run(op)
        log.info("storage_sftp_write_file", key=key)

    def download_bytes(self, key: str) -> bytes:
        remote = self._remote_path(key)

        def op(sftp: "paramiko.SFTPClient") -> bytes:
            try:
                with sftp.open(remote, "rb") as fh:
                    return fh.read()
            except FileNotFoundError as exc:
                raise StorageFileNotFound(key) from exc

        return self._run(op)

    def download_to(self, key: str, dest_path: Path) -> None:
        remote = self._remote_path(key)
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        def op(sftp: "paramiko.SFTPClient") -> None:
            try:
                sftp.get(remote, str(dest))
            except FileNotFoundError as exc:
                raise StorageFileNotFound(key) from exc

        self._run(op)

    def delete(self, key: str) -> None:
        remote = self._remote_path(key)

        def op(sftp: "paramiko.SFTPClient") -> None:
            try:
                sftp.remove(remote)
            except FileNotFoundError:
                pass

        self._run(op)
        log.info("storage_sftp_delete", key=key)

    def delete_prefix(self, prefix: str) -> None:
        remote = self._remote_path(prefix)
        self._run(lambda sftp: self._rmtree(sftp, remote))
        log.info("storage_sftp_delete_prefix", prefix=prefix)

    def _rmtree(self, sftp: "paramiko.SFTPClient", remote_dir: str) -> None:
        try:
            entries = sftp.listdir_attr(remote_dir)
        except FileNotFoundError:
            return
        for entry in entries:
            full = f"{remote_dir}/{entry.filename}"
            if stat_module.S_ISDIR(entry.st_mode or 0):
                self._rmtree(sftp, full)
            else:
                try:
                    sftp.remove(full)
                except FileNotFoundError:
                    pass
        try:
            sftp.rmdir(remote_dir)
        except FileNotFoundError:
            pass

    def exists(self, key: str) -> bool:
        return self.size(key) is not None

    def size(self, key: str) -> int | None:
        remote = self._remote_path(key)

        def op(sftp: "paramiko.SFTPClient") -> int | None:
            try:
                return sftp.stat(remote).st_size
            except FileNotFoundError:
                return None

        return self._run(op)


# ---------------------------------------------------------------------------
# Wrapper di fallback (cutover): legge dal locale se manca sul primario
# ---------------------------------------------------------------------------


class FallbackStorage:
    """Scrive/cancella solo sul primario; in lettura ripiega sul fallback se
    il file non è (ancora) presente sul primario. Usato durante il cutover
    (``storage_local_fallback=true``)."""

    def __init__(self, primary: Storage, fallback: Storage) -> None:
        self.primary = primary
        self.fallback = fallback

    def upload_bytes(self, key: str, data: bytes) -> None:
        self.primary.upload_bytes(key, data)

    def upload_file(self, key: str, local_path: Path) -> None:
        self.primary.upload_file(key, local_path)

    def delete(self, key: str) -> None:
        self.primary.delete(key)

    def delete_prefix(self, prefix: str) -> None:
        self.primary.delete_prefix(prefix)

    def download_bytes(self, key: str) -> bytes:
        try:
            return self.primary.download_bytes(key)
        except StorageFileNotFound:
            log.warning("storage_fallback_read", key=key)
            return self.fallback.download_bytes(key)

    def download_to(self, key: str, dest_path: Path) -> None:
        try:
            self.primary.download_to(key, dest_path)
        except StorageFileNotFound:
            log.warning("storage_fallback_read", key=key)
            self.fallback.download_to(key, dest_path)

    def exists(self, key: str) -> bool:
        return self.primary.exists(key) or self.fallback.exists(key)

    def size(self, key: str) -> int | None:
        s = self.primary.size(key)
        return s if s is not None else self.fallback.size(key)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _require_ovh_settings() -> None:
    s = get_settings()
    missing = [
        name
        for name, val in (
            ("OVH_FTP_HOST", s.ovh_ftp_host),
            ("OVH_FTP_USER", s.ovh_ftp_user),
            ("OVH_FTP_PASSWORD", s.ovh_ftp_password),
            ("OVH_PUBLIC_BASE_URL", s.ovh_public_base_url),
        )
        if not val
    ]
    if missing:
        raise StorageError(
            "Backend OVH ma mancano le variabili: " + ", ".join(missing)
        )


def _build_ovh_ftp() -> OvhFtpStorage:
    _require_ovh_settings()
    s = get_settings()
    return OvhFtpStorage(
        host=s.ovh_ftp_host,  # type: ignore[arg-type]
        port=s.ovh_ftp_port,
        user=s.ovh_ftp_user,  # type: ignore[arg-type]
        password=s.ovh_ftp_password,  # type: ignore[arg-type]
        base_path=s.ovh_ftp_base_path,
        use_tls=s.ovh_ftp_use_tls,
        timeout=s.ovh_ftp_timeout_seconds,
    )


def _build_ovh_sftp() -> OvhSftpStorage:
    if paramiko is None:
        raise StorageError(
            "storage_backend=ovh_sftp richiede paramiko (pip install paramiko)."
        )
    _require_ovh_settings()
    s = get_settings()
    return OvhSftpStorage(
        host=s.ovh_ftp_host,  # type: ignore[arg-type]
        port=s.ovh_sftp_port,
        user=s.ovh_ftp_user,  # type: ignore[arg-type]
        password=s.ovh_ftp_password,  # type: ignore[arg-type]
        base_path=s.ovh_ftp_base_path,
        timeout=s.ovh_ftp_timeout_seconds,
    )


def _build_remote(backend: str) -> Storage:
    if backend == "ovh_sftp":
        return _build_ovh_sftp()
    if backend == "ovh_ftp":
        return _build_ovh_ftp()
    raise StorageError(f"Backend remoto non valido: {backend!r}")


def build_remote_backend(protocol: str | None = None) -> Storage:
    """Costruisce il backend remoto (ftp|sftp) **a prescindere** da
    `storage_backend` — utile agli script di spike/migrazione che girano
    mentre l'app è ancora `local`. Se `protocol` è None lo deduce da
    `storage_backend` (se remoto), altrimenti solleva."""
    if protocol is None:
        sb = get_settings().storage_backend
        if sb in ("ovh_ftp", "ovh_sftp"):
            return _build_remote(sb)
        raise StorageError(
            "Specifica il protocollo (--protocol ftp|sftp) o imposta "
            "STORAGE_BACKEND=ovh_ftp|ovh_sftp."
        )
    return _build_remote(f"ovh_{protocol}")


def get_storage() -> Storage:
    """Ritorna il backend di storage attivo (in base a ``storage_backend``).

    Non cache-ato: le istanze sono leggere (nessuna connessione persistente)
    e così i test possono cambiare backend cambiando le env."""
    s = get_settings()
    if s.storage_backend in ("ovh_ftp", "ovh_sftp"):
        primary = _build_remote(s.storage_backend)
        if s.storage_local_fallback:
            return FallbackStorage(primary, LocalStorage())
        return primary
    return LocalStorage()
