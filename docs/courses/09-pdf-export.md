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
`slides_raw`) e necessità di pre-render delle figure — Mermaid, Vega-Lite,
DOT, `function` via `figure_render_service.render_figure_map` (sì per
testo+slide, no per discorso che è prosa pura).

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
| Math LaTeX → SVG | `MathJax` 3.2.2 (tex-svg) via `Playwright` | Pre-rendering server-side a SVG: WeasyPrint **non** renderizza MathML, ma rende l'SVG correttamente |
| Math LaTeX → MathML (fallback) | `latex2mathml` | Gate offline / fallback quando la CDN MathJax è irraggiungibile |
| Diagrammi Mermaid → SVG | `Playwright` (Chromium headless), pin `MERMAID_CDN_VERSION` = 11.17.2 | Solo pre-render in batch, una sessione per lezione (`mermaid_prerender`); SVG inline nella dispensa |
| Grafici Vega-Lite → SVG | `vl-convert-python` in processo figlio `spawn` | Registro `figure_render_service` (doc 17): validazione contro lo schema v6 + regole D5, tema `VEGALITE_THEME_CONFIG`, `normalize_svg` → `<img data:svg>` |
| Grafi DOT → SVG | binario `dot` (apt `graphviz`) in `subprocess` con timeout | Tema `DOT_DEFAULTS` iniettato, `normalize_svg` → `<img data:svg>` |
| Figure `function` → SVG | numpy + matplotlib in thread, sympy in processo figlio | `figure_function_service`: rami, punti notevoli, forme esatte, didascalia calcolata; `<img data:svg>` |
| Cornice e numerazione «Figura N.» | `figure_numbering` + `figure_markup` (partial `partials/figure.html.j2`) | Numero dalla prima citazione `[KIND:id]`, contatore per kind (figure, tabelle, equazioni, esempi; teorema «Lemma N.» sul contatore EQ), orfani in coda (A12, D3); «Figura.»/«Tabella.» senza numero nelle slide (A2) |
| Rimandi testuali e ancore | `asset_ref_normalize` (mirror `lib/assetRefNormalize.ts`) | Citazione in linea → «Figura N» (senza punto, lingua del corso), UNA ancora `[KIND:id]` dopo il blocco della prima citazione; coda (punti chiave, riferimenti) con soli rimandi via `cite_asset_refs` |
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
vengono **pre-renderizzate a SVG** server-side con **MathJax**
(`tex-svg`, caricato da CDN in una sessione `Playwright` headless,
stesso pattern dei diagrammi Mermaid). Scelta deliberata: WeasyPrint
**non** renderizza il MathML (ne stampa solo il contenuto testuale,
perdendo pedici/apici/frazioni), mentre rende l'SVG correttamente. Se
la CDN MathJax è irraggiungibile, ogni formula ricade su `latex2mathml`
(MathML → `<code>`): degrada con grazia, mai peggio di prima. I
diagrammi Mermaid vengono pre-renderizzati a SVG con una **sola**
sessione Playwright headless per lezione (carica
`mermaid@{settings.mermaid_cdn_version}`, default `11.17.2`, con
`htmlLabels: false` al livello top così le label diventano SVG `<text>` e
non `<foreignObject>` — WeasyPrint non supporta foreignObject; verificato
sui 15 tipi D8 da `tests/test_mermaid_no_foreignobject.py`). Le altre
tre famiglie di figure (Vega-Lite, DOT, `function`) sono renderizzate
**offline** dal registro `figure_render_service` (vl-convert e sympy in
un processo figlio, `dot` in subprocess, matplotlib in thread), passano
da `svg_normalize.normalize_svg` ed entrano nel PDF come
`<img class="figure-svg" src="data:image/svg+xml;base64,…">` (elemento
sostituito: `max-height` rispettato, nessuna collisione di id fra
figure). Tutte le figure — Mermaid e immagini caricate comprese — sono
avvolte dal partial unico `partials/figure.html.j2` con la didascalia
«Figura N.» (D4). Dettagli in [17 — Figure accademiche](17-visual-figures.md).

## Architettura backend

### `course_lesson_pdf_service.py`

Pure-functions + orchestrazione DB. Diviso in sezioni logiche:

**Markdown → HTML pipeline**

