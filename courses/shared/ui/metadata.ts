export type LessonMetadata = { block: string; blockNumber: number; lessonNumber: string; tags?: string[] };

/** Course-neutral metadata for authoring previews that have no course resolver. */
export function getGenericLessonMetadata(source: string, module?: string, title?: string): LessonMetadata | null {
  const path = source.replace(/\\/g, "/");
  const blockNumber = Number(path.match(/(?:^|\/)block_(\d+)\//)?.[1]);
  const pathNumber = path.match(/\/(?:Lesson)?(\d+(?:[._]\d+)*)(?:\s*-|(?:\.tsx)?$)/)?.[1];
  if (!blockNumber || !pathNumber) return null;
  const displayNumber = title?.match(/^\s*(\d+(?:\.\d+)*)(?:\.\s|\s*[-–—]\s)/)?.[1];
  const lessonNumber = (displayNumber ?? pathNumber).replace(/_/g, ".").split(".").map(part => String(Number(part))).join(".");
  const catalog = module?.match(/^Блок\s+(\d+)\s*[.·:—–-]\s*(.+)$/i);
  const blockTitle = catalog && Number(catalog[1]) === blockNumber ? catalog[2] : undefined;
  return { block: `Блок ${blockNumber}${blockTitle ? ` ${blockTitle}` : ""}`, blockNumber, lessonNumber };
}

/** Neutral labels for shared previews; course-specific tags come from metadata. */
export function getLessonTags(): string[] {
  return ["тема занятия", "практика"];
}
