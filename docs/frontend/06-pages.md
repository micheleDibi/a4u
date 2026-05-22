# Frontend 06 — `pages/`

Tutte le pagine routabili dell'applicazione. Per ognuna: percorso URL,
permessi richiesti, dati caricati, comportamenti chiave.

> **Pagine del dominio Corsi** (`CoursesListPage`, `CourseEditorPage`,
> `DocumentSummaryDialog`, `ModuleEditDialog`, `LessonEditDialog`,
> `GenerateArchitectureDialog`, `CourseArchitectureView`,
> `CourseDocumentUploader`) sono documentate in
> [Courses 06 — Frontend](../courses/06-frontend.md), non duplicate qui.
>
> Il `CourseEditorPage` è un wizard a tab con `TAB_ORDER`:
> `base`, `didactic`, `documents`, `architecture`, `lessons-structure`,
> `lesson-content`, `lesson-slides`, `lesson-speech`, **`lesson-video`**
> ("Video"), **`lesson-avatar-video`** ("Video con avatar"). Le ultime
> due schede sono visibili solo dopo il lock del setup didattico e
> abilitate (`TabsTrigger` non `disabled`) quando almeno una lezione ha
> sia `speech_status === "approved"` sia `slides_status === "approved"`.
> Renderizzano rispettivamente `CourseLessonVideoView` (Fase 6) e
> `CourseLessonAvatarVideoView` (Fase 6b), descritte in
> [05 — Components](05-components.md) e nelle doc Courses 12/13. Il tab
> attivo è persistito in `localStorage["course-editor-tab:{courseId}"]`.

---

## Auth

### `src/pages/auth/LoginPage.tsx`

**Path**: `/login`. Pubblica.

State: `email`, `password`, `error`, `submitting`.

`onSubmit`:
1. `await login(email.trim(), password)` (da `useAuth`).
2. Successo: `navigate("/", { replace: true })`.
3. Errore: `extractApiError(err)` → mostrato come `<Alert>`.

UI: `<Container maxWidth="sm">` con `<Paper>` centrato, form con email,
password, eventuale alert errore, bottone "Accedi".

> Se `me` esiste già al mount → reindirizza a `/`.

### `src/pages/auth/InvitationAcceptPage.tsx`

**Path**: `/invitations/:token`. Pubblica.

Flusso:

1. Mount → `invitationsApi.preview(token)` per pre-popolare la pagina.
2. Mostra info invito (organizzazione, email, ruolo).
3. Se `user_exists=false` → form con `full_name` + `password` (validati
   server-side: ≥10 char, una maiuscola, un numero).
4. Se `user_exists=true` → solo bottone "Accetta invito".
5. Submit → `invitationsApi.accept(token, payload)` →
   `auth.refresh()` per caricare il `me` aggiornato → `navigate("/")`.

Stati di errore:
- preview con `valid:false` → alert "Invito non valido/scaduto".
- accept fallito → alert con `extractApiError`.

---

## Admin (richiede `is_platform_admin`)

### `src/pages/admin/AdminDashboard.tsx`

**Path**: `/admin`.

Tre `<Card>`:
- Organizzazioni → `/admin/organizations`.
- Utenti → `/admin/users`.
- Permessi globali → `/admin/permissions`.

### `src/pages/admin/OrganizationsListPage.tsx`

**Path**: `/admin/organizations`.

State: `page`, `pageSize`, `q`, `toDelete`.

Hooks:
- `useQuery(["organizations", page, pageSize, q], () => organizationsApi.list(...))`.
- `useMutation((id) => organizationsApi.remove(id))` con invalidate
  `["organizations"]` e snackbar di successo/errore.

UI:
- `<TextField>` di ricerca per nome.
- `<DataGrid>` con colonne: avatar logo, nome, email, città, stato,
  azioni (modifica / membri / elimina).
- `<ConfirmDialog>` per la conferma delete.

### `src/pages/admin/OrganizationFormPage.tsx`

