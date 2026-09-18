"""Costo dei gate dei grafi limitato per costruzione (WP5, tetti di risorsa).

Oracolo che falliva prima della correzione (codice di f92a194), misurato in
processi figli: `MermaidRenderer.validate` su `erDiagram` con una riga `A[`
seguita da spazi durava 5,3 s a 2.400 caratteri e 42 s a 4.800 (regex del
blocco ER cubica, sotto il tetto A1 di 12.000); `flowchart TD` con una riga
`A` + 12.000 spazi 4,4 s (`_LINK_RE` di `graph_rules` e la coda di
`_MERMAID_TRIM_RE` del gate statico, entrambe quadratiche); `classDiagram`
con una corsa di `-` 3,2 s (relazione di classe con quantificatori pigri);
note e partecipanti della sequence e note dello state 1,4-1,9 s; `<a `
ripetuto fino a 1,2 s (`_HTML_TAG_RE.search` riprovata da ogni `<`);
12.000 caratteri di `\\"` in un DOT 0,42 s; un'etichetta `&#` seguita da
4.400 cifre sollevava `ValueError` in entrambi i formati. Il gate gira
sincrono sull'event loop del worker (`asset_validation_service`: 5,75 s di
blocco con cinque sorgenti da 2.400 caratteri) e in un thread senza timeout
nel PATCH: un sorgente sotto il tetto dello schema teneva occupati per
minuti l'uno o l'altro.

- I sorgenti dei finding, al tetto A1, in un processo figlio: meno di
  0,25 s ciascuno, meno di 300 MB in tutto, nessuna eccezione.
- Una scansione per tipo × inizio di riga × carattere ripetuto, sulla
  misura di entrambi i formati e sul `validate` Mermaid: nessun caso oltre
  0,25 s (dopo la correzione il peggiore sta sotto 0,1 s).
- Le letture riscritte danno gli stessi risultati di quelle con il
  backtracking, confrontate su input corti casuali ed esaustivi.
- La lettura Mermaid si ferma a `MAX_MERMAID_MEASURED_CHARS`, uguale al
  tetto A1: un sorgente ammesso dallo schema è letto intero.

Giro 1 della coda (V1-F1, codice di b51c888): un `\r` dentro una riga di un
`sankey-beta` faceva sollevare `csv.Error` a `validate` (500 nel PATCH,
lezione rigenerata dal worker); né le scansioni né i casi lo coprivano. Ora
le scansioni hanno `\r` fra i caratteri ripetuti e il sankey fra i tipi
del `validate`, due casi al tetto lo ripetono e sorgenti corti casuali su
tutti i tipi non sollevano mai.
"""

from __future__ import annotations

import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.schemas.course_lesson_content import VISUAL_ASSET_CONTENT_MAX_CHARS
from app.services import figure_render_service as frs
from app.services.figure_compute import graph_rules as gr
from app.services.figure_compute.graph_rules import GRAPH_TOO_DENSE, check_graph_rules

_BACKEND = Path(__file__).resolve().parents[1]
_SECONDS = 0.25
_RSS_MB = 300.0
# Oltre questi limiti il figlio è fermato: sul codice di prima un solo
# blocco ER al tetto richiedeva minuti.
_KILL_SECONDS = 60.0
_KILL_RSS_MB = 1500.0
_SIZE = VISUAL_ASSET_CONTENT_MAX_CHARS
# Cifre oltre il limite di conversione degli interi (4.300 di default).
_DIGITS = (sys.get_int_max_str_digits() or 4300) + 100


