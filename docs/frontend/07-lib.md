# Frontend 07 — `lib/`

Utility riusabili dal codebase.

---

## `src/lib/permissions.ts`

Mirror dei codici permessi/ruoli del backend, più etichette IT per la UI.

### Esporta

- `P` (oggetto `as const`) — tutti e 20 i codici, mirror di `app/core/permissions.py`:
  - `MEMBER_VIEW`, `MEMBER_INVITE`, `MEMBER_ASSIGN_ROLE`, `MEMBER_REMOVE`,
    `MEMBER_AVATAR_VIEW`.
  - `TEMPLATE_SLIDE_MANAGE`, `TEMPLATE_PDF_MANAGE`.
  - `PERMISSION_MANAGE`, `ORG_TRANSFER_CREATOR`, `ORG_UPDATE`.
  - `COURSE_CONFIG_MANAGE`.
  - `COURSE_VIEW`, `COURSE_VIEW_ALL`, `COURSE_CREATE`, `COURSE_ASSIGN`,
    `COURSE_EDIT`, `COURSE_DELETE`, `COURSE_GENERATE`, `COURSE_SAVE_DRAFT`,
    `COURSE_DUPLICATE`.
- `type PermissionCode = (typeof P)[keyof typeof P]`.
- `ALL_PERMISSIONS: PermissionCode[]` (= `Object.values(P)`).
- `PERMISSION_LABELS_IT: Record<PermissionCode, string>`.
- `ROLES` (oggetto `as const`):
  - `CREATOR`, `ORG_ADMIN`, `MANAGER`, `MEMBER`.
- `type RoleCode = (typeof ROLES)[keyof typeof ROLES]`.
- `ROLE_CODES: RoleCode[]` (= `Object.values(ROLES)`).
- `ROLE_LABELS_IT: Record<RoleCode, string>`.
- `ROLE_DEFAULT_PERMISSIONS: Record<RoleCode, readonly PermissionCode[]>` —
  mirror di `ROLE_DEFAULT_PERMISSIONS` lato backend. Usato dalla
  `RolePermissionsBox` per mostrare cosa concede ogni ruolo in fase di
  invito / cambio ruolo. `creator` punta direttamente a `ALL_PERMISSIONS`;
  `member:avatar:view` è incluso nei default di `org_admin` e `manager` ma
  NON di `member` (che ha solo `course:view`). Va tenuto allineato a mano col
  BE quando si aggiunge un permesso.
- `PERMISSION_CATEGORIES: ReadonlyArray<{ key: string; permissions: readonly
  PermissionCode[] }>` — raggruppamento dei codici per area (`members`,
  `templates`, `organization`, `coursesView`, `coursesManage`), usato dalla
  `RolePermissionsBox` per disporre i permessi in sezioni. La label di ogni
  `key` è risolta via i18n.

> Modificando i codici lato backend, aggiornare anche questo file (compresi
> `ROLE_DEFAULT_PERMISSIONS` e `PERMISSION_CATEGORIES`).

---

## `src/lib/errors.ts`

### `interface ApiErrorBody`

Forma normalizzata degli errori dal backend:
`{ code, message, request_id?, meta? }`.

### `extractApiError(err: unknown): ApiErrorBody`

- Se `err instanceof AxiosError` e `response.data` ha `message` →
  ritorna il body.
- Se è solo errore di rete → `{ code: "network_error", message }`.
- Altrimenti `{ code: "unknown_error", message: "Errore inatteso." }`.

Usato da tutte le pagine per produrre testi di errore UI uniformi.

---

## `src/lib/format.ts`

### `formatDate(value)`

Formatta come `dd/MM/yyyy` (locale `it-IT`). Accetta `string | Date | null
| undefined`. Vuoto se nullish.

### `formatDateTime(value)`

Formatta come `dd/MM/yyyy HH:mm` (locale `it-IT`).

### `uploadsUrl(path)`

Identità (i path sono già `/uploads/...` e vengono serviti dallo stesso
origin). Restituisce `undefined` per nullish o `path` se assoluto/path.

> Funzione helper "in attesa di crescere": placeholder per futuro CDN o
> path absoluti.

---

## `src/lib/logger.ts`

Wrapper console + invio errori al backend in produzione.

### Funzioni esportate

```ts
logger.debug(msg, meta?)
logger.info(msg, meta?)
logger.warn(msg, meta?)
logger.error(msg, meta?)
```

In dev usa `console.*`. In prod (`import.meta.env.DEV` falsy):
- `info`/`warn`/`error` inoltrano a `POST /api/v1/system/log-client`
  via `apiClient` (rate-limited 60/min, swallow di errori).

