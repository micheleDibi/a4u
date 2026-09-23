"""Statistica di non sostituzione (M7, I1): regole M7-S1…S4 e introduttive.

Casi costruiti: B uguale ad A passa; B che toglie figure generate in 6
lezioni su 8 fallisce S1; uno scarto medio negativo oltre la variabilità
fallisce S2; un cambio del mix dei formati fallisce S3; tabelle o parole
fuori banda in 3 lezioni falliscono S4; un'introduttiva che perde 2
figure fallisce. Le figure di fonte (budget (b)) non contano mai.
"""

from __future__ import annotations

from dataclasses import replace

from app.services.source_figure_substitution import (
    GenerationStats,
    LessonTriple,
    stats_from_output,
    substitution_report,
)


def _stats(g: int = 5, **kw: object) -> GenerationStats:
    base = {
        "generated_figures": g,
        "formats": {"mermaid": g // 2, "function": g - g // 2},
        "tables": 2,
        "equations": 4,
        "key_takeaways": 6,
        "examples": 2,
        "words": 2000,
    }
    base.update(kw)
    return GenerationStats(**base)  # type: ignore[arg-type]


def _triples(b_for: dict[int, GenerationStats] | None = None) -> list[LessonTriple]:
    out = []
    for i in range(8):
        b = (b_for or {}).get(i, _stats())
        out.append(LessonTriple(f"L{i}", False, _stats(), _stats(), b))
    out.append(LessonTriple("I1", True, _stats(2), _stats(1), _stats(1)))
    out.append(LessonTriple("I2", True, _stats(0), _stats(1), _stats(0)))
    return out


def test_equal_generations_pass() -> None:
    report = substitution_report(_triples())
    assert report.passed, report.checks


def test_extra_source_figures_do_not_count() -> None:
    report = substitution_report(_triples({i: _stats(source_figures=3) for i in range(8)}))
    assert report.passed


def test_losing_generated_figures_in_six_lessons_fails_s1() -> None:
    report = substitution_report(
        _triples({i: _stats(4, formats={"mermaid": 2, "function": 2}) for i in range(6)})
    )
    assert report.checks["S1"]["failed"] and not report.passed
    five = substitution_report(
        _triples({i: _stats(4, formats={"mermaid": 2, "function": 2}) for i in range(5)})
    )
    assert not five.checks["S1"]["failed"]


def test_mean_drop_beyond_variability_fails_s2() -> None:
    report = substitution_report(
        _triples({i: _stats(3, formats={"mermaid": 1, "function": 2}) for i in range(3)})
    )
    assert report.checks["S2"]["failed"]


def test_format_mix_shift_fails_s3() -> None:
    report = substitution_report(_triples({i: _stats(5, formats={"dot": 5}) for i in range(8)}))
    assert report.checks["S3"]["failed"]


def test_content_out_of_band_fails_s4() -> None:
    report = substitution_report(_triples({i: _stats(tables=0, words=1200) for i in range(3)}))
    assert report.checks["S4"]["failed"]
    assert report.checks["S4"]["out_of_band"]["words"] == ["L0", "L1", "L2"]


def test_intro_lesson_losing_two_figures_fails() -> None:
    triples = _triples()
    triples[8] = replace(triples[8], a1=_stats(3), a2=_stats(3), b=_stats(1))
    report = substitution_report(triples)
    assert report.checks["intro"]["failed"] and report.checks["intro"]["losses"] == ["I1"]


def test_stats_from_output_counts_only_generated_figures() -> None:
    stats = stats_from_output(
        {
            "introduction": "uno due",
            "sections": [{"content": "tre quattro cinque"}],
            "summary": "sei",
            "visual_assets": [
                {"format": "mermaid"},
                {"format": "function"},
                {"format": "source_figure"},
            ],
            "tables": [{}],
            "equations": [{}, {}],
            "key_takeaways": ["a", "b", "c"],
            "examples": [],
        }
    )
    assert stats.generated_figures == 2 and stats.source_figures == 1
    assert stats.formats == {"mermaid": 1, "function": 1}
    assert stats.words == 6
