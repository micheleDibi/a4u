# 18 — Figure da letteratura: fonti del corso, letteratura aperta e schemi TikZ

Branch `feat/literature-figures` (23-24 settembre 2026). Le dispense possono
contenere **figure tratte dalla letteratura**, per esempio lo schema di un
vibrometro laser Doppler preso da un articolo del corso. Le figure sono scelte
in automatico, riprodotte ad alta qualità e numerate «Figura N.» come le
altre. Hanno sempre una riga «Fonte», calcolata a render dai metadati e mai
scritta dal modello.

Le fonti, in ordine di priorità:

1. i **documenti del corso** (PDF, DOCX, PPTX caricati), con l'estrazione in
   un worker dedicato (WP1-WP4);
2. la **letteratura aperta** (Wikimedia Commons, OpenAlex), solo quando una
   lezione ha meno di `FIGURE_SOURCE_MIN_PER_LESSON` figure pertinenti dai
   documenti (WP5);
3. il **ridisegno vettoriale** nel formato `tikz`, compilato da XeLaTeX in
   una sandbox (WP6).

Le figure di fonte **si aggiungono** a quelle generate, con due budget
separati:

- (a) le figure generate (Mermaid, Vega-Lite, DOT, `function` e `tikz`):
  4-8 per lezione ordinaria, 0-2 per l'introduttiva, come prima;
- (b) le figure di fonte: 1-4, e solo se pertinenti.

Piano approvato:
`~/.claude/plans/quirky-snuggling-newell.md`. Configurazione in
[04 — Configurazione](../04-configuration.md). Prompt in
[PROMPTS.md](../PROMPTS.md): PROMPT 3, 5, 6 e 12 modificati, 18-21 nuovi.

## 1. Stato e accensione

| Parte | Codice | Default nel codice | Default in `.env.example` / compose |
|---|---|---|---|
| Catalogo delle figure di fonte in Fase 3 | WP1-WP4 | `FIGURE_SOURCE_ENABLED=true` | `true` |
| Estrazione dai documenti (Docling) | WP2 | `FIGURE_EXTRACTION_ENABLED=true` | **`false`**: si accende dopo la misura M0 sulla VM |
| Revisore delle ridondanze (PROMPT 19) | WP3 | `true` | `true` |
| Letteratura aperta (buchi) | WP5 | `FIGURE_LITERATURE_ENABLED=true` | **`false`** |
| Formato `tikz` (editor, resa) | WP6 | `FIGURE_TIKZ_ENABLED=false` | `false`; serve l'immagine con TeX (`--build-arg INSTALL_TEX=true`) |
| Proposta di `tikz` da parte del modello | WP6.3 | `FIGURE_TIKZ_PROPOSE_ENABLED=false` | `false` |

Con tutto spento il comportamento è quello di `main`. Il catalogo resta
vuoto, e allora PROMPT 3 (system, messaggio user e schema) è
**byte-identico** a prima (test I1). Ogni parte ha un kill-switch.

## 2. Architettura in breve

```
documento caricato ──► course_document_worker (testo + riassunto, invariato)
        │
        └─► course_document_figures_worker ──► processo figlio (Docling o euristico)
                 │  ritagli PNG/JPEG sotto /uploads/courses/{cid}/document_figures/…
                 │  Vision descrittiva (PROMPT 18), costo in vision_usage
                 ▼
        course_document_figure (catalogo del corso, licenza e provenienza sempre registrate)
                 │
   lezione in Fase 3 ──► source_figure_catalog.build_catalog ──► lesson_figure_selection
                 │        (top 8 nel messaggio user, id SRC-<hex8>)
                 │  buco (< MIN pertinenti) ──► course_lesson_figures_gap_worker ──► Wikimedia / OpenAlex
                 ▼                                   (PROMPT 20, prima della Fase 3)
        PROMPT 3 ──► source_figures[] ──► fusione in visual_assets (format="source_figure",
                 content = UUID) ──► validazione/fix ──► revisore PROMPT 19 (solo avvisi)
                 ▼
        resa: resolver lato server (source_figure_service) ──► dispensa, PDF di modulo,
        PDF slide, frame video, vista ed editor ── sempre con la riga «Fonte»
```

- **Riferimento vivo** (J-Q1). L'asset `source_figure` porta l'UUID della
  riga `course_document_figure`. Byte e riga «Fonte» li dà solo il resolver
  del server (`resolve_source_figures`), che filtra per `course_id`.
  L'attribuzione non viene mai dal client né dal modello.