**Path**: `/admin/organizations/new` (mode `create`),
`/admin/organizations/:id/edit` (mode `edit`).

Props: `mode: "create" | "edit"`.

State: `logoFile: File|null`, `removeLogo: boolean`.

React Hook Form + Zod schema:
- `name`: required, max 255.
- `email`: required email.
- altri campi optional max length.

Submit:
- `editing` → `organizationsApi.update(id, payload, {logo: logoFile,
  remove_logo: removeLogo})`.
- altrimenti → `organizationsApi.create(payload, logoFile)`.
- successo → invalidate query e `navigate("/admin/organizations")`.

UI:
- `<FormImageUpload>` per il logo.
- 11 campi anagrafici in `<Grid2>`.

### `src/pages/admin/OrganizationMembersPage.tsx`

**Path**: `/admin/organizations/:id/members`.

Permette al platform admin di iscrivere direttamente un utente come
membro.

State: `selectedUser`, `role`, `userSearch`.

Hooks:
- `useQuery(["organization", id])` per il nome.
- `useQuery(["org", id, "members"])` per la lista membri.
- `useQuery(["users", "search", userSearch])` con `placeholderData: prev`
  per autocompletamento utenti.
- `useMutation` per `organizationsApi.enrollUser(id, user.id, role)`.

UI:
- `<Autocomplete>` utenti + `<TextField select>` ruolo (4 opzioni).
- `<DataGrid>` membri esistenti.

### `src/pages/admin/UsersListPage.tsx`

**Path**: `/admin/users`.

State: `q`, `page`, `pageSize`, `createOpen`.

Hooks:
- `useQuery` per la lista utenti paginata.
- `useMutation` per:
  - `usersApi.create(...)`.
  - `usersApi.update(id, { is_active })` toggle.
  - `usersApi.update(id, { is_platform_admin })` toggle.

UI:
- DataGrid con colonne: nome, email, switch attivo, switch admin.
- Bottone "Nuovo utente" apre `<CreateUserDialog>` (componente locale al
  file).

### `src/pages/admin/PermissionsManagerPage.tsx`

**Path**: `/admin/permissions`.

Modifica i default globali per ogni ruolo.

State: `role: RoleCode`, `selected: Set<string>`.

Hooks:
- `useQuery(["role-defaults", role], () => permissionsApi.getRoleDefaults(role))`.
- `useEffect` per inizializzare `selected` quando arrivano i default.
- `useMutation` per `permissionsApi.setRoleDefaults(role, [...])`.

UI:
- Selettore ruolo.
- Grid responsive di `<FormControlLabel>` con checkbox per ogni
  permesso. Per ogni codice mostra:
  - **label** = `t("permissions.<code>")` (etichetta IT breve);
  - **descrizione** sotto la label = `t("permissionDescriptions.<code>")`
    (1-2 frasi che spiegano vincoli e portata);
  - il **codice raw** (es. `member:view`) come tooltip sull'hover della
    label (non più scritto in piccolo sotto).
- Bottone "Salva permessi".

### `src/pages/admin/I18nManagerPage.tsx`

**Path**: `/admin/i18n`. Lista delle lingue supportate con CRUD e
azione "Completa con AI".

UI wrappata in `<TooltipProvider delayDuration={150}>`.

Colonne della tabella:

- **`name`**: nome della lingua. Se `untranslated_count > 0` (e non è
  la lingua di default), accanto al nome viene mostrata l'icona
  `AlertTriangle` gialla dentro un `<Tooltip>` con testo
  `t("i18n.untranslatedTooltip")` (interpolato con il count).
- **`actions`** (dropdown): nuova `DropdownMenuItem` "Completa con AI"
  (icona `Sparkles`), abilitata solo se `canAutoTranslate = !is_default
  && untranslated_count > 0`. Apre un `ConfirmDialog` con messaggio
  *"Vuoi tradurre N voci..."*; al confirm chiama
  `autoTranslateMut.mutate(code)`.