def _fill(prefix: str, pump: str, suffix: str, total: int = _SIZE) -> str:
    return prefix + pump * ((total - len(prefix) - len(suffix)) // len(pump)) + suffix


# In ordine: i quadratici prima, i cubici del blocco ER per ultimi (sul
# codice di prima il figlio è fermato lì, e gli altri casi sono già misurati).
PAYLOADS: dict[str, tuple[str, str]] = {
    "flowchart_spazi": ("mermaid", _fill("flowchart TD\nA", " ", "B")),
    "flowchart_tab": ("mermaid", _fill("flowchart TD\nA", "\t", "B")),
    "flowchart_nbsp": ("mermaid", _fill("flowchart TD\nA", "\xa0", "B")),
    "flowchart_bom": ("mermaid", _fill("flowchart TD\nA", "\ufeff", "B")),
    "flowchart_punti": ("mermaid", _fill("flowchart TD\nA", ".", "")),
    "flowchart_tag_aperti": ("mermaid", _fill("flowchart TD\nA", "<a ", "")),
    "block_spazi": ("mermaid", _fill("block-beta\nA", " ", "B")),
    "class_trattini": ("mermaid", _fill("classDiagram\n", "-", " x")),
    "class_doppi_trattini": ("mermaid", _fill("classDiagram\n", "--", " x y")),
    "class_punti": ("mermaid", _fill("classDiagram\n", ".", " x")),
    "class_due_punti": ("mermaid", _fill("classDiagram\nA", "-", " x :")),
    "sequence_partecipante": ("mermaid", _fill("sequenceDiagram\nactor x", " ", " x")),
    "sequence_nota": ("mermaid", _fill("sequenceDiagram\nnote left of ", " ", "!")),
    "state_nota": ("mermaid", _fill("stateDiagram-v2\nnote left of ", " ", "!")),
    "er_relazione": ("mermaid", _fill("erDiagram\nA ", "-", " B")),
    "treemap_valore": ("mermaid", _fill('treemap-beta\n"a', " ", "b")),
    "mermaid_entita": ("mermaid", "flowchart TD\nA[&#" + "1" * _DIGITS + ";] --> B"),
    "dot_virgolette_escapate": ("dot", _fill('digraph { "', '\\"', " x }")),
    "dot_html_aperto": ("dot", _fill("digraph { a [label=<", "<", "] }")),
    "dot_entita": ("dot", 'digraph { a [label="&#' + "1" * _DIGITS + '"]; a -> b }'),
    # Giro 1 della coda (V1-F1): `\r` isolato nel CSV, prima `csv.Error`.
    "sankey_a_capo_cr": ("mermaid", _fill("sankey-beta\n", "A,B,1\r", "")),
    "sankey_cr_nel_campo": ("mermaid", _fill("sankey-beta\nA", "\r", ",B,1")),
    "er_blocco_graffa": ("mermaid", _fill("erDiagram\nA ", " ", "x {")),
    "er_blocco_spazi": ("mermaid", _fill("erDiagram\nA[", " ", "x")),
    "er_blocco_tab": ("mermaid", _fill("erDiagram\nA[", "\t", "x")),
}
_ENTITIES = ("mermaid_entita", "dot_entita")

_PUMPS = [
    " ",
    "\t",
    "\xa0",
    "-",
    ".",
    "=",
    "<",
    "[",
    "(",
    '"',
    ":",
    "- ",
    " as",
    '\\"',
    "<a ",
    "&#1",
]
SWEEPS: dict[str, dict[str, Any]] = {
    # Misura di `graph_rules`, tutti i tipi con una regola di conteggio.
    "misura_mermaid": {
        "call": "metrics",
        "fmt": "mermaid",
        "heads": [
            "flowchart TD",
            "graph LR",
            "block-beta",
            "sequenceDiagram",
            "stateDiagram-v2",
            "classDiagram",
            "erDiagram",
            "mindmap",
            "treemap-beta",
            "timeline",
            "pie",
            "quadrantChart",
            "radar-beta",
            "xychart-beta",
            "sankey-beta",
            "gantt",
        ],
        "prefixes": [
            "",
            "A",
            "A[",
            "A -- ",
            "A@{ label: ",
            "participant x",
            "actor x",
            "note left of ",
            "note over ",
            "state ",
            "A --> B : ",
            "class A",
            "note for A ",
            "A <|-- ",
            'A "1" ',
            "A ||--o{ B",
            "A {",
            "a(",
            '"a',
            "title ",
            "section ",
            "x-axis ",
            "axis ",
            "a: after ",
            "a:",
        ],
        # `\r` isolato: a capo per Mermaid, prima `csv.Error` nel sankey.
        "pumps": [*_PUMPS, "\r", "1,\r", "a\rb"],
    },
    "misura_dot": {
        "call": "metrics",
        "fmt": "dot",
        "heads": [""],
        "prefixes": [
            "digraph { ",
            "digraph { a [label=",
            'digraph { a [label="',
            'digraph { a [shape=record label="',
            "digraph { a [label=<",
            "strict graph { ",
            "digraph { a -> ",
            'digraph { "',
            "digraph { /*",
            "digraph { subgraph ",
        ],
        "pumps": [*_PUMPS, "{ ", "-> {", "a -> ", "subgraph {"],
    },
    # `validate` intero: gate statico (frontmatter, tag, shape, statement
    # con URL) e misura, sulle famiglie che il gate legge statement per
    # statement.
    "validate_mermaid": {
        "call": "validate",
        "fmt": "mermaid",
        "heads": [
            "flowchart TD",
            "classDiagram",
            "sequenceDiagram",
            "stateDiagram-v2",
            "mindmap",
            "sankey-beta",
        ],
        "prefixes": ["", "A", "A@{ label: ", "<a ", "click ", "%%{", "A[", "A,B,1"],
        "pumps": [*_PUMPS, "\ufeff", "<a", " <", ";", "'", "%%", "\r", "@{"],
    },
}

_CHILD = r"""
import json, os, resource, sys, threading, time

kill_rss_mb = float(sys.argv[2])
with open(sys.argv[1], encoding="utf-8") as fh:
    job = json.load(fh)


def rss_mb():
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 2**20 if sys.platform == "darwin" else raw / 2**10


def watchdog():
    while True:
        if rss_mb() > kill_rss_mb:
            print("COST " + json.dumps({"killed": f"rss {rss_mb():.0f} MB"}), flush=True)
            os._exit(3)
        time.sleep(0.02)


from app.services import figure_render_service as frs
from app.services.figure_compute import graph_rules as gr

threading.Thread(target=watchdog, daemon=True).start()


def call(kind, fmt, source):
    if kind == "metrics":
        return gr.graph_source_metrics(fmt, source)
    return frs.REGISTRY[fmt].validate(source, deep=False)


def timed(kind, fmt, source):
    started = time.perf_counter()
    result = call(kind, fmt, source)
    return time.perf_counter() - started, result


if job["mode"] == "payloads":
    for name, fmt, source in job["cases"]:
        try:
            seconds, result = timed("validate", fmt, source)
            row = {"seconds": seconds, "result": [result[0], result[1][:160]]}
        except Exception as exc:
            row = {"raised": repr(exc)[:200]}
        print("COST " + json.dumps({"case": name, **row}), flush=True)
else:
    sweep, bound, size = job["sweep"], job["bound"], job["size"]
    worst, count = [], 0
    for head in sweep["heads"]:
        for prefix in sweep["prefixes"]:
            for pump in sweep["pumps"]:
                base = f"{head}\n{prefix}" if head else prefix
                source = base + pump * ((size - len(base) - 2) // len(pump)) + " x"
                seconds, _ = timed(sweep["call"], sweep["fmt"], source)
                if seconds > bound:  # rumore: vale il minimo di tre misure
                    seconds = min(seconds, *(timed(sweep["call"], sweep["fmt"], source)[0]
                                             for _ in range(2)))
                worst.append([seconds, head, prefix, pump])
                count += 1
    worst.sort(reverse=True)
    print("COST " + json.dumps({"cases": count, "worst": worst[:5]}), flush=True)
print("COST " + json.dumps({"rss_mb": rss_mb()}), flush=True)
os._exit(0)
"""


def _rss_mb(pid: int) -> float | None:
    """Memoria residente del figlio vista da fuori (`/proc` o `ps`)."""
    statm = Path(f"/proc/{pid}/statm")
    if statm.is_file():
        try:
            pages = int(statm.read_text().split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return pages * os.sysconf("SC_PAGE_SIZE") / 2**20
    ps = shutil.which("ps")
    if ps is None:
        return None
    out = subprocess.run(
        [ps, "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return int(out) / 1024 if out.isdigit() else None


def _run_child(job: dict[str, Any]) -> dict[str, Any]:
    """Il lavoro in un processo figlio, fermato oltre `_KILL_SECONDS` o
    `_KILL_RSS_MB` (anche dal figlio stesso). Ritorna le righe `COST`
    fuse in un dizionario (`cases` per caso, `rss_mb`), con `killed` se il
    figlio è stato fermato o è uscito prima della fine."""
    with tempfile.TemporaryDirectory() as tmp:
        job_file = Path(tmp) / "job.json"
        job_file.write_text(json.dumps(job), encoding="utf-8")
        args = [sys.executable, "-c", _CHILD, str(job_file), str(_KILL_RSS_MB)]
        with tempfile.TemporaryFile() as out:
            proc = subprocess.Popen(args, cwd=_BACKEND, stdout=out, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + _KILL_SECONDS
            killed = None
            while proc.poll() is None:
                rss = _rss_mb(proc.pid)
                if rss is not None and rss > _KILL_RSS_MB:
                    killed = f"rss {rss:.0f} MB"
                elif time.monotonic() > deadline:
                    killed = f"oltre {_KILL_SECONDS:g} s"
                if killed is not None:
                    proc.kill()
                    proc.wait()
                    break
                time.sleep(0.05)
            out.seek(0)
            text = out.read().decode("utf-8", errors="replace")
    merged: dict[str, Any] = {"cases": {}}
    for line in text.splitlines():
        if not line.startswith("COST "):
            continue
        row = json.loads(line[5:])
        if "case" in row:
            merged["cases"][row.pop("case")] = row
        else:
            merged.update(row)
    if killed is None and "rss_mb" not in merged:
        killed = f"uscita {proc.returncode}: {text[-300:]}"
    if killed is not None:
        merged.setdefault("killed", killed)
    return merged


@pytest.fixture(scope="module")
def validated() -> dict[str, Any]:
    cases = [[name, fmt, source] for name, (fmt, source) in PAYLOADS.items()]
    return _run_child({"mode": "payloads", "cases": cases})


@pytest.mark.parametrize("name", list(PAYLOADS))
def test_finding_sources_are_validated_in_linear_time(validated: dict[str, Any], name: str) -> None:
    _, source = PAYLOADS[name]
    assert len(source) <= VISUAL_ASSET_CONTENT_MAX_CHARS  # ammesso dallo schema
    case = validated["cases"].get(name)
    assert case is not None, validated.get("killed")
    assert "raised" not in case, case
    assert case["seconds"] < _SECONDS, case
    if name in _ENTITIES:
        ok, msg = case["result"]
        assert ok is False
        assert msg.startswith(f"{GRAPH_TOO_DENSE}: "), msg


def test_finding_sources_stay_within_the_memory_bound(validated: dict[str, Any]) -> None:
    assert "killed" not in validated, validated.get("killed")
    assert validated["rss_mb"] < _RSS_MB, validated["rss_mb"]


@pytest.mark.parametrize("sweep", list(SWEEPS))
def test_no_line_shape_is_superlinear(sweep: str) -> None:
    run = _run_child({"mode": "sweep", "sweep": SWEEPS[sweep], "bound": _SECONDS, "size": _SIZE})
    assert "killed" not in run, run.get("killed")
    spec = SWEEPS[sweep]
    assert run["cases"] == len(spec["heads"]) * len(spec["prefixes"]) * len(spec["pumps"])
    assert run["worst"][0][0] < _SECONDS, run["worst"]
    assert run["rss_mb"] < _RSS_MB, run["rss_mb"]


@pytest.mark.parametrize("name", _ENTITIES)
def test_numeric_entities_beyond_the_int_limit_are_measured(name: str) -> None:
    """Prima: `ValueError` da `html.unescape` fuori da `validate` (500 nel
    PATCH, eccezione nel worker). Ora l'etichetta resta com'è ed è oltre
    soglia."""
    fmt, source = PAYLOADS[name]
    errors = check_graph_rules(fmt, source)
    assert any(e.startswith(f"{GRAPH_TOO_DENSE}: caratteri dell'etichetta ") for e in errors)
    assert gr._unescape("&amp;#1;") == "&#1;"


def test_mermaid_measure_reads_at_most_the_schema_cap() -> None:
    cap = gr.MAX_MERMAID_MEASURED_CHARS
    assert cap == VISUAL_ASSET_CONTENT_MAX_CHARS
    source = "flowchart TD\n" + "\n".join(f"N{i} --> N{i + 1}" for i in range(4000))
    assert len(source) > 3 * cap
    measured = gr.mermaid_source_metrics(source)
    prefix = gr.mermaid_source_metrics(source[:cap])
    # Righe e caratteri sul sorgente intero, conteggi sul prefisso.
    assert (measured.chars, measured.lines) == (len(source), 4001)
    assert (measured.nodes, measured.edges) == (prefix.nodes, prefix.edges)
    assert measured.nodes < 4001
    # Fino al tetto il sorgente è letto intero.
    whole = source[: source.rfind("\n", 0, cap)]
    edges = whole.count("-->")
    at_cap = gr.mermaid_source_metrics(whole)
    assert (at_cap.nodes, at_cap.edges) == (edges + 1, edges)


# ---------------------------------------------------------------------------
# Le letture riscritte leggono come quelle con il backtracking
# ---------------------------------------------------------------------------

# Le regex di f92a194, riferimento solo su input corti.
_OLD_LINK_RE = re.compile(r"\s*(<?(?:-{2,}|={2,}|-\.+-?|\.+-)[>xo]?|~{3,})\s*")
_OLD_PARTICIPANT_RE = re.compile(
    r"^(?:create\s+)?(?:participant|actor)\s+(.+?)(?:\s+as\s+(.+))?$", re.IGNORECASE
)
_OLD_REGEXES: dict[str, re.Pattern[str]] = {
    "_SEQ_NOTE_RE": re.compile(
        r"^note\s+(?:left of|right of|over)\s+[^:]+:\s*(.*)$", re.IGNORECASE
    ),
    "_STATE_NOTE_RE": re.compile(r"^note\s+(?:left|right)\s+of\s+[^:]+:\s*(.*)$", re.IGNORECASE),
    "_ER_REL_RE": re.compile(r"^(\S+)\s+(\S*(?:--|\.\.)\S*)\s+(\S+)\s*:\s*(.*)$"),
    "_ER_BLOCK_RE": re.compile(r"^([^\s{\[]+)\s*(?:\[\s*\"?([^\]\"]*)\"?\s*\])?\s*\{\s*$"),
}
_OLD_CLASS_REL_RE = re.compile(
    r"^(\S+?)\s*(?:\"[^\"]*\"\s*)?"
    r"(<\|--|\*--|o--|-->|--\*|--o|--\|>|<--|\.\.>|\.\.\|>|<\.\.|<\|\.\.|--|\.\.)"
    r"\s*(?:\"[^\"]*\"\s*)?(\S+?)\s*(?::\s*(.*))?$"
)
_OLD_TREE_VALUE_RE = re.compile(r"\s*:\s*[-+.\d]+\s*$")
_OLD_TAG_RE = re.compile(r"<[^>]*>")
_OLD_TRIM_RE = re.compile(r"^[\s\ufeff]+|[\s\ufeff]+$")
# Spazi che `\s` e `str.isspace` riconoscono oltre a quelli ASCII.
_ODD_SPACES = ["\xa0", "\x1c", "\u2028", "\x0b"]
_SAMPLES = 4000


def _random_lines(
    alphabet: list[str], *, seed: int, count: int = _SAMPLES, most: int = 10
) -> Iterator[str]:
    rnd = random.Random(seed)
    for _ in range(count):
        yield "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, most)))


def _old_class_relation(line: str) -> tuple[str, str, str | None] | None:
    m = _OLD_CLASS_REL_RE.match(line)
    return None if m is None else (m.group(1), m.group(3), m.group(4))


def test_class_relation_reads_like_the_lazy_regex() -> None:
    alphabet = ["A", "o", "-", ".", "<", "|", ">", "*", '"', ":", " ", "\t", "x", "--", "<|--"]
    alphabet += ["-->", "..|>", '"1"', " : ", *_ODD_SPACES]
    lines = [line.strip() for line in _random_lines(alphabet, seed=1)]
    small = ["A", "-", ".", ">", '"', ":", " ", "<"]
    for size in range(1, 6):
        lines += ["".join(t) for t in itertools.product(small, repeat=size)]
    diff = [s for s in lines if s and _old_class_relation(s) != gr._class_relation(s)]
    assert diff == []
    assert gr._class_relation('Animal "1" <|-- "*" Cane : eredita') == ("Animal", "Cane", "eredita")


def test_participant_reads_like_the_lazy_regex() -> None:
    alphabet = ["participant", "actor", "create", "ACTOR", " ", " ", "\t", "as", "AS", "a", "x"]
    alphabet += [" as ", ":", *_ODD_SPACES]
    diff = []
    for raw in _random_lines(alphabet, seed=2, most=9):
        line = raw.strip()
        m = _OLD_PARTICIPANT_RE.match(line)
        old = None if m is None else (m.group(1), m.group(2) or m.group(1))
        if old != gr._sequence_participant(line):
            diff.append((line, old))
    assert diff == []
    assert gr._sequence_participant("participant C as Il client") == ("C", "Il client")


@pytest.mark.parametrize("name", list(_OLD_REGEXES))
def test_possessive_regexes_match_like_the_backtracking_ones(name: str) -> None:
    alphabet = ["note", "left of", "right of", "over", "of", ":", " ", "\t", "a", "b:", "A", "B"]
    alphabet += ["-", "--", "..", ".", "|o--o{", "{", "}", "[", "]", '"', *_ODD_SPACES]
    old, new = _OLD_REGEXES[name], getattr(gr, name)
    diff = []
    for line in _random_lines(alphabet, seed=3, most=11):
        for candidate in (line, line.strip()):
            a, b = old.match(candidate), new.match(candidate)
            if (a and (a.span(), a.groups())) != (b and (b.span(), b.groups())):
                diff.append(candidate)
    assert diff == []


def test_substitutions_read_like_the_backtracking_ones() -> None:
    alphabet = ["a", " ", "\t", ":", "1", "-", "+", ".", "x", "\ufeff", "<", ">", "</", "<b>"]
    alphabet += ["\n", ";", *_ODD_SPACES]
    diff = [
        s
        for s in _random_lines(alphabet, seed=4, most=12)
        if _OLD_TREE_VALUE_RE.sub("", s) != gr._TREE_VALUE_RE.sub("", s)
        or _OLD_TAG_RE.sub("", s) != gr._strip_tags(s)
        or _OLD_TRIM_RE.sub("", s) != frs._MERMAID_TRIM_RE.sub("", s)
    ]
    assert diff == []


def test_html_tag_search_reads_like_the_regex() -> None:
    alphabet = ["<", "<", ">", ">", "/", "b", "r", "B", "a", "1", "-", " ", "\t", "]", ")"]
    alphabet += ["}", "x", "<br", "</", "/>", ">>", "\r", "\ufeff", "<a ", "<br/ >", "\n"]
    alphabet += _ODD_SPACES
    diff = []
    for line in _random_lines(alphabet, seed=5, most=14):
        m = frs._HTML_TAG_RE.search(line)
        if (m.group(0) if m else None) != frs._html_tag(line):
            diff.append(line)
    assert diff == []
    assert frs._html_tag("A[x <a y] --> B<b>") == "<b>"


def test_flowchart_links_read_like_before(monkeypatch: pytest.MonkeyPatch) -> None:
    alphabet = ["A", "B", " ", "\t", "-", "--", "-->", "---", "==", "==>", "-.", ".-", "-.->"]
    alphabet += [".", "..", "<", ">", "x", "o", "~~~", "|t|", "&", "[x]", '"q"', ";", ":::c"]
    bodies = []
    for seed in range(3):
        lines = [s.strip() for s in _random_lines(alphabet, seed=10 + seed, count=600, most=9)]
        bodies += [["flowchart TD", *filter(None, lines[i : i + 3])] for i in range(0, 600, 3)]

    def read(block: bool) -> list[tuple[Any, ...]]:
        out = []
        for body in bodies:
            acc = gr._flowchart(body, block=block)
            out.append((dict(acc.nodes), acc.edges, acc.labels, acc.wrapped))
        return out

    new = [read(False), read(True)]
    monkeypatch.setattr(gr, "_LINK_RE", _OLD_LINK_RE)
    assert [read(False), read(True)] == new


def test_dot_tokens_read_like_before(monkeypatch: pytest.MonkeyPatch) -> None:
    alphabet = ['"', '\\"', "\\", "a", " ", "->", "-", "{", "}", "[", "=", "label", "<", ">"]
    alphabet += ["/*", "*/", "//", "#", "\n", "1", ".5", "+", ";", "é"]
    sources = list(_random_lines(alphabet, seed=6, most=16))
    new = [gr._dot_tokens(s) for s in sources]
    # Con la ricerca della stringa sempre accesa il tokenizer è quello di prima.
    monkeypatch.setattr(gr, "_DOT_TOKEN_NO_STRING_RE", gr._DOT_TOKEN_RE)
    assert [gr._dot_tokens(s) for s in sources] == new


# ---------------------------------------------------------------------------
# Nessuna eccezione su sorgenti corti casuali (giro 1 della coda, V1-F1)
# ---------------------------------------------------------------------------

_FUZZ_HEADS = [*SWEEPS["misura_mermaid"]["heads"]]
_FUZZ_TOKENS = ["A", "B", "1", "-1.5", ",", '"', "'", ":", ";", "\r", "\r\n", "\n", " ", "\t"]
_FUZZ_TOKENS += ["-->", "--", "..", "|", "[", "]", "(", ")", "{", "}", "<", ">", "&#", "&"]
_FUZZ_TOKENS += ["&#" + "9" * 30 + ";", "&#x110000;", "%%", "%%{", "}%%", "@{", "title"]
_FUZZ_TOKENS += ["section", "x-axis", "axis", "note", "over", "as", "participant", "state"]
_FUZZ_TOKENS += ["~", "\\", "\\n", "<br>", "é", "﻿", "after", "[0.5, 0.5]", '"a,b"']
_FUZZ_TOKENS += [",,,", '"\r"', "a\rb", *_ODD_SPACES]
_FUZZ_DOT_TOKENS = ["digraph", "strict", "{", "}", "[", "]", "=", ";", ",", "a", "b", "->"]
_FUZZ_DOT_TOKENS += ["label", "xlabel", '"', '\\"', "\\", "<", ">", "<br/>", "&#xD800;", "\r"]
_FUZZ_DOT_TOKENS += ["\n", " ", "subgraph", "shape", "record", "|", "/*", "*/", "//", "#"]


def test_short_random_sources_never_raise() -> None:
    """Oracolo di V1-F1: sul codice di prima circa l'1 % di questi sorgenti
    (tutti sankey con un `\\r` dentro una riga) sollevava `csv.Error` da
    `validate`. Input corti, nel processo del test."""
    rnd = random.Random(20260917)
    raised: dict[str, str] = {}
    for i in range(12_000):
        if i % 4 == 0:
            fmt = "dot"
            tokens = (rnd.choice(_FUZZ_DOT_TOKENS) for _ in range(rnd.randint(0, 30)))
            source = "digraph { " + " ".join(tokens) + " }"
        else:
            fmt = "mermaid"
            lines = [rnd.choice(_FUZZ_HEADS)]
            for _ in range(rnd.randint(1, 6)):
                count = rnd.randint(0, 10)
                lines.append("".join(rnd.choice(_FUZZ_TOKENS) for _ in range(count)))
            source = "\n".join(lines)
        try:
            frs.REGISTRY[fmt].validate(source, deep=False)
            gr.graph_source_metrics(fmt, source)
        except Exception as exc:
            raised.setdefault(f"{fmt}:{type(exc).__name__}", source)
    assert raised == {}
