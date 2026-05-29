# 02 — Document pre-processing (Appendice A)

Pipeline che trasforma ogni documento caricato in un **riassunto strutturato JSON**
che diventa la rappresentazione canonica del documento per le fasi AI successive
(`prompt_generazione_corsi.md` §2 + Appendice A).

## Flusso

```
[upload]                                                                   [riassunto pronto]
   │                                                                              │
   ▼                                                                              │
course_document (summary_status=pending)                                          │
   │                                                                              │
   │  worker tick (poll ogni 4s)                                                  │
   ▼                                                                              │
summary_status=processing                                                         │
   │                                                                              │
   │  document_extraction_service.extract_text                                    │
   ▼                                                                              │
testo grezzo (troncato a 120k caratteri)                                          │
   │                                                                              │
   │  openai_summarize_service.summarize_document                                 │
   ▼                                                                              │
JSON Pydantic-validato (DocumentSummaryOut)                                       │
   │                                                                              │
   │  scrittura summary + summary_tokens + summary_generated_at                   │
   ▼                                                                              │
summary_status=ready ──────────────────────────────────────────────────────────────►│
   │
   │  errore in qualunque fase
   ▼
summary_status=failed (summary_error popolato; nessun retry automatico)
```

## Trigger

- **Upload**: ogni `POST /documents` crea la riga `course_document` con
  `summary_status='pending'`. Il worker la prende al prossimo tick.
- **Reprocess manuale**: `POST /documents/{id}/reprocess` resetta a `pending`.
- **Import paper scientifico**: l'import di un paper dalla ricerca (vedi
  [16 — Paper search](16-paper-search.md), endpoint `POST /papers/import`)
  crea righe `course_document` con `summary_status='pending'` tramite
  `course_service.add_document_from_bytes`
  (`backend/app/services/course_service.py:746` — sibling di `add_document`,
  riceve `bytes` invece di un `UploadFile`). La sorgente del documento dipende
  dalla disponibilità del PDF (vedi `paper_import_service.import_paper`,
  `backend/app/services/paper_import_service.py:145`):

  | Caso | Documento creato | MIME | `mode` |
  |---|---|---|---|
  | Paper open-access con PDF scaricabile (`download_pdf` OK) | `.pdf` | `application/pdf` | `pdf` |
  | Paper non-OA, oppure download/validazione PDF fallita | `.md` generato dai metadati | `text/markdown` | `metadata` |

  Il `.md` di fallback (`_render_metadata_md`,
  `backend/app/services/paper_import_service.py:83`) raccoglie titolo, autori,
  anno, journal, type, DOI, link, citazioni, flag OA, abstract, TL;DR
  (Semantic Scholar), keywords e subjects (Crossref). In **entrambi** i casi il
  `course_document_worker` processa la riga con lo **stesso flusso** descritto
  sopra (`extract_text` legge il `.md` come plain text UTF-8 + `summarize` AI).

  > **Audit**: l'import usa `action='course.document.add_from_bytes'` con
  > `metadata.source='external_import'`
  > (`backend/app/services/course_service.py:789-803`), distinto dall'upload
  > standard (`course.document.add`).

## Worker — `course_document_worker.py`

Pattern: single-instance asyncio task lanciato da `app.main.lifespan`. Gemello
di `avatar_clip_worker`.

Tick:
1. `SELECT ... WHERE summary_status IN ('pending', 'processing')` (include
   `processing` per gestire crash a metà run).
2. Per ogni doc: `_process_one(db, doc)`.

`_process_one`:
1. Set `status='processing'`, `summary_attempts += 1`, commit (visibilità per UI).
2. Estrae testo via `document_extraction_service.extract_text(...)`. Salva
   `text_extracted_at`, `text_chars_extracted`. Su errore → `failed` + messaggio.
3. Chiama `openai_summarize_service.summarize_document(...)`. Salva `summary`
   (JSONB), `summary_tokens`, `summary_generated_at`, `status='ready'`.
4. Audit `course.document.summary.{ready|failed}`.

Su `OpenAINotConfiguredError` → `failed` con messaggio "OpenAI non configurato
(admin: imposta OPENAI_API_KEY)". Nessun retry.

## Estrazione testo — `document_extraction_service.py`

Pure-Python, async-friendly via `asyncio.to_thread` (le librerie sottostanti
sono blocking).

Dispatch su mime type:

| Mime | Libreria | Note |
|---|---|---|
| `application/pdf` | `pdfplumber` | itera pagine, concatena `extract_text()` |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `python-docx` | paragrafi + tabelle (TSV) |
| `application/msword` (DOC legacy) | `docx2txt` | |
| `application/rtf` / `text/rtf` | `striprtf` | |
| `text/plain`, `text/markdown` | built-in | UTF-8 con `errors='replace'` |

