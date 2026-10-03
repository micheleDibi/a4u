import { useEffect, useRef } from "react";
import {
  hashKey,
  useQuery,
  useQueryClient,
  type QueryCacheNotifyEvent,
} from "@tanstack/react-query";

import {
  coursesApi,
  type CourseOut,
  type CourseStatusOut,
} from "@/api/courses";

/**
 * Polling dello stato leggero del corso (`GET .../status`, contratto
 * `docs/contracts/perf-l1-course-status.md`).
 *
 * Solo questa query polla: il dettaglio del corso (`["courses", "detail",
 * org, course]`, ~1,5 MB compressi su un corso da 72 lezioni) si ricarica
 * solo quando, in cache, non riflette uno stato sostanziale di `/status`,
 * mentre l'avanzamento dei job viene copiato nel dettaglio in cache con una
 * patch locale che crea oggetti nuovi solo per le entità cambiate (le righe
 * ferme non si ri-renderizzano).
 */

/** Intervallo di poll con un job attivo, subito dopo un cambiamento (ms). */
const ACTIVE_POLL_MS = 2_000;
/** Tetto del backoff con job attivo ma corpo invariato: 2 → 4 → 8 → 10 s. */
const ACTIVE_POLL_MAX_MS = 10_000;
/** Intervallo di poll senza job attivi (ms). */
const IDLE_POLL_MS = 30_000;

const ACTIVE_JOB_STATUSES = new Set(["pending", "processing"]);

/** Prefissi dei job di lezione che rendono il corso "attivo" (regola 2). */
const LESSON_JOB_PREFIXES = [
  "content",
  "slides",
  "speech",
  "pdf",
  "slides_pdf",
  "speech_pdf",
  "video",
  "avatar_video",
] as const;

type StatusValue = string | null | undefined;
type LessonJobStatusKey = `${(typeof LESSON_JOB_PREFIXES)[number]}_status`;

/** Forma minima per stabilire se un corso è attivo: la soddisfano sia
 *  `CourseStatusOut` sia il dettaglio `CourseOut` (che non ha i campi
 *  video delle lezioni). */
export interface CourseActivityShape {
  status: string;
  glossary_status: string;
  documents: readonly { summary_status: string; figures_status: StatusValue }[];
  modules: readonly {
    lessons_structure_status: string;
    lessons: readonly Partial<Record<LessonJobStatusKey, StatusValue>>[];
  }[];
}

const isJobActive = (s: StatusValue): boolean =>
  s != null && ACTIVE_JOB_STATUSES.has(s);

/** Regola 2 del contratto: c'è almeno un job in coda o in elaborazione. */
export function isCourseActive(c: CourseActivityShape): boolean {
  if (c.status === "architecture_pending") return true;
  if (isJobActive(c.glossary_status)) return true;
  if (
    c.documents.some(
      (d) => isJobActive(d.summary_status) || isJobActive(d.figures_status),
    )
  ) {
    return true;
  }
  return c.modules.some(
    (m) =>
      isJobActive(m.lessons_structure_status) ||
      m.lessons.some((l) =>
        LESSON_JOB_PREFIXES.some((p) => isJobActive(l[`${p}_status`])),
      ),
  );
}

/** Regola 3: 2 s da attivo, raddoppio per ogni poll con corpo invariato
 *  fino a 10 s; 30 s senza job attivi. */
export function pollIntervalMs(active: boolean, unchangedStreak: number): number {
  if (!active) return IDLE_POLL_MS;
  const exp = Math.max(0, Math.min(unchangedStreak, 10));
  return Math.min(ACTIVE_POLL_MS * 2 ** exp, ACTIVE_POLL_MAX_MS);
}

/**
 * `refetchInterval` della query `/status`. Senza dati: durante il primo
 * fetch nessun timer (il fetch iniziale parte comunque); se il primo GET
 * è fallito anche dopo il retry si riprova ogni 30 s, altrimenti il
 * polling non ripartirebbe più fino al reload della pagina.
 */
export function statusPollInterval(
  data: CourseStatusOut | undefined,
  queryStatus: "pending" | "error" | "success",
  unchangedStreak: number,
): number | false {
  if (!data) return queryStatus === "error" ? IDLE_POLL_MS : false;
  return pollIntervalMs(isCourseActive(data), unchangedStreak);
}

