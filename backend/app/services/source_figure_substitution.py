"""Statistica della misura M7: le figure di fonte non sostituiscono le generate.

Confronta, lezione per lezione, le generazioni del PROMPT 3 senza catalogo
(A1, A2: stesso prompt, due estrazioni, per stimare la variabilità) con
quella con il catalogo (B). Le metriche contano solo le figure GENERATE
(budget (a)); le figure di fonte sono il budget (b), in aggiunta.

Regole (piano, «(b) Misure», M7):

- M7-S1: fallisce se in almeno `S1_MAX_LOSSES` lezioni ordinarie su 8
  (≥ 6/8, binomiale, alfa ≈ 0,02) B ha meno figure generate del minimo di A1 e
  A2;
- M7-S2: fallisce se lo scarto medio g_B − media(g_A1, g_A2) è minore di
  −max(D_A, 0,5), con D_A = media di |g_A1 − g_A2|;
- M7-S3: fallisce se la distanza di variazione totale fra i formati di B e
  quelli di A supera quella fra A1 e A2 di più di 0,10;
- M7-S4: fallisce se tabelle, equazioni, punti chiave, esempi o parole di B
  escono dalla banda di A (min − tolleranza, max + tolleranza) in almeno
  `S4_MAX_OUT` lezioni ordinarie per la stessa metrica;
- introduttive: g_B ≥ min(g_A1, g_A2) − 1.

Modulo puro: lo script `scripts/measure_source_figures.py` lo alimenta con
gli output reali; i test lo verificano su casi costruiti.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

S1_MAX_LOSSES = 6
S2_MIN_TOLERANCE = 0.5
S3_TVD_MARGIN = 0.10
S4_MAX_OUT = 3
# Tolleranze della banda per metrica (conteggi assoluti; parole relative).
S4_COUNT_TOLERANCE = 1
S4_WORDS_RELATIVE = 0.20
SOURCE_FORMAT = "source_figure"


@dataclass(frozen=True)
class GenerationStats:
    generated_figures: int
    formats: dict[str, int]
    tables: int
    equations: int
    key_takeaways: int
    examples: int
    words: int
    source_figures: int = 0


def stats_from_output(output: Mapping[str, Any]) -> GenerationStats:
    """Statistiche di un output di Fase 3 (dict di `model_dump`)."""
    assets = [a for a in output.get("visual_assets") or [] if isinstance(a, Mapping)]
    generated = [a for a in assets if a.get("format") != SOURCE_FORMAT]
    body = " ".join(
        [
            str(output.get("introduction") or ""),
            *(str(s.get("content") or "") for s in output.get("sections") or []),
            str(output.get("summary") or ""),
        ]
    )
    return GenerationStats(
        generated_figures=len(generated),
        formats=dict(Counter(str(a.get("format")) for a in generated)),
        tables=len(output.get("tables") or []),
        equations=len(output.get("equations") or []),
        key_takeaways=len(output.get("key_takeaways") or []),
        examples=len(output.get("examples") or []),
        words=len(body.split()),
        source_figures=len(assets) - len(generated),
    )


@dataclass(frozen=True)
class LessonTriple:
    lesson: str
    introductory: bool
    a1: GenerationStats
    a2: GenerationStats
    b: GenerationStats


@dataclass
class SubstitutionReport:
    passed: bool
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)


def _tvd(a: Mapping[str, int], b: Mapping[str, int]) -> float:
    total_a = sum(a.values()) or 1
    total_b = sum(b.values()) or 1
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0) / total_a - b.get(k, 0) / total_b) for k in keys)


def _pooled(stats: Sequence[GenerationStats]) -> Counter[str]:
    pooled: Counter[str] = Counter()
    for s in stats:
        pooled.update(s.formats)
    return pooled


def _out_of_band(value: float, low: float, high: float, *, words: bool) -> bool:
    if words:
        return value < low * (1 - S4_WORDS_RELATIVE) or value > high * (1 + S4_WORDS_RELATIVE)
    return value < low - S4_COUNT_TOLERANCE or value > high + S4_COUNT_TOLERANCE


def substitution_report(triples: Sequence[LessonTriple]) -> SubstitutionReport:
    ordinary = [t for t in triples if not t.introductory]
    intro = [t for t in triples if t.introductory]
    checks: dict[str, dict[str, Any]] = {}

    losses = [
        t.lesson
        for t in ordinary
        if t.b.generated_figures < min(t.a1.generated_figures, t.a2.generated_figures)
    ]
    checks["S1"] = {
        "losses": losses,
        "count": len(losses),
        "threshold": S1_MAX_LOSSES,
        "failed": len(losses) >= S1_MAX_LOSSES,
    }

    deltas = [
        t.b.generated_figures - (t.a1.generated_figures + t.a2.generated_figures) / 2
        for t in ordinary
    ]
    d_a = (
        sum(abs(t.a1.generated_figures - t.a2.generated_figures) for t in ordinary) / len(ordinary)
        if ordinary
        else 0.0
    )
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    tolerance = max(d_a, S2_MIN_TOLERANCE)
    checks["S2"] = {
        "mean_delta": round(mean_delta, 3),
        "d_a": round(d_a, 3),
        "failed": mean_delta < -tolerance,
    }

    tvd_ab = _tvd(
        _pooled([t.b for t in ordinary]), _pooled([s for t in ordinary for s in (t.a1, t.a2)])
    )
    tvd_aa = _tvd(_pooled([t.a1 for t in ordinary]), _pooled([t.a2 for t in ordinary]))
    checks["S3"] = {
        "tvd_b_a": round(tvd_ab, 3),
        "tvd_a1_a2": round(tvd_aa, 3),
        "failed": tvd_ab > tvd_aa + S3_TVD_MARGIN,
    }

    s4: dict[str, list[str]] = {}
    for metric in ("tables", "equations", "key_takeaways", "examples", "words"):
        out = []
        for t in ordinary:
            a_values = (getattr(t.a1, metric), getattr(t.a2, metric))
            if _out_of_band(
                getattr(t.b, metric), min(a_values), max(a_values), words=metric == "words"
            ):
                out.append(t.lesson)
        s4[metric] = out
    checks["S4"] = {
        "out_of_band": s4,
        "threshold": S4_MAX_OUT,
        "failed": any(len(v) >= S4_MAX_OUT for v in s4.values()),
    }

    intro_fail = [
        t.lesson
        for t in intro
        if t.b.generated_figures < min(t.a1.generated_figures, t.a2.generated_figures) - 1
    ]
    checks["intro"] = {"losses": intro_fail, "failed": bool(intro_fail)}
    checks["sources"] = {
        "b_source_figures": {t.lesson: t.b.source_figures for t in triples},
    }
    passed = not any(c.get("failed") for c in checks.values())
    return SubstitutionReport(passed=passed, checks=checks)
