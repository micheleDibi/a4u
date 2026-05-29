# 16 — Ricerca paper scientifici (OpenAlex + enrichment + import + riassunto AI)

Sezione **"Paper Scientifici"** dentro la tab **Documenti** di un corso
(non un tab proprio). Permette di cercare paper accademici, vederne un
riassunto AI on-demand e importarli come `CourseDocument` — da cui la
pipeline di pre-processing (doc [02 — Document pre-processing](02-document-preprocessing.md))
produce poi il riassunto strutturato di Appendice A.

L'architettura è **multi-source**: OpenAlex è la **primary search**
(~250M paper, gratis, niente API key); Semantic Scholar e Crossref sono
usati **solo on-demand** per l'enrichment (TL;DR, subjects,
references_count, abstract fallback) di singoli paper — **mai in fase di
search**, per non scatenare ~40 chiamate secondarie a ricerca.

## 1. Architettura multi-source

| Source | Ruolo | Quando | Cosa fornisce |
|---|---|---|---|
| **OpenAlex** | Primary (discovery + paginazione) | Sempre, ad ogni search | metadata core, abstract (inverted index), `oa_pdf_url`, `relevance_score`, cursor |
| **Semantic Scholar** | Secondary (enrichment) | Solo on-demand (ai-summary, import) | `tldr.text`, fallback `openAccessPdf.url`, abstract pulito |
| **Crossref** | Secondary (enrichment) | Solo on-demand (ai-summary, import) | abstract ripulito da JATS, `subjects[]`, `references_count` |

> **Perché on-demand?** L'enrichment richiede un lookup S2 + Crossref
> **per ogni paper** (chiave: DOI). Farlo durante la search di 20 paper
> significherebbe ~40 chiamate HTTP secondarie a richiesta — lento e
> aggressivo verso i provider. Si arricchisce quindi solo il paper che
> l'utente vuole effettivamente riassumere o importare
> (`backend/app/services/openalex_search_service.py:5-7`).

Entrambi i client secondari sono **non bloccanti**: ritornano `None` su
404 / HTTP error / timeout, così l'enrichment procede degradando con
grazia senza far fallire il flusso principale
(`semantic_scholar_client.py:61-98`, `crossref_client.py:95-127`).

Tutti e 3 i client mettono `papers_polite_email` nel `User-Agent` come
`mailto:` (es. `a4u/1.0 (mailto:...)`) per entrare nel **"polite pool"**
dei provider — rate-limit più permissivo. Vuota → User-Agent senza
mailto (`openalex_client.py:72-77`, `semantic_scholar_client.py:54-58`,
`crossref_client.py:52-56`).

## 2. Pipeline / flusso

```
[1] SEARCH          POST /papers/search   (cursor-based, no enrichment)
       │                 OpenAlex /works → PaperOut[]
       ▼
[2] RIASSUNTO AI    POST /papers/ai-summary  (on-demand, sincrono, no DB)
       │                 enrichment server-side (se DOI) → OpenAI
       ▼
[3] IMPORT          POST /papers/import   (≤ 50 paper)
       │                 OA → download .pdf  | non-OA → genera .md metadata
       ▼
   CourseDocument (summary_status='pending')
       │
       ▼
   course_document_worker  →  extract_text + summarize AI  (doc 02)
```

I tre passi sono indipendenti: la search non arricchisce nulla, il
riassunto AI non persiste nulla, l'import è l'unico che crea documenti.

### Strategia di import (per ogni paper)

`backend/app/services/paper_import_service.py:145-217`
(`import_paper`):

1. Se `oa_pdf_url` presente → tenta `openalex_client.download_pdf`
   (`paper_import_service.py:165-182`). Limite =
   `course_document_max_mb × 1024 × 1024` (`:162`).
   - Download OK → salva il `.pdf` (`application/pdf`), `mode="pdf"`.
   - `OpenAlexError` (download fallito / 4xx / PDF troppo grande) →
     fallback graceful a `pdf_bytes=None` (niente eccezione propagata).
   - `ValidationAppError` in `add_document_from_bytes` (es. MIME) →
     ripiega comunque a metadata (`:197-204`).
2. Altrimenti (non-OA o fallback) → genera un `.md` con i metadata
   (`_render_metadata_md`, `:83-142`: titolo, autori, anno, journal,
   type, DOI, link, citations, OA, abstract, TL;DR da S2, keywords,
   subjects da Crossref) salvato come `text/markdown`, `mode="metadata"`.

