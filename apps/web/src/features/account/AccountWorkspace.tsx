import { useMemo } from "react";
import { AccountPanel } from "./AccountPanel";
import { canShowPersonalMeasurements } from "./course-statistics-analytics";
import { useAccountSchedule } from "./useAccountSchedule";
import type { AccountStatisticsData } from "./AccountStatistics";
import type { Track } from "../learning/types";
import type { CourseEntry } from "../learning/useLearningCourse";
import type { CalendarMonth, UserProfile } from "../../shared/api/contracts";
import { useBookingMode } from "../../shared/api/useBookingMode";
import type { ToolId } from "../analytics-tracker/tracker";

export function AccountWorkspace({ courseId, user, tracks, courseEntries, tracksLoaded, calendarMonth, onMonthChange, onLogout, onUserChange, onToolChange }: {
  courseId: string; user: UserProfile; tracks: Track[]; courseEntries: Record<string, CourseEntry>; tracksLoaded: boolean; calendarMonth: CalendarMonth;
  onMonthChange: (month: CalendarMonth) => void; onLogout: () => void; onUserChange: (user: UserProfile) => void;
  onToolChange?: (tool: ToolId) => void;
}) {
  const measured = canShowPersonalMeasurements(import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED, import.meta.env.VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED, user.role);
  const booking = useBookingMode(user);
  const schedule = useAccountSchedule(user.id, courseId, calendarMonth, typeof user.auth_revision === "number" && user.role ? user.account_ref : undefined, `${user.role}:${user.auth_revision}:${courseId}`, measured, booking.mode);
  const analyticsCatalog = useMemo(() => tracks.map(track => ({
    id: track.id,
    title: track.title,
    lessons: (courseEntries[track.id]?.lessons ?? []).map(lesson => ({id: lesson.id, title: lesson.title})),
  })), [tracks, courseEntries]);
  const statisticsData = useMemo<AccountStatisticsData>(() => {
    const courses = tracks.map((track) => {
      const lessons = courseEntries[track.id]?.lessons ?? [];
      const hasStatisticsScope = lessons.length > 0 && lessons.every(lesson => typeof lesson.counts_toward_statistics === "boolean");
      const countedLessons = lessons.filter(lesson => lesson.counts_toward_statistics);
      return {
        id: track.id,
        title: track.title,
        completed: hasStatisticsScope ? countedLessons.filter(lesson => lesson.completed).length : track.lessons_completed,
        total: hasStatisticsScope ? countedLessons.length : track.lessons_countable,
        blocks: (track.blocks ?? []).map((block) => {
          const lessonIds = new Set(block.lesson_ids ?? []);
          const blockLessons = lessons.filter(lesson => lessonIds.has(lesson.id));
          const countedBlockLessons = blockLessons.filter(lesson => lesson.counts_toward_statistics);
          const scopeKnown = hasStatisticsScope && block.lesson_ids !== undefined;
          return {
            ...block,
            ...(scopeKnown && {
              completed: countedBlockLessons.filter(lesson => lesson.completed).length,
              total: countedBlockLessons.length,
            }),
            lessons: blockLessons.map(lesson => ({
              id: lesson.id,
              title: lesson.title,
              completed: Boolean(lesson.completed),
              completedOn: lesson.completed_on ?? null,
              historicalCredit: lesson.historical_completion_credit === true,
            })),
          };
        }),
      };
    });
    const completedLessons = courses.reduce((total, course) => total + course.completed, 0);
    const totalLessons = courses.reduce((total, course) => total + course.total, 0);
    return {
      tracksLoaded,
      completedLessons,
      totalLessons,
      overallProgress: totalLessons ? Math.round(completedLessons / totalLessons * 100) : 0,
      activeDays: schedule.activitySummary?.total_active_days ?? null,
      currentStreak: measured ? (schedule.currentMeasurement?.streak_is_lower_bound || (schedule.currentMeasurement?.coverage !== "complete" && schedule.currentMeasurement?.current_streak === 0) ? null : schedule.currentMeasurement?.current_streak ?? null) : schedule.activitySummary?.current_streak ?? null,
      weeklyLessonsCompleted: schedule.activitySummary?.weekly_lessons_completed ?? null,
      courses,
    };
  }, [tracks, courseEntries, tracksLoaded, schedule.activitySummary, schedule.currentMeasurement, measured]);

  return <AccountPanel user={user} calendarMonth={calendarMonth} onMonthChange={onMonthChange}
    activityDays={schedule.activityDays} measurementStatus={schedule.measurementStatus}
    bookingSlots={schedule.bookingSlots} bookingQuota={schedule.bookingQuota} bookingLoading={booking.loading || schedule.bookingLoading}
    bookingError={booking.loading ? "" : booking.mode === null ? "Не удалось загрузить расписание. Откройте вкладку снова." : schedule.bookingError}
    courseId={courseId} ownerName={schedule.ownerName}
    onBookSlot={schedule.bookSlot} onCancelSlot={schedule.cancelSlotBooking} onLogout={onLogout} onUserChange={onUserChange}
    statisticsData={statisticsData} analyticsCatalog={analyticsCatalog} onToolChange={onToolChange} />;
}