- **Predicato unico** `source_figure_policy.figure_visibility(…, mode)`:
  - `select`: catalogo, PATCH di asset nuovi o cambiati, ricontrollo
    TOCTOU in generazione;
  - `render`: figure già collocate, solo condizioni strutturali (U1).
- **Numerazione**. Le figure di fonte stanno in `content_raw.visual_assets`,
  quindi numerazione, rimandi `[FIG:id]`, slide e localizzazione funzionano
  come per le altre figure.

## 3. Modello dati (migrazioni 0037 e 0038)

- **`course_document`, provenienza**:
  - `origin` (`upload | paper_import | paper_metadata`), `is_own_work`;
  - `license` e `license_source`;
  - `bibliography` (JSONB `DocumentBibliography`) e `bibliography_source`
    (`user | openalex | pdf_metadata | crossref | summary_proposal`).
- **`course_document`, estrazione**: `figures_status`
  (`pending | processing | ready | failed | skipped`; NULL = mai chiesta,
  **nessun backfill**), `figures_error_code` (16 codici), avanzamento
  (`figures_pages_done/_total`), `figures_coverage` (`full | partial`),
  statistiche.
- **`course_document_figure`**:
  - `course_id`, e `document_id` con FK `ON DELETE SET NULL` più
    `detached_at`;
  - `source_kind` (`uploaded | openalex | wikimedia`), `locator`, pagina,
    bbox, didascalia e contesto;
  - file (`storage_path`, anteprima, dpi, `phash`) e dati Vision;
  - `license` **NOT NULL senza default**, `license_url`, `attribution`
    congelata per le staccate e le esterne;
  - `excluded_by_user`, `status` e `reject_reason`, `duplicate_of_id`;
  - con la 0038: `external_id`, `source_url`, `retrieved_at` e un indice
    unico parziale sulle figure esterne.
- **`course_lesson`**:
  - `content_figure_review`, i verdetti del revisore: li crea solo la
    materializzazione, il CRUD li pota soltanto;
  - con la 0038, lo stato dei buchi: `figures_gap_status/attempts/…/usage/stats`.
- **`organization_course_settings`**: `figure_source_license_policy`,
  override per organizzazione.

CHECK e indici sono dichiarati anche nei modelli, perché i test creano lo
schema con `create_all`. La parità modello↔migrazione è verificata da
`test_source_figure_model`.

## 4. Estrazione dai documenti (WP2)

- **Worker gemello** `course_document_figures_worker` (J-Q3):
  - lavora un documento alla volta, con UPDATE condizionali, backoff e
    checkpoint per blocchi di pagine;
  - gli errori recuperabili tornano a `pending`, fino a
    `FIGURE_EXTRACTION_AUTO_RETRY_MAX`;
  - l'estrazione non tocca il riassunto (G5).
- **Processo figlio** (`document_figures/child.py`, `runner.py`):
  - ambiente in allowlist, senza segreti; `start_new_session` e killpg;
  - watchdog di RSS e di tempo; riciclo ogni `…_PAGES_PER_CHILD` pagine;
  - `MemAvailable` controllato prima di ogni blocco;
  - `heavy_job_lock` condiviso con la compilazione TeX;
  - il figlio non tocca mai lo storage: il padre scarica e carica.
- **Motori**:
  - `docling`, il default: solo layout (heron), OCR spento; il ritaglio
    lo fa pypdfium2 sul bbox;
  - `heuristic`: pdfplumber + pypdfium2, senza torch, di qualità minore;
    pensato per CI e per una decisione esplicita;
  - DOCX e PPTX si leggono con zipfile e XML.
- **Ritaglio**:
  - vettoriali a dpi adattivo fra 300 e 600, lato lungo verso 2400 px,
    tetto 12 MP (D21, deviazione dichiarata dal 200-300 dpi del brief);
  - raster al ppi nativo, fra 150 e 300.
- **Filtri**: dimensioni, proporzioni, vuoti, testate e piè di pagina,
  rumore del rilevatore, figure ripetute, duplicati (`phash`).
- **Vision descrittiva** (PROMPT 18, `gpt-4.1-mini` a 768 px, M4):
  produce tipo, descrizione, parole chiave (lingua del corso + inglese),
  qualità, leggibilità e utilità. Il costo cumulativo sta in
  `vision_usage` e compare nella dashboard admin, fase `document_figures`.
