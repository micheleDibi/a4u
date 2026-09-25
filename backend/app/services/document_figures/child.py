"""Sottoprocesso di estrazione delle figure (``python -m app.services.document_figures.child``).

Gira in una cartella temporanea con un ambiente ridotto (nessun segreto,
nessun accesso a DB o storage: vedi :mod:`.runner`). Protocollo a righe
JSON:

- stdin, prima riga: il lavoro ``{"source", "mime", "engine",
  "artifacts_path", "threads", "metadata", "crop_version"}``
  (``crop_version`` 2 = ritaglio sulla griglia nativa, 1 = storico;
  assente = 1); il figlio risponde
  ``{"event": "ready", "pages": N}`` oppure ``{"event": "error", "code"}``;
  con ``"metadata": true`` l'evento ``ready`` porta anche
  ``{"metadata": {"bibliography", "text", "info_title"}}`` (bibliografia e
  testo delle prime pagine letti QUI, non nel processo principale: il
  documento è ostile per ipotesi; tetto di tempo ``METADATA_SECONDS``);
- stdin, righe successive: un blocco ``{"pages": [primo, ultimo]}``; il
  figlio emette un evento ``figure`` per ogni figura (anche scartata, con
  ``reject_reason``) e chiude con ``{"event": "block_done"}``;
- EOF su stdin → uscita 0.

Con ``--probe`` verifica soltanto che il motore si carichi (import di torch
e Docling, modello di layout) e risponde ``probe_ok``.

Lo stdout del processo è riservato al protocollo: il descrittore 1 viene
rediretto su stderr, così le librerie che stampano non lo corrompono.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from app.services.document_figures import cropper, filters
from app.services.document_figures.captions import caption_label
from app.services.document_figures.detection import Detection
from app.services.document_figures.geometry import BBox
from app.services.document_figures.phash import phash
from app.services.document_figures.regions import PageRegions, page_regions

Emit = Callable[[dict[str, Any]], None]

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
# Metadati (bibliografia, testo delle prime pagine): tetto di tempo e di testo.
METADATA_SECONDS = 30
METADATA_TEXT_CHARS = 20_000


class ChildError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _protocol_stream() -> Emit:
    out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def emit(event: dict[str, Any]) -> None:
        out.write(json.dumps(event, ensure_ascii=False) + "\n")
        out.flush()

    return emit


def _write(out_dir: Path, locator: str, data: bytes, mime: str, preview: bytes) -> dict[str, Any]:
    ext = "jpg" if mime == "image/jpeg" else "png"
    name = f"{locator}.{ext}"
    (out_dir / name).write_bytes(data)
    preview_name = f"{locator}-preview.jpg"
    (out_dir / preview_name).write_bytes(preview)
    return {"file": name, "preview_file": preview_name, "byte_size": len(data), "mime": mime}


@dataclass(frozen=True)
class PageBoxes:
    """CropBox della pagina nello spazio di pdfplumber (origine della
    MediaBox, in alto a sinistra). PDFium rende solo la CropBox: un bbox di
    pdfplumber va traslato di (-ox, -oy) prima del ritaglio."""

    ox: float
    oy: float
    crop_w: float
    crop_h: float
    media_w: float
    media_h: float

    @property
    def cropped(self) -> bool:
        return (
            abs(self.ox) > 0.5
            or abs(self.oy) > 0.5
            or abs(self.crop_w - self.media_w) > 0.5
            or abs(self.crop_h - self.media_h) > 0.5
        )

    def in_crop_space(self, page_w: float, page_h: float) -> bool:
        """Il rilevatore ha misurato la pagina come la CropBox (e non come
        la MediaBox di pdfplumber)?"""
        return (
            self.cropped
            and abs(page_w - self.crop_w) <= 1.0
            and abs(page_h - self.crop_h) <= 1.0
            and not (abs(page_w - self.media_w) <= 1.0 and abs(page_h - self.media_h) <= 1.0)
        )


def page_boxes(ppage: Any) -> PageBoxes:
    x0, top, x1, bottom = (float(v) for v in ppage.cropbox)
    return PageBoxes(
        ox=x0,
        oy=top,
        crop_w=x1 - x0,
        crop_h=bottom - top,
        media_w=float(ppage.width),
        media_h=float(ppage.height),
    )


def _raster_share(
    det: Detection, rasters: list[tuple[BBox, float | None]]
) -> tuple[bool, float | None]:
    """(è vettoriale, ppi nativo): raster se le immagini coprono metà bbox."""
    covered = 0.0
    best: tuple[float, float | None] = (0.0, None)
    for box, ppi in rasters:
        inter = det.bbox.intersection_area(box)
        covered += inter
        if inter > best[0]:
            best = (inter, ppi)
    if det.bbox.area > 0 and covered / det.bbox.area >= 0.5:
        return False, best[1]
    return True, None


class PdfEngine:
    def __init__(
        self,
        path: Path,
        *,
        engine: str,
        artifacts_path: str | None,
        threads: int,
        crop_version: int = 1,
    ):
        import pdfplumber
        import pypdfium2 as pdfium

        self.path = path
        self.crop_version = crop_version
        # Contenuto delle pagine per il ritaglio v2 (una lettura per pagina).
        self._regions: dict[int, PageRegions] = {}
        try:
            self.pdf = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as exc:
            code = "encrypted" if "password" in str(exc).lower() else "corrupt"
            raise ChildError(code, str(exc)) from exc
        self.total_pages = len(self.pdf)
        try:
            self.plumber = pdfplumber.open(str(path))
        except Exception as exc:
            raise ChildError("corrupt", str(exc)) from exc
        self.detector = None
        if engine == "docling":
            from app.services.document_figures.docling_adapter import (
                DoclingDetector,
                DoclingUnavailableError,
            )

            try:
                self.detector = DoclingDetector(artifacts_path=artifacts_path, threads=threads)
            except DoclingUnavailableError as exc:
                raise ChildError("engine_unavailable", str(exc)) from exc

    def process(self, first: int, last: int, out_dir: Path, emit: Emit) -> None:
        from app.services.document_figures import heuristic
        from app.services.document_figures.page_text import (
            caption_near,
            context_excerpt,
            page_lines,
        )

        first = max(1, first)
        last = min(self.total_pages, last)
        by_page = self.detector.detect(self.path, first, last) if self.detector else {}
        for page_no in range(first, last + 1):
            ppage = self.plumber.pages[page_no - 1]
            try:
                lines = page_lines(ppage.extract_words())
                detections = (
                    by_page.get(page_no, [])
                    if self.detector
                    else heuristic.detect_page(ppage, page_no)
                )
                rasters: list[tuple[BBox, float | None]] = []
                for image in ppage.images:
                    box = BBox(
                        float(image["x0"]),
                        float(image["top"]),
                        float(image["x1"]),
                        float(image["bottom"]),
                    )
                    src = image.get("srcsize") or (0, 0)
                    ppi = src[0] / (box.width / 72.0) if src and src[0] and box.width > 0 else None
                    rasters.append((box, ppi))
                boxes = page_boxes(ppage)
                # Tutto nello spazio di pdfplumber (parole, immagini): i bbox
                # di un rilevatore che misura la CropBox si riportano lì.
                placed = [
                    det
                    if not boxes.in_crop_space(det.page_w, det.page_h)
                    else replace(
                        det,
                        bbox=det.bbox.translate(boxes.ox, boxes.oy),
                        page_w=boxes.media_w,
                        page_h=boxes.media_h,
                    )
                    for det in detections
                ]
                ordered = sorted(placed, key=lambda d: (d.bbox.top, d.bbox.x0))
                others = [d.bbox for d in ordered]
                for index, det in enumerate(ordered, start=1):
                    self._emit_one(
                        det,
                        index,
                        lines,
                        rasters,
                        out_dir,
                        emit,
                        partial(caption_near, others=others),
                        context_excerpt,
                        boxes,
                    )
            finally:
                close = getattr(ppage, "close", None)
                if callable(close):
                    close()
            emit({"event": "page_done", "page": page_no})

    def _emit_one(
        self,
        det: Detection,
        index: int,
        lines: list[Any],
        rasters: list[tuple[BBox, float | None]],
        out_dir: Path,
        emit: Emit,
        caption_near: Callable[..., str | None],
        context_excerpt: Callable[..., str | None],
        boxes: PageBoxes,
    ) -> None:
        locator = f"p{det.page:04d}-f{index:02d}"
        # Bbox nella pagina visibile (CropBox), per filtri, ritaglio e JSON;
        # una figura tutta fuori dalla CropBox non si vede: scartata.
        shifted = det.bbox.translate(-boxes.ox, -boxes.oy)
        on_page = shifted.intersection(BBox(0.0, 0.0, boxes.crop_w, boxes.crop_h))
        visible = on_page or shifted
        if self.detector is not None:
            is_vector, native_ppi = _raster_share(det, rasters)
        else:
            is_vector, native_ppi = det.is_vector, det.native_ppi
        # La didascalia del rilevatore vince solo se porta l'etichetta
        # («Figura N»); altrimenti (a volte Docling ne aggancia una sola
        # riga) si preferisce quella etichettata trovata vicino al bbox.
        caption = det.caption
        if not caption_label(caption):
            caption = caption_near(lines, det.bbox) or caption
        event: dict[str, Any] = {
            "event": "figure",
            "locator": locator,
            "page": det.page,
            "bbox": visible.as_json(boxes.crop_w, boxes.crop_h),
            "detector_class": det.detector_class,
            "detector_confidence": det.confidence,
            "caption": caption,
            "source_label": caption_label(caption),
            "context_excerpt": context_excerpt(lines, det.bbox, caption=caption, page_h=det.page_h),
            "is_vector": is_vector,
            "reject_reason": None,
        }
        reason = "header_footer" if det.detector_class == "furniture" else None
        if reason is None and on_page is None:
            reason = "too_small"
        reason = reason or filters.geometry_reject_reason(
            visible,
            page_w=boxes.crop_w,
            page_h=boxes.crop_h,
            is_vector=is_vector,
            confidence=det.confidence,
        )
        if reason is None:
            native = self._native_crop(det.page, visible, boxes) if self.crop_version >= 2 else None
            if native is not None:
                image = native.image
                data, mime = native.data, native.mime
                event.update(
                    width=native.width,
                    height=native.height,
                    dpi=native.dpi,
                    is_vector=native.is_vector,
                    crop_version=2,
                    crop_mode=native.mode,
                    native_ppi=native.native_ppi,
                    natural_width_mm=native.natural_width_mm,
                    aligned=native.aligned,
                )
            else:
                crop = cropper.render_crop(
                    self.pdf[det.page - 1],
                    visible,
                    page_w=boxes.crop_w,
                    page_h=boxes.crop_h,
                    is_vector=is_vector,
                    native_ppi=native_ppi,
                )
                image = crop.image
                data, mime = crop.data, crop.mime
                event.update(width=crop.width, height=crop.height, dpi=crop.dpi, crop_version=1)
            if cropper.is_blank(image):
                reason = "blank"
            else:
                event.update(_write(out_dir, locator, data, mime, cropper.preview(image)))
                event.update(phash=phash(image))
        event["reject_reason"] = reason
        emit(event)

    def _native_crop(self, page_no: int, visible: BBox, boxes: PageBoxes) -> Any:
        """Ritaglio v2; None (→ ritaglio v1) se la pagina non si legge o il
        render fallisce (es. bitmap oltre il tetto anti-bomba)."""
        page = self.pdf[page_no - 1]
        try:
            regions = self._regions.get(page_no)
            if regions is None:
                regions = self._regions[page_no] = page_regions(page)
            return cropper.render_native_crop(
                page, visible, regions, page_w=boxes.crop_w, page_h=boxes.crop_h
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            print(f"native_crop_failed page={page_no}: {detail}", file=sys.stderr)
            return None


class DocxEngine:
    """DOCX (una sola «pagina») e PPTX (una pagina per slide)."""

    def __init__(self, path: Path, *, pptx: bool = False, crop_version: int = 1) -> None:
        from app.services.document_figures.office import (
            OfficeFormatError,
            docx_images,
            pptx_images,
            pptx_slide_count,
        )

        self.pptx = pptx
        # Ritaglio v2: geometria del documento (srcRect, ribaltamenti,
        # rotazioni, EMU) e codifica per contenuto.
        self.geometry = crop_version >= 2
        try:
            if pptx:
                self.images = pptx_images(str(path), geometry=self.geometry)
                self.total_pages = max(1, pptx_slide_count(str(path)))
            else:
                self.images = docx_images(str(path), geometry=self.geometry)
                self.total_pages = 1
        except OfficeFormatError as exc:
            raise ChildError("corrupt", str(exc)) from exc

    def process(self, first: int, last: int, out_dir: Path, emit: Emit) -> None:
        for item in self.images:
            if self.pptx and not (first <= (item.page or 0) <= last):
                continue
            locator = (
                f"s{item.page or 0:04d}-f{item.order:02d}" if self.pptx else f"d{item.order:04d}"
            )
            event: dict[str, Any] = {
                "event": "figure",
                "locator": locator,
                "page": item.page,
                "bbox": None,
                "detector_class": "embedded",
                "detector_confidence": None,
                "caption": item.caption,
                "source_label": caption_label(item.caption),
                "context_excerpt": item.context,
                "is_vector": False,
                "reject_reason": item.reject_reason,
            }
            image = item.image
            if image is not None and event["reject_reason"] is None:
                if min(image.width, image.height) < 64:
                    event["reject_reason"] = "too_small"
                elif cropper.is_blank(image):
                    event["reject_reason"] = "blank"
                else:
                    if self.geometry:
                        data, mime = cropper.encode_figure(image, source_lossy=item.source_lossy)
                        event.update(
                            crop_version=2,
                            crop_mode="office",
                            native_ppi=item.native_ppi,
                            natural_width_mm=item.natural_width_mm,
                        )
                    else:
                        data, mime = cropper.encode_image(
                            image, photo=cropper.looks_like_photo(image)
                        )
                        event.update(crop_version=1)
                    event.update(_write(out_dir, locator, data, mime, cropper.preview(image)))
                    event.update(
                        width=image.width, height=image.height, dpi=None, phash=phash(image)
                    )
            emit(event)
        for page in range(first, min(last, self.total_pages) + 1):
            emit({"event": "page_done", "page": page})


def _open_engine(job: dict[str, Any], cwd: Path) -> PdfEngine | DocxEngine:
    source = cwd / Path(str(job["source"])).name
    mime = str(job.get("mime") or "")
    crop_version = int(job.get("crop_version") or 1)
    if mime == PDF_MIME:
        return PdfEngine(
            source,
            engine=str(job.get("engine") or "docling"),
            artifacts_path=job.get("artifacts_path"),
            threads=int(job.get("threads") or 1),
            crop_version=crop_version,
        )
    if mime == DOCX_MIME:
        return DocxEngine(source, crop_version=crop_version)
    if mime == PPTX_MIME:
        return DocxEngine(source, pptx=True, crop_version=crop_version)
    raise ChildError("unsupported_format", f"tipo non supportato: {mime}")


class _MetadataTimeout(BaseException):
    """Tempo dei metadati scaduto (BaseException: le funzioni di
    `metadata` intercettano `Exception` e non devono fermarlo)."""


def _on_alarm(signum: int, frame: Any) -> None:
    raise _MetadataTimeout()


def document_metadata(source: Path, mime: str) -> dict[str, Any]:
    """Bibliografia deterministica e testo delle prime pagine, entro
    `METADATA_SECONDS` (dopo, quello che c'è)."""
    from app.services.document_figures import metadata

    out: dict[str, Any] = {"bibliography": None, "text": "", "info_title": None}
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(METADATA_SECONDS)
    try:
        if mime == PDF_MIME:
            bib = metadata.pdf_bibliography(source)
            out["bibliography"] = bib.as_json() if bib is not None else None
            out["info_title"] = metadata.pdf_info_title(source)
            out["text"] = metadata.pdf_first_pages_text(source)[:METADATA_TEXT_CHARS]
        else:
            bib = metadata.office_bibliography(source)
            out["bibliography"] = bib.as_json() if bib is not None else None
    except _MetadataTimeout:
        out["timed_out"] = True
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    return out


def _probe(job: dict[str, Any], emit: Emit) -> int:
    info: dict[str, Any] = {"event": "probe_ok", "engine": job.get("engine")}
    if job.get("engine") == "docling":
        from app.services.document_figures.docling_adapter import (
            DoclingDetector,
            DoclingUnavailableError,
        )

        try:
            detector = DoclingDetector(
                artifacts_path=job.get("artifacts_path"), threads=int(job.get("threads") or 1)
            )
        except DoclingUnavailableError as exc:
            emit({"event": "error", "code": "engine_unavailable", "message": str(exc)[:500]})
            return 2
        info.update(torch=detector.torch_version, cpu_capability=detector.cpu_capability)
    else:
        import pdfplumber  # noqa: F401
        import pypdfium2  # noqa: F401
    emit(info)
    return 0


def main(argv: list[str]) -> int:
    emit = _protocol_stream()
    cwd = Path.cwd()
    try:
        job = json.loads(sys.stdin.readline() or "{}")
        if "--probe" in argv:
            return _probe(job, emit)
        engine = _open_engine(job, cwd)
        ready: dict[str, Any] = {"event": "ready", "pages": engine.total_pages}
        if job.get("metadata"):
            ready["metadata"] = document_metadata(
                cwd / Path(str(job["source"])).name, str(job.get("mime") or "")
            )
        emit(ready)
        for raw in sys.stdin:
            if not raw.strip():
                continue
            first, last = json.loads(raw)["pages"]
            engine.process(int(first), int(last), cwd, emit)
            emit({"event": "block_done", "pages": [int(first), int(last)]})
    except ChildError as exc:
        emit({"event": "error", "code": exc.code, "message": str(exc)[:500]})
        return 2
    except Exception as exc:  # errore inatteso: il padre lo tratta come crash
        emit({"event": "error", "code": "crashed", "message": f"{type(exc).__name__}: {exc}"[:500]})
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
