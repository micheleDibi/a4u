# Frontend 08 — Hooks

La cartella `src/hooks/` ospita i custom hooks promossi (riusati da più
moduli o con logica non triviale). Gli altri sono inline nei moduli che
li espongono.

- `useAuth()` → `auth/AuthContext.tsx` (inline).
- `useHasPermission(code, orgId?)` → `auth/PermissionGate.tsx` (inline).
- `useEffectiveOrgId()` → `src/hooks/useEffectiveOrgId.ts`.

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
