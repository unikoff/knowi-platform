import { apiRequest } from "../../shared/api/client";

export interface LearnerStatisticsReport {
  schema_version: 1;
  source: "analytics";
  as_of: string;
  period: { days: 7 | 30; start: string; end: string; timezone: "Europe/Moscow" };
  scope: { course_id: string | null; activity: "platform" };
  coverage: { visits: "legacy_global" | "partial"; time: "unavailable"; progress: "core" };
  summary: { active_days_total: number | null; current_streak: number | null; active_days_period: number; completed_lessons_period: number; active_seconds: null; theory_seconds: null; practice_seconds: null };
  days: Array<{ date: string; visited: boolean; completed_lessons: number; active_seconds: null; theory_seconds: null; practice_seconds: null }>;
}

export function getLearnerStatistics(days: 7 | 30, courseId: string) {
  const query = new URLSearchParams({ days: String(days), course_id: courseId });
  return apiRequest<LearnerStatisticsReport>(`/user/analytics/statistics?${query}`);
}
