"""Test del layer di storage (`app.services.remote_storage`).

Coprono la logica a più alto rischio: il mapping path-DB → key namespaced
(con le convenzioni di prefisso incoerenti delle colonne), il backend locale
e la costruzione degli URL. Il backend OVH è testato contro un server FTP
in-process (`pyftpdlib`) quando disponibile.
"""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services import remote_storage as rs


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    """Storage locale isolato in `tmp_path`, con cache settings ripulita."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("GENERATED_PDFS_DIR", str(tmp_path / "generated_pdfs"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


# --- Key mapping (logica più delicata) -------------------------------------


@pytest.mark.parametrize(
    "db_path,expected",
    [
        ("/uploads/courses/c/x.pdf", "uploads/courses/c/x.pdf"),
        ("uploads/courses/c/x.pdf", "uploads/courses/c/x.pdf"),
        ("lesson_videos/c/l.mp4", "uploads/lesson_videos/c/l.mp4"),
        ("avatars/u/a.wav", "uploads/avatars/u/a.wav"),
        ("/uploads/avatars/u/clips/k.mp4", "uploads/avatars/u/clips/k.mp4"),
    ],
)
def test_uploads_key(db_path, expected):
    assert rs.uploads_key(db_path) == expected


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("o/c/l.pdf", "generated_pdfs/o/c/l.pdf"),
        ("o/c/l_slides.pdf", "generated_pdfs/o/c/l_slides.pdf"),
        ("generated_pdfs/o/c/l.pdf", "generated_pdfs/o/c/l.pdf"),
    ],
)
def test_pdf_key(rel, expected):
    assert rs.pdf_key(rel) == expected


@pytest.mark.parametrize("bad", ["../etc/passwd", "/uploads/../secret", "a/../../b"])
def test_key_traversal_blocked(bad):
    with pytest.raises(rs.StorageError):
        rs.uploads_key(bad)


# --- URL building -----------------------------------------------------------


def test_media_and_public_url_local(local_env):
    key = rs.uploads_key("lesson_videos/c/l.mp4")
    assert rs.media_url(key) == "/uploads/lesson_videos/c/l.mp4"
    assert rs.public_url(key) == "http://localhost:8000/uploads/lesson_videos/c/l.mp4"


def test_media_url_ovh(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "ovh_ftp")
    monkeypatch.setenv("OVH_PUBLIC_BASE_URL", "https://progettiersaf.com/media")
    get_settings.cache_clear()
    try:
        key = rs.uploads_key("lesson_videos/c/l.mp4")
        assert (
            rs.media_url(key)
            == "https://progettiersaf.com/media/uploads/lesson_videos/c/l.mp4"
        )
        # In ovh mode public_url coincide con media_url (assoluto OVH).
        assert rs.public_url(key) == rs.media_url(key)
    finally:
        get_settings.cache_clear()


# --- LocalStorage round-trip ------------------------------------------------


def test_local_storage_roundtrip_uploads(local_env):
    st = rs.get_storage()
    assert isinstance(st, rs.LocalStorage)
    key = rs.uploads_key("courses/c1/doc.pdf")
    st.upload_bytes(key, b"hello")
    assert st.exists(key)
    assert st.size(key) == 5
    assert st.download_bytes(key) == b"hello"
    st.delete(key)
    assert not st.exists(key)


def test_local_storage_roundtrip_pdf(local_env):
    st = rs.get_storage()
    key = rs.pdf_key("org/course/lesson.pdf")
    st.upload_bytes(key, b"%PDF-1.4 fake")
    # Finisce sotto generated_pdfs/, non uploads/.
    assert (local_env / "generated_pdfs" / "org" / "course" / "lesson.pdf").is_file()
    assert st.download_bytes(key) == b"%PDF-1.4 fake"


def test_local_storage_missing_raises(local_env):
    st = rs.get_storage()
    with pytest.raises(rs.StorageFileNotFound):
        st.download_bytes(rs.uploads_key("courses/none/x.pdf"))


def test_local_storage_delete_prefix(local_env):
    st = rs.get_storage()
    for i in range(3):
        st.upload_bytes(rs.uploads_key(f"avatars/u1/clips/{i}.mp4"), b"x")
    st.upload_bytes(rs.uploads_key("avatars/u2/clips/0.mp4"), b"y")
    st.delete_prefix(rs.uploads_key("avatars/u1"))
    assert not st.exists(rs.uploads_key("avatars/u1/clips/0.mp4"))
    # L'altro utente resta intatto.
    assert st.exists(rs.uploads_key("avatars/u2/clips/0.mp4"))


def test_local_storage_upload_file(local_env, tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload-1234")
    st = rs.get_storage()
    key = rs.uploads_key("lesson_videos/c/l.mp4")
    st.upload_file(key, src)
    assert st.download_bytes(key) == b"payload-1234"


# --- OvhFtpStorage contro un server FTP in-process (se pyftpdlib c'è) -------


@pytest.fixture
def ftp_server(tmp_path):
    pyftpdlib = pytest.importorskip("pyftpdlib")
    import threading

    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer

    ftp_root = tmp_path / "ftproot"
    ftp_root.mkdir()
    authorizer = DummyAuthorizer()
    authorizer.add_user("u", "p", str(ftp_root), perm="elradfmwMT")
    handler = FTPHandler
    handler.authorizer = authorizer
    server = FTPServer(("127.0.0.1", 0), handler)
    port = server.socket.getsockname()[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, ftp_root
    finally:
        server.close_all()


def _ovh_storage(port: int) -> rs.OvhFtpStorage:
    return rs.OvhFtpStorage(
        host="127.0.0.1",
        port=port,
        user="u",
        password="p",
        base_path="/media",
        use_tls=False,  # il server di test è in chiaro
        timeout=10,
    )


def test_ovh_storage_roundtrip(ftp_server):
    port, _root = ftp_server
    st = _ovh_storage(port)
    key = rs.pdf_key("org/course/lesson.pdf")
    st.upload_bytes(key, b"%PDF data")  # crea ricorsivamente /media/generated_pdfs/...
    assert st.exists(key)
    assert st.size(key) == len(b"%PDF data")
    assert st.download_bytes(key) == b"%PDF data"
    # Overwrite atomico (rigenerazione).
    st.upload_bytes(key, b"%PDF v2 longer")
    assert st.download_bytes(key) == b"%PDF v2 longer"
    st.delete(key)
    assert not st.exists(key)


def test_ovh_storage_missing_raises(ftp_server):
    port, _root = ftp_server
    st = _ovh_storage(port)
    with pytest.raises(rs.StorageFileNotFound):
        st.download_bytes(rs.uploads_key("courses/x/none.pdf"))


def test_ovh_storage_delete_prefix(ftp_server):
    port, _root = ftp_server
    st = _ovh_storage(port)
    for i in range(3):
        st.upload_bytes(rs.uploads_key(f"avatars/u1/clips/{i}.mp4"), b"x")
    st.delete_prefix(rs.uploads_key("avatars/u1"))
    assert not st.exists(rs.uploads_key("avatars/u1/clips/0.mp4"))
