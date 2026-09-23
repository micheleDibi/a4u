"""Generatore deterministico dei documenti di prova delle figure di fonte.

Produce, in una cartella data, un PDF di 5 pagine (A4), un DOCX e un PPTX
con figure note e distrattori noti, più la verità di terreno
(`ground_truth`) che `manifest.json` fissa. Nessuna dipendenza nuova:
matplotlib per il PDF (testo vero, disegni vettoriali, un raster),
python-docx per il DOCX, zipfile + XML scritto a mano per il PPTX.

Coordinate dei bbox: punti PDF con origine in alto a sinistra
(`x0, top, x1, bottom`), come pdfplumber.

Uso: ``python -m tests.fixtures.source_figures.build <cartella>``.
"""

from __future__ import annotations

import io
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Ellipse, FancyArrowPatch, Rectangle

PAGE_W = 595.28
PAGE_H = 841.89
PDF_NAME = "vibrometria_dispensa.pdf"
DOCX_NAME = "vibrometria_appunti.docx"
PPTX_NAME = "vibrometria_lezione.pptx"

_PARAGRAPH_1 = (
    "Il vibrometro laser Doppler misura la velocità di vibrazione di una superficie "
    "senza contatto. Il fascio di un laser elio-neon viene diviso da un divisore di "
    "fascio: il fascio di misura colpisce la superficie vibrante, quello di "
    "riferimento attraversa una cella di Bragg che introduce uno spostamento di "
    "frequenza di 40 MHz. La luce diffusa dalla superficie interferisce con il "
    "riferimento sul fotorivelatore, e la frequenza del battimento contiene lo "
    "spostamento Doppler proporzionale alla velocità."
)
_PARAGRAPH_2 = (
    "Come mostra la Figura 2.1, l'interferometro è di tipo Mach-Zehnder eterodina: "
    "la cella di Bragg permette di distinguere il verso del moto. Il segnale del "
    "fotorivelatore viene demodulato in frequenza per ottenere la velocità, oppure "
    "in fase per ottenere lo spostamento."
)
_PARAGRAPH_3 = (
    "Il banco di misura comprende la testa vibrometrica, uno shaker elettrodinamico "
    "e il provino fissato su una base rigida. La distanza di lavoro e la qualità "
    "della superficie determinano il rapporto segnale-rumore della misura."
)
_PARAGRAPH_4 = (
    "La risposta in frequenza si ottiene dal rapporto fra la velocità misurata e la "
    "forza di eccitazione. Le risonanze del provino compaiono come picchi, la cui "
    "larghezza dipende dallo smorzamento."
)
_PARAGRAPH_5 = (
    "Ogni misura passa per una catena: il sensore converte la grandezza fisica in un "
    "segnale elettrico, il condizionamento lo amplifica e lo filtra, il convertitore "
    "analogico-digitale lo campiona e l'elaborazione numerica produce il risultato."
)
_PARAGRAPH_6 = (
    "Esercizio. Una superficie vibra a 2 kHz con ampiezza di spostamento di 1 µm. "
    "Calcolare la velocità di picco e lo spostamento Doppler per un laser a 633 nm. "
    "Discutere l'effetto della rugosità della superficie sulla qualità del segnale."
)