Usato da `ErrorBoundary` e in altri punti chiave.

---

## Mirror del backend per le figure (16 settembre 2026)

Quattro moduli sono **copie** di altrettanti moduli Python: stessa
grammatica, stesso algoritmo, stessi arrotondamenti. Non hanno import a
runtime, così i test del backend li caricano con
`node --experimental-strip-types` e li confrontano con l'originale caso per
caso su una fixture condivisa. Modificando un lato va modificato l'altro:
la fixture è il contratto.

| Modulo frontend | Originale backend | Fixture condivisa |
|---|---|---|
| `lib/assetRefNormalize.ts` | `services/asset_ref_normalize.py` | `backend/tests/fixtures/asset_ref_normalize_cases.json` |
| `lib/figureNumbering.ts` | `services/figure_numbering.py` | `backend/tests/fixtures/figure_numbering_cases.json` |
| `lib/inlineMath.ts` | `render_markdown_inline` (istanza `zero` del PDF) | parità di token in `backend/tests/test_frontend_inline_math.py` |
| `lib/figureFormats.ts` (parte «scala») | `services/figure_scale.py`, `svg_normalize` | `backend/tests/fixtures/figure_scale_cases.json` |
| `lib/chainLayout.ts` | `services/figure_compute/chain_layout.py` | `backend/tests/fixtures/chain_layout_cases.json` |

---

## `src/lib/assetRefNormalize.ts`

Rimandi testuali e ancore degli asset (`FIG`, `TAB`, `EQ`, `EX`).

```ts
type AssetKind = "FIG" | "TAB" | "EQ" | "EX";
type AssetNumbers = Partial<Record<AssetKind, ReadonlyMap<string, number>>>;
type ReferenceFn = (kind: AssetKind, idLower: string, n: number) => string;
interface NormalizeOptions { numbers: AssetNumbers; reference: ReferenceFn }

normalizeAssetRefs(markdown: string, opts: NormalizeOptions): string
citeAssetRefs(text: string, opts: NormalizeOptions): string
```