```python
md = MarkdownIt("commonmark", {"html": True, "linkify": True, "breaks": False})
   .enable(["table", "strikethrough"])
_install_math_grammar(md)  # l'unica grammatica del math (B3):
#  .use(dollarmath_plugin, **_DOLLARMATH_OPTIONS)
#     allow_labels=False, double_inline=True, allow_space=False, allow_digits=True
#     (`$ x $` e `$50 e sale a $70` non sono token; `2$^{10}$` resta math)
#  core rule `math_currency_guard` prima di `text_join` (parse E parseInline):
#     i `$..$` che sono importi (`$50/$70`, `5$, 10$`, `US$50 e US$70`,
#     `50$-70$`) tornano testo; `$5$`, `$-1$`, `$0{,}866$` restano math;
#     falsi positivi noti e pre-esistenti (identici a main, follow-up del
#     predicato, non di WP2): «separatore + testo + importo» come
#     `100$, poi 200$` (math «, poi 200»), `5$/h e 10$/h` (math «/h e 10»)
#     e un URL con `?q=$x&y=$z` (math «x&y=»); la stessa grammatica è in
#     `lib/inlineMath.ts`, quindi un ritocco va fatto su entrambi i lati
#  rule inline `math_bsdelim` PRIMA di `escape` (`\(..\)` → math_inline,
#     `\[..\]` → math_inline_double): limitata a `posMax` e al contenuto
#     inline di un paragrafo, quindi fence, code span e HTML block non
#     passano di li'; rifiuta `\[FIG:x\]` e `\[1\]` (lasciati a `escape`)
#  add_render_rule(t, _render_math_token) per TUTTI e quattro i token:
#     math_inline        → <span class="math-inline">{svg|mathml}</span>
#     math_inline_double → <span class="math-inline"> in frase/cella/titolo,
#                          <span class="math-block"> con i delimitatori su
#                          righe proprie (CSS `span.math-block{display:block}`);
#                          chiave SEMPRE `(src, "block")`, mai un <div> in un <p>
#     math_block, math_block_label → <div class="math-block">{svg|mathml}</div>
#  la chiave della mappa `env["math_svg"]` e' `_token_math_key(tok)`
#  = (`_normalize_math_source(content)`, `_MATH_TOKEN_DISPLAY[tok.type]`)

_md_inline_renderer = _install_math_grammar(MarkdownIt("zero"))  # + rule `text` markupsafe
#  campi inline (D9): didascalie di figura e tabella, titoli degli esempi,
#  label delle equazioni, punti chiave, citazioni → `render_markdown_inline`:
#  solo testo escapato (byte-identico a `markupsafe.escape`: `&#34;`, salvo
#  la core rule `normalize` di markdown-it: CRLF/CR → LF, NUL → U+FFFD) e
#  math; niente enfasi, link, code o escape (`\$5` resta `\$5`, `\[1\]`
#  resta `\[1\]`, come nel frontend): senza la rule `code` un titolo come
#  «Uso di `$HOME` e `$PATH`» rende «HOME` e `» come formula (nel CORPO il
#  code span protegge, come su main); il frontend rende gli stessi campi
#  con `InlineMath` (stessa grammatica in `lib/inlineMath.ts`, KaTeX):
#  parità pinnata da `test_frontend_inline_math.py`
```

> Prima di B3 solo `math_inline` e `math_block` avevano una rule nostra:
> `Sia $$E = mc^2$$ la relazione.` usciva come `<div class="math inline">`
> del plugin dentro il `<p>`, senza consultare la mappa SVG. Il test
> `test_lesson_pdf_math.py` pinna le quattro rule su entrambe le istanze,
> i flag, l'ordine delle core rule e delle rule inline (`math_inline` <
> `math_bsdelim` < `escape`) e la fixture `tests/fixtures/
> math_grammar_cases.json` (token e chiavi caso per caso): un upgrade di
> `mdit-py-plugins` che li cambiasse fa fallire i test, non il PDF in
> silenzio (`pyproject.toml`: `mdit-py-plugins<1`; il range
> `markdown-it-py[plugins]>=3.0.0` resta aperto e senza lock in
> `backend/`). Divergenze dal frontend dichiarate: `$ x_0 $` è prosa nel
> PDF (math in remark-math); `$$..$$` in frase è in display style nel PDF
> (text style in KaTeX); il frontend conserva `$50 e sale a $70` come
> math inline (L4, ticket separato); un testo che termina con `\` e ha un
> `$` nei primi due caratteri (`$x$\`) è prosa nel PDF (quirk di
> `dollarmath.is_escaped` in mdit-py-plugins 0.6.1: l'indice negativo
> legge l'ultimo carattere) e math nel frontend — collector e renderer
> usano la stessa istanza, quindi nessun fallback.

> **Firma renderer markdown-it-py**: `add_render_rule` lega la funzione
> come metodo del `RendererHTML` → la firma corretta è
> `(self, tokens, idx, options, env)`, non `(tokens, idx, options, env)`.
> Il **5° parametro è l'`env`**: i renderer leggono da lì la mappa SVG
> pre-renderizzata (`env["math_svg"]`), passata da `render_markdown`.

> Nessun pre-processing testuale: la vecchia `_normalize_math_delimiters`
> (regex `\(..\)` / `\[..\]` → `$..$` / `$$..$$`, applicata due volte)
> riscriveva anche il contenuto dei fence e dei `<pre>` degli esempi
> reiniettati nel corpo (`print("a\[0\]")` → `a$$0$$`, L11). Ora i
> delimitatori LaTeX puri sono una rule della grammatica (`math_bsdelim`) e
> un html_block o un fence non producono token. Il collector
> (`_collect_math_from_content(content, language=…)`) usa le STESSE due
> istanze sugli STESSI testi del renderer: `parse` sul corpo preparato da
> `_prepare_lesson_body` (titoli inclusi, rimandi «Figura N» già riscritti
> nella lingua del corso, ancore sostituite da un HTML block segnaposto
> `<div></div>`, così una formula a cavallo di un'ancora è spezzata dal
> blocco come nel renderer), sulle tabelle, sugli esempi, sugli enunciati
> e sui passaggi; `parseInline` sui campi inline (didascalie via
> `figure_markup.caption_text`, titoli, label, punti chiave e citazioni
> dopo `cite_asset_refs`). La chiave nasce da `_token_math_key`, la stessa
> della rule di render: collector e renderer non possono divergere
> (`test_collector_and_renderer_see_the_same_formulas`, uguaglianza;
> `$a [FIG:x] b$` è la chiave `a Figura 1 b` su entrambi i lati,
> `test_collector_parses_the_rewritten_citations_like_the_renderer`).

**Asset substitution** (`render_lesson_html`): `_prepare_lesson_body`
(unica per renderer e collector) fa `ids_by_kind` → corpo markdown →
`append_uncited_asset_refs` (orfani FIG → TAB → EQ → EX dopo la sintesi)
→ `compute_asset_numbers` (sul corpo NON normalizzato) → etichetta della
sintesi → `normalize_asset_refs` (citazioni in linea → rimandi «Figura
N», una sola ancora per asset); poi il renderer fa
`_build_asset_html_map(asset_numbers=…)` → `_substitute_asset_refs`
(sostituisce le sole ancore con i blocchi, senza righe vuote interne:
`_neutralize_blank_lines` normalizza CRLF/CR a LF, mette U+00A0 nelle
righe vuote dei `<pre>` e rimuove le altre, così ogni blocco resta UN
solo HTML block di markdown-it) → `render_markdown`. `key_takeaways` e
`references[].citation` passano da `cite_asset_refs` (rimandi testuali,
mai blocchi; `_PreparedBody.cite`) e poi da `render_markdown_inline`
(math inline, D9), in questo ordine; il risultato è `Markup` per
l'autoescape del template.

`_normalize_math_source(latex)` rimuove i delimitatori residui (`$$`,
`$`, `\[`, `\]`) e **ribilancia** gli ambienti malformati emessi a volte
dall'AI (`\end{env}` senza `\begin{env}` e viceversa, oppure
allineamento `&`/`\\` fuori da un ambiente → wrappa in `aligned`). Va
applicata in modo IDENTICO sia in raccolta (`_collect_math_from_content`,
che genera la chiave della mappa SVG) sia in lookup (`_render_math`),
altrimenti le chiavi non combaciano.

`_render_math_by_key((src, display), svg_map=…)` è il punto unico di
lookup di una formula (rule di render dei token e `_render_math(latex, *,
display, svg_map=None)` per le equazioni dedicate): se `svg_map` contiene
l'SVG MathJax pre-renderizzato per la chiave lo restituisce; altrimenti
ricade su `_convert_math_to_mathml` e lo dice con
`log.warning("math_render_fallback", reason=…, display=…, latex=…)`, dove
`reason` ∈ {`svg_map_missing` (chiamante senza pre-render), `svg_map_empty`
(CDN MathJax giù), `svg_missing` (drift fra collector e renderer)}. Su una
`MathSvgMap` il miss è anche contato (`misses`).

`_convert_math_to_mathml(latex, *, display)` wrappa `latex2mathml.
converter.convert` ed è solo il **fallback offline**: WeasyPrint NON rende
il MathML, lo stampa come testo piatto e in silenzio (`x^{2}` → «x2»,
`\frac{a}{b}` → «ab»; `test_weasyprint_non_rende_mathml` lo misura). Per
`display="block"` l'attributo `display="inline"` emesso da latex2mathml è
sostituito, non duplicato. In caso di parse error emette un fallback
`<code class="math-error">` col LaTeX grezzo, così la lezione resta
leggibile anche con sintassi malformata.

**Asset substitution**

Ogni `[FIG:..]/[TAB:..]/[EQ:..]/[EX:..]` nel body markdown viene
pre-renderizzato in un blocco HTML e iniettato sulla riga propria
(con righe vuote prima/dopo) prima del rendering markdown-it:

| Tipo | Output |
|---|---|
| `FIG` (visual_assets) con `format="mermaid"` | `<figure class="visual figure figure--lesson figure--mermaid" data-asset-id="…"><div class="figure-body"><div class="mermaid-svg">{svg_pre_renderizzato}</div></div><figcaption class="figure-caption"><span class="figure-label">Figura N.</span> {caption}</figcaption></figure>` (SVG inline byte-identico a prima del branch — A11-L3; il `<div class="mermaid-svg">` porta come ultimo attributo `style="width:Wmm"` dalla banda di leggibilità, D10) |
| `FIG` con `format="vegalite"` / `"dot"` / `"function"` | stesso partial (`figure--vegalite` …) con body `<img class="figure-svg" src="data:image/svg+xml;base64,…" alt="{alt_text}" style="width:Wmm">` (SVG normalizzato da `svg_normalize`, larghezza dalla banda D10); per `function` la didascalia riceve la coda calcolata («Zeri in x = −1, 1. …») |
| `FIG` (visual_assets) con `format="image"` | stesso partial con body `<img class="uploaded-image" src="data:{mime};base64,..." />` — file su filesystem letto via `_resolve_template_asset_url` e embeddato come data URL (no fetch HTTP da Playwright/WeasyPrint) |
| `FIG` legacy (`image_prompt|image_search_query|description`) | stesso partial con body `<div class="placeholder-image">{content}</div>` — testo italico, senza grafica |
| `FIG` renderizzabile senza SVG (render fallito, timeout, `dot` assente) | stesso partial con `<pre class="figure-fallback">{sorgente}</pre>` e `log.error("figure_render_fallback", lesson_code, asset_id, format, reason)` (A23) |
| `TAB` (tables) | `<figure class="table">` con tabella renderizzata da markdown-it |
| `EQ` (equations) | `<figure class="equation"><div class="math-block">{svg_mathjax}</div></figure>` (SVG MathJax pre-renderizzato; fallback MathML). Se l'asset ha `kind` ∈ teorema/proposizione/definizione/lemma/corollario e/o `statement`/`proof`, diventa `<figure class="equation theorem">` con intestazione localizzata + enunciato + formula + dimostrazione a passaggi (`&#8718;` QED) |
| `EX` (examples) | `<aside class="example">...</aside>` con titolo + corpo markdown |

Asset orfano (riferimento senza definizione): `<div class="missing-asset">`.
Asset `format="image"` ma file mancante sul filesystem:
`<div class="placeholder-image">[immagine mancante: {path}]</div>`.

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

**Copertina ricca + footer running**

La copertina (`.cover`, prima pagina, `page-break-after: always`) è
costruita da `render_lesson_html`, che passa al template i metadati di
corso/lezione e il `teacher_name` risolto da `materialize_lesson_pdf`.
Elementi, dall'alto:
- **accent-bar** colorata (`primary_color` del template);
- **codice lezione localizzato** `lesson.code_label` — prodotto da
  `_format_lesson_code_label(lesson_code, labels)`: il codice `M1.L1`
  diventa **"Modulo 1 - lezione 1"** (it) / **"Module 1 - lesson 1"**
  (en); se il codice non matcha `^M\d+\.L\d+$` (lezioni di verifica /
  codici legacy) viene ritornato invariato;
- **titolo lezione** (`lesson.title`);
- **course-title**: titolo del corso in corsivo, con i **CFU** in coda
  se valorizzati (`course.cfu` + label `CFU`/`ECTS`);
- **cover-meta**: `Docente: {teacher_name}` (label `Docente`/`Instructor`)
  se il corso ha un assegnatario.

I **CFU** vengono da `course.cfu`; il **docente** è
`User.full_name` dell'assegnatario `course.assignee_user_id` (risolto via
`db.get(User, ...)` in `materialize_lesson_pdf`); le label sono
localizzate da `_labels_for(language)` in base a `course.language_code`.

> **Nome organizzazione**: il riferimento all'org in pagina è dato dai
> **loghi running** del template (nessuna riga di testo col nome org in
> copertina). Il CSS prevede una classe `.org-name` ma il template
> attuale non la renderizza.

Il **footer** è anch'esso *running* (`position: running(pageFooter)` →
margin-box `@bottom-left` a piena larghezza) e quindi ripetuto su **ogni**
pagina. Riga sinistra (`.pf-meta`): `course.title · code_label ·
lesson.title` + (se presenti) `· {cfu} CFU` + `· Docente: {teacher}`.
Riga destra (`.pf-right`): `{counter(page)} / {counter(pages)} · Generated
by Avatar4University` (il credito prodotto resta su ogni pagina, non
rimovibile).

**Pre-render delle figure**

