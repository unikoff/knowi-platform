import { BrandLogo } from "@platform/shared/brand/BrandLogo";
import { useEffect, useRef, useState, type PointerEvent } from 'react';
import { ArrowUpRight, BookOpen, Compass, FolderCode, MessageCircle, Target, X } from 'lucide-react';
import { ConsultationForm } from './components/ConsultationForm';
import dmitriyPhoto from './assets/Dmitriy.jpg';
import varvaraPhoto from './assets/Varvara.jpg';
import zaharPhoto from './assets/Zahar.jpg';
import './styles/landing.css';
import './styles/landing-pages.css';
import './styles/pricing.css';


const plans = [
  {
    sessions: 10, reviewCount: null, label: 'Главный формат', price: 19900,
    items: [
      { icon: Compass, title: 'Личный план обучения', description: 'Поймёте, с чего начать и к чему двигаться дальше.' },
      { icon: FolderCode, title: 'Проект шаг за шагом', description: 'Вместе разбираем каждый этап создания вашего проекта.' },
      { icon: MessageCircle, title: 'Приоритетная поддержка', description: 'Пишите в Telegram в любое время. Ваши вопросы в приоритете.' },
      { icon: BookOpen, title: 'Все темы по порядку', description: 'Последовательно разбираем каждую тему на понятных примерах.' },
    ],
  },
  {
    sessions: 4, reviewCount: 8, label: 'Стабильный ритм', price: 9900,
    items: [
      { icon: Target, title: 'Ключевые затруднения', description: 'На встречах сосредоточимся на самых важных вопросах.' },
      { icon: FolderCode, title: 'Работа над проектом', description: 'Обсудим ключевые трудности, которые возникают в проекте.' },
      { icon: Compass, title: 'План практики', description: 'Темы и задачи для самостоятельной работы между встречами.' },
    ],
  },
];

const maxSessions = Math.max(...plans.map(plan => plan.sessions));
const unlimitedReviewsLabel = 'Без лимита';
const comparisonDetails = [
  ['Разбор кода', 'Разбор каждого задания', 'Разбор важных решений'],
  ['План и практика', 'Личный маршрут под вашу цель', 'План самостоятельной практики'],
  ['Темы', 'Последовательно разбираем каждую тему', 'Сосредоточимся на ключевых затруднениях'],
  ['Работа над проектом', 'Разбираем проект на каждом этапе', 'Обсуждаем ключевые трудности'],
  ['Поддержка', 'Пишите в Telegram в любое время. Ваши вопросы в приоритете', 'Не указана отдельно'],
];

export function LandingNav({ landingBaseUrl }: { landingBaseUrl: string }) {
  return <>
    <nav className="landing-header__nav" aria-label="Навигация по лендингу">
      <a href={`${landingBaseUrl}#reviews`}>Отзывы</a><a href={`${landingBaseUrl}#platform`}>Программа</a><a href={`${landingBaseUrl}#about`}>Обо мне</a><a href={`${landingBaseUrl}#pricing`}>Тарифы</a>
    </nav>
    <button className="landing-header__action" type="button" data-consultation-trigger>Оставить заявку</button>
  </>;
}

function MetricTicks({ count, total = count }: { count: number; total?: number }) {
  return <div className="landing-plan__ticks" aria-hidden="true">
    {Array.from({ length: total }, (_, tick) => <span className={tick < count ? 'is-filled' : ''} style={{ animationDelay: `${tick * 120}ms` }} key={tick} />)}
  </div>;
}

function movePlanLight(event: PointerEvent<HTMLElement>) {
  if (event.pointerType === 'touch') return;
  const bounds = event.currentTarget.getBoundingClientRect();
  event.currentTarget.style.setProperty('--plan-light-x', `${event.clientX - bounds.left}px`);
  event.currentTarget.style.setProperty('--plan-light-y', `${event.clientY - bounds.top}px`);
}

const formatPrice = (price: number) => price.toLocaleString('ru-RU');

