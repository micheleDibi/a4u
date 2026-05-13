# 06 — Frontend (corsi)

Pagine, componenti e pattern per il dominio Corsi.

## Routing

```
/orgs/:orgId/corsi                  → CoursesListPage
/orgs/:orgId/corsi/nuovo            → CourseEditorPage (mode='create')
/orgs/:orgId/corsi/:courseId        → CourseEditorPage (mode='edit')
```

## `CoursesListPage.tsx`

Tabella paginata con search, filtro status, badge stato, click → editor.
Pulsante "Nuovo corso" gated da `course:create`.

## `CourseEditorPage.tsx`

Editor tab-based. Layout principale: **`<Tabs>`** con 8 voci (in modalità edit;
in create solo le prime 2):

1. **Informazioni di base** — title, objectives, argomenti chiave, categoria,
   lingua, assegnatario, **CFU + summary dimensionamento** (tutto consolidato in
   un'unica Card)
2. **Inquadramento didattico** — 7 tassonomie (categoria spostata in Base)
3. **Documenti** — solo edit
4. **Architettura** — solo edit, AI Generate/Approve + view CRUD
5. **Struttura lezioni** — solo edit, gated su `course.status >= architecture_approved`. AI batch/per-modulo + edit manuale.
6. **Contenuti lezioni** — solo edit, gated su `course.status >= lessons_structure_approved`. AI batch/per-lezione (Fase 3) + glossario + **export PDF testo** (§7).
7. **Slide** — solo edit, gated su `course.status ∈ {content_ready, content_approved, slides_*, speech_*, published}`. AI batch/per-lezione (Fase 4) + edit manuale + **export PDF slide**.
8. **Discorso** — solo edit, gated su `course.status ∈ {slides_ready, slides_approved, speech_*, published}`. AI batch/per-lezione (Fase 5) + edit manuale + **export PDF discorso**.

Tab persistente per courseId in localStorage (`course-editor-tab:{courseId}`). Tab disabled (con relativo gating su course.status) restano visibili ma greyed-out finché la pre-condizione a monte non è soddisfatta.

### Lock setup didattico

`course.didactic_setup_confirmed_at` (migration 0017) blocca i Tab 1+2 in read-only quando confermato. Solo creator/org_admin/platform_admin possono fare unlock. Il lock è applicato anche server-side in `course_service.update_course`.

### Auto-save

Debounce 1500ms su `draft` change. Stable diff via `JSON.stringify(draft) === JSON.stringify(baseline)`.
`updateMut` per fields, `assigneeMut` separato (permission diversa).

### Polling

`refetchInterval` su `useQuery` — copertura completa pipeline AI:
- 5s se `course.status === 'architecture_pending'` (mostra `architecture_progress`)
- 5s se almeno un documento è in `pending`/`processing`
- 5s se almeno un modulo è in `lessons_structure_status ∈ {pending, processing}` (Fase 2)
- 5s se almeno una lezione è in `content_status ∈ {pending, processing}` (Fase 3)
- 4s se almeno una lezione è in `pdf_status ∈ {pending, processing}` (§7)
- 5s se almeno una lezione è in `slides_status ∈ {pending, processing}` (Fase 4)
- 4s se almeno una lezione è in `slides_pdf_status ∈ {pending, processing}` (Fase 4 PDF)
- 5s se almeno una lezione è in `speech_status ∈ {pending, processing}` (Fase 5)
- 4s se almeno una lezione è in `speech_pdf_status ∈ {pending, processing}` (Fase 5 PDF)
- 5s se `glossary_status ∈ {pending, processing}` (§10.1)
- altrimenti `false`

### Sticky footer

Auto-save indicator + pulsante Save (mostra "Salva e continua" in create, "Salva ora" in edit).

### Submit

```tsx
const submit = () => {
  if (mode === "create") createMut.mutate(payload);
  else performAutoSave();  // forza un'auto-save immediato
};
```

## `CourseDocumentUploader.tsx`

- Drag & drop + button "Scegli file"
- Mime accettati: PDF, DOC, DOCX, TXT, MD, RTF (limite 25MB)
- Per riga: filename, badge status, icona "Vedi dettaglio" (👁) se ready,
  icona "Rielabora" (🔄), icona elimina
- Click sulla riga (se ready) apre il summary dialog
- ConfirmDialog per delete e reprocess (se status=ready)

## `DocumentSummaryDialog.tsx`

Modal `max-w-4xl`. Sidebar verticale a sinistra (192px), contenuto scrollabile a destra (60vh).

Sezioni (i18n keys `courses.docs.summary.dialog.sections.*`):
1. Abstract
2. Struttura (lista numerata)
3. Concetti chiave (cards con name + explanation)
4. Definizioni (table-like grid: term + definition)
5. Esempi e casi (cards)
6. Formule e regole (con **KaTeX render**, fallback a mono)
7. Autori e riferimenti (badge author/cited_reference + value)
8. Tag rilevanza (chip)

Il sidebar item attivo usa `bg-primary` con `text-primary-foreground` (ben
distinguibile, no più "tutto grigio"). Hover degli altri usa `bg-muted/60`.

Testo body in `text-foreground` con `leading-relaxed` (no più
`text-muted-foreground` per evitare appiattimento visivo).

### KaTeX rendering

```tsx
function LatexBlock({ source }: { source: string }) {
  const html = useMemo(() => {
    try {
      return katex.renderToString(source, { displayMode: true, throwOnError: true, strict: "ignore" });
    } catch {
      return null;
    }
  }, [source]);
  if (!html) return <pre>{source}</pre>;  // fallback testo
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}
```

Dipendenze: `katex` + `@types/katex`. CSS importato da `katex/dist/katex.min.css`.

## `CourseArchitectureView.tsx`

Vista + CRUD inline dell'architettura. Presa in carico da `ArchitectureSection`
in `CourseEditorPage` (gated su `editable = canEdit && status ∈ {ready, approved}`).

Stati di rendering:

- **Vuoto** (status≠pending, no modules): testo "Nessuna architettura ancora generata" + pulsante "Aggiungi modulo" (se editable)
- **Pending**: spinner + label fase + percentuale + `<Progress />` + **ETA + tempo trascorso** (via `useTaskEta`, vedi [Frontend 08 — Hooks](../frontend/08-hooks.md): persiste lo `started_at` su `sessionStorage` keyed dal course id, stima ETA come `elapsed × (100 - progress) / progress` quando `progress ≥ 5%`)
- **Errore senza moduli**: card destructive con messaggio
- **Popolato**: overview/rationale + lista modules con CRUD inline

Per ogni modulo: card con badge `Modulo N`, titolo, controlli (↑↓ ✏️ 🗑️),
descrizione, e dentro:
- Loading pill (se sta generando lezioni)
- Pulsante "Genera lezioni con AI" (se vuoto e non in generazione)
- Lista lezioni con badge `Lezione N`, intro badge, controlli (↑↓ ✏️ 🗑️), summary, bibliografia
- Pulsante "Aggiungi lezione"

Pulsante "Aggiungi modulo" in fondo all'elenco.

### Mutations

- `moduleCreateMut`, `moduleUpdateMut`, `moduleDeleteMut`, `moduleReorderMut`
- `lessonCreateMut`, `lessonUpdateMut`, `lessonDeleteMut`, `lessonReorderMut`
- `moduleGenerateLessonsMut` (sync, 5min timeout)

### Optimistic reorder

`moduleReorderMut.onMutate` rinumera localmente moduli + lezioni nella cache TQ
prima della chiamata HTTP. Pattern speculare in `lessonReorderMut`.

```ts
const renumberModulesInCache = (current: CourseOut, ids: string[]): CourseOut => {
  const byId = new Map(current.modules.map((m) => [m.id, m]));
  const reordered = ids.map((id, i) => {
    const m = byId.get(id)!;
    const newCode = `M${i + 1}`;
    return {
      ...m,
      position: i + 1,
      module_code: newCode,
      lessons: m.lessons.map((l, li) => ({ ...l, lesson_code: `${newCode}.L${li + 1}` })),
    };
  });
  return { ...current, modules: reordered };
};
```

`onError` rollback con `qc.setQueryData(detailKey, ctx.previous)`.

### Auto-trigger AI

Dopo `moduleCreateMut.onSuccess`, trova il primo modulo con `lessons.length === 0`
e fa partire `moduleGenerateLessonsMut.mutate(newModule.id)`.

### Helper di formatting

```ts
const moduleLabel = (code: string) => {
  const m = code.match(/^M(\d+)$/);
  return m ? t("courses.architecture.moduleLabel", { n: m[1] }) : code;
};
const lessonLabel = (code: string) => {
  const m = code.match(/^M\d+\.L(\d+)$/);
  return m ? t("courses.architecture.lessonLabel", { n: m[1] }) : code;
};
```

## `ModuleEditDialog.tsx` / `LessonEditDialog.tsx`

Dialog di create/edit con UX migliorata (vedi
[04 — Manual editing](04-manual-editing.md) per dettagli completi):

Pattern condivisi:
- Componente interno `FormField` con counter live + hint sotto + asterisco
  required
- Auto-focus + select del titolo all'apertura
- Submit con `⌘+↵` / `Ctrl+↵`
- Header con badge mono (`Modulo N` / `Lezione N`)
- Footer con `min-w-[120px]` sul Save per evitare reflow

`LessonEditDialog` aggiuntivamente:
- `<ScrollArea>` interno (max 65vh)
- Toggle "Lezione introduttiva" come riquadro (con icona, hint, bordo brand quando attivo)
- **`BibliographyEditor`** sotto-componente: lista di items con grid 2-col,
  Add/Remove buttons, regola §4.4 enforced (source AI ⇒ confidence to_verify)

## `GenerateArchitectureDialog.tsx`

Dialog di conferma per la generazione/rigenerazione architettura. Title +
description differenti per `isRegeneration`. In modalità regenerate, mostra
una `<Textarea>` per il `regeneration_hint` (max 2000 char).

## `CourseLessonStructureView.tsx` (Fase 2)

Tab content per la struttura delle lezioni (vedi
[07 — Lesson structure](07-lesson-structure.md) per il flusso completo).

Layout:

- **Header card**: titolo, descrizione, pulsante "Genera/Rigenera struttura per
  tutti i moduli" (gated `canGenerate`, disabled durante batch attivo) +
  "Approva tutto" (visibile quando tutti i moduli sono `ready`).
- **Aggregate progress bar** (sempre visibile durante batch o quando esistono
  moduli in lavorazione):
  - Etichetta `{n_completed}/{n_total} moduli completati ({percent}%)`
  - `percent = avg(progress per modulo)` (i moduli `ready/approved` contano 100%)
  - Progress bar Radix
  - **ETA + tempo medio per modulo** (via `useBatchEta`) durante un batch attivo
  - Conteggio moduli `failed` con messaggio destructive
- **Lista moduli** (una card per modulo): badge stato + pulsante azione
  contestuale (Generate / Regenerate / Retry / Approve), label fase, percentuale
  per il modulo in lavorazione.
- **Per ogni lezione del modulo** (in `ready/approved`): row collapsible con
  4 sub-sezioni accordion:
  1. **Obiettivi formativi** — lista numerata
  2. **Temi obbligatori** — chip `topic_id` + topic + rationale
  3. **Prerequisiti** — bullet list
  4. **Scaletta sezioni** — lista numerata con `section_id` + title + purpose +
     chips coverage

### Mutations

- `generateAllMut` (batch dispatch su tutti i moduli)
- `generateModuleMut` (singolo modulo, con hint opzionale per rigenerare)
- `approveModuleMut`, `approveAllMut`
- `updateLessonMut` (PATCH lezione → struttura)

Tutte aggiornano la cache TanStack via `qc.setQueryData(detailKey, fresh)` +
invalidano la lista corsi.

## `LessonStructureEditDialog.tsx` (Fase 2)

Dialog `max-w-4xl` per CRUD manuale di una lezione. 4 fieldset:

1. **Obiettivi**: lista riordinabile (↑↓), warning prefisso lingua-corretto
2. **Temi**: lista con auto-genID (T1, T2, …), validation univocità
3. **Prerequisiti**: lista semplice
4. **Sezioni**: lista riordinabile + multi-select dei `topic_id` esistenti

Validazione client-side soft (warning), validazione hard server-side.
`⌘+↵` salva. Cancel o ESC per chiudere senza salvare.

## `LessonsStructureGenerateDialog.tsx` (Fase 2)

Dialog di conferma per generate/regenerate. 4 modalità:
`generate-module | regenerate-module | generate-all | regenerate-all`.
Per "regenerate-*", textarea per `regeneration_hint` (max 2000 char).

## `Progress` UI primitive

`frontend/src/components/ui/progress.tsx` — wrapper di `@radix-ui/react-progress`:

```tsx
<ProgressPrimitive.Root>
  <ProgressPrimitive.Indicator
    style={{ transform: `translateX(-${100 - (value ?? 0)}%)` }}
  />
</ProgressPrimitive.Root>
```

Usato in:
- `ArchitectureSection` (real backend progress)
- `CourseArchitectureView` (synthetic progress per generazione lezioni AI)

## i18n

Le chiavi sotto `courses.*` sono in `frontend/src/i18n/locales/{it,en}.json`.
**Solo IT/EN sono canoniche** — le altre 22 lingue UE vengono completate
in-app via "Completa con AI" (vedi
[Frontend 09 — i18n](../frontend/09-i18n.md)).

Namespaces principali:

```
courses.tabs.{base, didactic, documents, architecture, lessonsStructure}
courses.fields.{title, titlePlaceholder, objectives, ...}
courses.taxonomies.{categoria, ..., none}
courses.statuses.{draft, architecture_pending, ..., lessons_structure_approved, ...}
courses.summary.{title, modules, ...}
courses.docs.{uploaded, deleted, reprocess, viewDetail, summary.{...}}
courses.architecture.{generate, regenerate, approve, phases.{...},
                     moduleLabel, lessonLabel, moduleGenerating,
                     module.{add, createTitle, editTitle, fields.{...}},
                     lesson.{add, createTitle, editTitle, fields.{...},
                              bibliography.{title, hint, add, fields.{...}}}}
courses.lessonsStructure.{title, description, architectureNotApproved,
                          generateAll, regenerateAll, approveAll,
                          aggregate.{label, failed},
                          statuses.{empty, pending, processing, ready, approved, failed},
                          phases.{preparing_prompt, calling_openai, materializing},
                          fields.{learningObjectives, mandatoryTopics, prerequisites, sectionOutline},
                          subsection.empty,
                          module.{generate, regenerate, retry, approve, failed},
                          lesson.{editTitle, editDescription, emptyHint,
                                  objective.{add, placeholder, prefixHintIt, prefixHintEn, ...},
                                  topic.{add, fields.{topicId, topic, rationale}, idHint, duplicateId, ...},
                                  prerequisite.{add, placeholder, ...},
                                  section.{add, fields.{...}, coverageHint, uncoveredWarning, ...}},
                          dialog.{generateModule, regenerateModule, generateAll, regenerateAll,
                                  hintLabel, hintPlaceholder, hintHelper, generating,
                                  generateModuleCta, regenerateModuleCta, generateAllCta, regenerateAllCta},
                          validation.{...}, toast.{...}}
common.{save, cancel, ..., none, select, moveUp, moveDown}
```

## `CourseLessonContentView.tsx` (Fase 3 + §7 PDF)

Tab content per i contenuti delle lezioni. Per dettagli completi vedi
[08 — Lesson content](08-lesson-content.md) e [09 — PDF export](09-pdf-export.md).

Layout:

- **Header card**: aggregate progress + pulsanti "Genera tutti i contenuti" /
  "Approva tutti" + "Esporta tutti i PDF" / "Annulla export PDF" (§7). Durante
  un batch attivo mostra **ETA + tempo medio per lezione** sotto la progress bar
  (via `useBatchEta`).
- **Sub-pannello Glossario** (collapsible, hidden behind icon): chip dei
  termini con tooltip su `usage_note` + pulsante Rigenera.
- **Lista per modulo**: card per ogni lezione con:
  - Badge `LessonContentStatusBadge` + `LessonPdfStatusBadge` (entrambi
    funzioni interne a `CourseLessonContentView.tsx`, non file separati)
  - Bottoni contestuali content (Generate / Regenerate / Retry / Approve / Edit)
  - Bottoni contestuali PDF (Esporta PDF | Scarica PDF | Rigenera PDF)
  - Progress bar live durante content generation o PDF rendering
- **Header del modulo** — quando TUTTE le lezioni del modulo hanno
  `pdf_status='ready'`, compaiono due bottoni outline:
  - "Scarica modulo" → `coursesApi.lessonPdf.downloadModuleMerged(...)`
    → PDF unico concatenato.
  - "Esporta modulo" → `coursesApi.lessonPdf.downloadModuleZip(...)` →
    ZIP con un PDF per lezione.
  Stesso pattern è applicato a `CourseLessonSlidesView` (gating su
  `slides_pdf_status === "ready"`) e `CourseLessonSpeechView` (gating
  su `speech_pdf_status === "ready"`).
- **Render lezione espansa** (status `ready`/`approved`): via
  `LessonContentView` (Mermaid live, KaTeX, tabelle, esempi).

### Mutations

- Content: `generateLessonMut`, `generateAllMut`, `approveLessonMut`,
  `approveAllMut`, `editLessonMut`
- Glossary: `regenerateGlossaryMut`
- PDF (§7): `exportPdfMut`, `exportAllPdfMut`, `cancelAllPdfMut`,
  `downloadPdfMut` (blob via `coursesApi.lessonPdf.download` →
  `URL.createObjectURL` → `<a download>` → `revokeObjectURL`)

## `LessonContentEditDialog.tsx` (editor user-friendly)

`max-w-6xl` con pannello unico scrollabile a `SectionGroup` collassabili.
Sostituisce le textarea grezze con editor specializzati che nascondono
markdown/LaTeX/Mermaid all'utente finale:

| Campo | Editor |
|---|---|
| `introduction` / `sections[].content` / `summary` / `examples[].content` / `equations[].explanation` | `RichTextEditor` (TipTap) |
| `visual_assets[]` con `format='mermaid'` | `MermaidEditor` (preview live) |
| `visual_assets[]` con `format='image'` | preview `<img>` + bottone "Digitalizza in Mermaid" (Vision API) |
| `visual_assets[]` legacy (`image_prompt|image_search_query|description`) | banner "Asset legacy" + `<Textarea>` readonly |
| `tables[].markdown` | `TableEditor` |
| `equations[].latex` | `LatexEditor` |
| `key_takeaways[]` / `references[]` | `<Input>` lista |

Per ogni asset (FIG/TAB/EQ/EX) il dialog mostra **`RefIdField`**: chip readable
dell'ID canonico con pulsante copy + input rinominabile. La rinomina invoca
`patchRefs(kind, oldId, newId)` che fa cascade replace di `[KIND:oldId]` →
`[KIND:newId]` su tutti i campi testuali (introduction, sections.content,
summary, examples.content, equations.explanation). L'utente non deve
ricordarsi di sincronizzare i riferimenti a mano.

### Asset visivi: aggiunta + upload + digitalizza (`AddVisualAssetMenu`)

Refactor del commit `92d5f37`. Il pulsante "+ Aggiungi asset visivo"
è ora un **dropdown** con due opzioni:

- **"Carica immagine"** → apre file picker (`accept="image/png,image/jpeg,image/webp"`),
  poi `coursesApi.lessonAssets.upload(orgId, courseId, file)` →
  ritorna `{ path, url }` → l'editor aggiunge un nuovo asset con
  `format="image"` e `content=path`.
- **"Scrivi Mermaid a mano"** → asset nuovo con `format="mermaid"`,
  `content=""` e `MermaidEditor` subito aperto.

Sull'asset `format="image"` l'editor mostra una preview `<img>` con
height max ~80 (320px) e un pulsante secondario `[✨ Digitalizza in
Mermaid]`. Click → `coursesApi.lessonAssets.convertToMermaid(orgId,
courseId, path)` → su successo l'editor sostituisce localmente
`format`/`content` (immagine → codice Mermaid). Su errore (incluso il
caso `UNRECOGNIZED`) toast con messaggio dal backend, asset invariato.

Vedi anche [08 — Lesson content § Asset visivi: Mermaid + immagini caricate](08-lesson-content.md#asset-visivi-mermaid--immagini-caricate)
per i dettagli del workflow + cleanup file orfani lato backend.

### "Evidenzia dove usato"

Accanto a ciascun `RefIdField` (asset/tabella/equazione/esempio) c'è il
pulsante **`HighlightUsageButton`** che cerca la prima occorrenza di
`[KIND:id]` nei campi scansionabili (intro → sezioni → summary → esempi
→ explanation delle equazioni), apre il `SectionGroup` che la contiene
(se collassato), scrolla in vista e applica un flash visivo
(`ring-2 ring-amber-400`) per ~2.2s sul wrapper.

Dal commit `f53906c` il pulsante **evidenzia anche il token `[KIND:id]`
specifico dentro il paragrafo** (sfondo amber-400 al 45% + outline). Il
targeting è banale: `RichTextEditor.protectTokens` avvolge ogni token
in un `<code>`, quindi un `fieldEl.querySelectorAll("code")` con match
esatto sul `textContent` trova il nodo. Inline styles applicati via JS
+ cleanup al timeout. Niente decorazioni ProseMirror, niente CSS file.

I `SectionGroup` sono **controllati** (`open`/`onToggle` lifted nello
stato padre) proprio per supportare l'auto-espansione. Se nessuna
occorrenza viene trovata → toast informativo (nessuna eccezione).

Per i `references[]` (citazioni bibliografiche senza ID-token) lo stesso
pulsante esegue invece un substring match case-insensitive della
`citation` nei campi scansionabili — best-effort, perché le citazioni
quasi mai compaiono verbatim nel testo. Niente token highlight in questo
caso (solo flash sul wrapper). Se non c'è match → toast.

## `CourseLessonSlidesView.tsx` (Fase 4 + PDF slide)

Tab "Slide" del wizard. Mirror strutturale di `CourseLessonContentView` ma
scoped sui campi `slides_*` e `slides_pdf_*` della lezione.

Header: aggregate progress (slide+PDF combinato), CTA batch (Genera tutti /
Rigenera / Genera mancanti / Approva tutti / Annulla, + Esporta PDF tutti /
Annulla export).

Per modulo: card con lista lezioni. Per lezione, riga espandibile con:
- status badge + `<ApprovalBadge level="lessonSlides">` quando approved
- primary CTA contestuale (Genera → Approva → Modifica + Esporta PDF / Scarica PDF / Aggiorna PDF con stale logic)
- kebab menu (Rigenera, Rigenera PDF)
- progress live + phase
- `<StalenessAlert kind="slides">` quando `isSlidesStale === true`
- `<StalenessAlert kind="pdf">` quando `isSlidesPdfStale === true` (riusa la kind "pdf" — etichetta uguale "PDF non allineato")
- expanded: `<LessonSlidesView slides={slides_raw} contentRaw={content_raw} />`

### `LessonSlidesView.tsx` (viewer read-only)

Lista verticale di card per slide. Per ciascuna slide:
- Header: badge slide_number + badge type + titolo + (opzionale) badge "Da sezione" con `source_section_id`
- Body: prosa breve (`body`), bullets (`<ul>`), asset referenziati renderizzati via `resolveAsset()` di `lib/slides.ts` (visual mermaid → `<MermaidDiagram>`, table markdown → `<MarkdownRenderer>`, equation LaTeX → `$$...$$`, example card)
- Asset orfano (riferimento senza definizione): box destructive con messaggio

### `LessonSlidesEditDialog.tsx`

Editor del `slides_raw`. Layout: lista slide collassabili con titolo + type select + body textarea + bullets list editabile + references_assets multi-select (popolato dall'unione `contentRaw + new_assets`) + source_section_id select (sezioni di Fase 3). Sezione separata per `new_assets` (Fase 4-only).

Validazione client-side allentata; il backend applica le validazioni complete.

### `LessonSlidesGenerateDialog.tsx`

4 modes (`generate-lesson | regenerate-lesson | generate-all | regenerate-all`). Textarea `regeneration_hint` (max 2000) opzionale, visibile solo per modes regenerate.

### `LessonSlidesPdfExportDialog.tsx`

Dialog scelta template per export PDF slide. Usa `slideTemplatesApi.list(orgId)` (template `slide_templates`, **non** `pdf_templates`). Conferma: invia `pdf_template_id` o `null` (fallback `_default_slide_template_dict` server-side).

## `CourseLessonSpeechView.tsx` (Fase 5 + PDF discorso)

Tab "Discorso" del wizard. Mirror strutturale di `CourseLessonSlidesView` ma
scoped sui campi `speech_*` e `speech_pdf_*`.

Pre-condizione: empty state se `eligibleForGen === 0` (nessuna lezione con
`slides_status ∈ ready/approved`) — invita a tornare alla Fase 4.

Header e per-lezione row come Fase 4. CTA: Genera/Rigenera/Approva/Edit + Esporta PDF/Scarica PDF.

Stale alerts: `<StalenessAlert kind="speech">` (a monte: slides/content/structure/architecture) e `<StalenessAlert kind="speechPdf">` (PDF disallineato dal `speech_raw`).

### `LessonSpeechView.tsx` (viewer read-only)

Lista verticale **raggruppata per slide** (mirror del PDF). Helper `formatMmSs(seconds)` per timeline. Per ciascuna entry di `slide_to_segments_map`:
- Card header: badge slide_number + titolo slide (lookup da `slides_raw`) + durata totale slide
- Lista segmenti in ordine, ognuno:
  - timeline cumulativa `[mm:ss — mm:ss]` (calcolata sommando le durate precedenti)
  - durata `Ns`
  - testo segmento (paragrafo con leading-relaxed)
  - delivery notes in italic (se non vuote)

### `LessonSpeechEditDialog.tsx`

Editor del `speech_raw`. Lista slide (lookup da `slides_raw`) con segmenti raggruppati. Per ciascun segmento:
- `segment_id` (read-only chip)
- Selettore `slide_id` (popolato dalle slide della lezione)
- Textarea `text` con **warning chip TTS-safety inline** (regex client-side che duplica il BE per UX immediata: caratteri proibiti `* _ ` # \ $`, abbreviazioni `es.`, `etc.`, `ca.`, `p.es.`, `i.e.`, `e.g.`, comandi LaTeX `\frac`, `\sum`, ...)
- Input `estimated_duration_seconds` (number) con bottone **"Auto"** che ricalcola da `wordCount(text) × 60 / wpm` (`wpm = 130 IT / 150 EN`)
- Textarea `delivery_notes` (1 riga, opzionale)
- Bottone "Rimuovi" + "Aggiungi segmento" sotto ciascuna slide

Footer dialog: warning verde se `sum(durations) ∈ [target × 0.95, target × 1.05]`, ambra altrimenti con valori `actual / low / high / target`.

Submit ricostruisce automaticamente `slide_to_segments_map` (raggruppando i segmenti per `slide_id` nell'ordine delle slide originali) e i totali derivati prima di chiamare l'API.

### `LessonSpeechGenerateDialog.tsx`

4 modes come Fase 4. Hint placeholder con esempi tipici TTS (es. "Tono più informale", "Aggiungi una domanda retorica nelle slide concept").

### `LessonSpeechPdfExportDialog.tsx`

Dialog scelta template per export PDF discorso. Usa `pdfTemplatesApi.list(orgId)` (template `pdf_templates` — **stesso** del PDF lezione testo, perché il discorso è prosa pura A4 portrait single-column).

## Helper riusabili

### `lib/staleness.ts`

7 helper di stale-detection cascata. Tutti restituiscono `boolean`. La logica è una semplice serie di `isAfter(modified, generated)` confrontando timestamp `*_modified_at` (settati solo dai CRUD manuali) con `*_generated_at` (settati solo dai worker AI):

```ts
isStructureStale(module): module.architecture_modified_at > module.lessons_structure_generated_at
isContentStale(lesson, module): structure | architecture > content_generated_at
isPdfStale(lesson): content_generated_at | content_modified_at > pdf_generated_at
isSlidesStale(lesson, module): content | structure | architecture > slides_generated_at
isSlidesPdfStale(lesson): slides_generated_at | slides_modified_at > slides_pdf_generated_at
isSpeechStale(lesson, module): slides | content | structure | architecture > speech_generated_at
isSpeechPdfStale(lesson): speech_generated_at | speech_modified_at > speech_pdf_generated_at
```

### `lib/slides.ts`

`resolveAsset(assetId, contentRaw, newAssets) → { kind: 'visual'|'table'|'equation'|'example'|'new_visual', payload }` per il rendering degli asset slide. Mirror della logica backend `_resolve_asset_for_slide`.

### `components/shared/ApprovalBadge.tsx`

Badge "approvato" cross-fase. `level: 'architecture' | 'module' | 'lessonContent' | 'lessonSlides' | 'lessonSpeech'`.

### `components/shared/StalenessAlert.tsx`

Alert "qualcosa a monte è cambiato". `kind: 'structure' | 'content' | 'pdf' | 'slides' | 'speech' | 'speechPdf'`. Etichetta + CTA opzionale via i18n `courses.staleness.{kind}.{label,action}`.

## Dipendenze frontend nuove

```json
{
  "katex": "^0.16",
  "@types/katex": "^0.16",
  "@radix-ui/react-progress": "^1",

  "mermaid": "^11",
  "react-markdown": "^9",
  "remark-gfm": "^4",
  "remark-math": "^6",
  "rehype-katex": "^7",

  "@tiptap/react": "^3.22.5",
  "@tiptap/pm": "^3.22.5",
  "@tiptap/starter-kit": "^3.22.5",
  "@tiptap/extension-link": "^3.22.5",
  "tiptap-markdown": "^0.9.0"
}
```