GROUND_TRUTH: dict[str, Any] = {
    "pdf": {
        "file": PDF_NAME,
        "pages": 5,
        "figures": [
            {
                "id": "ldv_schema",
                "page": 1,
                "bbox": [70.0, 250.0, 525.0, 470.0],
                "label": "Figura 2.1",
                "caption": "Figura 2.1. Schema di principio di un vibrometro laser Doppler "
                "eterodina.",
                "kind": "schematic",
                "is_vector": True,
            },
            {
                "id": "bench_photo",
                "page": 2,
                "bbox": [120.0, 190.0, 475.0, 420.0],
                "label": "Figura 2.2",
                "caption": "Figura 2.2. Banco di misura con testa vibrometrica e provino.",
                "kind": "photo",
                "is_vector": False,
            },
            {
                "id": "freq_response",
                "page": 3,
                "bbox": [90.0, 330.0, 505.0, 560.0],
                "label": "Figura 2.3",
                "caption": "Figura 2.3. Risposta in frequenza misurata sul provino.",
                "kind": "chart",
                "is_vector": True,
            },
            {
                "id": "measurement_chain",
                "page": 4,
                "bbox": [80.0, 170.0, 515.0, 300.0],
                "label": None,
                "caption": None,
                "kind": "block_diagram",
                "is_vector": True,
            },
        ],
        "distractors": [
            {
                "id": "logo",
                "pages": [1, 2, 3, 4, 5],
                "bbox": [40.0, 24.0, 64.0, 48.0],
                "reason": "repeated",
            },
            {"id": "icon", "page": 2, "bbox": [60.0, 470.0, 72.0, 482.0], "reason": "too_small"},
            {"id": "table", "page": 3, "bbox": [90.0, 140.0, 505.0, 260.0], "reason": "table"},
        ],
        "keywords": ["vibrometro", "laser", "Doppler", "cella di Bragg", "fotorivelatore"],
    },
    "docx": {
        "file": DOCX_NAME,
        "figures": [
            {
                "id": "docx_ldv_schema",
                "order": 1,
                "label": "Figura 1",
                "caption": "Figura 1. Schema di un vibrometro laser Doppler.",
                "kind": "schematic",
            },
            {
                "id": "docx_bench_photo",
                "order": 2,
                "label": None,
                "caption": None,
                "kind": "photo",
            },
        ],
    },
    "pptx": {
        "file": PPTX_NAME,
        "slides": 3,
        "figures": [
            {
                "id": "pptx_ldv_schema",
                "slide": 2,
                "label": "Figura 1",
                "caption": "Figura 1. Schema del vibrometro laser Doppler.",
                "kind": "schematic",
            },
            {
                "id": "pptx_bench_photo",
                "slide": 3,
                "label": None,
                "caption": None,
                "kind": "photo",
            },
        ],
        "text": ["Vibrometria laser Doppler", "cella di Bragg"],
    },
}


def _axes(fig: plt.Figure, bbox: list[float]) -> plt.Axes:
    """Axes che occupa esattamente il bbox (punti, origine in alto a sinistra)."""
    x0, top, x1, bottom = bbox
    ax = fig.add_axes(
        (x0 / PAGE_W, 1 - bottom / PAGE_H, (x1 - x0) / PAGE_W, (bottom - top) / PAGE_H)
    )
    ax.set_axis_off()
    return ax


def _text(fig: plt.Figure, x: float, y: float, s: str, **kw: Any) -> None:
    fig.text(x / PAGE_W, 1 - y / PAGE_H, s, va="baseline", **kw)


def _paragraph(fig: plt.Figure, y: float, text: str, width: int = 95) -> float:
    for line in textwrap.wrap(text, width=width):
        _text(fig, 60, y, line, fontsize=9.5)
        y += 13
    return y + 8


def _logo() -> np.ndarray:
    grid = np.zeros((48, 48, 3), dtype=np.uint8)
    grid[..., 2] = 150
    grid[8:40, 8:40] = (240, 200, 40)
    grid[18:30, 18:30] = (150, 30, 30)
    return grid