export type StatusFieldKind = "substantial" | "progress" | "ignored";

/** Campi che non ricaricano né patchano il dettaglio (contano solo per il
 *  backoff): gli id; `updated_at`, che cambia a ogni tick dell'architettura;
 *  `video_status`/`avatar_video_status`, che valgono per "attivo" (regola 2)
 *  ma che il dettaglio non contiene (eccezione della regola 4). */
const IGNORED_FIELDS = new Set([
  "id",
  "course_id",
  "updated_at",
  "video_status",
  "avatar_video_status",
]);
/** Liste annidate: confrontate per id, non come campi. */
const NESTED_FIELDS = new Set(["documents", "modules", "lessons"]);
const SUBSTANTIAL_FIELDS = new Set([
  "status",
  "figures_error_code",
  "figures_count",
  "figures_coverage",
]);
const SUBSTANTIAL_SUFFIXES = [
  "_status",
  "_generated_at",
  "_approved_at",
  "_modified_at",
  "_error",
];

/**
 * Classifica un campo della risposta `/status`.
 * - `substantial` (regola 4): stati, timestamp di generazione, approvazione
 *   e modifica, errori, esito delle figure → ricarica del dettaglio se in
 *   cache è diverso; un `*_status` fra pending e processing è però
 *   avanzamento (eccezione 2, vedi `isActiveStatusShift`).
 * - `progress` (regola 5): `*_progress`, `*_progress_phase`, `*_attempts`,
 *   `summary_chunks_*`, `figures_pages_*`, `figures_progress`; più i pochi
 *   campi che il contratto non elenca (`summary_coverage`,
 *   `figures_requested_at`) → patch locale.
 * - `ignored`: id, `updated_at`, stati video e liste annidate.
 */
export function classifyStatusField(key: string): StatusFieldKind {
  if (IGNORED_FIELDS.has(key) || NESTED_FIELDS.has(key)) return "ignored";
  if (
    SUBSTANTIAL_FIELDS.has(key) ||
    SUBSTANTIAL_SUFFIXES.some((s) => key.endsWith(s))
  ) {
    return "substantial";
  }
  return "progress";
}

type Row = Record<string, unknown>;
type EntityKind = "course" | "document" | "module" | "lesson";

/** Un campo sostanziale di `/status` che il dettaglio in cache non riflette. */
export interface StatusChange {
  entity: EntityKind;
  id: string;
  field: string;
  /** Valore nel dettaglio in cache. */
  detailValue: unknown;
  /** Valore nella risposta `/status`. */
  value: unknown;
}

/** Differenze fra la risposta `/status` e il dettaglio in cache (regola 4). */
export interface CourseStatusDiff {
  /** Insiemi di id di documenti, moduli o lezioni (per modulo) diversi. */
  structural: boolean;
  /** Campi sostanziali che il dettaglio non riflette. */
  substantial: StatusChange[];
}

/** Uguaglianza profonda tra valori JSON; `null` e `undefined` coincidono. */
function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return a == null && b == null;
  if (typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
      return false;
    }
    return a.every((x, i) => sameValue(x, b[i]));
  }
  const ra = a as Row;
  const rb = b as Row;
  const keys = new Set([...Object.keys(ra), ...Object.keys(rb)]);
  for (const k of keys) {
    if (!sameValue(ra[k], rb[k])) return false;
  }
  return true;
}

/** Stesso valore nel dettaglio e nello stato; i timestamp si confrontano
 *  come istanti (tollera differenze di formato tra i due endpoint). */
function sameFieldValue(field: string, a: unknown, b: unknown): boolean {
  if (sameValue(a, b)) return true;
  if (field.endsWith("_at") && typeof a === "string" && typeof b === "string") {
    const ta = Date.parse(a);
    return Number.isFinite(ta) && ta === Date.parse(b);
  }
  return false;
}

/** Eccezione 2 della regola 4: un `*_status` che passa fra due stati attivi
 *  (pending ↔ processing) non cambia i dati del dettaglio, è avanzamento. */
