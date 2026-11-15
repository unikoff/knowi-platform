import { createLessonPageRegistry, type LessonModule } from "../../../../apps/web/src/course-runtime/page-registry";

// Literal, course-local, non-eager glob keeps the other course outside this module graph.
const modules = import.meta.glob<LessonModule>("./chapter_*/block_*/*.tsx");
export const defineLessonPages = createLessonPageRegistry(modules);
