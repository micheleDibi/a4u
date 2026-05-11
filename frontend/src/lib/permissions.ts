// Mirror dei codici dal backend (`app/core/permissions.py`).
// Le label localizzate sono in `i18n/locales/<lng>.json` sotto `permissions.<code>` e `roles.<code>`.
export const P = {
  MEMBER_VIEW: "member:view",
  MEMBER_INVITE: "member:invite",
  MEMBER_ASSIGN_ROLE: "member:assign_role",
  MEMBER_REMOVE: "member:remove",
  TEMPLATE_SLIDE_MANAGE: "template:slide:manage",
  TEMPLATE_PDF_MANAGE: "template:pdf:manage",
  PERMISSION_MANAGE: "permission:manage",
  ORG_TRANSFER_CREATOR: "org:transfer_creator",
  ORG_UPDATE: "org:update",
  COURSE_CONFIG_MANAGE: "course_config:manage",
  COURSE_VIEW: "course:view",
  COURSE_VIEW_ALL: "course:view_all",
  COURSE_CREATE: "course:create",
  COURSE_ASSIGN: "course:assign",
  COURSE_EDIT: "course:edit",
  COURSE_DELETE: "course:delete",
  COURSE_GENERATE: "course:generate",
  COURSE_SAVE_DRAFT: "course:save_draft",
} as const;

export type PermissionCode = (typeof P)[keyof typeof P];

export const ALL_PERMISSIONS: PermissionCode[] = Object.values(P);

export const ROLES = {
  CREATOR: "creator",
  ORG_ADMIN: "org_admin",
  MANAGER: "manager",
  MEMBER: "member",
} as const;

export type RoleCode = (typeof ROLES)[keyof typeof ROLES];

export const ROLE_CODES: RoleCode[] = Object.values(ROLES);

// Mirror di `ROLE_DEFAULT_PERMISSIONS` in `backend/app/core/permissions.py`.
// Usato dalla `RolePermissionsBox` per mostrare cosa permette ogni ruolo
// in fase di invito / cambio ruolo. Se aggiungi un permesso nuovo,
// aggiornalo qui di pari passo col BE.
export const ROLE_DEFAULT_PERMISSIONS: Record<RoleCode, readonly PermissionCode[]> = {
  creator: ALL_PERMISSIONS,
  org_admin: [
    P.MEMBER_VIEW, P.MEMBER_INVITE, P.MEMBER_ASSIGN_ROLE, P.MEMBER_REMOVE,
    P.TEMPLATE_SLIDE_MANAGE, P.TEMPLATE_PDF_MANAGE, P.ORG_UPDATE,
    P.COURSE_CONFIG_MANAGE,
    P.COURSE_VIEW, P.COURSE_VIEW_ALL, P.COURSE_CREATE, P.COURSE_ASSIGN,
    P.COURSE_EDIT, P.COURSE_DELETE, P.COURSE_GENERATE, P.COURSE_SAVE_DRAFT,
  ],
  manager: [
    P.MEMBER_VIEW,
    P.COURSE_VIEW, P.COURSE_VIEW_ALL, P.COURSE_CREATE, P.COURSE_ASSIGN,
    P.COURSE_EDIT, P.COURSE_GENERATE, P.COURSE_SAVE_DRAFT,
  ],
  member: [P.COURSE_VIEW],
};

// Raggruppamento dei permessi per area, usato dalla `RolePermissionsBox`.
export const PERMISSION_CATEGORIES: ReadonlyArray<{
  key: string;
  permissions: readonly PermissionCode[];
}> = [
  {
    key: "members",
    permissions: [
      P.MEMBER_VIEW, P.MEMBER_INVITE, P.MEMBER_ASSIGN_ROLE, P.MEMBER_REMOVE,
    ],
  },
  {
    key: "templates",
    permissions: [P.TEMPLATE_SLIDE_MANAGE, P.TEMPLATE_PDF_MANAGE],
  },
  {
    key: "organization",
    permissions: [P.ORG_UPDATE, P.ORG_TRANSFER_CREATOR, P.PERMISSION_MANAGE],
  },
  {
    key: "coursesView",
    permissions: [P.COURSE_VIEW, P.COURSE_VIEW_ALL],
  },
  {
    key: "coursesManage",
    permissions: [
      P.COURSE_CONFIG_MANAGE, P.COURSE_CREATE, P.COURSE_SAVE_DRAFT,
      P.COURSE_ASSIGN, P.COURSE_EDIT, P.COURSE_DELETE, P.COURSE_GENERATE,
    ],
  },
];
