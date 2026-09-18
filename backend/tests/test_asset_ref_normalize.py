"""`asset_ref_normalize` (D1, D2, D4): fixture condivisa con il frontend
(`tests/fixtures/asset_ref_normalize_cases.json`, specchio di
`lib/assetRefNormalize.ts`), invarianti del modulo, pin delle regex su
entrambi i lati e wiring della vista lezione.

- citazione in linea → rimando «Figura N» / «Tabella N» / «Lemma N» senza
  punto; ancora `[KIND:id]` su riga propria conservata, duplicati rimossi;
- guardia «parola-etichetta»: se la parola dell'etichetta precede già il
  tag, il rimando emette il solo numero («La figura [FIG:x]» → «La figura
  1»); confronto su parola intera, quindi il plurale resta fuori;
- chiave gestita senza ancora → UNA ancora inserita dopo il blocco della
  prima citazione (liste intere, fence e `$$` chiusi come unità opache);
- dentro fence, code span e math i tag sono citazioni, mai ancore;
- la coda (`cite_asset_refs`) riceve solo rimandi, mai blocchi (C9);
- tag non gestiti (id irrisolto, `[fig:x]`, kind senza numeri) byte-identici.

I test che leggono `frontend/src/lib/assetRefNormalize.ts` e la vista
falliscono (non saltano) finché la copia frontend non esiste: la parità è
un gate, non un'opzione.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from app.services import asset_ref_normalize as arn
from app.services import course_lesson_pdf_service as pdf
from app.services import figure_numbering as fn
from app.services import figure_theme as theme
from app.services.figure_theme import asset_ref, figure_labels

_FIXTURE = Path(__file__).parent / "fixtures" / "asset_ref_normalize_cases.json"
_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_CASES = _DATA["cases"]
_CITE = _DATA["cite"]
_REFS = _DATA["references"]
_FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
_FRONTEND_MODULE = _FRONTEND_SRC / "lib" / "assetRefNormalize.ts"
_FRONTEND_VIEW = (
    _FRONTEND_SRC / "pages" / "org" / "courses" / "components" / "LessonContentView.tsx"
)
# Numerazione e rimando condivisi dalle tre viste (WP8).
_FRONTEND_REFS = _FRONTEND_SRC / "lib" / "lessonAssetRefs.ts"
_LOCALES = _FRONTEND_SRC / "i18n" / "locales"

ASSET_REF_PATTERN = r"\[(FIG|TAB|EQ|EX):([^\]\n]+)\]"
ANCHOR_LINE_PATTERN = r"^[ \t]*\[(FIG|TAB|EQ|EX):([^\]\n]+)\][ \t]*\r?$"
# Confine di parola della guardia «parola-etichetta»: unica regola del
# modulo che `\w` non può esprimere allo stesso modo nei due linguaggi,
# quindi è scritta con gli escape e fissata qui su entrambi i lati.
WORD_CHAR_PATTERN = r"[0-9A-Za-z_\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f]"

# Esegue la copia frontend sulla stessa fixture (pattern di
# `test_figure_numbering.py`): `assetRefNormalize.ts` non ha import a
# runtime, quindi Node ≥ 22.6 la carica con `--experimental-strip-types`.
# La callable è la stessa del runner Python: template per kind, `THM` con
# la parola del kind per le equazioni elencate in `theorem_kinds`.
_FRONTEND_RUNNER = """
import * as m from {module!r};
import {{ readFileSync }} from "node:fs";
const data = JSON.parse(readFileSync({fixture!r}, "utf8"));
const toMaps = (numbers) => Object.fromEntries(
  Object.entries(numbers).map(([k, v]) => [k, new Map(Object.entries(v))]),
);
const makeRef = (refs, theoremKinds) => (kind, idLower, n) => {{
  const word = kind === "EQ" ? theoremKinds[idLower] : undefined;
  if (word) return refs.THM.replace("{{{{kind}}}}", word).replace("{{{{n}}}}", String(n));
  return refs[kind].replace("{{{{n}}}}", String(n));
}};
const out = {{ cases: [], cite: [] }};
for (const c of data.cases) {{
  const refs = {{ ...data.references, ...(c.references || {{}}) }};
  out.cases.push(m.normalizeAssetRefs(c.markdown, {{
    numbers: toMaps(c.numbers), reference: makeRef(refs, c.theorem_kinds || {{}}),
  }}));
}}
for (const c of data.cite) {{
  out.cite.push(m.citeAssetRefs(c.text, {{
    numbers: toMaps(c.numbers), reference: makeRef(data.references, c.theorem_kinds || {{}}),
  }}));
}}
process.stdout.write(JSON.stringify(out));
"""


def _reference(case: Mapping[str, Any]) -> Callable[[str, str, int], str]:
    refs = {**_REFS, **(case.get("references") or {})}
    theorem_kinds: Mapping[str, str] = case.get("theorem_kinds") or {}

    def reference(kind: str, id_lower: str, n: int) -> str:
        word = theorem_kinds.get(id_lower) if kind == "EQ" else None
        if word:
            return refs["THM"].replace("{{kind}}", word).replace("{{n}}", str(n))
        return refs[kind].replace("{{n}}", str(n))

    return reference


def _normalize(case: Mapping[str, Any], markdown: str | None = None) -> str:
    return arn.normalize_asset_refs(
        case["markdown"] if markdown is None else markdown,
        numbers=case["numbers"],
        reference=_reference(case),
    )


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_fixture_case(case: dict) -> None:
    assert _normalize(case) == case["normalized"]


@pytest.mark.parametrize("case", _CITE, ids=[c["name"] for c in _CITE])
def test_cite_fixture_case(case: dict) -> None:
    got = arn.cite_asset_refs(case["text"], numbers=case["numbers"], reference=_reference(case))
    assert got == case["cited"]


def _handled_key(
    numbers: Mapping[str, Mapping[str, int]], m: re.Match[str]
) -> tuple[str, str] | None:
    key = arn._trim(m.group(2)).lower()
    if key in (numbers.get(m.group(1)) or {}):
        return (m.group(1), key)
    return None


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_every_handled_id_has_exactly_one_anchor_and_no_inline_tag(case: dict) -> None:
    """Oracolo diretto di «un solo blocco per asset, nessuna frase spezzata»:
    nell'output ogni tag gestito fuori dalle regioni opache sta su una
    riga-ancora, e ogni chiave gestita presente nell'input ha esattamente
    una riga-ancora."""
    numbers = case["numbers"]
    out = _normalize(case)
    lines = out.split("\n")
    rstart, _rend = arn._opaque_regions(lines)
    anchors: dict[tuple[str, str], int] = {}
    for i, line in enumerate(lines):
        if rstart[i] != -1:
            continue
        for m in arn.ASSET_REF_RE.finditer(line):
            key = _handled_key(numbers, m)
            if key is None:
                continue
            assert arn.ANCHOR_LINE_RE.match(line), (case["name"], line)
            anchors[key] = anchors.get(key, 0) + 1
    expected = {
        key
        for m in arn.ASSET_REF_RE.finditer(case["markdown"])
        if (key := _handled_key(numbers, m)) is not None
    }
    assert set(anchors) == expected, case["name"]
    assert all(n == 1 for n in anchors.values()), (case["name"], anchors)


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_normalization_is_idempotent(case: dict) -> None:
    once = _normalize(case)
    assert _normalize(case, once) == once


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_identity_without_handled_numbers(case: dict) -> None:
    """Senza numeri (kind vuoti) ogni markdown torna byte-identico: spazi,
    `\\r` e righe vuote compresi."""
    empty = {"FIG": {}, "TAB": {}, "EQ": {}, "EX": {}}
    assert (
        arn.normalize_asset_refs(case["markdown"], numbers=empty, reference=_reference(case))
        == (case["markdown"])
    )
    assert (
        arn.normalize_asset_refs(case["markdown"], numbers={}, reference=_reference(case))
        == (case["markdown"])
    )


def test_regexes_are_pinned_in_python() -> None:
    """Stessa regex del PDF e di `figure_numbering`: un tag è un tag per
    tutti o per nessuno (non attraversa la riga, kind maiuscolo). Il
    confine della guardia «parola-etichetta» è fissato nella forma con gli
    escape, la sola che Python e JavaScript scrivono identica."""
    assert arn.ASSET_REF_RE.pattern == ASSET_REF_PATTERN
    assert pdf._ASSET_REF_RE.pattern == ASSET_REF_PATTERN
    assert fn.ASSET_REF_RE.pattern == ASSET_REF_PATTERN
    assert arn.ANCHOR_LINE_RE.pattern == ANCHOR_LINE_PATTERN
    assert arn.ANCHOR_LINE_RE.match("  [FIG:a]  \r")
    assert arn.ANCHOR_LINE_RE.match("- [FIG:a]") is None
    assert arn.ANCHOR_LINE_RE.match("[FIG:a] testo") is None
    assert arn._WORD_CHAR_RE.pattern == WORD_CHAR_PATTERN
    for pattern in (ASSET_REF_PATTERN, ANCHOR_LINE_PATTERN, WORD_CHAR_PATTERN):
        assert "\\s" not in pattern and "\\d" not in pattern


def test_frontend_module_pins_the_same_regexes_and_has_no_runtime_imports() -> None:
    """La copia frontend usa le stesse regex letterali e nessun import a
    runtime (solo `import type`), così il runner Node la carica."""
    assert _FRONTEND_MODULE.is_file(), f"copia frontend assente: {_FRONTEND_MODULE}"
    source = _FRONTEND_MODULE.read_text(encoding="utf-8")
    assert "/\\[(FIG|TAB|EQ|EX):([^\\]\\n]+)\\]/g" in source
    assert "/^[ \\t]*\\[(FIG|TAB|EQ|EX):([^\\]\\n]+)\\][ \\t]*\\r?$/" in source
    # Confine della guardia: stessa riga sui due lati, escape compresi.
    assert f"/{WORD_CHAR_PATTERN}/" in source
    runtime_imports = [
        line
        for line in source.splitlines()
        if line.startswith("import ") and not line.startswith("import type ")
    ]
    assert not runtime_imports, runtime_imports


def test_frontend_copy_matches_fixture() -> None:
    """Parità BE/FE eseguita davvero: `lib/assetRefNormalize.ts` deve
    produrre gli stessi `normalized`/`cited` della fixture."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node non disponibile: parità frontend non eseguibile")
    assert _FRONTEND_MODULE.is_file(), f"copia frontend assente: {_FRONTEND_MODULE}"
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
        assert fe == case["normalized"], case["name"]
    for case, fe in zip(_CITE, got["cite"], strict=True):
        assert fe == case["cited"], case["name"]


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}{key}."))
    elif isinstance(node, str):
        out[prefix[:-1]] = node
    return out


