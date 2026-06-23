# Frontend 08 — Hooks

La cartella `src/hooks/` ospita i custom hooks promossi (riusati da più
moduli o con logica non triviale). Gli altri sono inline nei moduli che
li espongono.

- `useAuth()` → `auth/AuthContext.tsx` (inline).
- `useHasPermission(code, orgId?)` → `auth/PermissionGate.tsx` (inline).
- `useEffectiveOrgId()` → `src/hooks/useEffectiveOrgId.ts`.
- `useBatchEta(tasks)` → `src/hooks/useBatchEta.ts` (ETA per batch AI).
- `useTaskEta(key, isActive, progress)` → `src/hooks/useTaskEta.ts` (ETA per task singolo).
- `useLessonVideo*` → `src/hooks/useLessonVideo.ts` (query/mutation del video MP4 della lezione, Fase 6).
- `useLessonAvatarVideo*` → `src/hooks/useLessonAvatarVideo.ts` (query/mutation del «Video con Avatar», Fase 6b).
- `useColumnVisibility(storageKey, defaults)` → `src/hooks/useColumnVisibility.ts` (visibilità colonne DataTable persistita in localStorage).
- `useMediaView(courseId, variant)` → co-locato in `src/pages/org/courses/components/media/useMediaView.ts` (stile vista + moduli collassati dei tab media, persistiti in localStorage).

Helper correlati:
- `formatDuration(ms)` → `src/lib/formatDuration.ts` (formattatore "1m 30s" da ms).

---

## `useEffectiveOrgId()` — `src/hooks/useEffectiveOrgId.ts`

**Scopo**: ritornare l'organizzazione "corrente" anche quando l'URL non
contiene `:orgId`. Necessario perché alcune pagine (es. `/me/avatar`
o `/admin/...`) non sono org-scoped, ma la `Sidebar` deve continuare a
mostrare l'`OrgSwitcher` con la selezione precedente e a propagare
l'orgId ai check di permesso del menù.

### Firma

```ts
function useEffectiveOrgId(): string | undefined;
```

### Implementazione

1. Legge `useParams().orgId`.
2. Se valorizzato → lo persiste in `localStorage["a4u.lastOrgId"]` e lo
   ritorna.
3. Altrimenti ritorna `localStorage["a4u.lastOrgId"]` (o `undefined`).

### Esempio

```tsx
const orgId = useEffectiveOrgId();
// /me/avatar  -> ritorna l'ultima org persistita (se mai selezionata)
// /orgs/abc   -> ritorna "abc" e aggiorna localStorage
```

Usato da `Sidebar.tsx`, `OrgSwitcher.tsx` e (di default) dai
`PermissionItem` quando `permissionOrgId` non è esplicitamente passato.

---

## `useBatchEta(tasks)` — `src/hooks/useBatchEta.ts`

**Scopo**: stimare il **tempo rimanente** di un batch AI long-running
(struttura lezioni di Fase 2, contenuti di Fase 3) basandosi sui
timestamp di completamento dei task già pronti.

### Firma

```ts
interface BatchEtaTask {
  status: string;                  // "ready" | "approved" | "pending" | "processing" | ...
  completedAt: string | null;       // ISO timestamp dal backend
}

interface BatchEtaResult {
  completed: number;                // task in {ready, approved}
  active: number;                   // task in {pending, processing}
  total: number;                    // completed + active (esclude empty/failed)
  remaining: number;                // = active
  avgPerTaskMs: number | null;      // velocità media nei task recenti
  etaMs: number | null;             // = avgPerTaskMs × remaining
}

function useBatchEta(tasks: BatchEtaTask[]): BatchEtaResult;
```

### Implementazione

1. Filtra `completedTimes`: solo task in `{ready, approved}` con
   `completedAt` valido **e** entro la **recent window di 90 minuti**
   (esclude task `ready` di sessioni precedenti che falserebbero la
   velocità).
2. Se `completedTimes.length >= 2`, ordina e calcola
   `avgPerTaskMs = (max - min) / (count - 1)` (intervallo medio tra
   completamenti). Senza almeno 2 timestamp, ritorna `null`.
