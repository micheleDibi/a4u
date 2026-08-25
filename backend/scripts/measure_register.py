"""Misura del registro nei contenuti generati — strumento DIAGNOSTICO, non un gate.

Scansiona le dispense (Fase 3), le slide (Fase 4) e il discorso (Fase 5)
delle lezioni e conta, per lezione x tipo, la presenza di "frasi ad
effetto" e di "asserzioni valutative non sostenute". Serve a confrontare
il registro prima/dopo una modifica dei prompt: i numeri sono euristiche
lessicali, NON giudizi di qualità, e vanno letti in aggregato. Nessuna
soglia blocca nulla: lo script non è (e non deve diventare) un gate.

Sola lettura sul DB (SELECT espliciti sulle sole colonne necessarie,
nessuna scrittura). Dipendenze: stdlib + SQLAlchemy già presente.

Pipeline per ogni testo
-----------------------
1. `normalize_prose`: gli span `$...$` e `$$...$$` diventano la parola
   `MATH`; i tag `[FIG:..]` `[TAB:..]` `[EQ:..]` `[EX:..]` sono rimossi;
   markdown ripulito (intestazioni `#`, `**`, backtick, `- ` a inizio
   riga); le righe bibliografiche `- Autore (2020) ...` sono scartate.
2. `split_sentences`: split su `.!?…` seguiti da spazio + maiuscola /
   virgoletta / parentesi, proteggendo abbreviazioni (es., ecc., cfr.,
   prof., n., p., pp., art., ...), iniziali `A.` e decimali `3.14`.
   Ogni riga (a capo) è comunque un confine di frase.
3. `analyze_text` → `TextMetrics`.

Indicatori (conteggi grezzi nella dataclass; le normalizzazioni per
1.000 parole e per 100 frasi sono calcolate nel report)
----------------------------------------------------------------------
* `punchline_after_long`: frase con <= PUNCH_MAX_WORDS (default 5)
  parole preceduta da una frase con >= LONG_MIN_WORDS (default 25)
  parole; la frase corta non deve terminare con ':'.
* `formula_hits`: occorrenze (case-insensitive) delle "formule" per
  lingua (`DEFAULT_FORMULAS`, override con `--formulas-file`). Le forme
  che terminano con '.' sono cercate come FRASE INTERA (uguaglianza
  dopo strip), le altre come sottostringa.
* `evaluative_total_a` / `evaluative_unjustified_a`: frasi con un
  aggettivo valutativo di tier A (elegante, potente, brillante,
  affascinante, sorprendente, straordinario, geniale, magico,
  meraviglioso...) e, fra queste, quelle SENZA marcatore di
  giustificazione nella stessa frase o nella successiva (':', perché,
  poiché, in quanto, dato che, infatti, cioè, ovvero, ad esempio, per
  esempio, come mostra, dimostra, si vede, una cifra, la parola MATH,
  un riferimento `(Autore, 2020)` / `[3]`, cfr.).
* `evaluative_total_b` / `evaluative_unjustified_b`: idem per il tier B
  (fondamentale, cruciale, essenziale, decisivo), contato a parte.
* `short_ratio`: frasi con < 6 parole / frasi totali.
* `rhetorical_questions`: frasi che terminano con '?' per 100 frasi
  (conteggio grezzo in `questions_count`).
* `antithesis_openers`: frasi che iniziano con "Ma ", "Eppure", "Anzi",
  "Non è ", "Proprio " (en: "But ", "Yet", "Rather", "Not ",
  "Precisely ") per 100 frasi (grezzo in `antithesis_count`).
* `sent_len_mean` / `sent_len_std`: media e deviazione standard
  (`statistics.pstdev`, 0.0 con meno di 2 frasi) delle parole per frase.

Nel report ogni indicatore di conteggio compare sia grezzo (`*_tot`)
sia normalizzato per 1.000 parole (`*_1k`) e, nelle righe aggregate,
come media per lezione (`*_lez`).

Estrazione dei testi (`extract_texts`)
--------------------------------------
* `dispensa`: introduction + sections[].content + summary +
  examples[].content (per le verifiche: testo delle domande);
* `slide`: slides[].title + slides[].body; `slide_bullets`: i bullet;
* `discorso`: speech_segments[].text.
I metadati cost_usd / duration_ms / reasoning_effort / cached_tokens
sono presi dal `*_tokens` della fase che ha generato quel tipo
(content_tokens per la dispensa, slides_tokens per slide e bullet,
speech_tokens per il discorso); estimated_word_count da content_raw
(dispensa) o da speech_raw.estimated_total_word_count (discorso).

Uso (dalla cartella `backend/`)
-------------------------------

    python -m scripts.measure_register                     # tabella markdown
    python -m scripts.measure_register --course "Analisi" --by-lesson --top 10
    python -m scripts.measure_register --since 2026-08-01 --language auto
    python -m scripts.measure_register --format csv --export-jsonl before.jsonl > before.csv
    python -m scripts.measure_register --from-jsonl before.jsonl --top 5
    python -m scripts.measure_register --compare before.jsonl after.jsonl

Sul server (`dc` = alias docker compose di produzione):

    dc exec -T backend python -m scripts.measure_register --format csv \\
        --export-jsonl before.jsonl > before.csv

Strumento diagnostico, non gate; confrontare solo run con lo stesso
SCRIPT_VERSION (stampato in ogni output).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import statistics
import sys
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "1"

PUNCH_MAX_WORDS = 5
LONG_MIN_WORDS = 25
SHORT_SENTENCE_WORDS = 6

TEXT_KINDS: tuple[str, ...] = ("dispensa", "slide", "slide_bullets", "discorso")
SUPPORTED_LANGUAGES: tuple[str, ...] = ("it", "en")

DEFAULT_FORMULAS: dict[str, list[str]] = {
    "it": [
        "Non è così.",
        "proprio per questo",
        "magia",
        "Non basta.",
        "Anzi.",
        "Punto.",
        "Tutto qui.",
        "Ma attenzione",
        "Non è un caso",
        "Ecco perché",
    ],
    "en": [
        "Not so.",
        "precisely because",
        "magic",
        "That's it.",
        "Period.",
        "Full stop.",
        "Quite the opposite.",
        "Not quite.",
    ],
}


# ---------------------------------------------------------------------------
# Pattern per lingua
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LanguagePatterns:
    """Regex lessicali di una lingua (tutte case-insensitive)."""

    evaluative_a: re.Pattern[str]
    evaluative_b: re.Pattern[str]
    justification: re.Pattern[str]
    antithesis: re.Pattern[str]


# Marcatori strutturali comuni a tutte le lingue (case-SENSITIVE: `MATH`
# è il segnaposto delle formule e non deve confondersi con "math").
_STRUCTURAL_JUSTIFICATION_RE = re.compile(r":|\d|\bMATH\b|\([^()]*\d{4}\)|\[\d+\]")

LANGUAGE_PATTERNS: dict[str, LanguagePatterns] = {
    "it": LanguagePatterns(
        evaluative_a=re.compile(
            r"\b(elegant\w*|potent[ei]|potentissim\w*|brillant\w*|affascinant\w*"
            r"|sorprendent\w*|straordinar\w*|geniale|magic\w*|meraviglios\w*)\b",
            re.IGNORECASE,
        ),
        evaluative_b=re.compile(
            r"\b(fondamental\w*|crucial\w*|essenzial\w*|decisiv\w*)\b", re.IGNORECASE
        ),
        justification=re.compile(
            r"\bperch[éè]\b|\bpoich[éè]\b|\bin quanto\b|\bdato che\b|\binfatti\b"
            r"|\bcio[èé]\b|\bovvero\b|\bad esempio\b|\bper esempio\b|\bcome mostra\b"
            r"|\bdimostra|\bsi vede\b|\bcfr\.",
            re.IGNORECASE,
        ),
        antithesis=re.compile(r"^(?:ma\s|eppure\b|anzi\b|non è\s|proprio\s)", re.IGNORECASE),
    ),
    "en": LanguagePatterns(
        evaluative_a=re.compile(
            r"\b(elegant|powerful|brilliant|fascinating|surprising|extraordinary"
            r"|ingenious|magical?|wonderful)\b",
            re.IGNORECASE,
        ),
        evaluative_b=re.compile(r"\b(fundamental|crucial|essential|decisive)\b", re.IGNORECASE),
        justification=re.compile(
            r"\bbecause\b|\bsince\b|\bfor example\b|\be\.g\.|\bas shown\b|\bthat is\b",
            re.IGNORECASE,
        ),
        antithesis=re.compile(r"^(?:but\s|yet\b|rather\b|not\s|precisely\s)", re.IGNORECASE),
    ),
}


def resolve_language(code: str | None) -> str:
    """`it-IT` → `it`; lingue non supportate ricadono su `it`."""
    short = (code or "").strip().lower()[:2]
    return short if short in SUPPORTED_LANGUAGES else "it"


# ---------------------------------------------------------------------------
# Normalizzazione e segmentazione
# ---------------------------------------------------------------------------

_MATH_BLOCK_RE = re.compile(r"\$\$.+?\$\$", re.DOTALL)
_MATH_INLINE_RE = re.compile(r"\$[^$\n]+?\$")
_ASSET_TAG_RE = re.compile(r"\[(?:FIG|TAB|EQ|EX):[^\]\n]*\]", re.IGNORECASE)
_BIB_LINE_RE = re.compile(r"^\s*-\s.*\(\d{4}\)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+")
_EMPHASIS_RE = re.compile(r"\*+|`+")
_SPACES_RE = re.compile(r"[ \t]+")

# Segnaposto (area privata Unicode) per i punti che NON chiudono una frase.
_DOT = "\ue000"
_ABBREV_RE = re.compile(
    r"\b(?:es|ecc|cfr|prof|n|p|pp|art|etc|ca|vol|cap|fig|tab|eq|dott|sig|ing"
    r"|vs|op|cit|e\.g|i\.e)\.",
    re.IGNORECASE,
)
_INITIAL_RE = re.compile(r"\b([A-Z])\.")
_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")
# Terminatore (eventualmente seguito da virgoletta/parentesi di chiusura),
# spazio, poi maiuscola / virgoletta / parentesi di apertura.
# Virgolette/parentesi di chiusura e apertura (anche tipografiche).
_CLOSERS = "\u00bb\u201d\u2019\"')]"
_OPENERS = "\"'\u00ab\u201c\u2018(["
_TERMINATORS = ".!?\u2026"
_SPLIT_RE = re.compile(
    rf"(?:(?<=[{re.escape(_TERMINATORS)}])"
    rf"|(?<=[{re.escape(_TERMINATORS)}][{re.escape(_CLOSERS)}]))\s+"
    rf"(?=[{re.escape(_OPENERS)}A-ZÀ-ÖØ-Þ])"
)


def normalize_prose(text: str | None) -> str:
    """Riduce markdown/asset a prosa piana; righe vuote eliminate."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _MATH_BLOCK_RE.sub(" MATH ", text)
    text = _MATH_INLINE_RE.sub(" MATH ", text)
    text = _ASSET_TAG_RE.sub(" ", text)
    lines: list[str] = []
    for raw_line in text.split("\n"):
        if _BIB_LINE_RE.match(raw_line):
            continue
        line = _HEADING_RE.sub("", raw_line)
        line = _BULLET_RE.sub("", line)
        line = _EMPHASIS_RE.sub("", line)
        line = _SPACES_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _protect_dots(line: str) -> str:
    line = _ABBREV_RE.sub(lambda m: m.group(0).replace(".", _DOT), line)
    line = _INITIAL_RE.sub(lambda m: m.group(1) + _DOT, line)
    return _DECIMAL_RE.sub(_DOT, line)


