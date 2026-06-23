# Frontend 05 — `components/`

Componenti riusabili.

---

## `src/components/layout/AppLayout.tsx`

Shell dell'app: AppBar fissa, Drawer laterale (responsive), area
contenuto con `<Outlet>` di react-router.

### Costanti interne

- `DRAWER_WIDTH = 260`.

### `<AppLayout />`

Stato locale: `mobileOpen: boolean` (toggle del Drawer mobile).

Logica del menù (vedi `Sidebar.tsx` per il dettaglio):

1. **Sezione "Personale"** (sempre visibile per ogni utente loggato):
   - Mio avatar (`/me/avatar`, icona `Smile`, key `user.myAvatar`).
2. **Sezione "Piattaforma"** (visibile solo se `me.is_platform_admin`):
   - Dashboard (`/admin`).
   - Organizzazioni (`/admin/organizations`).
   - Utenti (`/admin/users`).
   - Permessi globali (`/admin/permissions`).
   - Lingue (`/admin/i18n`).
3. **Sezione "Configurazioni"** (visibile solo se `me.is_platform_admin`):
   - Avatar (`/admin/configurazioni/avatar`).
4. **Sezione "Organizzazione"** (visibile se l'org è risolta tramite
   `useEffectiveOrgId()` e non in area `/admin`):
   - Dashboard (`/orgs/:orgId`).
   - Membri (`PermissionItem` con `P.MEMBER_VIEW`).
   - **Parametri corsi** (`/orgs/:orgId/configurazioni/corsi`,
     icona `GraduationCap`, gated da `P.COURSE_CONFIG_MANAGE`).
   - Template slide (`P.TEMPLATE_SLIDE_MANAGE`).
   - Template PDF (`P.TEMPLATE_PDF_MANAGE`).
5. `OrgSwitcher` in fondo.

Layout MUI:
- `<Drawer variant="temporary">` per mobile (`xs`).
- `<Drawer variant="permanent">` per desktop (`md+`).
- `<AppBar position="fixed">` con bottone burger su mobile + `UserMenu`.

### Componenti interni

- `SectionLabel({ label })`: piccolo header in caps lock per le sezioni.
- `NavItem({ to, icon, label, exact })`: voce di menu via `<NavLink>`.
  Usa `&.active` per evidenziare la pagina corrente.
- `Nav({ items, permissionOrgId? })`: gruppo di voci. La prop
  `permissionOrgId` viene propagata ai `PermissionItem` interni così
  i check `useHasPermission(code, permissionOrgId)` funzionano anche
  fuori da `/orgs/:orgId/...` (es. `/me/avatar`, `/admin/...`).
- `PermissionItem({ code, to, icon, label, permissionOrgId? })`: usa
  `useHasPermission(code, permissionOrgId)` per nascondersi se l'utente
  non ha il permesso. Quando `permissionOrgId` non è passato cade su
  `useEffectiveOrgId()`.

> **Sidebar e `useEffectiveOrgId`**: la `Sidebar` chiama
> `useEffectiveOrgId()` invece di `useParams().orgId`. Risultato: anche
> su rotte non-org (es. `/me/avatar`) l'`OrgSwitcher` continua a mostrare
> l'org corrente e la sezione "Organizzazione" resta navigabile (tranne
> in `/admin/...`, dove è esplicitamente nascosta). L'orgId effettivo
> è anche propagato come `permissionOrgId` ai NavItem per risolvere i
> permessi del menù.

---

## `src/components/layout/OrgSwitcher.tsx`

`<OrgSwitcher />` componente.

`<TextField select>` con le organizzazioni di `me.organizations`. Cambia
url con `navigate(`/orgs/${id}`)` quando l'utente sceglie un'altra org.

Usa l'hook `useEffectiveOrgId()` (vedi
`docs/frontend/08-hooks.md`) per leggere l'org corrente: se l'URL non
ha `:orgId` (es. su `/me/avatar` o `/admin/...`), continua a mostrare
l'ultima org selezionata leggendo `localStorage["a4u.lastOrgId"]`. La
duplicazione di logica localStorage che era qui inline è stata
rimossa.

Se `me.organizations.length === 0`, ritorna `null`.

---

## `src/components/layout/UserMenu.tsx`

Menu utente in `AppBar`.

Elementi:
- Mostra full name (visibile da `sm+`).
- Avatar con iniziali (max 2, uppercase).
- Click apre menu con voci:
  - **Esci** → `logout()` poi `navigate("/login", {replace: true})`.

> La voce "Mio avatar" è stata rimossa da qui: vive ora nella sezione
> "Personale" della `Sidebar` (più visibile e contestuale al menù di
> navigazione).

---

## `src/components/forms/FormAvatarImageInput.tsx`

Variante di `FormImageUpload` dedicata all'avatar utente: l'immagine
finale **deve** essere un quadrato 1024×1024 JPEG. Sostituisce
`FormImageUpload` solo nel caso avatar.