3. `etaMs = avgPerTaskMs × remaining` se entrambi disponibili e remaining > 0.
4. Tick interno via `setInterval(5_000)` per re-render del display
   (countdown decrescente anche tra polling TanStack).

### Esempio

```tsx
const eta = useBatchEta(
  course.modules.map((m) => ({
    status: m.lessons_structure_status,
    completedAt: m.lessons_structure_generated_at,
  })),
);

if (anyActive && eta.etaMs !== null) {
  return <span>{t("courses.lessonsStructure.aggregate.eta", {
    time: formatDuration(eta.etaMs),
  })}</span>;
}
```

Usato da `CourseLessonStructureView.tsx` e `CourseLessonContentView.tsx`.

---

## `useTaskEta(taskKey, isActive, progress)` — `src/hooks/useTaskEta.ts`

**Scopo**: stimare ETA per un **task singolo** (es. generazione architettura
corso, Fase 1) quando il backend non espone uno `started_at`.

### Firma

```ts
interface TaskEtaResult {
  elapsedMs: number | null;
  etaMs: number | null;
}

function useTaskEta(
  taskKey: string,
  isActive: boolean,
  progress: number,  // 0..100
): TaskEtaResult;
```

### Implementazione

1. Persiste il timestamp di inizio in
   `sessionStorage["task_eta_started_at:{taskKey}"]` la prima volta che
   `isActive=true`. Sopravvive a refresh / navigation tab finché la
   sessione browser è aperta.
2. Pulisce lo storage quando `isActive=false` (task completato/fallito).
3. `elapsedMs = Date.now() - storedStart`.
4. `etaMs = (elapsedMs / progress) × (100 - progress)` quando
   `progress ≥ 5%` (sotto soglia troppo rumoroso → null, mostra solo elapsed).
5. Tick interno via `setInterval(5_000)`.

### Esempio

```tsx
const archEta = useTaskEta(
  `arch:${course.id}`,
  course.status === "architecture_pending",
  course.architecture_progress ?? 0,
);

if (archEta.etaMs !== null) {
  return <span>ETA: ~{formatDuration(archEta.etaMs)}</span>;
}
if (archEta.elapsedMs && archEta.elapsedMs > 1_000) {
  return <span>Trascorso: {formatDuration(archEta.elapsedMs)}</span>;
}
```

Usato da `ArchitectureSection` in `CourseEditorPage.tsx`.

---

## `formatDuration(ms)` — `src/lib/formatDuration.ts`

Helper sincrono che formatta una durata in ms come stringa breve umana.

```ts
formatDuration(45_000)     // "45s"
formatDuration(90_000)     // "1m 30s"
formatDuration(3_660_000)  // "1h 1m"
formatDuration(7_200_000)  // "2h"
formatDuration(100)        // "<1s"
```

Usato accoppiato a `useBatchEta` / `useTaskEta` per evitare di duplicare
la logica di rendering "tempo umano".

---

## `useLessonVideo.ts` — hook del video MP4 della lezione (Fase 6)

**Scopo**: query di stato (con polling) e mutation per la generazione
del video MP4 della lezione (vedi
[Courses 12 — Lesson video](../courses/12-lesson-video.md)). Wrappano
`coursesApi.lessonVideo`.

Costanti modulo: `REFETCH_ACTIVE_MS = 2_000`, `REFETCH_IDLE_MS = false`,
`STALE_MS = 1_000`. Query key: `["course-video-status", orgId, courseId]`
e `["lesson-video-status", orgId, courseId, lessonId]`.

### Query hook

```ts
function useLessonVideoStatus(
  orgId, courseId, lessonId,
): UseQueryResult<LessonVideoStatusOut>;

function useCourseVideoStatus(
  orgId, courseId,
): UseQueryResult<LessonVideoBatchOut>;
```

