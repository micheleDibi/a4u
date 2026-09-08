"""Content-Security-Policy della pagina dell'applicazione (SEC-1).

La pagina è servita da nginx, non dal backend: la politica del documento è
quella di `frontend/nginx.conf`, e l'`img-src` è l'unico controllo che
impedisce la richiesta verso l'host scelto dall'autore di un diagramma
Mermaid — `mermaid.render` attacca l'SVG al documento per misurarlo prima
che `sanitizeMermaidSvg` possa toglierne un `<image href>`.

Questi test pinnano la presenza e la FORMA della direttiva, il fatto che
nessun `location` la faccia sparire per l'ereditarietà degli `add_header`
di nginx, ed eseguono lo stesso blocco `sh` del `Dockerfile` sui valori di
`VITE_UPLOADS_BASE_URL` che il progetto usa davvero (`/uploads` in
sviluppo, l'URL assoluto dello storage in produzione), su quelli
normalizzati (schema in maiuscolo, spazi ai bordi) e su quelli malformati,
che devono FAR FALLIRE il build invece di generare una direttiva sbagliata.
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
# Un blocco `location … { … }` di `nginx.conf`: il corpo non ha graffe
# annidate oggi, ma la seconda alternativa ne tollera un livello.
_LOCATION_RE = re.compile(r"location\s+[^{\n]+\{(?:[^{}]|\{[^{}]*\})*\}")


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


def test_nessun_location_definisce_un_proprio_add_header():
    """La politica arriva su ogni risposta solo per EREDITARIETÀ.

    nginx eredita gli `add_header` del livello superiore soltanto se il
    livello corrente non ne ha nessuno: basta un `add_header` dentro un
    `location` perché lì spariscano tutti e cinque quelli del `server`,
    la Content-Security-Policy compresa. Misurato su nginx 1.27.5 con
    `add_header X-Futuro "1" always;` dentro `location / { … }`: la
    risposta porta `X-Futuro: 1` e nessuno degli altri cinque. È un
    footgun silenzioso — il file resta valido e `nginx -t` passa — e da
    SEC-1 ci si appoggia un controllo di sicurezza, quindi lo pinniamo
    qui. Chi deve aggiungere un header a un `location` ripeta lì anche
    questi cinque (oppure li sposti in un file incluso da ogni blocco).
    """
    testo = NGINX_CONF.read_text()
    blocchi = _LOCATION_RE.findall(testo)
    nomi = re.findall(r"location\s+([^{\n]+?)\s*\{", testo)
    assert len(blocchi) == len(nomi) >= 3, f"blocchi {len(blocchi)}, location {nomi}"
    for nome, blocco in zip(nomi, blocchi, strict=True):
        assert "add_header" not in blocco, (
            f"`location {nome}` definisce un add_header: cancella i cinque header "
            f"del server su quel percorso.\n{blocco}"
        )


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


def _esegui_sostituzione(tmp_path: Path, uploads: str) -> tuple[int, str]:
    """Lo script del `Dockerfile` sui percorsi di `tmp_path`: codice di
    uscita e testo del file generato (vuoto se non è stato prodotto)."""
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
    return esito.returncode, generato.read_text() if generato.is_file() else ""


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
        # Schema in maiuscolo (giro 8): il `case` della shell è sensibile
        # alle maiuscole e cadeva nel ramo relativo, svuotando l'origine;
        # `media.ts` (`/^(https?:)?\/\//i`) lo tratta invece come assoluto e
        # il browser caricava da lì — cioè tutte le immagini caricate
        # sparivano in silenzio, con il build verde.
        (
            "HTTPS://Progettiersaf.com/media/uploads",
            "img-src 'self' data: blob: https://progettiersaf.com",
        ),
        ("Https://CDN.Example.org", "img-src 'self' data: blob: https://cdn.example.org"),
        # Spazi ai bordi (giro 8): tipici di un `.env` copiato male. Il
        # browser li ignora nell'attributo e caricava comunque dall'host.
        (" https://a.example.com/u ", "img-src 'self' data: blob: https://a.example.com"),
        ("\thttps://a.example.com/u\n", "img-src 'self' data: blob: https://a.example.com"),
    ],
)
def test_la_sostituzione_a_build_time_produce_la_direttiva(
    tmp_path: Path, uploads: str, atteso: str
):
    """Se qualcuno cambia la derivazione dell'origine senza accorgersene,
    questo test lo vede senza dover costruire l'immagine."""
    codice, testo = _esegui_sostituzione(tmp_path, uploads)
    assert codice == 0, testo
    assert SEGNAPOSTO not in testo
    assert _direttiva(testo) == atteso


@pytest.mark.parametrize(
    "uploads",
    [
        # Apice doppio: chiude la stringa della direttiva e ne inietta una
        # seconda nel file generato (`add_header X-Evil "1" always;`). Il
        # `grep -F` del giro 7 la trovava comunque e il build restava verde.
        'https://a.example.com"; add_header X-Evil "1',
        # `|` è il delimitatore della `sed`: l'espressione diventa invalida.
        "https://a.example.com|b",
        # Spazio INTERNO: non è un URL, e normalizzarlo sarebbe indovinare.
        "https://a.example.com /u",
        # Schema senza host e schema non http(s) con host vuoto.
        "https://",
        "//",
    ],
)
def test_la_sostituzione_fallisce_su_un_valore_malformato(tmp_path: Path, uploads: str):
    """La verifica finale del blocco è sulla FORMA della riga generata, non
    sulla sua presenza: un valore che non produce `img-src 'self' data:
    blob:` seguito al più da un'origine ben formata deve fermare il build,
    non arrivare in produzione (giro 8)."""
    codice, testo = _esegui_sostituzione(tmp_path, uploads)
    trovate = _DIRETTIVA_RE.findall(testo)
    assert codice != 0, f"build verde su un valore malformato: {trovate}"