def split_sentences(text: str | None) -> list[str]:
    """Applica `normalize_prose` e segmenta in frasi (vedi docstring modulo)."""
    sentences: list[str] = []
    for line in normalize_prose(text).split("\n"):
        for piece in _SPLIT_RE.split(_protect_dots(line)):
            sentence = piece.replace(_DOT, ".").strip()
            if sentence:
                sentences.append(sentence)
    return sentences


def count_words(sentence: str) -> int:
    """Token separati da spazi che contengono almeno un carattere alfanumerico."""
    return sum(1 for tok in sentence.split() if any(ch.isalnum() for ch in tok))


# ---------------------------------------------------------------------------
# Metriche
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlaggedSentence:
    kind: str
    prev: str
    sentence: str


@dataclass
class TextMetrics:
    """Conteggi grezzi di un testo (vedi docstring del modulo)."""

    words: int = 0
    sentences: int = 0
    short_ratio: float = 0.0
    punchline_after_long: int = 0
    formula_hits: int = 0
    evaluative_unjustified_a: int = 0
    evaluative_unjustified_b: int = 0
    evaluative_total_a: int = 0
    evaluative_total_b: int = 0
    rhetorical_questions: float = 0.0  # per 100 frasi
    antithesis_openers: float = 0.0  # per 100 frasi
    questions_count: int = 0
    antithesis_count: int = 0
    sent_len_mean: float = 0.0
    sent_len_std: float = 0.0
    flagged: list[FlaggedSentence] = field(default_factory=list)


