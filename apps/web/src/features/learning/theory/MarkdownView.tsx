import "./lesson-theory.css";
import React, { useEffect, useMemo, useState } from "react";
import { getLessonTheory as fetchLessonTheory } from "../api";
import { NextLessonLink } from "../navigation/LessonNavigation";
import { getLessonHeadingTitle } from "../course";
import { loadCoursePages, type CourseId, type LoadedCoursePages } from "../../../course-runtime/course-registry";
import { LessonMetadataProvider } from "@course-ui";
import { RichHero } from "@course-ui";
import { LessonLoading } from "../LessonLoading";
import { LessonPageBoundary } from "./LessonPageBoundary";
import type { Lesson, LessonTheoryResponse, LessonVideo } from "../types";

type MarkdownBlock =
  | { type: "code"; lang: string; text: string }
  | { type: "heading"; level: number; text: string }
  | { type: "hr" }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "paragraph"; text: string };

function parseMarkdownBlocks(source: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  let codeLines: string[] | null = null;
  let codeLang = "";
  let list: { ordered: boolean; items: string[] } | null = null;

  function flushParagraph() {
    if (paragraph.length > 0) {
      blocks.push({ type: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
  }

  function flushList() {
    if (list) {
      blocks.push({ type: "list", ordered: list.ordered, items: list.items });
      list = null;
    }
  }

  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();

    if (codeLines !== null) {
      if (trimmed.startsWith("```")) {
        blocks.push({ type: "code", lang: codeLang, text: codeLines.join("\n") });
        codeLines = null;
        codeLang = "";
      } else {
        codeLines.push(rawLine);
      }
      continue;
    }

    if (trimmed.startsWith("```")) {
      flushParagraph();
      flushList();
      codeLang = trimmed.slice(3).trim();
      codeLines = [];
      continue;
    }

    const headingMatch = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (headingMatch) {
      flushParagraph();
      flushList();
      blocks.push({ type: "heading", level: headingMatch[1].length, text: headingMatch[2] });
      continue;
    }

    if (trimmed === "---" || trimmed === "***") {
      flushParagraph();
      flushList();
      blocks.push({ type: "hr" });
      continue;
    }

    const listMatch = /^(?:[-*+]|\d+[.)])\s+(.*)$/.exec(trimmed);
    if (listMatch) {
      flushParagraph();
      const ordered = /^\d/.test(trimmed);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push(listMatch[1]);
      continue;
    }

    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }

    flushList();
    paragraph.push(trimmed);
  }

  if (codeLines !== null) {
    blocks.push({ type: "code", lang: codeLang, text: codeLines.join("\n") });
  }
  flushParagraph();
  flushList();

  return blocks;
}

function renderInlineMarkdown(text: string, keyPrefix: string): React.ReactNode[] {
  const cleaned = text.replace(/\[\[(.*?)\]\]/g, "$1");
  const nodes: React.ReactNode[] = [];
  const tokenRe = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let lastIndex = 0;
  let tokenIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenRe.exec(cleaned)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(cleaned.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(<code key={`${keyPrefix}-inline-${tokenIndex}`}>{token.slice(1, -1)}</code>);
    } else {
      nodes.push(<strong key={`${keyPrefix}-inline-${tokenIndex}`}>{token.slice(2, -2)}</strong>);
    }
    lastIndex = match.index + token.length;
    tokenIndex += 1;
  }

  if (lastIndex < cleaned.length) {
    nodes.push(cleaned.slice(lastIndex));
  }

  return nodes;
}