def _photo(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h, w = 460, 710
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack(
        [
            120 + 80 * np.sin(xx / 57.0) * np.cos(yy / 43.0),
            110 + 60 * np.cos(xx / 91.0 + yy / 77.0),
            90 + 50 * np.sin((xx + yy) / 63.0),
        ],
        axis=-1,
    )
    noise = rng.normal(0, 18, size=(h, w, 3))
    img = np.clip(base + noise, 0, 255).astype(np.uint8)
    img[150:320, 250:470] = np.clip(img[150:320, 250:470] * 0.5 + 90, 0, 255).astype(np.uint8)
    return img


def _page(pdf: PdfPages, number: int, draw: Any) -> None:
    fig = plt.figure(figsize=(PAGE_W / 72, PAGE_H / 72))
    logo_ax = _axes(fig, [40.0, 24.0, 64.0, 48.0])
    logo_ax.imshow(_logo(), interpolation="nearest", aspect="auto")
    _text(fig, 76, 42, "Università di Prova — Misure meccaniche e termiche", fontsize=8)
    _text(fig, PAGE_W / 2, PAGE_H - 30, str(number), fontsize=8, ha="center")
    draw(fig)
    # 150 dpi per i raster incorporati (la foto del banco resta sotto i
    # 300 ppi: il ritaglio non la ingrandisce oltre il nativo).
    pdf.savefig(fig, dpi=150)
    plt.close(fig)


def _box(ax: plt.Axes, x: float, y: float, w: float, h: float, label: str) -> None:
    ax.add_patch(Rectangle((x, y), w, h, fill=False, lw=1.4))
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=8.5)


def _arrow(ax: plt.Axes, start: tuple[float, float], end: tuple[float, float]) -> None:
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=12, lw=1.2))


def _ldv_schema(ax: plt.Axes) -> None:
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 50)
    _box(ax, 1, 36, 18, 10, "Laser He-Ne")
    ax.add_patch(Rectangle((30, 37), 8, 8, fill=False, lw=1.2))
    ax.plot([30, 38], [37, 45], color="black", lw=1.0)
    ax.text(34, 47.5, "Divisore", ha="center", fontsize=8)
    _box(ax, 48, 36, 20, 10, "Cella di Bragg")
    ax.add_patch(Ellipse((92, 41), 12, 16, fill=False, lw=1.4))
    ax.text(92, 30, "Superficie\nvibrante", ha="center", fontsize=8)
    _box(ax, 26, 2, 20, 10, "Fotorivelatore")
    _box(ax, 60, 2, 20, 10, "Demodulatore")
    _arrow(ax, (19, 41), (30, 41))
    _arrow(ax, (38, 41), (48, 41))
    _arrow(ax, (68, 41), (86, 41))
    _arrow(ax, (34, 37), (34, 12))
    _arrow(ax, (46, 7), (60, 7))
    ax.text(77, 43, "f + 40 MHz", fontsize=7.5)


def _chain(ax: plt.Axes) -> None:
    ax.set_xlim(0, 100)
    ax.set_ylim(8.5, 21.5)
    labels = ["Sensore", "Condizionamento", "ADC", "Elaborazione"]
    for i, label in enumerate(labels):
        _box(ax, 1 + i * 25, 9, 20, 12, label)
        if i:
            _arrow(ax, (i * 25 - 4, 15), (1 + i * 25, 15))


