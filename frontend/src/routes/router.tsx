import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { ProtectedRoute } from "../auth/ProtectedRoute";
import { AppLayout } from "../components/layout/AppLayout";
import LoginPage from "../pages/auth/LoginPage";
import RootRedirect from "../pages/RootRedirect";
import { RouteFallback } from "./RouteFallback";

/** Flag in `sessionStorage`: la scheda si è già ricaricata per un chunk mancante. */
const CHUNK_RELOAD_FLAG = "a4u:chunk-reload";

/**
 * Errori di CARICAMENTO di un modulo dinamico, non del suo codice: fetch
 * fallita in Chrome/Edge («Failed to fetch dynamically imported module»),
 * Firefox («error loading dynamically imported module»), Safari («Importing a
 * module script failed») e il CSS del chunk non scaricabile (`Unable to
 * preload CSS`, dal preload di Vite). Un'eccezione lanciata dal codice della
 * pagina ha un altro messaggio e non passa di qui.
 */
const CHUNK_LOAD_ERROR_RE =
  /dynamically imported module|Importing a module script failed|Unable to preload CSS/i;

function readReloadFlag(): boolean {
  try {
    return sessionStorage.getItem(CHUNK_RELOAD_FLAG) !== null;
  } catch {
    // storage non disponibile: meglio nessun reload che un ciclo di reload
    return true;
  }
}

/**
 * Scrive (o toglie) il flag e dice se ora lo stato in `sessionStorage` è
 * quello voluto, riletto dopo la scrittura. Serve a chi ricarica: se
 * `setItem` fallisce (storage pieno, vecchio Safari in navigazione privata)
 * mentre `getItem` funziona, il flag non c'è e senza questo controllo ogni
 * reload ne innescherebbe un altro su un chunk che manca davvero.
 */
function writeReloadFlag(on: boolean): boolean {
  try {
    if (on) sessionStorage.setItem(CHUNK_RELOAD_FLAG, "1");
    else sessionStorage.removeItem(CHUNK_RELOAD_FLAG);
    return (sessionStorage.getItem(CHUNK_RELOAD_FLAG) !== null) === on;
  } catch {
    // storage non disponibile
    return false;
  }
}

/**
 * Pagina caricata in modo pigro (`React.lazy`), in un chunk separato.
 *
 * Dopo un deploy l'immagine nuova non contiene più i chunk del build
 * precedente: una scheda rimasta aperta che naviga verso una pagina non
 * ancora caricata chiederebbe un file che non esiste più. In quel caso, e
 * solo per un errore di caricamento del modulo, la pagina si ricarica UNA
 * volta: `index.html` non è in cache (nginx), quindi arriva quello nuovo con i
 * nomi giusti. Il flag in `sessionStorage` evita il ciclo se il chunk manca
 * davvero (al secondo errore si rilancia e decide l'error element del
 * router) ed è tolto a ogni import riuscito, così un deploy successivo nella
 * stessa scheda può di nuovo ricaricare.
 */
function lazyPage<P extends object>(load: () => Promise<{ default: ComponentType<P> }>) {
  return lazy(async () => {
    try {
      const mod = await load();
      writeReloadFlag(false);
      return mod;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // Si ricarica solo se il flag è stato scritto davvero: è lui a fermare
      // il ciclo al giro successivo.
      if (CHUNK_LOAD_ERROR_RE.test(message) && !readReloadFlag() && writeReloadFlag(true)) {
        window.location.reload();
        // resta in sospeso: il fallback rimane a schermo fino al reload
        return new Promise<never>(() => undefined);
      }
      throw err;
    }
  });
}

/** Avvolge una pagina pigra nel `Suspense` col segnaposto di `RouteFallback`. */
function suspended(page: ReactNode, fullScreen = false) {
  return <Suspense fallback={<RouteFallback fullScreen={fullScreen} />}>{page}</Suspense>;
}