def test_reference_label_has_no_trailing_punctuation_and_values_mirror_locale() -> None:
    """Il rimando non porta punteggiatura (la frase dell'autore la ha già) e
    il valore dei locale frontend è identico a quello del backend (lo
    specchio confronta solo le chiavi)."""
    ref_keys = [
        "courses.figures.ref",
        "courses.figures.table.ref",
        "courses.figures.equation.ref",
        "courses.figures.example.ref",
        "courses.figures.theorem.ref",
    ]
    for lang in ("it", "en"):
        flat = _flatten(json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8")))
        for key in ref_keys:
            text = theme.FIGURE_I18N[lang][key]
            assert "{{n}}" in text, (lang, key)
            assert text[-1] not in ".:;", (lang, key)
            assert flat[key] == text, (lang, key)
    it, en, de = figure_labels("it"), figure_labels("en"), figure_labels("de")
    assert asset_ref(it, "FIG", 2) == "Figura 2"
    assert asset_ref(en, "FIG", 2) == "Figure 2"
    assert asset_ref(de, "FIG", 2) == "Figura 2"
    assert asset_ref(it, "TAB", 1) == "Tabella 1"
    assert asset_ref(en, "EQ", 3) == "Equation 3"
    assert asset_ref(it, "EX", 4) == "Esempio 4"
    assert asset_ref(it, "THM", 2, kind_word="Lemma") == "Lemma 2"


def _strip_comments(path: Path) -> str:
    source = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
    return re.sub(r"(?<![:\"'])//[^\n]*", " ", source)


def test_frontend_view_wires_the_normalizer() -> None:
    """La vista lezione normalizza il corpo DOPO aver numerato il corpo non
    normalizzato e cita la coda; senza, C2/C9 tornerebbero solo a video o i
    numeri cambierebbero nel caso «più id nello stesso paragrafo». Da WP8
    numeri e callable vengono da `lib/lessonAssetRefs.ts`, condiviso con le
    viste slide e discorso: è lì che vive l'ordine numerazione → rimandi e
    le chiavi `t()` letterali."""
    assert _FRONTEND_VIEW.is_file(), f"vista frontend assente: {_FRONTEND_VIEW}"
    source = _strip_comments(_FRONTEND_VIEW)
    assert "@/lib/assetRefNormalize" in source
    assert "@/lib/lessonAssetRefs" in source
    assert "normalizeAssetRefs(body" in source
    assert "citeAssetRefs(tail" in source

    assert _FRONTEND_REFS.is_file(), f"modulo dei rimandi assente: {_FRONTEND_REFS}"
    refs = _strip_comments(_FRONTEND_REFS)
    numbers_at = refs.index("computeAssetNumbers(body")
    cite_at = refs.index("citeAssetRefs(text")
    assert numbers_at < cite_at, "la numerazione va calcolata sul corpo NON normalizzato"
    for key in (
        "courses.figures.ref",
        "courses.figures.table.ref",
        "courses.figures.equation.ref",
        "courses.figures.example.ref",
        "courses.figures.theorem.ref",
    ):
        assert f't("{key}"' in refs, key


def test_fixture_covers_the_required_scenarios() -> None:
    """Guardia contro una fixture svuotata: i casi richiesti dal progetto
    (B1 e riconciliazioni dei quattro kind) devono esserci per nome."""
    names = " ".join(c["name"] for c in _CASES) + " " + " ".join(c["name"] for c in _CITE)
    for needle in (
        "prima dell'ancora",
        "ancora prima",
        "sole citazioni",
        "due ancore",
        "lista compatta",
        "lista sciolta",
        "fence",
        "code span",
        "$$",
        "irrisolto",
        "[fig:x]",
        "spazi e case",
        "a cavallo",
        "tabella",
        "blockquote",
        "heading",
        "CRLF",
        "coda",
        "«Tabella 1»",
        "«Equazione 2»",
        "«Esempio 1»",
        "«Lemma 2»",
        "[TAB:x] dentro code span",
        "quattro kind",
        "kind senza numeri",
        "«Figura» gia' presente",
        "guardia: «La figura [FIG:x]» reale",
        "guardia: «La tabella [TAB:x]» reale",
        "guardia in inglese",
        "famiglia teorema",
        "il plurale non corrisponde",
        "punteggiatura fra parola e tag",
        "confine di parola",
        "coda: guardia",
    ):
        assert needle in names, needle
    assert len(_CASES) >= 55 and len(_CITE) >= 6


def _real_reference(language: str, theorem_word: str | None = None) -> arn.Reference:
    """La callable vera del PDF: etichette dai locale, ramo THM per le
    equazioni in famiglia teorema (`_asset_reference_fn`)."""
    labels = figure_labels(language)

    def reference(kind: str, id_lower: str, n: int) -> str:
        if theorem_word is not None and kind == "EQ":
            return asset_ref(labels, "THM", n, kind_word=theorem_word)
        return asset_ref(labels, kind, n)

    return reference


# (testo, numeri, callable, prima riga attesa): le prime due frasi sono
# quelle dell'export del docente (M2.L2 e M12.L7), dove il rimando dava
# «La figura Figura 13» e «La tabella Tabella 1».
_GUARD_CASES = [
    (
        "La figura [FIG:fig_avvicinamenti_sequenziali] rappresenta le successioni.",
        {"FIG": {"fig_avvicinamenti_sequenziali": 13}},
        _real_reference("it"),
        "La figura 13 rappresenta le successioni.",
    ),
    (
        "La tabella [TAB:tab_theorem_comparison] raccoglie le differenze.",
        {"TAB": {"tab_theorem_comparison": 1}},
        _real_reference("it"),
        "La tabella 1 raccoglie le differenze.",
    ),
    (
        "The figure [FIG:a] shows the trend.",
        {"FIG": {"a": 2}},
        _real_reference("en"),
        "The figure 2 shows the trend.",
    ),
    (
        "The table [TAB:t] compares the three theorems.",
        {"TAB": {"t": 1}},
        _real_reference("en"),
        "The table 1 compares the three theorems.",
    ),
    (
        "Come dice il lemma [EQ:eq_x], la serie converge.",
        {"EQ": {"eq_x": 2}},
        _real_reference("it", "Lemma"),
        "Come dice il lemma 2, la serie converge.",
    ),
    (
        "Come mostrato in [FIG:a], il sistema converge.",
        {"FIG": {"a": 1}},
        _real_reference("it"),
        "Come mostrato in Figura 1, il sistema converge.",
    ),
    (
        "Le figure [FIG:a] e le altre.",
        {"FIG": {"a": 1}},
        _real_reference("it"),
        "Le figure Figura 1 e le altre.",
    ),
    (
        "Figura [FIG:a] mostra il ciclo.",
        {"FIG": {"a": 3}},
        _real_reference("it"),
        "Figura 3 mostra il ciclo.",
    ),
    (
        "La figura, [FIG:a], converge.",
        {"FIG": {"a": 1}},
        _real_reference("it"),
        "La figura, Figura 1, converge.",
    ),
    (
        "Il sistema si configura [FIG:a] come indicato.",
        {"FIG": {"a": 1}},
        _real_reference("it"),
        "Il sistema si configura Figura 1 come indicato.",
    ),
]


@pytest.mark.parametrize(
    ("text", "numbers", "reference", "expected"), _GUARD_CASES, ids=range(len(_GUARD_CASES))
)
def test_the_label_word_before_the_tag_is_not_repeated(
    text: str,
    numbers: Mapping[str, Mapping[str, int]],
    reference: arn.Reference,
    expected: str,
) -> None:
    """Guardia «parola-etichetta» con le etichette vere dei locale: quando
    la parola del kind precede il tag a meno di spazi, il rimando emette il
    solo numero. Il confronto è su parola INTERA (il plurale e «configura»
    non corrispondono) e la punteggiatura fra parola e tag lo annulla.
    Stesso esito nelle due funzioni, cioè nel corpo e nella coda."""
    assert arn.cite_asset_refs(text, numbers=numbers, reference=reference) == expected
    normalized = arn.normalize_asset_refs(text, numbers=numbers, reference=reference)
    assert normalized.split("\n")[0] == expected


def test_the_label_word_comes_from_the_reference_not_from_a_word_list() -> None:
    """La parola confrontata è quella che la callable stessa mette prima del
    numero: con un rimando inventato la guardia segue quello, non un elenco
    italiano scritto nel modulo."""

    def reference(kind: str, id_lower: str, n: int) -> str:
        return f"Schizzo {n}"

    numbers = {"FIG": {"a": 4}}
    assert (
        arn.cite_asset_refs("Lo schizzo [FIG:a] chiarisce.", numbers=numbers, reference=reference)
        == "Lo schizzo 4 chiarisce."
    )
    assert (
        arn.cite_asset_refs("La figura [FIG:a] chiarisce.", numbers=numbers, reference=reference)
        == "La figura Schizzo 4 chiarisce."
    )


def test_reference_callable_receives_kind_id_and_number() -> None:
    """La callable riceve `(kind, id_lower, n)`: l'id serve al ramo teorema
    (`[EQ:lem]` → «Lemma 2»), il numero è un dato mai ricalcolato."""
    seen: list[tuple[str, str, int]] = []

    def reference(kind: str, id_lower: str, n: int) -> str:
        seen.append((kind, id_lower, n))
        return f"<{kind}:{id_lower}:{n}>"

    out = arn.normalize_asset_refs(
        "Vedi [EQ: LEM ] e [TAB:t].",
        numbers={"EQ": {"lem": 7}, "TAB": {"t": 1}},
        reference=reference,
    )
    assert out == "Vedi <EQ:lem:7> e <TAB:t:1>.\n\n[EQ:LEM]\n\n[TAB:t]"
    assert seen == [("EQ", "lem", 7), ("TAB", "t", 1)]
