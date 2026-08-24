"""Pianificazione dei chunk per l'analisi a copertura totale (Blocco 1).

Funzioni PURE e DETERMINISTICHE (nessun I/O, nessuna chiamata LLM):
stesso testo + stessi parametri ⇒ stessi chunk. È il prerequisito della
ripresa per-chunk: gli indici dei chunk già persistiti restano validi
tra un run e l'altro finché il fingerprint non cambia.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.services.document_extraction_service import PageSpan

# Versione dei prompt map/digest/reduce: fa parte del fingerprint, così
# un cambiamento dei prompt invalida i chunk persistiti (mai risultati
# misti tra versioni). Bump manuale a ogni modifica dei prompt.
PROMPT_VERSION = "1"

# Fine frase: punto/interrogativo/esclamativo seguito da spazio o newline.
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]?[ \n]")  # noqa: RUF001


@dataclass(frozen=True)
class ChunkSpec:
    """Un chunk pianificato sul testo normalizzato."""

    index: int
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None

    def slice_text(self, text: str) -> str:
        return text[self.char_start:self.char_end]


def _page_of(spans: list[PageSpan], pos: int) -> int | None:
    """Pagina (1-based) che contiene la posizione `pos` (None se il
    formato non è paginato o `pos` cade in un separatore)."""
    for start, end, page in spans:
        if start <= pos < end:
            return page
    return None


def _pick_end(
    text: str,
    page_ends: list[int],
    start: int,
    target_chars: int,
) -> int:
    """Sceglie la fine del chunk: confine pagina entro ±15% del target →
    riga vuota → fine frase → taglio duro al target."""
    ideal = start + target_chars
    window = max(1, int(target_chars * 0.15))
    lo, hi = ideal - window, min(ideal + window, len(text))

    page_candidates = [p for p in page_ends if lo <= p <= hi and p > start]
    if page_candidates:
        return min(page_candidates, key=lambda p: abs(p - ideal))

    zone = text[max(start, lo):hi]
    zone_offset = max(start, lo)
    best: int | None = None
    for match in re.finditer(r"\n\n", zone):
        pos = zone_offset + match.end()
        if pos > start and (best is None or abs(pos - ideal) < abs(best - ideal)):
            best = pos
    if best is not None:
        return best

    for match in _SENTENCE_END.finditer(zone):
        pos = zone_offset + match.end()
        if pos > start and (best is None or abs(pos - ideal) < abs(best - ideal)):
            best = pos
    if best is not None:
        return best

    return min(ideal, len(text))


def plan_chunks(
    text: str,
    page_spans: list[PageSpan],
    *,
    target_chars: int,
    overlap_chars: int,
    tail_merge_ratio: float = 0.25,
) -> list[ChunkSpec]:
    """Divide il testo in chunk con overlap e tagli "intelligenti".

    - taglio preferito: confine di pagina entro ±15% del target, poi
      riga vuota, poi fine frase, poi taglio duro;
    - overlap: il chunk successivo riparte `overlap_chars` prima della
      fine del precedente (cattura definizioni a cavallo del taglio; i
      doppioni li elimina il merge);
    - coda < `tail_merge_ratio`·target: fusa nell'ultimo chunk.
    """
    n = len(text)
    if n == 0:
        return []
    target = max(1000, int(target_chars))
    overlap = max(0, min(int(overlap_chars), target // 2))
    page_ends = sorted(end for _s, end, _p in page_spans)

    chunks: list[ChunkSpec] = []
    start = 0
    index = 0
    while start < n:
        remaining = n - start
        if remaining <= int(target * (1 + tail_merge_ratio)):
            end = n
        else:
            end = _pick_end(text, page_ends, start, target)
            end = max(end, start + 1)
        chunks.append(
            ChunkSpec(
                index=index,
                char_start=start,
                char_end=end,
                page_start=_page_of(page_spans, start),
                page_end=_page_of(page_spans, max(start, end - 1)),
            )
        )
        if end >= n:
            break
        index += 1
        start = max(start + 1, end - overlap)
    return chunks


def compute_fingerprint(
    file_bytes: bytes,
    *,
    target_chars: int,
    overlap_chars: int,
    singleshot_max_chars: int,
    hard_cap_chars: int,
    model: str,
) -> str:
    """Fingerprint del run chunked: sha256 dei BYTES del file (economico:
    niente ri-estrazione del testo; il file è immutabile post-create) ‖
    parametri di chunking ‖ modello ‖ PROMPT_VERSION. Se cambia, i chunk
    persistiti vengono scartati (mai risultati misti)."""
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    payload = "|".join(
        [
            file_hash,
            str(int(target_chars)),
            str(int(overlap_chars)),
            str(int(singleshot_max_chars)),
            str(int(hard_cap_chars)),
            model,
            PROMPT_VERSION,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
