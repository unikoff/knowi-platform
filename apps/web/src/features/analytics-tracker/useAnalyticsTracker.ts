import { useCallback, useEffect, useRef, useState } from 'react';
import type { ToolId } from './tracker';
export type { ToolId } from './tracker';
import { BrowserTrackerController, type Panel } from './controller';

export type TrackerOptions = {
  /** Pass true only from the explicit VITE feature gate. */
  enabled?: boolean;
  toolsEnabled?: boolean;
  identityKey: string | null;
  subjectRef: string | null;
  role: string | null;
  courseId: string;
  catalogVersion: string | null;
  canTrackLesson(target: { trackId: string; lessonId: string }): boolean;
  baseUrl?: string;
};

/** Independent of Workspace/auth: caller owns actual entry and synchronous identity boundaries. */
export function useAnalyticsTrackerIdentity(options: TrackerOptions) {
  const latest = useRef(options); latest.current = options;
  const active = useRef<BrowserTrackerController | null>(null);
  const tool = useRef<ToolId | undefined>(undefined);
  const panel = useRef<Panel | undefined>(undefined);
  const [restart, setRestart] = useState(0);
  const { enabled, identityKey, subjectRef, role, courseId, catalogVersion, baseUrl, toolsEnabled } = options;
  useEffect(() => {
    if (enabled !== true || !identityKey || !subjectRef || role !== 'user') return;
    const controller = new BrowserTrackerController({ identityKey, subjectRef, role, baseUrl, toolsEnabled,
      canTrackLesson: target => latest.current.canTrackLesson(target) });
    active.current = controller;
    controller.start();
    if (latest.current.catalogVersion || latest.current.toolsEnabled) controller.setCourse({ courseId: latest.current.courseId, catalogVersion: latest.current.catalogVersion });
    controller.onActiveTool(tool.current);
    controller.onActivePanel(panel.current);
    return () => { controller.stop(); if (active.current === controller) active.current = null; };
  }, [enabled, identityKey, subjectRef, role, baseUrl, toolsEnabled, restart]);
  useEffect(() => {
    active.current?.setCourse(catalogVersion || toolsEnabled ? { courseId, catalogVersion } : null);
    active.current?.onActivePanel(panel.current);
  }, [courseId, catalogVersion, toolsEnabled, options.canTrackLesson]);
  const onActivePanel = useCallback((next?: Panel) => { panel.current = next; active.current?.onActivePanel(next); }, []);
  const onActiveTool = useCallback((next?: ToolId) => { tool.current = next; active.current?.onActiveTool(next); }, []);
  const stopBeforeLogout = useCallback(() => { active.current?.stop(); active.current = null; }, []);
  const restartAfterFailedLogout = useCallback(() => setRestart(value => value + 1), []);
  return { onActivePanel, onActiveTool, stopBeforeLogout, restartAfterFailedLogout };
}