export function PricingSection() {
  const compareRef = useRef<HTMLDialogElement>(null);
  const compareTriggerRef = useRef<HTMLButtonElement>(null);
  const compareContentRef = useRef<HTMLDivElement>(null);

  function openComparison() {
    const dialog = compareRef.current;
    if (!dialog || dialog.open) return;

    dialog.showModal();
    if (compareContentRef.current) compareContentRef.current.scrollTop = 0;
  }

  return <section className="landing-section landing-pricing" id="pricing" aria-label="Тарифы">
    <div className="landing-pricing__grid">
      {plans.map((plan, index) => <article className={`landing-plan${index === 0 ? ' landing-plan--featured' : ''}`} key={plan.label} onPointerMove={movePlanLight}>
        <header className="landing-plan__header">
          <h3>{plan.label}</h3>
          <div className="landing-plan__cost">
            <p className="landing-plan__price">{formatPrice(plan.price)}<span>₽</span></p>
            <p className="landing-plan__term">в месяц</p>
          </div>
        </header>
        <div className="landing-plan__body">
          <div className="landing-plan__metrics" aria-label="В месяц входит">
            <div>
              <div className="landing-plan__metric-heading">
                <span>Индивидуальные занятия</span>
                <strong>{plan.sessions} <small>/ месяц</small></strong>
              </div>
              <MetricTicks count={plan.sessions} total={maxSessions} />
            </div>
            <div>
              <div className="landing-plan__metric-heading">
                <span>Код-ревью</span>
                {plan.reviewCount === null
                  ? <strong className="landing-plan__unlimited">{unlimitedReviewsLabel}</strong>
                  : <strong>{plan.reviewCount} <small>/ месяц</small></strong>}
              </div>
              {plan.reviewCount === null
                ? <div className="landing-plan__ticks" aria-hidden="true"><span className="landing-plan__unlimited-bar" /></div>
                : <MetricTicks count={plan.reviewCount} />}
            </div>
          </div>
          <ul className="landing-plan__benefits">
            {plan.items.map(item => <li key={item.title}>
              <item.icon aria-hidden="true" size={18} strokeWidth={1.7} />
              <div>
                <strong>{item.title}</strong>
                <span>{item.description}</span>
              </div>
            </li>)}
          </ul>
          <button className="landing-plan__action" type="button" data-consultation-trigger>
            {index === 0 ? 'Обсудить главный формат' : 'Обсудить стабильный ритм'}
            <ArrowUpRight className="landing-plan__action-arrow" aria-hidden="true" size={18} strokeWidth={2} />
          </button>
        </div>
      </article>)}
    </div>
    <button className="landing-pricing__compare-trigger" ref={compareTriggerRef} type="button" onClick={openComparison} aria-haspopup="dialog">Сравнить форматы <span aria-hidden="true">↗</span></button>
    <dialog
      className="pricing-dialog"
      ref={compareRef}
      aria-labelledby="pricing-dialog-title"
      onClose={() => compareTriggerRef.current?.focus({ preventScroll: true })}
      onClick={event => { if (event.target === event.currentTarget) event.currentTarget.close(); }}
    >
      <header className="pricing-dialog__header">
        <h3 id="pricing-dialog-title">Сравните форматы</h3>
        <button className="pricing-dialog__close" type="button" autoFocus onClick={() => compareRef.current?.close()} aria-label="Закрыть сравнение">
          <X aria-hidden="true" size={20} />
        </button>
      </header>
      <div className="pricing-dialog__content" ref={compareContentRef} role="region" aria-label="Условия тарифов" tabIndex={0}>
        <table role="table" aria-label="Сравнение тарифов">
          <thead role="rowgroup">
            <tr role="row">
              <td className="pricing-dialog__condition" aria-hidden="true" />
              {plans.map((plan, index) => <th scope="col" role="columnheader" key={plan.label}>
                <div className={`pricing-dialog__plan${index === 0 ? ' pricing-dialog__plan--featured' : ''}`}>
                  <span>{plan.label}</span>
                  <div className="pricing-dialog__price">
                    <strong>{formatPrice(plan.price)}<span className="pricing-dialog__currency"> ₽</span></strong>
                    <small>в месяц</small>
                  </div>
                </div>
              </th>)}
            </tr>
          </thead>
          <tbody className="pricing-dialog__metrics" role="rowgroup">
            <tr className="pricing-dialog__group" role="row">
              <th scope="rowgroup" colSpan={3} role="rowheader">В месяц</th>
            </tr>
            <tr role="row">
              <th scope="row" role="rowheader">Индивидуальные занятия</th>
              {plans.map(plan => <td role="cell" key={plan.label}>
                <div className="pricing-dialog__metric"><strong>{plan.sessions}</strong><MetricTicks count={plan.sessions} total={maxSessions} /></div>
              </td>)}
            </tr>
            <tr role="row">
              <th scope="row" role="rowheader">Код-ревью</th>
              {plans.map(plan => <td role="cell" key={plan.label}>
                <div className="pricing-dialog__metric">
                  {plan.reviewCount === null
                    ? <>
                        <strong className="pricing-dialog__unlimited">{unlimitedReviewsLabel}</strong>
                        <div className="landing-plan__ticks" aria-hidden="true"><span className="landing-plan__unlimited-bar" /></div>
                      </>
                    : <><strong>{plan.reviewCount}</strong><MetricTicks count={plan.reviewCount} /></>}
                </div>
              </td>)}
            </tr>
          </tbody>
          <tbody role="rowgroup">
            <tr className="pricing-dialog__group" role="row">
              <th scope="rowgroup" colSpan={3} role="rowheader">Обучение и поддержка</th>
            </tr>
            {comparisonDetails.map(([label, ...values]) => <tr role="row" key={label}>
              <th scope="row" role="rowheader">{label}</th>
              {values.map((value, index) => <td role="cell" key={plans[index].label}>{value}</td>)}
            </tr>)}
          </tbody>
          <tfoot role="rowgroup">
            <tr role="row">
              <th scope="row" role="rowheader">Платформа</th>
              <td colSpan={2} role="cell">Доступ на месяц в обоих форматах</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </dialog>
  </section>;
}

