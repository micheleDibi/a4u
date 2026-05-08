import { apiClient } from "./client";
import type { PermissionOverrideEntry } from "./types";

export const permissionsApi = {
  async catalog() {
    const res = await apiClient.get<{
      permissions: string[];
      roles: { code: string; name_it: string }[];
    }>("/admin/permissions/permissions");
    return res.data;
  },
  async getRoleDefaults(roleCode: string) {
    const res = await apiClient.get<{ role_code: string; permissions: string[] }>(
      "/admin/permissions/role-defaults",
      { params: { role_code: roleCode } }
    );
    return res.data;
  },
  async setRoleDefaults(roleCode: string, permissions: string[]) {
    await apiClient.put("/admin/permissions/role-defaults", {
      role_code: roleCode,
      permissions,
    });
  },
};

export type { PermissionOverrideEntry };
