# Frontend 04 — Routing

## `src/routes/router.tsx`

`createBrowserRouter([...])`. Tutte le rotte protette sono dentro
`<ProtectedRoute>`; quelle che richiedono platform admin hanno
`<ProtectedRoute requirePlatformAdmin>`.

### Mappa rotte

```
/login                                                       → LoginPage
/invitations/:token                                          → InvitationAcceptPage
/                                                            → AppLayout (Outlet)
   ├── (index)                                               → RootRedirect
   ├── /admin                                                → AdminDashboard            (admin)
   ├── /admin/organizations                                  → OrganizationsListPage     (admin)
   ├── /admin/organizations/new                              → OrganizationFormPage create (admin)
   ├── /admin/organizations/:id/edit                         → OrganizationFormPage edit (admin)
   ├── /admin/organizations/:id/members                      → OrganizationMembersPage   (admin)
   ├── /admin/users                                          → UsersListPage             (admin)
   ├── /admin/permissions                                    → PermissionsManagerPage    (admin)
   ├── /admin/configurazioni/avatar                          → AvatarConfigPage          (admin)
   ├── /admin/configurazioni/tassonomie                      → CourseTaxonomyPage        (admin)
   ├── /admin/i18n                                           → I18nManagerPage           (admin)
   ├── /admin/i18n/:code                                     → I18nLanguageEditorPage    (admin)
   ├── /me/profile                                           → ProfilePage
   ├── /me/avatar                                            → MyAvatarPage
   ├── /orgs/:orgId                                          → OrgDashboard
   ├── /orgs/:orgId/members                                  → MembersListPage
   ├── /orgs/:orgId/members/:userId/permissions              → MemberPermissionsPage
   ├── /orgs/:orgId/templates/slide                          → SlideTemplatesListPage
   ├── /orgs/:orgId/templates/slide/:id                      → SlideTemplateEditorPage
   ├── /orgs/:orgId/templates/pdf                            → PdfTemplatesListPage
   ├── /orgs/:orgId/templates/pdf/:id                        → PdfTemplateEditorPage
   ├── /orgs/:orgId/configurazioni/corsi                     → CourseSettingsPage
   ├── /orgs/:orgId/corsi                                    → CoursesListPage
   ├── /orgs/:orgId/corsi/nuovo                              → CourseEditorPage create
   └── /orgs/:orgId/corsi/:courseId                          → CourseEditorPage edit
*                                                             → <Navigate to="/">
```

### Note

- `:id` per `templates/slide/:id` accetta sia un UUID sia il valore
  speciale `"new"` (gestito nel componente come `isNew = id === "new"`).
- Le rotte org-scoped non hanno guard di permesso a livello router: le
  pagine internamente usano `<PermissionGate>` o
  `useHasPermission` per render condizionale o redirect.
- Le rotte personali `/me/profile` (`ProfilePage`) e `/me/avatar`
  (`MyAvatarPage`) richiedono solo l'autenticazione (sono dentro
  `<ProtectedRoute>` senza `requirePlatformAdmin` né permesso RBAC):
  operano sull'utente corrente, cross-org. La `ProfilePage` è
  documentata in [06 — Pages](06-pages.md).

---

## `src/pages/RootRedirect.tsx`

Pagina `index` (path `/`). Decide dove redirigere all'avvio.

Logica:

- `me=null` → `null` (lo `ProtectedRoute` ha già rediretto a /login).
- `is_platform_admin` → `<Navigate to="/admin">`.
- `organizations.length === 1` → `<Navigate to="/orgs/{first.id}">`.
- `organizations.length > 1` → idem (in attesa di una lista interattiva).
- `organizations.length === 0` → mostra alert "Nessuna organizzazione, attendi
  un invito".

> Quando ci saranno multiple org, in futuro potremmo aggiungere una pagina
> dedicata di scelta org.
