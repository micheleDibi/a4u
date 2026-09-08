"""`docs/PROMPTS.md` riporta «verbatim» i system prompt: qui il verbatim è
un TEST, non una promessa.

Il repository possiede già il confronto meccanico
(`backend/scripts/check_prompts_md.py`), ma finché restava un comando da
lanciare a mano la deriva passava in silenzio: al momento di questa
revisione il documento aveva perso tre righe del PROMPT 12 (il divieto di
risorse esterne nelle shape `@{ ... }` di Mermaid), aggiunte al codice
diversi commit prima. Questo test invoca la stessa funzione di confronto
dello script, così una modifica ai prompt che non tocca il documento fa
fallire la suite invece di lasciare una documentazione falsa.

Test puro: nessun DB, nessuna rete, nessun modello: i renderer usano i
segnaposto documentati (`{language_code}`, `{ruolo_docente}`, …).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_prompts_md import DEFAULT_DOCS, build_checks, compare

_DOC = Path(DEFAULT_DOCS)


@pytest.fixture(scope="module")
def documento() -> str:
    if not _DOC.is_file():  # pragma: no cover - albero docs assente
        pytest.skip(f"documento assente: {_DOC}")
    return _DOC.read_text(encoding="utf-8")


def test_every_documented_prompt_is_identical_to_the_code(documento: str) -> None:
    diffs = [
        f"{check.label}:\n{diff}" for check, diff in compare(documento, build_checks()) if diff
    ]
    assert not diffs, (
        "docs/PROMPTS.md non è più il testo del codice. Riallinea con "
        "`python scripts/check_prompts_md.py` (stampa il diff) e incolla il "
        "blocco reso.\n\n" + "\n\n".join(diffs)
    )


def test_the_comparison_would_catch_a_missing_line(documento: str) -> None:
    """Controprova dell'oracolo: tolta una riga da un blocco del documento,
    il confronto la segnala. Senza questa, un estrattore rotto (nessun
    blocco trovato) renderebbe il test precedente vacuamente verde."""
    checks = build_checks()
    assert checks, "nessun prompt da confrontare"
    prima_riga_utile = next(
        riga for riga in checks[0].rendered.splitlines() if len(riga.strip()) > 30
    )
    mutilato = documento.replace(prima_riga_utile + "\n", "", 1)
    assert mutilato != documento, prima_riga_utile
    diffs = [diff for _check, diff in compare(mutilato, checks) if diff]
    assert diffs, "il confronto non vede una riga mancante"
