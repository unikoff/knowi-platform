import type { DockviewApi } from 'dockview-react';
import type { Panel } from './controller';

/** Observe the globally active Dockview panel, including parameter and visibility changes. */
export function observeDockview(api: DockviewApi, onPanel: (panel?: Panel) => void) {
  let current: typeof api.activePanel;
  let subscriptions: { dispose(): void }[] = [];
  const report = () => {
    const panel = api.activePanel;
    if (panel !== current) {
      subscriptions.forEach(item => item.dispose());
      current = panel;
      subscriptions = panel ? [panel.api.onDidParametersChange(report), panel.api.onDidVisibilityChange(report)] : [];
    }
    if (!panel || !panel.api.isVisible) { onPanel(undefined); return; }
    const kind = panel.id === 'theory' || panel.id === 'practice' ? panel.id : undefined;
    const params = panel.params;
    const target = kind && typeof params?.trackId === 'string' && params.trackId && typeof params.lessonId === 'string' && params.lessonId
      ? { trackId: params.trackId, lessonId: params.lessonId } : undefined;
    onPanel({ id: panel.id, kind, target });
  };
  const active = api.onDidActivePanelChange(report);
  const layout = api.onDidLayoutChange(report);
  report();
  return () => { active.dispose(); layout.dispose(); subscriptions.forEach(item => item.dispose()); onPanel(undefined); };
}