def build_pdf(path: Path) -> None:
    with PdfPages(
        path,
        metadata={"Title": "Vibrometria laser", "Author": "Docente di Prova", "CreationDate": None},
    ) as pdf:

        def page1(fig: plt.Figure) -> None:
            _text(fig, 60, 90, "Capitolo 2 — Vibrometria laser Doppler", fontsize=15)
            _paragraph(fig, 125, _PARAGRAPH_1)
            _ldv_schema(_axes(fig, [70.0, 250.0, 525.0, 470.0]))
            _text(
                fig,
                70,
                492,
                GROUND_TRUTH["pdf"]["figures"][0]["caption"],
                fontsize=9,
                style="italic",
            )
            _paragraph(fig, 530, _PARAGRAPH_2)

        def page2(fig: plt.Figure) -> None:
            _paragraph(fig, 90, _PARAGRAPH_3)
            _axes(fig, [120.0, 190.0, 475.0, 420.0]).imshow(_photo(), aspect="auto")
            _text(
                fig,
                120,
                440,
                GROUND_TRUTH["pdf"]["figures"][1]["caption"],
                fontsize=9,
                style="italic",
            )
            _axes(fig, [60.0, 470.0, 72.0, 482.0]).imshow(_logo(), aspect="auto")
            _text(fig, 78, 480, "Nota: attenzione alla sicurezza laser (classe 2).", fontsize=9)
            _paragraph(fig, 520, _PARAGRAPH_1)

        def page3(fig: plt.Figure) -> None:
            _text(
                fig,
                90,
                128,
                "Tabella 2.1. Caratteristiche tipiche dei vibrometri.",
                fontsize=9,
                style="italic",
            )
            table = _axes(fig, [90.0, 140.0, 505.0, 260.0])
            table.set_xlim(0, 3)
            table.set_ylim(0, 4)
            for r in range(5):
                table.plot([0, 3], [r, r], color="black", lw=0.8)
            for c in range(4):
                table.plot([c, c], [0, 4], color="black", lw=0.8)
            rows = [
                ("Grandezza", "Campo", "Risoluzione"),
                ("Velocità", "10 m/s", "0,02 µm/s"),
                ("Spostamento", "±75 mm", "2 pm"),
                ("Frequenza", "25 MHz", "—"),
            ]
            for r, row in enumerate(rows):
                for c, cell in enumerate(row):
                    table.text(c + 0.5, 3.5 - r, cell, ha="center", va="center", fontsize=8)
            _text(fig, 90, 295, "v(t) = λ · f_D(t) / 2", fontsize=10)
            chart = fig.add_axes(
                (
                    90 / PAGE_W + 0.06,
                    1 - 560 / PAGE_H + 0.04,
                    (505 - 90) / PAGE_W - 0.08,
                    (560 - 330) / PAGE_H - 0.06,
                )
            )
            freqs = np.linspace(10, 2000, 400)
            resp = 1 / np.sqrt((1 - (freqs / 620) ** 2) ** 2 + (0.08 * freqs / 620) ** 2)
            resp += 0.6 / np.sqrt((1 - (freqs / 1450) ** 2) ** 2 + (0.05 * freqs / 1450) ** 2)
            chart.plot(freqs, resp, color="tab:blue", lw=1.2)
            chart.set_xlabel("Frequenza [Hz]", fontsize=8)
            chart.set_ylabel("|H(f)| [(m/s)/N]", fontsize=8)
            chart.tick_params(labelsize=7)
            chart.grid(True, lw=0.3)
            _text(
                fig,
                90,
                580,
                GROUND_TRUTH["pdf"]["figures"][2]["caption"],
                fontsize=9,
                style="italic",
            )
            _paragraph(fig, 615, _PARAGRAPH_4)

        def page4(fig: plt.Figure) -> None:
            _paragraph(fig, 90, _PARAGRAPH_5)
            _chain(_axes(fig, [80.0, 170.0, 515.0, 300.0]))
            _paragraph(fig, 340, _PARAGRAPH_2)

        def page5(fig: plt.Figure) -> None:
            _paragraph(fig, 90, _PARAGRAPH_6)
            _paragraph(fig, 160, _PARAGRAPH_4)

        for number, draw in enumerate((page1, page2, page3, page4, page5), start=1):
            _page(pdf, number, draw)


def _png(draw: Any, size: tuple[float, float], dpi: int = 150) -> bytes:
    fig = plt.figure(figsize=size)
    ax = fig.add_axes((0.02, 0.02, 0.96, 0.96))
    ax.set_axis_off()
    draw(ax)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


def build_docx(path: Path) -> None:
    from docx import Document
    from docx.shared import Cm

    document = Document()
    document.core_properties.title = "Appunti di vibrometria"
    document.core_properties.author = "Docente di Prova"
    header = document.sections[0].header.paragraphs[0]
    header.add_run().add_picture(
        io.BytesIO(_png(lambda ax: ax.imshow(_logo()), (1, 1))), width=Cm(0.8)
    )
    document.add_heading("Vibrometria laser Doppler", level=1)
    document.add_paragraph(_PARAGRAPH_1)
    document.add_picture(io.BytesIO(_png(_ldv_schema, (6.3, 3.1))), width=Cm(15))
    document.add_paragraph(GROUND_TRUTH["docx"]["figures"][0]["caption"])
    document.add_paragraph(_PARAGRAPH_3)
    photo = _png(lambda ax: ax.imshow(_photo(), aspect="auto"), (5.0, 3.2))
    document.add_picture(io.BytesIO(photo), width=Cm(12))
    document.add_paragraph(_PARAGRAPH_4)
    document.save(str(path))


