import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { CourseStatus } from "@/api/courses";

const VARIANTS: Record<
  CourseStatus,
  "default" | "secondary" | "destructive" | "outline" | "muted" | "brand" | "warning"
> = {
  draft: "secondary",
  architecture_pending: "warning",
  architecture_ready: "brand",
  architecture_approved: "brand",
  lessons_structure_pending: "warning",
  lessons_structure_ready: "brand",
  lessons_structure_approved: "brand",
  content_pending: "warning",
  content_ready: "brand",
  content_approved: "brand",
  slides_pending: "warning",
  slides_ready: "brand",
  speech_pending: "warning",
  speech_ready: "brand",
  published: "default",
  archived: "outline",
};

export function CourseStatusBadge({ status }: { status: CourseStatus }) {
  const { t } = useTranslation();
  return (
    <Badge variant={VARIANTS[status]}>
      {t(`courses.statuses.${status}`)}
    </Badge>
  );
}
