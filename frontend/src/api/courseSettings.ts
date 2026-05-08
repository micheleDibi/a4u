import { apiClient } from "./client";
import type {
  OrganizationCourseSettingsInput,
  OrganizationCourseSettingsOut,
  UUID,
} from "./types";

export const courseSettingsApi = {
  async get(orgId: UUID) {
    const res = await apiClient.get<OrganizationCourseSettingsOut>(
      `/orgs/${orgId}/course-settings`
    );
    return res.data;
  },
  async update(orgId: UUID, payload: OrganizationCourseSettingsInput) {
    const res = await apiClient.put<OrganizationCourseSettingsOut>(
      `/orgs/${orgId}/course-settings`,
      payload
    );
    return res.data;
  },
};
