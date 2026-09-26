"""Verifica meccanica di `docs/PROMPTS.md` contro i prompt reali — sola lettura.

`docs/PROMPTS.md` riporta «verbatim» i system prompt delle fasi AI. Il testo
è scritto a mano e può divergere dal codice a ogni modifica dei prompt:
questo script estrae, per ogni sezione «# PROMPT n» pertinente, il blocco
```text del system prompt (e, per i PROMPT 12 e 17, le varianti dichiarate
«verbatim») e lo confronta carattere per carattere con l'output della
funzione `_system_prompt(...)` del servizio corrispondente.

I prompt sono renderizzati con i **segnaposto documentati** al posto dei
valori reali (`{language_code}`, `{ruolo_docente}`, `{stile_insegnamento}`,
`{livello_eqf}`, `{minuti_per_lezione}`), così il confronto è indipendente
dal corso. Due interpolazioni non si prestano al segnaposto letterale e
sono normalizzate qui, nello stesso modo in cui il documento le riporta:

- PROMPT 6 (discorso): `minuti * 60 = secondi` è calcolato dal codice; il
  prompt è reso con un valore fisso e la stringa `N * 60 = M` è riportata a
  `{minuti_per_lezione} * 60 = {secondi}`;
- PROMPT 11 (immagine → Mermaid): `lang_hint` vale «italiano» o «inglese»
  a seconda della lingua; il prompt è reso in entrambe le lingue e lo span
  che differisce è sostituito da `{lang_hint}`.

Perimetro: PROMPT 3 (dispense, con grounding), 4 (verifica), 5 (slide),
6 (discorso), 11 (immagine → Mermaid), 12 (fix degli asset: variante
principale Mermaid IT e le varianti Vega-Lite / DOT / `function` IT
dichiarate verbatim), 17 (revisore figura ↔ testo: prompt IT e variante
EN), 18 (Vision delle figure di fonte), 19 (ridondanze delle figure di
fonte) e 20 (letteratura aperta: pertinenza e termini di ricerca),
ciascuno con il prompt IT e la variante EN. I
messaggi user e gli schemi JSON non sono confrontati (sono template
descrittivi, non stringhe del codice).

Uso (dalla cartella `backend/`; nessun DB, nessuna rete; `JWT_SECRET` in
ambiente se manca `.env`, come per ogni comando che importa i servizi):

    python -m scripts.check_prompts_md
    python -m scripts.check_prompts_md --docs ../docs/PROMPTS.md

Esce con 0 se ogni blocco coincide, con 1 stampando un diff unificato per
ogni blocco divergente o mancante. Procedura documentata in
`docs/backend/11-tests.md`.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from app.services import openai_asset_fix_service as fix_service
from app.services import openai_figure_describe_service as describe_service
from app.services import openai_figure_intro_service as intro_service
from app.services import openai_figure_needs_service as needs_service
from app.services import openai_figure_redundancy_service as redundancy_service
from app.services import openai_figure_relevance_service as relevance_service
from app.services import openai_figure_review_service as review_service
from app.services import openai_image_to_mermaid_service as image_service
from app.services import openai_lesson_content_service as content_service
from app.services import openai_lesson_slides_service as slides_service
from app.services import openai_lesson_speech_service as speech_service
from app.services import openai_tikz_render_review_service as tikz_review_service

DEFAULT_DOCS = Path(__file__).resolve().parents[2] / "docs" / "PROMPTS.md"

_HEADER_RE = re.compile(r"^# PROMPT (\d+)\b")
_TEXT_FENCE_RE = re.compile(r"```text\n(.*?)\n```", re.DOTALL)
_VARIANT_RE = re.compile(
    r"\*\*Variante `(_SYSTEM_[A-Z]+_(?:IT|EN))`\*\* \(verbatim\):\s*\n\s*\n```text\n(.*?)\n```",
    re.DOTALL,
)

_SPEECH_MINUTES = 45


@dataclass(frozen=True)
class Check:
    """Un confronto: sezione del documento, eventuale variante, testo reso."""

    label: str
    prompt_number: int
    rendered: str
    variant: str | None = None


# ---------------------------------------------------------------------------
# Lettura del documento
# ---------------------------------------------------------------------------


def split_sections(doc: str) -> dict[int, str]:
    """Testo di ogni sezione «# PROMPT n» (dall'intestazione all'intestazione
    di primo livello successiva). Le righe dentro un fence non contano come
    intestazioni."""
    sections: dict[int, str] = {}
    current: int | None = None
    buffer: list[str] = []
    in_fence = False
    for line in doc.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("# "):
            if current is not None:
                sections[current] = "\n".join(buffer)
            match = _HEADER_RE.match(line)
            current = int(match.group(1)) if match else None
            buffer = []
        if current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer)
    return sections


def system_block(section: str) -> str | None:
    """Il primo blocco ```text della sezione: per convenzione del documento
    è il system prompt."""
    match = _TEXT_FENCE_RE.search(section)
    return match.group(1) if match else None


def variant_blocks(section: str) -> dict[str, str]:
    """Le varianti «verbatim» dei PROMPT 12 e 17, per nome della costante."""
    return dict(_VARIANT_RE.findall(section))


# ---------------------------------------------------------------------------
# Rendering dei prompt con i segnaposto documentati
# ---------------------------------------------------------------------------


def _replace_differing_span(first: str, second: str, placeholder: str) -> str:
    """Sostituisce con `placeholder` l'unico span in cui `first` e `second`
    differiscono, esteso ai confini di parola («italiano» / «inglese»
    condividono la «i» iniziale: il segnaposto copre la parola intera)."""
    prefix = 0
    limit = min(len(first), len(second))
    while prefix < limit and first[prefix] == second[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and first[len(first) - 1 - suffix] == second[len(second) - 1 - suffix]
    ):
        suffix += 1
    start, end = prefix, len(first) - suffix
    while start > 0 and first[start - 1].isalnum():
        start -= 1
    while end < len(first) and first[end].isalnum():
        end += 1
    return first[:start] + placeholder + first[end:]


def render_content() -> str:
    return content_service._system_prompt(
        "{language_code}",
        ruolo_docente="{ruolo_docente}",
        stile_insegnamento="{stile_insegnamento}",
        livello_eqf="{livello_eqf}",
        grounding_enabled=True,
    )


def render_assessment() -> str:
    return content_service._assessment_system_prompt("{language_code}")


def render_slides() -> str:
    # La durata entra in una f-string: il segnaposto letterale resta tale.
    return slides_service._system_prompt(
        "{language_code}",
        minuti_per_lezione=cast(int, "{minuti_per_lezione}"),
        livello_eqf="{livello_eqf}",
        ruolo_docente="{ruolo_docente}",
        stile_insegnamento="{stile_insegnamento}",
    )


def render_speech() -> str:
    rendered = speech_service._system_prompt(
        "{language_code}",
        minuti_per_lezione=_SPEECH_MINUTES,
        ruolo_docente="{ruolo_docente}",
    )
    computed = f"{_SPEECH_MINUTES} * 60 = {_SPEECH_MINUTES * 60}"
    return rendered.replace(computed, "{minuti_per_lezione} * 60 = {secondi}")


def render_image_to_mermaid() -> str:
    italian = image_service._system_prompt("it")
    english = image_service._system_prompt("en")
    return _replace_differing_span(italian, english, "{lang_hint}")


def render_fix(kind: str) -> str:
    italian, _english = fix_service._SYSTEM_PROMPTS[kind]
    return italian


def render_review(language: str) -> str:
    return review_service._system_prompt(language)


def render_describe(language: str) -> str:
    return describe_service._system_prompt(language)


def render_redundancy(language: str) -> str:
    return redundancy_service._system_prompt(language)


def render_relevance(language: str, kind: str) -> str:
    return relevance_service._system_prompt(language, kind)  # type: ignore[arg-type]


def render_needs(language: str) -> str:
    return needs_service.system_prompt(language)


def render_intro(language: str) -> str:
    return intro_service.system_prompt(language)


def render_tikz_review(language: str) -> str:
    return tikz_review_service._system_prompt(language)


_RENDERERS: tuple[tuple[str, int, str | None, Callable[[], str]], ...] = (
    ("PROMPT 3 — dispense (grounding)", 3, None, render_content),
    ("PROMPT 4 — verifica", 4, None, render_assessment),
    ("PROMPT 5 — slide", 5, None, render_slides),
    ("PROMPT 6 — discorso", 6, None, render_speech),
    ("PROMPT 11 — immagine → Mermaid", 11, None, render_image_to_mermaid),
    ("PROMPT 12 — fix Mermaid IT", 12, None, lambda: render_fix("mermaid")),
    ("PROMPT 12 — fix Vega-Lite IT", 12, "_SYSTEM_VEGALITE_IT", lambda: render_fix("vegalite")),
    ("PROMPT 12 — fix DOT IT", 12, "_SYSTEM_DOT_IT", lambda: render_fix("dot")),
    ("PROMPT 12 — fix function IT", 12, "_SYSTEM_FUNCTION_IT", lambda: render_fix("function")),
    ("PROMPT 12 — fix tikz IT", 12, "_SYSTEM_TIKZ_IT", lambda: render_fix("tikz")),
    ("PROMPT 17 — revisore figura ↔ testo IT", 17, None, lambda: render_review("it")),
    (
        "PROMPT 17 — revisore figura ↔ testo EN",
        17,
        "_SYSTEM_REVIEW_EN",
        lambda: render_review("en"),
    ),
    ("PROMPT 18 — Vision delle figure di fonte IT", 18, None, lambda: render_describe("it")),
    (
        "PROMPT 18 — Vision delle figure di fonte EN",
        18,
        "_SYSTEM_DESCRIBE_EN",
        lambda: render_describe("en"),
    ),
    ("PROMPT 19 — ridondanze delle figure di fonte IT", 19, None, lambda: render_redundancy("it")),
    (
        "PROMPT 19 — ridondanze delle figure di fonte EN",
        19,
        "_SYSTEM_REDUNDANCY_EN",
        lambda: render_redundancy("en"),
    ),
    (
        "PROMPT 20 — pertinenza delle figure della letteratura IT",
        20,
        None,
        lambda: render_relevance("it", "relevance"),
    ),
    (
        "PROMPT 20 — pertinenza delle figure della letteratura EN",
        20,
        "_SYSTEM_RELEVANCE_EN",
        lambda: render_relevance("en", "relevance"),
    ),
    (
        "PROMPT 20 — termini di ricerca della letteratura IT",
        20,
        "_SYSTEM_QUERIES_IT",
        lambda: render_relevance("it", "queries"),
    ),
    (
        "PROMPT 20 — termini di ricerca della letteratura EN",
        20,
        "_SYSTEM_QUERIES_EN",
        lambda: render_relevance("en", "queries"),
    ),
    ("PROMPT 21 — revisione della resa TikZ IT", 21, None, lambda: render_tikz_review("it")),
    (
        "PROMPT 21 — revisione della resa TikZ EN",
        21,
        "_SYSTEM_RENDER_EN",
        lambda: render_tikz_review("en"),
    ),
    ("PROMPT 22 — fabbisogni di figure di fonte IT", 22, None, lambda: render_needs("it")),
    (
        "PROMPT 22 — fabbisogni di figure di fonte EN",
        22,
        "_SYSTEM_NEEDS_EN",
        lambda: render_needs("en"),
    ),
    ("PROMPT 23 — frase che introduce una figura IT", 23, None, lambda: render_intro("it")),
    (
        "PROMPT 23 — frase che introduce una figura EN",
        23,
        "_SYSTEM_INTRO_EN",
        lambda: render_intro("en"),
    ),
)


def build_checks() -> list[Check]:
    return [
        Check(label=label, prompt_number=number, rendered=render(), variant=variant)
        for label, number, variant, render in _RENDERERS
    ]


# ---------------------------------------------------------------------------
# Confronto
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return text.strip("\n")


def compare(doc: str, checks: Sequence[Check]) -> list[tuple[Check, str | None]]:
    """Per ogni check ritorna `(check, diff)`: `diff` è `None` se il blocco
    coincide, altrimenti il diff unificato (o il motivo dell'assenza)."""
    sections = split_sections(doc)
    results: list[tuple[Check, str | None]] = []
    for check in checks:
        section = sections.get(check.prompt_number)
        if section is None:
            results.append((check, f"sezione «# PROMPT {check.prompt_number}» assente"))
            continue
        documented = (
            variant_blocks(section).get(check.variant) if check.variant else system_block(section)
        )
        if documented is None:
            what = f"variante {check.variant}" if check.variant else "blocco ```text"
            results.append((check, f"{what} assente nella sezione"))
            continue
        expected = _normalize(check.rendered)
        actual = _normalize(documented)
        if expected == actual:
            results.append((check, None))
            continue
        diff = "\n".join(
            difflib.unified_diff(
                actual.splitlines(),
                expected.splitlines(),
                fromfile="docs/PROMPTS.md",
                tofile="codice",
                lineterm="",
                n=1,
            )
        )
        results.append((check, diff))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Confronta i blocchi verbatim di docs/PROMPTS.md con i prompt del codice."
    )
    parser.add_argument(
        "--docs",
        type=Path,
        default=DEFAULT_DOCS,
        help=f"Percorso di PROMPTS.md (default: {DEFAULT_DOCS}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    doc = args.docs.read_text(encoding="utf-8")
    failures = 0
    for check, diff in compare(doc, build_checks()):
        if diff is None:
            print(f"[OK]   {check.label} ({len(_normalize(check.rendered))} caratteri)")
            continue
        failures += 1
        print(f"[DIFF] {check.label}")
        print(diff)
    total = len(_RENDERERS)
    print(f"{total - failures}/{total} blocchi identici al codice")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
