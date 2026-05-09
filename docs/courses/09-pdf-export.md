# 09 — PDF export delle lezioni

Esportazione PDF delle lezioni usando il template grafico configurato
per l'organizzazione del corso. **Tre pipeline distinte** coesistono e
sono indipendenti:

| Pipeline | Trigger | Template | Path file | Sezione |
|---|---|---|---|---|
| **Lezione testo** (§7) | `content_status ∈ ready/approved` | `pdf_templates` | `{org}/{course}/{lesson}.pdf` | [Lezione testo (§7)](#lezione-testo-7) |
| **Slide** (Fase 4) | `slides_status ∈ ready/approved` | `slide_templates` (unificato con avatar — migration 0022) | `{org}/{course}/{lesson}_slides.pdf` | [Slide (Fase 4)](#slide-fase-4) |
| **Discorso** (Fase 5) | `speech_status ∈ ready/approved` | `pdf_templates` (riusa il template della lezione testo) | `{org}/{course}/{lesson}_speech.pdf` | [Discorso (Fase 5)](#discorso-fase-5) |

Tutte usano lo stesso stack di base (Jinja2 + WeasyPrint) ma differiscono
per layout (A4 portrait / 16:9 / per-slide grouping), input dati
(`content_raw` / `slides_raw` + `content_raw` / `speech_raw` +
`slides_raw`) e necessità di pre-render Mermaid (sì per testo+slide, no
per discorso che è prosa pura).

**Reset PDF su rigenerazione AI a monte**: quando l'utente rigenera il
content / le slide / il discorso, lo status del PDF a valle viene
resettato a `empty` per impedire il download di un PDF stale (vedi
`request_lesson_*_generation` in service file).

---

## Lezione testo (§7)

Per ogni lezione con `content_status ∈ {ready, approved}`, l'utente può
generare un **PDF stampabile**: copertina con logo + titolo, body con
markdown renderizzato (tabelle, formule LaTeX live, diagrammi Mermaid live,
esempi, key takeaways, references). Il template (colori, font, page size,
margini, header/footer, loghi, background) è applicato in fase di rendering.

L'esportazione è scoped **per lezione** — niente PDF aggregato del corso in
MVP. Lo stato `pdf_status` è indipendente da `content_status`: si può
modificare il contenuto e poi rigenerare il PDF.

## Schema dati (migration 0016)

### `course_lesson` — 8 colonne nuove

| Colonna | Tipo | Note |
|---|---|---|
| `pdf_status` | VARCHAR(40) | CHECK ∈ `empty/pending/processing/ready/failed`, default `empty` |
| `pdf_progress` | SMALLINT | CHECK 0..100, default 0 |
| `pdf_progress_phase` | VARCHAR(50) | `preparing / rendering_html / rendering_pdf` |
| `pdf_path` | VARCHAR(500) | path **relativo** alla `GENERATED_PDFS_DIR` |
| `pdf_template_id` | UUID FK `pdf_templates.id` SET NULL | snapshot del template usato all'ultima generazione |
| `pdf_attempts` | SMALLINT | counter retry, default 0 |
| `pdf_error` | TEXT | ultimo errore (max 500 char) |
| `pdf_generated_at` | TIMESTAMPTZ | timestamp ultimo successo |

### State machine

```
empty → pending → processing ────────► ready
                          │              ▲
                          ▼              │
                       failed ───────────┘ (retry manuale)
```

Niente derivazione su `course.status`: la pipeline è indipendente.

## Stack tecnologico

| Layer | Lib | Note |
|---|---|---|
| Markdown → HTML | `markdown-it-py` + `mdit_py_plugins.dollarmath` | GFM tables, strikethrough, math |
| Math LaTeX → MathML | `latex2mathml` | Pre-rendering server-side, no JS in PDF |
| Diagrammi Mermaid → SVG | `Playwright` (Chromium headless) | Solo pre-render in batch, una sessione per lezione |
| Template HTML | `Jinja2` | `backend/app/templates/lesson_pdf.html.j2` |
| HTML → PDF | `WeasyPrint` 68+ | CSS Paged Media completo (background edge-to-edge, running header, page counter) |

**Perché WeasyPrint e non più Chromium**: Chromium headless ha bug
strutturali per CSS Paged Media:
- `position: fixed` viene **hard-clipped** all'`@page` content area;
  offset negativi non escapano il clip.
- `headerTemplate`/`footerTemplate` di Playwright soffrono di padding
  default ~4mm (Puppeteer #4132), bug di scala 4/3 (#7693), non
  ereditano stili dal body (#1853).
- `@page { background: url(...) }` viene silenziosamente droppato in
  headless.

WeasyPrint supporta CSS Paged Media correttamente: `position: fixed`
con offset negativi extende oltre l'`@page` content area, `position:
running()` con `@top-left { content: element(...) }` ripete header
su ogni pagina, `counter(page)` numera. Single-pass, niente clipping.

Pattern verificato sul progetto gemello `avatar4universityAPI`.

**KaTeX e Mermaid in-page non sono più usati.** Le formule LaTeX
vengono convertite a MathML server-side con `latex2mathml`
(WeasyPrint renderizza MathML nativamente). I diagrammi Mermaid
vengono pre-renderizzati a SVG con una **sola** sessione Playwright
headless per lezione (carica `mermaid@10.9.x` con `htmlLabels: false`
così le label diventano SVG `<text>` e non `<foreignObject>` —
WeasyPrint non supporta foreignObject).

## Architettura backend

### `course_lesson_pdf_service.py`

Pure-functions + orchestrazione DB. Diviso in sezioni logiche:

**Markdown → HTML pipeline**

```python
md = MarkdownIt("commonmark", {"html": True, "linkify": True})
   .enable(["table", "strikethrough"])
   .use(dollarmath_plugin, allow_labels=False, double_inline=True)

# Custom renderers per dollarmath: emettono MathML direttamente via
# latex2mathml — WeasyPrint renderizza <math> nativamente.
md.add_render_rule("math_inline", _render_math_inline)  # → <span class="math-inline"><math>...</math></span>
md.add_render_rule("math_block",  _render_math_block)   # → <div class="math-block"><math display="block">...</math></div>
```

> **Firma renderer markdown-it-py**: `add_render_rule` lega la funzione
> come metodo del `RendererHTML` → la firma corretta è
> `(self, tokens, idx, options, env)`, non `(tokens, idx, options, env)`.

`_normalize_math_delimiters` converte `\(..\)` / `\[..\]` (output AI
"puro" LaTeX) in `$..$` / `$$..$$` riconosciuti da `dollarmath`. Esclude
i pattern asset-ref `\[FIG:..\]` / `\[TAB:..\]` ecc.

`_convert_math_to_mathml(latex, *, display)` wrappa `latex2mathml.
converter.convert`. In caso di parse error emette un fallback `<code
class="math-error">` col LaTeX grezzo, così la lezione resta leggibile
anche con sintassi malformata.

**Asset substitution**

Ogni `[FIG:..]/[TAB:..]/[EQ:..]/[EX:..]` nel body markdown viene
pre-renderizzato in un blocco HTML e iniettato sulla riga propria
(con righe vuote prima/dopo) prima del rendering markdown-it:

| Tipo | Output |
|---|---|
| `FIG` (visual_assets) | `<figure class="visual"><div class="mermaid-svg">{svg_pre_renderizzato}</div></figure>` per mermaid (SVG inline); placeholder testuale altrimenti |
| `TAB` (tables) | `<figure class="table">` con tabella renderizzata da markdown-it |
| `EQ` (equations) | `<figure class="equation"><div class="math-block"><math>...</math></div></figure>` (MathML pre-renderizzato) |
| `EX` (examples) | `<aside class="example">...</aside>` con titolo + corpo markdown |

Asset orfano (riferimento senza definizione): `<div class="missing-asset">`.

**Template rendering** (`render_lesson_html`)

Pure-function: prende `course`, `lesson`, `organization`, `pdf_template`
e restituisce l'HTML completo. Indipendente dal DB → testabile in
isolamento. Le label (Sintesi, Punti chiave, Riferimenti) sono i18n
in base a `course.language_code`.

**Per-page assets (loghi + background) — pattern CSS Paged Media**

Tutti gli elementi ricorrenti su ogni pagina (sfondo edge-to-edge,
loghi in alto, numero pagina in basso) sono dichiarati nel CSS del
template Jinja. WeasyPrint risolve il pattern correttamente in
single-pass, senza header/footer iframe né fixed-element clipping.

```css
@page {
  size: A4;
  margin: {{ margin_top_cm }}cm {{ margin_side_cm }}cm
          {{ margin_bottom_cm }}cm {{ margin_side_cm }}cm;
  @top-left {
    content: element(pageHeader);  /* loghi running */
    width: 100%;
    vertical-align: top;
  }
  @bottom-center {
    content: counter(page);  /* numero pagina automatico */
    font-size: 9pt;
  }
}

.page-background {
  position: fixed;
  /* offset negativi = -margine: estende il box oltre il content area
     fino ai bordi del foglio. WeasyPrint NON clippa fixed elements
     all'@page area (a differenza di Chromium). */
  top: -{{ margin_top_cm }}cm;
  bottom: -{{ margin_bottom_cm }}cm;
  left: -{{ margin_side_cm }}cm;
  right: -{{ margin_side_cm }}cm;
  background-image: url("...");
  background-size: cover;
  z-index: -100;
}

.page-header {
  position: running(pageHeader);  /* spostato nel @top-left */
  /* layout flex/float per logo-left / logo-right */
}
```

I margini (`margin_top_cm`, `margin_side_cm`, `margin_bottom_cm`)
vengono calcolati da `_compute_template_margins_cm(tpl_dict)` a
partire dai campi `margin_mm`/`header_height_mm`/`footer_height_mm`
del template:
- `margin_top_cm = max(margin_mm, header_height_mm + 5) / 10` se ci
  sono loghi (serve spazio per il running header).
- `margin_bottom_cm = max(margin_mm, footer_height_mm + 5) / 10` se
  c'è footer height (serve spazio per page counter).
- `margin_side_cm = margin_mm / 10`.

Loghi del template inseriti come `<img>` dentro `<div class="page-header">`,
che il CSS sposta nel margin-box `@top-left` di **ogni** pagina (cover
inclusa) via `position: running(pageHeader)`. La copertina non duplica
i loghi inline.

**Mermaid pre-rendering**

`_prerender_mermaid_for_lesson(content)` estrae tutti gli asset
`format=mermaid` dal `content_raw` e li renderizza in batch con UNA
singola sessione Playwright headless: carica `mermaid@10.9.4`
(versione che rispetta `htmlLabels: false`), espone una funzione
`window.__renderMermaid(id, code)` e itera sui sorgenti restituendo
gli SVG. Costo tipico: ~1s di startup browser + ~50-200ms per
diagramma. Se la lezione non ha mermaid, niente browser viene avviato.

Il dict `{asset_id → svg_string}` è poi passato a `render_lesson_html`
e da lì a `_build_asset_html_map`, che lo iniezta nei blocchi
`<figure class="visual"><div class="mermaid-svg">{svg}</div></figure>`.
Se il rendering fallisce (rete, sintassi mermaid), il template emette
`<pre class="mermaid-fallback">` col codice originale.

**WeasyPrint** (`generate_pdf_bytes`)

```python
def _render_with_weasyprint_sync(html, *, base_url=None) -> bytes:
    return WeasyHTML(string=html, base_url=base_url).write_pdf()


async def generate_pdf_bytes(*, html, base_url=None) -> bytes:
    # WeasyPrint è sync e CPU-bound (~500ms-1s per A4 multipagina).
    # to_thread per non bloccare il loop asyncio del worker.
    return await asyncio.to_thread(_render_with_weasyprint_sync, html, base_url=base_url)
```

Niente JavaScript: WeasyPrint non lo esegue. Tutta la logica
JS-dependent è già stata espansa server-side prima di arrivare qui
(MathML inline, Mermaid SVG inline). Niente `window.__renderingDone`,
niente CDN da attendere.

> **Windows local-dev**: WeasyPrint richiede GTK3 runtime. Installalo
> una volta sola con `winget install tschoonj.GTKForWindows`. Su Linux
> production il Dockerfile installa `libpango-1.0-0`,
> `libpangoft2-1.0-0`, `libharfbuzz0b`, `libgdk-pixbuf-2.0-0`,
> `libffi8`, `fonts-dejavu-core`, `fonts-liberation`.

**Filesystem**

```
{generated_pdfs_dir}/{org_id}/{course_id}/{lesson_id}.pdf
```

Path persistito su `course_lesson.pdf_path` come stringa **relativa** alla
root configurata. Il prossimo export sovrascrive lo stesso file (no
versioning).

**`materialize_lesson_pdf`** è il punto di ingresso del worker:
1. risolve il template (lesson.pdf_template_id → org default);
2. `_prerender_mermaid_for_lesson(content_raw)` → `{asset_id: svg}` (una
   sessione Playwright per lezione, solo se ci sono mermaid);
3. `render_lesson_html(... mermaid_svg_map=...)` → HTML completo con
   MathML + SVG inline + CSS @page con sfondo edge-to-edge;
4. `generate_pdf_bytes(html=html)` → bytes via WeasyPrint;
5. scrive il file su disco;
6. aggiorna `pdf_path`, `pdf_template_id`, `pdf_generated_at`.

### `course_lesson_pdf_worker.py`

Pattern speculare al worker Fase 3 (lesson content), scoped a livello
LEZIONE:

- `_inflight: set[UUID]` su `lesson_id`
- `_semaphore = asyncio.Semaphore(course_lesson_pdf_max_concurrency)` —
  default `2` (WeasyPrint è CPU-bound; il pre-render mermaid è I/O-bound
  e dura solo se ci sono diagrammi)
- Polling: `course_lesson_pdf_poll_interval_seconds` (default `4`)
- Ticker progresso: ease-out 10→85% in ~20s (`pdf_progress` aggiornato
  ogni 2s)
- **Cancel-check post-rendering**: dopo `materialize_lesson_pdf`, ricarica
  `pdf_status` dal DB; se è stato spostato a `failed` (cancel-all),
  scarta il path appena scritto e non aggiorna `lesson.pdf_path` (il
  file resta sul disco — il prossimo export lo sovrascrive).
- **Auto-retry trasparente** — `_apply_failure(lesson, *, error,
  auto_retry_max)`: in caso di errore (timeout pre-render, parse
  WeasyPrint, FS write failure) se `pdf_attempts <
  course_lesson_pdf_auto_retry_max` (default 5), riporta
  `pdf_status='pending'` invece di `failed` e azzera `pdf_error`. La
  UI vede solo "in elaborazione" finché passa, mai il messaggio di
  errore. Solo dopo `auto_retry_max` esauriti `→ failed` (terminale).

Registrato in `app/main.py` lifespan.

### Template default fallback

Se l'org non ha `pdf_templates`, viene usato `_default_template_dict`:
A4, Inter, primary `#1976D2`, secondary `#9C27B0`, margin 20mm, no
loghi/background. Il PDF viene comunque generato.

### Asset URL resolver

Loghi e background del template sono salvati come path relativi al
filesystem (`/uploads/templates/<uuid>.png`).
`_resolve_template_asset_url(raw, public_base_url=None)`:
- URL assoluti (`http://`, `https://`, `data:`, `file://`) → inalterati.
- Path relativi → letti **dal filesystem** (`{upload_root}/templates/...`)
  e embeddati come **data URL base64** (`data:image/png;base64,...`).
  WeasyPrint accetta anche `url(file://...)` con `base_url`, ma le
  data URL sono più portabili e non dipendono dalla CWD del worker.
- File non trovato sul filesystem ma `public_base_url` fornito →
  fallback a `{public_base_url}/{path}` (utile in test o per CDN).
- Tutto fallisce → `None` (l'asset viene ignorato, il PDF si genera
  comunque senza quell'elemento; viene loggato `pdf_template_asset_*`).

Path-traversal protection: ogni path relativo viene risolto e
validato con `Path.relative_to(upload_root)`. Path che escono dalla
root vengono rifiutati con `pdf_template_asset_outside_upload_root`.

## API endpoints (4 nuovi)

Tutti sotto `/orgs/{org_id}/courses/{course_id}/...`.

| Metodo | Path | Permesso | Effetto |
|---|---|---|---|
| `POST` | `/lessons/{lid}/pdf/export?pdf_template_id={uuid?}` | `course:generate` | Set lezione `pdf_status='pending'`. **202**. |
| `POST` | `/lessons-pdf/export-all?pdf_template_id={uuid?}` | `course:generate` | Set tutte le lezioni esportabili `pending`. **202**. |
| `POST` | `/lessons-pdf/cancel-all` | `course:generate` | Annulla `pending`/`processing` → `failed`. |
| `GET` | `/lessons/{lid}/pdf/download` | `course:view` | Scarica il PDF. 404 se `pdf_status != ready`. |

Tutti i POST restituiscono `CourseOut` aggiornato. Il GET restituisce
`FileResponse(application/pdf)` con `Content-Disposition: attachment;
filename="..."` (filename costruito da `course.title — lesson_code
lesson.title.pdf`, sanitizzato).

### Scelta del template grafico

Il query param opzionale `pdf_template_id` permette all'utente di
scegliere quale template applicare. Quando fornito:
- Validato sull'org del corso (404 `pdf_template_not_found` se non
  appartiene). 
- Persistito su `course_lesson.pdf_template_id` PRIMA che il worker
  prenda il task. Il rendering legge questo campo via
  `_resolve_pdf_template_for_lesson`, con fall-back al template
  `is_default` dell'org se il campo è `None` o se il template scelto
  è stato eliminato nel frattempo.
- In modalità batch (`export-all`), lo stesso template viene applicato
  a tutte le lezioni esportabili (override del valore precedente
  per-lezione).

Se omesso: il worker usa il template della lezione (se già impostato
da un export precedente), altrimenti il default dell'org.

### Vincoli di stato

`request_lesson_pdf` solleva `ConflictError` se:
- `lesson.content_status` non è in `{ready, approved}` →
  `invalid_lesson_content_status_for_pdf`
- `lesson.pdf_status` non è in `{empty, ready, failed}` →
  `pdf_already_in_progress`

`download_lesson_pdf` solleva 404 se:
- `pdf_status != 'ready'` o `pdf_path` vuoto → `pdf_not_ready`
- file mancante sul filesystem → `pdf_file_missing`

## Frontend

### Tipi (`api/courses.ts`)

```ts
export type LessonPdfStatus = "empty" | "pending" | "processing" | "ready" | "failed";

interface CourseLessonOut {
  // ... campi esistenti
  pdf_status: LessonPdfStatus;
  pdf_progress: number;
  pdf_progress_phase: string | null;
  pdf_path: string | null;
  pdf_template_id: string | null;
  pdf_attempts: number;
  pdf_error: string | null;
  pdf_generated_at: string | null;
}
```

Namespace `coursesApi.lessonPdf`:
- `exportLesson(orgId, courseId, lessonId, pdfTemplateId?)` → `CourseOut`
- `exportAll(orgId, courseId, pdfTemplateId?)` → `CourseOut`
- `cancelAll(orgId, courseId)` → `CourseOut`
- `download(orgId, courseId, lessonId)` → `Blob` (FileResponse)
- `downloadUrl(orgId, courseId, lessonId)` → string (per `<a href>`)

### UI in `CourseLessonContentView.tsx`

Per ogni lezione con `content_status ∈ {ready, approved}`:
- **Badge** `LessonPdfStatusBadge` accanto al content status
- Bottoni contestuali: **"Esporta PDF"** (empty/failed) | **"Scarica PDF"**
  (ready) | **"Rigenera PDF"** (ready, dopo edit del content) | progress
  bar quando `pending`/`processing`

Header del tab:
- **"Esporta tutti i PDF"** (gated `course:generate`, disabled se almeno
  uno in flight)
- **"Annulla tutti gli export"** visibile quando esiste almeno un
  `pending`/`processing`

### Dialog scelta template — `LessonPdfExportDialog.tsx`

Cliccando "Esporta PDF" (singola o batch) si apre un dialog modal che:
- Carica i template dell'org via `pdfTemplatesApi.list(orgId)` (cache
  TanStack 30s).
- Renderizza ogni template come **radio card** con:
  - Swatch `primary_color` + `secondary_color`
  - Nome + badge "Predefinito" se `is_default`
  - Sottotitolo `font_family · page_size · margin_mm mm`
- Pre-selezione: `lesson.pdf_template_id` corrente (se settato) →
  altrimenti il template `is_default` → altrimenti il primo.
- Conferma → chiama `exportPdfMut.mutate({ lessonId, templateId })` o
  `exportAllPdfMut.mutate(templateId)` a seconda della modalità.
- Stato vuoto (org senza template): hint "Nessun template
  configurato — verrà usato il template di default integrato"; il
  bottone "Esporta" resta abilitato e invia `pdf_template_id=null` →
  il backend usa `_default_template_dict` (Inter, A4, blu/viola).

Download via blob:
```ts
const blob = await coursesApi.lessonPdf.download(orgId, courseId, lessonId);
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url; a.download = filenameFromContentDisposition(...);
a.click();
URL.revokeObjectURL(url);
```

### Polling

`CourseEditorPage.tsx`: `refetchInterval` esteso a 4000ms anche quando
almeno una lezione ha `pdf_status ∈ {pending, processing}`.

### i18n

Namespace `courses.lessonsPdf.*`:
- `statuses.{empty, pending, processing, ready, failed}`
- `phases.{preparing, rendering_html, rendering_pdf}`
- `actions.{export, exportAll, cancelAll, download, regenerate}`
- `tooltips.{notExportable, alreadyInFlight, mustApprove}`
- `toast.{exportRequested, exportAllRequested, cancelled, downloaded}`

## Configurazione

```env
# Worker
COURSE_LESSON_PDF_POLL_INTERVAL_SECONDS=4
COURSE_LESSON_PDF_MAX_CONCURRENCY=2
# Numero massimo di retry automatici prima di transitare a `failed`.
# La UI vede la lezione come "in elaborazione" durante i retry.
COURSE_LESSON_PDF_AUTO_RETRY_MAX=5

# Filesystem
GENERATED_PDFS_DIR=generated_pdfs        # path relativo o assoluto

# Asset resolver per loghi/background del template
PUBLIC_BASE_URL=http://localhost:8000    # antepone questo prefisso ai
                                         # path relativi del template
```

`COURSE_LESSON_PDF_MAX_CONCURRENCY=2` è prudente: WeasyPrint è
CPU-bound ma leggero (~50MB RAM per render). Il pre-render mermaid
apre un'istanza Chromium per ogni lezione che ha diagrammi
(~150-200MB RAM per ~2-5s totali). Aumentare solo dopo test di carico.

### Setup iniziale

```powershell
# 1. Playwright Chromium (per pre-render mermaid → SVG)
.venv/Scripts/python.exe -m playwright install chromium

# 2. WeasyPrint runtime (Windows local-dev)
winget install tschoonj.GTKForWindows
# Su Linux/Docker: gestito dal Dockerfile (libpango/libharfbuzz/...)
```

### Dipendenze pyproject

```toml
[project]
dependencies = [
  # ...
  "playwright>=1.49.0",       # solo pre-render mermaid → SVG
  "weasyprint>=63.0",         # HTML → PDF
  "latex2mathml>=3.77",       # LaTeX equations → MathML
  "jinja2>=3.1.4",
  "markdown-it-py[plugins]>=3.0.0",
]
```

## Cosa NON fa questa iterazione (out of scope)

1. **PDF aggregato del corso** (zip o single-PDF concatenato) — solo
   per-lezione.
2. **Object storage** (S3/MinIO) — il filesystem locale è la persistence
   layer; per scalare oltre il single-host serve uno step esplicito.
3. **Versioning storico**: il file viene **sovrascritto** ad ogni nuovo
   export. `pdf_template_id` snapshotta solo il template dell'ultima
   generazione.
4. **Rendering offline**: il pre-render Mermaid carica `mermaid@10.9.4`
   da CDN (jsdelivr). Se la macchina del worker non ha internet, i
   diagrammi falliscono (fallback testuale `<pre>`); il resto del PDF
   viene comunque generato. WeasyPrint stesso è completamente offline
   (no CDN per fonts, math o styling). Soluzione future: bundle locale
   di mermaid.esm o pre-rendering via mermaid-cli + node.
5. **Streaming SSE** del progresso al client: la UI fa polling.
6. **Diff-detection** automatico tra `content_raw` modificato e PDF già
   generato: il badge resta `ready` finché l'utente non clicca "Rigenera
   PDF" esplicitamente.

---

## Slide (Fase 4)

Pipeline parallela e indipendente dal PDF testo. Stato per-lezione su
`course_lesson.slides_pdf_*` (8 colonne, migration 0020 + FK migrata a
`slide_templates` in 0022). Path file: suffisso `_slides.pdf`.

### Stack

Stesso del PDF testo — **WeasyPrint** + **Jinja2** + **Playwright** per
pre-render mermaid. Differenze principali:
- Layout: A4 **portrait single-column block-flow** (mantenuto dal feedback utente: niente layout 16:9 landscape — il PDF deve essere comodo da stampare e leggere)
- Template: `slide_templates` (16:9 originariamente per avatar video, ora unificato anche per il PDF slide via migration 0022 con campi aggiunti `margin_mm` + `background_opacity_pct`)
- Asset rendering: stesso pattern di Fase 3 (visual/table/equation/example) + supporto a `new_assets` di Fase 4

### Slide split (bullet+asset → 2 pagine)

Il template `lesson_slides_pdf.html.j2` espande visualmente le slide
con `references_assets ≠ []` AND (`bullets ≠ []` OR `body ≠ ''`) in
**due pagine consecutive** con lo stesso titolo:

- **pagina N**: tag "Lezione X" + titolo + body + bullet (niente asset)
- **pagina N+1**: stesso titolo + asset isolato (niente bullet, niente body)

Vantaggio: gli asset (specialmente Mermaid) hanno l'intero body a
disposizione per il rendering, niente competizione verticale, niente
workaround di scaling SVG. La numerazione pagina viene ricalcolata
sulla sequenza espansa.

Le slide pure-bullet (no asset) o pure-asset (no bullet) restano
single-page.

### Mermaid rendering

I diagrammi Mermaid vengono incapsulati in `<img>` con **data-URI base64** anziché inseriti come SVG inline. Motivo: nel contesto slide PDF, un SVG inline con attributi `width="X" height="Y"` espliciti emessi da Mermaid 10.9.x ignora il vincolo CSS `max-height` e sborda dal body. Un `<img>` invece è un replaced element con aspect ratio intrinseca, e `max-width + max-height` gli applicano scaling proporzionale corretto.

```python
def _svg_to_data_uri(svg: str) -> str:
    payload = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"
```

Encoding base64 (non URL-encoding) perché l'SVG di Mermaid contiene molti `"` (attributi viewBox, xmlns, ...) che romperebbero un `src="..."` HTML.

### Asset resolver

`_resolve_asset_for_slide(asset_id, content_raw, new_assets)` cerca
nell'ordine:
1. `content_raw["visual_assets"]` (Fase 3) — `kind="visual"`
2. `content_raw["tables"]` — `kind="table"`
3. `content_raw["equations"]` — `kind="equation"`
4. `content_raw["examples"]` — `kind="example"`
5. `slides_raw["new_assets"]` (Fase 4) — `kind="new_visual"` (ricicla il rendering visual)

Mirror della logica frontend `lib/slides.resolveAsset()`.

### API endpoints (4 nuovi)

| Metodo | Path | Permesso | Effetto |
|---|---|---|---|
| `POST` | `/lessons/{lid}/slides-pdf/export?pdf_template_id={uuid?}` | `course:generate` | Set `slides_pdf_status='pending'`. **202**. |
| `POST` | `/lessons-slides-pdf/export-all?pdf_template_id={uuid?}` | `course:generate` | Tutte le lezioni esportabili. |
| `POST` | `/lessons-slides-pdf/cancel-all` | `course:generate` | Annulla `pending`/`processing`. |
| `GET` | `/lessons/{lid}/slides-pdf/download` | `course:view` | Scarica `application/pdf`. |

### Frontend

Tab "Slide" (`CourseLessonSlidesView.tsx`) ha bottoni primary "Esporta PDF" / "Scarica PDF" / "Aggiorna PDF" (con stale logic via `isSlidesPdfStale`) e kebab "Rigenera PDF". Dialog `LessonSpeechPdfExportDialog`... wait, refuso — dialog è `LessonSlidesPdfExportDialog.tsx` che usa `slideTemplatesApi.list(orgId)` (template avatar+slide).

### File rilevanti

```
backend/app/services/course_lesson_slides_pdf_service.py   # render + materialize + slide split + mermaid pre-render
backend/app/services/course_lesson_slides_pdf_worker.py    # worker (cap=2, riusa course_lesson_pdf_*)
backend/app/templates/lesson_slides_pdf.html.j2            # template Jinja A4 portrait
backend/alembic/versions/0020_lesson_slides_pdf.py         # 8 colonne slides_pdf_*
backend/alembic/versions/0022_unify_slide_templates.py     # FK slides_pdf_template_id → slide_templates
frontend/src/pages/org/courses/components/LessonSlidesPdfExportDialog.tsx
```

---

## Discorso (Fase 5)

Pipeline parallela e indipendente dal PDF slide. Stato per-lezione su
`course_lesson.speech_pdf_*` (8 colonne, migration 0024). Path file:
suffisso `_speech.pdf`.

### Stack

Solo **WeasyPrint** + **Jinja2** — niente Mermaid pre-render (il
discorso è prosa pura, niente asset visivi). Template `pdf_templates`
(stesso del PDF lezione testo, perché il discorso è anch'esso testo
single-column block-flow A4 portrait).

### Layout per-slide grouping

Confermato dall'utente come scelta di design: il PDF discorso è
**raggruppato per slide** (non lineare). Per ciascuna entry di
`slide_to_segments_map`:

- **Header slide**: badge `slide_number` + titolo slide (lookup da `slides_raw`) + durata totale slide
- **Lista segmenti** in ordine, ognuno:
  - riga timeline `[mm:ss — mm:ss]` cumulativa (calcolata da `format_timeline()` server-side)
  - durata `Ns` in font monospace
  - testo segmento (font serif, line-height 1.5, justify)
  - delivery notes in italic muted (se non vuote)
- Separatore tra slide

Cover con badge "DISCORSO" + "Lezione N" + titolo + corso + meta-row con durata totale e word count.

Footer pagina: `{course.title} · {lesson.title} · pageNum/total`.

### `format_timeline` helper

```python
def format_timeline(
    slide_to_segments_map: list[dict],
    seg_by_id: dict[str, dict],
) -> list[dict]:
    """Calcola la timeline cumulativa per ciascuna slide.
    Output: lista di entries pronte per Jinja con start_mmss, end_mmss,
    duration_label, slide_total_label."""
```

Il cumulativo è calcolato seguendo l'ordine slide → segment_ids[] del
map. Mirror della logica frontend `LessonSpeechView.tsx` per
consistenza UI/PDF.

### API endpoints (4 nuovi)

| Metodo | Path | Permesso | Effetto |
|---|---|---|---|
| `POST` | `/lessons/{lid}/speech-pdf/export?pdf_template_id={uuid?}` | `course:generate` | Set `speech_pdf_status='pending'`. **202**. |
| `POST` | `/lessons-speech-pdf/export-all?pdf_template_id={uuid?}` | `course:generate` | Tutte le lezioni esportabili. |
| `POST` | `/lessons-speech-pdf/cancel-all` | `course:generate` | Annulla. |
| `GET` | `/lessons/{lid}/speech-pdf/download` | `course:view` | Scarica `application/pdf`. |

### Frontend

Tab "Discorso" (`CourseLessonSpeechView.tsx`) ha gli stessi bottoni del PDF slide. Dialog `LessonSpeechPdfExportDialog.tsx` usa `pdfTemplatesApi.list(orgId)` (NO `slide_templates` — il discorso è prosa, usa il template lezione).

### File rilevanti

```
backend/app/services/course_lesson_speech_pdf_service.py   # render + materialize + format_timeline (riusa helper di base)
backend/app/services/course_lesson_speech_pdf_worker.py    # worker (cap=2, riusa course_lesson_pdf_*)
backend/app/templates/lesson_speech_pdf.html.j2            # template Jinja A4 portrait per-slide grouping
backend/alembic/versions/0024_lesson_speech_pdf.py         # 8 colonne speech_pdf_*
frontend/src/pages/org/courses/components/LessonSpeechPdfExportDialog.tsx
```

---

## Settings comuni

I worker delle 3 pipeline PDF condividono le stesse settings env
(`course_lesson_pdf_*`) per uniformità. WeasyPrint è il bottleneck CPU,
quindi il cap=2 di default si applica al totale concorrenza
intra-pipeline. Se necessario, si può separare tramite altre env (non
ancora implementato).

```env
COURSE_LESSON_PDF_POLL_INTERVAL_SECONDS=4
COURSE_LESSON_PDF_MAX_CONCURRENCY=2
COURSE_LESSON_PDF_AUTO_RETRY_MAX=5
GENERATED_PDFS_DIR=generated_pdfs
```
