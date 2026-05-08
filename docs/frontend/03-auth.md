# Frontend 03 — `auth/`

Gestione autenticazione client-side. Tutto basato sul cookie HttpOnly del
backend; il client non legge mai il token, lo scambia solo via cookie.

---

## `src/auth/AuthContext.tsx`

### `interface AuthContextValue`

```ts
{
  me: MeOut | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}
```

### `<AuthProvider>` componente

State interno:
- `me: MeOut | null`.
- `loading: boolean` (true al primo mount, false dopo il primo `refresh`).

Funzioni:

- `refresh()`: chiama `authApi.me()`, setta `me` o `null` su errore.
- `useEffect` al mount esegue `refresh()` poi `setLoading(false)`.
- `login(email, password)`: chiama `authApi.login`, poi `refresh()`.
- `logout()`: chiama `authApi.logout()`, poi `setMe(null)` (sempre, anche
  se logout backend fallisce).

`useMemo` per stabilizzare `value` (evita re-render dei consumer quando
non cambia nulla).

### `useAuth()`

Hook. Throw se chiamato fuori da `<AuthProvider>`.

---

## `src/auth/ProtectedRoute.tsx`

Componente wrapper che richiede autenticazione e (opzionalmente)
`platform_admin`.

### Props

- `children: ReactNode`
- `requirePlatformAdmin?: boolean` (default `false`).

### Comportamento

1. `loading=true` → spinner full-screen (`<CircularProgress>` centrato).
2. `me=null` → `<Navigate to="/login" replace state={{ from: pathname }}>`.
3. `requirePlatformAdmin && !me.is_platform_admin` → `<Navigate to="/">`.
4. Altrimenti renderizza `children`.

---

## `src/auth/PermissionGate.tsx`

Permessi a livello UI: nasconde elementi se l'utente non ha il permesso
richiesto.

### `<PermissionGate>` componente

Props:

- `code: string | string[]`: il/i codici permessi richiesti (AND).
- `children: ReactNode`.
- `fallback?: ReactNode` (default `null`).
- `orgId?: string` (default: `useParams().orgId`).

Logica:

1. `me` da `useAuth()`.
2. Se `me=null` → `fallback`.
3. Se `is_platform_admin` → `children`.
4. Cerca `org` in `me.organizations` per `orgId`. Se assente → `fallback`.
5. Verifica che TUTTI i codici siano in `org.permissions`. Se sì →
   `children`, altrimenti `fallback`.

### `useHasPermission(code, orgId?)` hook

Ritorna `boolean`. Stessa logica di `<PermissionGate>` ma come hook.

> Le `permissions` in `me.organizations[i]` sono **già risolte** dal
> backend (default ⊕ override org ⊕ override membership). Non occorre
> ricalcolare.