def per_1000(count: float, words: int) -> float:
    return round(count / words * 1000.0, 3) if words else 0.0


def per_100(count: float, sentences: int) -> float:
    return round(count / sentences * 100.0, 3) if sentences else 0.0


def _formula_hits(sentence: str, formulas: Sequence[str]) -> int:
    low = sentence.lower()
    hits = 0
    for formula in formulas:
        needle = formula.strip().lower()
        if not needle:
            continue
        if needle.endswith("."):
            hits += int(low == needle)
        else:
            hits += low.count(needle)
    return hits


def _is_justified(sentence: str, patterns: LanguagePatterns) -> bool:
    return bool(
        _STRUCTURAL_JUSTIFICATION_RE.search(sentence) or patterns.justification.search(sentence)
    )


def analyze_text(
    text: str | None,
    *,
    language: str = "it",
    formulas: Sequence[str] | None = None,
    punch_max_words: int = PUNCH_MAX_WORDS,
    long_min_words: int = LONG_MIN_WORDS,
) -> TextMetrics:
    """Calcola `TextMetrics` su un testo grezzo (markdown/asset ammessi)."""
    lang = resolve_language(language)
    patterns = LANGUAGE_PATTERNS[lang]
    formula_list = list(formulas) if formulas is not None else DEFAULT_FORMULAS[lang]

    sentences = split_sentences(text)
    lengths = [count_words(s) for s in sentences]
    metrics = TextMetrics(words=sum(lengths), sentences=len(sentences))
    if not sentences:
        return metrics

    for i, sentence in enumerate(sentences):
        prev = sentences[i - 1] if i > 0 else ""
        nxt = sentences[i + 1] if i + 1 < len(sentences) else ""
        n_words = lengths[i]

        is_punchline = (
            i > 0
            and lengths[i - 1] >= long_min_words
            and n_words <= punch_max_words
            and not sentence.endswith(":")
        )
        if is_punchline:
            metrics.punchline_after_long += 1
            metrics.flagged.append(FlaggedSentence("punchline", prev, sentence))

        hits = _formula_hits(sentence, formula_list)
        if hits:
            metrics.formula_hits += hits
            metrics.flagged.append(FlaggedSentence("formula", prev, sentence))

        if patterns.evaluative_a.search(sentence):
            metrics.evaluative_total_a += 1
            if not (_is_justified(sentence, patterns) or _is_justified(nxt, patterns)):
                metrics.evaluative_unjustified_a += 1
                metrics.flagged.append(FlaggedSentence("valutativa_A", prev, sentence))
        if patterns.evaluative_b.search(sentence):
            metrics.evaluative_total_b += 1
            if not (_is_justified(sentence, patterns) or _is_justified(nxt, patterns)):
                metrics.evaluative_unjustified_b += 1
                metrics.flagged.append(FlaggedSentence("valutativa_B", prev, sentence))

        if sentence.rstrip(_CLOSERS).endswith("?"):
            metrics.questions_count += 1
        if patterns.antithesis.match(sentence.lstrip(_OPENERS)):
            metrics.antithesis_count += 1

    n = len(sentences)
    metrics.short_ratio = round(sum(1 for w in lengths if w < SHORT_SENTENCE_WORDS) / n, 4)
    metrics.rhetorical_questions = per_100(metrics.questions_count, n)
    metrics.antithesis_openers = per_100(metrics.antithesis_count, n)
    metrics.sent_len_mean = round(statistics.fmean(lengths), 3)
    metrics.sent_len_std = round(statistics.pstdev(lengths), 3) if n >= 2 else 0.0
    return metrics