function isActiveStatusShift(field: string, a: unknown, b: unknown): boolean {
  return (
    field.endsWith("_status") &&
    typeof a === "string" &&
    typeof b === "string" &&
    isJobActive(a) &&
    isJobActive(b)
  );
}

function sameIdSet(
  a: readonly { id: string }[],
  b: readonly { id: string }[],
): boolean {
  if (a.length !== b.length) return false;
  const ids = new Set(a.map((x) => x.id));
  return b.every((x) => ids.has(x.id));
}

/** Gli insiemi di id della risposta `/status` coincidono con il dettaglio
 *  (confronto per id, mai per posizione). */
function sameIdSets(status: CourseStatusOut, detail: CourseOut): boolean {
  if (!sameIdSet(status.documents, detail.documents)) return false;
  if (!sameIdSet(status.modules, detail.modules)) return false;
  const detailModules = new Map(detail.modules.map((m) => [m.id, m]));
  return status.modules.every((m) => {
    const dm = detailModules.get(m.id);
    return !!dm && sameIdSet(m.lessons, dm.lessons);
  });
}

function indexDetail(detail: CourseOut): Map<string, Row> {
  const index = new Map<string, Row>();
  for (const d of detail.documents) index.set(`document:${d.id}`, d as unknown as Row);
  for (const m of detail.modules) {
    index.set(`module:${m.id}`, m as unknown as Row);
    for (const l of m.lessons) index.set(`lesson:${l.id}`, l as unknown as Row);
  }
  return index;
}

/**
 * Confronta la risposta `/status` con il dettaglio IN CACHE (regola 4), non
 * con il poll precedente: così un reload fallito o la risposta di una
 * mutazione arrivata in ritardo (che riporta indietro il dettaglio) vengono
 * corretti al poll successivo. E dopo una mutazione la risposta già in cache
 * riflette lo stato, quindi non parte un secondo GET del corso intero.
 */
export function diffCourseStatus(
  status: CourseStatusOut,
  detail: CourseOut,
): CourseStatusDiff {
  const diff: CourseStatusDiff = {
    structural: !sameIdSets(status, detail),
    substantial: [],
  };
  const detailRows = indexDetail(detail);
  const visit = (
    entity: EntityKind,
    id: string,
    statusRow: object,
    detailRow: Row | undefined,
  ) => {
    // Entità assente dal dettaglio: è già una differenza strutturale.
    if (!detailRow) return;
    for (const [field, value] of Object.entries(statusRow)) {
      if (classifyStatusField(field) !== "substantial") continue;
      // Un campo che il dettaglio non ha non si può confrontare; trattarlo
      // come differenza farebbe ricaricare il dettaglio a ogni poll.
      if (!(field in detailRow)) continue;
      const detailValue = detailRow[field];
      if (
        sameFieldValue(field, detailValue, value) ||
        isActiveStatusShift(field, detailValue, value)
      ) {
        continue;
      }
      diff.substantial.push({ entity, id, field, detailValue, value });
    }
  };
  visit("course", status.course_id, status, detail as unknown as Row);
  for (const d of status.documents) {
    visit("document", d.id, d, detailRows.get(`document:${d.id}`));
  }
  for (const m of status.modules) {
    visit("module", m.id, m, detailRows.get(`module:${m.id}`));
    for (const l of m.lessons) {
      visit("lesson", l.id, l, detailRows.get(`lesson:${l.id}`));
    }
  }
  return diff;
}

/** Il dettaglio in cache va ricaricato (una sola invalidate, regola 4). */
export function needsDetailReload(diff: CourseStatusDiff): boolean {
  return diff.structural || diff.substantial.length > 0;
}

/** Campo da copiare nel dettaglio con la patch locale: avanzamento
 *  (regola 5) o `*_status` fra due stati attivi (eccezione 2). */
function isPatchable(field: string, current: unknown, value: unknown): boolean {
  const kind = classifyStatusField(field);
  if (kind === "progress") return true;
  return kind === "substantial" && isActiveStatusShift(field, current, value);
}

/** Copia nella riga i campi patchabili cambiati; stessa riga se non cambia
 *  niente. */