- **Provenienza**, da fonti deterministiche, in quest'ordine:
  1. il docente;
  2. OpenAlex;
  3. i metadati del PDF (Info/XMP) o `core.xml` di DOCX/PPTX;
  4. Crossref (DOI trovato nelle prime pagine).

  Il riassunto dell'LLM è solo una **proposta** (`summary_proposal`),
  mostrata in UI da confermare, e non entra mai nella riga.
- **Documenti esclusi**. Da `excluded` e `content_only` non si estrae
  nulla (`skipped` con il motivo).
- **Richiesta dell'estrazione**. Endpoint `…/figures/extract` e script
  `scripts/extract_document_figures.py` (dry-run di default; opzioni
  `--apply`, `--retry-failed` e `--gc-detached`). Non c'è backfill automatico (G10).

## 5. Attribuzione e licenze (WP1)

- **Punto unico**: `figure_attribution.attribution_line(src, language,
  mode="written"|"spoken", adapted=False)`.
  - Testi it/en, con ripiego su it.
  - «et al.» oltre 3 autori; la licenza si omette se `unknown`.
  - La variante parlata garantisce `validate_tts_safety(...) == []`.
- **Esempio** reale della prova §7.3: «Fonte: Docente di Prova, «Dispensa
  di vibrometria laser», fig. 2.1, p. 1».
- **Politica** `FIGURE_SOURCE_LICENSE_POLICY`, con override per
  organizzazione:
  - `cite_all`: qualunque licenza, sempre con attribuzione completa;
  - `open_only`: solo CC0, pubblico dominio, CC BY e CC BY-SA.
- **Nota legale**. `cite_all` presuppone che l'uso rientri nelle eccezioni
  per citazione e insegnamento (artt. 70 e 70-bis L. 633/1941). La
  valutazione legale spetta al committente e non blocca il rilascio:
  `open_only` è l'opzione prudente.
- **Crediti**. Le figure esterne CC hanno in fondo alla dispensa
  l'appendice «Crediti delle figure»: autore, titolo, licenza con
  versione e URI, fonte.

## 6. Selezione e PROMPT 3 (WP3)

