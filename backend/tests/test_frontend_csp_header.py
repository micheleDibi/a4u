"""Content-Security-Policy della pagina dell'applicazione (SEC-1).

La pagina è servita da nginx, non dal backend: la politica del documento è
quella di `frontend/nginx.conf`, e l'`img-src` è l'unico controllo che
impedisce la richiesta verso l'host scelto dall'autore di un diagramma
Mermaid — `mermaid.render` attacca l'SVG al documento per misurarlo prima
che `sanitizeMermaidSvg` possa toglierne un `<image href>`.

Questi test pinnano la presenza e la FORMA della direttiva, ed eseguono lo
stesso blocco `sh` del `Dockerfile` sui due valori di
`VITE_UPLOADS_BASE_URL` che il progetto usa davvero (`/uploads` in
sviluppo, l'URL assoluto dello storage in produzione).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
NGINX_CONF = FRONTEND / "nginx.conf"
DOCKERFILE = FRONTEND / "Dockerfile"
SEGNAPOSTO = "__A4U_UPLOADS_ORIGIN__"
_DIRETTIVA_RE = re.compile(
    r'add_header\s+Content-Security-Policy\s+"([^"]*)"\s+always;',
)


def _direttiva(testo: str) -> str:
    trovate = _DIRETTIVA_RE.findall(testo)
    assert len(trovate) == 1, f"attese 1 direttiva CSP, trovate {len(trovate)}"
    return trovate[0]


def test_nginx_serve_una_csp_con_il_solo_img_src():
    """La pagina dell'app ha la politica, e ha SOLO `img-src`.

    Senza `default-src` il raggio d'azione resta le immagini: script,
    stili, font (Google Fonts sta in `index.html`), connessioni e media
    non sono toccati e la politica non può rompere l'app altrove."""
    politica = _direttiva(NGINX_CONF.read_text())
    assert politica == f"img-src 'self' data: blob:{SEGNAPOSTO}"
    assert ";" not in politica, "una sola direttiva: aggiungerne altre allarga il raggio"


def test_le_anteprime_locali_restano_ammesse():
    """`data:` e `blob:` sono nella politica: sono le anteprime di
    Vega-Lite, DOT e `function` (SVG normalizzato in un `<img src="data:">`)
    e le anteprime di un file appena scelto (`URL.createObjectURL`)."""
    politica = _direttiva(NGINX_CONF.read_text())
    assert "data:" in politica and "blob:" in politica and "'self'" in politica


def _blocco_di_sostituzione() -> str:
    """Il blocco `RUN set -eu; …` del `Dockerfile`, senza il prefisso
    `RUN`: è lo script che deriva l'origine degli upload."""
    testo = DOCKERFILE.read_text()
    inizio = testo.index("RUN set -eu;")
    righe = []
    for riga in testo[inizio:].split("\n"):
        righe.append(riga)
        if not riga.rstrip().endswith("\\"):
            break
    return "\n".join(righe).removeprefix("RUN ")


@pytest.mark.parametrize(
    ("uploads", "atteso"),
    [
        # Default: gli upload stanno sulla stessa origine (`/uploads`
        # proxato da nginx), `'self'` basta e non si aggiunge nulla.
        ("/uploads", "img-src 'self' data: blob:"),
        # Produzione: storage OVH su un altro host (`.env.example`).
        (
            "https://progettiersaf.com/media/uploads",
            "img-src 'self' data: blob: https://progettiersaf.com",
        ),
        # Porta esplicita e URL senza path.
        ("http://127.0.0.1:9000/uploads", "img-src 'self' data: blob: http://127.0.0.1:9000"),
        ("https://cdn.example.org", "img-src 'self' data: blob: https://cdn.example.org"),
        # Senza schema: per la CSP l'host da solo è una sorgente valida.
        ("//cdn.example.org/uploads", "img-src 'self' data: blob: cdn.example.org"),
    ],
)
def test_la_sostituzione_a_build_time_produce_la_direttiva(
    tmp_path: Path, uploads: str, atteso: str
):
    """Lo stesso script del `Dockerfile`, eseguito qui sui percorsi di
    `tmp_path`: se qualcuno cambia la derivazione dell'origine senza
    accorgersene, questo test lo vede senza dover costruire l'immagine."""
    if shutil.which("sh") is None:  # pragma: no cover - ambiente senza shell
        pytest.skip("shell POSIX non disponibile")
    sorgente = tmp_path / "nginx.conf.in"
    generato = tmp_path / "default.conf"
    shutil.copy(NGINX_CONF, sorgente)
    script = (
        _blocco_di_sostituzione()
        .replace("/etc/nginx/a4u-nginx.conf.in", str(sorgente))
        .replace("/etc/nginx/conf.d/default.conf", str(generato))
    )
    esito = subprocess.run(
        ["sh", "-c", script],
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "VITE_UPLOADS_BASE_URL": uploads},
        capture_output=True,
        text=True,
        check=False,
    )
    assert esito.returncode == 0, esito.stderr
    testo = generato.read_text()
    assert SEGNAPOSTO not in testo
    assert _direttiva(testo) == atteso
