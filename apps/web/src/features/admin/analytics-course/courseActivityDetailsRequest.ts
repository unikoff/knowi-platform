import { startCourseActivityRequest, type CourseActivityFailure, type CourseActivityFilters } from "./courseActivityRequest";
import type { ActivityDetailsReport } from "../../analytics-reporting/types";

export type CourseDetailsFilters = CourseActivityFilters & { lesson_id?: string; offset: number; limit: number };
export type CourseDetailsFetch = (filters: CourseDetailsFilters, expectedActorRef: string, signal: AbortSignal) => Promise<ActivityDetailsReport>;

// Reuse the bounded, identity-aware request lifecycle; the shared client validates
// the wire DTO, target mapping and pagination before this adapter receives it.
export function startCourseActivityDetailsRequest({ filters, fetchDetails, expectedActorRef, onDetails, onFailure, timeoutMs }: {
  filters: CourseDetailsFilters; fetchDetails: CourseDetailsFetch; expectedActorRef: string;
  onDetails: (value: ActivityDetailsReport) => void; onFailure: (failure: CourseActivityFailure) => void; timeoutMs?: number;
}) {
  let details: ActivityDetailsReport | null = null;
  return startCourseActivityRequest({ filters, expectedActorRef, timeoutMs,
    fetchReport: async (_filters, actor, signal) => {
      if (!Number.isSafeInteger(filters.offset) || filters.offset < 0 || filters.offset > 20_000 || !Number.isSafeInteger(filters.limit) || filters.limit < 1 || filters.limit > 100) throw new Error("Invalid pagination");
      const result = await fetchDetails(filters, actor, signal);
      if (result.lesson_id !== (filters.lesson_id ?? null) || result.rows.length > filters.limit || (result.next_offset !== null && (result.next_offset !== filters.offset + filters.limit || result.next_offset > 20_000))) throw new Error("Invalid detail scope");
      details = result;
      return result.report;
    },
    onReport: () => { if (details) onDetails(details); else onFailure("error"); },
    onFailure,
  });
}
