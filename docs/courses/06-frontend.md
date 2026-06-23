# 06 — Frontend (corsi)

Pagine, componenti e pattern per il dominio Corsi.

## Routing

```
/orgs/:orgId/corsi                  → CoursesListPage
/orgs/:orgId/corsi/nuovo            → CourseEditorPage (mode='create')
/orgs/:orgId/corsi/:courseId        → CourseEditorPage (mode='edit')
```

## `CoursesListPage.tsx`

Tabella paginata in stile "operations view" — l'utente può filtrare
rapidamente, ordinare per data, e vedere a colpo d'occhio il progresso
pipeline (contenuti / slide / video / avatar) per ogni corso senza
aprirlo.

**Toolbar filtri** (sopra la tabella, in `Card`):

- Search testuale su titolo/obiettivi (debounce 300ms via
  `useDebouncedValue`).
- Select **Assegnatario** (popolato da `useOrgMembers(orgId)`).
- Select **Stato** con tutti i 22 valori di `course.status`
  (`STATUS_FILTERS`, da `draft` ad `archived`) + "Tutti".
- Select **Lingua** (da `useLanguages()`, con bandiera + nome nativo).
- Due **`DateRangeField`** (Popover + 2 input date nativi): "Creato" e
  "Aggiornato".
- Bottone **Reset filtri** (visibile solo se almeno un filtro è
  attivo).

**Stato dei filtri sincronizzato in URL** via `useSearchParams`:
`?status=draft&assignee_user_id=…&created_after=2026-03-01&sort_by=created_at`.
Refresh-safe + condivisibile. Default `updated_at desc` omesso
dall'URL per pulizia.

**Ordinamento**: `Select` "Ordina" nella toolbar (sostituisce i vecchi
header cliccabili sulle colonne data). Encoding `"${sort_by}:${sort_dir}"`
come singolo value; 4 opzioni (`updated_at`/`created_at` × `desc`/`asc`)
con icona `ArrowDown`/`ArrowUp`. `setSort` pulisce dall'URL il default
`updated_at desc`.

**Colonne** (in ordine `id`): `title`, `assignee`, `status`
(`CourseStatusBadge`), `lang` (bandiera + ISO), `pipeline` (4 chip via
`CoursePipelineRowChips`, vedi [frontend/05](../frontend/05-components.md)),
**`cfu`** (intero `tabular-nums`), **`corsoDiLaurea`** (`corso_di_laurea`
troncata a 200px con `title`, `—` se NULL), `modules` (`modules_count`),
`created`, `updated`, `actions` (menu `⋮`).

**Visibilità colonne personalizzabile** (`DataTableColumnToggle` +
`useColumnVisibility`):

- Il selettore **"Colonne"** (`DataTableColumnToggle`, in
  [05 — Components](../frontend/05-components.md)) è un dropdown di
  checkbox in fondo alla toolbar filtri (`ms-auto`); itera le colonne con
  `enableHiding !== false` e usa `column.meta.label` come etichetta.
  `title` e `actions` hanno `enableHiding: false` (sempre visibili).
- La visibilità è persistita per-browser in localStorage via
  `useColumnVisibility(COURSES_COLUMNS_STORAGE_KEY, DEFAULT_COLUMN_VISIBILITY)`
  (chiave `"courses-list-columns"`, globale: identica tra organizzazioni). Lo
  stato salvato è unito sopra ai default (`{...defaults, ...saved}`) così
  una colonna nuova eredita il suo default finché non viene toccata.
- `DEFAULT_COLUMN_VISIBILITY`: `cfu` e `corsoDiLaurea` partono **visibili**;
  `modules`, `created` e `updated` partono **nascoste** (l'utente le attiva
  dal selettore). `title`/`assignee`/`status`/`lang`/`pipeline`/`actions`
  visibili.
- `columnVisibility`/`setColumnVisibility` sono inoltrati sia a
  `DataTable` (props `columnVisibility`/`onColumnVisibilityChange`) sia al
  selettore.

