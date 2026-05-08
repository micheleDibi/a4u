# Frontend 08 — Hooks

La cartella `src/hooks/` ospita i custom hooks promossi (riusati da più
moduli o con logica non triviale). Gli altri sono inline nei moduli che
li espongono.

- `useAuth()` → `auth/AuthContext.tsx` (inline).
- `useHasPermission(code, orgId?)` → `auth/PermissionGate.tsx` (inline).
- `useEffectiveOrgId()` → `src/hooks/useEffectiveOrgId.ts`.
- `useBatchEta(tasks)` → `src/hooks/useBatchEta.ts` (ETA per batch AI).
- `useTaskEta(key, isActive, progress)` → `src/hooks/useTaskEta.ts` (ETA per task singolo).

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

## Quando aggiungere hooks

Promuovere a `src/hooks/` quando:

- L'hook è usato da ≥2 componenti distinti.
- Ha logica complessa (es. caching, debounce, IntersectionObserver).
- Vorresti testarlo in isolamento.

### Esempi candidati per il futuro

- `useDebouncedValue<T>(value, delay)`: per le ricerche con debounce nei
  TextField (oggi inline nei pages).
- `useOrganization()`: helper che combina `useParams().orgId` con
  `me.organizations.find(...)`.
- `useIsPlatformAdmin()`: shorthand per `useAuth().me?.is_platform_admin`.

Non astrarre prima del 2°/3° utilizzo (regola del DRY tardivo).