`_prerender_visual_assets_for_lesson(content, *, language)` (alias
storico `_prerender_mermaid_for_lesson`) delega a
`figure_render_service.render_figure_map(assets, language=…)`, l'unico
punto dell'export con `asyncio.to_thread`, `asyncio.wait_for` e semaforo
(`render_svg_map` ne è la proiezione `{asset_id: svg}`): raggruppa gli
asset con `format` in `RENDERABLE_FORMATS` per formato e chiama **un**
batch per formato (`_render_batch`: `render_figure_batch` se il renderer
lo espone, cioè Mermaid con le metriche misurate, altrimenti
`render_svg_batch` del protocollo), sotto semaforo
(`FIGURE_RENDER_MAX_WORKERS`) e `asyncio.wait_for`
(`FIGURE_RENDER_TIMEOUT_SECONDS`; il batch Mermaid ha un tetto proprio di
almeno 60 s per il costo fisso di Chromium + CDN), con cache LRU degli SVG
(`FIGURE_SVG_CACHE_SIZE`, chiave formato + hash + `THEME_VERSION`) e cache
negativa di 60 s per i render falliti. Non solleva mai: le chiavi assenti
attivano il fallback del partial.

- Mermaid: `MermaidRenderer.render_figure_batch` →
  `mermaid_prerender._prerender_mermaid_batch_sync` (la proiezione `.svg`
  `_prerender_mermaid_to_svg_batch_sync` e gli altri nomi storici restano
  re-esportati da `course_lesson_pdf_service`) apre UNA
  sessione Playwright headless per lezione, carica
  `mermaid@{settings.mermaid_cdn_version}` (default `11.17.2`) con
  l'inizializzazione di `figure_theme` (`htmlLabels: false` al livello
  top, tema D3), espone `window.__renderMermaid(id, code)` (stringa) e
  `window.__renderMermaidMeasured(id, code)` e itera sui sorgenti con UNA
  `page.evaluate` per figura: ne tornano l'SVG e il corpo dei testi
  misurato nella stessa pagina (`MEASURE_SVG_FONT_PX_JS`: `getComputedStyle`
  su `text`/`tspan` con testo proprio, host fuori schermo, filtro solo su
  `display:none`), cioè `MermaidPrerender(svg, metrics)`; una misura
  fallita lascia `metrics=None` con `mermaid_font_measure_failed` e la
  figura resta valida. Mermaid 11.17.2 emette ancora `style="max-width: <px>px;"`
  sull'SVG: `_strip_mermaid_max_width` resta in vigore (fixture
  `tests/fixtures/mermaid11_flowchart.svg`). Costo tipico: ~1 s di
  startup browser + ~50-200 ms per diagramma. Se la lezione non ha
  Mermaid, niente browser viene avviato.
- Vega-Lite: `vl_convert.vegalite_to_svg` nel processo figlio
  (`figure_compute.isolated.run_isolated`, ~0,3-0,5 s di spawn + import
  alla prima chiamata), `$schema` v6 e `VEGALITE_THEME_CONFIG` imposti dal
  registro; DOT: `dot -Tsvg` in `subprocess.run` senza shell con `cwd`
  vuoto e ambiente minimo (~50 ms); `function`: `figure_function_service`
  (numpy + matplotlib in thread, sympy nel figlio con timeout
  `FIGURE_FUNCTION_TIMEOUT_SECONDS`). I tre passano da
  `svg_normalize.normalize_svg` (prologo rimosso, scansione che rifiuta
  `<script>`/`<foreignObject>`/`<image>`/href esterni, radice riscritta
  in px).

La mappa `{asset_id → RenderedFigure(svg, metrics)}` di
`render_figure_map` (le metriche del testo stanno accanto all'SVG, che non
viene mai riscritto; `render_svg_map` ne è la proiezione `.svg`, e una
stringa nella mappa vale `RenderedFigure.from_svg`, metriche lette da
`svg_normalize.svg_base_font_px`) è poi passata a `render_lesson_html`
(`visual_svg_map`; `mermaid_svg_map` resta accettato e fuso) e da lì a
`_build_asset_html_map` → `_render_visual_asset_block` →
`figure_markup.render_figure_html`, che produce il blocco `<figure
class="visual figure …">` con il body del formato e la didascalia
«Figura N.». Se il rendering fallisce (rete, sintassi, timeout, `dot`
assente), il partial emette `<pre class="figure-fallback">` con il
sorgente e `log.error("figure_render_fallback", …)` (A23).

**Larghezza delle figure dalla banda di leggibilità (D10, D11).** Il
corpo del testo più piccolo di ogni figura deve cadere fra 8 e 11 pt nella
dispensa (e nel web) e fra 10 e 14 pt nelle slide e nei frame video
(`figure_scale.READABILITY_BANDS_PT`). `_render_visual_asset_block` calcola
con `figure_scale.fit_figure_width_mm` la larghezza a cui renderla e la
scrive come ULTIMO attributo `style="width:Wmm"` del corpo della figura
(`<div class="mermaid-svg">` nella dispensa, `<img class="figure-svg">` o
`<img class="mermaid-svg">` altrove): l'SVG resta byte-identico (golden
della catena Mermaid in `test_lesson_pdf_figures.py`), la figura sta nei
margini del testo, niente pagina dedicata, niente landscape.

- **Box.** Dispensa: `_compute_template_margins_cm` (chiamata PRIMA di
  `_build_asset_html_map`) dà `figure_box_w_mm` (larghezza del contenuto:
  170 mm su A4 con margine 20, Letter e A3 seguono il foglio) e
  `figure_box_h_mm = max_figure_height_cm · 10` (242 mm sul template di
  default, sotto i 248,7 mm di SVG che entrano in una pagina intera
  misurati con WeasyPrint); per Mermaid si sottraggono i 2 mm di padding del wrapper (168
  mm). Slide: il `FigureBox` della pagina resa (D12, sotto), 255 mm per
  l'altezza che la pagina lascia.
- **Politica.** Gli SVG fluidi (Mermaid, `width="100%"`) riempiono il box
  e scendono al tetto della banda; gli `<img>` con dimensione intrinseca
  (Vega-Lite, DOT, `function` normalizzati) partono da scala 1 e crescono
  solo fino al fondo della banda; mai oltre il box (larghezza per difetto
  al centesimo); senza testo scala naturale. Il flowchart della fixture
  v11, che prima di D10 usciva a 13,3 pt in dispensa e a 19,9 pt nelle
  slide, va a 140,76 mm e 11 pt in dispensa e a 179,15 mm e 14 pt nelle
  slide.
- **Corpo del testo.** Mermaid: misurato in Chromium accanto all'SVG
  (`font_source="measured"`); Vega-Lite, DOT e `function`: letto dagli
  attributi (`parsed`) o dal `<style>` (`root_rule`) con
  `svg_base_font_px`, che serve anche da ripiego per Mermaid quando la
  misura manca. Una lettura `unresolved` (dichiarazione fuori grammatica,
  valore che dipende dal contesto: `rem`, `var()`, `calc()`, parole
  chiave, `em`/`%` senza antenati) o metriche assenti usano la costante
  di formato (`FALLBACK_BASE_FONT_PX`: Mermaid 14, Vega-Lite 11, DOT
  40/3, `function` 12 px) con `font_source="constant"`.