Le colonne data (`created`/`updated`) **non hanno più header sortable**:
l'ordinamento è ora un `Select` "Ordina" nella toolbar (encoding
`sort_by:sort_dir` come singolo value); le date dettagliate restano leggibili
nel pannello info del menu azioni `⋮` di ogni riga.

**Search input**: state locale `qInput` reattivo + `debouncedQ` con 300ms
che scrive in URL solo dopo il debounce. Sync bidirezionale: se l'URL
cambia da fuori (es. reset), `qInput` si allinea.

**Reset filtri**: svuota interamente il querystring (tranne path) e
azzera `qInput`. Pagina 1.

**Pagination**: pageIndex e pageSize derivati da URL (`?page=2&page_size=50`),
default 1 e 25. Pagina 1 e size 25 omessi dall'URL.

Pulsante "Nuovo corso" gated da `course:create`.

**Menu azioni di riga (`⋮`)**: include "Modifica", "Duplica in altra
lingua" (gated `course:duplicate`, nascosto se il corso è già target
di un job di duplicazione attivo), "Elimina" (gated `course:delete`).
La voce di duplicazione apre il `DuplicateCourseDialog`.

**Duplicazione corso in altra lingua**:
- `DuplicateCourseDialog` (`components/`): dialog con `Select` di
  lingue (popolato da `useLanguages()`, escludendo la lingua corrente
  del corso). Mutation `coursesApi.duplicate(orgId, courseId,
  target_language_code)`. Su success: toast + invalidate
  `["courses","list",orgId]` + re-invalidate ogni 2s per 16s totali
  (per non aspettare il polling regolare prima di vedere comparire la
  riga del target).

- `CourseDuplicationBadge` (`components/`): badge **rich UX** a 4
  righe renderizzato sotto il titolo della riga quando
  `course.duplication_job != null`:
  1. **Header**: icona globe + label DUPLICAZIONE + bandiera +
     nome nativo lingua target (es. "Hrvatski") + step indicator
     `5/7` (hover → tooltip pipeline) + bottone `✕` annulla.
  2. **Phase label**: spinner `Loader2` animato + label localizzata
     della fase corrente (mapping `progress_phase` →
     `courses.duplicate.badge.phases.*`).
  3. **Sub-progress + ETA**: `job.progress_detail` (es.
     "23/48 lezioni completate") + ETA stimato (`~4 min rimanenti`)
     calcolato lato FE da `started_at` + `progress`. Auto-refresh
     ogni 5s via `setInterval` tra un polling list e l'altro.
  4. **Progress bar**: shadcn `Progress` h-2 con classe
     `progress-shimmer` (keyframe in `index.css`) attiva durante
     processing. % prominente.

  Hover sullo step indicator → tooltip con la pipeline completa delle
  7 phase: ✓ completate, ⟳ corrente (bold), · future (grigio).

- **Polling automatico**: `useQuery refetchInterval` condizionato a
  3000ms quando almeno una riga della pagina corrente ha un
  `duplication_job` in `pending`/`processing`. Quando tutti i job
  completano, polling disabilitato.

Vedi [15 — Duplicazione corso in altra lingua](15-course-duplication.md)
per il design completo (pipeline worker, multi-pass + fallback model,
resume-from-progress, cleanup, badge UX dettagliato).

## `CourseEditorPage.tsx`

Editor a **stepper di 4 macro-fasi**. Sopra la `TabsList` viene renderizzato
un **`CoursePhaseStepper`** orizzontale (`setup → architecture → content →
media`); sotto, la `TabsList` mostra **solo le sub-tab della fase corrente**.
La fase corrente è derivata da `activeTab` via `phaseOfTab(activeTab)`
(`currentPhase: PhaseId`); cliccare una fase non-locked nello stepper chiama
`onPhaseNavigate`, che porta alla **prima sub-tab** della fase
(`phase.tabs[0]`). Questo sostituisce la vecchia lista piatta di 11
`TabsTrigger` che andava a capo su due righe.

Le 10 sub-tab (in modalità edit; in create solo le prime 2-3 di `setup`)
restano le stesse, raggruppate per fase:

- **Setup** (`base, didactic, objectives, documents`)
  1. **Informazioni di base** — title, objectives, argomenti chiave, categoria,
     lingua, assegnatario, **CFU + summary dimensionamento** (tutto consolidato
     in un'unica Card)
  2. **Inquadramento didattico** — 7 tassonomie (categoria spostata in Base)
  3. **Obiettivi e Argomenti chiave** — textarea obiettivi + keyword + AI da documento
  4. **Documenti** — solo edit + `setupLocked`; include il pannello
     `CoursePaperSearch` (ricerca paper scientifici, vedi sotto)
- **Architettura** (`architecture, lessons-structure`)
  5. **Architettura** — solo edit, AI Generate/Approve + view CRUD
  6. **Struttura lezioni** — `disabled` finché `isCourseAtLeast(course.status, "architecture_approved")`. AI batch/per-modulo + edit manuale.
- **Contenuti** (`lesson-content, lesson-slides, lesson-speech`)
  7. **Contenuti lezioni** — `disabled` finché `isCourseAtLeast(course.status, "lessons_structure_approved")`. AI batch/per-lezione (Fase 3) + glossario + **export PDF testo** (§7).
  8. **Slide** — `disabled` finché `isCourseAtLeast(course.status, "content_ready")`. AI batch/per-lezione (Fase 4) + edit manuale + **export PDF slide**.
  9. **Discorso** — `disabled` finché nessuna lezione ha `slides_status ∈ {ready, approved}`. AI batch/per-lezione (Fase 5) + edit manuale + **export PDF discorso**.
- **Media** (`lesson-video, lesson-avatar-video`)
  10. **Video** (`lesson-video`) — `disabled` finché nessuna lezione ha `speech_status='approved'` AND `slides_status='approved'`. Generazione video MP4 (Fase 6).
  11. **Video con avatar** (`lesson-avatar-video`) — stesso gating del tab Video. Generazione del video con avatar parlante (Fase 6b).

`PHASES` (da `CoursePhaseStepper.tsx`) enumera le 4 fasi e il loro mapping
fase→tab; `TAB_ORDER` enumera le sub-tab e `TabId` ne è il tipo derivato. Il
gating delle sub-tab "Struttura lezioni", "Contenuti" e "Slide" usa ora
`isCourseAtLeast(course.status, milestone)` (basato su `COURSE_STATUS_RANK`)
al posto delle lunghe whitelist inline di `course.status` precedenti. La sub-tab
attiva è persistita per courseId in localStorage
(`course-editor-tab:{courseId}`); al rientro lo stepper si posiziona sulla fase
che contiene quella tab. Le sub-tab disabled restano visibili ma greyed-out
finché la pre-condizione a monte non è soddisfatta.

### `CoursePhaseStepper.tsx`

Stepper orizzontale delle 4 macro-fasi (`CoursePhaseStepper`) + helper di
gating condivisi con `CourseEditorPage`.

- **`PHASES`** (`CoursePhaseStepper.tsx:45`): array readonly delle 4 fasi, ognuna
  con `id`, `labelKey` (i18n `courses.phases.{id}`) e l'elenco delle `tabs`:

  | Fase | `id` | sub-tab |
  |---|---|---|
  | Setup | `setup` | `base, didactic, objectives, documents` |
  | Architettura | `architecture` | `architecture, lessons-structure` |
  | Contenuti | `content` | `lesson-content, lesson-slides, lesson-speech` |
  | Media | `media` | `lesson-video, lesson-avatar-video` |

  `PhaseId` è il tipo derivato `(typeof PHASES)[number]["id"]`.

- **`COURSE_STATUS_RANK`** (`CoursePhaseStepper.tsx:9`): mirror 1:1 di
  `backend/app/core/course_phase_order.py:COURSE_STATUS_RANK` — mappa i **22**
  stati del corso a un rank `0..21` (`draft=0` … `published=20`, `archived=21`).
  **Invariante**: la tabella esiste in due posti (BE + FE) e va tenuta allineata
  a mano; aggiungendo un nuovo stato va aggiunto in entrambi i lati.

- **`isCourseAtLeast(status, milestone)`** (`:39`): `true` se `status` ha
  raggiunto o superato `milestone` nella pipeline (`RANK[status] >= RANK[milestone]`;
  status sconosciuto → `-1`, milestone sconosciuta → `Infinity`). È il primitivo
  di gating riusato sia per i `disabled` dei `TabsTrigger` sia per gli stati di fase.

- **`phaseOfTab(tabId)`** (`:71`): risale la fase che contiene una sub-tab;
  fallback `setup`.

- **`computePhaseStatus(phaseId, course, setupLocked)`** (`:80`): deriva lo stato
  per-fase `PhaseStatus = "done" | "in_progress" | "locked" | "idle"`:
  - **setup**: `done` se `setupLocked`, altrimenti `in_progress`.
  - **architecture**: `locked` se non `setupLocked`; `done` sse
    `isCourseAtLeast(s, "lessons_structure_approved")`; altrimenti `in_progress`.
  - **content**: `locked` se non `setupLocked` o se non
    `isCourseAtLeast(s, "lessons_structure_approved")`; `done` sse tutte le
    lezioni hanno `speech_status === "approved"`; altrimenti `in_progress`.
  - **media**: `locked` se non `setupLocked` o se nessuna lezione ha
    `speech_status === "approved" && slides_status === "approved"`; `done` sse
    `isCourseAtLeast(s, "published")`; altrimenti `in_progress`.

- **Rendering** (`PhaseStep`, `:192`): ogni fase è un `<button>` con un pallino
  (`size-7` rounded-full) il cui aspetto dipende dallo status — `done` =
  cerchio emerald + `Check`, `locked` = muted + `Lock`, `in_progress` =
  `bg-primary` + indice, `idle` = bordato + indice — più l'etichetta e
  **connettori** (`h-px`) tra step (emerald se la fase precedente è `done`). La
  fase attiva ha `bg-primary/10` e `aria-current="step"`. Il click è ignorato
  quando `locked`; in tal caso il `title` mostra il **lockedHint** localizzato
  via `courses.phases.lockedHint.{id}`.

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

I tab **Video** (Fase 6) e **Video con avatar** (Fase 6b) hanno un
polling proprio, indipendente dal `useQuery` del corso: gli hook
`useCourseVideoStatus` / `useCourseAvatarVideoStatus` interrogano gli
endpoint `*-video/status` e rinfrescano ogni **2s** finché almeno una
lezione è in flight (`pending`/`processing`), poi si fermano.

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

## `CoursePaperSearch.tsx` + `PaperResultCard.tsx`

Pannello **ricerca paper scientifici** montato nella sub-tab **Documenti**
(`CoursePaperSearch orgId courseId`, in `CourseEditorPage.tsx:1491`). Sorgente
primaria OpenAlex + enrichment on-demand Semantic Scholar/Crossref. Il
deep-dive della feature vive in `docs/courses/16-paper-search.md`; gli endpoint
sono in [05 — API reference](05-api-reference.md).

### `CoursePaperSearch.tsx`

- **Pannello filtri** (`Card` collassabile via `showFilters`): input **query**
  (Enter → ricerca) + **7 filtri** opzionali:
  - `is_oa` (`Switch`, "Solo Open Access")
  - `work_type` (`Select`: `any | article | preprint | review | other`)
  - `year_from` / `year_to` (`number`, 1900–2100)
  - `min_citations` (`number`, ≥0)
  - `author_name`, `venue_name` (`Input` testo)

  `onSearch` rifiuta con toast `results.queryRequired` se query e filtri sono
  entrambi vuoti (`hasAnyFilter`). "Reset filtri" riporta `filters` a
  `{ is_oa: null, work_type: null }`.

- **Lista risultati + paginazione cursor**: `searchMut` chiama
  `coursesApi.papers.search` con `per_page: 20` fisso. Prima pagina
  (`cursor === null`) → **reset** di `results`/`selectedIds`/`lastQuery`;
  **"Carica altri 20"** (`onLoadMore`, footer visibile solo se `nextCursor`)
  → **append con dedup per `id`**. Header risultati sticky con counter
  `results.countWithTotal` (`{{shown}}/{{total}}`).

- **Multi-select + import**: `selectedIds: Set<string>` (`onToggleSelect`).
  Quando `selectedCount > 0` compare il bottone **"Importa selezionati"**
  (sticky, plurali via `import.button`) → `importMut`
  (`coursesApi.papers.importMany`) → toast `import.successDetail`
  (`{{total}}/{{pdf}}/{{metadata}}`), azzera la selezione e invalida
  `["courses","detail",orgId,courseId]` per ricaricare la lista documenti.

- **Riassunti AI cached per sessione**: due state separati —
  `summariesById: Record<string, SummaryState>` (`:88`) e
  `expandedSummaryIds: Set<string>` (`:91`). `onToggleSummary(paper)` (`:178`):
  se il riassunto è già `success` toggla **solo la visibilità** (nessuna nuova
  chiamata); se `loading` ignora; altrimenti genera/ritenta via
  `coursesApi.papers.aiSummary` salvando il risultato in `summariesById`. La
  cache **sopravvive** a "Carica altri" e all'expand/collapse; **si perde** solo
  a unmount o a una nuova ricerca primaria.

### `PaperResultCard.tsx`

Card singolo risultato. Esporta il tipo `SummaryState = { status: "loading" }
| { status: "success"; data: PaperAISummaryOut } | { status: "error"; error }`.

- **Riga badge**: `Checkbox` di selezione + badge **OA** (`variant="success"`,
  `Unlock`) / **non-OA** (`variant="warning"`, `Lock`) + (se presente) chip
  **rilevanza** con barra colorata a soglie **70/40** (emerald/amber/muted) e
  tooltip `card.relevanceTooltip` + badge **citazioni** (plurali) + badge tipo.
- **Titolo** linkato a `doi_url` (se presente), riga autori (primi 5 +
  `plusMoreAuthors`) + anno + journal.
- **Abstract collapsible**: preview a `ABSTRACT_PREVIEW_CHARS = 320` con
  toggle `abstractShowMore`/`abstractShowLess`; sotto, chip uniti
  `keywords + subjects` (max 10) e box **TL;DR** se popolato on-demand.
- **Footer**: bottone "Apri DOI" + bottone **Riassunto AI** a 4 modalità
  (idle → loading → collapsed-success → expanded-success), disabilitato durante
  `loading`. Il riassunto è **inline** (niente dialog): su `error` box
  destructive, su `success` espanso il `PaperSummaryBlock` con 4 sezioni
  (`shortSummary`, `technicalSummary`, `keywords` chip, `limitations`).

**API** (`coursesApi.papers`, `frontend/src/api/courses.ts:1006`):
`search`, `aiSummary`, `importMany` → `POST .../papers/{search,ai-summary,import}`
(permission `course:edit`).

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
courses.phases.{setup, architecture, content, media,
                lockedHint.{setup, architecture, content, media}}
courses.papers.{section.{title, subtitle},
                filters.{query, queryPlaceholder, toggle, yearFrom, yearTo,
                         minCitations, author, venue, type, typeAny, isOa,
                         search, resetFilters},
                results.{emptyInitial, empty, queryRequired, countWithTotal,
                         loadMore},
                card.{selectPaper, oaBadge, nonOaBadge, relevance,
                      relevanceTooltip, citations_one|other, openDoi, aiSummary,
                      abstractShowMore, abstractShowLess,
                      plusMoreAuthors_one|other},
                types.{article, preprint, review, other},
                import.{button_one|other, importing, successDetail},
                summary.{generating, show, hide, error, shortSummary,
                         technicalSummary, keywords, limitations}}
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
- **Render lezione espansa** (status `ready`/`approved`): per le lezioni
  didattiche via `LessonContentView` (Mermaid live, KaTeX, tabelle,
  esempi); per le lezioni di **verifica** (`is_assessment`) via
  `LessonAssessmentView`. Il branch è guidato dal type-guard
  `isAssessmentRaw(content_raw)`.
- **Lezioni di verifica** (`is_assessment`): la pipeline PDF è
  disattivata (niente `LessonPdfStatusBadge`, niente CTA PDF, escluse dal
  conteggio "tutti i PDF pronti" e da "Esporta tutti i PDF"). Al loro
  posto la row mostra un bottone **"Esporta CSV"** quando ci sono
  domande. Per la modifica apre `LessonAssessmentEditDialog` invece di
  `LessonContentEditDialog`. Vedi [14 — Assessment lesson](14-assessment-lesson.md).

### Export CSV della verifica (client-side)

Helper interni a `CourseLessonContentView.tsx`: `buildAssessmentCsv()` +
`csvCell()` (quoting RFC-4180, separatore `;`). Una riga per domanda; le
colonne opzione sono dimensionate sul massimo numero di opzioni fra le
MC. `handleExportAssessmentCsv()` antepone un BOM UTF-8 (Excel-friendly),
crea un `Blob` `text/csv` e lo scarica via `<a download>`. Nessuna
chiamata al backend: il CSV è costruito interamente lato client dal
`content_raw` già in cache.

### Mutations

- Content: `generateLessonMut`, `generateAllMut`, `approveLessonMut`,
  `approveAllMut`, `editLessonMut`
- Assessment: `editLessonMut` invia `LessonAssessmentUpdateInput` a
  `coursesApi.lessonContent.updateAssessment` quando la lezione editata
  è `is_assessment`
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

## Verifica delle competenze (assessment)

Vedi [14 — Assessment lesson](14-assessment-lesson.md) per il flusso
completo. La verifica vive **dentro la scheda Contenuti** (Fase 3): non
ha un tab proprio. Due componenti dedicati, più i marker descritti
sopra in `CourseLessonContentView.tsx`.

### `LessonAssessmentView.tsx` (viewer read-only)

Render della verifica nel corpo espanso di una lezione `is_assessment`.
Una sezione per le domande a scelta multipla (opzioni in lista,
**opzione corretta evidenziata** in verde con icona `CheckCircle2`) e
una per le domande aperte (testo + box "Risposta attesa"). Empty state
se non ci sono domande.

### `LessonAssessmentEditDialog.tsx`

Editor del `content_raw` di una verifica (`max-w-3xl`, `<ScrollArea>`
interno). Due sezioni:
- **Scelta multipla**: per domanda testo + lista opzioni (2..6) +
  `radio` per marcare l'opzione corretta. Le `option_id` (A..F) sono
  ri-assegnate per indice in fase di submit; lo stato locale traccia
  `correctIndex`, non l'id.
- **Domande aperte**: testo + risposta attesa.

`question_id` generati lato client (`q-{8 hex}`). Submit invia
`LessonAssessmentUpdateInput`; la validazione hard è server-side.

Le righe `is_assessment` della scheda **Struttura lezioni** mostrano un
badge "Verifica competenze" (icona `ListChecks`), analogo al badge
"Introduttiva" delle lezioni didattiche.

## `CourseLessonVideoView.tsx` (Fase 6 — Video)

Tab "Video" del wizard. Genera il video MP4 della lezione (slide +
discorso parlato con voce clonata). Vedi
[12 — Lesson video](12-lesson-video.md).

Layout:

- **Card selettore lingua TTS** (sempre in cima): `Select` sulle 16
  lingue `XTTS_SUPPORTED_LANGUAGES`, più il sentinel
  `__course_default__` (visibile solo se la lingua del corso è
  supportata) per resettare l'override a NULL. La mutation patcha
  `course.video_language_code` (stringa vuota → reset). La lingua
  effettiva è override → corso → `"it"`.
- **Banner lingua non supportata**: se la lingua del corso non è in
  `XTTS_SUPPORTED_LANGUAGES` e non c'è override, banner ambra che invita
  a scegliere una lingua compatibile.
- **Banner voice sample mancante**: ambra quando l'avatar
  dell'assegnatario non ha il campione vocale.
- **Aggregate card**: progress aggregato + CTA "Genera tutti" /
  "Annulla tutti", contatori `ready/total`, badge lezioni `failed`,
  **ETA** (via `useBatchEta`) durante un batch attivo.
- **Lista lezioni** (raggruppate per modulo): delegata alla vista media
  condivisa `<LessonMediaView variant="video">` (vedi
  [§ Vista media condivisa](#vista-media-condivisa-componentsmedia)). Le
  parti specifiche del tab Video — status badge, avvisi (speech/slides non
  approvati, stale, errore), CTA Genera/Rigenera/Annulla/Scarica, label
  fase (`tts`/`rendering_slides`/`encoding`/`preparing`), chip `tokens`
  (durata, device, dimensione file) e nome file `${lesson_code}.mp4` —
  arrivano via l'adapter `MediaRenderers<LessonVideoStatusOut>` definito
  nella view.

Hook: `useLessonVideo.ts` — `useCourseVideoStatus`, `useLessonVideoStatus`
(per-lezione) + 4 mutation hook (`useGenerateLessonVideo`,
`useGenerateAllVideos`, `useCancelLessonVideo`, `useCancelAllVideos`).
Refetch ogni 2s finché ci sono job in flight. API:
`coursesApi.lessonVideo` (6 metodi).

## `CourseLessonAvatarVideoView.tsx` (Fase 6b — Video con avatar)

Tab "Video con avatar" del wizard. Mirror strutturale di
`CourseLessonVideoView`: prende il video MP4 già generato e ci
sovrappone l'avatar parlante (lip-sync MuseTalk). Vedi
[13 — Avatar video](13-avatar-video.md).

Layout:

- **Banner avatar senza clip**: ambra quando l'avatar dell'assegnatario
  non ha clip MiniMax pronte (`avatar_clips_ready === false`).
- **Aggregate card**: descrizione + progress aggregato + CTA "Genera
  tutti" / "Annulla tutti", contatori, badge `failed`, **ETA**.
- **Lista lezioni**: come per il tab Video, delegata a
  `<LessonMediaView variant="avatar">`
  ([§ Vista media condivisa](#vista-media-condivisa-componentsmedia)).
  L'adapter `MediaRenderers<LessonAvatarVideoStatusOut>` fornisce status
  badge, avvisi ("video della lezione non pronto" / stale / errore), CTA
  Genera/Rigenera/Annulla/Scarica, label fase (`preparing`/`lipsync`/
  `overlay`), chip `tokens` (durata, n. clip, dimensione file) e nome file
  `${lesson_code}-avatar.mp4`.

Nessun selettore lingua (l'audio è ereditato dal video della lezione).

Hook: `useLessonAvatarVideo.ts` — `useCourseAvatarVideoStatus` + 4
mutation hook (`useGenerateLessonAvatarVideo`, `useGenerateAllAvatarVideos`,
`useCancelLessonAvatarVideo`, `useCancelAllAvatarVideos`). Refetch ogni
2s finché ci sono job in flight. API: `coursesApi.lessonAvatarVideo`.

I tre parametri MuseTalk per-avatar (`musetalk_extra_margin`,
`musetalk_left_cheek_width`, `musetalk_right_cheek_width`) si
configurano da `MyAvatarPage.tsx` (sezione avanzata) via
`PATCH /me/avatar/musetalk-params`, non da questa scheda.

## Vista media condivisa (`components/media/`)

I tab **Video** (Fase 6) e **Video con avatar** (Fase 6b) condividono la
stessa presentazione delle lezioni tramite i componenti in
`components/media/`. Sostituisce le vecchie "card per lezione" con player
`<video>` incorporato (che allungavano a dismisura la pagina) con una
**vista compatta** lista/griglia, moduli collassabili e **player in
modale**. Ogni view (`CourseLessonVideoView`/`CourseLessonAvatarVideoView`)
mantiene header, banner e fetch propri e passa le parti specifiche per
variante via un `MediaRenderers`.

### `LessonMediaView.tsx`

**File**: `components/media/LessonMediaView.tsx`.
**Scopo**: render condiviso della lista lezioni dei tab media.
**Esporta**: `LessonMediaView<TItem extends MediaStatusItem>`.

- **Props**: `course: CourseOut`, `variant: "video" | "avatar"`,
  `itemByLessonId: Map<string, TItem>`, `renderers: MediaRenderers<TItem>`.
- **Comportamento**: filtra `course.modules` ai soli moduli con almeno una
  lezione presente in `itemByLessonId` (preservando l'ordine); se nessuno,
  rende `null`. Mostra un `MediaViewToggle` (Lista/Griglia) e una
  `MediaModuleSection` per modulo con contatore `ready/total`. In modalità
  `list` rende una `LessonMediaRow` per lezione, in `grid` una
  `LessonMediaCard` (grid `1 / sm:2 / xl:3` colonne). Le etichette sono
  sempre **"Modulo N"/"Lezione N"** (via `useCourseLabels` →
  `moduleLabel`/`lessonLabel`), mai i codici tecnici `M1`/`M1.L2`. Lo stato
  di view + moduli collassati arriva da `useMediaView(course.id, variant)`.
- **Player in modale**: tiene lo stato `playing` (lezione + modulo + item) e
  monta un singolo `VideoPlayerModal`; click su riga/card pronta lo apre con
  titolo `"Modulo N · Lezione N — Titolo"`, `videoUrl`, nome file di download
  (`renderers.downloadName`) e metadata (`renderers.tokens`).

### `useMediaView.ts`

**File**: `components/media/useMediaView.ts`.
**Esporta**: `useMediaView(courseId, variant)` + tipo `MediaViewMode = "list" | "grid"`.

- **Restituisce**: `{ viewMode, setViewMode, collapsed: Set<string>, toggleModule }`.
- **Comportamento**: persiste in localStorage, separato **per corso e per
  variante** (chiavi `lesson-media-view:{courseId}:{variant}` e
  `lesson-media-collapsed:{courseId}:{variant}`), così la scelta su "Video"
  non si trascina su "Video con avatar". Default `viewMode = "grid"`. Lazy
  init + `try/catch` tollerante agli errori di storage (stesso pattern di
  `useColumnVisibility`).

### `types.ts`

**File**: `components/media/types.ts`.
**Esporta**: interfacce `MediaStatusItem` e `MediaRenderers<TItem>`.

- `MediaStatusItem`: sottoinsieme comune di `LessonVideoStatusOut` e
  `LessonAvatarVideoStatusOut` (`lesson_id`, `status`, `progress`,
  `progress_phase`, `video_url`, `error`, `is_stale`); entrambi i tipi BE
  vi sono strutturalmente assegnabili.
- `MediaRenderers<TItem>`: adapter forniti da ciascuna view —
  `statusBadge`, `warnings`, `actions(lesson, item)`, `tokens`,
  `phaseLabel`, `downloadName(lesson, item)`. È il punto in cui le parti
  per-variante restano nella pagina che le possiede.

### `MediaViewToggle.tsx`

Segmented control **Lista ↔ Griglia** (`button` nativi, icone `List`/
`LayoutGrid`; non esiste una primitive `ToggleGroup` nel design system).
i18n `courses.media.{viewList, viewGrid}`.

### `MediaModuleSection.tsx`

Sezione modulo **collassabile**: header cliccabile con "Modulo N · Titolo",
contatore `Badge` `courses.media.readyCount` (`{{ready}}/{{total}} pronti`,
`variant="default"` quando tutte pronte) e chevron; il contenuto è nascosto
quando `collapsed`.

### `LessonMediaRow.tsx` / `LessonMediaCard.tsx`

Riga (Lista) e card (Griglia) di una lezione. Pulsante ▶ abilitato solo
quando `status === "ready" && video_url` (altrimenti icona `FilmIcon`
disabilitata); click → `onPlay()`. Entrambi rendono
`renderers.statusBadge`, `renderers.warnings`, `renderers.actions` e una
`Progress` inline con `phaseLabel` + percentuale durante
`pending`/`processing`. La card usa un tile `aspect-[99/70]` su sfondo
neutro (niente poster reale).

### `VideoPlayerModal.tsx`

Player in **modale** (`Dialog`, `max-w-3xl`): `<video controls autoPlay>`
`aspect-[99/70]`, metadata opzionali sotto e bottone "Scarica MP4"
(`courses.media.modalDownload`, apre in nuova scheda). Sostituisce i player
incorporati.

i18n della vista media: `courses.media.{viewList, viewGrid, readyCount,
play, modalDownload}`.

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
