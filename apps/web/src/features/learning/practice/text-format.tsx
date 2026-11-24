import React from "react";
import { CodeBlock } from "@course-ui";

export function formatManualPracticeResult(result: string): string {
  // Старые и новые тексты практики могут уже включать эту подпись, а компонент
  // выводит её один раз для единообразной структуры и доступности.
  return result.replace(/^\s*готово\s*,\s*если\s*:?\s*/iu, "");
}

export function renderPracticeText(text: string, { inlineOnly = false } = {}): React.ReactNode {
  const parts: React.ReactNode[] = [];
  const codePattern = /```([^\n`]*)\n([\s\S]*?)```|`([^`\n]+)`/g;
  let lastIndex = 0;

  for (const match of text.matchAll(codePattern)) {
    const matchIndex = match.index ?? lastIndex;
    if (matchIndex > lastIndex) {
      parts.push(text.slice(lastIndex, matchIndex));
    }

    if (match[1] !== undefined) {
      if (inlineOnly) {
        parts.push(match[0]);
      } else {
        const language = match[1].trim();
        parts.push(
          <div className="practice-code-snippet" key={`block:${matchIndex}`}>
            <CodeBlock
              code={match[2].replace(/\r?\n$/, "")}
              language={language || "text"}
              showLanguage={Boolean(language)}
              preserveWhitespace
            />
          </div>,
        );
      }
    } else if (match[3] !== undefined) {
      parts.push(
        <code className="lesson-token" key={`inline:${matchIndex}`}>
          {match[3]}
        </code>,
      );
    }

    lastIndex = matchIndex + match[0].length;
  }

  if (lastIndex === 0) {
    return text;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts;
}