function patchRow<T extends object>(row: T, status: object): T {
  let out: Row | null = null;
  for (const [field, value] of Object.entries(status)) {
    const current = (row as Row)[field];
    if (sameValue(current, value) || !isPatchable(field, current, value)) continue;
    out ??= { ...(row as Row) };
    out[field] = value;
  }
  return out ? (out as T) : row;
}

function patchList<D extends { id: string }, S extends { id: string }>(
  rows: D[],
  statusRows: readonly S[],
  patch: (row: D, status: S) => D,
): D[] {
  const byId = new Map(statusRows.map((s) => [s.id, s]));
  let changed = false;
  const out = rows.map((row) => {
    const s = byId.get(row.id);
    if (!s) return row;
    const patched = patch(row, s);
    if (patched !== row) changed = true;
    return patched;
  });
  return changed ? out : rows;
}

/**
 * Regola 5: copia nel dettaglio i campi di avanzamento della risposta
 * `/status` (e gli stati passati fra pending e processing). Crea oggetti
 * nuovi solo per le entità cambiate (e per i loro contenitori); se non
 * cambia niente restituisce lo stesso `detail`.
 */
export function applyProgressPatch(
  detail: CourseOut,
  status: CourseStatusOut,
): CourseOut {
  const course = patchRow(detail, status);
  const documents = patchList(detail.documents, status.documents, patchRow);
  const modules = patchList(detail.modules, status.modules, (m, sm) => {
    const row = patchRow(m, sm);
    const lessons = patchList(m.lessons, sm.lessons, patchRow);
    return lessons === m.lessons ? row : { ...row, lessons };
  });
  if (
    course === detail &&
    documents === detail.documents &&
    modules === detail.modules
  ) {
    return detail;
  }
  return { ...course, documents, modules };
}

/** Effetto di un evento della `queryCache` sulla detail key, letto dalla
 *  sottoscrizione di `useCourseStatus`. */
export type DetailEventEffect = "ignore" | "settled" | "fetched";

/**
 * - `fetched`: fetch del dettaglio riuscito (non una `setQueryData`, che è
 *   `manual`: la patch del hook o la risposta di una mutazione).
 * - `settled`: il fetch è finito senza dati nuovi: errore, annullamento
 *   (`cancelQueries` riporta lo stato a `idle` con un `setState`), reset
 *   o rimozione della query. Serve ad azzerare il flag anti-eco, che
 *   altrimenti resterebbe acceso e farebbe ignorare il fetch successivo.
 * - `ignore`: tutto il resto (avvio del fetch, `setQueryData`, observer).
 */
export function detailEventEffect(event: QueryCacheNotifyEvent): DetailEventEffect {
  if (event.type === "removed") return "settled";
  if (event.type !== "updated") return "ignore";
  const { action } = event;
  if (action.type === "error") return "settled";
  if (action.type === "setState" && event.query.state.fetchStatus === "idle") {
    return "settled";
  }
  if (action.type === "success" && !action.manual) return "fetched";
  return "ignore";
}

/**
 * Polla `/status` e tiene allineato il dettaglio in cache (regole 1-5).
 *
 * Perché la logica sta nel `queryFn` e non in un `useEffect` sui dati: un
 * effect dovrebbe osservare anche i poll a corpo invariato (per il backoff),
 * e quindi farebbe ri-renderizzare a ogni poll il componente che usa il
 * hook, cioè l'intero editor. Nel `queryFn` il confronto gira una sola
 * volta per fetch, anche con più observer, e chi usa il hook si
 * ri-renderizza solo se ne legge il risultato e il corpo cambia (lo
 * structural sharing di react-query conserva il riferimento se è uguale).
 */
