"""Rilevazione del layout con Docling (MIT), solo per trovare le figure.

Configurazione (J-Q7): OCR spento, niente struttura delle tabelle, niente
immagini di pagina né di figura trattenute (il ritaglio lo fa PDFium sul
bbox). Import pigri: il modulo si importa anche senza l'extra `figures`,
e l'errore emerge solo alla costruzione del rilevatore (→
`engine_unavailable`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.document_figures.captions import clip_caption
from app.services.document_figures.detection import Detection
from app.services.document_figures.geometry import BBox


class DoclingUnavailableError(RuntimeError):
    """Docling, torch o i modelli non sono utilizzabili in questo ambiente."""


class DoclingDetector:
    def __init__(self, *, artifacts_path: str | None, threads: int) -> None:
        try:
            import torch
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except Exception as exc:  # ImportError, OSError di librerie native
            raise DoclingUnavailableError(f"import docling/torch fallito: {exc}") from exc
        torch.set_num_threads(max(1, threads))
        options = PdfPipelineOptions(
            artifacts_path=Path(artifacts_path) if artifacts_path else None
        )
        options.do_ocr = False
        options.do_table_structure = False
        options.generate_page_images = False
        options.generate_picture_images = False
        options.images_scale = 1.0
        accelerator = getattr(options, "accelerator_options", None)
        if accelerator is not None:
            accelerator.num_threads = max(1, threads)
            accelerator.device = "cpu"
        try:
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
            self._converter.initialize_pipeline(InputFormat.PDF)
        except Exception as exc:
            raise DoclingUnavailableError(f"modello di layout non caricabile: {exc}") from exc
        self.torch_version = str(torch.__version__)
        backends_cpu = getattr(torch.backends, "cpu", None)
        capability = getattr(backends_cpu, "get_cpu_capability", None)
        self.cpu_capability = str(capability()) if callable(capability) else None

    def detect(self, pdf_path: Path, first: int, last: int) -> dict[int, list[Detection]]:
        """Figure delle pagine [first, last] (numerazione fisica da 1)."""
        result = self._converter.convert(str(pdf_path), page_range=(first, last))
        doc = result.document
        out: dict[int, list[Detection]] = {}
        for picture in doc.pictures:
            if not picture.prov:
                continue
            prov = picture.prov[0]
            page = doc.pages.get(prov.page_no)
            if page is None or page.size is None:
                continue
            page_w, page_h = float(page.size.width), float(page.size.height)
            box = prov.bbox.to_top_left_origin(page_h)
            layer = str(getattr(picture, "content_layer", "") or "").lower()
            out.setdefault(prov.page_no, []).append(
                Detection(
                    page=prov.page_no,
                    bbox=BBox(float(box.l), float(box.t), float(box.r), float(box.b)),
                    page_w=page_w,
                    page_h=page_h,
                    is_vector=True,  # deciso dal figlio in base ai raster del bbox
                    detector_class="furniture" if "furniture" in layer else str(picture.label),
                    caption=clip_caption(_caption(picture, doc)),
                )
            )
        return out


def _caption(picture: Any, doc: Any) -> str | None:
    try:
        text = picture.caption_text(doc)
    except Exception:
        return None
    return str(text) if text else None
