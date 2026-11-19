import { useEffect, useId, useRef, useState } from "react";
import type { DockviewApi } from "dockview-react";
import { WeeklyActivityBars } from "./WeeklyActivityBars";
import { useWeeklyLearningTime } from "./useWeeklyLearningTime";
import "./workspace-metrics.css";

export function WorkspaceMetrics({ isAuthenticated, identity, enabled, dockviewApi }: { isAuthenticated: boolean; identity: { id: number; accountRef: string; authRevision: number } | null; enabled: boolean; dockviewApi?: DockviewApi }) {
  const [open, setOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const { days, status } = useWeeklyLearningTime(identity, enabled, refresh);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  // Keep the global metrics over the top-right group, reserving space in that
  // header only. Group count alone cannot describe a split grid's geometry.
  useEffect(() => {
    const workbench = rootRef.current?.closest<HTMLElement>(".dock-workbench");
    if (!workbench || !dockviewApi) return;
    let header: HTMLElement | undefined;
    let frame = 0;
    const update = () => {
      frame = 0;
      const groups = dockviewApi.groups.filter((group) => group.api.location.type === "grid")
        .map((group) => ({ group, bounds: group.element.getBoundingClientRect() }))
        .filter(({ bounds }) => bounds.width > 0 && bounds.height > 0)
        .sort((left, right) => Math.abs(left.bounds.top - right.bounds.top) > 1
          ? left.bounds.top - right.bounds.top : right.bounds.right - left.bounds.right);
      const target = groups[0];
      const next = target?.group.element.querySelector<HTMLElement>(".dv-tabs-and-actions-container") ?? undefined;
      if (next !== header) {
        header?.removeAttribute("data-workspace-metrics");
        header = next;
        header?.setAttribute("data-workspace-metrics", "");
      }
      const metricsWidth = rootRef.current?.getBoundingClientRect().width ?? 0;
      workbench.style.setProperty("--workspace-metrics-reserved-width", `${Math.ceil(metricsWidth + 8)}px`);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    const layout = dockviewApi.onDidLayoutChange(schedule);
    const resize = new ResizeObserver(schedule);
    resize.observe(workbench);
    if (rootRef.current) resize.observe(rootRef.current);
    schedule();
    return () => {
      layout.dispose();
      resize.disconnect();
      cancelAnimationFrame(frame);
      header?.removeAttribute("data-workspace-metrics");
      workbench.style.removeProperty("--workspace-metrics-reserved-width");
    };
  }, [dockviewApi]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("pointerdown", onPointerDown); document.removeEventListener("keydown", onKeyDown); };
  }, [open]);

  return <div className="workspace-metrics" ref={rootRef} role="group" aria-label="Время обучения">
    <button ref={triggerRef} type="button" className={`workspace-metric${open ? " is-open" : ""}`}
      aria-label="Время обучения на платформе за последние 7 дней — открыть подробности"
      aria-expanded={open} aria-controls={open ? panelId : undefined} onClick={() => { if (!open) setRefresh(value => value + 1); setOpen(!open); }}>
      <WeeklyActivityBars days={days} />
    </button>
    {open && <div className="workspace-metrics-panel" id={panelId} role="region" aria-label="Время обучения за неделю">
      <h3 className="workspace-metrics-panel__heading">Последние 7 дней</h3>
      <WeeklyActivityBars days={days} detailed />
      <div className="workspace-metrics-scale" aria-label="Шкала: 0 часов — пусто, 4 часа — акцентный цвет, 8 часов и больше — белый">
        <div className="workspace-metrics-scale__labels"><span>0</span><span>4 ч · цель</span><span>8 ч</span></div>
      </div>
      <p className="workspace-metrics-panel__note">Учитываются занятия по всем материалам платформы, а не только в этом курсе.</p>
      {(!isAuthenticated || !enabled || !identity || status !== "ready") && <p className="workspace-metrics-panel__empty" role="status">{!isAuthenticated ? "Войдите, чтобы видеть время обучения." : !enabled || !identity ? "Данные о времени обучения недоступны." : status === "loading" ? "Загружаем время обучения…" : status === "partial" ? "Данные могут быть неполными." : "Не удалось обновить время обучения."}</p>}
    </div>}
  </div>;
}
