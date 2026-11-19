import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

type PreviewState = { key: string; phase: "open" | "closing" } | null;
type Controller = {
  active: PreviewState;
  request: (key: string, immediate: boolean) => void;
  hold: (key: string) => void;
  leave: (key: string) => void;
  remove: (key: string) => void;
  dismiss: (key: string) => void;
};
const PreviewContext = createContext<Controller | null>(null);

export function CoursePreviewProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<PreviewState>(null);
  const current = useRef<PreviewState>(null);
  const pending = useRef<string | null>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout>>();
  const closeTimer = useRef<ReturnType<typeof setTimeout>>();
  const exitTimer = useRef<ReturnType<typeof setTimeout>>();
  const update = useCallback((next: PreviewState) => { current.current = next; setActive(next); }, []);
  const cancelOpen = useCallback(() => { clearTimeout(openTimer.current); pending.current = null; }, []);
  const cancelClose = useCallback(() => clearTimeout(closeTimer.current), []);
  const cancelExit = useCallback(() => clearTimeout(exitTimer.current), []);
  const dismiss = useCallback((key: string) => {
    if (pending.current === key) cancelOpen();
    if (current.current?.key !== key) return;
    cancelClose(); cancelExit();
    update({ key, phase: "closing" });
    exitTimer.current = setTimeout(() => {
      if (current.current?.key === key && current.current.phase === "closing") update(null);
    }, 160);
  }, [cancelClose, cancelExit, cancelOpen, update]);
  const request = useCallback((key: string, immediate: boolean) => {
    cancelOpen(); cancelClose();
    if (current.current?.key === key) {
      cancelExit(); update({ key, phase: "open" }); return;
    }
    if (current.current) dismiss(current.current.key);
    const activate = () => {
      if (pending.current !== key) return;
      pending.current = null;
      cancelExit(); update({ key, phase: "open" });
    };
    pending.current = key;
    if (immediate) activate();
    else openTimer.current = setTimeout(activate, current.current ? 70 : 150);
  }, [cancelClose, cancelExit, cancelOpen, dismiss, update]);
  const hold = useCallback((key: string) => {
    if (current.current?.key === key && (!pending.current || pending.current === key)) cancelClose();
  }, [cancelClose]);
  const leave = useCallback((key: string) => {
    if (pending.current === key) cancelOpen();
    if (current.current?.key !== key || (pending.current && pending.current !== key)) return;
    cancelClose();
    closeTimer.current = setTimeout(() => {
      if (current.current?.key === key && !pending.current) dismiss(key);
    }, 180);
  }, [cancelClose, cancelOpen, dismiss]);
  const remove = useCallback((key: string) => {
    if (pending.current === key) cancelOpen();
    if (current.current?.key === key) { cancelClose(); cancelExit(); update(null); }
  }, [cancelClose, cancelExit, cancelOpen, update]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        cancelOpen();
        if (current.current) dismiss(current.current.key);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); cancelOpen(); cancelClose(); cancelExit(); };
  }, [cancelClose, cancelExit, cancelOpen, dismiss]);
  const value = useMemo(() => ({ active, request, hold, leave, remove, dismiss }), [active, request, hold, leave, remove, dismiss]);
  return <PreviewContext.Provider value={value}>{children}</PreviewContext.Provider>;
}

export function useCoursePreview() {
  const value = useContext(PreviewContext);
  if (!value) throw new Error("CourseTile requires CoursePreviewProvider");
  return value;
}