const reviews = [
  { name: 'Дмитрий Вострецов', handle: 'dimkalu46i', role: 'Backend-разработчик в Ozon', photo: dmitriyPhoto, context: 'Для тех, у кого уже есть база', mentorStory: ['Дмитрий пришёл ко мне с хорошей базой после ИТМО. Он хотел понять, что мешает проходить на более сильные вакансии.', 'Мы начали с разбора его знаний, чтобы не тратить время на знакомые темы. Затем работали над Python, архитектурой и тем, как объяснять решения на собеседовании.'], caseOutcome: 'Через три месяца подготовки Дмитрий получил оффер в Ozon.', text: 'Сначала мы просто разобрали, что я уже умею, а где начинаю плавать. Заново проходить знакомые темы не пришлось. Больше всего времени ушло на Python, архитектурные вопросы и на то, чтобы нормально объяснять свои решения, а не только писать код.', result: 'Подготовка: 3 месяца · Тренировочных интервью: 5', paragraphs: ['После ИТМО я, если честно, думал, что хорошей базы уже должно хватить. Но на практике я работал на позиции, которая мне не нравилась, получал около 100 000 ₽ и не понимал, почему на более сильные вакансии всё ещё не прохожу.', 'Сначала мы просто разобрали, что я уже умею, а где начинаю плавать. Заново проходить знакомые темы не пришлось. Больше всего времени ушло на Python, архитектурные вопросы и на то, чтобы нормально объяснять свои решения, а не только писать код. Тренировочные интервью хорошо показали, где я пока не готов и что ещё нужно подтянуть.', 'После подготовки я получил оффер в Ozon на 250 000 ₽. До сих пор работаю в компании, а сейчас получаю уже больше 300 000 ₽.'] },
  { name: 'Варвара Николаева', handle: 'varznik', role: 'Из поддержки в Python-разработку', photo: varvaraPhoto, context: 'Для тех, кто учится самостоятельно', mentorStory: ['Варвара пришла ко мне после самостоятельного обучения. Она изучала курсы и статьи, но ей не хватало уверенности, когда нужно было написать код самой.', 'Мы сосредоточились на практике: Варвара приносила решения, получала замечания и дорабатывала код. Постепенно она собрала свой API и подготовилась к техническим интервью.'], caseOutcome: 'За пять месяцев Варвара получила первую работу Python-разработчиком.', text: 'Я приносила код, получала замечания и переделывала решение. Иногда хотелось оставить «и так работает», но именно на таких доработках я наконец начала понимать, почему одно решение нормальное, а другое развалится при первой же правке.', result: 'Подготовка: 5 месяцев · Код-ревью каждую неделю', paragraphs: ['У меня долго было ощущение, что я всё время учусь, но разработчиком так и не становлюсь. Я работала в поддержке, проходила курсы, сохраняла статьи, делала конспекты, а когда нужно было написать что-то самой, уверенности всё равно не было.', 'Здесь уже не получалось просто посмотреть урок и поставить галочку. Я приносила код, получала замечания и переделывала решение. Иногда хотелось оставить «и так работает», но именно на таких доработках я наконец начала понимать, почему одно решение нормальное, а другое развалится при первой же правке.', 'За пять месяцев я собрала API, подготовилась к техническим интервью и получила первую работу Python-разработчиком с зарплатой 170 000 ₽. Для меня самым важным было то, что я наконец перестала считать себя человеком, который только учится.'] },
  { name: 'Захар Казначеев', handle: 'kaz_zax', role: 'Со склада в удалённый backend', photo: zaharPhoto, context: 'Для тех, кто совмещает работу и учёбу', mentorStory: ['Захар пришёл ко мне, когда работал на складе. Ему было важно учиться, не увольняясь и не подстраивая всю жизнь под занятия.', 'Мы выстроили обучение вокруг его смен: небольшие задачи в рабочие дни, проект в выходные. Если он выпадал из ритма, возвращались к ближайшему понятному шагу.'], caseOutcome: 'Захар сменил работу после оффера и стал junior backend-разработчиком на удалёнке.', text: 'Поэтому график собирали вокруг моей реальной жизни, а не вокруг идеальной недели из расписания. В рабочие дни я делал небольшие задачи, в выходные садился за проект.', result: 'Нагрузка: 15 часов в неделю · Не увольнялся до оффера', paragraphs: ['Я работал на складе и получал около 150 000 ₽. Деньги были нормальные, поэтому вариант «увольняйся и полгода только учись» мне вообще не подходил. После смены сил часто оставалось максимум на короткое занятие, а иногда не оставалось совсем.', 'Поэтому график собирали вокруг моей реальной жизни, а не вокруг идеальной недели из расписания. В рабочие дни я делал небольшие задачи, в выходные садился за проект. Если выпадал, не пытался за один вечер наверстать всё сразу, возвращался к ближайшей понятной точке и продолжал.', 'Со склада я ушёл только после оффера. Устроился junior backend-разработчиком на удалёнку с зарплатой 160 000 ₽. По деньгам рост был не огромным, зато исчезли дорога и физические смены, а главное, появилась профессия, в которой я вижу, куда расти дальше.'] },
];

