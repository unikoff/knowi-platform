import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { List, BookOpen, SquarePen, LayoutDashboard, ShieldCheck, User, Terminal, Ellipsis, Pin, PinOff, type LucideIcon } from "lucide-react";
import { normalizedBase } from "../../shared/course-paths";
import type { WorkspaceView } from "../../features/learning";
import { isRequiredSidebarPin, isSidebarToolPinned, readSidebarPins, sidebarPinsKey, toggleSidebarPin, writeSidebarPins } from "./sidebarPins";
import "./sidebar-tools.css";

const DEFAULT_PINS = ["lessons", "interpreter", "theory", "practice", "platform-admin", "admin", "account"];
type SidebarTool = { id: string; label: string; openLabel?: string; Icon: LucideIcon; active?: boolean; action?: () => void; href?: string };

export function WorkspaceActivityBar({
  ownerKey, compact, isExplorerOpen, isAdmin, isPlatformAdmin, isAdminOpen, isAccountOpen, isInterpreterOpen, activeView,
  onOpenLessons, onLessonsPointerEnter, onLessonsPointerLeave, onOpenTheory, onOpenPractice,
  onOpenAdmin, onOpenAccount, onOpenInterpreter,
}: {
  ownerKey: string;
  compact: boolean;
  isExplorerOpen: boolean;
  isAdmin: boolean;
  isPlatformAdmin: boolean;
  isAdminOpen: boolean;
  isAccountOpen: boolean;
  isInterpreterOpen: boolean;
  activeView?: WorkspaceView;
  onOpenLessons: () => void;
  onLessonsPointerEnter: (pointerType: string) => void;
  onLessonsPointerLeave: (pointerType: string) => void;
  onOpenTheory: () => void;
  onOpenPractice: () => void;
  onOpenAdmin: () => void;
  onOpenAccount: () => void;
  onOpenInterpreter: () => void;
}) {
  const [pins, setPins] = useState(() => readSidebarPins(ownerKey, DEFAULT_PINS));
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPinnedOpen, setMenuPinnedOpen] = useState(false);
  const [menuTop, setMenuTop] = useState(12);
  const barRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeMenuTimer = useRef<number>();

  function clearMenuTimer() {
    window.clearTimeout(closeMenuTimer.current);
    closeMenuTimer.current = undefined;
  }

  function closeMenu() {
    clearMenuTimer();
    setMenuPinnedOpen(false);
    setMenuOpen(false);
  }

  function previewMenu(pointerType: string) {
    if (pointerType !== "mouse" || compact) return;
    clearMenuTimer();
    setMenuOpen(true);
  }

  function leaveMenu(pointerType: string) {
    if (pointerType !== "mouse" || compact || menuPinnedOpen) return;
    clearMenuTimer();
    closeMenuTimer.current = window.setTimeout(() => {
      const focused = document.activeElement;
      if (!(focused && menuRef.current?.contains(focused) && focused.matches(":focus-visible"))) setMenuOpen(false);
    }, 150);
  }

  function toggleMenu() {
    clearMenuTimer();
    if (menuPinnedOpen) closeMenu();
    else {
      setMenuPinnedOpen(true);
      setMenuOpen(true);
    }
  }

  useEffect(() => () => clearMenuTimer(), []);

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === sidebarPinsKey(ownerKey) || event.key === null) setPins(readSidebarPins(ownerKey, DEFAULT_PINS));
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [ownerKey]);

  useLayoutEffect(() => {
    if (!menuOpen) return;
    const position = () => {
      const bar = barRef.current?.getBoundingClientRect();
      const trigger = triggerRef.current?.getBoundingClientRect();
      const menuHeight = menuRef.current?.offsetHeight;
      if (bar && trigger && menuHeight !== undefined) setMenuTop(Math.max(12 - bar.top, Math.min(trigger.top - bar.top, window.innerHeight - bar.top - menuHeight - 12)));
    };
    position();
    window.addEventListener("resize", position);
    return () => window.removeEventListener("resize", position);
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) closeMenu();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeMenu();
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [menuOpen]);

  const tools: SidebarTool[] = [
    { id: "lessons", label: "Список занятий", Icon: List, active: isExplorerOpen, action: onOpenLessons },
    { id: "interpreter", label: "Интерпретатор Python", openLabel: "Открыть интерпретатор Python", Icon: Terminal, active: isInterpreterOpen, action: onOpenInterpreter },
    { id: "theory", label: "Теория", openLabel: "Открыть теорию", Icon: BookOpen, active: activeView === "theory", action: onOpenTheory },
    { id: "practice", label: "Практика", openLabel: "Открыть практику", Icon: SquarePen, active: activeView === "practice", action: onOpenPractice },
    ...(isPlatformAdmin ? [{ id: "platform-admin", label: "Администрирование платформы", Icon: LayoutDashboard, href: `${normalizedBase(import.meta.env.BASE_URL)}admin-platform` }] : []),
    ...(isAdmin ? [{ id: "admin", label: "Админ-панель", Icon: ShieldCheck, active: isAdminOpen, action: onOpenAdmin }] : []),
    { id: "account", label: "Личный кабинет", openLabel: "Открыть личный кабинет", Icon: User, active: isAccountOpen, action: onOpenAccount },
  ];

  function togglePin(id: string) {
    if (isRequiredSidebarPin(id)) return;
    const next = toggleSidebarPin(id, pins);
    setPins(next);
    writeSidebarPins(ownerKey, next);
  }

  function renderPinned(tool: SidebarTool) {
    const className = `activity-button${tool.id === "lessons" ? " activity-button--lessons" : ""} ${tool.active ? "activity-button--active" : ""}`;
    const icon = <tool.Icon size={20} aria-hidden="true" />;
    if (tool.href) return <a key={tool.id} className={className} href={tool.href} title={tool.label} aria-label={tool.label}>{icon}</a>;
    return <button key={tool.id} className={className} type="button" title={tool.label}
      aria-label={tool.id === "lessons" ? (isExplorerOpen ? "Свернуть занятия" : "Открыть список занятий") : tool.openLabel ?? `Открыть ${tool.label.toLowerCase()}`}
      aria-expanded={tool.id === "lessons" ? isExplorerOpen : undefined}
      aria-controls={tool.id === "lessons" ? "workspace-lessons-drawer" : undefined}
      onPointerEnter={tool.id === "lessons" ? (event) => onLessonsPointerEnter(event.pointerType) : undefined}
      onPointerLeave={tool.id === "lessons" ? (event) => onLessonsPointerLeave(event.pointerType) : undefined}
      onClick={tool.action}>{icon}</button>;
  }

  return <aside ref={barRef} className="activity-bar activity-bar--tools" aria-label="Навигация">
    <div className="activity-bar__fixed">
      {tools.filter((tool) => isRequiredSidebarPin(tool.id) && tool.id !== "admin" && tool.id !== "account").map(renderPinned)}
    </div>
    <button ref={triggerRef} className={`activity-button${menuOpen ? " activity-button--active" : ""}`} type="button"
      title="Все инструменты" aria-label="Все инструменты" aria-expanded={menuOpen} aria-controls="workspace-tools-menu"
      onPointerEnter={(event) => previewMenu(event.pointerType)} onPointerLeave={(event) => leaveMenu(event.pointerType)}
      onClick={toggleMenu}><Ellipsis size={21} aria-hidden="true" /></button>
    <section ref={(element) => { menuRef.current = element; if (element) element.inert = !menuOpen; }} id="workspace-tools-menu"
      className={`sidebar-tools-menu${menuOpen ? " sidebar-tools-menu--open" : ""}`} style={{ top: menuTop }} aria-label="Все инструменты" aria-hidden={!menuOpen}
      onPointerEnter={(event) => previewMenu(event.pointerType)} onPointerLeave={(event) => leaveMenu(event.pointerType)}>
      <div className="sidebar-tools-menu__heading">Инструменты</div>
      <ul className="sidebar-tools-menu__list">
        {tools.filter((tool) => !isRequiredSidebarPin(tool.id)).map((tool) => <li key={tool.id} className="sidebar-tools-menu__row">
          {tool.href
            ? <a className="sidebar-tools-menu__open" href={tool.href}><tool.Icon size={19} aria-hidden="true" /><span>{tool.label}</span></a>
            : <button className="sidebar-tools-menu__open" type="button" onClick={() => { closeMenu(); tool.action?.(); }}><tool.Icon size={19} aria-hidden="true" /><span>{tool.label}</span></button>}
          <button className={`sidebar-tools-menu__pin${pins.includes(tool.id) ? " is-pinned" : ""}`} type="button"
            aria-label={`${pins.includes(tool.id) ? "Открепить" : "Закрепить"}: ${tool.label}`}
            aria-pressed={pins.includes(tool.id)} title={pins.includes(tool.id) ? "Открепить" : "Закрепить"}
            onClick={() => togglePin(tool.id)}>{pins.includes(tool.id) ? <Pin size={17} aria-hidden="true" /> : <PinOff size={17} aria-hidden="true" />}</button>
        </li>)}
      </ul>
    </section>
    <div className="activity-bar__separator" aria-hidden="true" />
    <div className="activity-bar__optional">
      {tools.filter((tool) => !isRequiredSidebarPin(tool.id) && isSidebarToolPinned(tool.id, pins)).map(renderPinned)}
    </div>
    <div className="activity-bar__bottom">
      {tools.filter((tool) => tool.id === "admin" || tool.id === "account").map(renderPinned)}
    </div>
  </aside>;
}