Dipendenza nuova: [`react-image-crop`](https://www.npmjs.com/package/react-image-crop)
(~14KB, listata in `package.json`).

### Props

```ts
{
  label: string;
  helperText?: string;
  value?: File | null;
  existingUrl?: string | null;
  onChange: (file: File | null) => void;
  onRemoveExisting?: () => void;
}
```

### Comportamento

- Preview grande 240×280px sopra il bottone.
- All'upload apre un `Dialog` con `<ReactCrop>` `aspect={1}` e selezione
  iniziale centrata all'80%.
- Su "Applica ritaglio": disegna su `<canvas>` 1024×1024, esporta in
  JPEG `quality=92`, wrappa in `new File([blob], "avatar-image.jpg",
  {type: "image/jpeg"})` e chiama `onChange(file)`.
- Bottone "Ritaglia di nuovo" riapre il Dialog sull'ultima sorgente
  caricata senza richiedere un nuovo upload.
- Bottone "Rimuovi": `onChange(null)` + `onRemoveExisting?.()`.

---

## `src/components/forms/FormImageUpload.tsx`

Upload immagine con preview locale.

### Props

```ts
{
  label: string;
  helperText?: string;
  value?: File | null;          // file selezionato
  existingUrl?: string | null;  // path lato server (per edit)
  onChange: (file: File | null) => void;
  onRemoveExisting?: () => void;  // callback quando l'utente rimuove l'esistente
  accept?: string;              // default: image/png,image/jpeg,image/webp
}
```

### Comportamento

- Se `value` (File) presente: crea `URL.createObjectURL(value)` per la
  preview. Cleanup in `useEffect` con `revokeObjectURL`.
- Altrimenti se `existingUrl` (server) presente: mostra quella.
- Bottone "Carica" apre il picker file (`<input type="file" hidden>`).
- Bottone "Rimuovi" (visibile solo se c'è un `value` o `existingUrl`):
  chiama `onChange(null)` e `onRemoveExisting?.()`.

### Anteprima

Box quadrato 96x96 con `<img>` `object-fit: contain`, fallback "Nessuna
immagine".

---

## `src/components/forms/FormAudioInput.tsx`

Input audio per l'avatar utente: due tab — **Carica** (file picker
classico) e **Registra** (live tramite `MediaRecorder` API). Espone un
file audio uniforme verso il chiamante.

### Props

```ts
{
  label: string;
  helperText?: string;
  value?: File | null;          // file scelto/registrato
  existingUrl?: string | null;  // path lato server (per edit)
  onChange: (file: File | null) => void;
  onRemoveExisting?: () => void;
  accept?: string;              // default: audio/*
}
```

### Comportamento

- **Tab "Carica"**: `<input type="file" accept="audio/*">`. Il file
  selezionato viene passato a `onChange`.
- **Tab "Registra"**: usa `navigator.mediaDevices.getUserMedia({audio:
  true})` e `new MediaRecorder(stream)`. Stop produce un `Blob` che viene
  wrappato in `new File([blob], "recording.<ext>", {type: blob.type})`.
  Il formato dipende dal browser (`audio/webm` su Chrome, `audio/mp4` su
  Safari). Il backend whitelistа già queste varianti.
- Se `MediaRecorder` non è disponibile (browser legacy), la tab
  "Registra" è nascosta e resta solo "Carica".
- Player `<audio controls>` per riascoltare il file selezionato/registrato
  (`URL.createObjectURL` con cleanup) o l'`existingUrl` lato server.
- Bottone "Rimuovi" → `onChange(null)` + `onRemoveExisting?.()`.

## `src/components/forms/DateRangeField.tsx`

Date range field riusabile. Pulsante che mostra il range corrente
(`Da DD/MM/YY a DD/MM/YY`) o un placeholder; click apre `Popover` con
due `<input type="date">` (Da / A) + bottoni "Pulisci" / "Applica".
Zero dipendenze nuove: il calendario nativo del browser si apre al
click sull'input (UX gentile su mobile).

### Props

```ts
interface DateRangeValue {
  from?: string; // "YYYY-MM-DD" (lower bound, inclusivo)
  to?: string;   // "YYYY-MM-DD" (upper bound, inclusivo)
}

interface DateRangeFieldProps {
  label: string;
  value: DateRangeValue;
  onChange: (next: DateRangeValue) => void;
  placeholder?: string;
  className?: string;
}
```

### Comportamento

- Trigger mostra `Da DD/MM/YY — A DD/MM/YY` (con `…` se uno dei due è
  vuoto) oppure il placeholder.
- Se il range ha valori, una "X" inline sul trigger pulisce il campo
  senza aprire il popover.
- Popover usa `draft` locale + commit con "Applica" (così il
  `onChange` non viene chiamato a ogni keystroke).
- Testi sotto `dateRangeField.*` + `common.apply`.

Usato in `CoursesListPage` per i filtri "Creato" e "Aggiornato".

---

## `src/components/feedback/ErrorBoundary.tsx`

Class component (Reactrequires per Error Boundaries).

Stato: `{ error: Error | null }`.

`getDerivedStateFromError(error)` → setta lo stato.

`componentDidCatch(error, info)` → chiama `logger.error("react_boundary",
{...})` per inoltrare a Sentry/backend.

Render: alert MUI con messaggio + bottone "Ricarica la pagina".

---

## `src/components/shared/ConfirmDialog.tsx`

Dialogo modal di conferma.

Props:
- `open: boolean`
- `title: string`
- `message: string`
- `confirmLabel?: string` (default "Conferma")
- `cancelLabel?: string` (default "Annulla")
- `destructive?: boolean` (cambia colore del bottone confirm in `error`)
- `onConfirm()`, `onClose()`.

Usato per delete e azioni potenzialmente irreversibili (transfer creator).

> **Overflow con testi lunghi**: `DialogTitle` e `DialogDescription`
> usano `break-words`; sul `message` è aggiunto anche lo style inline
> `{ overflowWrap: "anywhere", wordBreak: "break-word" }` e il
> `DialogHeader` ha `min-w-0`
> (`src/components/shared/ConfirmDialog.tsx:37-44`). Serve a mandare a
> capo messaggi con filename lunghi senza parole spaziate, evitando che
> il contenuto sfondi il dialog. Lo style inline ha la precedenza su
> eventuali utility con specificità maggiore. Nessuna nuova prop.

---

## `src/components/shared/DataTableColumnToggle.tsx`

Selettore "Colonne" riusabile: dropdown di checkbox per mostrare /
nascondere le colonne di una `DataTable` (TanStack Table). Itera le
colonne con `enableHiding !== false` e usa `column.meta.label` come
etichetta (fallback all'id colonna).

### Props

```ts
{
  columns: ColumnDef<TData, unknown>[];
  value: VisibilityState;
  onChange: (next: VisibilityState) => void;
  label?: string;   // etichetta pulsante; default t("courses.list.columnsButton")
}
```

### Comportamento

- Trigger: `Button` outline con icona `SlidersHorizontal` + label.
- Per ogni colonna nascondibile un `DropdownMenuCheckboxItem` con
  `checked={value[id] !== false}` (colonna visibile di default).
- `onSelect={(e) => e.preventDefault()}` evita la chiusura del menu ad
  ogni toggle (multi-selezione fluida).
- `onCheckedChange` propaga `{ ...value, [id]: next }` al chiamante.

Lo stato di visibilità è tipicamente gestito da
[`useColumnVisibility`](08-hooks.md) (persistenza localStorage). Usato in
`CoursesListPage` accoppiato a `DataTable` (vedi
[06 — Pages](06-pages.md)).

---

## `src/components/avatar/AvatarStatusDot.tsx`

Pallino tri-stato con etichetta per lo stato avatar di un membro, reso
nella colonna "Avatar" della `MembersListPage`. Opzionalmente cliccabile
per aprire l'anteprima (`MemberAvatarDialog`).

### Props

```ts
{
  status: AvatarClipsAggregateStatus | null;
  audio: boolean;
  onClick?: () => void;   // se presente, l'indicatore diventa un bottone
}
```

### Stati

L'helper interno `avatarDotState(status, audio)` deriva uno di 3 stati:

| Stato | Colore | Condizione |
|---|---|---|
| `complete` | emerald | `status === "ready"` **e** `audio` |
| `progress` | amber | avatar avviato ma non completo (clip in corso / parziali / fallite, oppure clip pronte senza voce) |
| `none` | grigio | `status === null` (nessun avatar) |

Label da `members.avatar.{complete|progress|none}`. Quando `onClick` è
passato, rende un `<button>` con `title={t("members.avatar.view")}`;
altrimenti uno `<span>` non interattivo.

---

## `src/pages/org/members/MemberAvatarDialog.tsx`

Anteprima in sola lettura dell'avatar di un membro (immagine, campione
vocale e clip video). Riservata a chi ha `member:avatar:view`; i dati
arrivano da `memberAvatarApi.get(orgId, userId)` →
`GET /orgs/{orgId}/members/{userId}/avatar` (vedi
[02 — API client](02-api-client.md)).

### Props

```ts
{
  orgId: string;
  member: MembershipOut | null;   // null = dialog chiuso
  onClose: () => void;
}
```

### Comportamento

- `open = !!member`; `useQuery` con key
  `["org", orgId, "member-avatar", userId]`, `enabled` solo a dialog
  aperto e `userId` valorizzato.
- Header: titolo `members.avatarDialog.title` (interpolato con il nome) +
  `AvatarClipsBadge` con lo stato aggregato delle clip.
- Stati: skeleton in loading; placeholder `members.avatarDialog.empty`
  se l'avatar è `null`; altrimenti immagine (`size-32`), player
  `<audio controls>` (o `members.avatarDialog.noAudio`) e griglia di
  `AvatarClipCard` (o `members.avatarDialog.noClips`).
- Testi sotto `members.avatarDialog.*`. Aperto da `AvatarStatusDot`
  nella `MembersListPage`.

---

## `src/components/templates/SlideTemplatePreview.tsx`

Preview HTML live di un template slide. Riscritto in modo significativo
rispetto alla versione iniziale: ora replica più fedelmente il layout
finale di una slide didattica e supporta l'inserimento dell'avatar live
in basso a destra.

### Props

```ts
{
  background?: string | null;
  logoLeft?: string | null;
  logoRight?: string | null;
  avatarUrl?: string | null;
  textColor: string;
  primaryColor: string;
  secondaryColor: string;
  fontFamily: string;
  slideSize: "16:9" | "4:3";
}
```

### Layout

- Container con `aspect-ratio: 16/9` o `4/3` e
  `containerType: inline-size`: la tipografia interna è espressa in
  unità `cqw` per scalare in base alla larghezza del **container**
  (non più al viewport), così la preview resta proporzionata anche in
  riquadri stretti come la card sticky di `MyAvatarPage`.
- Sfondo:
  - se `background` URL → `<img>` assoluto `object-cover` a piena
    opacità (la slide non ha opacità configurabile, a differenza del
    template PDF);
  - altrimenti → gradiente bianco → `primaryColor` → `secondaryColor`.
- **Header**: due loghi (sinistro e destro), `object-contain`, ai due
  estremi.
- **Body**:
  - eyebrow grigio "Lezione 01" (`templates.preview.lessonTag`),
  - eyebrow blu "Capitolo introduttivo"
    (`templates.preview.chapter`),
  - titolo grande (`templates.preview.title`),
  - sottotitolo (`templates.preview.subtitle`),
  - 3 bullet (`templates.preview.bullet1..3`) con marker dot in
    gradient primary→secondary.
- **Footer**: autore / cattedra / data
  (`templates.preview.{author,role,date}`) + paginatore "01 / 24".
- **Avatar overlay** (se `avatarUrl`): in basso a destra, **quadrato
  22% del lato corto**, `rounded-lg`, ring sottile, ombra accentuata,
  badge "Live" (`templates.preview.live`) in overlay sfumato in basso.
- Tutti i testi sono i18n sotto `templates.preview.*`.
- Tutto il testo usa `fontFamily` interpolato; fallback
  Roboto/Helvetica/Arial.

Nessuno stato interno: re-renderizza ad ogni cambio props.

> Nella `MyAvatarPage` la preview è renderizzata con palette blu fissa
> (`primaryColor=#2563EB`, `secondaryColor=#0EA5E9`,
> `textColor=#0F172A`) per dare il senso di "anteprima nella slide".

---

## `src/components/templates/PdfTemplatePreview.tsx`

Preview HTML live di un template PDF.

### Props

```ts
{
  background?: string | null;
  logoLeft?: string | null;
  logoRight?: string | null;
  textColor: string;
  primaryColor: string;
  secondaryColor: string;
  fontFamily: string;
  pageSize: "A4" | "Letter";
  headerHeightMm: number;
  footerHeightMm: number;
  marginMm: number;
  backgroundOpacityPct?: number;   // default 15 (era hardcoded)
}
```

### Layout

- Container con `aspect-ratio: PAGE_RATIO[pageSize]`
  (A4: 210/297 ≈ 0.707; Letter: 215.9/279.4 ≈ 0.773).
- Sfondo (se presente): l'opacità non è più hardcoded. Si applica
  `style={{ opacity }}` con `opacity =
  clamp(backgroundOpacityPct ?? 15, 0, 100) / 100`.
- **Header**: top, altezza `headerHeightMm / totalHeightMm * 100%`,
  border-bottom 2px primary, due loghi.
- **Body**: tra `header + margin` e `footer + margin`. Mostra titolo,
  barretta secondary, paragrafi simulati (5 box di colore con alpha).
- **Footer**: bottom, altezza proporzionale, border-top 1px primary
  alpha, "a4u — corso universitario" / "Pagina 1".

Tutti i px sono ricalcolati come percentuale del container, quindi la
preview è responsive (la pagina può essere ridimensionata e mantiene le
proporzioni).

---

## `src/components/CommandPalette.tsx`

`⌘K` / `Ctrl+K` palette globale. **Registry dichiarativo**: invece di
JSX inline con conditional rendering, le voci sono dichiarate in un
array `COMMANDS: CommandEntry[]` con metadata di visibilità. Per
aggiungere un comando basta append a `COMMANDS`, niente modifiche al
body del componente.

Org corrente via `useEffectiveOrgId()` (ultima org visitata in
`localStorage`): le voci org-scoped appaiono anche su rotte non-org.

### Shape di una entry

```ts
type CommandEntry = {
  id: string;
  group: "navigation" | "actions" | "preferences";
  labelKey: string;          // i18n
  shortcutKey?: string;       // hint a destra
  icon: LucideIcon;
  action: (ctx: CommandContext) => void;
  requirePlatformAdmin?: boolean;
  requireOrgId?: boolean;
  requirePermission?: PermissionCode;  // bypassato per platform admin
};
```

Il filtro `visible(cmd)` applica i tre gate; la lingua è dinamica
(generata da `useLanguages()`) e resta come gruppo separato dopo il
registry.

### Comandi esposti

**Navigation — platform admin** (`requirePlatformAdmin`):
Dashboard, Organizzazioni, Utenti, Permessi, Lingue, Configurazioni —
Avatar, Configurazioni — Tassonomie.

**Navigation — org** (`requireOrgId` + `requirePermission`):
Dashboard org, Membri (`P.MEMBER_VIEW`), Corsi (`P.COURSE_VIEW`),
Parametri corsi (`P.COURSE_CONFIG_MANAGE`), Template slide
(`P.TEMPLATE_SLIDE_MANAGE`), Template PDF (`P.TEMPLATE_PDF_MANAGE`).

**Navigation — personale**: Mio avatar (sempre, se loggato).

**Actions — quick-create** (org-scoped, gated per permesso):
- "Nuovo corso" → `/orgs/{id}/corsi/nuovo` (`P.COURSE_CREATE`).
- "Invita membro" → `/orgs/{id}/members?invite=1`
  (`P.MEMBER_INVITE`) — apre il dialog d'invito automaticamente (vedi
  `MembersListPage` in [06 — Pages](06-pages.md)).