function StoryPhoto({ src, alt = '', lazy = false }: { src: string; alt?: string; lazy?: boolean }) {
  const [loaded, setLoaded] = useState(false);
  return <img src={src} alt={alt} loading={lazy ? 'lazy' : 'eager'} className={`story-photo${loaded ? ' story-photo--loaded' : ''}`} onLoad={() => setLoaded(true)} onError={() => setLoaded(false)} />;
}

export function ReviewsSection() {
  const [active, setActive] = useState<number | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (active !== null && dialogRef.current && !dialogRef.current.open) dialogRef.current.showModal();
  }, [active]);
  const review = active === null ? null : reviews[active];
  return <section className="landing-section landing-stories" id="reviews" aria-labelledby="stories-title">
    <div className="landing-stories__heading"><h2 id="stories-title">Три показательных кейса</h2></div>
    <div className="story-portraits">{reviews.map((item, index) => <button type="button" className="story-portrait" key={item.handle} onClick={() => setActive(index)} aria-label={`Прочитать историю: ${item.name}`}>
      <StoryPhoto src={item.photo} lazy /><span className="story-portrait__shade" aria-hidden="true" />
      <span className="story-portrait__info"><span className="story-portrait__context">{item.context}</span><strong>{item.name}</strong><span className="story-portrait__role">{item.role}</span><span className="story-portrait__teaser">«{item.text}»</span><span className="story-portrait__action">Открыть историю <span aria-hidden="true">↗</span></span></span>
    </button>)}</div>
    <dialog className="story-dialog" ref={dialogRef} onClose={() => setActive(null)} aria-labelledby="story-dialog-title" onClick={(event) => { if (event.target === event.currentTarget) event.currentTarget.close(); }}>
      {review && <><button className="story-dialog__close" type="button" autoFocus onClick={() => dialogRef.current?.close()} aria-label="Закрыть историю"><svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button><div className="story-dialog__layout"><div className="story-dialog__photo"><img src={review.photo} alt={`Портрет: ${review.name}`} /><div><span>История ученика</span><strong>{review.name}</strong></div></div><div className="story-dialog__content"><p className="landing-overline">{review.context}</p><h3 id="story-dialog-title">{review.role}</h3><div className="story-dialog__author"><strong>{review.name}</strong><a href={`https://t.me/${review.handle}`} target="_blank" rel="noopener noreferrer">@{review.handle} ↗</a></div><section className="story-dialog__guide" aria-label="О чём этот кейс"><p className="case-path__eyebrow">Как проходило обучение</p><div className="case-path__story">{review.mentorStory.map(paragraph => <p key={paragraph}>{paragraph}</p>)}</div><div className="case-path__outcome"><p>{review.caseOutcome}</p></div></section><div className="story-dialog__text"><h4>История ученика</h4>{review.paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}</div><p className="story-dialog__result">{review.result}</p></div></div></>}
    </dialog>
  </section>;
}

