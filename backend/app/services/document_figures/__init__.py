"""Estrazione delle figure di fonte dai documenti del corso.

Architettura (docs/courses/18-literature-figures.md):

- il worker `course_document_figures_worker` (processo dell'app) prende un
  documento alla volta, lo scarica dallo storage in una cartella temporanea
  e guida un sottoprocesso (:mod:`.child`) tramite :mod:`.runner`;
- il sottoprocesso rileva le figure (Docling, oppure il motore euristico) e
  ne ritaglia il bbox con PDFium, scrivendo i PNG/JPEG nella cartella
  temporanea; non tocca mai DB, storage né segreti;
- il padre applica filtri e deduplicazione, carica i ritagli nello storage e
  scrive le righe `course_document_figure`.

I moduli di rilevazione e ritaglio sono puri (niente `get_settings()`), così
il sottoprocesso gira con un ambiente ridotto all'osso.
"""

# Versione dell'algoritmo di estrazione: cambia quando cambiano rilevazione,
# ritaglio o filtri in modo da rendere diversi i ritagli.
EXTRACTION_VERSION = 1
