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

`⌘K` palette globale. Le voci sono raggruppate per sezione e dipendono
dallo stato `me` e dall'org effettiva.

> **Cambio rilevante**: per leggere l'org corrente il palette ora usa
> `useEffectiveOrgId()` (in precedenza usava `useParams().orgId`). Le
> voci della sezione "Organizzazione" appaiono quindi anche su rotte
> non-org (es. `/admin/...`, `/me/avatar`), continuando a puntare
> all'**ultima org visitata** memorizzata in `localStorage`.

Voci attualmente esposte:

- **Per platform admin** (`me.is_platform_admin`):
  Dashboard, Organizzazioni, Utenti, Permessi, **Lingue**
  (`/admin/i18n`), **Configurazioni — Avatar**
  (`/admin/configurazioni/avatar`).
- **Quando l'org effettiva è risolta**:
  dashboard org + Membri + **Parametri corsi**
  (`/orgs/:id/configurazioni/corsi`, icona `GraduationCap`, label
  `nav.courseSettings`) + Template slide + Template PDF. La vecchia
  voce "Avatars" org-scoped è stata rimossa (l'avatar è ora personale,
  non org-scoped).
- **Sezione "Personale"** (per ogni utente loggato):
  **Mio avatar** (icona `Smile`) → `/me/avatar`.
- **Sezione preferenze**: Light / Dark / System (icone `Sun`, `Moon`,
  `Laptop`; quest'ultima sostituisce il duplicato `Sun` precedente).
- **Sezione "Cambia lingua"**: lista delle 24 lingue UE (invariata).

---

## `src/components/ui/badge.tsx`

Componente `Badge` di shadcn. Variants supportate: `default`,
`secondary`, `destructive`, `outline`, `warning` (nuova).

La variant `warning` rende un badge ambra (`bg-amber-100 text-amber-900`
in light, `bg-amber-500/20 text-amber-300` in dark) ed è usata nelle
pagine i18n (header e righe non tradotte) per segnalare visivamente le
voci che richiedono attenzione.
