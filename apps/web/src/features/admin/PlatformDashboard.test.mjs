import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("scoped dashboard shows unavailable activity while retaining learning and schedule", async () => {
  const vite = await createServer({configFile: false, optimizeDeps: {noDiscovery: true, include: []}, server: {middlewareMode: true}});
  try {
    const { AdminOverviewDashboard } = await vite.ssrLoadModule("/src/features/admin/overview/AdminOverviewDashboard.tsx");
    const dashboard = {total_students: 2, active_students_7d: null, inactive_students_7d: null, daily_active_students: null,
      active_subscriptions_count: 1, lessons_completed_7d: 3, expiring_subscriptions_count: 0, expiring_subscriptions: [],
      slots_30d: {booked: 1, total: 2, free: 1, occupancy_percent: 50}, daily_lessons_completed: []};
    const html = renderToStaticMarkup(React.createElement(AdminOverviewDashboard, {data: dashboard, hideSubscriptions: true, onOpenStudent() {}}));
    assert.match(html, /Данные о посещениях по дням недоступны/);
    assert.match(html, /3 урока/);
    assert.match(html, /50%/);
    assert.doesNotMatch(html, /Истекают · 7 дней/);
  } finally { await vite.close(); }
});
