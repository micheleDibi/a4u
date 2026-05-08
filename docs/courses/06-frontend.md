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

Editor tab-based. Layout principale: **`<Tabs>`** con 6 voci (in modalità edit;
in create solo le prime 2):

1. **Informazioni di base** — title, objectives, argomenti chiave, categoria,
   lingua, assegnatario, **CFU + summary dimensionamento** (tutto consolidato in
   un'unica Card)
2. **Inquadramento didattico** — 7 tassonomie (categoria spostata in Base)
3. **Documenti** — solo edit
4. **Architettura** — solo edit, AI Generate/Approve + view CRUD
5. **Struttura lezioni** — solo edit, gated su `course.status >= architecture_approved`. AI batch/per-modulo + edit manuale.
6. **Contenuti lezioni** — solo edit, gated su `course.status >= lessons_structure_approved`. AI batch/per-lezione (Fase 3) + glossario + **export PDF** (§7).

### Auto-save

Debounce 1500ms su `draft` change. Stable diff via `JSON.stringify(draft) === JSON.stringify(baseline)`.
`updateMut` per fields, `assigneeMut` separato (permission diversa).

### Polling

`refetchInterval` su `useQuery`:
- 5s se `course.status === 'architecture_pending'` (mostra `architecture_progress`)
- 5s se almeno un documento è in `pending`/`processing`
- 5s se almeno un modulo è in `lessons_structure_status ∈ {pending, processing}` (Fase 2)
- 5s se almeno una lezione è in `content_status ∈ {pending, processing}` o se `glossary_status ∈ {pending, processing}` (Fase 3)
- 4s se almeno una lezione è in `pdf_status ∈ {pending, processing}` (§7 — Iterazione E)
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
- **Pending**: spinner + label fase + percentuale + `<Progress />`
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
  "Approva tutti" + "Esporta tutti i PDF" / "Annulla export PDF" (§7).
- **Sub-pannello Glossario** (collapsible, hidden behind icon): chip dei
  termini con tooltip su `usage_note` + pulsante Rigenera.
- **Lista per modulo**: card per ogni lezione con:
  - Badge `LessonContentStatusBadge` + `LessonPdfStatusBadge` (entrambi
    funzioni interne a `CourseLessonContentView.tsx`, non file separati)
  - Bottoni contestuali content (Generate / Regenerate / Retry / Approve / Edit)
  - Bottoni contestuali PDF (Esporta PDF | Scarica PDF | Rigenera PDF)
  - Progress bar live durante content generation o PDF rendering
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
| `visual_assets[].content` con `format='mermaid'` | `MermaidEditor` |
| `visual_assets[].content` con altri formati | `<Textarea>` semplice |
| `tables[].markdown` | `TableEditor` |
| `equations[].latex` | `LatexEditor` |
| `key_takeaways[]` / `references[]` | `<Input>` lista |

Per ogni asset (FIG/TAB/EQ/EX) il dialog mostra **`RefIdField`**: chip readable
dell'ID canonico con pulsante copy + input rinominabile. La rinomina invoca
`patchRefs(kind, oldId, newId)` che fa cascade replace di `[KIND:oldId]` →
`[KIND:newId]` su tutti i campi testuali (introduction, sections.content,
summary, examples.content, equations.explanation). L'utente non deve
ricordarsi di sincronizzare i riferimenti a mano.

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