> `text/markdown` è già gestito dal branch built-in (lettura plain text UTF-8):
> i `.md` generati dall'**import paper** (vedi [Trigger](#trigger)) ricadono qui
> senza codice dedicato — `extract_text` ne legge i metadati come testo grezzo,
> che `summarize_document` riassume nel JSON di Appendice A come per ogni altro
> documento.

Solleva `DocumentExtractionError` su corruzione, password protetta, formato non
riconosciuto. Tronca a `settings.course_document_max_chars` (default 120000) e
logga warning con la lunghezza originale.

> PDF immagine (scan): `pdfplumber` ritorna stringa vuota → `failed` con
> messaggio "Documento privo di testo estraibile (forse scan? OCR non
> supportato)". OCR non è in scope.

## OpenAI summarize — `openai_summarize_service.py`

Wrapper della chiamata OpenAI per Appendice A.

- **Modello**: `settings.openai_summarize_model` (default `gpt-4o-mini`).
- **Response format**: `json_schema` (più rigido di `json_object`).
- **Schema inline**: copia letterale di Appendice A (`source_title`,
  `detected_language`, `abstract`, `structure_outline`, `key_concepts`,
  `definitions`, `examples_or_cases`, `formulas_or_rules`,
  `authors_and_references`, `didactic_relevance_tags`).
- **System prompt**: copia letterale dell'Appendice A.
- **Parametri**: `temperature=0.2`, `max_tokens=settings.openai_summarize_max_tokens`
  (default 8000).
- **Validazione output**: `DocumentSummaryOut.model_validate(...)`. Failure →
  `OpenAISummarizeError` con dettagli.

## Schema Pydantic — `schemas/document_summary.py`

Mirror dell'Appendice A. `model_config = ConfigDict(extra='forbid')` su tutti i
sotto-modelli.

```python
class DocumentSummaryOut(BaseModel):
    source_title: str
    detected_language: str
    abstract: str
    structure_outline: list[str]
    key_concepts: list[KeyConcept]              # min 1
    definitions: list[Definition]
    examples_or_cases: list[ExampleOrCase]
    formulas_or_rules: list[FormulaOrRule]
    authors_and_references: list[AuthorOrReference]
    didactic_relevance_tags: list[str]          # min 1, max 20
```

## Frontend

- **`CourseDocumentUploader.tsx`** — drag&drop + lista. Per ogni riga:
  - Badge `summary_status` (Pronto/Errore/Elaborazione…/Da elaborare)
  - Tooltip con `summary_error` su `failed`
  - Icona "Vedi dettaglio" (👁) se `ready` → apre il dialog
  - Pulsante "Rielabora" (🔄) sempre disponibile
  - Pulsante elimina

- **`DocumentSummaryDialog.tsx`** — modal con:
  - Sidebar verticale a sinistra (8 voci: abstract, struttura, concetti chiave,
    definizioni, esempi e casi, formule e regole, autori e riferimenti, tag
    rilevanza)
  - Contenuto scrollabile a destra
  - **KaTeX rendering** delle formule (con fallback a mono se il parsing
    fallisce per testo non-LaTeX)
  - No footer tecnico (rimosso per non confondere l'utente)

- **`CourseEditorPage.tsx`** — `useQuery` con `refetchInterval` di 5s quando
  almeno un documento è in stato `pending` o `processing`. Si ferma quando
  tutti sono `ready`/`failed`.

## Configurazione

| Var | Default | Descrizione |
|---|---|---|
| `openai_summarize_model` | `gpt-4o-mini` | Modello OpenAI per il riassunto |
| `openai_summarize_max_tokens` | `8000` | Tetto token completion |
| `course_document_max_chars` | `120000` | Troncamento input (~30k token) |
| `course_document_poll_interval_seconds` | `4` | Intervallo poll worker |

## API endpoint

| Metodo | Path | Permission | Descrizione |
|---|---|---|---|
| `POST` | `/documents` | `course:edit` | Upload (multipart) — ritorna doc con status pending |
| `GET` | `/documents` | `course:view` | Lista documenti del corso |
| `GET` | `/documents/{id}?include_summary=true` | `course:view` | Dettaglio con summary JSONB esploso |
| `POST` | `/documents/{id}/reprocess` | `course:edit` | Reset a `pending` |
| `DELETE` | `/documents/{id}` | `course:edit` | Elimina (anche file su disco) |

## Audit

- `course.document.add` (su upload — `course_service.py:731`)
- `course.document.add_from_bytes` (import paper — `metadata.source='external_import'`, `course_service.py:791`)
- `course.document.summary.ready` (success worker — `course_document_worker.py:158`)
- `course.document.summary.failed` (failure worker — `course_document_worker.py:69,112,131`)
- `course.document.reprocess` (reset manuale — `course_service.py:820`)
- `course.document.delete` (eliminazione — `course_service.py:850`)
