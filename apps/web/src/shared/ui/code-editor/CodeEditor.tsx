import "./code-editor.css";
import React, { useLayoutEffect, useMemo, useRef } from "react";
import { highlightPython } from "../../lib/python-highlight";
import { VisualHeading } from "../VisualHeading";

const INDENT = "    ";

type CodeEditorProps = {
  value: string;
  onChange: (next: string) => void;
  ariaLabel?: string;
  scrollable?: boolean;
};

/**
 * Лёгкий редактор кода без зависимостей: прозрачная textarea поверх
 * подсвеченного <pre> (классическая overlay-техника). Поддерживает Tab /
 * Shift+Tab, автоотступ по Enter и номера строк. Растёт по содержимому —
 * прокрутка остаётся за страницей, поэтому слои не рассинхронизируются.
 */
export function CodeEditor({ value, onChange, ariaLabel, scrollable = false }: CodeEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const gutterRef = useRef<HTMLDivElement | null>(null);
  const highlightRef = useRef<HTMLPreElement | null>(null);
  const pendingCaret = useRef<number | null>(null);

  const highlighted = useMemo(() => highlightPython(value) + "\n", [value]);
  const lineCount = value.split("\n").length;

  useLayoutEffect(() => {
    if (pendingCaret.current !== null && textareaRef.current) {
      textareaRef.current.setSelectionRange(pendingCaret.current, pendingCaret.current);
      pendingCaret.current = null;
    }
  }, [value]);

  function applyEdit(next: string, caret: number) {
    pendingCaret.current = caret;
    onChange(next);
  }

  function syncScroll(event: React.UIEvent<HTMLTextAreaElement>) {
    if (!scrollable) return;
    const { scrollTop, scrollLeft } = event.currentTarget;
    if (highlightRef.current) {
      highlightRef.current.scrollTop = scrollTop;
      highlightRef.current.scrollLeft = scrollLeft;
    }
    if (gutterRef.current) gutterRef.current.scrollTop = scrollTop;
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    const textarea = event.currentTarget;
    const { selectionStart, selectionEnd } = textarea;

    if (event.key === "Tab") {
      event.preventDefault();

      const lineStart = value.lastIndexOf("\n", selectionStart - 1) + 1;

      if (event.shiftKey) {
        // Снимаем один уровень отступа с текущей строки.
        const removable = value.slice(lineStart, lineStart + INDENT.length);
        const removeCount = removable.startsWith(INDENT)
          ? INDENT.length
          : removable.match(/^ +/)?.[0].length ?? 0;
        if (removeCount > 0) {
          const next = value.slice(0, lineStart) + value.slice(lineStart + removeCount);
          applyEdit(next, Math.max(lineStart, selectionStart - removeCount));
        }
        return;
      }

      if (selectionStart !== selectionEnd && value.slice(selectionStart, selectionEnd).includes("\n")) {
        // Многострочное выделение — сдвигаем каждую строку.
        const blockEnd = value.indexOf("\n", selectionEnd);
        const sliceEnd = blockEnd === -1 ? value.length : blockEnd;
        const block = value.slice(lineStart, sliceEnd);
        const indented = block
          .split("\n")
          .map((line) => INDENT + line)
          .join("\n");
        const next = value.slice(0, lineStart) + indented + value.slice(sliceEnd);
        applyEdit(next, selectionEnd + INDENT.length * block.split("\n").length);
        return;
      }

      const next = value.slice(0, selectionStart) + INDENT + value.slice(selectionEnd);
      applyEdit(next, selectionStart + INDENT.length);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();

      const lineStart = value.lastIndexOf("\n", selectionStart - 1) + 1;
      const currentLine = value.slice(lineStart, selectionStart);
      const baseIndent = currentLine.match(/^ */)?.[0] ?? "";
      const extraIndent = currentLine.trimEnd().endsWith(":") ? INDENT : "";
      const insertion = "\n" + baseIndent + extraIndent;

      const next = value.slice(0, selectionStart) + insertion + value.slice(selectionEnd);
      applyEdit(next, selectionStart + insertion.length);
    }
  }

  return (
    <div className={`code-editor-shell${scrollable ? " code-editor-shell--scrollable" : ""}`}>
      <div className="code-gutter" ref={gutterRef} aria-hidden="true">
        {Array.from({ length: lineCount }, (_, index) => (
          <span key={index}>{index + 1}</span>
        ))}
      </div>
      <div className="code-area">
        <pre className="code-highlight" ref={highlightRef} aria-hidden="true" dangerouslySetInnerHTML={{ __html: highlighted }} />
        <textarea
          ref={textareaRef}
          className="code-input"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          onScroll={syncScroll}
          spellCheck={false}
          autoCapitalize="off"
          autoComplete="off"
          autoCorrect="off"
          wrap="off"
          aria-label={ariaLabel}
        />
      </div>
    </div>
  );
}

/**
 * Общая карточка примера с подписью: Python с подсветкой или обычный текст.
 * Редактор выше остаётся отдельным интерактивным компонентом.
 */
export function CodeBlock({
  code,
  caption,
  language = "python",
  showLanguage = false,
  preserveWhitespace = false,
  as = "figure",
}: {
  code: string;
  caption?: string;
  language?: string;
  showLanguage?: boolean;
  preserveWhitespace?: boolean;
  as?: "figure" | "span";
}) {
  const source = preserveWhitespace ? code : code.trimEnd();
  const isPython = ["python", "py"].includes(language.toLowerCase());
  const highlighted = useMemo(
    () => isPython ? highlightPython(source) : null,
    [source, isPython],
  );

  // Кнопки вариантов допускают только phrasing content: span/code вместо figure/pre.
  const Container = as;
  const Caption = as === "span" ? "span" : "figcaption";
  const content = highlighted === null
    ? <code>{source}</code>
    : <code dangerouslySetInnerHTML={{ __html: highlighted }} />;

  return (
    <Container className="code-block" aria-label={caption}>
      <Caption className={as === "span" ? "code-block-caption" : undefined}>
        {showLanguage && <span className="code-block-language">{language}</span>}
        <VisualHeading kind="code" as="span" />
        {caption && <span className="code-block-context">{caption}</span>}
      </Caption>
      {as === "span" ? content : <pre>{content}</pre>}
    </Container>
  );
}