export function MarkdownView({ source }: { source: string }) {
  const blocks = useMemo(() => parseMarkdownBlocks(source), [source]);

  return (
    <div className="markdown-body">
      {blocks.map((block, index) => {
        const key = `md-${index}`;

        if (block.type === "code") {
          return (
            <pre className="markdown-code" data-lang={block.lang || "code"} key={key}>
              <code>{block.text}</code>
            </pre>
          );
        }
        if (block.type === "heading") {
          const level = Math.min(Math.max(block.level, 1), 4);
          const HeadingTag = `h${level}` as keyof React.JSX.IntrinsicElements;
          return <HeadingTag key={key}>{renderInlineMarkdown(block.text, key)}</HeadingTag>;
        }
        if (block.type === "hr") {
          return <hr key={key} />;
        }
        if (block.type === "list") {
          const ListTag = block.ordered ? "ol" : "ul";
          return (
            <ListTag key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-item-${itemIndex}`}>{renderInlineMarkdown(item, `${key}-item-${itemIndex}`)}</li>
              ))}
            </ListTag>
          );
        }
        return <p key={key}>{renderInlineMarkdown(block.text, key)}</p>;
      })}
    </div>
  );
}

function getLessonTheory(lesson: Lesson): string[] {
  return lesson.theory?.length
    ? lesson.theory
    : [
        "Теория для этого урока скоро появится. Пока используйте практику и видео как рабочий черновик.",
        "Каждый урок будет состоять из короткого объяснения, видео и нескольких заданий с автоматической проверкой.",
      ];
}

function getLessonVideo(lesson: Lesson): LessonVideo | null | undefined {
  return lesson.video;
}

function LessonVideoCard({ video }: { video: LessonVideo }) {
  return (
    <section className="lesson-video-card">
      <div>
        <span>Видео из YouTube</span>
        <h2>{video.title}</h2>
      </div>
      <iframe
        src={video.embed_url}
        title={video.title}
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        allowFullScreen
      />
    </section>
  );
}

export function LearningTheoryView({ lesson, courseId }: { lesson: Lesson; courseId: CourseId }) {
  const video = getLessonVideo(lesson);
  const [coursePages, setCoursePages] = useState<LoadedCoursePages>();
  const [isRegistryLoading, setIsRegistryLoading] = useState(true);
  const [registryError, setRegistryError] = useState<string>();
  useEffect(() => {
    let cancelled = false;
    setCoursePages(undefined);
    setRegistryError(undefined);
    setIsRegistryLoading(true);
    loadCoursePages(courseId).then((loaded) => {
      if (!cancelled) setCoursePages(loaded);
    }).catch(() => {
      if (!cancelled) setRegistryError(courseId);
    }).finally(() => {
      if (!cancelled) setIsRegistryLoading(false);
    });
    return () => { cancelled = true; };
  }, [courseId]);
  const courseReady = coursePages?.courseId === courseId;
  const RichLessonPage = lesson.source_file && coursePages?.courseId === courseId ? coursePages.richLessonPages[lesson.source_file] : undefined;
  const theoryKey = `${courseId}:${lesson.id}`;
  const [theoryResult, setTheoryResult] = useState<{ key: string; markdown: string | null }>();
  const theoryMarkdown = theoryResult?.key === theoryKey ? theoryResult.markdown : null;
  const [isTheoryLoading, setIsTheoryLoading] = useState(true);

  useEffect(() => {
    if (isRegistryLoading || !courseReady || registryError === courseId) return;
    if (RichLessonPage) {
      return;
    }

    let cancelled = false;
    setIsTheoryLoading(true);
    setTheoryResult(undefined);

    fetchLessonTheory(lesson.id)
      .then((data) => {
        if (!cancelled) {
          setTheoryResult({ key: theoryKey, markdown: data.theory_markdown });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTheoryResult({ key: theoryKey, markdown: null });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsTheoryLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [theoryKey, courseId, courseReady, lesson.id, RichLessonPage, isRegistryLoading, registryError]);

  if (isRegistryLoading || !courseReady && registryError !== courseId) return <LessonLoading />;
  if (registryError === courseId) return <div className="panel-message" role="alert">Не удалось загрузить материалы этого курса. Обновите страницу и повторите попытку.</div>;

  if (RichLessonPage) {
    return (
      <LessonMetadataProvider resolveMetadata={coursePages?.courseId === courseId ? coursePages.getLessonMetadata : undefined} source={lesson.source_file ?? ""} module={lesson.module} title={lesson.title}>
      <div className="document-view document-view--rich">
        <LessonPageBoundary key={`${courseId}:${lesson.source_file}`}>
          <RichLessonPage module={lesson.module} />
          {video && <LessonVideoCard video={video} />}
          <NextLessonLink />
        </LessonPageBoundary>
      </div>
      </LessonMetadataProvider>
    );
  }

  return (
    <LessonMetadataProvider resolveMetadata={coursePages?.courseId === courseId ? coursePages.getLessonMetadata : undefined} source={lesson.source_file ?? ""} module={lesson.module} title={lesson.title}>
    <div className="document-view">
      <RichHero chip={lesson.module} title={getLessonHeadingTitle(lesson)} intro={lesson.duration} />
      {theoryMarkdown ? (
        <MarkdownView source={theoryMarkdown.replace(/^\s*#\s+[^\n]*\n/, "")} />
      ) : isTheoryLoading || theoryResult?.key !== theoryKey ? (
        <LessonLoading />
      ) : (
        getLessonTheory(lesson).map((paragraph) => <p key={paragraph}>{paragraph}</p>)
      )}
      {video && <LessonVideoCard video={video} />}
      <NextLessonLink />
    </div>
    </LessonMetadataProvider>
  );
}