- "Nuovo template slide" → `/orgs/{id}/templates/slide/new`
  (`P.TEMPLATE_SLIDE_MANAGE`).
- "Nuovo template PDF" → `/orgs/{id}/templates/pdf/new`
  (`P.TEMPLATE_PDF_MANAGE`).

**Actions — platform admin**: Nuova organizzazione.

**Preferences**: Light / Dark / System (`useTheme().setTheme`).

**Switch language**: 24 lingue UE (`useLanguages()`).

> Permessi org-scoped letti dal `me.organizations[].permissions` per
> l'org corrente; platform admin bypassa il check. Niente chiamate
> server: il filtro è completamente client-side.

---

## `src/components/ui/badge.tsx`

Componente `Badge` di shadcn (`src/components/ui/badge.tsx:8-20`).
Variants supportate: `default`, `secondary`, `destructive`, `outline`,
`muted`, `brand`, `warning`, `success` (nuova).

| Variant | Classi (light → dark) | Uso tipico |
|---|---|---|
| `default` | `bg-primary text-primary-foreground` | badge primario |
| `secondary` | `bg-secondary text-secondary-foreground` | badge secondario |
| `destructive` | `bg-destructive text-destructive-foreground` | errore/azione distruttiva |
| `outline` | solo `text-foreground` (bordo) | neutro, low-emphasis |
| `muted` | `bg-muted text-muted-foreground` | placeholder / valore assente |
| `brand` | `bg-brand/15 text-brand` | accento di marca |
| `warning` | `bg-amber-100 text-amber-900` → `bg-amber-500/20 text-amber-300` | voci che richiedono attenzione |
| `success` | `bg-emerald-100 text-emerald-900` → `bg-emerald-500/20 text-emerald-300` | esito positivo / stato OK |