In **entrambi** i casi viene creato un `CourseDocument` con
`summary_status="pending"`, e il `course_document_worker` lo prende in
carico (extract_text + summarize AI). Il `.md` viene letto dal worker
come **plain text** — `text/markdown` è in whitelist
`ALLOWED_DOCUMENT_MIME_TYPES` (`file_service.py:61-69`), che è ciò che
abilita l'import in modalità metadata.

**Filename** (`_build_filename_stem`, `:67-80`): pattern
`{primo_autore_slug}_{anno}_{titolo_slug}_{uuid6}`, con slug
NFKD → ascii lowercase e separatore `_`; il suffisso UUID corto evita
collisioni quando lo stesso paper viene re-importato.

## 3. Enrichment (`paper_enrichment_service.enrich_paper`)

`backend/app/services/paper_enrichment_service.py:85-130`. Riceve un
`OpenAlexWork` e ritorna un `EnrichedPaper`:

- Se il paper **non ha DOI** → salta l'enrichment, restituisce i soli
  dati OpenAlex (`:88-91`).
- Se ha DOI → lancia **in parallelo** S2 + Crossref con
  `asyncio.gather(..., return_exceptions=True)` (`:92-96`); le eccezioni
  vengono loggate (`paper_enrichment_s2_failed` /
  `paper_enrichment_crossref_failed`) ma **non propagate** (`:100-111`).

**Merge con priorità per campo:**

| Campo | Priorità | Riferimento |
|---|---|---|
| `abstract` | OpenAlex > Crossref > S2 (soglia `len >= 40`; sotto soglia ripiega su qualunque valore presente) | `_merge_abstract`, `:52-72` |
| `oa_pdf_url` | OpenAlex > S2 fallback | `_merge_oa_pdf_url`, `:75-82` |
| `tldr` | solo Semantic Scholar | `:127` |
| `subjects` | solo Crossref | `:128` |
| `references_count` | solo Crossref | `:129` |

> OpenAlex restituisce l'abstract come **inverted index**
> (`{"word": [pos,...]}`): `_reconstruct_abstract`
> (`openalex_client.py:92-109`) lo ricostruisce, ma può risultare
> spezzato — da qui la preferenza per l'abstract "ufficiale" di Crossref
> quando OpenAlex è povero.

Per il riassunto AI il BE costruisce un `OpenAlexWork` **sintetico** dal
`PaperOut` ricevuto dal FE (solo i campi necessari al merge), per
riusare `enrich_paper` senza ri-cercare su OpenAlex
(`backend/app/api/v1/courses.py:741-767`).

## 4. Riassunto AI (`openai_paper_summary_service.generate_paper_summary`)

`backend/app/services/openai_paper_summary_service.py:157-287`. Pattern
**speculare a** `openai_course_objectives_service`: sincrono, JSON schema
strict, **niente persistenza** (l'output serve solo per il blocco inline
nel FE).

- Modello `settings.openai_paper_summary_model` (default `gpt-4o-mini`),
  `max_tokens = settings.openai_paper_summary_max_tokens` (default 3000),
  `temperature=0.3` (`:191-203`).
- `response_format` = `json_schema` con `strict: True` e
  `additionalProperties: False` (`PAPER_SUMMARY_JSON_SCHEMA`, `:132-154`).