_PPTX_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _pptx_text_shape(shape_id: int, paragraphs: list[str]) -> str:
    body = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in paragraphs)
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Testo {shape_id}"/><p:cNvSpPr/>'
        f"<p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/>{body}</p:txBody></p:sp>"
    )


def _pptx_picture(shape_id: int, rel_id: str) -> str:
    return (
        f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="Immagine {shape_id}"/><p:cNvPicPr/>'
        f'<p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="{rel_id}"/></p:blipFill>'
        "<p:spPr/></p:pic>"
    )


def build_pptx(path: Path) -> None:
    """PPTX minimale ma strutturalmente valido: 3 slide (solo testo; schema
    con didascalia; foto senza didascalia) e le proprietà del documento."""
    import zipfile

    schema = _png(_ldv_schema, (6.3, 3.1))
    photo = _png(lambda ax: ax.imshow(_photo(), aspect="auto"), (5.0, 3.2))
    slides = [
        (["Vibrometria laser Doppler", "Misura della velocità senza contatto."], None),
        (
            [
                "Principio di funzionamento",
                "Il fascio attraversa la cella di Bragg e interferisce sul fotorivelatore.",
                GROUND_TRUTH["pptx"]["figures"][0]["caption"],
            ],
            "image1.png",
        ),
        (["Il banco di misura"], "image2.png"),
    ]
    files: dict[str, bytes | str] = {}
    overrides = [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.'
        'openxmlformats-package.core-properties+xml"/>',
    ]
    sld_ids, pres_rels = [], []
    for index, (paragraphs, media) in enumerate(slides, start=1):
        shapes = _pptx_text_shape(2, paragraphs)
        rels = []
        if media:
            shapes += _pptx_picture(3, "rId2")
            rels.append(
                f'<Relationship Id="rId2" Type="{_REL_TYPE}/image" Target="../media/{media}"/>'
            )
        files[f"ppt/slides/slide{index}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><p:sld {_PPTX_NS}><p:cSld><p:spTree>'
            f"{shapes}</p:spTree></p:cSld></p:sld>"
        )
        files[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_REL_NS}">'
            f"{''.join(rels)}</Relationships>"
        )
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{index}.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
        sld_ids.append(f'<p:sldId id="{255 + index}" r:id="rId{index}"/>')
        pres_rels.append(
            f'<Relationship Id="rId{index}" Type="{_REL_TYPE}/slide" '
            f'Target="slides/slide{index}.xml"/>'
        )
    files["ppt/media/image1.png"] = schema
    files["ppt/media/image2.png"] = photo
    files["ppt/presentation.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><p:presentation {_PPTX_NS}>'
        f"<p:sldIdLst>{''.join(sld_ids)}</p:sldIdLst></p:presentation>"
    )
    files["ppt/_rels/presentation.xml.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_REL_NS}">'
        f"{''.join(pres_rels)}</Relationships>"
    )
    files["_rels/.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_REL_NS}">'
        f'<Relationship Id="rId1" Type="{_REL_TYPE}/officeDocument" '
        'Target="ppt/presentation.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
        'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    files["docProps/core.xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Lezione di vibrometria</dc:title><dc:creator>Docente di Prova</dc:creator>"
        "</cp:coreProperties>"
    )
    files["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.'
        'openxmlformats-package.relationships+xml"/><Default Extension="xml" '
        'ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
        f"{''.join(overrides)}</Types>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in [
            "[Content_Types].xml",
            *sorted(n for n in files if n != "[Content_Types].xml"),
        ]:
            data = files[name]
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))


def build_all(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    build_pdf(out_dir / PDF_NAME)
    build_docx(out_dir / DOCX_NAME)
    build_pptx(out_dir / PPTX_NAME)
    return GROUND_TRUTH


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    truth = build_all(target)
    print(json.dumps({"written": [PDF_NAME, DOCX_NAME, PPTX_NAME], "dir": str(target)}))
