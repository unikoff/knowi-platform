import { useEffect } from "react";
import { ArrowLeft } from "lucide-react";
import { catalogPreviewCourses, type CatalogPreviewSlug } from "../../shared/catalog-preview-courses";
import { normalizedBase } from "../../shared/course-paths";
import "./catalog.css";

export function CatalogCoursePlaceholder({ slug }: { slug: CatalogPreviewSlug }) {
  const course = catalogPreviewCourses.find(item => item.slug === slug)!;
  useEffect(() => {
    const previousTitle = document.title;
    document.title = `${course.title} | knowi`;
    return () => { document.title = previousTitle; };
  }, [course.title]);

  return <div className="course-library">
    <main className="course-library__container catalog-placeholder" aria-labelledby="preview-title">
      <a className="catalog-placeholder__back" href={`${normalizedBase(import.meta.env.BASE_URL)}catalog`}><ArrowLeft size={17} aria-hidden="true" />Все курсы</a>
      <h1 id="preview-title">{course.title}</h1>
      <p>Страница курса готовится.</p>
    </main>
  </div>;
}