- **Lingua = `course.language_code`** (NON quella dell'abstract): il
  system prompt è scelto IT/EN in base al language_code (`:124-129`) e
  istruisce esplicitamente a generare tutte le sezioni nella lingua del
  corso anche se l'abstract è in un'altra lingua.
- Validazione: JSON → `PaperAISummaryOut.model_validate`; errori
  HTTP / parse / schema → `OpenAIPaperSummaryError` (`:254-273`).
- Ritorna `(output, usage)` con token/model, ma l'endpoint **scarta**
  `usage` (`courses.py:799`) e non scrive nulla in DB.

**Output `PaperAISummaryOut`** (4 sezioni richieste, vincoli "soft" di
lunghezza nel prompt, vincoli "hard" Pydantic più larghi):

| Campo | Target prompt | Vincolo Pydantic |
|---|---|---|
| `short_summary` | 200-400 char | `min 20`, `max 2000` |
| `technical_summary` | 600-1200 char | `min 50`, `max 4000` |
| `keywords` | 5-10 | `min 1`, `max 20` |
| `study_limitations` | 200-500 char | `min 20`, `max 2000` |

## 5. Endpoint API (3)

Tutti sotto `/orgs/{org_id}/courses/{course_id}/papers/*`, permesso
`course:edit`, previo `_ensure_org` + `course_service.get_course`. Vedi
la reference completa in [05 — API reference](05-api-reference.md).

| Metodo | Path | Permesso | Request → Response | Errori |
|---|---|---|---|---|
| POST | `/{course_id}/papers/search` | `course:edit` | `PaperSearchInput` → `PaperSearchResultsOut` | `502 {code:"openalex_error"}` su `OpenAlexError` |
| POST | `/{course_id}/papers/ai-summary` | `course:edit` | `PaperAISummaryInput` → `PaperAISummaryOut` | `409 openai_not_configured`; `502 {code:"openai_error"}` su `OpenAIPaperSummaryError` |
| POST | `/{course_id}/papers/import` | `course:edit` | `PaperImportInput` → `PaperImportResultOut` (`pdf_count`/`metadata_count`) | (errori di download gestiti con fallback a metadata) |

`search` (`courses.py:652-697`), `ai-summary` (`:700-814`), `import`
(`:817-868`). L'import itera i paper, chiama `import_paper`, conta
pdf/metadata e infine `db.commit()` (`:847-868`).

## 6. Schemi Pydantic

`backend/app/schemas/paper_search.py` + `paper_ai_summary.py`.

`PaperType = Literal["article", "preprint", "review", "other"]`
(`paper_search.py:19`).

| Schema | Campi salienti |
|---|---|
| `PaperSearchFilters` (`:22-32`) | `year_from`/`year_to` `int\|None` `[1900,2100]`; `is_oa` `bool\|None`; `min_citations` `int\|None` `ge=0`; `author_name`/`venue_name` `str\|None` `max 200`; `work_type` `PaperType\|None` |
| `PaperSearchInput` (`:35-39`) | `query` `str` `max 500` (def `""`); `filters` (default-factory); `cursor` `str\|None`; `per_page` `int` `[1,50]` def 20 |
| `PaperOut` (`:42-62`) | `id` (OpenAlex Work URL), `doi`, `title`, `abstract`, `authors[]`, `year`, `journal`, `citations`, `is_oa`, `oa_pdf_url`, `doi_url`, `work_type`, `keywords[]`, `relevance_score`; on-demand: `tldr` (def None), `subjects` (def `[]`), `references_count` (def None) |
| `PaperSearchResultsOut` (`:65-68`) | `results[]`, `next_cursor`, `total_count` |
| `PaperAISummaryInput` (`:71-76`) | solo `paper: PaperOut` |
| `PaperImportInput` (`:79-82`) | `papers: list[PaperOut]` `min 1`, `max 50` |
| `PaperImportItemResultOut` (`:85-92`) | `document_id`, `filename`, `mode` (`pdf`\|`metadata`), `paper_id` |
| `PaperImportResultOut` (`:95-98`) | `imported[]`, `pdf_count`, `metadata_count` |
| `PaperAISummaryOut` (`paper_ai_summary.py:19-45`) | vedi §4. Validator `_clean_keywords` (`:25-45`): trim, troncamento a 80 char, dedup case-insensitive, `ValueError` se lista vuota dopo cleanup |

## 7. Service / client backend

| File | Responsabilità |
|---|---|
| `openalex_client.py` | `search_works` (`/works`, cursor-based, `:238-317`) + `download_pdf` (`:320-366`) + dataclass `OpenAlexWork`. Ricostruzione abstract da inverted index, estrazione DOI/autori/journal/keywords/OA-url. Errori → `OpenAlexError`. |
| `openalex_search_service.py` | Wrapper di alto livello: mapping `OpenAlexWork → PaperOut` (`_to_paper_out`), `_normalize_type` (collassa i tipi granulari OpenAlex nei 4 valori del FE), `_clamp_relevance`, `_doi_url`. **NON fa enrichment** (`:1-7`). |
| `semantic_scholar_client.py` | `get_paper_by_doi` via `/graph/v1/paper/DOI:{doi}` (fields ridotti). Fornisce `tldr.text` + fallback `openAccessPdf.url`. Non bloccante. |
| `crossref_client.py` | `get_work_by_doi` via `/works/{doi}`. Abstract ripulito dai tag JATS (`_clean_abstract`), `subjects[]`, `references_count` (= `len(message["reference"])`). Non bloccante. |
| `paper_enrichment_service.py` | `enrich_paper` — merge parallelo S2 + Crossref (vedi §3). |
| `openai_paper_summary_service.py` | `generate_paper_summary` — riassunto AI sincrono (vedi §4). |
| `paper_import_service.py` | `import_paper`, `_build_filename_stem`, `_render_metadata_md`, `_slugify` (vedi §2). |

Helper riusati dall'import:
`course_service.add_document_from_bytes` (`course_service.py:746-804`) →
`file_service.save_document_from_bytes` (`file_service.py:314-360`):
salva in subdir `courses/{course.id}`, crea `CourseDocument` con
`summary_status="pending"` (`course_service.py:777`) e scrive audit con
`metadata.source="external_import"` (`:801`).

### Relevance score

`_clamp_relevance` (`openalex_search_service.py:55-65`) normalizza il
`relevance_score` di OpenAlex (può essere > 1) in `[0, 1]` con una
compressione sigmoide-like `score / (score + 5)`: score 5 → 0.5,
20 → 0.8, 1 → 0.17. `None` resta `None`, valori negativi → `0.0`. Il FE
lo mostra come percentuale con barra colorata a soglie 70 / 40
(`PaperResultCard.tsx:61-72`).

## 8. Frontend

- **`CoursePaperSearch.tsx`** — pannello della sezione. Form filtri
  (query + 7 filtri opzionali), lista risultati con paginazione cursor
  ("Carica altri 20"), multi-select + "Importa selezionati", riassunti
  AI **cached per la sessione** in `summariesById:
  Record<string, SummaryState>` (`:88-90`) + `expandedSummaryIds`
  (`:91-93`).
  - `per_page` fisso a 20 (`:105`). Prima pagina `cursor=null` → reset
    risultati/selezione (`:108-112`); "Carica altri" usa `nextCursor` →
    append con **dedup per `id`** (`:113-121`). Footer visibile solo se
    `nextCursor` presente (`:490-503`).
  - `onToggleSummary` (`:178-217`): se già `success` toggla solo la
    visibilità (nessuna nuova chiamata); se `loading` ignora; altrimenti
    genera o ritenta. La cache sopravvive a "Carica altri" e al toggle;
    si perde a unmount o a una nuova ricerca primaria.
- **`PaperResultCard.tsx`** — card del singolo paper.
  `SummaryState = loading | success | error` (`:24-27`); badge OA /
  non-OA, barra relevance, badge citazioni/tipo, abstract collassabile
  (`ABSTRACT_PREVIEW_CHARS = 320`), keywords + subjects uniti, blocco
  TL;DR se presente, bottone "Riassunto AI". Il riassunto è reso
  **inline** sotto i bottoni in `PaperSummaryBlock` (`:286-323`) — 4
  sezioni: short, technical, keywords, limitations.
- **`api/courses.ts`** → namespace `coursesApi.papers.{search, aiSummary,
  importMany}` (`:1006-1058`). Timeout: search `60s`, aiSummary `180s`,
  importMany `300s` (gli import lunghi scaricano N PDF in sequenza).
  Tipi esposti: `PaperType`, `PaperSearchFilters`, `PaperSearchInput`,
  `PaperOut`, `PaperSearchResultsOut`, `PaperAISummaryOut`,
  `PaperImportItemResultOut`, `PaperImportResultOut` (`:735-803`).

## 9. Configurazione

Vedi [04 — Configuration](../04-configuration.md). Variabili rilevanti
(`backend/app/core/config.py:91-98`):

| ENV | Default |
|---|---|
| `OPENALEX_BASE_URL` | `https://api.openalex.org` |
| `SEMANTIC_SCHOLAR_BASE_URL` | `https://api.semanticscholar.org` |
| `CROSSREF_BASE_URL` | `https://api.crossref.org` |
| `PAPERS_POLITE_EMAIL` | `""` (vuota = User-Agent senza mailto) |
| `OPENAI_PAPER_SUMMARY_MODEL` | `gpt-4o-mini` |
| `OPENAI_PAPER_SUMMARY_MAX_TOKENS` | `3000` |

## Note / edge case

- **Nessuna API key** per OpenAlex / Semantic Scholar / Crossref: bastano
  gli endpoint pubblici. La `PAPERS_POLITE_EMAIL` è raccomandata ma non
  obbligatoria.
- L'enrichment è **best-effort**: se S2 o Crossref non rispondono, il
  paper resta con i soli dati OpenAlex — nessun fallimento utente-facing.
- Un paper marcato OA il cui `oa_pdf_url` non scarica un PDF valido
  finisce comunque in import **come metadata `.md`**: l'import non
  fallisce mai per un download andato male.
- L'import di metadata `.md` dipende dalla presenza di `text/markdown`
  nella whitelist MIME documenti: il `course_document_worker` legge il
  `.md` come testo e produce il riassunto AI di Appendice A (vedi
  [02 — Document pre-processing](02-document-preprocessing.md)).
