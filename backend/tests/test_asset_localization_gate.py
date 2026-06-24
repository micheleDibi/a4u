"""Gate della rete di sicurezza i18n sugli asset (Bug "asset non tradotti").

Verifica `asset_validation_service._needs_localization` + i mattoni di
`app/core/i18n_scripts`: per lingue a script non-latino (giapponese) un campo
asset rimasto in italiano viene segnalato per la ri-traduzione, mentre un campo
già nello script target, il testo puramente matematico e le lingue latine NON
attivano la rete di sicurezza. Test puro (niente DB, niente rete).
"""
from __future__ import annotations

from app.core.i18n_scripts import has_target_script_chars, primary_script
from app.services.asset_validation_service import _needs_localization


def test_primary_script_latin_vs_non_latin() -> None:
    assert primary_script("ja") == "cjk"
    assert primary_script("ru") == "cyrillic"
    # Lingue latine → nessuno script primario (gate spento a monte).
    assert primary_script("en") is None
    assert primary_script("it") is None


def test_has_target_script_chars_japanese() -> None:
    assert has_target_script_chars("プロセス図", "ja") is True
    assert has_target_script_chars("Diagramma del processo", "ja") is False
    # Per lingue latine qualunque testo è accettato (nessun vincolo).
    assert has_target_script_chars("Process diagram", "en") is True


def test_needs_localization_flags_wrong_language_text() -> None:
    # Caption italiana in un corso giapponese → va localizzata.
    assert _needs_localization("Diagramma del processo", "ja") is True
    # Enunciato con LaTeX ma prosa italiana → va localizzato.
    assert _needs_localization("Teorema di Pitagora: $a^2+b^2=c^2$", "ja") is True


def test_needs_localization_skips_correct_and_neutral_fields() -> None:
    # Già in giapponese → ok.
    assert _needs_localization("プロセス図", "ja") is False
    # Solo math (nessuna prosa da tradurre) → skip, niente token sprecati.
    assert _needs_localization("$x^2 + y^2 = z^2$", "ja") is False
    # Vuoto / whitespace → skip.
    assert _needs_localization("", "ja") is False
    assert _needs_localization("   ", "ja") is False
    # Lingua latina: il gate per-campo non scatta mai (la difesa è il prompt).
    assert _needs_localization("Process diagram", "en") is False
