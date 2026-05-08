# Frontend 01 — Entry, theme, styles, vite

## `frontend/index.html`

Entry HTML.
- `<html lang="it">`.
- Preconnect/preload Google Fonts (Roboto 300/400/500/700).
- `<title>a4u — Corsi universitari</title>`.
- `<meta theme-color>` (per address bar mobile).
- `<div id="root">` + `<script type="module" src="/src/main.tsx">`.

## `frontend/public/favicon.svg`

Logo testuale `a4u` su quadrato blu primario.

## `frontend/vite.config.ts`

`defineConfig`:
- Plugin `react()`.
- `server.port = 5173`, `strictPort: true`.
- `server.proxy`:
  - `/api` → `http://localhost:8000`.
  - `/uploads` → `http://localhost:8000`.
- `build.sourcemap = true`, `target = es2022`.
- `resolve.alias` `@ → /src`.

## `frontend/tsconfig.json` / `tsconfig.app.json` / `tsconfig.node.json`

Composito. `tsconfig.app.json` è strict, target `ES2022`, `jsx="react-jsx"`,
`moduleResolution=bundler`, `baseUrl=.`, `paths."@/*": ["src/*"]`,
`types: ["vite/client"]`.

## `frontend/eslint.config.js`

Flat config:
- Estende `js.configs.recommended` + `typescript-eslint.configs.recommended`.
- Plugin `react-hooks`, `react-refresh`.
- Regola: `react-refresh/only-export-components` warning con
  `allowConstantExport: true`.
- `@typescript-eslint/no-unused-vars` warn con `argsIgnorePattern=^_`.

## `frontend/src/main.tsx`

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

Punto di ingresso. `StrictMode` attivo (utile per detectare side-effect in
sviluppo).

## `frontend/src/styles.css`

Reset minimo:
- `html,body,#root` altezza 100%, no margin/padding.
- Font system come fallback.
- Background grigio chiaro `#f5f5f7`.
- `*` `box-sizing: border-box`.

## `frontend/src/vite-env.d.ts`

Estende `ImportMetaEnv` con le var `VITE_API_BASE_URL`,
`VITE_UPLOADS_BASE_URL`, `VITE_SENTRY_DSN`. Tipa `import.meta.env`.

## `frontend/src/theme.ts`

`createTheme(...)` con:

- `palette.primary.main = "#1976d2"`, `secondary.main = "#9c27b0"`.
- `background.default = "#f5f5f7"`, `paper = "#ffffff"`.
- `typography.fontFamily = '"Roboto", "Helvetica", "Arial", sans-serif'`.
- `shape.borderRadius = 8`.
- `components`:
  - `MuiButton`: `defaultProps: { variant: "contained", disableElevation: true }`.
  - `MuiTextField`: `defaultProps: { size: "small", fullWidth: true }`.
  - `MuiPaper`: `defaultProps: { elevation: 0 }`.
- Locali: `coreItIT` (`@mui/material/locale`) e `gridItIT`
  (`@mui/x-data-grid/locales`).

## `frontend/src/App.tsx`

Componente radice. Compone i provider:

```tsx
<ThemeProvider theme={theme}>
  <CssBaseline />
  <SnackbarProvider maxSnack={3} anchorOrigin={{ vertical: "bottom", horizontal: "right" }}>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </SnackbarProvider>
</ThemeProvider>
```

`queryClient`:
- `staleTime: 30_000`.
- `retry: 1`.
- `refetchOnWindowFocus: false`.
- `mutations.retry: 0`.

## `frontend/.env.example`

```
VITE_API_BASE_URL=/api/v1
VITE_UPLOADS_BASE_URL=/uploads
VITE_SENTRY_DSN=
```

## `frontend/Dockerfile`

Multi-stage:
- `deps`: `node:20-alpine`, `npm ci || npm install`.
- `build`: legge build-args `VITE_*`, `npm run build`.
- `runtime`: `nginx:1.27-alpine`, copia `dist/` e `nginx.conf`. Healthcheck
  via `wget -qO- http://localhost/`.

## `frontend/nginx.conf`

Server `listen 80`:
- Security headers (X-Content-Type-Options, X-Frame-Options,
  Referrer-Policy, Permissions-Policy).
- `/api/` → `proxy_pass http://backend:8000`.
- `/uploads/` → idem.
- `try_files $uri /index.html` (SPA fallback).
- gzip on per JS/CSS/JSON/SVG/wasm.
