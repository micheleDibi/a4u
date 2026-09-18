"""WP8 — il rimando testuale vale anche nella prosa delle slide e del discorso.

Difetto chiuso (prova di consegna, `asset_tags_left = 1` in tutti i PDF
slide): un tag `[FIG:id]` lasciato dal modello nella prosa di una slide
restava LETTERALE nel PDF delle slide, nel PDF del discorso e nelle viste,
mentre in dispensa lo stesso tag diventava «Figura 1». La normalizzazione
era applicata al solo corpo della dispensa.

Oracoli (tutti falliscono sul codice precedente):

- PDF slide e PDF discorso VERI (WeasyPrint + pypdf): «Figura 1» nel testo
  estratto e nessun `[FIG:` residuo;
- parità del numero fra dispensa e slide per lo stesso asset, sulla stessa
  lezione e con più kind: i numeri sono quelli della dispensa, calcolati
  una volta sola (`base_pdf.lesson_asset_refs`);
- un tag verso un id inesistente — e un tag verso un asset dichiarato solo
  in Fase 4, che la dispensa non numera — resta com'è e produce UN evento
  (`slide_asset_ref_unresolved` / `speech_asset_ref_unresolved`) per slide;
- le didascalie in una riga (figura, tabella, label dell'equazione, titolo
  dell'esempio) ricevono lo stesso rimando, in dispensa e nelle slide;
- parità collector/renderer con i tag: il collector del math vede le
  stesse chiavi del renderer anche quando una formula sta a cavallo di un
  rimando riscritto;
- mirror frontend: `lib/lessonAssetRefs.ts`, compilato con l'esbuild del
  frontend ed eseguito con Node, produce gli stessi rimandi del backend
  sugli stessi contenuti.

Limite dichiarato e pinnato (NON corretto qui): `sanitize_tts_text` non
tocca i tag, quindi il testo letto dalla voce conserva `[FIG:id]`; vedi
`docs/courses/11-lesson-speech.md`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
import structlog.testing

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import course_lesson_speech_pdf_service as speech_pdf
from app.services import course_lesson_speech_service as speech_svc
from tests.test_lesson_pdf_figures import SVG_A, _pdf_text_and_warnings, _weasyprint

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_LESSON_ASSET_REFS_TS = _FRONTEND / "src" / "lib" / "lessonAssetRefs.ts"
_LOCALES = _FRONTEND / "src" / "i18n" / "locales"


# ---------------------------------------------------------------------------
# Corpus: una lezione con i quattro kind, citati nella prosa delle slide
# ---------------------------------------------------------------------------


def _content_raw() -> dict[str, Any]:
    """Dispensa con due figure, una tabella, un'equazione-teorema e un
    esempio. `dip` è citata DOPO `iter`, la tabella è citata nel corpo,
    l'esempio non è mai citato (coda A12): i numeri nascono da qui."""
    return {
        "introduction": "Come mostra [FIG:iter] il metodo converge.",
        "sections": [
            {
                "section_id": "S1",
                "title": "Dipendenze",
                "content": "Le dipendenze sono in [FIG:dip]; i costi in [TAB:costi].\n"
                "L'enunciato è [EQ:lem].",
            }
        ],
        "summary": "Sintesi.",
        "key_takeaways": [],
        "references": [],
        "visual_assets": [
            {
                "asset_id": "iter",
                "format": "mermaid",
                "content": "flowchart LR\n  A --> B",
                "caption": "Ciclo del metodo",
                "alt_text": "ciclo",
            },
            {
                "asset_id": "dip",
                "format": "mermaid",
                "content": "flowchart LR\n  C --> D",
                "caption": "Dipendenze di [FIG:iter]",
                "alt_text": "dipendenze",
            },
        ],
        "tables": [
            {
                "table_id": "costi",
                "caption": "Costi, come in [FIG:iter]",
                "markdown": "| Metodo | Costo |\n|---|---|\n| Jacobi | n |",
            }
        ],
        "equations": [
            {
                "equation_id": "lem",
                "kind": "lemma",
                "latex": "x=1",
                "label": "Convergenza di [FIG:iter]",
                "statement": "Se il raggio spettrale e minore di uno.",
                "proof": [{"text": "Passo unico.", "latex": "y=2"}],
            }
        ],
        "examples": [
            {
                "example_id": "es",
                "title": "Applicazione di [TAB:costi]",
                "content": "Testo dell'esempio.",
            }
        ],
    }


