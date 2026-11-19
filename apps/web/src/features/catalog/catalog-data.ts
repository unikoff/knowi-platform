import { BookOpen, Braces, Code2, Dna, Landmark, Sigma, type LucideIcon } from "lucide-react";
import { registeredCourses } from "../../../.cache/course-registry/course-ids";
import { catalogPreviewCourses } from "../../shared/catalog-preview-courses";
import { catalogPreviewUrl, courseUrls } from "../../shared/course-paths";

export const catalogGroups = [
  { id: "professional", label: "Профобразование", navLabel: "Профобразование" },
  { id: "ege", label: "Подготовка к ЕГЭ", navLabel: "ЕГЭ" },
  { id: "oge", label: "Подготовка к ОГЭ", navLabel: "ОГЭ" },
] as const;
export type CatalogGroupId = typeof catalogGroups[number]["id"];
export type CatalogGroupFilter = "all" | CatalogGroupId;
export type CatalogCourse = { id: string; title: string; href: string; group: CatalogGroupId };

export const catalogCourses: CatalogCourse[] = [
  ...registeredCourses.filter(course => course.id !== "backend_copy").map(course => ({
    id: course.id, title: course.title, href: courseUrls(import.meta.env.BASE_URL, course.id).about, group: "professional" as const,
  })),
  ...catalogPreviewCourses.map(course => ({
    id: course.slug, title: course.title, href: catalogPreviewUrl(import.meta.env.BASE_URL, course.slug), group: course.group,
  })),
];

type CoursePresentation = { tags: string[]; cover: string; Icon: LucideIcon; color: string };

const subjectPresentation: Record<string, { label: string; Icon: LucideIcon; color: string }> = {
  informatika: { label: "Информатика", Icon: Code2, color: "var(--syntax-class)" },
  obshchestvoznanie: { label: "Обществознание", Icon: Landmark, color: "var(--callout-yellow)" },
  biologiya: { label: "Биология", Icon: Dna, color: "var(--callout-green)" },
  matematika: { label: "Математика", Icon: Sigma, color: "var(--syntax-keyword)" },
  "russkiy-yazyk": { label: "Русский язык", Icon: BookOpen, color: "var(--accent)" },
  "angliyskiy-yazyk": { label: "Английский язык", Icon: BookOpen, color: "var(--syntax-class)" },
  fizika: { label: "Физика", Icon: Sigma, color: "var(--syntax-keyword)" },
  khimiya: { label: "Химия", Icon: Dna, color: "var(--callout-green)" },
  istoriya: { label: "История", Icon: Landmark, color: "var(--callout-yellow)" },
  geografiya: { label: "География", Icon: Landmark, color: "var(--accent)" },
  literatura: { label: "Литература", Icon: BookOpen, color: "var(--syntax-class)" },
};

const professionalPresentation: Record<string, CoursePresentation> = {
  backend: { tags: ["Python", "Backend", "С нуля"], cover: "Python", Icon: Braces, color: "var(--accent)" },
  "python-basics": { tags: ["Python", "С нуля"], cover: "Python", Icon: Braces, color: "var(--accent)" },
  "programming-basics": { tags: ["Программирование", "С нуля"], cover: "Код", Icon: Code2, color: "var(--syntax-class)" },
  "algorithms-data-structures": { tags: ["Алгоритмы", "Структуры данных"], cover: "Algo", Icon: Braces, color: "var(--syntax-keyword)" },
  "sql-practice": { tags: ["SQL", "Базы данных"], cover: "SQL", Icon: Code2, color: "var(--callout-green)" },
  "html-css": { tags: ["HTML", "CSS"], cover: "HTML/CSS", Icon: Code2, color: "var(--syntax-class)" },
  "javascript-basics": { tags: ["JavaScript", "С нуля"], cover: "JS", Icon: Braces, color: "var(--callout-yellow)" },
  "react-frontend": { tags: ["React", "Frontend"], cover: "React", Icon: Code2, color: "var(--syntax-class)" },
  "software-testing": { tags: ["Тестирование", "QA"], cover: "QA", Icon: BookOpen, color: "var(--callout-green)" },
  "data-analytics": { tags: ["Аналитика", "Данные"], cover: "Данные", Icon: Sigma, color: "var(--syntax-keyword)" },
  "git-teamwork": { tags: ["Git", "Командная работа"], cover: "Git", Icon: Braces, color: "var(--accent)" },
  "linux-developer": { tags: ["Linux", "Разработка"], cover: "Linux", Icon: Code2, color: "var(--callout-yellow)" },
  "docker-deploy": { tags: ["Docker", "Деплой"], cover: "Docker", Icon: Code2, color: "var(--syntax-class)" },
  "math-for-it": { tags: ["Математика", "IT"], cover: "Math", Icon: Sigma, color: "var(--syntax-keyword)" },
};

export const courseDetails = new Map<string, CoursePresentation>([
  ...Object.entries(professionalPresentation),
  ...catalogPreviewCourses.flatMap(course => {
    if (course.group === "professional") return [];
    const subject = subjectPresentation[course.slug.slice(4)];
    const exam = course.group === "ege" ? "ЕГЭ" : "ОГЭ";
    return [[course.slug, { tags: [exam, subject.label], cover: exam, Icon: subject.Icon, color: subject.color }] as [string, CoursePresentation]];
  }),
]);
export const fallbackCourseDetails = { tags: [], cover: "knowi", Icon: BookOpen, color: "var(--accent)" };

// Each pack has its own manually curated selection, not measured traffic.
export const demoRecommendationIds: Record<CatalogGroupFilter, readonly string[]> = {
  all: ["backend", "ege-informatika", "oge-matematika", "python-basics", "ege-biologiya", "oge-russkiy-yazyk", "sql-practice", "ege-obshchestvoznanie", "oge-fizika", "html-css"],
  professional: ["backend", "python-basics", "programming-basics", "algorithms-data-structures", "sql-practice", "html-css", "javascript-basics", "react-frontend", "software-testing", "data-analytics"],
  ege: ["ege-informatika", "ege-matematika", "ege-biologiya", "ege-obshchestvoznanie", "ege-russkiy-yazyk", "ege-angliyskiy-yazyk", "ege-fizika", "ege-khimiya", "ege-istoriya", "ege-geografiya"],
  oge: ["oge-informatika", "oge-matematika", "oge-biologiya", "oge-obshchestvoznanie", "oge-russkiy-yazyk", "oge-angliyskiy-yazyk", "oge-fizika", "oge-khimiya", "oge-istoriya", "oge-geografiya"],
};
