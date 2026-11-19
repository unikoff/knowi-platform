// Learning time belongs to the user across the entire platform, never to a
// course. Visits and streaks must not substitute for measured study minutes.
export const DAILY_LEARNING_GOAL_MINUTES = 4 * 60;
export const DAILY_LEARNING_COLOR_CAP_MINUTES = 8 * 60;

export function learningTimeScale(minutes: number | null) {
  if (minutes === null || !Number.isFinite(minutes) || minutes < 0) return null;
  return {
    fillPercent: Math.min(minutes / DAILY_LEARNING_GOAL_MINUTES, 1) * 100,
    whitePercent: Math.min(Math.max(minutes - DAILY_LEARNING_GOAL_MINUTES, 0)
      / (DAILY_LEARNING_COLOR_CAP_MINUTES - DAILY_LEARNING_GOAL_MINUTES), 1) * 100,
  };
}