def _slides_raw(**overrides: Any) -> dict[str, Any]:
    base = {
        "slides": [
            {
                "slide_id": "s1",
                "slide_number": 1,
                "type": "concept",
                "title": "Il ciclo di [FIG:iter]",
                "body": "Come mostra [FIG:iter] il sistema converge.",
                "bullets": [
                    "Il ramo di ritorno di [FIG:iter] riformula lo splitting.",
                    "I costi stanno in [TAB:costi], l'enunciato in [EQ:lem].",
                ],
                "references_assets": ["iter"],
            }
        ],
        "new_assets": [],
        "new_tables": [],
        "new_equations": [],
        "new_examples": [],
    }
    return {**base, **overrides}


def _speech_raw(text: str = "Guardate [FIG:iter]: il ciclo torna indietro.") -> dict[str, Any]:
    return {
        "speech_segments": [
            {
                "segment_id": "g1",
                "text": text,
                "delivery_notes": "Indicare [TAB:costi] sulla slide.",
                "estimated_duration_seconds": 30,
                "estimated_word_count": 60,
            }
        ],
        "slide_to_segments_map": [
            {"slide_id": "s1", "segment_ids": ["g1"], "slide_total_duration_seconds": 30}
        ],
        "estimated_total_duration_seconds": 30,
        "estimated_total_word_count": 60,
    }


def _course(language: str = "it") -> Course:
    return Course(title="Corso di prova", language_code=language, cfu=6)


def _lesson(
    content_raw: dict[str, Any] | None = None,
    slides_raw: dict[str, Any] | None = None,
    speech_raw: dict[str, Any] | None = None,
) -> CourseLesson:
    return CourseLesson(
        lesson_code="M1.L1",
        title="Lezione di prova",
        content_raw=_content_raw() if content_raw is None else content_raw,
        slides_raw=_slides_raw() if slides_raw is None else slides_raw,
        speech_raw=_speech_raw() if speech_raw is None else speech_raw,
    )


def _svg_map() -> dict[str, str]:
    return {"iter": SVG_A, "dip": SVG_A}


def _slides_html(lesson: CourseLesson, *, language: str = "it") -> str:
    return slides_pdf.render_slides_html(
        course=_course(language),
        lesson=lesson,
        organization=None,
        slide_template=None,
        visual_svg_map=_svg_map(),
        enable_split=False,
    )


def _speech_html(lesson: CourseLesson, *, language: str = "it") -> str:
    return speech_pdf.render_speech_html(
        course=_course(language),
        lesson=lesson,
        organization=None,
        pdf_template=None,
    )


def _lesson_html(lesson: CourseLesson, *, language: str = "it") -> str:
    return pdf.render_lesson_html(
        course=_course(language),
        lesson=lesson,
        organization=None,
        pdf_template=None,
        visual_svg_map=_svg_map(),
    )


# ---------------------------------------------------------------------------
# 1. I PDF veri: nessun tag letterale, il rimando al suo posto
# ---------------------------------------------------------------------------


def test_slides_pdf_shows_the_textual_reference_not_the_raw_tag() -> None:
    """L'oracolo del difetto: nel PDF delle slide il docente legge
    «Figura 1», non «[FIG:iter]». Misurato sul PDF vero, come la prova di
    consegna (`asset_tags_left`)."""
    weasyprint = _weasyprint()
    _data, text, _warnings = _pdf_text_and_warnings(weasyprint, _slides_html(_lesson()))
    assert "[FIG:" not in text and "[TAB:" not in text and "[EQ:" not in text
    assert text.count("Figura 1") == 3  # titolo, prosa, primo bullet
    assert "Tabella 1" in text and "Lemma 1" in text