// Eager solo ciò che serve al primo paint: layout, guardia, login e il
// redirect dell'indice (minuscolo; pigro aggiungerebbe un round-trip prima di
// ogni redirect). Tutto il resto è un chunk per pagina.
const InvitationAcceptPage = lazyPage(() => import("../pages/auth/InvitationAcceptPage"));
const AdminDashboard = lazyPage(() => import("../pages/admin/AdminDashboard"));
const OrganizationsListPage = lazyPage(() => import("../pages/admin/OrganizationsListPage"));
const OrganizationFormPage = lazyPage(() => import("../pages/admin/OrganizationFormPage"));
const OrganizationMembersPage = lazyPage(() => import("../pages/admin/OrganizationMembersPage"));
const UsersListPage = lazyPage(() => import("../pages/admin/UsersListPage"));
const PermissionsManagerPage = lazyPage(() => import("../pages/admin/PermissionsManagerPage"));
const I18nManagerPage = lazyPage(() => import("../pages/admin/I18nManagerPage"));
const I18nLanguageEditorPage = lazyPage(() => import("../pages/admin/I18nLanguageEditorPage"));
const AvatarConfigPage = lazyPage(() => import("../pages/admin/AvatarConfigPage"));
const CourseTaxonomyPage = lazyPage(() => import("../pages/admin/CourseTaxonomyPage"));
const MyAvatarPage = lazyPage(() => import("../pages/me/MyAvatarPage"));
const ProfilePage = lazyPage(() => import("../pages/me/ProfilePage"));
const OrgDashboard = lazyPage(() => import("../pages/org/OrgDashboard"));
const MembersListPage = lazyPage(() => import("../pages/org/members/MembersListPage"));
const MemberPermissionsPage = lazyPage(() => import("../pages/org/members/MemberPermissionsPage"));
const SlideTemplatesListPage = lazyPage(
  () => import("../pages/org/templates/SlideTemplatesListPage"),
);
const SlideTemplateEditorPage = lazyPage(
  () => import("../pages/org/templates/SlideTemplateEditorPage"),
);
const PdfTemplatesListPage = lazyPage(() => import("../pages/org/templates/PdfTemplatesListPage"));
const PdfTemplateEditorPage = lazyPage(
  () => import("../pages/org/templates/PdfTemplateEditorPage"),
);
const CourseSettingsPage = lazyPage(() => import("../pages/org/courseSettings/CourseSettingsPage"));
const CoursesListPage = lazyPage(() => import("../pages/org/courses/CoursesListPage"));
const CourseEditorPage = lazyPage(() => import("../pages/org/courses/CourseEditorPage"));

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/invitations/:token", element: suspended(<InvitationAcceptPage />, true) },
  {
    path: "/",
    element: (
      <ProtectedRoute>
        <AppLayout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <RootRedirect /> },
      {
        path: "admin",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<AdminDashboard />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/organizations",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<OrganizationsListPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/organizations/new",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<OrganizationFormPage mode="create" />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/organizations/:id/edit",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<OrganizationFormPage mode="edit" />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/organizations/:id/members",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<OrganizationMembersPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/users",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<UsersListPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/permissions",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<PermissionsManagerPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/i18n",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<I18nManagerPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/i18n/:code",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<I18nLanguageEditorPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/configurazioni/avatar",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<AvatarConfigPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/configurazioni/tassonomie",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            {suspended(<CourseTaxonomyPage />)}
          </ProtectedRoute>
        ),
      },
      { path: "me/profile", element: suspended(<ProfilePage />) },
      { path: "me/avatar", element: suspended(<MyAvatarPage />) },
      { path: "orgs/:orgId", element: suspended(<OrgDashboard />) },
      { path: "orgs/:orgId/members", element: suspended(<MembersListPage />) },
      {
        path: "orgs/:orgId/members/:userId/permissions",
        element: suspended(<MemberPermissionsPage />),
      },
      { path: "orgs/:orgId/templates/slide", element: suspended(<SlideTemplatesListPage />) },
      { path: "orgs/:orgId/templates/slide/:id", element: suspended(<SlideTemplateEditorPage />) },
      { path: "orgs/:orgId/templates/pdf", element: suspended(<PdfTemplatesListPage />) },
      { path: "orgs/:orgId/templates/pdf/:id", element: suspended(<PdfTemplateEditorPage />) },
      {
        path: "orgs/:orgId/configurazioni/corsi",
        element: suspended(<CourseSettingsPage />),
      },
      { path: "orgs/:orgId/corsi", element: suspended(<CoursesListPage />) },
      {
        path: "orgs/:orgId/corsi/nuovo",
        element: suspended(<CourseEditorPage mode="create" />),
      },
      {
        path: "orgs/:orgId/corsi/:courseId",
        element: suspended(<CourseEditorPage mode="edit" />),
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