La variant `warning` (ambra) è usata nelle pagine i18n (header e righe
non tradotte) per segnalare visivamente le voci che richiedono
attenzione.

La variant `success` (verde emerald) è usata nella ricerca paper
scientifici — es. badge "Open Access" e marcatura di paper già importato
(endpoint riassunti in
[Courses 05 — API reference](../courses/05-api-reference.md)).

---

## Componenti del dominio Corsi — schede del `CourseEditorPage`

> I componenti riusabili del dominio Corsi vivono in
> `src/pages/org/courses/components/`. Le schede AI documentate in
> [Courses 06 — Frontend](../courses/06-frontend.md) non sono duplicate
> qui. Le 4 sezioni che seguono coprono le schede aggiunte dopo le
> Fasi 1-5 (Video, Video con avatar, verifica delle competenze).

### `src/pages/org/courses/components/CourseLessonVideoView.tsx`

Vista della scheda **"Video"** del `CourseEditorPage` — generazione del
video MP4 della lezione (Fase 6 §9; vedi
[Courses 12 — Lesson video](../courses/12-lesson-video.md)).

#### Props

```ts
{
  course: CourseOut;
  canGenerate: boolean;   // permesso course:generate
  orgId: string;
}
```

#### Struttura

- **Selettore lingua TTS** (card in cima, sempre visibile): `<Select>`
  limitato a `XTTS_SUPPORTED_LANGUAGES` (16 lingue). Il valore "segui
  corso" è il sentinel `__course_default__` → setta
  `video_language_code` a `null`. La mutation è una patch parziale del
  corso (`coursesApi.update`) che invalida le query corso + batch video.