def test_speech_pdf_shows_the_textual_reference_not_the_raw_tag() -> None:
    weasyprint = _weasyprint()
    _data, text, _warnings = _pdf_text_and_warnings(weasyprint, _speech_html(_lesson()))
    assert "[FIG:" not in text and "[TAB:" not in text
    # titolo della slide + testo del segmento
    assert text.count("Figura 1") == 2
    assert "Tabella 1" in text  # note al docente


def test_lesson_and_slides_pdf_agree_on_every_asset_number() -> None:
    """Parità dei numeri fra dispensa e slide per lo stesso asset, sul PDF
    vero: se le slide ricalcolassero la numerazione sul proprio contenuto
    fuso, `dip` (mai citata dalle slide) e i nuovi asset di Fase 4
    sposterebbero i numeri."""
    weasyprint = _weasyprint()
    lesson = _lesson()
    _d1, lesson_text, _w1 = _pdf_text_and_warnings(weasyprint, _lesson_html(lesson))
    _d2, slides_text, _w2 = _pdf_text_and_warnings(weasyprint, _slides_html(lesson))
    for reference in ("Figura 1", "Tabella 1", "Lemma 1"):
        assert reference in lesson_text, reference
        assert reference in slides_text, reference
    # `dip` è la seconda figura in dispensa e non è citata dalle slide: il
    # suo numero non può cambiare l'1 di `iter`.
    assert "Figura 2." in lesson_text


def test_numbers_come_from_the_lesson_body_not_from_the_slide_surface() -> None:
    """Stessa mappa, stessa funzione: `lesson_asset_refs` è la numerazione
    della dispensa, e le slide la riusano senza ricalcolarla."""
    content = _content_raw()
    refs = pdf.lesson_asset_refs(content, language="it")
    assert refs.asset_numbers == {
        ("FIG", "iter"): 1,
        ("FIG", "dip"): 2,
        ("TAB", "costi"): 1,
        ("EQ", "lem"): 1,
        ("EX", "es"): 1,
    }
    assert refs.cite("Vedi [FIG:dip] e [EX:es].") == "Vedi Figura 2 e Esempio 1."
    # Il ramo teorema usa la parola del kind, come il blocco e il PDF.
    assert refs.cite("[EQ:lem]") == "Lemma 1"


# ---------------------------------------------------------------------------
# 2. Tag non risolvibili: nessun rimando inventato, un evento per slide
# ---------------------------------------------------------------------------


def _events(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry.get("event") == event]


def test_an_unknown_tag_stays_literal_and_is_logged_once_per_slide() -> None:
    slides = _slides_raw(
        slides=[
            {
                "slide_id": "s1",
                "slide_number": 1,
                "type": "concept",
                "title": "Titolo con [FIG:ignoto]",
                "body": "Prosa con [FIG:ignoto] e [FIG:iter].",
                "bullets": ["Bullet con [TAB:assente]."],
                "references_assets": ["iter"],
            }
        ]
    )
    with structlog.testing.capture_logs() as logs:
        html = _slides_html(_lesson(slides_raw=slides))
    assert "[FIG:ignoto]" in html and "[TAB:assente]" in html
    assert "Figura 1" in html
    events = _events(logs, "slide_asset_ref_unresolved")
    assert len(events) == 1, events
    assert events[0]["slide_id"] == "s1"
    assert events[0]["tags"] == ["[FIG:ignoto]", "[TAB:assente]"]


def test_a_phase_four_only_asset_is_not_numbered_and_stays_literal() -> None:
    """Un asset dichiarato solo in Fase 4 non ha numero in dispensa: un
    rimando inventato andrebbe in collisione con «Figura 1» del corpo."""
    slides = _slides_raw(
        slides=[
            {
                "slide_id": "s1",
                "slide_number": 1,
                "type": "concept",
                "title": "Nuovo",
                "body": "Vedi [FIG:nuovo] accanto a [FIG:iter].",
                "bullets": [],
                "references_assets": ["nuovo"],
            }
        ],
        new_assets=[
            {
                "asset_id": "nuovo",
                "format": "mermaid",
                "content": "flowchart LR\n  E --> F",
                "caption": "Nuova di Fase 4",
                "alt_text": "nuova",
            }
        ],
    )
    with structlog.testing.capture_logs() as logs:
        html = _slides_html(_lesson(slides_raw=slides))
    assert "[FIG:nuovo]" in html and "Figura 1" in html
    events = _events(logs, "slide_asset_ref_unresolved")
    assert len(events) == 1 and events[0]["tags"] == ["[FIG:nuovo]"]


