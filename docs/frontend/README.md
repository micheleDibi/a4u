# Frontend overview

React 18 + Vite + TypeScript + Material UI v6 + TanStack Query + React
Hook Form + Zod + axios + notistack. Localizzazione italiana via
`@mui/material/locale` `itIT`.

Avviabile con `npm run start` (alias di `vite`).

## Struttura ad albero

```
frontend/
├── package.json                   # script start/build/lint/type-check
├── vite.config.ts                 # proxy /api e /uploads → :8000
├── tsconfig.json                  # references a app + node
├── tsconfig.app.json              # config per src
├── tsconfig.node.json             # config per vite.config.ts
├── eslint.config.js               # ESLint flat config (react-hooks, refresh, TS)
├── index.html                     # entry HTML, link Google Fonts
├── public/favicon.svg
├── Dockerfile                     # multi-stage node→nginx
├── nginx.conf                     # serve dist + proxy api/uploads
├── .env.example
└── src/
    ├── main.tsx                   # ReactDOM.createRoot
    ├── App.tsx                    # provider tree
    ├── styles.css                 # baseline globale
    ├── theme.ts                   # MUI theme + locale
    ├── vite-env.d.ts              # types per import.meta.env
    ├── api/                       # axios client + 9 moduli endpoint
    ├── auth/                      # AuthContext, ProtectedRoute, PermissionGate
    ├── components/
    │   ├── layout/                # AppLayout, OrgSwitcher, UserMenu
    │   ├── forms/                 # FormImageUpload
    │   ├── feedback/              # ErrorBoundary
    │   ├── shared/                # ConfirmDialog
    │   └── templates/             # SlideTemplatePreview, PdfTemplatePreview
    ├── hooks/                     # (riservato; oggi vuota)
    ├── lib/                       # permissions, errors, format, logger
    ├── pages/                     # auth, admin, org, RootRedirect
    ├── routes/                    # router.tsx
    └── i18n/                      # placeholder per i18next futuro
```

## Documentazione per file

- [01 — Entry: `main.tsx`, `App.tsx`, `theme.ts`, `styles.css`,
  `vite.config.ts`, `vite-env.d.ts`](01-entry.md)
- [02 — `api/`](02-api-client.md)
- [03 — `auth/`](03-auth.md)
- [04 — Routing (`routes/router.tsx`, `pages/RootRedirect.tsx`)](04-routing.md)
- [05 — `components/` (layout, forms, feedback, shared, templates)](05-components.md)
- [06 — `pages/` (auth, admin, org)](06-pages.md)
- [07 — `lib/`](07-lib.md)
- [08 — `hooks/`](08-hooks.md)

## Convenzioni interne

- **Material UI v6** in modalità Grid2 (importato come
  `Grid2 as Grid`).
- **TanStack Query** per data fetching:
  - `staleTime: 30s`, `retry: 1`, `refetchOnWindowFocus: false`.
  - Mutations con `onSuccess` che invalidano le query rilevanti.
- **React Hook Form + Zod** per i form (resolvers `@hookform/resolvers`).
- **notistack**: toast con `enqueueSnackbar` da `useSnackbar`.
- **axios**: interceptor 401 che tenta `/auth/refresh` una volta e ritenta;
  se anche il refresh fallisce, il chiamante riceve l'errore originale.
- **TypeScript strict**: `noUnusedLocals`, `noUnusedParameters`,
  `noFallthroughCasesInSwitch`, `noImplicitReturns`.
- **Path alias**: `@/*` configurato sia in `tsconfig.app.json` sia in
  `vite.config.ts` (`alias: { "@": "/src" }`). Non ancora utilizzato a
  larga scala.

## Flusso dati di una pagina tipica

1. La pagina chiama `useAuth()` per l'utente corrente e
   `useHasPermission(code)` per le permission gate.
2. `useQuery` carica dati dal backend via uno dei moduli `api/*`.
3. Le mutation chiamano `api/*.<verb>` poi `qc.invalidateQueries(...)`.
4. Errori UI: `extractApiError(err)` → `enqueueSnackbar` o `<Alert>`.