export function useCourseStatus(
  orgId: string,
  courseId: string | undefined,
  enabled = true,
) {
  const qc = useQueryClient();
  // Poll consecutivi con corpo invariato: guida il backoff della regola 3.
  const unchangedStreak = useRef(0);
  // Flag anti-eco: vero da quando questo hook invalida il dettaglio fino
  // all'esito del fetch che ne segue (successo, errore, annullamento), così
  // la sottoscrizione qui sotto non scambia la ricarica fatta da noi per un
  // job avviato da un altro componente. Un'invalidate concorrente annulla
  // in silenzio il nostro fetch e ne avvia un altro: l'esito di quello
  // azzera comunque il flag, che quindi non resta acceso.
  const selfReload = useRef(false);

  const query = useQuery({
    queryKey: ["courses", "status", orgId, courseId],
    enabled: enabled && !!courseId,
    queryFn: async ({ signal }) => {
      // Se la request fallisce (rete, 5xx) o viene annullata, l'eccezione
      // esce qui: dettaglio non toccato e streak invariato.
      const next = await coursesApi.getStatus(orgId, courseId!, signal);
      // Poll annullato (es. invalidate della status key dopo una mutazione)
      // a risposta già arrivata: react-query la scarta, e confrontarla con
      // il dettaglio appena scritto dalla mutazione farebbe un GET in più.
      if (signal.aborted) return next;
      // Backoff (regola 3): conta i poll con corpo identico al precedente.
      const prev = qc.getQueryData<CourseStatusOut>([
        "courses",
        "status",
        orgId,
        courseId,
      ]);
      unchangedStreak.current =
        prev && sameValue(prev, next) ? unchangedStreak.current + 1 : 0;

      const detailKey = ["courses", "detail", orgId, courseId];
      const detailQuery = qc
        .getQueryCache()
        .find<CourseOut>({ queryKey: detailKey, exact: true });
      const detail = detailQuery?.state.data;
      if (!detailQuery || !detail) return next;

      if (needsDetailReload(diffCourseStatus(next, detail))) {
        // Una ricarica già in corso non si annulla con un'altra invalidate:
        // se il GET del dettaglio dura più dell'intervallo di poll verrebbe
        // annullato e rilanciato all'infinito. Se era partita prima del
        // cambio e torna indietro, il poll successivo la rifà. Lo stesso
        // vale per un reload fallito: si riprova al poll successivo.
        if (detailQuery.state.fetchStatus === "idle") {
          selfReload.current = detailQuery.isActive();
          void qc.invalidateQueries({ queryKey: detailKey, exact: true });
        }
      } else {
        // Regola 5. Se non cambia niente `applyProgressPatch` restituisce
        // lo stesso oggetto e non si scrive in cache (nessun evento).
        const patched = applyProgressPatch(detail, next);
        if (patched !== detail) qc.setQueryData(detailKey, patched);
      }
      return next;
    },
    // Rivalutato da react-query a ogni aggiornamento della query, quindi
    // anche subito dopo il fetch che ha aggiornato lo streak. Con la scheda
    // nascosta il poll si ferma (default `refetchIntervalInBackground`).
    refetchInterval: (q) =>
      statusPollInterval(q.state.data, q.state.status, unchangedStreak.current),
  });

  // Componenti che avviano un job e invalidano solo il dettaglio (upload e
  // import di documenti, estrazione figure): se il dettaglio ricaricato è
  // attivo mentre lo stato è fermo o in backoff, il poll riparte subito a
  // 2 s. Anti-eco: si guardano solo i fetch del dettaglio, perché
  // `action.manual` esclude le `setQueryData` (la patch di questo hook e le
  // risposte delle mutazioni, che invalidano già la status key da sole,
  // regola 6), e si salta il fetch provocato da questo hook (`selfReload`).
  useEffect(() => {
    if (!enabled || !courseId) return;
    const statusKey = ["courses", "status", orgId, courseId];
    const detailHash = hashKey(["courses", "detail", orgId, courseId]);
    return qc.getQueryCache().subscribe((event) => {
      if (event.query.queryHash !== detailHash) return;
      const effect = detailEventEffect(event);
      if (effect === "settled") {
        selfReload.current = false;
        return;
      }
      if (effect !== "fetched") return;
      if (selfReload.current) {
        selfReload.current = false;
        return;
      }
      const detail = event.query.state.data as CourseOut | undefined;
      const status = qc.getQueryData<CourseStatusOut>(statusKey);
      if (!detail || !status || !isCourseActive(detail)) return;
      if (isCourseActive(status) && unchangedStreak.current === 0) return;
      unchangedStreak.current = 0;
      void qc.invalidateQueries({ queryKey: statusKey, exact: true });
    });
  }, [qc, orgId, courseId, enabled]);

  return query;
}
