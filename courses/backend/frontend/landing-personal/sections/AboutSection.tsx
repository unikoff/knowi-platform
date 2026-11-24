import { ArrowUpRight, CodeXml, MessagesSquare } from 'lucide-react';
import portrait from '../assets/mentor-portrait.png';
import '../styles/about-section.css';

export function AboutSection() {
  return <section className="mentor-about" id="about" aria-labelledby="mentor-about-title">
    <header className="mentor-about__intro">
      <p className="mentor-about__role">Backend-разработчик и наставник</p>
      <h2 id="mentor-about-title">Никита Кирюхин</h2>
      <p className="mentor-about__bio">Помогаю освоить backend на практике. На занятиях мы разбираем ваш код, а новые темы вы сразу применяете в своём проекте.</p>
    </header>

    <div className="mentor-about__portrait">
      <img src={portrait} alt="Никита Кирюхин" width={1086} height={1448} loading="lazy" />
    </div>

    <div className="mentor-about__details">
      <ul className="mentor-about__stats">
        <li><strong>5 лет</strong><span>в разработке</span></li>
        <li><strong>CEO</strong><span>опыт руководства</span></li>
        <li><strong>Стартап</strong><span>собственный проект</span></li>
      </ul>
      <div className="mentor-about__experience">
        <div>
          <h3><CodeXml size={18} aria-hidden="true" /> Коммерческая разработка</h3>
          <p>Работал над коммерческими продуктами. В проектах использую Python, FastAPI, PostgreSQL, Redis и Docker.</p>
        </div>
        <div>
          <h3><MessagesSquare size={18} aria-hidden="true" /> Преподавание и менторство</h3>
          <p>Преподавал в онлайн-школах. Объясняю сложные темы на примерах и подбираю задачи под вашу цель.</p>
        </div>
      </div>
      <button className="primary-action landing-action mentor-about__action" type="button" data-consultation-trigger>
        Обсудить обучение <ArrowUpRight size={17} aria-hidden="true" />
      </button>
    </div>
  </section>;
}