def test_an_unknown_tag_in_the_speech_stays_literal_and_is_logged_per_slide() -> None:
    with structlog.testing.capture_logs() as logs:
        html = _speech_html(_lesson(speech_raw=_speech_raw("Vedi [FIG:ignoto] e [FIG:iter].")))
    assert "[FIG:ignoto]" in html and "Figura 1" in html
    events = _events(logs, "speech_asset_ref_unresolved")
    assert len(events) == 1 and events[0]["tags"] == ["[FIG:ignoto]"]
    assert events[0]["slide_id"] == "s1"


@pytest.mark.parametrize(
    "content_raw",
    [
        pytest.param(None, id="assente"),
        pytest.param({"questions": [{"text": "Domanda"}]}, id="lezione-verifica"),
        pytest.param(
            {"introduction": "x", "sections": ["non un dict"], "visual_assets": []},
            id="sezione-non-oggetto",
        ),
        pytest.param(["non un dict"], id="corpo-non-oggetto"),
        pytest.param(
            {"introduction": 3, "sections": {"a": 1}, "summary": ["x"]},
            id="campi-di-tipo-sbagliato",
        ),
    ],
)
def test_a_malformed_lesson_body_does_not_break_slides_and_speech(
    content_raw: dict[str, Any] | None,
) -> None:
    """Da WP8 slide e discorso LEGGONO il corpo della dispensa per averne
    i numeri: un `content_raw` che quel corpo non sa concatenare non deve
    far fallire due superfici che prima non lo toccavano affatto.
    `LessonContentOutput` impedisce la forma, ma il contenuto in archivio
    non ripassa dallo schema. Senza la guardia il caso
    `sezione-non-oggetto` esce con `AttributeError: 'str' object has no
    attribute 'get'`; i tag restano letterali, come per una lezione senza
    asset."""
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione di prova",
        content_raw=content_raw,
        slides_raw=_slides_raw(),
        speech_raw=_speech_raw(),
    )
    with structlog.testing.capture_logs() as logs:
        slides_html = _slides_html(lesson)
        speech_html = _speech_html(lesson)
    assert "[FIG:iter]" in slides_html and "[FIG:iter]" in speech_html
    assert len(_events(logs, "slide_asset_ref_unresolved")) == 1
    assert len(_events(logs, "speech_asset_ref_unresolved")) == 1


# ---------------------------------------------------------------------------
# 3. Didascalie: stesso rimando nelle due superfici
# ---------------------------------------------------------------------------


def test_captions_get_the_same_reference_in_lesson_and_slides() -> None:
    """Didascalia di figura e tabella, label dell'equazione e titolo
    dell'esempio: il tag diventa rimando, e il testo è lo stesso nelle due
    superfici (la didascalia è un solo testo d'autore)."""
    lesson = _lesson(
        slides_raw=_slides_raw(
            slides=[
                {
                    "slide_id": "s1",
                    "slide_number": 1,
                    "type": "concept",
                    "title": "Asset",
                    "body": "",
                    "bullets": [],
                    "references_assets": ["dip", "costi", "lem", "es"],
                }
            ]
        )
    )
    for html in (_lesson_html(lesson), _slides_html(lesson)):
        assert "Dipendenze di Figura 1" in html
        assert "Costi, come in Figura 1" in html
        assert "Convergenza di Figura 1" in html
        assert "Applicazione di Tabella 1" in html
        assert "[FIG:iter]" not in html and "[TAB:costi]" not in html


