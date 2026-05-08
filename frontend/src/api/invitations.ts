import { apiClient } from "./client";
import type { InvitationCreateResponse, InvitationPreview, UUID } from "./types";

export const invitationsApi = {
  async create(orgId: UUID, email: string, roleCode: string) {
    const res = await apiClient.post<InvitationCreateResponse>(
      `/orgs/${orgId}/invitations`,
      { email, role_code: roleCode }
    );
    return res.data;
  },
  async preview(token: string) {
    const res = await apiClient.get<InvitationPreview>(
      `/invitations/${token}/preview`
    );
    return res.data;
  },
  async accept(
    token: string,
    payload: { full_name?: string; password?: string }
  ) {
    await apiClient.post(`/invitations/${token}/accept`, payload);
  },
};