Hooks:
- `useQuery(["admin-languages"], i18nApi.list)`.
- `useMutation(autoTranslate)`:
  - on success: toast `t("i18n.autoTranslateSuccess")` interpolato
    con `{translated, requested}`. Se `errors.length > 0`, anche
    `toast.warning(errors[0])`.
  - Invalida `["admin-languages"]`,
    `["admin-i18n-translations"]`, `["i18n.translations"]`.

### `src/pages/admin/I18nLanguageEditorPage.tsx`

**Path**: `/admin/i18n/:code`. Editor delle traduzioni di una singola
lingua.

Header:

- Titolo con codice lingua. Se non è la lingua di default e
  `untranslated_count > 0`, accanto al codice viene reso un badge
  `variant="warning"` con `AlertTriangle` + count.

Action bar:

- Bottone outline **"Completa con AI"** (icona `Sparkles`) accanto al
  bottone "Salva". Visibile solo se `canAutoTranslate = !isItalian &&
  liveUntranslatedCount > 0`. Apre un `ConfirmDialog` come nella
  manager page.

Search bar:

- Nuova checkbox **"Mostra solo non tradotte (N)"** (filtro
  client-side; conteggio `liveUntranslatedCount` aggiornato in tempo
  reale durante l'editing).

Tabella delle chiavi:

- Helper locale `isRowUntranslated(k)`: `true` se il target è vuoto o
  mancante. **Non** considera il caso `current === reference` come
  non tradotto (allineato al backend: i valori identici sono spesso
  traduzioni legittime — brand name, prestiti, stringhe tecniche).
- Per ogni riga `isRowUntranslated`:
  - sfondo amber tenue + bordo sinistro `border-amber-400`;
  - icona `AlertTriangle` gialla accanto alla key;
  - badge `variant="warning"` con label "Da tradurre" sotto la key;
  - bordo dell'`Input` ambra.

`liveUntranslatedCount` viene ricalcolato a ogni edit per aggiornare
sia la checkbox di filtro che la visibilità del bottone "Completa con
AI" e l'header.

---

## Org (membership richiesta o platform admin)

### `src/pages/org/OrgDashboard.tsx`

**Path**: `/orgs/:orgId`.

Hooks:
- `useAuth()` per `me`.
- `useHasPermission(P.MEMBER_VIEW | TEMPLATE_SLIDE_MANAGE | TEMPLATE_PDF_MANAGE)`.

Cards mostrate solo se permesso:
- Membri.
- Template slide.
- Template PDF.

### `src/pages/org/members/MembersListPage.tsx`

**Path**: `/orgs/:orgId/members`. Permesso: `member:view`.

State: `inviteOpen`, `inviteEmail`, `inviteRole`, `inviteToken`,
`toRemove`, `toTransfer`.

Hooks:
- `useQuery` membri.
- `useMutation` per:
  - `invitationsApi.create(...)`.
  - `membershipsApi.changeRole(...)`.
  - `membershipsApi.remove(...)`.
  - `membershipsApi.transferCreator(...)`.

DataGrid:
- Colonne: nome, email, ruolo, azioni.
- Colonna ruolo: se `canAssignRole` e non è creator né "self", `<TextField select>`
  con i 3 ruoli non-creator. Altrimenti testo.
- Azioni:
  - "Permessi membro" → `/orgs/:orgId/members/:userId/permissions`
    (visibile se `canPermissions` e non creator).
  - "Trasferisci creator" → `setToTransfer(p.row)` (visibile se
    `canTransfer`, non creator, non self).
  - "Rimuovi" → `setToRemove(p.row)` (visibile se `canRemove`, non
    creator, non self).

Dialogo invito:
- Stato 1: form (email, ruolo) → submit chiama API.
- Stato 2: dopo create, mostra `accept_url` in TextField readonly per
  copia/share.

`<ConfirmDialog>` per remove e transfer.

### `src/pages/org/members/MemberPermissionsPage.tsx`

**Path**: `/orgs/:orgId/members/:userId/permissions`.
Permesso: `permission:manage`.

State: `overrides: Record<string, "default"|"grant"|"revoke">`.

Hooks:
- `useQuery` per recuperare i membri (per nome del target).
- `useQuery` per `getMemberPermissions(orgId, userId)`.
- `useEffect` inizializza overrides dai dati server.
- `useMutation` per `setMemberPermissions(...)`.

UI: per ogni codice permesso una riga con tre radio (default / concedi
/ revoca). Per ogni codice viene mostrato:
- **label** = `t("permissions.<code>")`;
- **descrizione** sotto = `t("permissionDescriptions.<code>")`;
- **codice raw** come tooltip sull'hover della label.

La payload submit include solo i codici diversi da `default`.

### `src/pages/org/templates/SlideTemplatesListPage.tsx`

**Path**: `/orgs/:orgId/templates/slide`. Permesso: `template:slide:manage`.

Lista come griglia di card; ogni card mostra `SlideTemplatePreview` in
miniatura + nome + dimensione + font + bottone elimina.

Bottone "Nuovo template" → `/orgs/:orgId/templates/slide/new`.

### `src/pages/org/templates/SlideTemplateEditorPage.tsx`

**Path**: `/orgs/:orgId/templates/slide/:id` (con `id="new"` per create).

Layout split-view su `lg+`: form a sinistra (Grid2 6/12), preview live a
destra (Grid2 6/12, `position: sticky` su desktop).

State per file: `bgFile`, `logoLFile`, `logoRFile` + flag `removeBg`,
`removeLogoL`, `removeLogoR`.

React Hook Form + Zod:
- `name` required (max 120),
- 3 colori validati `#[0-9a-fA-F]{6}`,
- `font_family` da lista predefinita (Roboto, Inter, Open Sans, Lato,
  Montserrat, Poppins, Source Sans Pro),
- `slide_size` enum.

`form.watch()` per leggere i valori live; `URL.createObjectURL` per
preview file appena selezionati (cleanup in useEffect).

Submit:
- `isNew` → `slideTemplatesApi.create(...)`.
- altrimenti → `slideTemplatesApi.update(orgId, id, fields, {bg, ll, lr,
  remove_*})`.

Helper interni:
- `<ColorRow>`: combina `<input type="color">` con un `<TextField>` testuale
  per il valore esadecimale.

### `src/pages/org/templates/PdfTemplatesListPage.tsx`

**Path**: `/orgs/:orgId/templates/pdf`. Analoga alla slide list ma
`PdfTemplatePreview`. La card miniatura passa anche
`backgroundOpacityPct` letto dal template, così la filigrana riflette il
valore configurato dall'utente.

### `src/pages/org/templates/PdfTemplateEditorPage.tsx`

**Path**: `/orgs/:orgId/templates/pdf/:id` (con `id="new"`).

Stessa struttura dello slide editor; in più i campi:
- `page_size` enum `A4|Letter`.
- 3 slider `header_height_mm`, `footer_height_mm`, `margin_mm`.
- nuovo `<SliderRow>` 0..100 con label "Opacità sfondo (%)" sotto
  l'upload sfondo, mappato su `background_opacity_pct`.

Helper interno:
- `<SliderRow>`: label dinamica con valore + `<Slider>` MUI con marks
  0/halfmax/max. Ora accetta una prop `unit` (default `mm`, qui `%`)
  per la formattazione del valore visualizzato.

### `src/pages/org/courseSettings/CourseSettingsPage.tsx`

**Path**: `/orgs/:orgId/configurazioni/corsi`. Permesso:
`course_config:manage`.

Configura i parametri di generazione dei corsi a livello di
organizzazione (1:1 con `Organization`).

Hooks:
- `useQuery(["org", orgId, "course-settings"], () =>
  courseSettingsApi.get(orgId))`.
- `useMutation` per `courseSettingsApi.update(orgId, payload)` con
  invalidate della query di lettura e toast di successo.
- React Hook Form + Zod schema (`modules_per_cfu ≥ 1`,
  `lessons_per_module ≥ 1`, `lesson_duration_minutes ≥ 1`,
  `multiple_choice_questions_count ≥ 0`, `open_questions_count ≥ 0`).

UI a 2 colonne (`lg`): a sinistra il form con 3 `SectionCard`, a
destra una `SummaryCard` sticky.

- **SectionCard "Struttura del corso"** (icona `Layers`): stepper
  `modules_per_cfu`, stepper `lessons_per_module`.
- **SectionCard "Durata delle lezioni"** (icona `Clock`): stepper
  `lesson_duration_minutes` (in minuti).
- **SectionCard "Verifica di apprendimento finale"** (icona
  `ClipboardCheck`, accent quando enabled): switch
  `assessment_lesson_enabled` con `HelpCircle` + `Tooltip` accanto alla
  label che chiarisce: *"Se attivo, l'ultima lezione di ogni modulo è
  una verifica di apprendimento. La piattaforma genera una serie di
  domande per verificare le competenze acquisite. La piattaforma
  genera solo i contenuti, non gestisce la fruizione da parte degli
  studenti."* (a4u è una piattaforma di **generazione** contenuti,
  non di delivery agli studenti). Quando il toggle è
  `false`, gli stepper `multiple_choice_questions_count` e
  `open_questions_count` sono **nascosti** (i valori restano
  persistiti).