- **Log e report.** Ogni fit produce `figure_fit` (info) e una
  `FigureFitEntry` nel `fit_report` di `render_lesson_html` /
  `render_slides_html`; `figure_fit_out_of_band` (warning,
  `in_band=False`) quando la banda è irraggiungibile nel box (la figura
  prende la larghezza del box); `figure_font_fallback` (warning) per ogni
  figura calcolata sulla costante e per un Mermaid non misurato;
  `figure_fit_skipped` senza viewBox o con box degenere. A fine lezione
  `figure_fit_report` (info) riassume totale, in banda, l'elenco fuori
  banda con corpo e provenienza del font e `font_fallback` (figure il cui
  `in_band` è un'ipotesi): è l'input del gate editoriale D13.
- **Salti pagina nella dispensa.** La crescita degli `<img>` fino al
  fondo della banda rende più alte le figure che a scala 1 hanno testo
  sotto 8 pt: il blocco di un DOT con archi a 5 pt (`dot_tiny`) passa da
  79,7 a 123,7 mm, quello di un Vega-Lite con etichette a 4,5 pt
  (`vl_mixed_small`) da 84,6 a 128,7 mm. Se la figura più alta non entra
  nello spazio rimasto, WeasyPrint la porta prima alla pagina seguente e
  la dispensa può guadagnare una pagina. Misura del 17 settembre 2026
  contro `df7af78` (prima di WP3): su 31 figure in 3 posizioni del testo
  (93 dispense) il branch ha una pagina in meno in 24 casi e una in più
  in nessuno, senza figure frammentate o fuori dal content-box; in una
  scansione mirata (5 figure in 16 posizioni, 80 dispense) `dot_tiny` e
  `vl_mixed_small` aggiungono una pagina in 3 posizioni ciascuna (6 casi
  su 80, sempre da 2 a 3 pagine), gli altri 74 restano uguali. È il costo
  accettato della banda D11. La crescita massima (fondo della banda /
  corpo naturale, mai oltre il box) è pinnata da
  `test_figure_scale.py::test_intrinsic_img_below_the_band_grows_at_most_to_the_band_floor`
  e, sul PDF reso, da
  `test_lesson_pdf_figure_text_size.py::test_intrinsic_img_grows_at_most_to_the_band_floor`.
- **Modelli degli editor fuori banda.** Misura del 17 settembre 2026 sui
  57 modelli degli editor (`test_frontend_figure_templates.py`: 18 DOT,
  24 Vega-Lite, 15 Mermaid) resi dal registro e passati a
  `fit_figure_width_mm` con il box della dispensa (170 × 242 mm, 168 per
  Mermaid) e con quello della pagina asset-only delle slide (255 ×
  86,6 mm, titolo e didascalia su una riga); il corpo coincide con quello
  misurato nel PDF di WeasyPrint. Tutti hanno `in_band=False`,
  `figure_fit_out_of_band` e la voce nel `figure_fit_report`.

  | Formato | Modelli | Fuori banda in dispensa (8-11 pt) | Fuori banda nelle slide (10-14 pt) |
  | --- | --- | --- | --- |
  | DOT | 18 | 2: pipeline 7,7, network 7,17 | 1: layers 9,75 |
  | Vega-Lite | 24 | 0 | 22: violin 7,28; barsHorizontal, bubble, heatmap, barsRanked, lollipop 9,44; gli altri 16 a 9,25 |
  | Mermaid | 15 | 4: timeline 5,6, treemap 4,78, radar 6,08, gantt 3,72 | 12: flowchart 7,79, state 9,04, er 7,74, mindmap 9,3, timeline 5,75, treemap 6,62, pie 9,27, xychart 6,87, radar 4,21, sankey 8,59, gantt 5,65, quadrant 5,89 |

  In dispensa il vincolo è sempre la larghezza: sono diagrammi larghi
  (gantt 1280 × 172, timeline 1190 × 598, treemap 996 × 371, radar
  940 × 700, i DOT pipeline 626 × 104 e network 739 × 187) che a 168-170 mm
  scendono sotto 8 pt. Nelle slide il vincolo è l'altezza in 34 casi su
  35 (solo gantt è limitato dalla larghezza): il box di pagina è alto
  86,6 mm, quindi i diagrammi verticali (state 104 × 380, flowchart
  347 × 441, er 372 × 444) e quelli già alti a scala 1 non possono
  crescere fino a 10 pt. Un Vega-Lite alto 292 px (77,3 mm) con etichette
  a 8,25 pt dovrebbe arrivare a 93,6 mm per portarle a 10 pt; quadrant
  (500 × 500) e radar (940 × 700) sono nella stessa condizione. Fuori dai
  modelli, la figura `function` di prova di
  `test_lesson_pdf_figure_text_size.py` esce nelle slide a 8,52 pt per la
  stessa ragione. Nessuna correzione del tema né del template: i modelli
  restano come sono e il report li consegna al gate editoriale D13 (WP5),
  che li deve riconoscere come fuori banda noti e non come regressioni.

**LaTeX pre-rendering (MathJax → SVG)**

Lo stesso pattern Playwright è usato per le formule: `_MATHJAX_RENDERER_HTML`
(`build_mathjax_renderer_html(version=…)`, pin `settings.mathjax_cdn_version`,
default `3.2.2`, stesso schema di `mermaid_prerender`) è un mini-documento
che carica `mathjax@{versione}/es5/tex-svg.js` da CDN (jsdelivr) ed
espone `window.__renderMath(latex, display)`; la pagina installa
`block_external_requests` PRIMA di `set_content` e può contattare solo il
CDN (SEC-1, come Mermaid e validatore). Config chiave:
- `svg: { fontCache: 'none' }` — ogni SVG è autonomo (glyph come path
  inline, niente `<defs>/<use>` con id condivisi che collidono incollando
  molte formule nello stesso documento);
- `startup: { typeset: false }` — niente auto-render: si usa
  `MathJax.tex2svg(latex, { display })` in modo programmatico;
- su parse error MathJax emette un nodo `merror` → `__renderMath`
  ritorna `null` e il chiamante ricade su MathML.

`_prerender_math_to_svg_batch_async(items)` renderizza una lista di
`(latex, display)` con UNA sola sessione headless (attende
`window.__mathReady`, timeout 20s — `tex-svg.js` è ~1MB, più ampio del
Mermaid). Come per il Mermaid è wrappata da `_..._sync` /
`_..._batch` che girano in un thread con loop dedicato
(`ProactorEventLoop` su Windows, unico a supportare il `subprocess_exec`
di Playwright).

Nessun retry del launch Chromium né del caricamento della pagina MathJax:
un `pw.chromium.launch` fallito (binario assente, crash all'avvio)
propaga l'eccezione a `materialize_lesson_pdf` e la lezione ricade
sull'auto-retry del worker (`lesson_pdf_auto_retry`, fino a
`COURSE_LESSON_PDF_AUTO_RETRY_MAX`), che ripete l'intero export; un
`window.__mathReady` scaduto (CDN lenta o giù) non solleva ma degrada
tutte le formule della lezione a MathML con un solo
`mathjax_renderer_setup_failed` e il summary `lesson_pdf_math_fallbacks`.
**Follow-up dichiarato (B3, non fatto in WP2)**: un retry del solo launch
dentro il batch (stessa sessione, una ripetizione) andava deciso sul grep
dei log di produzione (`mathjax_renderer_setup_failed`, errori di
`launch`) che non era disponibile; resta da misurare e, solo se la
frequenza è > 0, da aggiungere qui e in `mermaid_prerender` (che oggi non
lo ha nemmeno). Vedi anche «Cosa NON fa questa iterazione», punto 7.

`_collect_math_from_content(content, language=…)` raccoglie le formule
per parse (le equazioni dedicate più ogni token math delle due istanze
markdown-it sui campi di `_iter_math_sources`, che sono i testi del
renderer: corpo di `_prepare_lesson_body` con i rimandi riscritti nella
lingua del corso e le ancore sostituite da un HTML block segnaposto, coda
dopo `cite_asset_refs`) e `_prerender_math_for_lesson(content,
language=…)` restituisce sempre una `MathSvgMap` (un `dict` `{(latex,
display): svg}` con `requested` = chiavi raccolte e `misses` = lookup
falliti, anche vuota), passata a `render_lesson_html(math_svg_map=...)`
insieme al corso della stessa lingua. Le chiavi assenti (CDN MathJax
giù, o drift fra collector e renderer) ricadono su MathML in
`_render_math_by_key`/`_convert_math_to_mathml` con un warning
`math_render_fallback` per formula; `materialize_lesson_pdf`,
`materialize_lesson_slides_pdf` e `render_slides_to_png` (video) chiudono
con un solo evento per lezione, `log.error("lesson_pdf_math_fallbacks",
lesson_code=…, count=…, requested=…, rendered=…, sample=…)`
(`_log_math_fallbacks`), che in produzione misura quante formule un PDF
ha perso e quante chiavi in più raccoglie il collector per parse. Una
formula a cavallo di un tag asset (`$a [FIG:x] b$`) è raccolta con il
rimando già riscritto («a Figura 1 b»), la stessa chiave che cerca il
renderer; una formula a cavallo di un'ancora su riga propria è spezzata
dal blocco su entrambi i lati e non produce chiavi. Lato template
(`lesson_pdf.html.j2`): `.math-block svg` è
centrato con `max-width:100%`, `.math-inline svg` conserva il
`vertical-align` MathJax per allinearsi alla baseline del testo,
`span.math-block { display: block; }` (anche nelle slide) manda a capo il
`$$..$$` con i delimitatori su righe proprie dentro un paragrafo; la
regola `math { font-size: 1.05em }` resta solo come fallback per il
MathML.

**Immagini caricate (`format="image"`)**

Niente pre-rendering: il file è già un'immagine binaria sul filesystem
(jpg/png/webp, salvato via `file_service.save_upload_image` in
`{UPLOAD_ROOT}/lesson_assets/{course_id}/{uuid}.{ext}`).

Il renderer `_render_visual_asset_block` (commit `92d5f37`) riusa
`_resolve_template_asset_url(asset.content)` — la stessa funzione usata
per loghi/sfondi del template PDF: legge i bytes dal filesystem e li
embedda nell'HTML come `data:{mime};base64,...`. WeasyPrint consuma le
data URL senza fetch HTTP, il PDF risultante ha l'immagine inline.

CSS lato template (`lesson_pdf.html.j2`): `.uploaded-image` ha
`max-width: 100%` e `max-height: {{ max_figure_height_cm }}cm` per
evitare overflow di pagina. Lo stesso pattern è replicato in
`lesson_slides_pdf.html.j2` per le slide che referenziano un
`[FIG:...]` di tipo image.

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
(formule come SVG MathJax inline — fallback MathML —, Mermaid SVG
inline). Niente `window.__renderingDone`, niente CDN da attendere in
fase di rendering finale (le CDN MathJax/Mermaid sono usate solo nello
step di pre-render Playwright a monte).

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
1. risolve il template (lesson.pdf_template_id → org default) e carica
   l'`Organization` + il docente (`course.assignee_user_id` →
   `User.full_name`) per la copertina/footer;
2. `_prerender_visual_assets_for_lesson(content_raw, language=…)` →
   `{asset_id: RenderedFigure(svg, metrics)}` via `render_figure_map`
   (una sessione Playwright per lezione solo se ci sono Mermaid;
   Vega-Lite, DOT e `function` offline);
3. `_prerender_math_for_lesson(content_raw)` → `{(latex, display): svg}`
   (una sessione Playwright MathJax `tex-svg`, solo se la lezione
   contiene formule);