- `useLessonVideoStatus` — status di una singola lezione.
- `useCourseVideoStatus` — aggregato pagina-corso (card "Genera tutti i
  video" + lista per-lezione).
- Entrambi: `enabled` solo con tutti gli id valorizzati; `refetchInterval`
  dinamico — refetch ogni **2 s** finché un job è in flight
  (`status ∈ {pending, processing}` per la singola, oppure
  `pending_count + processing_count > 0` per l'aggregato), poi si ferma.

### Mutation hook

```ts
function useGenerateLessonVideo(); // generate per lezione
function useGenerateAllVideos();   // generate batch
function useCancelLessonVideo();   // cancel per lezione
function useCancelAllVideos();     // cancel batch
```

Ogni mutation, su `onSuccess`, invalida **sia** lo status del corso
(`course-video-status`) **sia** lo status puntuale della lezione
(`lesson-video-status`), così la tab Video e il banner pagina-corso
restano coerenti. Le mutation batch invalidano il prefisso
`["lesson-video-status", orgId, courseId]`.

Usati da `CourseLessonVideoView.tsx` (vedi
[05 — Components](05-components.md)).

---

## `useLessonAvatarVideo.ts` — hook del «Video con Avatar» (Fase 6b)

**Scopo**: query di stato (con polling) e mutation per il «Video con
Avatar» — lip-sync MuseTalk sovrapposto al video della lezione (vedi
[Courses 13 — Avatar video](../courses/13-avatar-video.md)). Wrappano
`coursesApi.lessonAvatarVideo`.

Stesse costanti di `useLessonVideo.ts`. Query key:
`["course-avatar-video-status", orgId, courseId]` e
`["lesson-avatar-video-status", orgId, courseId, lessonId]`.

### Query hook

```ts
function useCourseAvatarVideoStatus(
  orgId, courseId,
): UseQueryResult<LessonAvatarVideoBatchOut>;
```

Aggregato pagina-corso per la scheda «Video con avatar»: card "Genera
tutti" + lista per-lezione. `refetchInterval` dinamico — refetch ogni
**2 s** se `pending_count + processing_count > 0`, poi si ferma.

> A differenza di `useLessonVideo.ts` non c'è un hook di status
> per-lezione: la scheda «Video con avatar» usa solo l'aggregato.

### Mutation hook

```ts
function useGenerateLessonAvatarVideo();
function useGenerateAllAvatarVideos();
function useCancelLessonAvatarVideo();
function useCancelAllAvatarVideos();
```

Su `onSuccess` invalidano l'aggregato `course-avatar-video-status` e lo
status puntuale `lesson-avatar-video-status` (le batch invalidano il
prefisso). Usati da `CourseLessonAvatarVideoView.tsx`.

---

## `useAdminMetrics()` — `src/hooks/useAdminMetrics.ts`

**Scopo**: snapshot metriche platform-wide per la `AdminDashboard`
(vedi [06 — Pages](06-pages.md)). Wrapper su `adminMetricsApi.get()`.

Costanti: `REFETCH_MS = 60_000` (coerente con il TTL della cache
backend), `STALE_MS = 30_000`. Query key: `["admin-metrics"]`.

```ts
function useAdminMetrics(): UseQueryResult<AdminMetricsOut>;
```

Nessun argomento (l'endpoint è singleton). `refetchOnWindowFocus`
disabilitato — la cache backend rende inutile il fetch on focus.
Richiede `is_platform_admin=true` lato server (403 altrimenti).

---

## `useOrgMetrics(orgId)` — `src/hooks/useOrgMetrics.ts`

**Scopo**: snapshot metriche org-scoped per la `OrgDashboard`. Wrapper
su `orgMetricsApi.get(orgId)`.

Stesse costanti di `useAdminMetrics`. Query key: `["org-metrics",
orgId]`. `enabled` solo con `orgId` valorizzato.

```ts
function useOrgMetrics(
  orgId: string | null | undefined,
): UseQueryResult<OrgMetricsOut>;
```

Niente cache lato server (org-scoped già scoped); refetch 60s come
default ragionevole. Richiede `course:view` nell'org (gate backend).

---

## `useDebouncedValue<T>(value, delayMs)` — `src/hooks/useDebouncedValue.ts`

**Scopo**: ritarda l'aggiornamento di un valore reattivo finché non è
rimasto stabile per `delayMs` ms. Utile per ricerche testuali / filtri
che colpiscono la rete senza spamare query per ogni keystroke.

```ts
function useDebouncedValue<T>(value: T, delayMs?: number = 300): T;
```

Implementazione semplice (`setTimeout` + `clearTimeout` in `useEffect`),
zero dipendenze. Usato da `CoursesListPage` per il debounce della
search testuale nei filtri (300 ms, sync verso `useSearchParams`).

---

## `useColumnVisibility(storageKey, defaults)` — `src/hooks/useColumnVisibility.ts`

**Scopo**: gestire la visibilità delle colonne di una `DataTable`
persistendola in `localStorage` per-browser. Modellato sul pattern di
`useMediaView` (lazy init + `try/catch` tollerante agli errori di
storage).

### Firma

```ts
import type { VisibilityState } from "@tanstack/react-table";

function useColumnVisibility(
  storageKey: string,
  defaults: VisibilityState,
): {
  columnVisibility: VisibilityState;
  setColumnVisibility: (next: VisibilityState) => void;
};
```

### Implementazione

1. Lazy init dello stato: legge `localStorage[storageKey]`; se presente
   fa il merge `{ ...defaults, ...parsed }` — così se in futuro si
   aggiunge una colonna questa eredita il suo default finché l'utente
   non la tocca, senza rompere la preferenza già salvata. Su errore di
   parse / storage, cade su `defaults`.
2. `setColumnVisibility(next)` aggiorna lo stato **e** scrive l'intero
   oggetto in `localStorage` (`JSON.stringify`), in `try/catch`.

### Esempio

```tsx
const { columnVisibility, setColumnVisibility } = useColumnVisibility(
  "courses-list-columns",
  DEFAULT_COLUMN_VISIBILITY,
);
```

Usato da `CoursesListPage` (chiave `"courses-list-columns"`) e
inoltrato sia a `DataTable` (`columnVisibility` /
`onColumnVisibilityChange`) sia al `DataTableColumnToggle` (vedi
[05 — Components](05-components.md) e [06 — Pages](06-pages.md)).

---

## `useMediaView(courseId, variant)` — `src/pages/org/courses/components/media/useMediaView.ts`

**Scopo**: stato di presentazione condiviso dei tab media (Video / Video
con Avatar): stile di vista (lista compatta vs griglia) e set di moduli
collassati. Entrambi persistiti in `localStorage`, separati **per corso**
e **per variante** (così la scelta su "Video" non si trascina su "Video
con Avatar"). Co-locato con i componenti media, non in `src/hooks/`.

### Firma

```ts
type MediaViewMode = "list" | "grid";

function useMediaView(
  courseId: string,
  variant: string,   // "video" | "avatar"
): {
  viewMode: MediaViewMode;
  setViewMode: (next: MediaViewMode) => void;
  collapsed: Set<string>;            // id moduli chiusi
  toggleModule: (moduleId: string) => void;
};
```

### Implementazione

1. Due chiavi localStorage derivate da `courseId` + `variant`:
   `lesson-media-view:{courseId}:{variant}` e
   `lesson-media-collapsed:{courseId}:{variant}`.
2. `viewMode`: lazy init dal valore salvato (`"list"`/`"grid"`), default
   `"grid"`. `setViewMode` aggiorna stato + storage.
3. `collapsed`: `Set<string>` lazy init dall'array serializzato; un
   `useEffect` lo ri-serializza (`JSON.stringify([...collapsed])`) ad
   ogni cambio. `toggleModule(moduleId)` aggiunge/rimuove l'id dal set.
4. Tutte le scritture in `try/catch` (storage tollerante agli errori).

Usato da `LessonMediaView` (vedi [05 — Components](05-components.md)),
condiviso da `CourseLessonVideoView` e `CourseLessonAvatarVideoView`.

---

## Quando aggiungere hooks

Promuovere a `src/hooks/` quando:

- L'hook è usato da ≥2 componenti distinti.
- Ha logica complessa (es. caching, debounce, IntersectionObserver).
- Vorresti testarlo in isolamento.

### Esempi candidati per il futuro

- `useOrganization()`: helper che combina `useParams().orgId` con
  `me.organizations.find(...)`.
- `useIsPlatformAdmin()`: shorthand per `useAuth().me?.is_platform_admin`.

Non astrarre prima del 2°/3° utilizzo (regola del DRY tardivo).