Componenti locali:

- **`Stepper`**: bottone `-` + `Input` numerico + bottone `+`
  vincolati ai limiti del campo.
- **`SummaryCard`**: mostra i totali calcolati (numero totale di
  lezioni e durata complessiva del corso) basati sui valori live.

Save bar sticky in fondo (visibile solo quando
`form.formState.isDirty`).

---

## Avatar utente

### `src/pages/me/MyAvatarPage.tsx`

**Path**: `/me/avatar`. Solo autenticazione (no permesso RBAC). L'utente
gestisce il **proprio** avatar (1:1, cross-org).

Pagina riscritta con un layout a due colonne, save bar sticky e preview
live nella slide.

Hooks:
- `useQuery(["me", "avatar"], myAvatarApi.get)`.
- `useQuery(["me", "avatar", "voice-script", lang], () =>
  myAvatarApi.getVoiceScript(lang))` con fallback automatico lato server.
- `useMutation` per `myAvatarApi.upsert(...)`,
  `myAvatarApi.regenerateClips()`, `myAvatarApi.remove()`.
- Polling automatico se `clips_status ∈ {pending, processing}`
  (`refetchInterval: 5000`) per riflettere lo stato live mentre il worker
  processa le clip.

UI:

- **PageHeader** semplice con titolo + descrizione.
- **Status banner** se `hasAvatar`: card orizzontale con thumbnail 56px,
  titolo "Avatar attivo" + badge stato aggregato, bandiera lingua +
  counter "X/Y clip pronte · Z errori", bottoni "Ascolta audio",
  "Rigenera", icona cestino.
