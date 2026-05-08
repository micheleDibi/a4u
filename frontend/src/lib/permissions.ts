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
  COURSE_CREATE: "course:create",
  COURSE_ASSIGN: "course:assign",
  COURSE_EDIT: "course:edit",
  COURSE_DELETE: "course:delete",
  COURSE_GENERATE: "course:generate",
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
