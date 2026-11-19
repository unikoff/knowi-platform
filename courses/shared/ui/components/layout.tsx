import React from "react";
import { Check, Tag } from "lucide-react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";
import { plainOptionText } from "./option-text";
import { LessonMetadataContext } from "../metadata-context";
import { getLessonTags } from "../metadata";
import { CodeBlock } from "@platform/shared/ui/code-editor/CodeEditor";

function renderInlineCode(value: React.ReactNode, keyPrefix: string): React.ReactNode {
  if (typeof value === "string") {
    const parts = value.split(/(`[^`]+`)/g);
    if (parts.length === 1) return value;
    return parts.map((part, index) =>
      part.startsWith("`") && part.endsWith("`")
        ? <code key={`${keyPrefix}-${index}`}>{part.slice(1, -1)}</code>
        : part,
    );
  }

  if (Array.isArray(value)) {
    return value.map((item, index) => {
      // Older lessons sometimes put the separator before an inline token inside
      // <code>. Move a single separator after adjacent prose outside the token.
      // Explicit whitespace examples and indentation must stay untouched.
      const previous = value[index - 1];
      if (
        typeof previous === "string" && /\S$/.test(previous) &&
        React.isValidElement<{ children?: React.ReactNode; "data-preserve-space"?: boolean }>(item) &&
        item.type === "code" && !item.props["data-preserve-space"] &&
        typeof item.props.children === "string" && /^ [^\s](?:[\s\S]*\S)?$/.test(item.props.children)
      ) {
        return <React.Fragment key={`${keyPrefix}-${index}`}>{" "}{React.cloneElement(item, {}, item.props.children.slice(1))}</React.Fragment>;
      }
      const rendered = renderInlineCode(item, `${keyPrefix}-${index}`);
      return React.isValidElement(rendered) && rendered.key == null
        ? React.cloneElement(rendered, { key: `${keyPrefix}-${index}` })
        : rendered;
    });
  }

  if (!React.isValidElement<Record<string, unknown>>(value)) {
    if (value && typeof value === "object") {
      const prototype = Object.getPrototypeOf(value);
      if (prototype === Object.prototype || prototype === null) {
        return Object.fromEntries(
          Object.entries(value).map(([name, propValue]) => [
            name,
            /^(?:code|fix|solution|initialCode|starterCode)$/i.test(name)
              ? propValue
              : renderInlineCode(propValue as React.ReactNode, `${keyPrefix}-${name}`),
          ]),
        ) as unknown as React.ReactNode;
      }
    }
    return value;
  }
  if (value.type === "code" || value.type === "pre") return value;

  const props = { ...value.props };
  for (const [name, propValue] of Object.entries(props)) {
    if (/^(?:code|fix|solution|initialCode|starterCode)$/i.test(name)) continue;
    props[name] = renderInlineCode(propValue as React.ReactNode, `${keyPrefix}-${name}`);
  }
  return React.cloneElement(value, props);
}

export function RichLesson({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={`rich-lesson${className ? ` ${className}` : ""}`}>{renderInlineCode(children, "rich-inline-code")}</div>;
}

export function RichHero({
  chip,
  title,
  intro,
  tags,
}: {
  chip?: string;
  title: string;
  intro: React.ReactNode;
  tags?: { icon: React.ReactNode; label: string }[];
}) {
  const metadata = React.useContext(LessonMetadataContext);
  const resolvedTags = metadata
    ? (metadata.tags ?? getLessonTags()).map((label) => ({ icon: <Tag size={14} aria-hidden="true" />, label }))
    : tags;
  return (
    <header className="rich-hero">
      <div className="rich-hero-meta">
        {(metadata?.block || chip) && <span className="rich-hero-chip">{metadata?.block ?? chip}</span>}
        {resolvedTags && resolvedTags.length > 0 && (
          <ul className="rich-hero-tags" aria-label="Темы занятия">
            {resolvedTags.map((tag, index) => (
              <li key={index}>
                {tag.icon}<span>{tag.label}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <h1>{title}</h1>
      <p>{intro}</p>
    </header>
  );
}

export function Section({ number, title, children, className }: { number?: string; title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rich-section${className ? ` ${className}` : ""}`}>
      <div className="rich-section-title">
        {number && <span>{number}</span>}
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

export function Lead({ children }: { children: React.ReactNode }) {
  return <p className="rich-lead">{children}</p>;
}

/** Overview list for section 00 and the final section of a lesson. */
export function LinkedNotes({ items }: {
  items: { title: React.ReactNode; description: React.ReactNode }[];
}) {
  return (
    <div className="linked-notes-block">
      <ul className="linked-notes" role="list">
        {items.map(({ title, description }, index) => (
          <li className="linked-note" key={index}>
            <h3>{title}</h3>
            <p>{description}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Full-width by default. Authors may set maxWidth and place companion content in children. */
export function ContentList({ items, children, maxWidth, ordered = false }: {
  items: { title?: React.ReactNode; description: React.ReactNode }[];
  children?: React.ReactNode;
  maxWidth?: React.CSSProperties["maxWidth"];
  ordered?: boolean;
}) {
  const List = ordered ? "ol" : "ul";
  const list = (
    <div className="lesson-list-block" style={{ maxWidth }}>
      <List className="lesson-list" role="list">
        {items.map(({ title, description }, index) => (
          <li key={index}>{title != null && title !== "" && <><strong>{title}</strong>{" "}</>}{description}</li>
        ))}
      </List>
    </div>
  );
  if (children == null) return list;
  return (
    <div className="lesson-list-layout">
      {list}
      <div className="lesson-list-content">{children}</div>
    </div>
  );
}

export function Callout({ children, tone = "warn", color }: {
  children: React.ReactNode;
  tone?: "accent" | "success" | "warn" | "danger";
  color?: string;
}) {
  return (
    <aside className={`lesson-callout lesson-callout--${tone}`} style={color ? { "--callout-color": color } as React.CSSProperties : undefined}>
      <VisualHeading kind="callout" />
      <p>{children}</p>
    </aside>
  );
}

export function TypeCards({ children }: { children: React.ReactNode }) {
  return <div className="type-cards-block"><div className="type-cards">{children}</div></div>;
}

export function TypeCard({
  badge,
  badgeTone,
  title,
  children,
  code,
}: {
  badge: string;
  badgeTone?: "float" | "str";
  title: string;
  children: React.ReactNode;
  code?: string;
}) {
  return (
    <article className="type-card">
      <span className={`type-card-badge ${badgeTone ? `type-card-badge--${badgeTone}` : ""}`}>{badge}</span>
      <h3>{title}</h3>
      <p>{children}</p>
      {code && <CodeBlock code={code} />}
    </article>
  );
}

export function PracticeCta({ text }: { text?: string }) {
  void text;
  return null;
}

/** Reusable checklist with a custom heading and independently selectable items. */
export function Checklist({ title = "Чек-лист", points }: {
  title?: React.ReactNode;
  points: React.ReactNode[];
}) {
  const titleId = React.useId();
  const hasCustomTitle = plainOptionText(title) !== "Чек-лист";
  const [checked, setChecked] = React.useState<Set<number>>(() => new Set());
  const completed = points.filter((_, index) => checked.has(index)).length;

  function toggle(index: number) {
    setChecked(previous => {
      const next = new Set(previous);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  return (
    <aside className="takeaways" aria-labelledby={titleId}>
      <div className="takeaways-head">
        <VisualHeading kind="checklist" id={hasCustomTitle ? undefined : titleId} />
        <span className="takeaways-progress" role="status" aria-label={`Отмечено ${completed} из ${points.length}`}>
          {completed} из {points.length}
        </span>
      </div>
      {hasCustomTitle && <h3 className="takeaways-title" id={titleId}>{title}</h3>}
      {points.length > 0 && <div className="takeaways-meter" aria-hidden="true"><span style={{ width: `${completed / points.length * 100}%` }} /></div>}
      <ul role="list">
        {points.map((point, index) => (
          <li key={index}>
            <label className="takeaways-option">
              <input type="checkbox" checked={checked.has(index)} onChange={() => toggle(index)} />
              <span className="takeaways-checkbox" aria-hidden="true"><Check size={14} /></span>
              <span className="takeaways-text">{point}</span>
            </label>
          </li>
        ))}
      </ul>
      {points.length > 0 && <div className="takeaways-footer">
        <span>{completed === points.length ? "Всё отмечено" : "Отмечайте пункты по мере выполнения"}</span>
        <button className="interactive-secondary" type="button" aria-disabled={completed === 0} onClick={() => {
          if (completed > 0) setChecked(new Set());
        }}>Сбросить</button>
      </div>}
    </aside>
  );
}

/** Backward-compatible lesson summary using the same interactive checklist. */
export function KeyTakeaways({ title = "Главное из урока", points }: {
  title?: React.ReactNode;
  points: React.ReactNode[];
}) {
  return <Checklist title={title} points={points} />;
}

// ---------- Схемы уроков ----------
export function TheoryBridge({ link, boundary }: { link?: string; boundary?: string; lesson?: number }) {
  if (!link || !boundary) return null;
  return <Callout tone="success"><strong>Связь с курсом.</strong> {link}{" "}<strong>Важно не перепутать:</strong> {boundary}</Callout>;
}
