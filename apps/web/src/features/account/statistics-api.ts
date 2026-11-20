import { apiRequest } from "../../shared/api/client";

export interface LearnerStatisticsReport {
  schema_version: 1;
  source: "analytics";
  as_of: string;
  period: { days: 7 | 30; start: string; end: string; timezone: "Europe/Moscow" };
  scope: { course_id: string | null; activity: "platform" };
  coverage: { visits: "legacy_global" | "partial"; time: "unavailable" | "partial" | "measured"; progress: "core" };
  summary: { active_days_total: number | null; current_streak: number | null; active_days_period: number | null; completed_lessons_period: number; active_seconds: number | null; theory_seconds: number | null; practice_seconds: number | null };
  days: Array<{ date: string; visited: boolean | null; completed_lessons: number; active_seconds: number | null; theory_seconds: number | null; practice_seconds: number | null }>;
}

export function getLearnerStatistics(days: 7 | 30, courseId: string) {
  const query = new URLSearchParams({ days: String(days), course_id: courseId });
  return apiRequest<LearnerStatisticsReport>(`/user/analytics/statistics?${query}`);
}
