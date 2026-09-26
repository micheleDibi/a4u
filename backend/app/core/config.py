from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Risolve il path assoluto di `.env` alla root del repository:
#   backend/app/core/config.py → parents[3] = repo root.
# Pydantic Settings ignora silenziosamente il file se non esiste
# (es. in container produzione le var arrivano da `environment:`).
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT_ENV),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    database_url: str = "postgresql+asyncpg://a4u:a4u_dev_password@localhost:5432/a4u"
    # Pool DB: alzato da 10/20 a 20/60 per supportare la duplicazione corso
    # con concorrenza alta. La phase combined apre 3 sessioni in parallelo
    # per lezione (content + slides + speech in 3 sessioni separate); con
    # cap 15-20 lezioni concorrenti si arriva a 45-60 connessioni dedicate
    # al worker. Postgres default supporta 100 connessioni totali, abbiamo
    # ~20 di margine per le richieste utente normali.
    database_pool_size: int = 20
    database_max_overflow: int = 60

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 15
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 7

    frontend_origin: str = "http://localhost:5173"
    cookie_domain: str | None = None
    cookie_secure: bool = False

    upload_dir: str = "./uploads"
    upload_max_mb: int = 5
    avatar_audio_max_mb: int = 10
    course_document_max_mb: int = 25
    public_base_url: str = "http://localhost:8000"

    # === Storage backend (persistenza file) ===
    # `local`    → filesystem locale (default, dev): comportamento storico.
    # `ovh_ftp`  → server OVH via FTP/FTPS (porta 21).
    # `ovh_sftp` → server OVH via SFTP (porta 22, su SSH). Più robusto su
    #              hosting condiviso. Riusa le stesse credenziali `ovh_ftp_*`.
    # In ovh_* i file sono serviti pubblicamente via HTTP da
    # `ovh_public_base_url`. Vedi `app/services/remote_storage.py`.
    storage_backend: Literal["local", "ovh_ftp", "ovh_sftp"] = "local"
    # Cutover: se un file non è (ancora) su OVH, prova a leggerlo/servirlo dal
    # filesystem locale ancora montato (e logga un warning). Disattivare a
    # regime (Fase 4) una volta completata la migrazione.
    storage_local_fallback: bool = True
    # Credenziali FTP OVH (valorizzare in `.env`, MAI in codice).
    ovh_ftp_host: str | None = None
    ovh_ftp_port: int = 21
    ovh_ftp_user: str | None = None
    ovh_ftp_password: str | None = None
    # Path FTP della cartella radice in cui scrivere/leggere, mappato al
    # docroot pubblico. Es. `/www/media` se `ovh_public_base_url` =
    # `https://progettiersaf.com/media`. Le key (`uploads/...`,
    # `generated_pdfs/...`) sono appese a questo path.
    ovh_ftp_base_path: str = "/"
    # FTPS esplicito (TLS) sul canale di controllo + dati. Disattivare
    # (`false`) SOLO come escape di debug: invia le credenziali in chiaro.
    # Ignorato con `ovh_sftp` (SFTP è sempre cifrato).
    ovh_ftp_use_tls: bool = True
    ovh_ftp_timeout_seconds: int = 30
    # Porta SFTP (usata solo con `storage_backend=ovh_sftp`). Stesse
    # credenziali/host di `ovh_ftp_*`, ma su SSH.
    ovh_sftp_port: int = 22
    # Base URL pubblica da cui i file su OVH sono raggiungibili via HTTP:
    # `public_url(key) = f"{ovh_public_base_url}/{key}"`. Es.
    # `https://progettiersaf.com/media`.
    ovh_public_base_url: str | None = None

    minimax_api_key: str | None = None
    minimax_base_url: str = "https://api.minimax.io"
    minimax_video_model: str = "MiniMax-Hailuo-02"
    minimax_clip_duration: int = 6
    minimax_clip_resolution: str = "1080P"
    minimax_poll_interval_seconds: int = 10

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    # Fallback model usato dalla duplicazione corso quando il modello
    # default fallisce con transient persistenti (5xx, timeout) anche
    # dopo i retry interni. Piu' costoso ma piu' stabile.
    openai_model_fallback: str = "gpt-4o"
    # Cap globale di chiamate OpenAI translate concorrenti durante la
    # duplicazione corso. 80 e' al ~3% del rate-limit gpt-4o-mini tier
    # 2 (30000 RPM): margine ampio per gestire i 520 transient di
    # Cloudflare senza saturare. Alzabile in prod via env.
    openai_translate_global_concurrency: int = 80
    openai_translate_batch_size: int = 40
    openai_summarize_model: str = "gpt-4o-mini"
    openai_summarize_max_tokens: int = 8000
    # Pipeline di analisi a copertura totale (map → merge → reduce) per i
    # documenti sopra soglia. Output map contenuto (fatti di UN chunk);
    # reduce a 12000 (gpt-4o-mini regge 16k out) per non troncare il JSON
    # finale con molte definizioni — il single-shot resta a 8000.
    openai_summarize_map_max_tokens: int = 4000
    openai_summarize_reduce_max_tokens: int = 12000
    # Generazione AI di obiettivi corso + argomenti chiave da un
    # documento di riferimento caricato dall'utente (tab "Obiettivi e
    # Argomenti chiave"). Output target: obiettivi 2500-5000 caratteri
    # (prosa articolata) + lista 8-15 argomenti chiave. Max tokens 8000
    # per evitare troncamenti sul JSON (string escaping inflaziona il
    # conteggio token rispetto ai caratteri visibili).
    openai_objectives_model: str = "gpt-4o-mini"
    openai_objectives_max_tokens: int = 8000

    # Ricerca paper scientifici nella tab "Documenti" (multi-source).
    # OpenAlex e' la primary search, Semantic Scholar e Crossref sono
    # usati on-demand per arricchire i metadata di singoli paper.
    # `papers_polite_email` viene messa nel User-Agent come `mailto:` per
    # entrare nel "polite pool" di tutti e 3 i provider (no rate-limit
    # aggressivo). Vuota = User-Agent senza mailto.
    openalex_base_url: str = "https://api.openalex.org"
    semantic_scholar_base_url: str = "https://api.semanticscholar.org"
    crossref_base_url: str = "https://api.crossref.org"
    papers_polite_email: str = ""
    # Riassunto AI di paper (sincrono, no persistenza). Output: riassunto
    # breve + tecnico + parole chiave + limiti dello studio.
    openai_paper_summary_model: str = "gpt-4o-mini"
    openai_paper_summary_max_tokens: int = 3000
    openai_modules_lessons_model: str = "gpt-5.5"
    openai_architecture_max_tokens: int = 8000
    # Reasoning effort: vedi `.env.example` per spiegazione + valori validi.
    # Su modelli non-reasoning il backend NON invia il parametro (no error).
    openai_architecture_reasoning_effort: str = "medium"
    openai_lesson_structure_model: str = "gpt-5.5"
    # gpt-5.5 consuma molti token nel reasoning prima di emettere il JSON.
    # 16000 lascia margine per ~5 lezioni × 4 sezioni con rationale lunghi
    # + reasoning. Se vedi `lessons_structure_output_truncated`, alza ancora.
    openai_lesson_structure_max_tokens: int = 16000
    openai_lesson_structure_reasoning_effort: str = "medium"
    course_document_max_chars: int = 120_000
    course_document_poll_interval_seconds: int = 4
    # --- Copertura totale dell'analisi documenti (Blocco 1) ---
    # Kill-switch: False = comportamento storico (single-shot troncato a
    # course_document_max_chars) senza rollback DB.
    course_document_full_coverage_enabled: bool = True
    # Sotto questa soglia il flusso resta il single-shot attuale.
    course_document_singleshot_max_chars: int = 100_000
    # Dimensionamento chunk (~7k token a ~3,5 char/token) e overlap
    # (mezza pagina: cattura definizioni a cavallo del taglio).
    course_document_chunk_chars: int = 24_000
    course_document_chunk_overlap_chars: int = 1_500
    # Hard cap di sicurezza per la memoria (~800-1000 pagine): oltre,
    # analisi del prefisso con summary_coverage='partial' (mai silenzioso).
    course_document_max_chars_hard: int = 2_000_000
    # Semaforo delle chiamate map DENTRO un documento (il worker resta
    # sequenziale TRA documenti).
    course_document_chunk_concurrency: int = 3
    # Tentativi per chiamata sui soli errori transient (rete, 429, 5xx).
    course_document_llm_retry_max: int = 4
    # Budget input del reduce: oltre, i mini-abstract passano da un
    # livello di digest (gruppi da reduce_group_size).
    course_document_reduce_input_max_chars: int = 80_000
    course_document_reduce_group_size: int = 20
    # Guardia anti-loop: oltre N tentativi (righe rimaste `processing`
    # per crash/eccezioni inattese, ritentate a ogni tick) → failed.
    course_document_summary_attempts_max: int = 10
    course_architecture_poll_interval_seconds: int = 4
    course_architecture_documents_context_max_chars: int = 60_000
    course_lesson_structure_poll_interval_seconds: int = 4
    course_lesson_structure_max_concurrency: int = 5
    course_lesson_structure_documents_context_max_chars: int = 30_000
    # Numero massimo di retry automatici dopo errore (transitorio o
    # validazione). Il worker re-imposta status='pending' invece di
    # 'failed' finché attempts < auto_retry_max. UX: l'utente non
    # vede mai l'errore, vede solo "in elaborazione" finché passa.
    course_lesson_structure_auto_retry_max: int = 5

    # Glossario corso (§10.1) — chiamata AI single-shot, prerequisito Fase 3.
    openai_glossary_model: str = "gpt-5.5"
    openai_glossary_max_tokens: int = 4_000
    course_glossary_documents_context_max_chars: int = 20_000

    # Fase 3 — Contenuti delle lezioni (§6).
    # Output lezione 8-15k tokens + reasoning gpt-5.5 → cap alto (32000).
    openai_lesson_content_model: str = "gpt-5.5"
    openai_lesson_content_max_tokens: int = 32_000
    # Default `high` perché il task contenuto lezione è il più complesso del
    # pipeline (markdown lungo + asset + bibliografia + JSON schema strict).
    openai_lesson_content_reasoning_effort: str = "high"
    course_lesson_content_poll_interval_seconds: int = 4
    # Cap=3: output 5x più grande di Fase 2, evita rate-limit OpenAI.
    course_lesson_content_max_concurrency: int = 3
    # Contesto documenti P3: budget totale (char) del blocco selezionato per
    # lezione. 40k ≈ 11,5k token: 3-4 documenti rilevanti con estratti interi
    # (con 20k, cinque documenti erano tagliati a 4k l'uno).
    course_lesson_content_documents_context_max_chars: int = 40_000
    # Tetto per singolo documento rilevante; con un solo documento
    # rilevante vale l'intero budget residuo.
    course_lesson_content_documents_per_doc_max_chars: int = 12_000
    # Kill-switch del grounding P3: False = comportamento storico
    # (_build_documents_context senza selezione per lezione, esempi e
    # formule non serializzati, blocco RIFERIMENTI invece di FONTI E
    # ANCORAGGIO, vecchio ordine dei blocchi nello user prompt).
    course_lesson_content_documents_selection_enabled: bool = True
    # Auto-retry trasparente per l'utente. Vedi
    # `course_lesson_structure_auto_retry_max`.
    course_lesson_content_auto_retry_max: int = 5

    # Fase 4 — Slide della lezione (§7).
    # Output 4-8k tokens + reasoning. Cap=16000 per non troncare.
    openai_lesson_slides_model: str = "gpt-5.5"
    openai_lesson_slides_max_tokens: int = 16_000
    openai_lesson_slides_reasoning_effort: str = "medium"
    course_lesson_slides_poll_interval_seconds: int = 4
    # Cap=3 come content: input ~8-18k, output ~4-8k, niente bottleneck.
    course_lesson_slides_max_concurrency: int = 3
    # Auto-retry trasparente per l'utente. Vedi
    # `course_lesson_structure_auto_retry_max`.
    course_lesson_slides_auto_retry_max: int = 5

    # Fase 5 — Discorso temporizzato (§8).
    # Output prosa pura, ~6-12k tokens. Cap=16000 per coprire lezioni
    # lunghe (90 min ≈ 11700 parole IT, ~16k token con reasoning).
    openai_lesson_speech_model: str = "gpt-5.5"
    openai_lesson_speech_max_tokens: int = 16_000
    openai_lesson_speech_reasoning_effort: str = "medium"
    course_lesson_speech_poll_interval_seconds: int = 4
    # Cap=3 come slides/content: input ~12-25k (testo + slide),
    # output ~6-12k, niente bottleneck di rate-limit.
    course_lesson_speech_max_concurrency: int = 3
    # Auto-retry trasparente per l'utente. Vedi
    # `course_lesson_structure_auto_retry_max`.
    course_lesson_speech_auto_retry_max: int = 5

    # Image → Mermaid: chiamata Vision API on-demand quando l'utente
    # carica un'immagine nell'editor lezione e clicca "Digitalizza".
    # Modello deve supportare input multimodale (image_url). gpt-4o è
    # vision-capable e non-reasoning (effort = None default).
    openai_image_to_mermaid_model: str = "gpt-4o"
    openai_image_to_mermaid_reasoning_effort: str | None = None
    openai_image_to_mermaid_max_tokens: int = 4_000

    # Auto-fix degli asset "fragili" (formule LaTeX + diagrammi Mermaid)
    # a generazione AI (Fase 3 + Fase 4). Quando un asset generato non
    # supera la validazione (latex2mathml + KaTeX per le formule, Mermaid
    # 11.x — pin `mermaid_cdn_version` — per i diagrammi), viene riparato
    # con una chiamata AI mirata e ri-validato, finché valido o esaurito
    # `asset_fix_max_attempts`.
    # gpt-4o-mini basta per il fix sintattico mirato; l'escalation (re-gen
    # intera lezione con gpt-5.5) copre i casi residui via auto-retry.
    openai_asset_fix_model: str = "gpt-4o-mini"
    openai_asset_fix_reasoning_effort: str | None = None
    openai_asset_fix_max_tokens: int = 4_000
    # Tentativi di fix AI per singolo asset prima di alzare un errore
    # recuperabile (che fa rigenerare l'intera lezione via auto-retry).
    asset_fix_max_attempts: int = 3

    # Localizzazione asset (rete di sicurezza i18n, Fase 3 + Fase 4). Quando il
    # modello lascia un campo testuale di un asset (caption, alt_text, enunciato/
    # dimostrazione equazioni, esempi, label Mermaid, celle tabelle) in una lingua
    # diversa da quella del corso, viene ritradotto. Il rilevamento (via
    # `app/core/i18n_scripts`) si attiva SOLO per lingue con script non-latino
    # (cjk, cirillico, arabo, ecc.): per le lingue latine il gate è spento e non
    # si spende alcun token. Vedi `asset_validation_service`.
    openai_asset_localize_model: str = "gpt-4o-mini"
    openai_asset_localize_max_tokens: int = 8_000
    asset_localize_enabled: bool = True

    # Revisore AI figura ↔ testo (Fase 3, D15). Dopo il fix, ogni figura
    # valida è confrontata con il testo integrale della sezione che la cita e
    # con la sua misura (nodi, archi, incroci, difetti, corpo del testo): il
    # verdetto predefinito è `coerente` (nessuna riscrittura); `correggi`
    # porta un sorgente nuovo, accettato solo se supera la validazione e non
    # peggiora la misura, altrimenti resta l'originale byte-identico.
    # gpt-4o-mini come il fix e la localizzazione: circa 0,0006 USD a figura
    # con 3.000 token in ingresso e 300 in uscita (listino di
    # `openai_pricing`); il costo entra in `content_tokens.assets`. Un
    # rifiuto non fa rigenerare la lezione: `figure_review_max_attempts`
    # limita le chiamate per figura (0 = nessuna), `figure_review_enabled=
    # False` spegne la fase senza alcuna chiamata HTTP né resa.
    # `figure_review_max_parallel`: chiamate del revisore in volo per
    # processo (un giro ne lancia una per figura e le lezioni corrono in
    # parallelo: senza tetto sarebbero figure × lezioni).
    openai_figure_review_model: str = "gpt-4o-mini"
    openai_figure_review_reasoning_effort: str | None = None
    openai_figure_review_max_tokens: int = 4_000
    figure_review_max_attempts: int = 2
    figure_review_max_parallel: int = 4
    figure_review_enabled: bool = True

    # --- Figure accademiche (Fase 3/4) ---
    # Quattro famiglie di asset visivi renderizzati dal registro
    # `figure_render_service`: Mermaid (sempre attivo), Vega-Lite
    # (vl-convert), Graphviz DOT (binario `dot`), `function` (sympy +
    # matplotlib). Kill-switch per formato: `False` toglie il formato dallo
    # schema strict offerto al modello e dal validatore (i contenuti già in
    # DB con quel formato ricadono sul fallback `<pre>` a render). Un
    # formato è offerto solo se abilitato E la dipendenza è presente
    # (`available_formats()`).
    figure_vegalite_enabled: bool = True
    figure_dot_enabled: bool = True
    figure_function_enabled: bool = True
    # Unico pin di Mermaid per validatore (Playwright) e pre-render
    # PDF/video: con `htmlLabels:false` top-level la 11.x emette `<text>`
    # puro (0 foreignObject) per i tipi D8. Il frontend segue con il lock npm.
    mermaid_cdn_version: str = "11.17.2"
    # Pin di MathJax (tex-svg) per il pre-render delle formule del PDF
    # dispensa, del PDF slide e dei frame video (`course_lesson_pdf_service.
    # build_mathjax_renderer_html`); la pagina headless può contattare
    # solo il CDN.
    mathjax_cdn_version: str = "3.2.2"
    # Tetto per il render di un batch di figure di una lezione (thread +
    # `asyncio.wait_for`): oltre, le figure mancanti degradano a fallback e
    # l'export prosegue.
    figure_render_timeout_seconds: int = 20
    # Tetto del calcolo simbolico di `function` (processo figlio `spawn`,
    # ucciso allo scadere): oltre, il risultato numerico resta e la
    # didascalia riporta «valori approssimati».
    figure_function_timeout_seconds: int = 10
    # Render CPU-bound concorrenti (worker + anteprime `render-function`).
    # Default 2 per la VM a 2 core.
    figure_render_max_workers: int = 2
    # Cache LRU in memoria degli SVG renderizzati (chiave: formato, hash del
    # contenuto, versione del tema, lingua).
    figure_svg_cache_size: int = 256
    # Oltre questa dimensione un SVG prodotto viene rifiutato (fallback).
    figure_svg_max_bytes: int = 1_500_000
    # Limite del sorgente DOT accettato dal validatore.
    figure_dot_max_chars: int = 12_000
    # Percorso del binario `dot`; None = ricerca nel PATH (`shutil.which`).
    graphviz_dot_path: str | None = None

    # --- Figure da letteratura (figure di fonte) ---
    # Figure estratte dai documenti del corso (e, dopo il cancello, dalla
    # letteratura aperta) citate in lezione come asset `source_figure`, con
    # la riga di attribuzione calcolata a render. Vedi
    # docs/courses/18-literature-figures.md.
    # Kill-switch del catalogo nel prompt di Fase 3: con False messaggio
    # user e schema strict sono identici a prima della feature.
    figure_source_enabled: bool = True
    # Politica di licenza: `cite_all` riproduce figure di qualunque licenza
    # purché con attribuzione completa; `open_only` solo CC0, CC BY, CC BY-SA,
    # pubblico dominio e documenti dichiarati propri. Override per
    # organizzazione in `organization_course_settings` (NULL = questo valore).
    figure_source_license_policy: Literal["cite_all", "open_only"] = "cite_all"
    # Catalogo per lezione nel messaggio user di Fase 3 (voci e caratteri).
    figure_source_catalog_max_items: int = 8
    figure_source_catalog_max_chars: int = 4_000
    # Budget (b) delle figure di fonte, separato da quello delle generate.
    figure_source_max_per_lesson: int = 4
    figure_source_max_per_intro_lesson: int = 1
    # Sotto questo numero di figure di fonte pertinenti una lezione è «in
    # buco» e (dopo il cancello, WP5) si integra dalla letteratura aperta.
    figure_source_min_per_lesson: int = 1
    # Riuso limitato: una figura di fonte compare in al più tante lezioni del
    # corso (mai due volte nella stessa). 1 = una sola lezione per le figure
    # nuove, come in 9bb7c31; le collocazioni già esistenti restano alla
    # rigenerazione della lezione (U1).
    figure_source_max_lessons_per_figure: int = 2
    # Risoluzione effettiva (doc 18 §22): classe good/acceptable/low/unusable
    # alla larghezza di riferimento, regola di stampa unica per dispensa,
    # slide e frame, `unusable` fuori da catalogo e selettore. false = regola
    # di stampa v1 (solo dispensa) e nessun filtro di classe.
    figure_resolution_rules_enabled: bool = True
    # Ritaglio v2 delle figure estratte (doc 18 §22): render sulla griglia
    # dei pixel nativi, PNG per il tratto, geometria di DOCX e PPTX. false =
    # ritaglio storico (150-300 dpi, JPEG per i raster), crop_version 1.
    figure_extraction_native_crop_enabled: bool = True
    # Attesa massima della Fase 3 per le estrazioni in corso dei documenti
    # del corso (filtro nel `_tick`, mai uno sleep).
    figure_wait_max_minutes: int = 15

    # Estrazione delle figure dai documenti (worker gemello del riassunto).
    # In produzione resta spenta finché la misura M0 sulla VM non conferma
    # che torch/Docling girano (vedi docs/07-deployment.md).
    figure_extraction_enabled: bool = True
    # `docling` (default del brief) oppure `heuristic` (pdfplumber +
    # pypdfium2, senza torch: qualità minore, solo per decisione esplicita).
    figure_extraction_engine: Literal["docling", "heuristic"] = "docling"
    # Thread del processo figlio (OMP/MKL/OPENBLAS e Docling).
    figure_extraction_threads: int = 1
    # Pagine per blocco di conversione e per processo figlio (riciclo della
    # memoria), tetto di pagine per documento (oltre: copertura parziale) e
    # tempo massimo di un giro del worker su un documento. 0 = nessun tetto:
    # copertura totale; resta il tempo massimo di ogni blocco.
    figure_extraction_block_pages: int = 10
    figure_extraction_pages_per_child: int = 40
    figure_extraction_max_pages: int = 0
    figure_extraction_total_timeout_seconds: int = 0
    figure_extraction_probe_timeout_seconds: int = 180
    # Watchdog di memoria del figlio e memoria minima disponibile prima di
    # ogni blocco (sotto: rinvio senza consumare tentativi).
    figure_extraction_max_rss_mb: int = 2_048
    figure_extraction_min_available_mb: int = 1_800
    figure_extraction_max_defer_minutes: int = 180
    # Errori recuperabili → pending con backoff fino a questo tetto, poi
    # failed; guardia anti-loop sui `processing` ripresi dopo un crash.
    figure_extraction_auto_retry_max: int = 3
    figure_extraction_attempts_max: int = 6
    figure_extraction_poll_interval_seconds: int = 5
    # Modelli Docling preinstallati nell'immagine (niente download a runtime).
    figure_docling_artifacts_path: str = "/opt/docling-models"
    # Idoneità didattica calcolata in lettura (una soglia cambiata agisce
    # senza rielaborare) e tetto delle figure descritte per documento
    # (0 = tutte).
    figure_min_quality_score: int = 3
    figure_describe_max_per_document: int = 0

    # Descrizione Vision delle figure candidate (PROMPT 18). Default
    # provvisorio fino alla misura M4; il modello deve stare a listino
    # (`openai_pricing.MODEL_PRICING`), altrimenti il costo non si vede.
    openai_figure_describe_model: str = "gpt-4.1-mini"
    openai_figure_describe_reasoning_effort: str | None = None
    openai_figure_describe_max_tokens: int = 800
    # `detail` sempre esplicito: con alcuni modelli un detail omesso vale
    # «original» e fattura l'immagine a piena risoluzione.
    openai_figure_describe_detail: Literal["low", "high"] = "high"
    openai_figure_describe_timeout_seconds: int = 60
    openai_figure_describe_concurrency: int = 3

    # Revisore delle figure di fonte (coerenza e ridondanza, PROMPT 19):
    # segnala soltanto, non modifica mai `content_raw`; errore = nessun avviso.
    figure_redundancy_enabled: bool = True
    figure_redundancy_max_attempts: int = 2
    figure_redundancy_timeout_seconds: int = 120
    openai_figure_redundancy_model: str = "gpt-4o-mini"
    openai_figure_redundancy_reasoning_effort: str | None = None
    openai_figure_redundancy_max_tokens: int = 1_500
    # PROMPT 19 v2 (doc 18 §23.7): per una figura legata a un fabbisogno del
    # piano chiede anche se mostra la figura richiesta (`subject_match`).
    figure_redundancy_subject_check_enabled: bool = True

    # Fase 4: inserisce in modo deterministico la slide dedicata mancante
    # per ogni figura di Fase 3 (generate e di fonte). False = output di
    # Fase 4 identico a prima della feature.
    figure_slides_coverage_repair_enabled: bool = True

    # Letteratura aperta (WP5): una lezione con meno di
    # `figure_source_min_per_lesson` figure di fonte pertinenti dai documenti
    # riceve, prima della Fase 3, figure da Wikimedia Commons e (solo con
    # `openalex_api_key`) dai PDF open access di OpenAlex, fino al budget
    # (b). Solo ritagli nel catalogo del corso, mai documenti: riassunti,
    # testo e riferimenti delle lezioni non cambiano. In produzione resta
    # spenta finché non la si accende (rete esterna, costo Vision).
    figure_literature_enabled: bool = True
    # Candidate valutate dalla Vision per lezione (PROMPT 20) e tetto delle
    # figure della letteratura aperta per corso.
    figure_literature_max_candidates_per_lesson: int = 15
    # Piano delle figure (doc 18 §23.5): candidate per fabbisogno scoperto
    # (1 per gli should), tetto in dollari per verifica (Vision e copie
    # OpenAlex) e PDF a pagamento (copie OpenAlex) per lezione.
    figure_literature_max_candidates_per_need: int = 3
    figure_literature_max_cost_usd_per_check: float = 0.08
    figure_literature_max_paid_pdf_per_lesson: int = 3
    figure_literature_max_per_course: int = 40
    # Tempo massimo del lavoro di una lezione (ricerca, download, Vision).
    figure_literature_timeout_seconds: int = 300
    # Download: byte massimi di un'immagine e di un PDF, pixel decodificati
    # massimi, pagine massime di un PDF OpenAlex.
    figure_literature_max_image_mb: int = 20
    figure_literature_max_pdf_mb: int = 30
    figure_literature_max_image_pixels: int = 16_000_000
    figure_literature_max_pdf_pages: int = 40
    # Larghezza del PNG chiesto a Wikimedia (anche per gli SVG, resi da
    # Commons). 1920 è un passo standard dei rendering di Commons: con 2000
    # gli SVG arrivavano a 3840 px (sonda del 25/09/2026), e 1920 px bastano
    # per qualunque larghezza di stampa.
    figure_literature_image_width: int = 1_920
    # Errori recuperabili (rete, 429) → pending fino a questo tetto.
    figure_literature_auto_retry_max: int = 2
    figure_literature_poll_interval_seconds: int = 5
    wikimedia_api_url: str = "https://commons.wikimedia.org/w/api.php"
    # OpenAlex chiede una API key dal 13/02/2026: senza, la ricerca delle
    # figure salta OpenAlex (Wikimedia resta).
    openalex_api_key: str | None = None
    # Termini di ricerca e pertinenza delle candidate (PROMPT 20); il modello
    # deve stare a listino (`openai_pricing.MODEL_PRICING`).
    openai_figure_relevance_model: str = "gpt-4.1-mini"
    openai_figure_relevance_reasoning_effort: str | None = None
    openai_figure_relevance_max_tokens: int = 800
    openai_figure_relevance_timeout_seconds: int = 60

    # Piano delle figure di fonte (doc 18 §23): fabbisogni per lezione
    # (PROMPT 22), calcolati quando si chiede la Fase 3 e validi finché la
    # struttura della lezione non cambia. Il modello deve stare a listino.
    figure_plan_enabled: bool = True
    openai_figure_needs_model: str = "gpt-5.5"
    openai_figure_needs_reasoning_effort: str | None = "none"
    openai_figure_needs_max_tokens: int = 4500
    openai_figure_needs_timeout_seconds: int = 90
    figure_needs_concurrency: int = 4
    figure_needs_auto_retry_max: int = 3
    figure_needs_poll_interval_seconds: int = 5
    # Fabbisogni per lezione (al più 8 must) e nella lezione introduttiva.
    figure_needs_max_per_lesson: int = 10
    figure_needs_max_per_intro_lesson: int = 3
    # Budget (b) del piano: figure di fonte per lezione = min(tetto, max(
    # morbido, must pronti)), morbido = minuti della lezione // minuti per
    # figura, fra il budget senza piano e il tetto (doc 18 §23.4).
    figure_plan_max_per_lesson: int = 8
    figure_source_minutes_per_figure: int = 4
    # Abbinamento dal solo testo per le figure senza `depicts` (descritte
    # prima della 0041): spento, precisione 0,53 nella misura M-A3.
    figure_plan_legacy_match_enabled: bool = False
    # Blocco del piano nel messaggio user del PROMPT 3 (doc 18 §23.6). Spento:
    # nessuna offerta, catalogo lessicale come prima; i fabbisogni servono
    # solo a editor e buchi.
    figure_plan_in_prompt_enabled: bool = True

    # Formato `tikz` (WP6): figure vettoriali (schemi di strumenti, circuiti
    # IEC, catene di misura) compilate con XeLaTeX nella sandbox del
    # container (SBX-2: limiti di processo, ambiente senza segreti, TeX
    # paranoico, autotest all'avvio). Spento finché non lo si accende: TeX
    # Live entra nell'immagine solo con l'argomento di build INSTALL_TEX
    # (misura M5: +550 MB circa). `figure_tikz_propose_enabled` decide se il
    # modello di Fase 3 può proporlo da sé (altrimenti solo editor).
    figure_tikz_enabled: bool = False
    figure_tikz_propose_enabled: bool = False
    figure_tikz_max_chars: int = 8_000
    figure_tikz_timeout_seconds: int = 10
    figure_tikz_queue_timeout_seconds: int = 30
    figure_tikz_fix_max_attempts: int = 1
    figure_tikz_render_review_enabled: bool = True
    openai_tikz_review_model: str = "gpt-4.1-mini"
    openai_tikz_review_max_tokens: int = 1_500
    figure_tikz_preview_per_minute: int = 10
    # Cartella dei binari TeX (xelatex, kpsewhich, pdftocairo); vuota = PATH.
    tex_bin_dir: str | None = None

    # §7 — Export PDF lezioni.
    # Cap=2: rendering Playwright è I/O+CPU intensive (Chromium istanza).
    course_lesson_pdf_poll_interval_seconds: int = 4
    course_lesson_pdf_max_concurrency: int = 2
    # Auto-retry trasparente per l'utente. Vedi
    # `course_lesson_structure_auto_retry_max`.
    course_lesson_pdf_auto_retry_max: int = 5
    # Directory di output per i PDF generati. Path relativo alla root
    # del backend o assoluto. Il file system è la persistence layer:
    # niente object storage in MVP.
    generated_pdfs_dir: str = "generated_pdfs"

    # §9 — Generazione video MP4 (TTS XTTS-v2 su RunPod + slide + ffmpeg).
    # Pre-condizione runtime: speech_status='approved' AND
    # slides_status='approved' AND course.assignee.avatar.audio_path
    # esiste su filesystem AND servizio TTS RunPod configurato.
    #
    # TTS XTTS-v2 su RunPod Serverless GPU (vedi cartella `XTTS/`): il
    # backend invia un job per video e consuma i segment in streaming.
    runpod_api_key: str | None = None
    runpod_tts_endpoint_id: str | None = None
    runpod_base_url: str = "https://api.runpod.ai"
    # Timeout wall-clock totale di un job TTS (assorbe il cold start GPU).
    runpod_tts_timeout_seconds: int = 1800
    runpod_tts_poll_interval_seconds: int = 3

    # Worker video: orchestrazione (TTS remoto + slide + ffmpeg). Default 1.
    course_lesson_video_poll_interval_seconds: int = 4
    course_lesson_video_max_concurrency: int = 1
    course_lesson_video_auto_retry_max: int = 3

    # Encoding ffmpeg (1080p @ 30fps H.264 + AAC).
    video_resolution: str = "1920x1080"
    video_framerate: int = 30
    video_audio_bitrate: str = "192k"
    video_audio_sample_rate: int = 48000
    video_video_codec: str = "libx264"
    video_crf: int = 23  # quality 1080p tipico YouTube
    # libx264 preset: trade-off velocità/compressione. Per slide statiche
    # (`-tune stillimage`) `veryfast` produce file identico a `medium` in
    # qualità percepita ma è 3-5× più veloce. Su CPU senza AVX (QEMU VM)
    # questo taglia ~70% del tempo di encoding. Valori validi:
    # ultrafast, superfast, veryfast, faster, fast, medium, slow, slower,
    # veryslow. Override via env `VIDEO_PRESET`.
    video_preset: str = "veryfast"
    video_pixel_format: str = "yuv420p"  # compat HTML5/Quicktime
    lesson_video_max_mb: int = 500  # safety upper bound
    ffmpeg_binary: str = "ffmpeg"

    # §9b — "Video con Avatar" (lip-sync MuseTalk su RunPod).
    # Il client MuseTalk vendored (`app/musetalk_client/`) gira come
    # subprocess isolato: genera un video di avatar parlante e il worker
    # `course_lesson_avatar_video_worker` lo sovrappone in basso a destra
    # al video MP4 già generato della lezione. Pre-condizione runtime:
    # `video_status='ready'` AND l'avatar dell'assegnatario ha clip pronte.
    #
    # RunPod: stesso account del TTS (`runpod_api_key` riusato), endpoint
    # serverless dedicato a MuseTalk. R2 (Cloudflare, S3-compatible) è lo
    # storage di transito per video/audio/output del job. Queste credenziali
    # vengono passate al subprocess come variabili d'ambiente.
    runpod_musetalk_endpoint_id: str | None = None
    r2_endpoint: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None

    # Worker video con avatar: orchestrazione subprocess MuseTalk +
    # overlay ffmpeg. Cap=1 (un job GPU per volta, costoso).
    course_lesson_avatar_video_poll_interval_seconds: int = 4
    course_lesson_avatar_video_max_concurrency: int = 1
    course_lesson_avatar_video_auto_retry_max: int = 3
    # Timeout wall-clock del subprocess MuseTalk (preprocess + lipsync +
    # download). Generoso: assorbe cold start GPU + audio molto lunghi.
    course_lesson_avatar_video_timeout_seconds: int = 10800

    # Overlay dell'avatar sul video della lezione. Quadrato (le clip
    # MiniMax sono 1:1), ancorato in basso a destra.
    #   scale  = lato del quadrato come frazione della larghezza del video
    #   margin = distanza dai bordi destro/inferiore, in pixel
    avatar_video_overlay_scale: float = 0.24
    avatar_video_overlay_margin: int = 24
    # Risoluzione (lato del quadrato) a cui a4u ridimensiona le clip
    # dell'avatar prima di passarle a MuseTalk. Le clip MiniMax sono
    # 1080×1080: a quella risoluzione il lip-sync su RunPod sfora il tetto
    # di 60 min (blending + encode + RAM scalano con l'area del frame).
    # 640 riporta i tempi nella norma senza perdita visibile — nel video
    # finale l'avatar è ~475px. Vedi
    # `course_lesson_avatar_video_worker._prepare_musetalk_clips`.
    avatar_video_clip_resolution: int = 640

    # Worker duplicazione corso in altra lingua. Job lungo (5-15 min per
    # corso medio): cap globale=1 per evitare conflitti di rate-limit
    # OpenAI tra job. Dentro al job, le lezioni vengono tradotte in
    # parallelo cap=3 per fase (mirror del content worker).
    course_duplication_poll_interval_seconds: int = 4
    course_duplication_max_concurrent_jobs: int = 1
    # Cap di lezioni tradotte in parallelo (per phase). Con la phase
    # combined (content+slides+speech in parallelo dentro la stessa
    # lezione) ogni lezione consuma 3 task local + N chiamate OpenAI
    # (chunk parallelizzati dentro ogni phase). Cap 20 → 60 task local
    # → fino a ~60-120 chiamate OpenAI concorrenti, capped dal
    # `openai_translate_global_concurrency` (80). Pool DB 80 connessioni
    # supporta 60 sessioni concorrenti con margine.
    course_duplication_lesson_translate_concurrency: int = 20
    course_duplication_auto_retry_max: int = 5
    # Timeout massimo (in minuti) per un job di duplicazione completo.
    # Oltre questo limite il job viene marcato `failed` e il target
    # course viene eliminato automaticamente. 90 min copre con margine
    # un corso da 100 lezioni con il retry esponenziale attivo
    # (un corso da 80 lezioni in condizioni normali finisce in ~30 min).
    course_duplication_job_timeout_minutes: int = 90

    # Nova — assistente AI contestuale floating widget. Stateless DB-side.
    # Modello veloce ed economico (chat conversazionale, no JSON schema).
    openai_nova_model: str = "gpt-4o-mini"
    openai_nova_max_tokens: int = 512
    openai_nova_temperature: float = 0.7
    # Cap di messaggi di history inviati dal FE al BE per ogni chat.
    # Mantiene il filo del discorso senza esplodere i token.
    nova_history_cap: int = 10
    # Rate limit per utente (slowapi). 30/min = ~1 msg ogni 2s, generoso
    # per una conversazione fluida ma stoppa abuse.
    nova_rate_limit_per_minute: int = 30

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_full_name: str = "Platform Admin"

    rate_limit_login_per_min: int = 5
    login_lockout_threshold: int = 10
    login_lockout_minutes: int = 15

    sentry_dsn: str | None = None

    @field_validator("cookie_domain", mode="before")
    @classmethod
    def _empty_string_to_none(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value

    @field_validator(
        "sentry_dsn",
        "bootstrap_admin_email",
        "bootstrap_admin_password",
        "minimax_api_key",
        "openai_api_key",
        "openalex_api_key",
        "runpod_api_key",
        "runpod_tts_endpoint_id",
        "runpod_musetalk_endpoint_id",
        "r2_endpoint",
        "r2_bucket",
        "r2_access_key_id",
        "r2_secret_access_key",
        mode="before",
    )
    @classmethod
    def _none_if_empty(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value

    @property
    def upload_root(self) -> Path:
        return Path(self.upload_dir).resolve()

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_allow_origins(self) -> list[str]:
        return [self.frontend_origin]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor; per `lifespan` ricaricare manualmente è raro."""
    return Settings()  # type: ignore[call-arg]
