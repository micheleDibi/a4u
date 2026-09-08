"""`figure_numbering` (D4, Q2): casi della fixture condivisa con il frontend
(`tests/fixtures/figure_numbering_cases.json`, specchio di
`lib/figureNumbering.ts`) e proprietà del modulo.

- prima citazione → N crescente; citazioni ripetute → stesso N;
- id senza asset → nessun numero consumato;
- `FIG` case-sensitive (`[fig:x]` ignorato), id case-insensitive;
- asset non citati accodati dopo il testo in ordine di array (A12) e
  numerati dopo le citate;
- `strip_figure_prefix` con cifra obbligatoria e separatore obbligatorio
  dopo il numero (o fine del testo), solo a render: mai «lossy» su una
  frase («Figure 2 shows the flow»).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import figure_numbering as fn

_FIXTURE = Path(__file__).parent / "fixtures" / "figure_numbering_cases.json"
_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_CASES = _DATA["cases"]
_STRIP = _DATA["strip_prefix"]
_FRONTEND_MODULE = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "figureNumbering.ts"
)

# Esegue la copia frontend sulla stessa fixture: `figureNumbering.ts` non ha
# import, quindi Node ≥ 22.6 la carica con `--experimental-strip-types`.
_FRONTEND_RUNNER = """
import * as fn from {module!r};
import {{ readFileSync }} from "node:fs";
const data = JSON.parse(readFileSync({fixture!r}, "utf8"));
const out = {{ cases: [], strip_prefix: [] }};
for (const c of data.cases) {{
  const appended = fn.appendUncitedFigureRefs(c.markdown, c.asset_ids);
  out.cases.push({{
    appended,
    numbers: Object.fromEntries(fn.computeFigureNumbers(appended, c.asset_ids)),
  }});
}}
for (const p of data.strip_prefix) out.strip_prefix.push(fn.stripFigurePrefix(p.input));
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_fixture_case(case: dict) -> None:
    appended = fn.append_uncited_figure_refs(case["markdown"], case["asset_ids"])
    assert appended == case["appended"]
    assert fn.compute_figure_numbers(appended, case["asset_ids"]) == case["numbers"]


@pytest.mark.parametrize("pair", _STRIP, ids=[p["input"] or "vuoto" for p in _STRIP])
def test_strip_prefix_fixture(pair: dict) -> None:
    assert fn.strip_figure_prefix(pair["input"]) == pair["output"]


def test_fixture_covers_the_required_scenarios() -> None:
    """La fixture deve contenere i casi richiesti dal piano (duplicati, id
    mancante, case diverso, `[fig:x]` ignorato, non citati in coda, nessuna
    figura): guardia contro una fixture svuotata."""
    names = " ".join(c["name"] for c in _CASES)
    for needle in (
        "ripetute",
        "senza asset",
        "case diverso",
        "[fig:x]",
        "non citati",
        "nessuna figura",
    ):
        assert needle in names, needle


def test_fig_ref_re_is_case_sensitive_on_fig_and_stops_at_newline() -> None:
    assert fn.FIG_REF_RE.pattern == r"\[FIG:([^\]\n]+)\]"
    assert fn.FIG_REF_RE.search("[FIG:a1]") is not None
    assert fn.FIG_REF_RE.search("[fig:a1]") is None
    assert fn.FIG_REF_RE.search("[Fig:a1]") is None
    assert fn.FIG_REF_RE.search("[FIG:a\n1]") is None
    assert fn.FIG_REF_RE.search("[FIG:]") is None


def test_numbers_are_bound_to_ids_not_occurrences() -> None:
    md = "[FIG:A] [FIG:B] [FIG:A] [FIG:C] [FIG:B]"
    numbers = fn.compute_figure_numbers(md, ["A", "B", "C"])
    assert numbers == {"a": 1, "b": 2, "c": 3}
    assert list(numbers) == ["a", "b", "c"]  # ordine di prima citazione


def test_missing_ids_never_shift_the_numbers() -> None:
    md = "[FIG:x] [FIG:y] [FIG:A] [FIG:z] [FIG:B]"
    assert fn.compute_figure_numbers(md, ["A", "B"]) == {"a": 1, "b": 2}


def test_append_is_idempotent_and_preserves_declared_case() -> None:
    once = fn.append_uncited_figure_refs("Testo.", ["Fig_A", "fig_b"])
    assert once == "Testo.\n\n[FIG:Fig_A]\n\n[FIG:fig_b]"
    assert fn.append_uncited_figure_refs(once, ["Fig_A", "fig_b"]) == once