4. `render_lesson_html(... visual_svg_map=..., math_svg_map=...,
   teacher_name=...)` → HTML completo con SVG MathJax (fallback MathML) +
   figure nel partial unico (numerazione «Figura N.» calcolata sul corpo
   markdown prima della sostituzione degli asset, orfane accodate dopo la
   sintesi) + CSS @page con sfondo edge-to-edge;
5. `generate_pdf_bytes(html=html)` → bytes via WeasyPrint;
6. salva il PDF (`remote_storage.upload_bytes(pdf_key(rel), ...)`);
7. aggiorna `pdf_path`, `pdf_template_id`, `pdf_generated_at`.

Le formule sono raccolte da `_collect_math_from_content` con la stessa
grammatica del renderer: equazioni dedicate (`equations[].latex` +
passaggi `proof[].latex`) e i token math (`$..$`, `$$..$$`, `\(..\)`,
`\[..\]`) del parse dei campi resi — corpo assemblato (introduction,
`## title`, sections, summary), tabelle e didascalie, esempi e titoli,
`statement`/`explanation`/`label`/`proof[].text`, didascalie delle
figure, `key_takeaways`, `references[].citation`. Dedup per chiave
`(latex_normalizzato, display)`; gli importi `$50` declassati dalla core
rule non sono mai raccolti.

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

### `course_module_pdf_service.py` — bundle per-modulo

Aggregazione "post-export": presuppone che TUTTE le lezioni del modulo
abbiano già il PDF della pipeline richiesta in stato `ready` con il
file presente sul filesystem. Niente render: solo concatenazione di
file già scritti dai worker per-lezione.

Pipeline supportate via `PdfKind = Literal["content", "slides", "speech"]`.
Le tre primitive per-kind (status, path, filename per download) sono
centralizzate in `_lesson_pdf_status` / `_lesson_pdf_path` /
`_lesson_pdf_filename`. Il resolver del path assoluto è invece unico
(`pdf_absolute_path`) perché tutti e tre i PDF condividono la stessa
root: il `kind` è già codificato nel suffisso del nome file
(`{lesson_id}.pdf`, `_slides.pdf`, `_speech.pdf`).

Pre-condition (helper `_ensure_all_pdfs_ready`):
- modulo non vuoto → altrimenti `409 module_has_no_lessons`
- tutte le lezioni (escluse le verifiche, `is_assessment`) in stato
  `ready` con `*_pdf_path` valorizzato → altrimenti
  `409 module_pdfs_not_ready` con `meta.missing_lessons = [lesson_id, ...]`
- file presente sullo storage attivo per ognuna: la presenza è
  verificata al momento della lettura (`remote_storage.download_bytes` /
  RETR su OVH, read locale altrimenti); una lezione mancante solleva
  `404 module_pdf_file_missing`

Due funzioni pubbliche, entrambe sincrone in-memory:

- `merge_module_pdfs(*, kind, course, module) -> bytes` — concatena
  con `pypdf.PdfWriter.append(...)` (preserva metadati, font, immagini
  incorporate, bookmark di partenza di ogni PDF lezione). Ogni lezione
  riceve un outline-item (segnalibro) col titolo. Ordinamento lezioni:
  parsing del `lesson_code` regex `^M\d+\.L(\d+)$` con zero-padding,
  fallback all'ordine inserito.
- `zip_module_pdfs(*, kind, course, module) -> bytes` — stdlib
  `zipfile.ZipFile(mode="w", compression=zipfile.ZIP_DEFLATED)`,
  un'entry per lezione con `arcname` = filename utente-friendly
  identico al download per-lezione singola.

Helper filename: `module_merged_filename(...)` e
`module_zip_filename(...)`, nel formato `{course.title} —
{module.module_code} {module.title} ({label}).pdf|.zip`, con suffisso
`(Contenuti)` / `(Slide)` / `(Discorso)` derivato da `_kind_label(kind)`.

Dipendenza: `pypdf>=4.0` (aggiunto a `pyproject.toml`). La libreria
stdlib `zipfile` non aggiunge dipendenze.

> **Aggregazione a livello CORSO**: oltre al per-modulo, il service
> espone anche `merge_course_pdfs` / `zip_course_pdfs` (con
> `course_merged_filename` / `course_zip_filename`, suffisso "corso
> completo") che concatenano/zippano tutte le lezioni di tutti i
> moduli (ZIP: una sottocartella per modulo). Vedi il punto sotto in
> "out of scope" — questa parte è ormai implementata.

## API endpoints (4 + 2 nuovi)

Tutti sotto `/orgs/{org_id}/courses/{course_id}/...`.

| Metodo | Path | Permesso | Effetto |
|---|---|---|---|
| `POST` | `/lessons/{lid}/pdf/export?pdf_template_id={uuid?}` | `course:generate` | Set lezione `pdf_status='pending'`. **202**. |
| `POST` | `/lessons-pdf/export-all?pdf_template_id={uuid?}` | `course:generate` | Set tutte le lezioni esportabili `pending`. **202**. |
| `POST` | `/lessons-pdf/cancel-all` | `course:generate` | Annulla `pending`/`processing` → `failed`. |
| `GET` | `/lessons/{lid}/pdf/download` | `course:view` | Scarica il PDF. 404 se `pdf_status != ready`. |
| `GET` | `/modules/{mid}/lessons-pdf/download-merged` | `course:view` | Bundle modulo: un solo PDF concatenato di tutte le lezioni. |
| `GET` | `/modules/{mid}/lessons-pdf/download-zip` | `course:view` | ZIP modulo: un PDF per lezione. |

Tutti i POST restituiscono `CourseOut` aggiornato. Il GET restituisce
`FileResponse(application/pdf)` con `Content-Disposition: attachment;
filename="..."` (filename costruito da `course.title — lesson_code
lesson.title.pdf`, sanitizzato).

Gli analoghi 2 endpoint batch-per-modulo esistono anche per la pipeline
slide (`/modules/{mid}/lessons-slides-pdf/{download-merged,download-zip}`)
e discorso (`/modules/{mid}/lessons-speech-pdf/{download-merged,download-zip}`).
Vedi anche `course_module_pdf_service.py` qui sotto.

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
(~150-200MB RAM per ~2-5s totali). I render CPU-bound delle altre figure
(vl-convert, sympy, matplotlib, `dot`) condividono il semaforo
`FIGURE_RENDER_MAX_WORKERS=2` con le anteprime `render-function`
dell'editor. Aumentare solo dopo test di carico.

### Setup iniziale

```powershell
# 1. Playwright Chromium (per pre-render mermaid → SVG)
.venv/Scripts/python.exe -m playwright install chromium

# 2. WeasyPrint runtime (Windows local-dev)
winget install tschoonj.GTKForWindows
# Su Linux/Docker: gestito dal Dockerfile (libpango/libharfbuzz/...)

# 3. Graphviz (figure DOT): binario `dot` nel PATH o in GRAPHVIZ_DOT_PATH
brew install graphviz        # macOS;  apt-get install graphviz su Debian/Ubuntu
# Su Linux/Docker: già nel Dockerfile. vl-convert, sympy e matplotlib
# arrivano da pyproject (pip install .).
```

### Dipendenze pyproject

```toml
[project]
dependencies = [
  # ...
  "playwright>=1.49.0",       # pre-render mermaid + MathJax (LaTeX) → SVG
  "weasyprint>=63.0",         # HTML → PDF
  "latex2mathml>=3.77",       # fallback offline: LaTeX → MathML
  "jinja2>=3.1.4",
  "markdown-it-py[plugins]>=3.0.0",
  # Figure accademiche (doc 17)
  "vl-convert-python>=1.9",   # Vega-Lite → SVG senza browser
  "altair>=6,<7",             # solo per il file JSON dello schema Vega-Lite v6
  "jsonschema>=4.18",         # validazione delle spec contro lo schema
  "sympy>=1.13",              # forme esatte delle figure `function`
  "matplotlib>=3.9",          # disegno delle figure `function`
]
```

## Cosa NON fa questa iterazione (out of scope)

1. ~~**PDF aggregato del corso**~~ — ora **implementato**: bundle
   per-modulo (`merge_module_pdfs`/`zip_module_pdfs`) e per-corso
   (`merge_course_pdfs`/`zip_course_pdfs`) in `course_module_pdf_service.py`.
   Resta per-lezione il rendering vero e proprio (i bundle sono
   pura concatenazione post-export).
2. **Object storage** (S3/MinIO) — il filesystem locale è la persistence
   layer; per scalare oltre il single-host serve uno step esplicito.
3. **Versioning storico**: il file viene **sovrascritto** ad ogni nuovo
   export. `pdf_template_id` snapshotta solo il template dell'ultima
   generazione.
4. **Rendering offline parziale**: il pre-render carica
   `mermaid@{settings.mermaid_cdn_version}` (default `11.17.2`) e
   `mathjax@{settings.mathjax_cdn_version}` (default `3.2.2`) da CDN
   (jsdelivr) in Playwright, l'unica origine ammessa dalla guardia di
   rete delle pagine headless. Se la macchina del
   worker non ha internet: i diagrammi Mermaid falliscono (fallback
   testuale `<pre class="figure-fallback">` con `log.error`); le formule
   ricadono su `latex2mathml` → MathML, che però WeasyPrint stampa solo
   come testo (pedici/apici/frazioni degradati ma leggibili). Le figure
   Vega-Lite, DOT e `function` sono renderizzate offline e non degradano.
   Il resto del PDF viene comunque generato. Il rendering finale
   WeasyPrint è completamente offline (no CDN per fonts, math o
   styling). Soluzione future: bundle locale di mermaid.esm / MathJax o
   pre-rendering via CLI + node.
5. **Streaming SSE** del progresso al client: la UI fa polling.
6. **Diff-detection** automatico tra `content_raw` modificato e PDF già
   generato: il badge resta `ready` finché l'utente non clicca "Rigenera
   PDF" esplicitamente.
7. **Retry del launch Chromium nel pre-render MathJax/Mermaid**: il batch
   apre Chromium una volta per lezione senza ripetere il launch; un launch
   fallito fa fallire l'export (auto-retry del worker sull'intera
   lezione), una CDN scaduta degrada le formule a MathML. Il retry del
   solo launch era condizionato al grep dei log di produzione
   (`mathjax_renderer_setup_failed`, errori di `launch`), non disponibile
   in questa iterazione: follow-up da decidere sui dati, non implementato.