- **Banner di avviso** (ambra) se la lingua del corso non è supportata
  da XTTS e non c'è override (`courses.video.errors.unsupported_language`),
  e se il campione vocale dell'assegnatario manca
  (`courses.video.errors.voice_sample_missing`).
- **Card aggregata**: titolo, contatore `ready_count/total`, badge
  `failed_count`, jobs in flight, ETA (`useBatchEta`), `<Progress>` con
  la percentuale aggregata calcolata client-side. Bottoni "Genera tutti"
  / "Annulla tutti" condizionati a `canGenerate` ed `eligible_count`.
- **Card per lezione** (raggruppate per modulo): badge di stato, alert
  pre-requisiti (`speech_not_approved`, `slides_not_approved`),
  `staleAlert` se `is_stale`, messaggio di errore se `failed`. Bottoni
  per lezione: Scarica MP4 (anchor `download`), Annulla (se in flight),
  Genera/Rigenera. Mentre il job è attivo mostra la fase
  (`phaseLabel`) + progress per-lezione. Quando `ready` rende un
  `<video controls>` (aspect `99/70`) e una riga di telemetria
  (`tokens`).

Stato live via `useCourseVideoStatus` (polling 2 s). Mutation hook:
`useGenerateLessonVideo`, `useGenerateAllVideos`,
`useCancelLessonVideo`, `useCancelAllVideos` (vedi
[08 — Hooks](08-hooks.md)).

### `src/pages/org/courses/components/CourseLessonAvatarVideoView.tsx`

Vista della scheda **"Video con avatar"** del `CourseEditorPage` —
lip-sync MuseTalk di un avatar parlante sovrapposto al video MP4 della
lezione (Fase 6b §9b; vedi
[Courses 13 — Avatar video](../courses/13-avatar-video.md)).

#### Props

Identiche a `CourseLessonVideoView`: `{ course, canGenerate, orgId }`.

#### Struttura

Mirror della scheda "Video", senza il selettore lingua TTS:

- **Banner pre-requisiti** (ambra): se l'avatar dell'assegnatario non ha
  clip pronte (`courses.avatarVideo.errors.avatar_clips_not_ready`),
  letto da `data.avatar_clips_ready`.
- **Card aggregata** con descrizione, contatore `ready_count/total`,
  badge `failed_count`, jobs in flight + ETA (`useBatchEta`),
  `<Progress>`. Bottoni "Genera tutti" / "Annulla tutti".
