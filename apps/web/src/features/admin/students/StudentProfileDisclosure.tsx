import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";

export function StudentProfileDisclosure({ title, children, onFirstOpen }: { title: string; children: ReactNode; onFirstOpen?: () => void }) {
  const [hasOpened, setHasOpened] = useState(false);
  return <details className="course-admin-statistics" onToggle={(event) => {
    if (event.currentTarget.open && !hasOpened) {
      setHasOpened(true);
      onFirstOpen?.();
    }
  }}>
    <summary className="course-admin-statistics__summary">
      <span>{title}</span>
      <ChevronDown size={20} aria-hidden="true" />
    </summary>
    {hasOpened && <div className="course-admin-statistics__content">{children}</div>}
  </details>;
}