---

## Slide (Fase 4)

Pipeline parallela e indipendente dal PDF testo. Stato per-lezione su
`course_lesson.slides_pdf_*` (8 colonne, migration 0020 + FK migrata a
`slide_templates` in 0022). Path file: suffisso `_slides.pdf`.

### Stack

Stesso del PDF testo — **WeasyPrint** + **Jinja2** + **Playwright** per
pre-render mermaid **e** LaTeX → SVG (MathJax), più il registro
`figure_render_service` per Vega-Lite, DOT e `function`
(`_prerender_mermaid_for_slides`, nome storico, oggi tutti i formati:
fonde gli asset di Fase 3 e i `new_assets` di Fase 4 e delega a
`render_figure_map`). Differenze principali:
- Layout: A4 **portrait single-column block-flow** (mantenuto dal feedback utente: niente layout 16:9 landscape — il PDF deve essere comodo da stampare e leggere)
- Template: `slide_templates` (16:9 originariamente per avatar video, ora unificato anche per il PDF slide via migration 0022 con campi aggiunti `margin_mm` + `background_opacity_pct`)
- Asset rendering: stesso pattern di Fase 3 (visual/table/equation/example) + supporto a `new_assets` di Fase 4
- Math: riusa il pre-render MathJax di `course_lesson_pdf_service` via
  `_prerender_math_for_slides`, che fonde le equazioni/tabelle/esempi
  delle Dispense (`content_raw`) con i nuovi asset di Fase 4
  (`slides_raw.new_equations|new_tables|new_examples`) e delega a
  `base_pdf._prerender_math_for_lesson`. Fallback MathML identico.
