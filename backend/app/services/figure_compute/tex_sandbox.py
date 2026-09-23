"""Lanciatore di un comando TeX con limiti di risorse (SBX-2, WP6).

Si esegue come script separato, con `python -I` (niente site, niente
variabili PYTHON*): `python -I tex_sandbox.py --cpu 10 --as-mb 1536
--fsize-mb 16 --nofile 256 -- xelatex …`. Imposta i limiti del processo
(CPU, memoria virtuale, dimensione dei file scritti, file aperti, core
dump a zero) e poi SOSTITUISCE se stesso con il comando (`execv`): nessun
Python resta vivo accanto a TeX. L'ambiente lo decide il chiamante
(`tikz_compile_service._env`: solo variabili ammesse, nessun segreto).

Solo libreria standard: il modulo non importa nulla dell'applicazione.
"""

from __future__ import annotations

import contextlib
import os
import resource
import sys

_LIMITS = {
    "--cpu": (resource.RLIMIT_CPU, 1),
    "--as-mb": (resource.RLIMIT_AS, 1024 * 1024),
    "--fsize-mb": (resource.RLIMIT_FSIZE, 1024 * 1024),
    "--nofile": (resource.RLIMIT_NOFILE, 1),
}


def _apply(limit: int, value: int) -> None:
    _soft, hard = resource.getrlimit(limit)
    target = value if hard == resource.RLIM_INFINITY else min(value, hard)
    # Alcuni limiti non esistono su ogni sistema (RLIMIT_AS su macOS):
    # restano gli altri e il timeout del chiamante.
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(limit, (target, target))


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("uso: tex_sandbox.py [limiti] -- comando …", file=sys.stderr)
        return 2
    split = argv.index("--")
    options, command = argv[:split], argv[split + 1 :]
    if not command:
        return 2
    for i in range(0, len(options) - 1, 2):
        spec = _LIMITS.get(options[i])
        if spec is not None:
            _apply(spec[0], int(options[i + 1]) * spec[1])
    _apply(resource.RLIMIT_CORE, 0)
    os.execv(command[0], command)
    return 127  # pragma: no cover - execv non ritorna


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