- `normalizeAssetRefs` riscrive ogni citazione in linea di un tag
  «gestito» (kind presente in `numbers` e id normalizzato con un numero)
  nel rimando prodotto da `reference` («Figura 2», «Tabella 1», «Lemma 2»:
  senza punto, la punteggiatura dell'autore resta); tiene UNA sola ancora
  per chiave `KIND:id_lower` e, se una chiave è citata senza ancora, ne
  inserisce una su riga propria dopo il blocco della prima citazione.
  Fence e blocchi `$$…$$` chiusi sono unità opache; dentro codice e math i
  tag sono citazioni, mai ancore; un tag non gestito resta byte-identico.
- Guardia «parola-etichetta»: se la parola dell'etichetta precede già il
  tag sulla stessa riga, a meno di spazi e su parola intera, il rimando
  emette il solo numero («La figura [FIG:x]» → «La figura 1»). La parola è
  il testo che `reference` stessa mette prima del numero, cioè la chiave
  i18n del rimando: vale in italiano e in inglese senza elenchi a parte.
- `citeAssetRefs` fa la sola sostituzione, senza inserire né rimuovere
  ancore: serve alla coda (punti chiave, riferimenti), che non rende
  blocchi; la guardia vale anche lì.
- I numeri sono un **dato** calcolato prima, sul corpo non normalizzato;
  la funzione è idempotente e il testo fuori dai tag è byte-identico. Le
  regex usano classi esplicite (`[ \t]`, `[0-9]`), mai `\s`/`\d`, per
  avere lo stesso esito in JavaScript e in Python.
- Limiti dichiarati (gli stessi del backend, pinnati in fixture): blocchi
  indentati di 4 spazi non riconosciuti come codice, fence con prefisso e
  code span multi-riga non riconosciuti, guardia «parola-etichetta» sulla
  parola completa, quindi il plurale non corrisponde («Le figure [FIG:a]»
  → «Le figure Figura 1») e nemmeno la parola separata dal tag da un segno
  di punteggiatura («La figura, [FIG:a]»); «a meno di spazi» è il solo
  `[ \t]`, non la classe larga `WS`, quindi uno spazio unificatore
  (U+00A0) fra parola e tag disattiva la guardia e la ripetizione
  sopravvive; la regola è lessicale e non distingue il verbo omografo dal
  sostantivo («Il ciclo completo figura [FIG:a]» → «… figura 1», unico
  caso in cui il rimando perde l'etichetta); con la parola incollata al
  tag («La figura[FIG:a]») la cifra resta incollata alla parola («La
  figura1»). Gli ultimi tre hanno 0 occorrenze nell'export reale
  (doc `courses/17-visual-figures.md` §20.3).

Pipeline reale in `LessonContentView`: `appendUncitedAssetRefs` →
`computeAssetNumbers` → `normalizeAssetRefs` sul corpo e `citeAssetRefs`
sulla coda. Il `content_raw` non è mai toccato.

---

## `src/lib/figureNumbering.ts`

Numerazione editoriale, con un contatore **indipendente per kind**
(«Figura 1» e «Tabella 1» convivono).

```ts
citedAssetIds(markdown: string): Array<[AssetKind, string]>
appendUncitedAssetRefs(markdown: string, idsByKind: AssetIdsByKind): string
computeAssetNumbers(markdown: string, idsByKind: AssetIdsByKind): Map<string, number>
assetNumbersByKind(numbers: ReadonlyMap<string, number>): Partial<Record<AssetKind, Map<string, number>>>
equationLabelFamily(eq: EquationLike): "EQ" | "THM"
nonEmptyProofSteps<T extends ProofStepLike>(proof): T[]
stripFigurePrefix(caption: string): string
```

- Il numero è legato all'id, non all'occorrenza: la prima citazione nel
  corpo (introduzione → sezioni → sintesi) assegna N, le ripetizioni
  condividono lo stesso N, un id senza asset non consuma numeri.
- `appendUncitedAssetRefs` accoda gli asset mai citati nell'ordine
  `FIG → TAB → EQ → EX`, saltando gli id che il token non sa trasportare.
- `equationLabelFamily` decide il ramo teorema (statement o passi di
  dimostrazione non vuoti → `THM`, reso «Lemma 2.»); il contatore resta
  quello di `EQ`, perché il tag `[EQ:id]` non trasporta la famiglia.
- `stripFigurePrefix` toglie un prefisso «Figura 3.» già scritto nella
  didascalia: **solo a render**, il dato persistito non cambia.
- `ASSET_REF_RE` e `FIG_REF_RE` sono case-sensitive sul kind e il tag non
  attraversa la riga: `[fig:x]` non è sostituito da nessun renderer e
  numerarlo produrrebbe un numero fantasma.
- Le proiezioni storiche sulle sole figure (`citedFigureIds`,
  `appendUncitedFigureRefs`, `computeFigureNumbers`) restano e danno gli
  stessi risultati di prima.

---

## `src/lib/inlineMath.ts`

```ts
type InlineMathSegment =
  | { kind: "text"; text: string }
  | { kind: "math"; latex: string; display: boolean };

splitInlineMath(text: string): InlineMathSegment[]
```

Grammatica dei campi inline (didascalie di figura e di tabella, label
delle equazioni, titoli degli esempi, titolo/prosa/bullet delle slide,
testo e note del discorso): riconosce **solo** il math e lascia tutto il
resto letterale (niente enfasi, link, code o escape: `\$5` resta `\$5`).
Rispecchia dollarmath con `allow_space=False` (`$ x $` e `$50 e sale a
$70` sono prosa) e `allow_digits=True` (`2$^{10}$` è math), la rule
`\(..\)` / `\[..\]` che rifiuta i tag `\[FIG:x\]` e le citazioni `\[1\]`,
e la guardia anti-importi (`$50/$70`, `5$, 10$`, `US$50 e US$70`). Senza
delimitatori riconosciuti ritorna il solo segmento di testo, identico
all'ingresso. Il consumatore è `components/shared/InlineMath.tsx`, che
monta ogni formula con `katex.render` via ref (mai HTML da stringa).

---

## `src/lib/figureFormats.ts` — scala e misura del testo

Oltre a formati, etichette e sanificazione degli SVG, il modulo porta la
parte «scala» (D10/D11), mirror di `figure_scale.py`.

```ts
const MM_PER_PX = 25.4 / 96;
const PT_PER_PX = 0.75;
const READABILITY_BANDS_PT = { lesson: [8, 11], slide: [10, 14] };
const MERMAID_FALLBACK_FONT_PX = 14;

svgIntrinsicBox(svg: string): SvgBox | null
svgIntrinsicSize(svg: string): SvgSize | null          // proiezione di svgIntrinsicBox
fitFigureWidthMm(input: FigureFitInput): FigureFit | null
formatMm(value: number): string
measureSvgFontPx(svg: string): SvgFontMetrics | null
```

`FigureFitInput` è `{ vbW, vbH, baseFontPx, boxWMm, boxHMm, variant?,
intrinsicWPx? }` e `FigureFit` è `{ widthMm, scale, textPt, inBand }`.

- **Politica di scala.** Gli SVG fluidi (`intrinsicWPx` assente: Mermaid,
  `width="100%"`) riempiono il box e vengono ridotti al tetto della banda;
  gli `<img>` intrinseci partono da scala 1, crescono solo fino al fondo
  della banda e scendono al tetto se sopra; mai oltre il box. Senza testo
  (`baseFontPx` nullo) vale la scala naturale; banda irraggiungibile →
  larghezza massima del box e `inBand: false`. Un box `null` è «nessun
  vincolo»: sul web la colonna la applica il CSS `min(100%, Wpx)`.
- **Arrotondamenti.** Larghezza per DIFETTO al centesimo di mm
  (`Math.floor`), scala e corpo half-up: stesso ordine di operazioni del
  Python, mai `toFixed` sui numeri confrontati. `formatMm` stampa come
  `figure_scale.format_mm` (`.2f` senza zeri finali).
- **`measureSvgFontPx`** misura nel DOM il corpo dei testi di contenuto
  (`text`/`tspan` con nodo di testo proprio non vuoto e non
  `display:none`) e ritorna `{ min, median, count }` in unità utente: è il
  gemello funzionale di `MEASURE_SVG_FONT_PX_JS` del pre-render backend
  (`mermaid_prerender.py`), stessa selezione, stesso filtro, stessa
  mediana, con parità provata in Chromium. Host fuori schermo, mai `visibility:hidden` (azzererebbe i
  testi), rimosso in `finally`; `null` se la misura fallisce e il
  chiamante ripiega su `MERMAID_FALLBACK_FONT_PX`.

Consumatore principale: `MermaidDiagram`, che mette
`width: min(100%, Wpx)` su un wrapper senza padding.

`LESSON_REFERENCE_BOX_MM` (`[168, 242]`, mirror di
`figure_scale.LESSON_REFERENCE_BOX_MM`) è il box della dispensa su A4 con
margine di 20 mm meno il padding del wrapper Mermaid;
`SLIDE_REFERENCE_BOX_MM` (`[255, 86.6]`, mirror di
`course_lesson_slides_pdf_service.reference_slide_figure_box_mm`) è quello
della slide di riferimento, e `REFERENCE_BOX_MM` li indicizza per
`FigureVariant`. Servono SOLO a decidere la direzione di una catena
quando il box vero non è noto (D15), ciascuno sulla propria superficie,
mai come larghezza di resa.

---

## `src/lib/chainLayout.ts` — direzione delle catene lineari

Mirror di `services/figure_compute/chain_layout.py` (D15).

```ts
const MIN_CHAIN_NODES = 3;
const VERTICAL_OF = { LR: "TB", RL: "BT" };

verticalChainVariant(source: string): string | null
isLinearChain(nodes: string[], edges: [string, string][]): boolean
```

`verticalChainVariant` ritorna il sorgente Mermaid con il SOLO token di
direzione cambiato quando il sorgente è una catena lineare dichiarata in
orizzontale, `null` altrimenti: nessun altro byte si muove, nemmeno
spaziatura, terminatori di riga o commenti. Il riconoscimento è
conservativo — intestazione `flowchart|graph LR|RL` come prima riga utile,
con il `;` finale facoltativo come nel corpo (frontmatter YAML e commenti
`%%` prima sono ammessi, una direttiva `%%{…}%%` no), nessun
`subgraph`/`end`/`direction`, archi solo semplici
(`-->`, `---`), grado entrante e uscente al più 1, un solo componente
connesso, almeno tre nodi — perché la variante costa una resa e nel dubbio
non vale la pena: nel dubbio `null`.

`SPACE` è la classe degli spazi, l'INTERSEZIONE fra quelli di JavaScript e
quelli di Python: `String.trim()` toglie anche U+FEFF e `str.strip()`
anche U+001C-U+001F e U+0085, quindi senza una classe esplicita i due lati
sceglievano direzioni diverse. Qui `strip`, `rstrip` e `firstWord`
sostituiscono `trim()`, `trimEnd()` e `split(/\s+/)`.

Consumatore: `MermaidDiagram`, che rende la variante e tiene quella con il
corpo più grande. Parità con il Python sui 36 casi di
`chain_layout_cases.json`, eseguiti dai due lati
(`backend/tests/test_frontend_figure_layout.py::test_frontend_chain_layout_matches_the_shared_fixture`).