- **Card per lezione** (per modulo): badge di stato, alert
  `lesson_video_not_ready` (il video MP4 della lezione deve esistere
  prima), `staleAlert`, errore se `failed`. Bottoni Scarica/Annulla/
  Genera/Rigenera; fase + progress per-lezione mentre attivo; `<video>`
  e riga `tokens` (`duration`, `clips`, `size`) quando `ready`.

Stato live via `useCourseAvatarVideoStatus` (polling 2 s). Mutation
hook: `useGenerateLessonAvatarVideo`, `useGenerateAllAvatarVideos`,
`useCancelLessonAvatarVideo`, `useCancelAllAvatarVideos`.

### `src/pages/org/courses/components/LessonAssessmentView.tsx`

Render **read-only** di una lezione di verifica delle competenze (vedi
[Courses 14 — Assessment lesson](../courses/14-assessment-lesson.md)).
Renderizzato nel corpo espanso delle righe `is_assessment` della scheda
Contenuti.

#### Props

```ts
{
  assessment: LessonAssessmentRaw;
}
```

#### Comportamento

- Sezione **domande a scelta multipla**: lista numerata di domande;
  ogni opzione con prefisso lettera; l'opzione corretta
  (`option_id === correct_option_id`) è evidenziata in emerald con
  un'icona `CheckCircle2` + label `correctAnswer`.
- Sezione **domande aperte**: lista numerata; per ognuna un box
  tratteggiato con la `expected_answer` (traccia di risposta attesa).
- Se non ci sono domande, mostra un placeholder
  (`courses.lessonsContent.assessment.render.empty`).
- Nessuno stato interno. Tutti i testi sotto
  `courses.lessonsContent.assessment.render.*`.

### `src/pages/org/courses/components/CoursePipelineRowChips.tsx`

4 chip compatti per la colonna **Pipeline** di `CoursesListPage`. Per
ogni stadio della pipeline (Contenuti / Slide / Video / Video con
avatar) mostra `[icona] [ratio]` con colore di sfondo graduato:

- `total === 0` → muted (ratio `—`)
- `done === 0` → empty (gray)
- `0 < done < total` → partial (amber)
- `done === total` → done (emerald)

Hover → tooltip via attributo `title` con label localizzata
(`courses.list.progressChip.*`).

```ts
interface CoursePipelineRowChipsProps {
  progress: CourseListLessonsProgress;
  // { total, content_ready, slides_ready, videos_ready, avatar_videos_ready }
}
```

Icone (lucide): `FileText` (contenuti), `Presentation` (slide), `Video`
(video), `Smile` (avatar). Usa il tipo `CourseListLessonsProgress` di
`@/api/courses` (vedi [02 — API client](02-api-client.md)).

### `src/pages/org/courses/components/DuplicateCourseDialog.tsx`

Dialog che apre dalla voce "Duplica in altra lingua" nel menu `⋮`
della lista corsi. Mostra un `Select` di lingue (popolato da
`useLanguages()`, escludendo `course.language_code` corrente), con
bandiera e nome nativo. Al submit chiama
`coursesApi.duplicate(orgId, courseId, target_language_code)`. Su
success: toast + `qc.invalidateQueries(["courses","list",orgId])` +
close. Su error: toast con `extractApiError(err).message`.

Props:
```ts
{ orgId: string; course: CourseListItemOut; onClose: () => void }
```

Vedi [Courses 15](../courses/15-course-duplication.md).

### `src/pages/org/courses/components/CourseDuplicationBadge.tsx`

Badge progress visibile nella riga della lista corsi quando il corso
è target di un job di duplicazione attivo
(`course.duplication_job != null`). Render:
- Badge "Duplicazione in corso" + bandiera della lingua target
  (`flagFor(job.target_language_code)`)
- Progress bar 0-100% su `job.progress`
- Bottone Annulla inline (icona `X`) — chiama
  `coursesApi.cancelDuplication(orgId, job.id)`

Polling automatico ogni 3s a livello pagina (gestito da
`CoursesListPage` via `useQuery refetchInterval` condizionato).

Props:
```ts
{ orgId: string; job: CourseDuplicationJobCompact }
```

### `src/pages/org/courses/components/LessonAssessmentEditDialog.tsx`

Editor modal dedicato della verifica delle competenze. Aperto dalla
scheda Contenuti sulle righe `is_assessment`.

#### Props

```ts
{
  open: boolean;
  isPending: boolean;
  lessonLabel: string;
  initial: LessonAssessmentRaw;
  onClose: () => void;
  onSubmit: (payload: LessonAssessmentUpdateInput) => void;
}
```

#### Comportamento

- Stato locale lazy-init dal prop `initial` (il parent fa
  conditional-render del dialog).
- **Domande a scelta multipla**: per ognuna testo + 2..6 opzioni +
  selezione dell'opzione corretta via radio. L'opzione corretta è
  tracciata **per indice** (`correctIndex`), non per id: in submit le
  opzioni vengono ri-lettarate `A, B, C, …` (costante `OPTION_LETTERS`,
  `MAX_OPTIONS = 6`). Mutators per aggiungere/rimuovere domande e
  opzioni (minimo 2 opzioni per domanda).
- **Domande aperte**: per ognuna testo + risposta attesa
  (`expected_answer`). Mutators add/remove.
- `submit` costruisce un `LessonAssessmentUpdateInput` (testi
  trimmati, `correct_option_id` ricalcolato dalla lettera) e chiama
  `onSubmit`.
- Contenuto in `<ScrollArea>` (max 65vh). Footer con Annulla + Salva
  (label `common.saving` mentre `isPending`).
