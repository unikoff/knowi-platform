import { BrandLogo } from "@platform/shared/brand/BrandLogo";
import { useEffect, useId, useLayoutEffect, useRef, useState, type PointerEvent } from 'react';
import { ArrowDownRight, ArrowLeftRight, ArrowRight, BookOpen, Check, Code2, Video } from 'lucide-react';
import { LearningSystem } from './sections/LearningSystem';
import { CareerLaunch } from './sections/CareerLaunch';
import { LandingFaq } from './sections/LandingFaq';
import { AboutSection } from './sections/AboutSection';
import { LandingNav, PricingSection, ReviewsSection } from './LandingPages';
import { ConsultationForm } from './components/ConsultationForm';
import { LandingBackdrop } from './components/LandingBackdrop';
import { StartOfferPreview } from './components/StartOfferPreview';
import mentorPhoto from './assets/mentor-portrait.png';
import './styles/landing.css';
import './styles/start-offer.css';

function trackMetricLight(event: PointerEvent<HTMLLIElement>) {
  if (event.pointerType === 'touch' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const card = event.currentTarget;
  const bounds = card.getBoundingClientRect();
  card.style.setProperty('--metric-x', `${event.clientX - bounds.left}px`);
  card.style.setProperty('--metric-y', `${event.clientY - bounds.top}px`);
}

const mentoringAudiences = [
  {
    title: 'Начинаете с нуля',
    points: ['Первое знакомство с программированием', 'Основы Python', 'Первые задачи и практика'],
  },
  {
    title: 'Продолжаете обучение',
    points: ['Самостоятельное изучение Python', 'Обучение после курсов', 'Пробелы в основах', 'Больше практики и обратной связи'],
  },
  {
    title: 'Развиваетесь в backend',
    points: ['Углубление навыков Python', 'Разработка backend-проектов', 'Подготовка к собеседованиям', 'Переход на новую позицию'],
  },
  {
    title: 'Переходите в разработку',
    points: ['Смена профессии', 'Переход из смежной сферы в IT', 'Учёба параллельно с работой', 'Подготовка к первой работе в backend'],
  },
];

function MentoringAudiences() {
  const [active, setActive] = useState<number | null>(null);
  const id = useId();

  return <div className="mentoring-choice__cases">{mentoringAudiences.map((audience, index) => {
    const isOpen = active === index;
    const buttonId = `${id}-audience-${index}`;
    const panelId = `${id}-panel-${index}`;
    return <article className={`mentoring-choice__case${isOpen ? ' mentoring-choice__case--open' : ''}`} key={audience.title}>
      <button className="mentoring-choice__case-toggle" id={buttonId} type="button" aria-expanded={isOpen} aria-controls={panelId} onClick={() => setActive(isOpen ? null : index)}>
        <span>{audience.title}</span><span className="mentoring-choice__case-icon" aria-hidden="true">+</span>
      </button>
      <div className="mentoring-choice__case-content" id={panelId} role="region" aria-labelledby={buttonId} aria-hidden={!isOpen}>
        <div className="mentoring-choice__case-inner"><ul>{audience.points.map(point => <li key={point}>{point}</li>)}</ul></div>
      </div>
    </article>;
  })}</div>;
}

const mentoringFormats = ['С наставником', 'Курс в knowi', 'Самостоятельно'];

type ComparisonCell = string | { strength: string };

const mentoringComparison: { label: string; values: [ComparisonCell, ComparisonCell, ComparisonCell] }[] = [
  {
    label: 'Материалы и практика',
    values: [{ strength: 'Доступ к knowi' }, { strength: 'Уроки и задачи' }, { strength: 'Свои источники' }],
  },
  {
    label: 'Проект для портфолио',
    values: [{ strength: 'Вместе по шагам' }, { strength: 'Учебные проекты' }, { strength: 'Своя тема и стек' }],
  },
  {
    label: 'План обучения',
    values: [{ strength: 'Под вашу цель' }, { strength: 'Готовая программа' }, '—'],
  },
  {
    label: 'Навыки вайбкодинга',
    values: [{ strength: 'С пониманием backend' }, { strength: 'Доп. материалы' }, '—'],
  },
  {
    label: 'Личные занятия',
    values: [{ strength: 'Встречи 1:1' }, '—', '—'],
  },
  {
    label: 'Обратная связь по коду',
    values: [{ strength: 'Личное код-ревью' }, '—', '—'],
  },
  {
    label: 'Подготовка к собеседованию',
    values: [{ strength: 'С практиками из IT' }, '—', '—'],
  },
  {
    label: 'Оплата',
    values: [{ strength: 'Помесячно, без рассрочки' }, { strength: 'Подписка на материалы' }, '—'],
  },
];

function MentoringComparison() {
  const [activeRow, setActiveRow] = useState<string | null>(null);

  return <div className="mentoring-choice__comparison">
    <div className="mentoring-choice__comparison-heading">
      <h3 id="mentoring-comparison-title">Сравнение форматов обучения</h3>
      <p className="mentoring-choice__swipe-hint">
        <ArrowLeftRight size={15} aria-hidden="true" />
        Листайте таблицу в стороны
      </p>
    </div>
    <div className="mentoring-choice__table-wrap" role="region" aria-labelledby="mentoring-comparison-title" tabIndex={0}>
      <table className="mentoring-choice__table" aria-labelledby="mentoring-comparison-title">
        <thead>
          <tr>
            <th scope="col">Что получаете</th>
            {mentoringFormats.map((format, index) => <th scope="col" className={index === 0 ? 'choice-featured' : undefined} key={format}>{format}</th>)}
          </tr>
        </thead>
        <tbody>
          {mentoringComparison.map(({ label, values }) => <tr key={label}>
            <th scope="row">
              <button className="mentoring-choice__row-toggle" type="button" aria-label={`Выделить строку: ${label}`} aria-pressed={activeRow === label} onClick={() => setActiveRow(current => current === label ? null : label)}>
                {label}
              </button>
            </th>
            {values.map((value, index) => <td className={index === 0 ? 'choice-featured' : undefined} key={mentoringFormats[index]}>
              {typeof value === 'string'
                ? value
                : <span className="mentoring-choice__strength"><Check size={15} aria-hidden="true" /><span>{value.strength}</span></span>}
            </td>)}
          </tr>)}
        </tbody>
      </table>
    </div>
  </div>;
}

export function BackendLandingPage({ courseId, landingBaseUrl, learningUrl }: {
  courseId: string; landingBaseUrl: string; learningUrl: string;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const navigationRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const navigation = navigationRef.current;
    const viewport = viewportRef.current;
    if (!navigation || !viewport) return;

    const startOffer = viewport.querySelector('#start .landing-offer__panel');
    const updateNavigationSurface = () => {
      const bounds = startOffer?.getBoundingClientRect();
      navigation.toggleAttribute('data-accent-backdrop', Boolean(bounds && bounds.top < navigation.offsetHeight && bounds.bottom > 0));
    };
    const updateNavigationSpace = () => {
      viewport.style.setProperty('--landing-nav-space', `${navigation.getBoundingClientRect().height}px`);
      updateNavigationSurface();
    };
    updateNavigationSpace();
    const resize = new ResizeObserver(updateNavigationSpace);
    resize.observe(navigation, { box: 'border-box' });
    viewport.addEventListener('scroll', updateNavigationSurface, { passive: true });
    return () => {
      resize.disconnect();
      viewport.removeEventListener('scroll', updateNavigationSurface);
    };
  }, []);

  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'instant' });
  }, []);

  useEffect(() => {
    const previousTitle = document.title;
    const metadata = [
      ['name', 'description', 'Backend с личным наставником: индивидуальные занятия, практика на knowi и персональный разбор кода.'],
      ['property', 'og:title', 'Backend с личным наставником | knowi'],
      ['property', 'og:description', 'Начните с нуля или развивайте навыки с личным наставником.'],
    ] as const;
    const originalMetadata = metadata.map(([attribute, name]) => {
      const selector = `meta[${attribute}="${name}"]`;
      const element = document.head.querySelector<HTMLMetaElement>(selector);
      return { selector, element, content: element?.content ?? null };
    });

    document.title = 'Backend с личным наставником | knowi';
    metadata.forEach(([attribute, name, content], index) => {
      const current = originalMetadata[index].element ?? document.createElement('meta');
      current.setAttribute(attribute, name);
      current.content = content;
      if (!current.isConnected) document.head.append(current);
    });

    return () => {
      document.title = previousTitle;
      originalMetadata.forEach(({ selector, element, content }) => {
        const current = document.head.querySelector<HTMLMetaElement>(selector);
        if (!element) {
          current?.remove();
        } else if (current) {
          current.content = content ?? '';
        }
      });
    };
  }, []);

  useEffect(() => {
    const root = viewportRef.current;
    if (!root) return;

    const targets = root.querySelectorAll<HTMLElement>('[data-reveal]');
    const revealAll = () => targets.forEach((target) => target.classList.add('landing-reveal--visible'));
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) {
      revealAll();
      return;
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('landing-reveal--visible');
          observer.unobserve(entry.target);
        }
      });
    }, { root, rootMargin: '0px 0px -36px 0px', threshold: 0.08 });

    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, []);


  useEffect(() => {
    const root = viewportRef.current;
    if (!root) return;
    const desktop = window.matchMedia('(min-width: 901px) and (min-height: 701px) and (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)');
    const sections = Array.from(root.querySelectorAll<HTMLElement>('.backend-landing__inner > :is(section, .landing-ending)'));
    let lastWheel = 0;
    let lastDirection = 0;
    const onWheel = (event: WheelEvent) => {
      if (root.querySelector('dialog[open]')) return;
      if (!desktop.matches || event.ctrlKey || event.shiftKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY) || !event.deltaY) return;
      if (event.deltaMode === 0 && Math.abs(event.deltaY) < 80) return;
      const direction = Math.sign(event.deltaY);
      let element = event.target instanceof HTMLElement ? event.target : null;
      while (element && element !== root) {
        if (element.scrollHeight > element.clientHeight + 1 &&
          (direction < 0 ? element.scrollTop > 0 : element.scrollTop + element.clientHeight < element.scrollHeight - 1) &&
          /(auto|scroll)/.test(window.getComputedStyle(element).overflowY)) return;
        element = element.parentElement;
      }
      const bounds = root.getBoundingClientRect();
      const navigationSpace = Number.parseFloat(window.getComputedStyle(root).scrollPaddingBlockStart);
      const visibleTop = bounds.top + navigationSpace;
      const visibleHeight = root.clientHeight - navigationSpace;
      const center = visibleTop + visibleHeight / 2;
      const index = sections.findIndex((section) => {
        const rect = section.getBoundingClientRect();
        return rect.top <= center && rect.bottom >= center;
      });
      if (index < 0) return;
      const current = sections[index].getBoundingClientRect();
      if (current.height > visibleHeight && (direction > 0 ? current.bottom > bounds.bottom + 8 : current.top < visibleTop - 8)) return;
      const target = sections[index + direction];
      if (!target) return;
      event.preventDefault();
      const now = performance.now();
      if (direction === lastDirection && now - lastWheel < 160) return;
      lastWheel = now;
      lastDirection = direction;
      const rect = target.getBoundingClientRect();
      const top = root.scrollTop + rect.top - visibleTop;
      const destination = direction < 0 ? top + Math.max(0, rect.height - visibleHeight) : top;
      root.scrollTo({ top: Math.max(0, Math.min(root.scrollHeight - root.clientHeight, destination)), behavior: 'smooth' });
    };
    root.addEventListener('wheel', onWheel, { passive: false });
    return () => root.removeEventListener('wheel', onWheel);
  }, []);

  return (
    <main className="backend-landing backend-landing--personal" aria-labelledby="landing-title">
      <div className="landing-navigation" ref={navigationRef}>
        <header className="landing-header">
          <a className="landing-brand" href={landingBaseUrl}>Никита Кирюхин</a>
          <LandingNav landingBaseUrl={landingBaseUrl} />
        </header>
      </div>
      <div className="landing-viewport" ref={viewportRef} role="region" aria-label="Разделы лендинга" tabIndex={0}>
      <div className="backend-landing__inner">
        <LandingBackdrop />

        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="hero-main">
            <div className="hero-main__intro">
              <h1 id="landing-title">Backend с ментором<span>Вместе к первой работе в IT</span></h1>
              <p className="hero-main__lead">Начнём с вашего уровня. Вместе напишем код на Python и соберём проект для портфолио.</p>
            </div>
            <button className="primary-action landing-action" type="button" data-consultation-trigger>
              Бесплатная консультация <ArrowRight size={17} aria-hidden="true" />
            </button>
            <nav className="hero-features" aria-labelledby="hero-features-title">
              <h2 id="hero-features-title">Почему выбирают меня</h2>
              <ul>
                <li><a href="#lessons">
                  <strong>1:1 <ArrowDownRight size={14} aria-hidden="true" /></strong>
                  <span>Занимаемся один на один в вашем темпе.</span>
                </a></li>
                <li><a href="#platform">
                  <strong>knowi <ArrowDownRight size={14} aria-hidden="true" /></strong>
                  <span>Изучаете теорию и решаете задачи на Python.</span>
                </a></li>
                <li><a href="#demo">
                  <strong>Code review <ArrowDownRight size={14} aria-hidden="true" /></strong>
                  <span>Разбираю ваш код и помогаю исправить ошибки.</span>
                </a></li>
              </ul>
            </nav>
          </div>
          <aside className="hero-mentor" aria-label="Ваш ментор Никита Кирюхин">
            <div className="hero-mentor__profile">
              <img src={mentorPhoto} alt="Никита Кирюхин, backend-ментор" />
              <div><p className="hero-mentor__label">Ваш личный ментор</p><h2>Никита Кирюхин</h2><a className="hero-mentor__more" href="#about">Подробнее обо мне <span aria-hidden="true">↗</span></a></div>
            </div>
            <div className="hero-mentor__experience">
              <ul>
                <li onPointerMove={trackMetricLight}><strong>30+</strong><span>учеников уже работают backend-разработчиками</span></li>
                <li onPointerMove={trackMetricLight}><strong>180 000 ₽</strong><span>средний оффер в месяц после менторства</span></li>
                <li onPointerMove={trackMetricLight}><strong>100%</strong><span>учеников получили заметный результат и ясный следующий шаг</span></li>
              </ul>
            </div>
          </aside>
        </section>

        <section className="landing-section landing-path landing-reveal" id="path" aria-labelledby="path-title" data-reveal>
          <div className="landing-path__intro">
            <h2 id="path-title">Начать можно с простого <span>разговора</span></h2>
            <p className="landing-path__lead">Оставьте заявку — я свяжусь с вами, чтобы обсудить ваши цели и договориться о бесплатной консультации.</p>
            <ConsultationForm courseId={courseId} landingBaseUrl={landingBaseUrl} />
          </div>
          <div className="landing-path__invitation">
            <div className="landing-path__invitation-heading">
              <div><h3>Первая<br />встреча</h3><p>Подготовка не нужна. Просто расскажите, что хотите изучить.</p></div>
              <div className="landing-path__price">
                <strong>0<span>₽</span></strong>
              </div>
            </div>
            <ol className="landing-path__journey">
              <li><span className="landing-path__marker landing-path__marker--connected"><span>01 · СОЗВОН</span><span className="landing-path__flow" aria-hidden="true" /></span><div><strong>Обсудим ваши цели</strong><p>Познакомимся, разберём ваш опыт и определим первый шаг.</p></div></li>
              <li><span className="landing-path__marker">02 · ДОСТУП</span><div><strong>Посмотрите knowi</strong><p>Открою бесплатный доступ на несколько дней. Вы изучите платформу и решите, подходит ли формат.</p></div></li>
            </ol>
            <div className="landing-path__optional"><a className="landing-inline-link" href="#start">«Старт в Python» для новичков <span aria-hidden="true">↓</span></a></div>
          </div>
        </section>

        <section className="landing-section mentoring-choice landing-reveal" id="mentoring" aria-labelledby="mentoring-title" data-reveal>
          <div className="mentoring-choice__side">
            <h2 className="mentoring-choice__heading" id="mentoring-title">Ваш путь в&nbsp;backend <span>с&nbsp;наставником</span></h2>
            <p className="mentoring-choice__audience-label">Кому подходит</p>
            <MentoringAudiences />
          </div>
          <MentoringComparison />
        </section>

        <ReviewsSection />

        <LearningSystem learningUrl={learningUrl} />

        <CareerLaunch />

        <AboutSection />

        <section className="landing-section landing-offer" id="start" aria-labelledby="offer-title">
          <div className="landing-offer__panel">
            <div className="landing-offer__heading">
              <p className="landing-offer__label">Пробный формат для новичков</p>
              <h2 id="offer-title">Старт <span>в Python</span></h2>
              <p className="landing-offer__lead">Попробуйте учиться с наставником на первых задачах по Python. За месяц вы поймёте, подходит ли вам такой формат.</p>
            </div>
            <div className="landing-offer__purchase">
              <p className="landing-offer__label">Пробный месяц</p>
              <p className="landing-offer__price"><strong>4990<span>₽</span></strong><del>10&nbsp;000₽</del></p>
              <ul className="landing-offer__features" aria-label="Что входит в стоимость">
                <li><Video size={18} aria-hidden="true" /><span><strong>4 встречи</strong> с наставником по 60 минут</span></li>
                <li><Code2 size={18} aria-hidden="true" /><span><strong>4 код-ревью</strong> ваших решений</span></li>
                <li><BookOpen size={18} aria-hidden="true" /><span><strong>30 дней</strong> доступа к knowi</span></li>
              </ul>
              <button className="landing-action landing-offer__action" type="button" data-consultation-trigger>Обсудить старт <span aria-hidden="true">↗</span></button>
              <p className="landing-offer__note">Первая консультация бесплатна.</p>
            </div>
            <StartOfferPreview />
          </div>
        </section>

        <PricingSection />

        <div className="landing-ending">
          <LandingFaq learningUrl={learningUrl} />
          <footer className="landing-footer"><a className="landing-brand" href={learningUrl} aria-label="knowi — платформа"><BrandLogo /></a><a href={`${landingBaseUrl}/privacy-policy`}>Политика обработки данных</a><a href={`${landingBaseUrl}/personal-data`}>Согласие на обработку данных</a><span>ИП КИРЮХИН Н.А. ИНН 230109261735</span></footer>
        </div>
      </div>
      </div>
    </main>
  );
}
