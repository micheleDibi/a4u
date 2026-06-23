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
