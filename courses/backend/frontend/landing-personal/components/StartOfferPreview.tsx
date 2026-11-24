import { useId, useRef, useState, type KeyboardEvent } from 'react';
import { ClipboardList, MessageSquare, Terminal } from 'lucide-react';

const steps = [
  {
    label: 'План',
    icon: ClipboardList,
    title: 'Выберите, с чего начать',
    description: 'На встрече обсудите с наставником свои цели и время на учёбу. Вместе подберёте первые темы и задачи.',
  },
  {
    label: 'Практика',
    icon: Terminal,
    title: 'Попробуйте написать код',
    description: 'Разберите пример на knowi, измените его и запустите код в браузере. Затем примените то, что поняли, в задаче.',
  },
  {
    label: 'Код-ревью',
    icon: MessageSquare,
    title: 'Разберите своё решение',
    description: 'Наставник покажет, где возникла ошибка или как упростить код. Вы сможете применить замечания на практике.',
  },
];

export function StartOfferPreview() {
  const id = useId();
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const [active, setActive] = useState(0);

  const moveFocus = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    if (event.key === 'ArrowRight') next = (index + 1) % steps.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + steps.length) % steps.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = steps.length - 1;
    else return;

    event.preventDefault();
    setActive(next);
    tabs.current[next]?.focus({ preventScroll: true });
  };

  return (
    <div className="landing-offer__preview">
      <h3 id={`${id}-title`}>Как проходит обучение</h3>
      <div className="landing-offer__tabs" role="tablist" aria-labelledby={`${id}-title`}>
        {steps.map(({ label, icon: Icon }, index) => (
          <button
            className="landing-offer__tab"
            key={label}
            ref={element => { tabs.current[index] = element; }}
            type="button"
            role="tab"
            id={`${id}-tab-${index}`}
            aria-selected={active === index}
            aria-controls={`${id}-panel-${index}`}
            tabIndex={active === index ? 0 : -1}
            onClick={() => setActive(index)}
            onKeyDown={event => moveFocus(event, index)}
          >
            <Icon size={18} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </div>
      {steps.map(({ label, title, description }, index) => (
        <div
          className="landing-offer__step"
          key={label}
          role="tabpanel"
          id={`${id}-panel-${index}`}
          aria-labelledby={`${id}-tab-${index}`}
          tabIndex={active === index ? 0 : -1}
          hidden={active !== index}
        >
          <p><strong>{title}</strong></p>
          <p>{description}</p>
        </div>
      ))}
    </div>
  );
}