- Copertina: `lesson_label` = `base_pdf._format_lesson_code_label(...)`
  ("Modulo X - lezione Y" localizzato), badge `cfu_label`
  (`CFU`/`ECTS`), `course.cfu`, `teacher` (`User.full_name`
  dell'assegnatario).

### Slide split (bullet+asset → 2 pagine)

`render_slides_html` espande le slide con `references_assets ≠ []` AND
`bullets ≠ []` (solo i bullet fanno splittare: la prosa resta con la
figura, come nella slide dedicata di Fase 4) in **due pagine consecutive**
con lo stesso titolo:

- **pagina N**: tag "Lezione X" + titolo + body + bullet (niente asset)
- **pagina N+1**: stesso titolo + asset isolato (niente bullet, niente body)

Vantaggio: gli asset (specialmente Mermaid) hanno l'intero body a
disposizione per il rendering, niente competizione verticale. La
numerazione pagina viene ricalcolata sulla sequenza espansa.

Le slide pure-bullet (no asset) o pure-asset (no bullet) restano
single-page. Il rendering procede in due passi per slide: risoluzione
degli asset e decisione di split, poi — per ogni pagina resa — il budget
verticale della figura calcolato da `slide_geometry.page_figure_budget`
sul contenuto reale della pagina (D12, sotto), e solo allora il rendering
dei blocchi.

### Rendering delle figure nelle slide

Tutte le figure (Mermaid, Vega-Lite, DOT, `function`) vengono incapsulate in `<img>` con **data-URI base64** anziché inserite come SVG inline (A8). Motivo: nel contesto slide PDF, un SVG inline con attributi `width="X" height="Y"` espliciti emessi da Mermaid (10.9.x come 11.x) ignora il vincolo CSS `max-height` e sborda dal body. Un `<img>` invece è un replaced element con aspect ratio intrinseca, e `max-width + max-height` gli applicano scaling proporzionale corretto. La regola unica `.slide-asset .figure-svg, .slide-asset .mermaid-svg, .slide-asset .uploaded-image { max-height: var(--figure-h); … }` vale anche per le immagini caricate (prima tagliate da `overflow: hidden` oltre 80 mm). Ogni figura passa dal partial `partials/figure.html.j2` con `variant="slide"`: etichetta «Figura.» **senza numero** (A2), didascalia a 8pt, fallback `<pre class="figure-fallback">` con CSS dedicato.

**Box della figura per pagina (D12, 16 settembre 2026).** Il cap fisso di 80 mm non vale più per le figure: `slide_geometry.page_figure_budget(title, body, bullets, n_blocks)` sottrae ai 120 mm del `.slide-body` tag (budget 1,5 × 9 pt), titolo (10,37 mm per riga stimata), prosa (6,65 mm per riga), bullet (5,24 mm per riga + 2,5 mm), margini degli asset (4 + 2 mm) e 3 mm di safety, e divide il resto in parti uguali fra i blocchi asset della pagina resa; `_render_visual_asset_block(figure_budget=)` ne ricava il box dell'immagine con `image_box` (didascalia stimata sul testo reale: etichetta, didascalia dell'autore, coda calcolata di `function`), lo emette come `style="--figure-w: 255.0mm; --figure-h: Hmm"` sul `<figure>` (dopo `aria-label`) e dentro quel box applica la banda di leggibilità 10-14 pt (D10, `style="width:Wmm"` sull'`<img>`). Il template legge il box con `var()`: `.slide-asset .figure-body { width: var(--figure-w) }`, `max-height: var(--figure-h)` sulle immagini e sul `<pre>` di fallback. Equazioni ed esempi non ricevono box: la regola generica `.slide-asset svg, .slide-asset img` porta `max-height: var(--figure-h, 80mm)`, quindi un SVG MathJax fuori da un blocco figura resta al cap di 80 mm di prima (un `aligned` di 8 righe con frazioni è alto 107 mm, una `pmatrix` di 20 righe 132 mm: senza cap sbordavano o, con i bullet nel video, sparivano dalla pagina); le formule dei teoremi mantengono il loro cap di 40 mm. Righe stimate da `estimate_lines`, limite superiore per classi di carattere (calibrato `real ≤ stima ≤ real + 1` sui `LineBox` di WeasyPrint per sei famiglie). Pagina asset-only con titolo su una riga: 86,6 mm di immagine (era 80); slide dedicata di Fase 4 (titolo + prosa su 3 righe): 61,3 mm senza taglio; pavimento 25 mm con `slide_figure_box_exhausted` quando il testo da solo supera il body (la pagina sborda per i bullet, non per la figura); più blocchi nella stessa pagina (solo legacy) dividono il budget, `slide_figure_box_shared`. Il sorgente del fallback `<pre>` è troncato in Python alle righe che entrano (`truncate_fallback_source`, marcatore «…», log `figure_fallback_truncated`) perché WeasyPrint ignora `max-height` sui blocchi frammentati dal fondo pagina. Il costo di una riga sorgente è l'altezza della sua riga resa: dal settimo giro il `<pre>` delle slide non va a capo (paragrafo seguente), quindi una riga sorgente è una riga resa. Prima il costo era il numero di righe rese in `white-space: pre-wrap`, stimato prima con gli spazi collassati a 0,30 em (una riga DOT da 176 caratteri con 22 spazi valeva 1 riga contro 2, il sorgente troncato a 25 righe ne rendeva 46 e il `<pre>` sbordava di 55 mm in WeasyPrint, con la didascalia fuori pagina, e di 272 px nel frame video), poi, dal terzo al sesto giro, a colonne da 0,605 em con spazi e tab conservati, token lunghi a `ceil(2 · w / riga)` e larghezze per lingua e per script. **Altezza delle righe e lingua del corso (quinto giro, 17 settembre 2026).** Nel container (WeasyPrint 69, `fonts-noto-core` e `fonts-noto-cjk`) la riga del `<pre>` non è sempre 1,3 × 7 pt = 3,210 mm: WeasyPrint allinea sulla riga base del font scelto per la lingua di `<html lang>` (`fc-match monospace:lang=xx`) e la riga cresce a 1,3 em + |ΔA−D|/2 quando i glifi vengono da un font con ascendente meno discendente diverso (DejaVu Sans Mono 0,692, Noto Sans CJK 0,872, Noto Serif Kannada 0,200, Noto Serif Tibetan 0,117). Con un corso zh-cn, ja o ko anche un sorgente tutto ASCII rendeva righe da 3,432 mm, e con ideogrammi o Hangul in qualunque lingua: 25 righe non entravano negli 82,3 mm utili, il «…» finiva a cavallo del bordo (+1,49 mm) o spariva dal PDF (+2,33 mm con due bullet; 34 marcatori tagliati nella matrice lingue × contenuti). Misure su 44 lingue × 31 script: fino a 3,818 mm con kn, 3,921 con bo, 4,142 (1,6775 em) con ja e tibetano. Ora `truncate_fallback_source(source, box, language)` somma altezze, non righe: 1,3 em per riga solo quando la riga sorgente è tutta nell'insieme base (`_MONO_BASE_RE`: ASCII, Latin-1, Latin Extended-A, greco e cirillico di base, punteggiatura, frecce, operatori e filetti d'uso comune; 669 caratteri verificati uno per uno a 3,210 mm nel container e in locale) e la lingua è neutra; altrimenti `fallback_tall_line_budget` = 1,70 em (4,198 mm: 1,3 + (0,917 − 0,117)/2, ogni combinazione di font fra Noto Serif Tibetan e Noto Sans Symbols). Profili di lingua (`_mono_profile`): neutra dove `fc-match` dà DejaVu Sans Mono (it, en, ru, ar, el, …) e per vi (Noto Sans Mono: righe da 3,210 mm sull'insieme base salvo greco, cirillico, ∏, ∑ e ∫, che in una riga pura passano a DejaVu Sans Mono con righe da 3,314 mm, `_MONO_VI_NOT_BASE_RE`), «altra» per i 39 sottotag primari di `_MONO_TALL_LANGS` (fra cui ja, ko, zh, hi, kn, th, he, lo, bo e sh, Noto Mono con 154 caratteri base su righe da 3,324 mm), per i tag con regione ber-ma, ku-iq, ku-ir, mn-cn, pa-pk, ps-af, ps-pk, ti-er, ti-et e per i codici di tre lettere. Le lingue che nella matrice di 302 lingue × 75 campioni superavano 1,70 em hanno la loro riga (`_MONO_TALL_LINE_EM`, sesto giro): mn-cn 1,83 em (riga base in Noto Sans Mongolian, 4,50 mm con il tibetano), tcy 1,76 em (4,317 mm con gli ideogrammi); ogni riga con mongolo tradizionale vale 1,83 em in ogni corso. Anche il marcatore paga la riga alta. Effetto: la pagina asset-only tiene 24 righe più «…» in italiano e 18 più «…» con ja, zh-cn, ko, hi. Limiti dichiarati: script con A−D fuori banda accanto a font all'altro estremo sulla stessa riga (Nastaliq 1,308 o mongolo 1,164 accanto a Siddham −0,030 o Myanmar Serif −0,021). Nessun cambio di font, `line-height`, `72ch` o margini: le slide già in DB rendono uguali salvo l'altezza delle figure e il fallback. I frame video ricevono lo stesso HTML (`enable_split=False`: senza split bullet e figura si dividono i 120 mm senza tagli). Oracoli in `tests/test_slide_figure_geometry.py`.

**Fallback senza a capo (settimo giro, 17 settembre 2026).** Decisione del settimo giro di verifica e deviazione dichiarata dal piano del branch, che prevedeva la troncatura su una stima delle righe a capo: nelle slide e nei frame video il `<pre>` di fallback è in `white-space: pre` e non più in `pre-wrap`; una riga di sorgente è una riga resa e `overflow: hidden` taglia a destra le righe più larghe dei 255 mm del box; `max-height: var(--figure-h)` resta come cintura. Perché: la troncatura in Python ha bisogno delle righe rese, e sei giri di verifica hanno battuto ognuno la stima delle righe a capo in `pre-wrap` con un caso nuovo di Pango (WeasyPrint) o di Chromium: spazi collassati, spazi e tab conservati, U+2028 e U+2029 (a capo in Pango, non in Chromium), profili di lingua con righe più alte, ASCII e simboli larghi per lingua e accanto a un altro script (V6-2: dopo Hangul, kana, ebraico o sillabario canadese «—», «…», «‰» e i filetti escono dal font mono fino a 1,342 em; nel container 37 righe sottostimate su 590 per it e vi, `WP_RIGHE_OLTRE_CLIP` fino a +41,7 mm e «…» invisibile) e infine le regole UAX #14 di Pango (V6-1: niente a capo prima di `) ] } ! ? , . : ; /` né dopo `( [ {`, anche con spazi in mezzo; 22 sottostime su 36 casi in italiano, «voce . . . .» ripetuto su una riga stimato 25 righe e reso in 46, `<pre>` fuori dal body di 55 mm e didascalia sparita dal PDF). Ogni correzione aggiungeva una tabella di larghezze di un motore di testo; senza a capo il conteggio è esatto nei due motori e non dipende da font, larghezza o lingua. Costo: nel solo fallback delle slide, cioè nel percorso d'errore di una figura non resa (log `figure_render_fallback`), le righe più larghe del box si leggono fino al bordo destro; la dispensa resta in `pre-wrap` nel flusso di pagina. A capo misurati su tutti i caratteri Cc, Cf, Zs, Zl e Zp e sull'intero BMP (riga «a<carattere>b» nel `<pre>` del template, nel container e in locale) e sui piani 1 e 2 (nel container): `\n`, `\r\n` e `\r` in entrambi i motori (l'HTML normalizza CR), U+2028 e U+2029 solo in WeasyPrint, NEL, FF e VT in nessuno dei due. La divergenza si risolve contando la riga come spezzata e riscrivendo CRLF, CR, U+2028 e U+2029 come `\n` nel testo del `<pre>` (`truncate_fallback_source`, anche quando tutto entra), così i due motori rendono le stesse righe; i NUL, che il parser HTML scarta (in testa al `<pre>` insieme all'a capo che li segue), sono tolti prima di contare. Rimossi da `slide_geometry` lo stimatore mono (`estimate_lines(mono=True)`, `_mono_rows`, `_mono_char_em`), le tabelle di larghezza per lingua e per script (`_MONO_WIDE_ASCII_*`, `_WIDE_ASCII_*`, `_MONO_ASCII_CROSS`, `_MONO_WIDE_SCRIPT_RE`, `_MONO_CJK_FULL_RE`, le costanti di colonna e di tab), il profilo CJK (ja, ko e zh sono «altra», come già per l'altezza) e `SlideGeometry.fallback_w_mm`; `estimate_lines` resta per titoli, prosa, bullet e didascalie con la calibrazione di prima; ⇐, ⇒, ⇔ e ∅ tornano nell'insieme base di vi (erano esclusi solo per la larghezza, 1,2 em, e hanno righe da 3,210 mm). Effetto: la slide da 60 righe corte resta a 24 righe più «…»; le righe DOT da 176 caratteri, le parole brevi da 200 colonne e le righe con 170 spazi o 22 tab iniziali passano da 12 a 24 righe più «…»; nel container le slide dei casi V6-1 e V6-2 rendono in WeasyPrint esattamente le righe del testo troncato, senza righe sotto il clip, con didascalia e «…» visibili. Oracoli in `tests/test_slide_figure_geometry.py`: corpus avversario di 66 casi (`pre_sources` della fixture) in 12 lingue (it, vi, hi, th, he, ar, ru, ja, zh-cn, ko, kn, bo) con righe ESATTE nei `LineBox` di WeasyPrint e nelle righe di Chromium e altezza stimata non inferiore a quella resa; le stesse sorgenti nelle slide senza sbordi, con didascalia e con «…» reso e visibile dove il sorgente è troncato; nessun inchiostro oltre il bordo destro del body nel PDF e nel frame video; controprove con la regola `pre-wrap` di prima (più di 40 righe e didascalia persa sui casi V6-1) e senza `overflow: hidden` (testo oltre il bordo in entrambi i motori).

**Prima run ideografica (ottavo giro, 17 settembre 2026).** Il conteggio delle righe era esatto, l'altezza no: in WeasyPrint una riga del `<pre>` dipendeva anche dall'ordine degli script. Con gli stessi caratteri «分བོད» era alta 2,006 em in un corso it e 2,293 in bo, «x分བོད» 1,588 e 1,300; il sorgente troncato su 1,70 em per riga lasciava fino a 11,3 mm (it) e 19,8 mm (bo) di righe sotto il clip del `<pre>` e il «…» fuori dal PDF (V7-1: `cjk_tib` in 14 lingue su 14, `cjk_emoji`, `hangul_pua`, `kana_my` in bo e my). Meccanismo: Pango 1.56 (`apply_baseline_shift`, uguale in 1.58) allinea le run di font diversi sulla baseline dello script della prima run della riga, cioè del primo carattere con script reale (Common, Inherited e Unknown prendono lo script che segue; Pango itemizza fra due a capo, quindi decide la riga da sola). Per Han, Hangul, Hiragana, Katakana, Bopomofo, Tangut, Nüshu e Khitan HarfBuzz usa la baseline ideografica e, senza tabella BASE, la sintetizza dal discendente: la run di Noto Serif Tibetan (−1,068) sale di 0,994 em rispetto a Noto Sans CJK (−0,074). WeasyPrint prende altezza e baseline della riga Pango e le allinea alla strut del `<pre>`: riga = 1,3 em + |c_strut − c_testo|, con c = (alto − basso)/2 dei rettangoli logici spostati. Con la baseline romana o sospesa non c'è spostamento (HarfBuzz dà 0 e 0,6 em a ogni font senza BASE, e le BASE del container hanno gli stessi valori), per questo le righe con un id ASCII in testa restavano nel budget. Correzione in `slide_geometry`: `_ideographic_lead(line)` trova il primo carattere che decide (`_IDEO_SCRIPT_RE`, blocchi che coprono gli script ideografici di Unicode 15, 16 e 17; una lettera con script reale fuori da `_NOT_REAL_LETTER_RE` decide per la baseline romana) e quelle righe costano `SlideGeometry.fallback_ideo_line_budget` = 2,46 em (6,075 mm), anche in mn-cn e tcy. Il valore è un limite analitico: c_testo di un insieme di run sta fra i valori delle coppie (prima run, altra run); sulle coppie delle 321 facce del container (BASE e ripieghi di HarfBuzz 10.2) arriva a 1,1405 e la strut di qualunque font scende a −0,015, quindi la riga resta sotto 2,4555 em; la prova di carico (2.702 righe per lingua in it, bo, dz, my, kn, ja, mn-cn, tcy, te) arriva a 2,2935. Costo, solo nel fallback delle slide: le righe con un ideogramma, un kana o un Hangul in testa valgono 2,46 em invece di 1,70 (la slide asset-only in bo tiene 12 righe `藏文…` più intestazione e «…», 17 con l'id ASCII in testa; il caso zh-cn della fixture passa da 47 a 51 righe omesse). Nel container, dopo la correzione, le slide `cjk_tib` (it) e `cjk_emoji`, `hangul_pua` (bo) non hanno righe sotto il clip, con didascalia e «…» visibili. Restano i limiti dichiarati per gli script con A−D fuori banda (per esempio i geroglifici egizi, A−D 0,998, a 1,74 em in un corso bo o dz). Oracoli in `tests/test_slide_figure_geometry.py`: corpus `pre_sources` di 71 casi (i cinque V7-1, con il controllo `ascii_tib`) in 13 lingue (aggiunta my), con l'altezza controllata riga per riga in WeasyPrint e nelle slide; classificatore contro gli script di GLib, la tabella che usa Pango, su tutto Unicode; controprova con il modello di prima (katakana in testa e birmano in bo: righe oltre la stima e ultima riga sotto il clip, anche in locale).