def test_the_reference_is_written_after_the_author_prefix_is_stripped() -> None:
    """Ordine fissato: `strip_figure_prefix` lavora sul testo d'autore e
    solo dopo arriva il rimando. All'incontrario «[FIG:iter]. Ciclo»
    diventerebbe «Figura 1. Ciclo» e lo strip del prefisso mangerebbe il
    rimando."""
    content = _content_raw()
    content["visual_assets"][1]["caption"] = "[FIG:iter]. Ciclo ripreso"
    html = _lesson_html(_lesson(content_raw=content))
    assert "Figura 1. Ciclo ripreso" in html


def _aria_labels(html: str) -> list[str]:
    return re.findall(r'aria-label="([^"]*)"', html)


def test_the_accessible_name_says_what_the_caption_says() -> None:
    """Senza `alt_text` l'accessible name del blocco figura è la
    didascalia: deve essere quella CITATA, non il tag grezzo. Il mirror
    `FigureFrame.tsx` cita già (`aria-label={altText || text}` con `text =
    cite(stripped)`), quindi un `aria-label` non citato farebbe dire al
    PDF «Dipendenze di [FIG:iter]» e alla vista «Dipendenze di Figura 1»
    sulla stessa figura."""
    content = _content_raw()
    # `alt_text: str = Field(default="", …)` nei due schemi: la didascalia
    # come accessible name è un caso raggiungibile, non teorico.
    content["visual_assets"][1]["alt_text"] = ""
    lesson = _lesson(
        content_raw=content,
        slides_raw=_slides_raw(
            slides=[
                {
                    "slide_id": "s1",
                    "slide_number": 1,
                    "type": "concept",
                    "title": "Asset",
                    "body": "",
                    "bullets": [],
                    "references_assets": ["dip"],
                }
            ]
        ),
    )
    for html in (_lesson_html(lesson), _slides_html(lesson)):
        labels = _aria_labels(html)
        assert "Dipendenze di Figura 1" in labels, labels
        assert not [label for label in labels if "[FIG:" in label], labels


def test_the_author_alt_text_wins_and_is_never_cited() -> None:
    """`alt_text` è testo d'autore e resta com'è su entrambi i lati: il
    mirror usa `altText` grezzo, quindi citarlo qui romperebbe la parità
    al contrario."""
    content = _content_raw()
    content["visual_assets"][1]["alt_text"] = "Dipendenze di [FIG:iter]"
    labels = _aria_labels(_lesson_html(_lesson(content_raw=content)))
    assert "Dipendenze di [FIG:iter]" in labels, labels


# ---------------------------------------------------------------------------
# 4. Parità collector/renderer con i rimandi riscritti
# ---------------------------------------------------------------------------


def test_slides_collector_sees_the_reference_the_renderer_writes() -> None:
    """Una formula a cavallo di un rimando (`$a [FIG:iter] b$`) è una
    chiave sola: se il collector leggesse il tag grezzo, il renderer
    cercherebbe una chiave che la mappa non ha e ricadrebbe sul MathML."""
    from tests.test_lesson_pdf_math import RecordingMap

    slides = _slides_raw(
        slides=[
            {
                "slide_id": "s1",
                "slide_number": 1,
                "type": "concept",
                "title": "Titolo",
                "body": "Formula $a [FIG:iter] b$ in frase.",
                "bullets": [],
                # L'equazione è resa qui: il collector delle slide raccoglie
                # per TUTTI gli asset della lezione, il renderer solo per
                # quelli citati, e l'uguaglianza vale a parità di perimetro.
                "references_assets": ["lem"],
            }
        ]
    )
    lesson = _lesson(slides_raw=slides)
    collected = set(
        pdf._collect_math_from_content(
            slides_pdf._math_content_for_slides(lesson.content_raw, slides, language="it")
        )
    )
    assert ("a Figura 1 b", "inline") in collected
    rec = RecordingMap()
    html = slides_pdf.render_slides_html(
        course=_course(),
        lesson=lesson,
        organization=None,
        slide_template=None,
        visual_svg_map=_svg_map(),
        math_svg_map=rec,
        enable_split=False,
    )
    assert set(rec.seen) == collected
    assert "<math" not in html


