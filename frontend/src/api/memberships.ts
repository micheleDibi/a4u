import { apiClient } from "./client";
import type { MembershipOut, PermissionOverrideEntry, UUID } from "./types";

export const membershipsApi = {
  async list(orgId: UUID) {
    const res = await apiClient.get<MembershipOut[]>(`/orgs/${orgId}/members`);
    return res.data;
  },
  async changeRole(orgId: UUID, userId: UUID, roleCode: string) {
    const res = await apiClient.put<MembershipOut>(
      `/orgs/${orgId}/members/${userId}/role`,
      { role_code: roleCode }
    );
    return res.data;
  },
  async remove(orgId: UUID, userId: UUID) {
    await apiClient.delete(`/orgs/${orgId}/members/${userId}`);
  },
  async getMemberPermissions(orgId: UUID, userId: UUID) {
    const res = await apiClient.get<{
      membership_id: string;
      overrides: PermissionOverrideEntry[];
    }>(`/orgs/${orgId}/members/${userId}/permissions`);
    return res.data;
  },
  async setMemberPermissions(
    orgId: UUID,
    userId: UUID,
    overrides: PermissionOverrideEntry[]
  ) {
    await apiClient.put(`/orgs/${orgId}/members/${userId}/permissions`, { overrides });
  },
  async getRolePermissions(orgId: UUID, roleCode: string) {
    const res = await apiClient.get<{
      role_code: string;
      defaults: string[];
      overrides: PermissionOverrideEntry[];
    }>(`/orgs/${orgId}/permissions/role/${roleCode}`);
    return res.data;
  },
  async setRolePermissions(
    orgId: UUID,
    roleCode: string,
    overrides: PermissionOverrideEntry[]
  ) {
    await apiClient.put(`/orgs/${orgId}/permissions/role/${roleCode}`, { overrides });
  },
  async transferCreator(orgId: UUID, targetUserId: UUID) {
    await apiClient.post(`/orgs/${orgId}/transfer-creator`, {
      target_user_id: targetUserId,
    });
  },
};
