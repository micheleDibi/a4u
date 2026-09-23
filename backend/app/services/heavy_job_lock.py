"""Lock di processo per i lavori pesanti su CPU e memoria.

Estrazione delle figure (Docling) e compilazione TeX (WP6) non girano mai
insieme: sulla VM di produzione (2 core, ~4 GB) due picchi sommati
porterebbero all'OOM. I render Chromium esistenti restano fuori (rischio
residuo dichiarato in docs/courses/18-literature-figures.md).
"""

from __future__ import annotations

import asyncio

HEAVY_JOB_LOCK = asyncio.Lock()