- Testi sotto `courses.lessonsContent.assessment.editor.*`.

---

## Componenti media condivisi — `src/pages/org/courses/components/media/`

Set di componenti riusabili che alimentano **entrambe** le schede media
del `CourseEditorPage` — "Video" (Fase 6) e "Video con avatar" (Fase 6b)
— evitando di duplicare la presentazione. Le parti specifiche per
variante (badge di stato, avvisi, pulsanti azione, chip token, label di
fase, nome file di download) restano nelle view che le possiedono
(`CourseLessonVideoView` / `CourseLessonAvatarVideoView`) e vengono
iniettate via un oggetto `renderers`. Le pagine mantengono
header / banner / dati propri; qui vive solo il render di lista/griglia
con player in modale.

### Pattern variant `video | avatar` + renderers

Il punto di ingresso è `LessonMediaView<TItem>` che riceve la `variant`
(`"video"` o `"avatar"`, usata per discriminare le chiavi localStorage e
il nome file di download) e due adattatori:

```ts
// media/types.ts
interface MediaStatusItem {
  lesson_id: string;
  status: "empty" | "pending" | "processing" | "ready" | "failed" | "cancelled";
  progress: number;
  progress_phase: string | null;
  video_url: string | null;
  error: string | null;
  is_stale: boolean;
}

interface MediaRenderers<TItem extends MediaStatusItem> {
  statusBadge: (item: TItem) => ReactNode;
  warnings: (item: TItem) => ReactNode;
  actions: (lesson: CourseLessonOut, item: TItem) => ReactNode;
  tokens: (item: TItem) => ReactNode;
  phaseLabel: (item: TItem) => string;
  downloadName: (lesson: CourseLessonOut, item: TItem) => string;
}
```

`MediaStatusItem` è il sottoinsieme comune di `LessonVideoStatusOut` e
`LessonAvatarVideoStatusOut` (entrambi strutturalmente assegnabili).
Testi condivisi sotto `courses.media.*`.

### `LessonMediaView.tsx`

Orchestratore della scheda media.

#### Props

```ts
{
  course: CourseOut;
  variant: "video" | "avatar";
  itemByLessonId: Map<string, TItem>;
  renderers: MediaRenderers<TItem>;
}
```

#### Comportamento

- Usa `useMediaView(course.id, variant)` (vedi
  [08 — Hooks](08-hooks.md)) per `viewMode` (lista/griglia) e i moduli
  collassati, persistiti per corso + variante.