- **Layout 2 colonne** `lg:grid-cols-[minmax(0,1fr)_minmax(440px,580px)]`:
  - **Sinistra**: Card form senza padding interno con `Tabs` shadcn stile
    stepper (numero in cerchio + icona + label + sottolineatura attiva).
    - **Tab "Immagine"** (icona `ImageIcon`):
      `<FormAvatarImageInput />` (crop 1:1 obbligatorio, output JPEG
      1024×1024).
    - **Tab "Audio"** (icona `Mic`, disabled finché non c'è
      un'immagine nuova o esistente):
      Select lingua con bandiere + `<ScriptToReadCard />` (mostra il
      testo standardizzato risolto da `getVoiceScript(lang)`) +
      `<FormAudioInput />`.
  - **Destra**: card sticky `lg:sticky lg:top-6` con titolo
    "Anteprima nella slide" + `<SlideTemplatePreview />` con palette
    blu (`primaryColor=#2563EB`, `secondaryColor=#0EA5E9`,
    `textColor=#0F172A`) e `avatarUrl` collegato all'immagine corrente.
- **Save bar sticky bottom**: bg semi-trasparente + `backdrop-blur` +
  border-top.
  - A sinistra: hint contestuale (warn ambra "modificare l'immagine
    rigenererà i clip" / "Modifiche non salvate" / vuoto).
  - A destra: "Indietro" (solo nel tab audio) + bottone "Salva"
    `size="lg"`.
- **Sezione "Clip generate"** sotto: h2 + counter "{N} clip in totale"
  + badge aggregato a destra. Grid `sm:2` / `xl:3`. Numero clip
  overlay nera in alto-sinistra del player. Le `ready` mostrano
  `<video controls loop>` con `src=video_path`; le `failed` mostrano
  l'`error_message`.
- **Sezione "Avatar parlante — parametri avanzati"** (componente locale
  `MusetalkParamsSection`, visibile solo se l'avatar esiste): card con
  3 campi numerici (componente locale `MusetalkField`) per i parametri
  MuseTalk per-avatar usati dal lip-sync del «Video con Avatar» delle
  lezioni:
  - `musetalk_extra_margin` (range 0..200),
  - `musetalk_left_cheek_width` (range 0..400),
  - `musetalk_right_cheek_width` (range 0..400).
  Lo stato locale si ri-sincronizza quando l'avatar è ricaricato dal
  server. Bottone "Salva" abilitato solo se i valori sono `dirty`;
  salva via `myAvatarApi.updateMusetalkParams(...)` e invalida la query
  `["my-avatar"]`. Testi sotto `myAvatar.musetalk.*`.
- **Skeleton loading state** durante la query iniziale.
- `<ConfirmDialog>` per la cancellazione (icona cestino dello status
  banner).

> Il vecchio campo libero `audio_text` non c'è più: il testo che
> l'utente legge è quello servito dall'admin via
> `avatar_voice_scripts` (read-only nell'editor).

---

## Configurazioni admin

### `src/pages/admin/AvatarConfigPage.tsx`

**Path**: `/admin/configurazioni/avatar`. Richiede `is_platform_admin`.

Gestisce due aree distinte:

1. i prompt EN passati a MiniMax per generare le clip avatar;
2. gli script di lettura per la registrazione audio, uno per lingua.

Pagina riscritta con due tab e azioni contestuali al tab.

Hooks:
- `useQuery(["avatar-config", "prompts"], avatarConfigApi.list)`.
- `useQuery(["avatar-config", "voice-scripts"],
  avatarConfigApi.listVoiceScripts)`.
- `useMutation` per
  `create`/`update`/`remove`/`reorder` dei prompt e per
  `upsertVoiceScript`/`deleteVoiceScript` degli script.

UI:

- **PageHeader** senza azioni (l'azione vive nel tab attivo).
- **Tabs**: "Prompt video" (icona `Sparkles`) e "Lingue trascrizione"
  (icona `Languages`).

#### Tab "Prompt video"

- Header: counter a sinistra ("N prompt configurati") + bottone
  "Aggiungi prompt" a destra (era nel `PageHeader`, ora è inline al
  tab).
- Form di creazione inline + lista di `<PromptRow>` con frecce ↑/↓ per
  riordinamento, switch `is_active`, edit on-blur dei campi, bottone
  elimina.
- `<ConfirmDialog>` per la cancellazione.

#### Tab "Lingue trascrizione"

- Lista di `<VoiceScriptRow>` con **read-only di default** + bottoni:
  - **Modifica** (outline, icona `Pencil`) → entra in edit mode con
    `autoFocus` sulla `<textarea>`;
  - **Elimina** (icona `Trash2`).
- In **edit mode**:
  - bottoni **Annulla** (icona `X`) e **Salva** (`disabled` se non
    dirty o testo vuoto). Niente più auto-save su blur.
- Card "Aggiungi testo per una lingua" con `<Select>` tra le lingue che
  non hanno ancora uno script configurato.