type Page = 'reviews' | 'platform' | 'pricing';
const titles: Record<Page, string> = { reviews: 'Истории учеников', platform: 'О платформе', pricing: 'Тарифы' };

export function LandingDetailPage({ page, courseId, landingBaseUrl, learningUrl }: {
  page: Page; courseId: string; landingBaseUrl: string; learningUrl: string;
}) {
  useEffect(() => { const previous = document.title; document.title = `${titles[page]} | knowi`; return () => { document.title = previous; }; }, [page]);
  return <main className="backend-landing" aria-labelledby="detail-title"><div className="backend-landing__inner">
    <div className="landing-header-layer"><header className="landing-header"><a className="landing-brand" href={landingBaseUrl}>Никита Кирюхин</a><LandingNav landingBaseUrl={landingBaseUrl} /></header></div><ConsultationForm showActions={false} courseId={courseId} landingBaseUrl={landingBaseUrl} />
    <div className="landing-detail"><a className="landing-detail__back" href={landingBaseUrl}>← На главную</a>
      {page === 'reviews' && <><div className="landing-detail__intro"><p className="landing-overline">Истории учеников</p><h1 id="detail-title">Разные точки старта — свои пути в backend</h1><p>Истории учеников о практике, обратной связи и движении к своим целям.</p></div><div className="landing-reviews">{reviews.map(review => <article className="landing-review" key={review.name}><img src={review.photo} alt="" loading="lazy" /><div><p className="landing-overline">{review.context}</p><h2>{review.role}</h2><div className="landing-review__context"><p className="case-path__eyebrow">Как проходило обучение</p><div className="case-path__story">{review.mentorStory.map(paragraph => <p key={paragraph}>{paragraph}</p>)}</div><div className="case-path__outcome"><p>{review.caseOutcome}</p></div></div><blockquote>{review.paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}</blockquote><p>{review.result}</p><strong>{review.name}</strong> · <a href={`https://t.me/${review.handle}`} target="_blank" rel="noopener noreferrer">@{review.handle} ↗</a></div></article>)}</div></>}
      {page === 'platform' && <><div className="landing-detail__intro"><p className="landing-overline">Учебная платформа</p><h1 id="detail-title">knowi: теория, практика и обратная связь в одном месте</h1><p>Здесь удобно учиться в своём темпе, возвращаться к материалам и видеть следующий шаг.</p></div><section className="landing-detail__block landing-platform-detail"><div><p className="landing-overline">Знакомство с платформой</p><h2>Посмотрите, как устроено обучение</h2><p>Здесь появится видео с обзором платформы и рабочего пространства ученика.</p><a className="primary-action landing-action" href={learningUrl}>Открыть платформу <span aria-hidden="true">↗</span></a></div><div className="landing-video-placeholder" aria-label="Место для видеообзора платформы"><div className="landing-video-placeholder__bar" aria-hidden="true"><span /><span /><span /><small>knowi / видеообзор</small></div><div className="landing-video-placeholder__content"><span className="landing-video-placeholder__play" aria-hidden="true">▶</span><strong>Знакомство с knowi</strong><small>Здесь будет видео о платформе</small></div><div className="landing-video-placeholder__timeline" aria-hidden="true"><span /></div></div></section><section className="landing-detail__block"><h2>Что внутри</h2><div className="landing-detail__columns"><p><strong>Теория и практика.</strong> Материалы и задания собраны в одном учебном пространстве.</p><p><strong>Разбор решений.</strong> Код-ревью помогает увидеть ошибки и понять, как двигаться дальше.</p><p><strong>Прогресс.</strong> Можно вернуться к пройденным материалам и продолжить обучение.</p></div></section></>}
      {page === 'pricing' && <><div className="landing-detail__intro"><p className="landing-overline">Тарифы</p><h1 id="detail-title">Формат под вашу цель</h1><p>Выберите подходящий ритм и обсудите детали на бесплатной консультации.</p></div><PricingSection /></>}
    </div><footer className="landing-footer"><a className="landing-brand" href={learningUrl} aria-label="knowi — платформа"><BrandLogo /></a><a href={`${landingBaseUrl}/privacy-policy`}>Политика обработки данных</a><a href={`${landingBaseUrl}/personal-data`}>Согласие на обработку данных</a><span>ИП КИРЮХИН Н.А. ИНН 230109261735</span></footer>
  </div></main>;
}
