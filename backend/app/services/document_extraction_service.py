"""Estrazione testo da documenti caricati (PDF/DOCX/DOC/RTF/TXT/MD).

Tutte le librerie usate (`pdfplumber`, `python-docx`, `docx2txt`, `striprtf`)
sono pure-Python o wheel-installabili su Windows. La extraction è blocking
quindi viene wrappata in `asyncio.to_thread`.

Solleva `DocumentExtractionError(message)` quando il file è corrotto,
protetto da password, o di formato non supportato.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.document_extraction")


class DocumentExtractionError(Exception):
    """Errore durante l'estrazione del testo da un documento."""


# Lista MIME → extractor (sync). Le estensioni file sono fallback
# se il MIME risulta vuoto o non riconosciuto.
PDF_MIMES = {"application/pdf"}
DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
PPTX_MIMES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
DOC_MIMES = {"application/msword"}
RTF_MIMES = {"application/rtf", "text/rtf"}
TEXT_MIMES = {"text/plain", "text/markdown"}


def _extract_pdf(path: Path) -> str:
    return "\n\n".join(_extract_pdf_pages(path))


def _extract_docx(path: Path) -> str:
    import docx

    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise DocumentExtractionError(
            f"Impossibile leggere il file DOCX: {exc}"
        ) from exc

    parts: list[str] = []
    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            parts.append(paragraph.text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append("\t".join(cells))
    return "\n".join(parts)


def _extract_pptx(path: Path) -> str:
    """Testo delle slide in ordine (zipfile + XML, come l'estrazione delle
    figure: `document_figures.office`)."""
    from app.services.document_figures.office import OfficeFormatError, pptx_text

    try:
        return pptx_text(str(path))
    except OfficeFormatError as exc:
        raise DocumentExtractionError(
            f"Impossibile leggere il file PPTX: {exc}"
        ) from exc


def _extract_doc(path: Path) -> str:
    import docx2txt

    try:
        text = docx2txt.process(str(path))
    except Exception as exc:
        raise DocumentExtractionError(
            f"Impossibile leggere il file DOC: {exc}"
        ) from exc
    return text or ""


def _extract_rtf(path: Path) -> str:
    from striprtf.striprtf import rtf_to_text

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        return rtf_to_text(raw)
    except Exception as exc:
        raise DocumentExtractionError(
            f"Impossibile leggere il file RTF: {exc}"
        ) from exc


def _extract_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise DocumentExtractionError(
            f"Impossibile leggere il file di testo: {exc}"
        ) from exc


def _extract_pdf_pages(path: Path) -> list[str]:
    """Come `_extract_pdf` ma restituisce le pagine NON vuote separate
    (stesso contenuto: `_extract_pdf` è il join `\\n\\n` di questa lista)."""
    import pdfplumber

    parts: list[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text:
                    parts.append(text)
    except Exception as exc:
        msg = str(exc).lower()
        if "password" in msg or "encrypt" in msg:
            raise DocumentExtractionError(
                "Il PDF è protetto da password e non può essere letto."
            ) from exc
        raise DocumentExtractionError(
            f"Impossibile leggere il PDF: {exc}"
        ) from exc
    return parts


def _dispatch(path: Path, mime_type: str) -> str:
    mime = (mime_type or "").lower()
    suffix = path.suffix.lower()
    if mime in PDF_MIMES or suffix == ".pdf":
        return _extract_pdf(path)
    if mime in DOCX_MIMES or suffix == ".docx":
        return _extract_docx(path)
    if mime in PPTX_MIMES or suffix == ".pptx":
        return _extract_pptx(path)
    if mime in DOC_MIMES or suffix == ".doc":
        return _extract_doc(path)
    if mime in RTF_MIMES or suffix == ".rtf":
        return _extract_rtf(path)
    if mime in TEXT_MIMES or suffix in {".txt", ".md", ".markdown"}:
        return _extract_text_file(path)
    raise DocumentExtractionError(
        f"Formato non supportato: mime='{mime_type}', estensione='{suffix}'."
    )


def _dispatch_parts(path: Path, mime_type: str) -> tuple[list[str], bool]:
    """Come `_dispatch` ma a parti: (lista parti, is_pdf).

    Per i PDF una parte = una pagina non vuota (join `\\n\\n` ≡
    `_extract_pdf`); per gli altri formati una parte unica (identica a
    `_dispatch`). Regola di equivalenza: `sep.join(parts)` deve essere
    BYTE-IDENTICO all'output di `_dispatch` — è ciò che garantisce che
    `extract_text` (wrapper) non cambi mai comportamento.
    """
    mime = (mime_type or "").lower()
    suffix = path.suffix.lower()
    if mime in PDF_MIMES or suffix == ".pdf":
        return _extract_pdf_pages(path), True
    return [_dispatch(path, mime_type)], False


# Span di pagina sul testo normalizzato: (char_start, char_end, pagina).
# `pagina` è 1-based e conta le sole pagine NON vuote (le uniche che
# contribuiscono al testo); None per i formati non paginati.
PageSpan = tuple[int, int, int | None]


async def extract_segments(
    file_path: Path, mime_type: str, *, hard_cap_chars: int
) -> tuple[str, list[PageSpan], int, bool]:
    """Estrae il testo con le coordinate di pagina per il chunking.

    Ritorna `(text, page_spans, original_chars, hard_capped)` dove:
    - `text` = join per-formato delle parti (PDF: `\\n\\n` tra pagine non
      vuote) con strip GLOBALE (mai per-segmento), troncato a
      `hard_cap_chars`;
    - `page_spans` = span (start, end, pagina|None) sul testo normalizzato
      e troncato;
    - `original_chars` = lunghezza post-strip PRE-troncamento (stessa
      semantica di `extract_text`);
    - `hard_capped` = True se il testo eccedeva `hard_cap_chars`.
    """
    if not file_path.exists():
        raise DocumentExtractionError(f"File non trovato: {file_path}")

    parts, is_pdf = await asyncio.to_thread(
        _dispatch_parts, file_path, mime_type
    )
    sep = "\n\n" if is_pdf else ""
    joined = sep.join(parts)
    stripped = (joined or "").strip()
    original = len(stripped)
    if not stripped:
        raise DocumentExtractionError(
            "Documento privo di testo estraibile (forse è una scansione? "
            "OCR non supportato in questa versione)."
        )

    hard_cap = max(1000, int(hard_cap_chars))
    hard_capped = original > hard_cap
    if hard_capped:
        log.warning(
            "course_document_text_truncated",
            path=str(file_path),
            original=original,
            kept=hard_cap,
        )
    text = stripped[:hard_cap] if hard_capped else stripped

    # Offset delle parti sul testo joined, poi shiftati dello strip
    # iniziale e clampati sul testo troncato.
    leading = len(joined) - len(joined.lstrip())
    spans: list[PageSpan] = []
    cursor = 0
    for i, part in enumerate(parts):
        start = cursor - leading
        end = cursor + len(part) - leading
        cursor += len(part) + len(sep)
        start = max(0, min(start, len(text)))
        end = max(0, min(end, len(text)))
        if end > start:
            spans.append((start, end, (i + 1) if is_pdf else None))
    return text, spans, original, hard_capped


async def extract_text(file_path: Path, mime_type: str) -> tuple[str, int]:
    """Estrae il testo da `file_path`. Ritorna `(text_truncated, original_chars)`.

    Il testo viene troncato a `settings.course_document_max_chars` per
    contenere il consumo di token. `original_chars` contiene la lunghezza
    pre-troncamento (utile per logging). Wrapper di `extract_segments`
    con semantica INVARIATA (usato anche dal flusso sincrono
    obiettivi-da-file: non cambiare).
    """
    settings = get_settings()
    text, _spans, original, _capped = await extract_segments(
        file_path,
        mime_type,
        hard_cap_chars=max(1000, int(settings.course_document_max_chars)),
    )
    return text, original
