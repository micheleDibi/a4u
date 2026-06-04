import { createBrowserRouter, Navigate } from "react-router-dom";
import { ProtectedRoute } from "../auth/ProtectedRoute";
import { AppLayout } from "../components/layout/AppLayout";
import LoginPage from "../pages/auth/LoginPage";
import InvitationAcceptPage from "../pages/auth/InvitationAcceptPage";
import AdminDashboard from "../pages/admin/AdminDashboard";
import OrganizationsListPage from "../pages/admin/OrganizationsListPage";
import OrganizationFormPage from "../pages/admin/OrganizationFormPage";
import OrganizationMembersPage from "../pages/admin/OrganizationMembersPage";
import UsersListPage from "../pages/admin/UsersListPage";
import PermissionsManagerPage from "../pages/admin/PermissionsManagerPage";
import I18nManagerPage from "../pages/admin/I18nManagerPage";
import I18nLanguageEditorPage from "../pages/admin/I18nLanguageEditorPage";
import AvatarConfigPage from "../pages/admin/AvatarConfigPage";
import CourseTaxonomyPage from "../pages/admin/CourseTaxonomyPage";
import MyAvatarPage from "../pages/me/MyAvatarPage";
import ProfilePage from "../pages/me/ProfilePage";
import OrgDashboard from "../pages/org/OrgDashboard";
import MembersListPage from "../pages/org/members/MembersListPage";
import MemberPermissionsPage from "../pages/org/members/MemberPermissionsPage";
import SlideTemplatesListPage from "../pages/org/templates/SlideTemplatesListPage";
import SlideTemplateEditorPage from "../pages/org/templates/SlideTemplateEditorPage";
import PdfTemplatesListPage from "../pages/org/templates/PdfTemplatesListPage";
import PdfTemplateEditorPage from "../pages/org/templates/PdfTemplateEditorPage";
import CourseSettingsPage from "../pages/org/courseSettings/CourseSettingsPage";
import CoursesListPage from "../pages/org/courses/CoursesListPage";
import CourseEditorPage from "../pages/org/courses/CourseEditorPage";
import RootRedirect from "../pages/RootRedirect";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/invitations/:token", element: <InvitationAcceptPage /> },
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
            <AdminDashboard />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/organizations",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <OrganizationsListPage />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/organizations/new",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <OrganizationFormPage mode="create" />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/organizations/:id/edit",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <OrganizationFormPage mode="edit" />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/organizations/:id/members",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <OrganizationMembersPage />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/users",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <UsersListPage />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/permissions",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <PermissionsManagerPage />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/i18n",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <I18nManagerPage />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/i18n/:code",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <I18nLanguageEditorPage />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/configurazioni/avatar",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <AvatarConfigPage />
          </ProtectedRoute>
        ),
      },
      {
        path: "admin/configurazioni/tassonomie",
        element: (
          <ProtectedRoute requirePlatformAdmin>
            <CourseTaxonomyPage />
          </ProtectedRoute>
        ),
      },
      { path: "me/profile", element: <ProfilePage /> },
      { path: "me/avatar", element: <MyAvatarPage /> },
      { path: "orgs/:orgId", element: <OrgDashboard /> },
      { path: "orgs/:orgId/members", element: <MembersListPage /> },
      {
        path: "orgs/:orgId/members/:userId/permissions",
        element: <MemberPermissionsPage />,
      },
      { path: "orgs/:orgId/templates/slide", element: <SlideTemplatesListPage /> },
      { path: "orgs/:orgId/templates/slide/:id", element: <SlideTemplateEditorPage /> },
      { path: "orgs/:orgId/templates/pdf", element: <PdfTemplatesListPage /> },
      { path: "orgs/:orgId/templates/pdf/:id", element: <PdfTemplateEditorPage /> },
      {
        path: "orgs/:orgId/configurazioni/corsi",
        element: <CourseSettingsPage />,
      },
      { path: "orgs/:orgId/corsi", element: <CoursesListPage /> },
      {
        path: "orgs/:orgId/corsi/nuovo",
        element: <CourseEditorPage mode="create" />,
      },
      {
        path: "orgs/:orgId/corsi/:courseId",
        element: <CourseEditorPage mode="edit" />,
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