- **Selezione** `lesson_figure_selection` (J-Q2):
  - lessicale, gemella di `lesson_document_selection`, sulle parole chiave
    Vision nella lingua del corso e in inglese;
  - soglie «candidata» e «pertinente» (quest'ultima conta per i buchi);
  - lezioni di verifica escluse.
- **Prompt**. Catalogo di al più 8 voci e 4000 caratteri nel **messaggio
  user**, dentro i delimitatori di dati; nel system entra solo una riga
  statica (M6).
- **Schema**. `source_figures[]`, con l'enum degli id `SRC-…`;
  `visual_assets` non cambia mai.
- **Budget (b)**. È una riga a parte, dopo la misura M7: «Prima decidi le
  figure generate come se il catalogo non ci fosse: stesso numero, stesse
  sezioni e stessi formati…».
- **Fusione** `source_figure_fusion`:
  - le scelte diventano asset `source_figure`; gli asset generati con il
    prefisso `SRC-` vengono rinominati in modo deterministico;
  - il ricontrollo TOCTOU ha un audit;
  - `source_figures` viene svuotato ed escluso dai dump.
- **Revisore** (PROMPT 19, `gpt-4o-mini`):
  - per ogni figura di fonte dà coerenza e coppie
    `distinta | complementare | ridondante`;
  - **segnala soltanto**, con un avviso sulla card nell'editor, e non
    tocca mai `content_raw`;
  - errore o timeout valgono «nessun avviso»; il costo va in
    `content_tokens.assets` (`phase="redundancy"`).
- **Attesa delle estrazioni**. Il `_tick` di Fase 3 aspetta le estrazioni
  in corso con un filtro SQL (`FIGURE_WAIT_MAX_MINUTES`), mai con uno
  sleep.

## 7. Superfici (WP4)

- **Dispensa e PDF di modulo**. La riga «Fonte» sta nel `<figcaption>`
  (partial unico; senza figure di fonte è byte-identico).
- **PDF slide e frame video** (U2). Fascia `.slide-attribution` in basso a
  sinistra (x 18-212 mm), fuori dall'avatar, alta 15 mm su 4 righe:
  - con 1-2 figure, 2 righe per figura;
  - con 3-4 figure, 1 riga per figura;
  - dalla quinta, segnaposto.

  Il testo è adattato alla larghezza (`fitted_written_line`). Una figura
  di fonte senza riga diventa segnaposto: mai l'immagine senza fonte.
- **Fase 4**:
  - la riparazione deterministica 8c aggiunge la slide mancante per ogni
    figura di Fase 3 (`FIGURE_SLIDES_COVERAGE_REPAIR_ENABLED`);
  - il JSON dato al modello passa da `figure_provenance.prompt_view`
    (niente UUID).
- **Fase 5**. La fonte si dice a voce una volta, dal blocco
  `FONTI DELLE FIGURE` (frasi neutralizzate con `safe_spoken_text`).
- **Frontend**:
  - `SourceFigure.tsx` è il punto unico: immagine come Blob
    dall'endpoint autenticato, mai l'URL dello storage;
  - `SourceFigurePicker` sceglie dal catalogo;
  - badge, polling ed estrazione nell'uploader; `DocumentSourceDialog`
    per bibliografia, licenza e «proprio»;
  - politica di organizzazione in `CourseSettingsPage`.
- **Editor**:
  - azioni Rimuovi, Escludi, Sostituisci e ↑/↓;
  - il riordino segue la prima citazione `[FIG:id]`.
- **PATCH della dispensa**:
  - asset nuovi o cambiati → 422 `source_figure_not_in_course` o
    `source_figure_not_available`, e `source_figure_format_locked`;
  - gli asset invariati sono ammessi (U1).

## 8. Ciclo di vita e duplicazione

- **Non retroattività** (U1). Cambio di politica, esclusione del docente e
  passaggio a `open_only`:
  - agiscono subito su catalogo, PATCH e ricontrollo in generazione;
  - le figure già collocate restano, con la loro riga, finché la lezione
    non viene rigenerata.

  È una **deviazione dichiarata** dal vincolo del brief («da content_only
  nessuna figura viene riprodotta»). Rischio accettato: la figura di un
  documento diventato `content_only` resta visibile, con la fonte in
  chiaro, fino alla rigenerazione.
- **Cancellazione del documento**:
  - le figure **usate** vengono staccate: riga e file restano,
    `document_id → NULL`, attribuzione congelata;
  - le non usate si cancellano;
  - la FK è `SET NULL` invece del cascade del brief (deviazione
    dichiarata).
- **Duplicazione**:
  - clona documenti (provenienza compresa), figure (anche staccate ed
    esterne) e file;
  - rimappa `content_raw` e `content_figure_review`;
  - traduce descrizione e parole chiave;
  - controprova G6: stessa riga «Fonte» prima e dopo;
  - il sorgente `tikz` si copia com'è (stesso SVG).

## 9. Letteratura aperta (WP5)

- **Buchi**. Una lezione ordinaria con meno di `FIGURE_SOURCE_MIN_PER_LESSON`
  figure pertinenti dai documenti riceve figure esterne **prima della
  Fase 3**, anche se il corso non ha documenti o ha PDF senza figure (I2).
  - Lo fa `course_lesson_figures_gap_worker`: più lezioni per tick,
    backoff, reset che conta come tentativo.
  - Il worker di Fase 3 marca il buco e attende entro
    `FIGURE_WAIT_MAX_MINUTES`.
  - Non si crea **nessun** `CourseDocument`: riassunti, selezione dei
    documenti e riferimenti della lezione restano invariati.
- **Download**, `safe_http.fetch`:
  - IP risolto e fissato (Host/SNI), redirect ricontrollati;
  - IPv6 NAT64/SIIT/site-local bloccati, IDN in punycode, DNS dentro la
    scadenza totale;
  - `Content-Encoding` rifiutato e byte grezzi letti (niente bombe
    compresse), sniffing del tipo;
  - header del chiamante tolti sui redirect fra host diversi.
- **Limiti delle immagini**, `image_limits`: pixel controllati prima della
  decodifica (16 MP) e PDF oltre il limite di pagine rifiutati.
- **Wikimedia Commons**:
  - solo licenze aperte; `Restrictions` scartate;
  - campo `Attribution` come credito, versione della licenza;
  - PNG reso da Commons: anche per gli SVG si chiede il PNG, in
    deviazione dal «preferire l'SVG» del piano.
- **OpenAlex**:
  - `OPENALEX_API_KEY` obbligatoria dal 13/02/2026: senza, i buchi usano
    solo Wikimedia;
  - solo lavori con PDF nella `best_oa_location` e licenza della
    **stessa** location fra CC BY, CC BY-SA, CC0 e pubblico dominio;
  - PDF elaborato dallo stesso processo figlio, tenendo solo i ritagli;
  - la chiave non compare nei log (httpx a WARNING).
- **Import dei paper**. `/papers/import` rilegge il lavoro lato server e
  scarica solo il PDF del server via `safe_http`: la SSRF preesistente è
  chiusa.
- **PROMPT 20**:
  - termini di ricerca in inglese, una chiamata per lezione;
  - pertinenza e descrizione Vision per candidata (al più 8);
  - costo in `course_lesson.figures_gap_usage`, fase admin `figures_gap`.

## 10. Formato `tikz` (WP6)

- **Motore** (J-Q4): XeLaTeX di TeX Live Debian, poi PDF, poi
  `pdftocairo -svg`, poi `normalize_svg`.
  - Niente dvisvgm né Ghostscript (AGPL): la guardia del Dockerfile
    rifiuta `libgs`.
  - Il preambolo è fisso: font Noto Sans/CJK scelto dal contenuto, colori
    `a4u*`, librerie ammesse, `circuitikz` europeo e `pgfplots` solo se
    usati.
- **Lexer ad allowlist** (`tikz_lexer`). Un solo ambiente esterno. Sono
  ammesse solo le control word dell'elenco; sono vietate le chiavi che
  eseguono codice, `^^`, `@` e `#`. Ci sono tetti su caratteri, graffe,
  nodi, tracciati, `samples` e `\foreach`.
- **Sandbox SBX-2** (`tex_sandbox.py`, `tikz_compile_service`):
  - `python -I` con `setrlimit` ed `execv`;
  - ambiente in allowlist; `openin_any=p`, `openout_any=p`,
    `shell_escape=f`; killpg al timeout;
  - una compilazione alla volta, mai durante un'estrazione Docling;
  - autotest all'avvio, a guasto chiuso.

  SBX-1 (container dedicato) non è stato fatto.
- **Oracolo geometrico** (`tikz_geometry`, pdfplumber sul PDF). Difetti
  rilevati:
  - `labels_overlap` (per glifo);
  - `text_outside_owner`;
  - `edge_crosses_label`;
  - `content_outside_page`;
  - `text_small`, cioè testo sotto 7 pt alla scala della dispensa, con
    pedici e apici riconosciuti dal glifo vicino (commit fa47802).

  In generazione i difetti bloccano; nell'anteprima e nel PATCH sono
  avvisi.
- **Fase 3** (WP6.3). `phase3_visual_formats` offre `tikz` solo se valgono
  insieme tre condizioni:
  - proposta accesa;
  - script della lingua coperto dai font;
  - nessun `tikz_unresolved` al tentativo precedente (marcatore in
    memoria del worker).

  Se non è offerto, system, messaggio e schema sono byte-identici. Quando
  è offerto:
  - nel messaggio user entra il blocco `## Formato aggiuntivo: tikz`;
  - `tikz` conta nel budget (a);
  - ha un solo fix (PROMPT 12, variante `tikz`);
  - sandbox occupata o motore assente non vanno al fix.
- **Revisione Vision della resa** (PROMPT 21, consultiva). Usa il PNG a
  150 dpi. Con difetti, spende l'unico fix solo se la riscrittura passa
  la validazione severa. Il costo va in `content_tokens.assets`.
- **Endpoint e frontend**:
  - `render-tikz`, anteprima con quota per utente;
  - `tikz-view`, che rende solo sorgenti già salvati nel corso;
  - `formats`;
  - `TikzFigure` per vista e slide; `TikzEditor` con 4 modelli (catena di
    misura, vibrometro, partitore, ponte di Wheatstone) provati dal test
    dei template;
  - la voce di menu compare solo nella dispensa e solo se il server rende
    `tikz`.
- **Fase 4 e 5**. `prompt_view` sostituisce il sorgente TeX con
  «(schema TikZ; etichette: …)».

## 11. Misure

| # | Esito |
|---|---|
| **M0** CPU/RAM della VM | **Non eseguita** (a cura dell'utente). Bloccante per `FIGURE_EXTRACTION_ENABLED=true` in produzione |
| **M1** fonti reali in produzione | **Non eseguita** (SQL in sola lettura nell'Appendice B del piano, a cura dell'utente) |
| **M2** Docling su PDF reali | arXiv 2402.10966 e 2304.11054, OCW 20.309, OpenStax. **Tempi** (arm64 nativo, 1 thread): 2,0-2,6 s/pag; RSS del figlio 1,35-1,76 GB. **Motore euristico**: 0,06-0,32 s/pag. **Richiamo** sulle figure con didascalia: Docling 0,93, euristico 0,90. **Precisione**: 0 falsi positivi su 45 ritagli. **Didascalie** esatte dopo la correzione. p90 dei byte 30-126 KB. **Proiezione sulla VM** senza M0 (k = 6): 11-15 s/pag, oltre la soglia di 4 s → decisione al cancello |
| **M3** delta dell'immagine | Stima arm64 ~1,39 GB espansi / ~0,48 GB compressi (build completa non eseguita: disco di Docker pieno). 134 pacchetti, nessun AGPL, CUDA, triton od OpenCV |
| **M4** Vision descrittiva | 49 ritagli: tutti i modelli passano. Scelto **gpt-4.1-mini a 768 px**: kind 0,94-0,98, useful 0,98-1,0, p95 3,6-4,8 s, ~0,0007 $/figura; manuale di 300 pagine ≈ 0,06 $ |
| **M5** TikZ | TeX Live Debian per XeLaTeX: **+550 MB** (pdflatex +393 MB), oltre soglia → TeX solo con build arg. Nessuna libgs. Spike di generazione con il modello (5 schemi × 3): **non eseguito**, perché la proposta automatica resta spenta |
| **M6** system prompt | Righe statiche misurate sulla variante peggiore; guardie invariate (PROMPT 3 ≤ 31.500, PROMPT 5 ≤ 18.200, PROMPT 6 ≤ 12.500) |
| **M7** non sostituzione | Due giri: il 1° **fallito** su S3, il 2° **passato** (dettagli sotto). Costo 15,13 $ |
| **Prova §7.3** | Corso con PDF arXiv CC BY sulla vibrometria + dispensa di prova. **Estrazione**: 4 + 14 figure, Vision 0,016 $. **Dispensa**: 3 figure di fonte oltre alle 4 generate, budget (a) rispettato. **Riga «Fonte»**: presente e identica in dispensa, PDF slide e frame video. **Fascia** fino a 212 mm, avatar da 212 mm. **Slide** 0,081 $. Il PDF arXiv non ha metadati bibliografici: la riga ripiega sul nome del file |

Dettaglio di M7:

- **1° giro**: TVD dei formati 0,31 contro 0,04 fra due estrazioni senza
  catalogo; con il catalogo il modello rinunciava agli schemi.
- **Correzione**: revisione mirata della riga del budget (b).
- **2° giro**: S1 0/8, S2 Δ 0,0, S3 TVD 0,06 contro 0,13, S4 ok.

**M7 dopo WP6.3 non ripetuta**: con la proposta spenta il modello non vede
`tikz`. Va ripetuta prima di accendere `FIGURE_TIKZ_PROPOSE_ENABLED`
(`scripts/measure_source_figures.py --substitution`).

## 12. Cancello intermedio (dopo WP4): rapporto e decisioni

L'utente ha chiesto di proseguire con WP5-WP7 senza attendere. Le decisioni
del cancello sono state prese in autonomia, con l'opzione **più
conservativa e reversibile**.

| Tema | Decisione | Motivo |
|---|---|---|
| Estrazione in produzione | Spenta nel compose (`FIGURE_EXTRACTION_ENABLED=false`) | M0 non eseguita; proiezione di M2 oltre 4 s/pag |
| Motore euristico in produzione | **Non** attivato | Qualità minore sulle figure composte; serve una decisione esplicita |
| Letteratura aperta | Spenta nel compose; OpenAlex solo con `OPENALEX_API_KEY` | Rete esterna e costi: accensione esplicita |
| Sandbox TikZ | SBX-2 nel codice, SBX-1 non fatta | SBX-1 richiede una modifica del compose |
| TeX Live nell'immagine | Solo con `--build-arg INSTALL_TEX=true` | M5: +550 MB |
| Proposta automatica di `tikz` | Spenta | Spike M5 di generazione e M7 ripetuta non eseguiti |
| Font / stile circuiti | Noto Sans (latino, greco, cirillico, CJK), `circuitikz` europeo | Default del piano |

## 13. Decisioni dell'utente e giurie

| # | Decisione |
|---|---|
| U1 | Cambi di politica e cancellazioni **non retroattivi** (§8) |
| U2 | Riga «Fonte» nella fascia in basso a sinistra di slide e frame |
| U3 | PPTX fra i documenti del corso |
| U4 | Chiave OpenAI dall'ambiente (`~/.zshenv`) per M4, M7 e la prova §7.3 |
| U5 | Ritagli sotto `/uploads`, come i documenti; nomi non indovinabili (uuid + sha12). Il frontend non espone mai l'URL |
| U6 | docling-slim (MIT) + torch CPU (BSD) come extra `figures`, `pypdfium2`, Dockerfile trixie |
| U7 | Codice scritto dall'agente principale; 3 verificatori in sola lettura per WP |

Esiti delle giurie:

- **J-Q1**: riferimento vivo, `content` = UUID.
- **J-Q2**: selezione ibrida (lessicale + riordino del modello).
- **J-Q3**: worker gemello con processo figlio.
- **J-Q4**: XeLaTeX → pdftocairo, con SBX-2.
- **J-Q5**: non retroattivo (U1).
- **J-Q6**: nessun ridisegno da `content_only`.
- **J-Q7**: Docling solo per rilevare, più ritaglio con pypdfium2.
- **J-Q8**: revisore gemello (PROMPT 19).

Motivazioni e alternative scartate sono nel piano, sezione (c).

## 14. Deviazioni dichiarate

1. **U1 contro il vincolo `content_only`** del brief: le figure collocate
   restano fino alla rigenerazione.
2. **FK `SET NULL`** con lo stacco, invece del cascade.
3. **dpi adattivo 300-600** per i vettoriali (D21).
4. **OpenAlex**: licenza della stessa location del PDF, non sempre
   `best_oa_location.license`.
5. **Wikimedia**: PNG reso da Commons anche per gli SVG.
6. **Esclusione di TikZ ribaltata** (D17). Il prompt «figure accademiche»
   escludeva LaTeX/TikZ; il committente l'ha riaperto con questo brief. Il
   formato esiste, ma è spento.
7. **«Ridisegna da figura» fuori perimetro** (D11): 3A, 3B e 3C non
   esistevano.
8. **`tikz_busy` risponde 409**, non 503 (l'API non usa 503 altrove).
9. **`prompt_view` anche in Fase 4** per i `tikz`: il modello delle slide
   vede solo le etichette, non il sorgente.
10. **Revisione Vision** della resa non rilanciata dopo il fix: contano
    solo i controlli deterministici.
11. **`storage_service._ALLOWED_ROOTS` non esteso a `courses`**: un
    `delete_directory("courses")` cancellerebbe tutti i corsi. WP2 usa un
    helper stretto per `courses/{cid}/document_figures/…`.

## 15. Sicurezza e rischi residui

- **`/uploads` pubblico** (U5; in produzione `ovh_sftp` su una docroot
  pubblica). I ritagli hanno la stessa esposizione per URL dei PDF
  sorgente. I nomi non sono indovinabili, ma un membro del corso che
  conosce il percorso può condividerlo.
- **Sandbox SBX-2**. È nello stesso container del backend: difesa a
  strati (lexer, preambolo fisso, TeX paranoico, limiti, ambiente senza
  segreti), non un isolamento del kernel. Per esporla a utenti non fidati
  l'upgrade è SBX-1.
- **Marcatore `tikz_unresolved` in memoria**. Un riavvio lo perde: al più
  un tentativo `tikz` in più, entro `course_lesson_content_auto_retry_max`.
- **Lock TeX/Docling**. La compilazione TeX attende l'estrazione, ma
  l'estrazione non attende una compilazione già in corso: il blocco è a
  senso unico. I render Chromium esistenti non sono coordinati.
- **Contenuti di terzi**. Didascalie, Vision, extmetadata di Commons,
  metadati OpenAlex ed etichette dei nodi passano da
  `prompt_safety.neutralize_third_party_text`, fra delimitatori di dati
  (test con canarino).
- **Cache degli SVG `tikz` solo in memoria**: il primo export dopo un
  deploy ricompila.

## 16. Limiti noti e lavori futuri

- **Test e controlli mancanti**:
  - nessun test dell'SSRF a livello di endpoint `/papers/import` (c'è a
    livello di client);
  - nessun test della downgrade della 0038 (il ciclo è stato eseguito a
    mano su un DB usa-e-getta);
  - harness DOM di `FigureFrame` assente (il frontend non ha un test
    runner).
- **Ottimizzazioni rinviate**:
  - OpenAlex senza cache dei PDF per corso;
  - formato TeX precompilato non fatto.
- **Comportamenti noti**:
  - il conteggio d'uso non si aggiorna al cambio di politica;
  - slide e frame mostrano solo nome e versione della licenza, senza URI
    (l'URI è nei crediti della dispensa);
  - il discorso cita anche le fonti di documenti diventati riservati
    finché la lezione non viene rigenerata (U1);
  - nella duplicazione con traduzione, le etichette dei nodi `tikz`
    restano nella lingua di partenza, come le etichette di Mermaid, DOT e
    Vega-Lite (`visual_assets[].content` non si traduce).
- **Prima di accendere la proposta automatica di `tikz`**: spike M5 di
  generazione (≥ 12/15 schemi puliti) e M7 ripetuta.

## 17. Test (garanzie → file)

| Garanzia | File |
|---|---|
| G1 riga «Fonte» ovunque | `test_figure_attribution`, `test_source_figure_render`, `test_source_figure_band`, `test_source_figure_api`, `test_speech_source_figures`, `test_frontend_source_figures`, `test_source_figure_call_sites` |
| G2 excluded/content_only fuori da catalogo e generazione | `test_source_figure_materialize` (canarini, TOCTOU), `test_source_figure_policy`, `test_source_figure_worker` (nessuna estrazione) |
| G3 open_only | `test_license_policy`, `test_source_figure_policy` |
| G4 licenza sempre valorizzata | `test_source_figure_model` |
| G5 isolamento figure/riassunto | `test_source_figure_worker` |
| G6 duplicazione | `test_source_figure_duplication` |
| G7 IDOR | `test_source_figure_api` (altro corso, uuid casuali), `test_tikz_endpoint` (vista di un altro corso) |
| G8 ciclo di vita non retroattivo | `test_source_figure_document_lifecycle` |
| G9 costo | `test_source_figure_vision`, `test_source_figure_redundancy`, `test_source_figure_relevance`, `test_tikz_render_review` |
| G10 nessun backfill | `test_source_figure_worker` |
| G11 nessun AGPL | `test_no_agpl_dependencies` |
| G12 estrazione | `test_source_figure_extraction` (Docling nel container), `test_source_figure_filters`, `test_source_figure_fixtures`, `test_document_metadata`, `test_pptx_documents` |
| I1 non sostituzione | `test_source_figure_non_substitution`, `test_source_figure_prompt_schema` + M7 |
| I2 buchi | `test_source_figure_gaps` |
| I3 revisore | `test_source_figure_redundancy` |
| I4 Fase 4 | `test_slides_source_figures` |
| WP5 rete | `test_safe_fetch`, `test_image_limits`, `test_wikimedia_client`, `test_openalex_figures` |
| WP6 | `test_tikz_validator`, `test_tikz_sandbox`, `test_tikz_render`, `test_tikz_registry`, `test_tikz_phase3`, `test_tikz_render_review`, `test_tikz_endpoint`, `test_frontend_figure_templates` |

**Test con dipendenze pesanti**. Docling, TeX e Chromium si provano nel
container `test` del Dockerfile, con `A4U_REQUIRED_DEPS`: lì uno skip
diventa un fallimento (`tests/dep_guard.py`). In locale i test saltano
con il motivo esplicito (`[dep:tex]`, `[dep:docling]`).

## 18. Passi manuali per la produzione

1. **M0** sulla VM, con il kit del piano: CPU, AVX, RAM, sonda Docker con
   Docling. Poi **M1**, con l'SQL dell'Appendice B in sola lettura.
2. **Deploy**:
   - `docker compose build backend` (per TikZ,
     `--build-arg INSTALL_TEX=true`);
   - migrazioni 0037 e 0038 **prima** dell'avvio (vedi CLAUDE.md);
   - `docker image prune` se il disco non basta.
3. **Accensioni esplicite**, in `.env`:
   - `FIGURE_EXTRACTION_ENABLED=true`, dopo M0;
   - `FIGURE_LITERATURE_ENABLED=true` e `OPENALEX_API_KEY`;
   - `FIGURE_TIKZ_ENABLED=true` (con TeX nell'immagine);
   - `FIGURE_TIKZ_PROPOSE_ENABLED=true`, solo dopo lo spike M5 e M7.
4. Nessun backfill: le estrazioni dei corsi esistenti si chiedono dalla UI
   o con `scripts/extract_document_figures.py --apply`.

## 19. Revisione avversariale (Fase D)

Esito riportato nel corpo della PR e nel report finale della campagna.