def test_speech_collector_sees_the_reference_the_renderer_writes() -> None:
    from tests.test_lesson_pdf_math import RecordingMap

    lesson = _lesson(speech_raw=_speech_raw("Formula $a [FIG:iter] b$ letta."))
    collected = set(
        pdf._collect_math_from_content(speech_pdf._math_content_for_speech(lesson, language="it"))
    )
    assert ("a Figura 1 b", "inline") in collected
    rec = RecordingMap()
    html = speech_pdf.render_speech_html(
        course=_course(),
        lesson=lesson,
        organization=None,
        pdf_template=None,
        math_svg_map=rec,
    )
    assert set(rec.seen) == collected
    assert "<math" not in html


def test_the_reference_follows_the_course_language() -> None:
    html = _slides_html(_lesson(), language="en")
    assert "Figure 1" in html and "[FIG:iter]" not in html


# ---------------------------------------------------------------------------
# 5. Limite dichiarato: il testo del TTS conserva il tag
# ---------------------------------------------------------------------------


def test_tts_sanitizer_leaves_the_tag_in_the_spoken_text() -> None:
    """Limite dichiarato (docs/courses/11-lesson-speech.md): i caratteri
    proibiti del TTS non comprendono le parentesi quadre, quindi
    `sanitize_tts_text` NON tocca il tag e la voce legge «FIG due punti
    iter». Non è corretto qui: il testo del parlato è contenuto
    PERSISTITO, mentre il rimando è una normalizzazione di render; farne
    una mutazione in materializzazione darebbe due comportamenti diversi
    a seconda di quando la lezione è stata generata."""
    assert speech_svc.sanitize_tts_text("Guardate [FIG:iter] qui.") == "Guardate [FIG:iter] qui."
    assert speech_svc.validate_tts_safety("Guardate [FIG:iter] qui.") == []


# ---------------------------------------------------------------------------
# 6. Mirror frontend (bundle esbuild, come gli altri test di parità)
# ---------------------------------------------------------------------------


_RUNNER = """
const m = require(process.argv[2]);
const fs = require("node:fs");
const locale = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const flat = {};
(function walk(node, prefix) {
  for (const [k, v] of Object.entries(node)) {
    if (typeof v === "string") flat[prefix + k] = v;
    else if (v && typeof v === "object") walk(v, prefix + k + ".");
  }
})(locale, "");
const t = (key, opts) => {
  let text = flat[key];
  if (text === undefined) return (opts && opts.defaultValue) || key;
  for (const [k, v] of Object.entries(opts || {})) {
    text = text.split("{{" + k + "}}").join(String(v));
  }
  return text;
};
const cases = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const out = [];
for (const c of cases) {
  const refs = m.lessonAssetRefs(c.content, t);
  out.push({
    numbers: Object.fromEntries(refs.assetNumbers),
    cited: c.texts.map((x) => refs.cite(x)),
    unresolved: refs.unresolved(...c.texts),
  });
}
process.stdout.write(JSON.stringify(out));
"""


def _mirror_cases() -> list[dict[str, Any]]:
    content = _content_raw()
    return [
        {
            "content": content,
            "texts": [
                "Come mostra [FIG:iter] il sistema converge.",
                "Le dipendenze sono in [FIG:dip]; i costi in [TAB:costi].",
                "L'enunciato e [EQ:lem], l'esempio [EX:es].",
                "Un tag verso [FIG:ignoto] resta com'e.",
                "[fig:iter] minuscolo non e un tag.",
                "Nessun tag qui.",
            ],
        },
        {
            "content": {
                "introduction": "",
                "sections": [],
                "summary": "",
                "visual_assets": [],
                "tables": [],
                "equations": [],
                "examples": [],
            },
            "texts": ["Vedi [FIG:iter]."],
        },
    ]


