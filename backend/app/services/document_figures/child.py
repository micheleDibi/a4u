"""Sottoprocesso di estrazione delle figure (``python -m app.services.document_figures.child``).

Gira in una cartella temporanea con un ambiente ridotto (nessun segreto,
nessun accesso a DB o storage: vedi :mod:`.runner`). Protocollo a righe
JSON:

- stdin, prima riga: il lavoro ``{"source", "mime", "engine",
  "artifacts_path", "threads"}``; il figlio risponde
  ``{"event": "ready", "pages": N}`` oppure ``{"event": "error", "code"}``;
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
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services.document_figures import cropper, filters
from app.services.document_figures.captions import caption_label
from app.services.document_figures.detection import Detection
from app.services.document_figures.geometry import BBox
from app.services.document_figures.phash import phash

Emit = Callable[[dict[str, Any]], None]

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


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
    def __init__(self, path: Path, *, engine: str, artifacts_path: str | None, threads: int):
        import pdfplumber
        import pypdfium2 as pdfium

        self.path = path
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
                ordered = sorted(detections, key=lambda d: (d.bbox.top, d.bbox.x0))
                for index, det in enumerate(ordered, start=1):
                    self._emit_one(
                        det, index, lines, rasters, out_dir, emit, caption_near, context_excerpt
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
    ) -> None:
        locator = f"p{det.page:04d}-f{index:02d}"
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
            "bbox": det.bbox.as_json(det.page_w, det.page_h),
            "detector_class": det.detector_class,
            "detector_confidence": det.confidence,
            "caption": caption,
            "source_label": caption_label(caption),
            "context_excerpt": context_excerpt(lines, det.bbox, caption=caption, page_h=det.page_h),
            "is_vector": is_vector,
            "reject_reason": None,
        }
        reason = "header_footer" if det.detector_class == "furniture" else None
        reason = reason or filters.geometry_reject_reason(
            det.bbox,
            page_w=det.page_w,
            page_h=det.page_h,
            is_vector=is_vector,
            confidence=det.confidence,
        )
        if reason is None:
            crop = cropper.render_crop(
                self.pdf[det.page - 1],
                det.bbox,
                page_w=det.page_w,
                page_h=det.page_h,
                is_vector=is_vector,
                native_ppi=native_ppi,
            )
            if cropper.is_blank(crop.image):
                reason = "blank"
            else:
                event.update(
                    _write(out_dir, locator, crop.data, crop.mime, cropper.preview(crop.image))
                )
                event.update(
                    width=crop.width, height=crop.height, dpi=crop.dpi, phash=phash(crop.image)
                )
        event["reject_reason"] = reason
        emit(event)


class DocxEngine:
    """DOCX (una sola «pagina») e PPTX (una pagina per slide)."""

    def __init__(self, path: Path, *, pptx: bool = False) -> None:
        from app.services.document_figures.office import (
            OfficeFormatError,
            docx_images,
            pptx_images,
            pptx_slide_count,
        )

        self.pptx = pptx
        try:
            if pptx:
                self.images = pptx_images(str(path))
                self.total_pages = max(1, pptx_slide_count(str(path)))
            else:
                self.images = docx_images(str(path))
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
                    data, mime = cropper.encode_image(image, photo=cropper.looks_like_photo(image))
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
    if mime == PDF_MIME:
        return PdfEngine(
            source,
            engine=str(job.get("engine") or "docling"),
            artifacts_path=job.get("artifacts_path"),
            threads=int(job.get("threads") or 1),
        )
    if mime == DOCX_MIME:
        return DocxEngine(source)
    if mime == PPTX_MIME:
        return DocxEngine(source, pptx=True)
    raise ChildError("unsupported_format", f"tipo non supportato: {mime}")


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
        emit({"event": "ready", "pages": engine.total_pages})
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