```python
# svg_normalize.svg_to_data_uri — re-esportata da course_lesson_slides_pdf_service
def svg_to_data_uri(svg: str) -> str:
    payload = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"
```

Encoding base64 (non URL-encoding) perché l'SVG contiene molti `"` (attributi viewBox, xmlns, ...) che romperebbero un `src="..."` HTML. La stessa funzione è usata dal PDF della dispensa per le figure Vega-Lite/DOT/`function` (Mermaid resta inline lì).

### Asset resolver

`_resolve_asset_for_slide(asset_id, content_raw, new_assets)` cerca
nell'ordine:
1. `content_raw["visual_assets"]` (Fase 3) — `kind="visual"`
2. `content_raw["tables"]` — `kind="table"`
3. `content_raw["equations"]` — `kind="equation"`
4. `content_raw["examples"]` — `kind="example"`
5. `slides_raw["new_assets"]` (Fase 4) — `kind="new_visual"` (ricicla il rendering visual)

Mirror della logica frontend `lib/slides.resolveAsset()`.

### API endpoints (4 + 2 nuovi)

| Metodo | Path | Permesso | Effetto |
|---|---|---|---|
| `POST` | `/lessons/{lid}/slides-pdf/export?pdf_template_id={uuid?}` | `course:generate` | Set `slides_pdf_status='pending'`. **202**. |
| `POST` | `/lessons-slides-pdf/export-all?pdf_template_id={uuid?}` | `course:generate` | Tutte le lezioni esportabili. |
| `POST` | `/lessons-slides-pdf/cancel-all` | `course:generate` | Annulla `pending`/`processing`. |
| `GET` | `/lessons/{lid}/slides-pdf/download` | `course:view` | Scarica `application/pdf`. |
| `GET` | `/modules/{mid}/lessons-slides-pdf/download-merged` | `course:view` | Bundle modulo (PDF unico delle slide). |
| `GET` | `/modules/{mid}/lessons-slides-pdf/download-zip` | `course:view` | ZIP modulo (1 PDF slide per lezione). |

### Frontend

Tab "Slide" (`CourseLessonSlidesView.tsx`) ha bottoni primary "Esporta PDF" / "Scarica PDF" / "Aggiorna PDF" (con stale logic via `isSlidesPdfStale`) e kebab "Rigenera PDF". Dialog `LessonSpeechPdfExportDialog`... wait, refuso — dialog è `LessonSlidesPdfExportDialog.tsx` che usa `slideTemplatesApi.list(orgId)` (template avatar+slide).

### File rilevanti

```
backend/app/services/course_lesson_slides_pdf_service.py   # render + materialize + slide split + pre-render delle figure (render_figure_map)
backend/app/services/figure_render_service.py              # registro dei renderer (doc 17): Mermaid, Vega-Lite, DOT, function
backend/app/services/figure_markup.py                      # partial unico delle figure + didascalia «Figura N.» / «Figura.»
backend/app/templates/partials/figure.html.j2              # partial D4 condiviso da dispensa, slide e frame video
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

Solo **WeasyPrint** + **Jinja2** — niente pre-render di figure (il
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

Cover con badge "DISCORSO" + `code_label` (`_format_lesson_code_label`
→ "Modulo X - lezione Y" localizzato) + titolo lezione + corso (con
`cfu_label`/`course.cfu` e `teacher_label`/`teacher` =
`User.full_name` dell'assegnatario) + meta-row con durata totale e word
count.

Footer pagina: `{course.title} · {lesson.title} · pageNum/total`.

> Niente pre-render MathJax/Mermaid in questa pipeline: il discorso è
> prosa pura, senza formule LaTeX né asset visivi.

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

### API endpoints (4 + 2 nuovi)

| Metodo | Path | Permesso | Effetto |
|---|---|---|---|
| `POST` | `/lessons/{lid}/speech-pdf/export?pdf_template_id={uuid?}` | `course:generate` | Set `speech_pdf_status='pending'`. **202**. |
| `POST` | `/lessons-speech-pdf/export-all?pdf_template_id={uuid?}` | `course:generate` | Tutte le lezioni esportabili. |
| `POST` | `/lessons-speech-pdf/cancel-all` | `course:generate` | Annulla. |
| `GET` | `/lessons/{lid}/speech-pdf/download` | `course:view` | Scarica `application/pdf`. |
| `GET` | `/modules/{mid}/lessons-speech-pdf/download-merged` | `course:view` | Bundle modulo (PDF unico del discorso). |
| `GET` | `/modules/{mid}/lessons-speech-pdf/download-zip` | `course:view` | ZIP modulo (1 PDF discorso per lezione). |

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

### Figure (doc 17)

Il pre-render delle figure di dispensa e slide legge il blocco «Figure
accademiche» di `config.py` (replicato in `.env.example` e
`docker-compose.prod.yml`; tabella completa in
[04 — Configuration](../04-configuration.md)):

```env
FIGURE_VEGALITE_ENABLED=true          # kill-switch per formato (Mermaid non disattivabile)
FIGURE_DOT_ENABLED=true
FIGURE_FUNCTION_ENABLED=true
MERMAID_CDN_VERSION=11.17.2           # pin unico: validatore Playwright + pre-render PDF/video
MATHJAX_CDN_VERSION=3.2.2             # pin MathJax tex-svg: pre-render delle formule PDF/video
FIGURE_RENDER_TIMEOUT_SECONDS=20      # tetto del batch di figure di una lezione (Mermaid: almeno 60 s)
FIGURE_FUNCTION_TIMEOUT_SECONDS=10    # calcolo simbolico nel processo figlio (oltre: valori approssimati)
FIGURE_RENDER_MAX_WORKERS=2           # render CPU-bound concorrenti (worker + anteprime render-function)
FIGURE_SVG_CACHE_SIZE=256             # cache LRU degli SVG in memoria
FIGURE_SVG_MAX_BYTES=1500000          # oltre, l'SVG è rifiutato (fallback)
FIGURE_DOT_MAX_CHARS=12000            # limite del sorgente DOT
GRAPHVIZ_DOT_PATH=                    # vuoto = `dot` cercato nel PATH
```

**Rivalidazione degli asset Mermaid già in DB** (livello L5 della
regressione zero di A11): `backend/scripts/revalidate_mermaid_assets.py`
è un dry-run in sola lettura che, per ogni asset `format="mermaid"` in
`content_raw.visual_assets` e `slides_raw.new_assets`, applica la pulizia
del pre-render, il gate statico D8 (tipo ammesso, niente `%%{init`,
niente HTML nelle label) e il render con Mermaid 11 (conteggio dei
`<foreignObject>`), e riporta totale / ok / da correggere più il numero
di lezioni con asset non citati nel corpo (che con la numerazione
«Figura N.» compaiono in coda, A12):

```bash
# dalla cartella backend/, Postgres raggiungibile (JWT_SECRET in ambiente se manca .env)
python -m scripts.revalidate_mermaid_assets                    # tabella markdown
python -m scripts.revalidate_mermaid_assets --skip-render      # solo gate statico, senza Chromium né rete
python -m scripts.revalidate_mermaid_assets --course "Analisi" --show-ok
python -m scripts.revalidate_mermaid_assets --format csv > mermaid.csv
# sul server (dc = alias docker compose di produzione)
dc exec -T backend python -m scripts.revalidate_mermaid_assets --format csv > mermaid.csv
```

Gli asset «da correggere» (`mermaid_type_not_allowed`,
`mermaid_init_directive`, `mermaid_html_in_label`, `render_failed`,
`foreignobject`) finiscono nel PDF come fallback `<pre>`: il gate statico
li blocca solo alla rigenerazione o alla modifica di quel singolo asset,
mai all'edit del testo (A15). Esito della run di consegna in
[17 — Figure accademiche § Verifiche e consegna](17-visual-figures.md#14-verifiche-e-consegna).