def test_uncited_are_numbered_after_cited_when_computed_on_appended_text() -> None:
    md = "Solo [FIG:C]."
    ids = ["A", "B", "C"]
    appended = fn.append_uncited_figure_refs(md, ids)
    assert fn.compute_figure_numbers(appended, ids) == {"c": 1, "a": 2, "b": 3}
    # Sul testo NON appeso gli orfani non hanno numero: la coda deve essere
    # calcolata prima della numerazione.
    assert fn.compute_figure_numbers(md, ids) == {"c": 1}


def test_cited_figure_ids_normalizes_and_deduplicates() -> None:
    assert fn.cited_figure_ids("[FIG: A ] [FIG:a] [FIG:B]") == ["a", "b"]
    assert fn.cited_figure_ids("") == []


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("Figura 7. Schema", "Schema"),
        ("FIGURA 7. Schema", "Schema"),
        ("Figure 10— Long dash", "Long dash"),
        ("Fig.3:Compatto", "Compatto"),
        ("Figura 7. Figura 8. Doppio", "Figura 8. Doppio"),  # una sola rimozione
        ("Figura", "Figura"),
        ("Figura X. Non numerata", "Figura X. Non numerata"),
        ("Figurine 3 pezzi", "Figurine 3 pezzi"),
        # Separatore obbligatorio: senza, il numero fa parte della frase.
        ("Figure 2 shows the flow", "Figure 2 shows the flow"),
        ("Figura 3 e 4 a confronto", "Figura 3 e 4 a confronto"),
        ("Figura 1.2 Schema", "Figura 1.2 Schema"),  # «1.» non è un separatore
        ("Figura 1.2: Schema", "Schema"),
        ("Figura 3.", ""),
        ("Figura 3   ", ""),
        ("Fig. 10b) Dettaglio", "Dettaglio"),
        ("Figura 3. 2 casi", "2 casi"),  # separatore seguito da spazio, poi cifra
    ],
)
def test_strip_prefix_edge_cases(caption: str, expected: str) -> None:
    assert fn.strip_figure_prefix(caption) == expected


def test_strip_prefix_handles_none_like_input() -> None:
    assert fn.strip_figure_prefix("") == ""
    assert fn.strip_figure_prefix(None) == ""  # type: ignore[arg-type]


def test_frontend_copy_matches_fixture() -> None:
    """Parità BE/FE eseguita davvero: la copia `lib/figureNumbering.ts` deve
    produrre gli stessi `appended`/`numbers` e lo stesso `strip_prefix`
    della fixture (cifre Unicode comprese: `\\d` Python = `\\p{Nd}` con `u`).
    Salta con motivo esplicito se manca `node` (≥ 22.6) o il frontend."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node non disponibile: parità frontend non eseguibile")
    if not _FRONTEND_MODULE.is_file():
        pytest.skip(f"copia frontend assente: {_FRONTEND_MODULE}")
    script = _FRONTEND_RUNNER.format(module=str(_FRONTEND_MODULE), fixture=str(_FIXTURE))
    proc = subprocess.run(
        [node, "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0 and "strip-types" in proc.stderr:
        pytest.skip(f"node senza --experimental-strip-types: {proc.stderr.strip()[:200]}")
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    for case, fe in zip(_CASES, got["cases"], strict=True):
        assert fe["appended"] == case["appended"], case["name"]
        assert fe["numbers"] == case["numbers"], case["name"]
    for pair, fe in zip(_STRIP, got["strip_prefix"], strict=True):
        assert fe == pair["output"], pair["input"]


def test_uncited_refs_skip_ids_the_token_cannot_carry() -> None:
    """`asset_id` è solo `str` 1..50 e un PATCH manuale può salvare `A]`: la
    coda `[FIG:A]]` veniva letta come `A` e produceva un «Asset non trovato»
    falso più un `]` orfano, invece della figura (COR-4). Gli id che il
    token non rilegge esattamente vengono saltati."""
    out = fn.append_uncited_figure_refs("Testo.", ["A]", " ", "C\nD", "[FIG:E]", "B"])
    assert out == "Testo.\n\n[FIG:B]"
    # Gli id ordinari (anche con parentesi quadre aperte) restano.
    assert fn.append_uncited_figure_refs("T.", ["A[1", "F_2"]) == "T.\n\n[FIG:A[1]\n\n[FIG:F_2]"
