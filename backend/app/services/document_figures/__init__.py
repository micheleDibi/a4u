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

# Versione del solo ritaglio (colonna `crop_version`, migrazione 0039): 1 =
# ritaglio storico (raster fra 150 e 300 dpi, JPEG per i raster); 2 = render
# allineato alla griglia nativa e PNG per il tratto (doc 18 §22). Cambiarla
# NON cambia l'impronta dell'estrazione (niente supersede delle figure già
# collocate, decisione V2): le figure esistenti passano alla v2 col
# ri-ritaglio sul posto (`scripts/rerender_document_figures`).
CROP_VERSION = 2