# ---------------------------------------------------------------------------
# Estrazione dei testi dai JSONB
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _join(parts: list[str], sep: str = "\n\n") -> str:
    return sep.join(p for p in parts if p)


def _extract_dispensa(content: dict[str, Any]) -> str:
    if content.get("is_assessment") or "multiple_choice_questions" in content:
        parts: list[str] = []
        for q in _as_list(content.get("multiple_choice_questions")):
            qd = _as_dict(q)
            parts.append(_str(qd.get("text")))
            parts.extend(_str(_as_dict(o).get("text")) for o in _as_list(qd.get("options")))
        for q in _as_list(content.get("open_questions")):
            qd = _as_dict(q)
            parts.extend((_str(qd.get("text")), _str(qd.get("expected_answer"))))
        return _join(parts)
    parts = [_str(content.get("introduction"))]
    parts.extend(_str(_as_dict(s).get("content")) for s in _as_list(content.get("sections")))
    parts.append(_str(content.get("summary")))
    parts.extend(_str(_as_dict(e).get("content")) for e in _as_list(content.get("examples")))
    return _join(parts)


def extract_texts(content_raw: Any, slides_raw: Any, speech_raw: Any) -> dict[str, str]:
    """Testo per tipo (`TEXT_KINDS`); stringa vuota quando il dato manca."""
    content = _as_dict(content_raw)
    slides = _as_dict(slides_raw)
    speech = _as_dict(speech_raw)

    slide_parts: list[str] = []
    bullet_parts: list[str] = []
    for s in _as_list(slides.get("slides")):
        sd = _as_dict(s)
        slide_parts.append(_join([_str(sd.get("title")), _str(sd.get("body"))], "\n"))
        bullet_parts.append(_join([_str(b) for b in _as_list(sd.get("bullets"))], "\n"))

    speech_parts = [
        _str(_as_dict(seg).get("text")) for seg in _as_list(speech.get("speech_segments"))
    ]
    return {
        "dispensa": _extract_dispensa(content),
        "slide": _join(slide_parts),
        "slide_bullets": _join(bullet_parts),
        "discorso": _join(speech_parts),
    }


# ---------------------------------------------------------------------------
# Righe lezione x tipo (serializzabili in JSONL)
# ---------------------------------------------------------------------------


@dataclass
class LessonRow:
    course_id: str
    course: str
    lesson_code: str
    lesson_title: str
    kind: str
    language: str
    is_introductory: bool
    is_assessment: bool
    generated_at: str | None
    cost_usd: float | None
    duration_ms: int | None
    reasoning_effort: str | None
    cached_tokens: int | None
    estimated_word_count: int | None
    metrics: TextMetrics

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"script_version": SCRIPT_VERSION}
        data.update({k: v for k, v in asdict(self).items() if k != "metrics"})
        metrics = asdict(self.metrics)
        data["flagged"] = metrics.pop("flagged")
        data["metrics"] = metrics
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LessonRow:
        known = {f.name for f in fields(TextMetrics)} - {"flagged"}
        raw_metrics = _as_dict(data.get("metrics"))
        flagged = [
            FlaggedSentence(
                kind=_str(f.get("kind")), prev=_str(f.get("prev")), sentence=_str(f.get("sentence"))
            )
            for f in (_as_dict(x) for x in _as_list(data.get("flagged")))
        ]
        metrics = TextMetrics(
            **{k: v for k, v in raw_metrics.items() if k in known}, flagged=flagged
        )
        return cls(
            course_id=str(data.get("course_id") or ""),
            course=str(data.get("course") or ""),
            lesson_code=str(data.get("lesson_code") or ""),
            lesson_title=str(data.get("lesson_title") or ""),
            kind=str(data.get("kind") or ""),
            language=resolve_language(data.get("language")),
            is_introductory=bool(data.get("is_introductory")),
            is_assessment=bool(data.get("is_assessment")),
            generated_at=data.get("generated_at"),
            cost_usd=data.get("cost_usd"),
            duration_ms=data.get("duration_ms"),
            reasoning_effort=data.get("reasoning_effort"),
            cached_tokens=data.get("cached_tokens"),
            estimated_word_count=data.get("estimated_word_count"),
            metrics=metrics,
        )