- Calcola `modulesWithItems`: solo i moduli con almeno una lezione che
  ha un item di stato (preservando l'ordine dei moduli del corso); se
  vuoto ritorna `null`.
- Per ogni modulo un `MediaModuleSection` con contatore
  `readyCount/totalCount` (item in `status === "ready"`); il contenuto è
  una lista di `LessonMediaRow` (`viewMode === "list"`) o una griglia di
  `LessonMediaCard` (responsive `sm:2 / xl:3`).
- Etichette sempre via `useCourseLabels()` ("Modulo N" / "Lezione N",
  mai i codici tecnici).
- Stato locale `playing` (lezione + modulo + item correnti); il click su
  ▶ apre il `VideoPlayerModal`.

### `MediaViewToggle.tsx`

Segmented control Lista ↔ Griglia (costruito su `button` nativi, non
esiste una primitive ToggleGroup nel design system).

```ts
{ value: MediaViewMode; onChange: (next: MediaViewMode) => void }
```

Due opzioni con icone `List` / `LayoutGrid` e label
`courses.media.viewList` / `courses.media.viewGrid`; `aria-pressed`
sull'opzione attiva.

### `MediaModuleSection.tsx`

Sezione modulo collassabile. Header cliccabile (etichetta "Modulo N ·
Titolo", `aria-expanded`) con chevron e `Badge` contatore
`courses.media.readyCount` (`{ready, total}`); variante `default` quando
tutte le lezioni sono pronte, `secondary` altrimenti. Il contenuto è
nascosto quando `collapsed`.

```ts
{
  title: string;
  readyCount: number;
  totalCount: number;
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}
```

### `LessonMediaRow.tsx` e `LessonMediaCard.tsx`

Render per-lezione nelle due viste. Props identiche:

```ts
{
  lesson: CourseLessonOut;
  item: TItem;
  lessonLabelText: string;
  renderers: MediaRenderers<TItem>;
  onPlay: () => void;
}
```

- `LessonMediaRow` (stile "Lista"): riga compatta con ▶ + "Lezione N ·
  Titolo" + `renderers.statusBadge`, avvisi, azioni; progress inline
  (`renderers.phaseLabel` + `item.progress`%) quando
  `status ∈ {pending, processing}`.
- `LessonMediaCard` (stile "Griglia"): tile cliccabile `aspect-[99/70]`
  con ▶ su sfondo neutro (niente poster reale), "Lezione N", titolo
  (`line-clamp-2`), badge stato, avvisi, progress, azioni.
- Entrambi: il ▶ è abilitato solo se `status === "ready"` **e**
  `item.video_url`; `aria-label`/`title` da `courses.media.play`. Niente
  player incorporato — il click chiama `onPlay()`.

### `VideoPlayerModal.tsx`

Player video in **modale** (sostituisce i `<video>` incorporati che
allungavano a dismisura le pagine). Si apre al click su riga/card di una
lezione pronta.

```ts
{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  videoUrl: string | null;
  downloadName: string;
  meta?: ReactNode;   // chip token opzionali sotto il player
}
```

`Dialog` `max-w-3xl` con `<video controls autoPlay preload="metadata">`
(`aspect-[99/70]`), riga `meta` opzionale e footer con download dell'MP4
(`<a download target="_blank">`, label `courses.media.modalDownload`).

### `useMediaView.ts`

Hook co-locato che gestisce `viewMode` + moduli collassati con
persistenza localStorage per corso + variante. Documentato in
[08 — Hooks](08-hooks.md).

---

## Widget dashboard — `src/components/dashboard/`

Widget riusabili **CSS-only** (nessuna libreria charts), usati da
`AdminDashboard` e/o `OrgDashboard` (vedi [06 — Pages](06-pages.md)).
`DonutMini` e `ActivityList` esistono nel codice ma **non sono
attualmente usati** dalle dashboard (rimangono disponibili per usi
futuri — il bundle splitting li elimina dal chunk delle pagine se non
referenziati).

### `KpiCard.tsx`

Tile metrica grande: label uppercase tracking-wider, valore `text-3xl
tabular-nums`, sublabel opzionale, icona opzionale in tinta primary
(`bg-primary/10 text-primary`) o muted. Hover: lift discreto
(`-translate-y-0.5`) + shadow.

```ts
props: {
  label: string;
  value: string | number;
  sublabel?: string;
  icon?: LucideIcon;
  tone?: "default" | "muted";  // muted = valore in grigio + icona neutra
}
```

### `StatusBarChart.tsx`

Barra orizzontale stacked segmentata per status + legenda con dot
colorato. Tooltip via attributo `title` (label + count).

```ts
interface StatusBarItem {
  key: string;         // chiave React (es. status raw)
  label: string;       // label localizzata
  count: number;
  color: string;       // classe Tailwind bg-*
}
props: {
  items: StatusBarItem[];
  emptyLabel?: string;
  compact?: boolean;   // barra più sottile, no spacing
}
```

Usato per: pipeline lezioni (5 fasi, uno per ciascuna).

### `CoursePipelineDetail.tsx`

Widget specifico per la "Pipeline corsi" delle dashboard. Mostra
**tutti i 17 stati** di `course.status` raggruppati nelle **8
macro-fasi** (draft, architecture, structure, content, slides, speech,
published, archived). Ogni fase è una card responsiva (1/2/3/4 colonne)
con:

- nome fase + percentuale del totale
- numero totale grande (`text-3xl tabular-nums`)
- progress bar colorata (colore specifico della fase da
  `COURSE_BUCKET_COLORS`)
- per le **5 fasi intermedie** (architecture..speech) breakdown per
  sub-stato `pending / ready / approved` con counts inline

Le card con count 0 sono mostrate con `opacity-60` per dare il senso di
"cosa potrebbe esserci" senza distrarre.

```ts
props: {
  items: StatusCount[];    // raw, 17 statuses
  total: number;
  emptyLabel?: string;
}
```

Empty state: se `total === 0`, render di un messaggio centrato.

### `DonutMini.tsx` _(non in uso)_

Donut compatto via `conic-gradient` (CSS pure, niente SVG/canvas) +
foro centrale con il totale. Legenda laterale con dot, label e count.

```ts
interface DonutItem {
  key: string;
  label: string;
  count: number;
  color: string;  // classe Tailwind bg-* per il dot legenda
  hex: string;    // hex equivalente per il conic-gradient
}
props: {
  items: DonutItem[];
  centerLabel?: string;  // uppercase small sotto il numero
  size?: number;          // px lato esterno (default 140)
}
```

Coppia `bg`/`hex` necessaria perché `conic-gradient` richiede colori
inline mentre la legenda usa classi Tailwind. Mapping centralizzato in
[`lib/statusColors.ts`](#libstatuscolorsts). Resta disponibile per uso
futuro.

### `ActivityList.tsx` _(non in uso)_

Lista compatta di eventi audit log con icona contestuale, attore,
organizzazione (opzionale per viste org-scoped), e timestamp relativo
("5m fa" / "2h fa" / "3g fa" / data localizzata).

```ts
interface ActivityEntry {
  id: string;
  created_at: string;
  action: string;
  actor_user_name: string | null;
  organization_name?: string | null;  // omesso nelle viste org-scoped
  target_type?: string | null;
}
props: {
  items: ActivityEntry[];
  emptyLabel?: string;
  maxItems?: number;
}
```

Icone derivate dal prefisso `action` (`auth.*` → LogIn, `organization.*`
→ Building2, `course.lesson*` → BookOpenCheck, ecc.). Time-ago via
`@/lib/formatTimeAgo.ts` (zero deps). Resta disponibile per uso futuro.

### `lib/statusColors.ts`

Helper di palette unificato:
- `statusColor(status)` → `{ bg: Tailwind class, hex: string }`.
  Lifecycle generico (empty/pending/processing/ready/approved/failed/
  cancelled/partial), course.status (draft/published/archived), login
  (success/failure).
- `courseBucketFor(status)` → `CourseMacroBucket | null`. Mappa i 17
  valori di `course.status` in 8 bucket visivi (draft / architecture /
  structure / content / slides / speech / published / archived).
- `COURSE_BUCKET_COLORS` — palette dedicata ai bucket macro (violet,
  sky, blue, cyan, teal, emerald).
- `sortByLifecycleOrder(items)` — ordine canonico
  approved → ready → processing → pending → failed → ecc.
