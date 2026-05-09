export type UUID = string;

export interface UserOut {
  id: UUID;
  email: string;
  full_name: string;
  is_platform_admin: boolean;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface MeOrganization {
  organization_id: UUID;
  organization_name: string;
  role_code: string;
  role_name_it: string;
  permissions: string[];
}

export interface MeOut {
  user: UserOut;
  organizations: MeOrganization[];
  is_platform_admin: boolean;
}

export interface OrganizationOut {
  id: UUID;
  name: string;
  email: string;
  phone: string | null;
  website: string | null;
  vat_number: string | null;
  fiscal_code: string | null;
  country: string | null;
  address: string | null;
  city: string | null;
  province: string | null;
  postal_code: string | null;
  logo_path: string | null;
  created_at: string;
  updated_at: string;
}

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
}

export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

export interface MembershipOut {
  id: UUID;
  user_id: UUID;
  user_email: string;
  user_full_name: string;
  organization_id: UUID;
  role_id: UUID;
  role_code: string;
  role_name_it: string;
  joined_at: string;
}

export interface InvitationCreateResponse {
  invitation: {
    id: UUID;
    organization_id: UUID;
    email: string;
    role_code: string;
    expires_at: string;
    accepted_at: string | null;
  };
  token: string;
  accept_url: string;
}

export interface InvitationPreview {
  valid: boolean;
  organization_name?: string;
  email?: string;
  role_name_it?: string;
  user_exists?: boolean;
  expires_at?: string;
}

export interface SlideTemplateOut {
  id: UUID;
  organization_id: UUID;
  name: string;
  background_image_path: string | null;
  logo_left_path: string | null;
  logo_right_path: string | null;
  text_color: string;
  primary_color: string;
  secondary_color: string;
  font_family: string;
  slide_size: "16:9" | "4:3";
  margin_mm: number;
  background_opacity_pct: number;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface PdfTemplateOut {
  id: UUID;
  organization_id: UUID;
  name: string;
  background_image_path: string | null;
  logo_left_path: string | null;
  logo_right_path: string | null;
  text_color: string;
  primary_color: string;
  secondary_color: string;
  font_family: string;
  page_size: "A4" | "Letter";
  header_height_mm: number;
  footer_height_mm: number;
  margin_mm: number;
  background_opacity_pct: number;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export type AvatarClipStatus = "pending" | "processing" | "ready" | "failed";
export type AvatarClipsAggregateStatus =
  | "pending"
  | "processing"
  | "ready"
  | "partial"
  | "failed";

export interface AvatarClipOut {
  id: UUID;
  position: number;
  prompt_text: string;
  status: AvatarClipStatus;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  video_url: string | null;
}

export interface AvatarOut {
  id: UUID;
  user_id: UUID;
  audio_lang: string | null;
  clips_status: AvatarClipsAggregateStatus;
  image_url: string;
  audio_url: string;
  created_at: string;
  updated_at: string;
  clips: AvatarClipOut[];
}

export interface AvatarVoiceScriptOut {
  language_code: string;
  text: string;
  created_at: string;
  updated_at: string;
}

export interface AvatarClipPromptOut {
  id: UUID;
  position: number;
  prompt: string;
  label_it: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PermissionOverrideEntry {
  code: string;
  granted: boolean;
}

export interface OrganizationCourseSettingsOut {
  id: UUID;
  organization_id: UUID;
  modules_per_cfu: number;
  lessons_per_module: number;
  lesson_duration_minutes: number;
  assessment_lesson_enabled: boolean;
  multiple_choice_questions_count: number;
  open_questions_count: number;
  created_at: string;
  updated_at: string;
}

export type OrganizationCourseSettingsInput = Pick<
  OrganizationCourseSettingsOut,
  | "modules_per_cfu"
  | "lessons_per_module"
  | "lesson_duration_minutes"
  | "assessment_lesson_enabled"
  | "multiple_choice_questions_count"
  | "open_questions_count"
>;