_LESSON_CODE_RE = re.compile(r"^M(\d+)\.L(\d+)$", re.IGNORECASE)


def lesson_sort_key(code: str) -> tuple[int, int, str]:
    m = _LESSON_CODE_RE.match(code or "")
    return (int(m.group(1)), int(m.group(2)), "") if m else (10**9, 0, code)


def _kind_index(kind: str) -> int:
    """Ordine di `TEXT_KINDS`; tipi sconosciuti in coda."""
    return TEXT_KINDS.index(kind) if kind in TEXT_KINDS else len(TEXT_KINDS)


def row_sort_key(row: LessonRow) -> tuple[Any, ...]:
    return (
        row.course,
        row.course_id,
        *lesson_sort_key(row.lesson_code),
        _kind_index(row.kind),
    )


def load_jsonl(path: Path) -> tuple[list[LessonRow], set[str]]:
    """Legge un file prodotto da `--export-jsonl`; ritorna righe e versioni viste."""
    rows: list[LessonRow] = []
    versions: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            versions.add(str(data.get("script_version") or "?"))
            rows.append(LessonRow.from_json(data))
    return rows, versions


def write_jsonl(path: Path, rows: Sequence[LessonRow]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.to_json(), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Accesso al DB (sola lettura)
# ---------------------------------------------------------------------------


def _tokens_meta(tokens: Any) -> dict[str, Any]:
    t = _as_dict(tokens)
    return {
        "cost_usd": t.get("cost_usd"),
        "duration_ms": t.get("duration_ms"),
        "reasoning_effort": t.get("reasoning_effort"),
        "cached_tokens": t.get("cached_tokens"),
    }


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def build_statement(args: argparse.Namespace, since: datetime | None) -> Any:
    """SELECT esplicito (sole colonne necessarie) lezione+corso con i filtri CLI.

    Funzione pura rispetto al DB: costruisce la query senza eseguirla.
    """
    from sqlalchemy import or_, select

    import app.models  # noqa: F401  registra tutti i mapper (relazioni per stringa)
    from app.models.course import Course
    from app.models.course_lesson import CourseLesson

    stmt = (
        select(
            Course.id.label("course_id"),
            Course.title.label("course_title"),
            Course.language_code,
            CourseLesson.lesson_code,
            CourseLesson.title.label("lesson_title"),
            CourseLesson.is_introductory,
            CourseLesson.is_assessment,
            CourseLesson.content_raw,
            CourseLesson.slides_raw,
            CourseLesson.speech_raw,
            CourseLesson.content_tokens,
            CourseLesson.slides_tokens,
            CourseLesson.speech_tokens,
            CourseLesson.content_generated_at,
            CourseLesson.slides_generated_at,
            CourseLesson.speech_generated_at,
        )
        .join(Course, Course.id == CourseLesson.course_id)
        .order_by(Course.title, Course.id, CourseLesson.lesson_code)
    )
    if args.course:
        try:
            stmt = stmt.where(Course.id == uuid.UUID(args.course))
        except ValueError:
            stmt = stmt.where(Course.title.ilike(f"%{args.course}%"))
    if args.lesson:
        stmt = stmt.where(CourseLesson.lesson_code == args.lesson)
    if not args.include_assessment:
        stmt = stmt.where(CourseLesson.is_assessment.is_(False))
    if since is not None:
        stmt = stmt.where(
            or_(
                CourseLesson.content_generated_at >= since,
                CourseLesson.slides_generated_at >= since,
                CourseLesson.speech_generated_at >= since,
            )
        )
    return stmt


async def load_rows_from_db(
    args: argparse.Namespace, formulas: dict[str, list[str]]
) -> list[LessonRow]:
    """Esegue `build_statement` e analizza i testi. Nessuna scrittura."""
    from app.db.session import async_session_factory, engine

    since = _parse_since(args.since)
    stmt = build_statement(args, since)
    try:
        async with async_session_factory() as session:
            records = (await session.execute(stmt)).mappings().all()
    finally:
        await engine.dispose()

    rows: list[LessonRow] = []
    for rec in records:
        lang = resolve_language(rec["language_code"]) if args.language == "auto" else args.language
        generated_by_kind = {
            "dispensa": rec["content_generated_at"],
            "slide": rec["slides_generated_at"],
            "slide_bullets": rec["slides_generated_at"],
            "discorso": rec["speech_generated_at"],
        }
        tokens_by_kind = {
            "dispensa": rec["content_tokens"],
            "slide": rec["slides_tokens"],
            "slide_bullets": rec["slides_tokens"],
            "discorso": rec["speech_tokens"],
        }
        ewc_by_kind = {
            "dispensa": _as_dict(rec["content_raw"]).get("estimated_word_count"),
            "discorso": _as_dict(rec["speech_raw"]).get("estimated_total_word_count"),
        }
        texts = extract_texts(rec["content_raw"], rec["slides_raw"], rec["speech_raw"])
        for kind, text in texts.items():
            if not text.strip():
                continue
            generated_at = generated_by_kind[kind]
            if since is not None and (generated_at is None or generated_at < since):
                continue
            metrics = analyze_text(
                text,
                language=lang,
                formulas=formulas[lang],
                punch_max_words=args.punch_max_words,
                long_min_words=args.long_min_words,
            )
            rows.append(
                LessonRow(
                    course_id=str(rec["course_id"]),
                    course=rec["course_title"],
                    lesson_code=rec["lesson_code"],
                    lesson_title=rec["lesson_title"],
                    kind=kind,
                    language=lang,
                    is_introductory=bool(rec["is_introductory"]),
                    is_assessment=bool(rec["is_assessment"]),
                    generated_at=generated_at.isoformat() if generated_at else None,
                    estimated_word_count=ewc_by_kind.get(kind),
                    metrics=metrics,
                    **_tokens_meta(tokens_by_kind[kind]),
                )
            )
    rows.sort(key=row_sort_key)
    return rows


def filter_rows(rows: list[LessonRow], args: argparse.Namespace) -> list[LessonRow]:
    """Filtri applicati anche alle righe lette da JSONL (già filtrate lato DB)."""
    since = _parse_since(args.since)
    out: list[LessonRow] = []
    for r in rows:
        if args.course and not (
            args.course == r.course_id or args.course.lower() in r.course.lower()
        ):
            continue
        if args.lesson and r.lesson_code != args.lesson:
            continue
        if r.is_assessment and not args.include_assessment:
            continue
        if since is not None:
            try:
                gen = datetime.fromisoformat(r.generated_at) if r.generated_at else None
            except ValueError:
                gen = None
            if gen is None or gen < since:
                continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def indicator_values(m: TextMetrics) -> dict[str, float]:
    """Indicatori normalizzati usati da `--by-lesson` e `--compare`."""
    return {
        "parole": float(m.words),
        "frasi": float(m.sentences),
        "frase_media": m.sent_len_mean,
        "frase_std": m.sent_len_std,
        "short_ratio": m.short_ratio,
        "punch_1k": per_1000(m.punchline_after_long, m.words),
        "formule_1k": per_1000(m.formula_hits, m.words),
        "evalA_nj_1k": per_1000(m.evaluative_unjustified_a, m.words),
        "evalA_tot_1k": per_1000(m.evaluative_total_a, m.words),
        "evalB_nj_1k": per_1000(m.evaluative_unjustified_b, m.words),
        "domande_100": m.rhetorical_questions,
        "antitesi_100": m.antithesis_openers,
    }


def _group_label(row: LessonRow, group_introductory: bool) -> str:
    if not group_introductory:
        return "tutte"
    return "intro" if row.is_introductory else "standard"


def _aggregate_group(
    course: str, kind: str, group: str, rows: Sequence[LessonRow]
) -> dict[str, Any]:
    n = len(rows)
    ms = [r.metrics for r in rows]
    words = sum(m.words for m in ms)
    sentences = sum(m.sentences for m in ms)

    def tot(attr: str) -> int:
        return sum(getattr(m, attr) for m in ms)

    def mean(attr: str) -> float:
        return round(statistics.fmean(getattr(m, attr) for m in ms), 3) if ms else 0.0

    def triple(prefix: str, attr: str) -> dict[str, Any]:
        t = tot(attr)
        return {
            f"{prefix}_tot": t,
            f"{prefix}_lez": round(t / n, 3) if n else 0.0,
            f"{prefix}_1k": per_1000(t, words),
        }

    out: dict[str, Any] = {
        "script_version": SCRIPT_VERSION,
        "corso": course,
        "tipo": kind,
        "gruppo": group,
        "lezioni": n,
        "parole": words,
        "frasi": sentences,
        "frase_media": mean("sent_len_mean"),
        "frase_std": mean("sent_len_std"),
        "short_ratio": mean("short_ratio"),
    }
    out.update(triple("punch", "punchline_after_long"))
    out.update(triple("formule", "formula_hits"))
    out.update(triple("evalA_nj", "evaluative_unjustified_a"))
    out["evalA_tot"] = tot("evaluative_total_a")
    out.update(triple("evalB_nj", "evaluative_unjustified_b"))
    out["domande_100"] = per_100(tot("questions_count"), sentences)
    out["antitesi_100"] = per_100(tot("antithesis_count"), sentences)
    return out


def aggregate_rows(rows: Sequence[LessonRow], *, group_introductory: bool) -> list[dict[str, Any]]:
    """Una riga per corso x tipo (x gruppo) + righe TOTALE per tipo (x gruppo)."""
    by_course: dict[tuple[str, str, str, str], list[LessonRow]] = {}
    by_total: dict[tuple[str, str], list[LessonRow]] = {}
    for r in rows:
        grp = _group_label(r, group_introductory)
        by_course.setdefault((r.course, r.course_id, r.kind, grp), []).append(r)
        by_total.setdefault((r.kind, grp), []).append(r)

    out = [
        _aggregate_group(course, kind, grp, grouped)
        for (course, _cid, kind, grp), grouped in sorted(
            by_course.items(),
            key=lambda kv: (kv[0][0], kv[0][1], _kind_index(kv[0][2]), kv[0][3]),
        )
    ]
    if len({r.course_id for r in rows}) > 1:
        out.extend(
            _aggregate_group("TOTALE", kind, grp, grouped)
            for (kind, grp), grouped in sorted(
                by_total.items(), key=lambda kv: (_kind_index(kv[0][0]), kv[0][1])
            )
        )
    return out


def by_lesson_rows(rows: Sequence[LessonRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        m = r.metrics
        row: dict[str, Any] = {
            "script_version": SCRIPT_VERSION,
            "corso": r.course,
            "lezione": r.lesson_code,
            "tipo": r.kind,
            "intro": r.is_introductory,
            "parole": m.words,
            "frasi": m.sentences,
            "frase_media": m.sent_len_mean,
            "frase_std": m.sent_len_std,
            "short_ratio": m.short_ratio,
            "punch": m.punchline_after_long,
            "punch_1k": per_1000(m.punchline_after_long, m.words),
            "formule": m.formula_hits,
            "formule_1k": per_1000(m.formula_hits, m.words),
            "evalA_nj": m.evaluative_unjustified_a,
            "evalA_nj_1k": per_1000(m.evaluative_unjustified_a, m.words),
            "evalA_tot": m.evaluative_total_a,
            "evalB_nj": m.evaluative_unjustified_b,
            "evalB_nj_1k": per_1000(m.evaluative_unjustified_b, m.words),
            "domande_100": m.rhetorical_questions,
            "antitesi_100": m.antithesis_openers,
            "generated_at": r.generated_at,
            "cost_usd": r.cost_usd,
        }
        out.append(row)
    return out


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "sì" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown_table(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return "_(nessuna riga)_"
    columns = list(rows[0].keys())
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_fmt(row.get(c)).replace("|", "\\|") for c in columns) + " |"
        for row in rows
    )
    return "\n".join(lines)


def render_csv(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return ""
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _fmt(v) if isinstance(v, bool) else v for k, v in row.items()})
    return buf.getvalue()


def _clip(text: str, limit: int = 220) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def render_top(rows: Sequence[LessonRow], k: int) -> str:
    """Le prime K frasi flaggate, a rotazione fra i tipi di flag (ordine documento)."""
    by_kind: dict[str, list[tuple[LessonRow, FlaggedSentence]]] = {}
    for r in rows:
        for f in r.metrics.flagged:
            by_kind.setdefault(f.kind, []).append((r, f))
    picked: list[tuple[LessonRow, FlaggedSentence]] = []
    queues = [by_kind[kind] for kind in sorted(by_kind)]
    depth = max((len(q) for q in queues), default=0)
    for i in range(depth):
        for q in queues:
            if i < len(q) and len(picked) < k:
                picked.append(q[i])
    if not picked:
        return f"## Top {k} frasi flaggate\n\n_(nessuna frase flaggata)_"
    lines = [f"## Top {k} frasi flaggate ({len(picked)} mostrate)", ""]
    for r, f in picked:
        lines.append(f"- [{f.kind}] {r.course} {r.lesson_code} ({r.kind})")
        if f.prev:
            lines.append(f"  - prima: {_clip(f.prev)}")
        lines.append(f"  - frase: {_clip(f.sentence)}")
    return "\n".join(lines)


def render_compare(
    before: Sequence[LessonRow], after: Sequence[LessonRow], versions: tuple[set[str], set[str]]
) -> str:
    """Join per (corso, lesson_code, tipo) e delta medio per indicatore, per tipo."""
    lines = [
        f"# measure_register --compare — SCRIPT_VERSION={SCRIPT_VERSION}",
        f"versioni nei file: BEFORE={sorted(versions[0])} AFTER={sorted(versions[1])}",
    ]
    if versions[0] | versions[1] != {SCRIPT_VERSION}:
        lines.append(
            "ATTENZIONE: versioni diverse dallo script corrente, il confronto non è affidabile."
        )
    b_idx = {(r.course, r.lesson_code, r.kind): r for r in before}
    a_idx = {(r.course, r.lesson_code, r.kind): r for r in after}
    kinds = sorted({k[2] for k in b_idx} | {k[2] for k in a_idx}, key=_kind_index)
    for kind in kinds:
        b_keys = {k for k in b_idx if k[2] == kind}
        a_keys = {k for k in a_idx if k[2] == kind}
        joined = sorted(b_keys & a_keys)
        lines.extend(
            [
                "",
                f"## {kind} — coppie: {len(joined)} "
                f"(solo BEFORE: {len(b_keys - a_keys)}, solo AFTER: {len(a_keys - b_keys)})",
                "",
            ]
        )
        if not joined:
            continue
        b_vals = [indicator_values(b_idx[k].metrics) for k in joined]
        a_vals = [indicator_values(a_idx[k].metrics) for k in joined]
        table: list[dict[str, Any]] = []
        for name in b_vals[0]:
            mb = statistics.fmean(v[name] for v in b_vals)
            ma = statistics.fmean(v[name] for v in a_vals)
            delta = ma - mb
            pct = (delta / mb * 100.0) if mb else None
            table.append(
                {
                    "indicatore": name,
                    "prima": round(mb, 3),
                    "dopo": round(ma, 3),
                    "delta": round(delta, 3),
                    "delta_%": None if pct is None else round(pct, 2),
                }
            )
        lines.append(render_markdown_table(table))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_formulas(path: str | None) -> dict[str, list[str]]:
    formulas = {lang: list(v) for lang, v in DEFAULT_FORMULAS.items()}
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for lang, items in _as_dict(data).items():
            formulas[resolve_language(lang)] = [str(x) for x in _as_list(items)]
    return formulas


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.measure_register",
        description=(
            "Misura il registro dei contenuti generati (strumento diagnostico, "
            f"non un gate). SCRIPT_VERSION={SCRIPT_VERSION}"
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--db", action="store_true", help="Legge dal database (default; sola lettura)."
    )
    source.add_argument(
        "--from-jsonl", metavar="PATH", help="Rilegge un file prodotto da --export-jsonl."
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE.jsonl", "AFTER.jsonl"),
        help="Confronta due export (join per corso, lezione, tipo) e stampa i delta.",
    )
    parser.add_argument("--course", help="Filtro corso: parte del titolo (ILIKE) oppure UUID.")
    parser.add_argument("--lesson", help="Filtro lesson_code esatto, es. M1.L1.")
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", help="Solo contenuti generati da questa data (per tipo)."
    )
    parser.add_argument(
        "--include-assessment",
        action="store_true",
        help="Include le lezioni di verifica (default: saltate).",
    )
    parser.add_argument(
        "--language",
        choices=("it", "en", "auto"),
        default="auto",
        help="Lingua dei pattern (auto = course.language_code[:2], fallback it).",
    )
    parser.add_argument("--format", choices=("table", "csv"), default="table")
    parser.add_argument("--by-lesson", action="store_true", help="Una riga per lezione x tipo.")
    parser.add_argument(
        "--group-introductory",
        action="store_true",
        help="Nelle righe aggregate separa le lezioni introduttive.",
    )
    parser.add_argument(
        "--top", type=int, default=0, metavar="K", help="Stampa le K frasi flaggate in coda."
    )
    parser.add_argument(
        "--export-jsonl", metavar="PATH", help="Scrive una riga JSON per lezione x tipo."
    )
    parser.add_argument(
        "--formulas-file",
        metavar="PATH",
        help='JSON {"it": [...], "en": [...]} che sostituisce le formule di default.',
    )
    parser.add_argument("--punch-max-words", type=int, default=PUNCH_MAX_WORDS)
    parser.add_argument("--long-min-words", type=int, default=LONG_MIN_WORDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.compare:
        before, v_before = load_jsonl(Path(args.compare[0]))
        after, v_after = load_jsonl(Path(args.compare[1]))
        print(
            render_compare(filter_rows(before, args), filter_rows(after, args), (v_before, v_after))
        )
        return 0

    formulas = load_formulas(args.formulas_file)
    if args.from_jsonl:
        rows, versions = load_jsonl(Path(args.from_jsonl))
        rows = sorted(filter_rows(rows, args), key=row_sort_key)
        source = f"jsonl:{args.from_jsonl} (versioni {sorted(versions)})"
        if versions - {SCRIPT_VERSION}:
            print(
                f"[!] il file contiene righe con SCRIPT_VERSION {sorted(versions)} "
                f"!= {SCRIPT_VERSION}: confronti non affidabili.",
                file=sys.stderr,
            )
    else:
        rows = asyncio.run(load_rows_from_db(args, formulas))
        source = "db"

    if args.export_jsonl:
        write_jsonl(Path(args.export_jsonl), rows)
        print(f"[i] export JSONL: {args.export_jsonl} ({len(rows)} righe)", file=sys.stderr)

    report_rows = (
        by_lesson_rows(rows)
        if args.by_lesson
        else aggregate_rows(rows, group_introductory=args.group_introductory)
    )
    if args.format == "csv":
        # Lo stdout resta CSV puro: la versione è una colonna, il --top va su stderr.
        sys.stdout.write(render_csv(report_rows))
        if args.top > 0:
            print(render_top(rows, args.top), file=sys.stderr)
        return 0

    print(
        f"# measure_register — SCRIPT_VERSION={SCRIPT_VERSION} — sorgente: {source} "
        f"— righe lezione x tipo: {len(rows)}"
    )
    print("_Strumento diagnostico, non gate: confrontare solo run con lo stesso SCRIPT_VERSION._")
    print()
    print(render_markdown_table(report_rows))
    if args.top > 0:
        print()
        print(render_top(rows, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
