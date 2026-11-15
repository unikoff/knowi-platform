import type { ComponentType } from "react";
import { pages as pythonFoundations } from "./chapter_01";
import { pages as pythonDeeper } from "./chapter_02";
import { pages as plannerApi } from "./chapter_03";
import { pages as fastapiSqlalchemy } from "./chapter_04";
import { pages as fastapiAuth } from "./chapter_05";
import { pages as postgresqlStudyhub } from "./chapter_06";
import { pages as asyncStudyhub } from "./chapter_07";
import { pages as deployableStudyhub } from "./chapter_08";
import { pages as studyhubLmsRelease } from "./chapter_09";
export { getLessonMetadata } from "./metadata";

// Current MarkdownView compatibility shape. Chapter manifests contain the stable source keys.
export const richLessonPages: Record<string, ComponentType<{ module?: string }>> = {
  ...pythonFoundations,
  ...pythonDeeper,
  ...plannerApi,
  ...fastapiSqlalchemy,
  ...fastapiAuth,
  ...postgresqlStudyhub,
  ...asyncStudyhub,
  ...deployableStudyhub,
  ...studyhubLmsRelease,
};
