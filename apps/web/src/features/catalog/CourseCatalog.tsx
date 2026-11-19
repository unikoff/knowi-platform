import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { BrandLogo } from "../../shared/brand/BrandLogo";
import { normalizedBase } from "../../shared/course-paths";
import { catalogCourses, catalogGroups, courseDetails, demoRecommendationIds, type CatalogGroupFilter } from "./catalog-data";
import { TopCoursesCarousel } from "./TopCoursesCarousel";
import { CourseTile } from "./CourseTile";
import { CoursePreviewProvider } from "./CoursePreviewProvider";
import "./catalog.css";

function readGroup(): CatalogGroupFilter {
  const group = new URL(window.location.href).searchParams.get("group");
  return catalogGroups.find(item => item.id === group)?.id ?? "all";
}

export function CourseCatalog() {
  const [query, setQuery] = useState(() => new URL(window.location.href).searchParams.get("q") ?? "");
  const [group, setGroup] = useState<CatalogGroupFilter>(readGroup);
  const scrollContainer = useRef<HTMLDivElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const selectGroup = (next: CatalogGroupFilter) => {
    setGroup(next);
    scrollContainer.current?.scrollTo({ top: 0 });
  };
  const clearSearch = () => {
    setQuery("");
    searchInput.current?.focus();
  };
  const search = query.trim().toLocaleLowerCase("ru");
  const courses = catalogCourses.filter(course => {
    if (group !== "all" && course.group !== group) return false;
    const details = courseDetails.get(course.id);
    return [course.title, ...(details?.tags ?? [])].join(" ").toLocaleLowerCase("ru").includes(search);
  });
  const recommendations = demoRecommendationIds[group].flatMap(id => {
    const course = courses.find(item => item.id === id);
    return course ? [course] : [];
  }).slice(0, 10);
  const groups = catalogGroups.map(item => ({ ...item, courses: courses.filter(course => course.group === item.id) })).filter(item => item.courses.length > 0);
  const catalogUrl = `${normalizedBase(import.meta.env.BASE_URL)}catalog`;

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "Каталог курсов | knowi";
    return () => { document.title = previousTitle; };
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (group === "all") url.searchParams.delete("group");
    else url.searchParams.set("group", group);
    if (query) url.searchParams.set("q", query);
    else url.searchParams.delete("q");
    window.history.replaceState(window.history.state, "", url);
  }, [group, query]);

  return <CoursePreviewProvider><div className="course-library" ref={scrollContainer}>
    <a className="course-library__skip" href="#course-list">Перейти к курсам</a>
    <header className="course-library__header"><div className="course-library__header-row course-library__container">
      <a className="course-library__brand" href={catalogUrl} aria-label="knowi — каталог курсов">
        <BrandLogo />
      </a>
      <div className="course-library__packs" role="group" aria-label="Наборы курсов">
        <button type="button" aria-pressed={group === "all"} onClick={() => selectGroup("all")}>Все курсы</button>
        {catalogGroups.map(item => <button type="button" key={item.id} aria-pressed={group === item.id} onClick={() => selectGroup(item.id)}>
          {item.navLabel}
        </button>)}
      </div>
          <div className="course-library__search">
            <Search size={18} aria-hidden="true" />
            <input ref={searchInput} type="search" aria-label="Поиск курсов" placeholder="Поиск курсов" value={query} onChange={event => setQuery(event.target.value)} />
            {query && <button type="button" aria-label="Очистить поиск" onClick={clearSearch}><X size={16} aria-hidden="true" /></button>}
          </div>
    </div></header>

    <main className="course-library__container course-library__courses" id="course-list" aria-label="Каталог курсов" tabIndex={-1}>
        <TopCoursesCarousel courses={recommendations} selectionKey={`${group}:${search}`} renderCourse={(course, rank) => <CourseTile course={course} context="recommended" rank={rank} />} />

        {courses.length > 0 ? <div className="course-library__groups">
          {groups.map(item => <section key={item.id} aria-labelledby={`catalog-group-${item.id}`}>
            <h2 className={group === "all" ? "course-library__group-heading" : "course-library__visually-hidden"} id={`catalog-group-${item.id}`}>{item.label}</h2>
            <ul className="course-library__grid" aria-label={item.label}>{item.courses.map(course => <li key={course.id}><CourseTile course={course} /></li>)}</ul>
          </section>)}
        </div> : <div className="course-library__empty">
          <Search size={28} aria-hidden="true" />
          <h2>{catalogCourses.length ? "Курсы не найдены" : "Курсов пока нет"}</h2>
          {catalogCourses.length > 0 && <p>Измените запрос или выберите другой набор.</p>}
          {query && <button type="button" onClick={clearSearch}>Сбросить поиск</button>}
        </div>}
    </main>
  </div></CoursePreviewProvider>;
}