def _bundle_lesson_asset_refs(tmp: Path) -> Path:
    """`lib/lessonAssetRefs.ts` compilato in CommonJS con l'esbuild del
    frontend: la parità esegue il modulo VERO, non una copia."""
    esbuild = _FRONTEND / "node_modules" / ".bin" / "esbuild"
    if not esbuild.is_file() or shutil.which("node") is None:
        pytest.skip("esbuild del frontend o node non disponibili")
    out = tmp / "bundle.cjs"
    proc = subprocess.run(
        [
            str(esbuild),
            str(_LESSON_ASSET_REFS_TS),
            "--bundle",
            "--format=cjs",
            "--platform=node",
            "--target=es2020",
            f"--tsconfig={_FRONTEND / 'tsconfig.app.json'}",
            f"--outfile={out}",
        ],
        cwd=str(_FRONTEND),
        env={**os.environ, "NODE_PATH": str(_FRONTEND / "node_modules")},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"esbuild non riuscito: {proc.stderr.strip()[:300]}")
    return out


def test_frontend_mirror_produces_the_same_references() -> None:
    """Parità BE/FE eseguita davvero: numeri, rimandi e tag irrisolti di
    `lib/lessonAssetRefs.ts` coincidono con `lesson_asset_refs`."""
    assert _LESSON_ASSET_REFS_TS.is_file(), f"mirror assente: {_LESSON_ASSET_REFS_TS}"
    node = shutil.which("node")
    if node is None:
        pytest.skip("node non disponibile: parità frontend non eseguibile")
    cases = _mirror_cases()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bundle = _bundle_lesson_asset_refs(tmp)
        runner = tmp / "runner.cjs"
        runner.write_text(_RUNNER, encoding="utf-8")
        payload = tmp / "cases.json"
        payload.write_text(json.dumps(cases), encoding="utf-8")
        proc = subprocess.run(
            [node, str(runner), str(bundle), str(_LOCALES / "it.json"), str(payload)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    for case, fe in zip(cases, got, strict=True):
        refs = pdf.lesson_asset_refs(case["content"], language="it")
        expected_numbers = {f"{kind}:{aid}": n for (kind, aid), n in refs.asset_numbers.items()}
        assert fe["numbers"] == expected_numbers
        assert fe["cited"] == [refs.cite(text) for text in case["texts"]]
        assert fe["unresolved"] == refs.unresolved(*case["texts"])


def test_frontend_views_wire_the_reference_into_prose_and_captions() -> None:
    """Le due viste passano prosa e didascalie dal rimando: senza, il
    docente vedrebbe «Figura 1» nel PDF e «[FIG:iter]» a video."""
    views = _FRONTEND / "src" / "pages" / "org" / "courses" / "components"
    slides = (views / "LessonSlidesView.tsx").read_text(encoding="utf-8")
    speech = (views / "LessonSpeechView.tsx").read_text(encoding="utf-8")
    content = (views / "LessonContentView.tsx").read_text(encoding="utf-8")
    for source in (slides, speech, content):
        assert "@/lib/lessonAssetRefs" in source
        assert "lessonAssetRefs(" in source
    for needle in (
        "refs.cite(slide.title)",
        "refs.cite(slide.body)",
        "refs.cite(b)",
        "cite={refs.cite}",
    ):
        assert needle in slides, needle
    for needle in (
        "refs.cite(slideMeta.title)",
        "refs.cite(segment.text)",
        "refs.cite(segment.delivery_notes)",
    ):
        assert needle in speech, needle
    # Il contenitore del discorso deve passare `contentRaw`: senza, la
    # vista non avrebbe i numeri della dispensa.
    container = (views / "CourseLessonSpeechView.tsx").read_text(encoding="utf-8")
    assert "contentRaw={" in container
    # La cornice cita DOPO lo strip del prefisso, come il partial del PDF.
    frame = (_FRONTEND / "src" / "components" / "shared" / "FigureFrame.tsx").read_text(
        encoding="utf-8"
    )
    assert "stripFigurePrefix(caption" in frame
    assert frame.index("stripFigurePrefix(caption") < frame.index("cite ? cite(stripped)")
    # L'accessible name nasce dal testo CITATO su entrambi i lati: qui il
    # lato frontend della parità che il PDF prova poco sopra.
    assert "aria-label={altText || text" in frame
