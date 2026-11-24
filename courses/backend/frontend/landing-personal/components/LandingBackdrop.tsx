import { useEffect, useRef } from 'react';
import '@course-ui/effects/aceternity.css';

const highlights = [
  { selector: '.hero-main [data-consultation-trigger]', shape: 'hero-action', anchorY: .45 },
  { selector: '#path .landing-path__intro', shape: 'wide', anchorY: .55 },
  { selector: '#mentoring .mentoring-choice__cases', shape: 'wide', anchorY: .44 },
  { selector: '#reviews .story-portraits', shape: 'wide', anchorY: .05 },
  {
    selector: '#learning-system :is(.learning-system__platform-cta, .learning-system__review-cta, .learning-system__lessons-cta)',
    shape: 'action',
    anchorY: .5,
  },
  { selector: '#career .career-card--resume', shape: 'portrait', anchorY: .68 },
  { selector: '#about .mentor-about__portrait img', shape: 'portrait', anchorY: .46 },
  { selector: '#pricing .landing-plan--featured .landing-plan__price', shape: 'portrait', anchorY: .5 },
  { selector: '#faq .faq-redesign__action', shape: 'faq', anchorX: .16, anchorY: .45 },
  { selector: '.hero-mentor__profile img', shape: 'hero-portrait', anchorY: .4 },
];

export function LandingBackdrop() {
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const backdrop = backdropRef.current;
    const content = backdrop?.parentElement;
    if (!backdrop || !content) return;

    const spots = backdrop.querySelectorAll<HTMLElement>('.landing-backdrop__spot');
    const groups = highlights.map(({ selector }) => Array.from(content.querySelectorAll(selector)));
    const panels = content.querySelectorAll('.learning-system__panel');

    const positionSpots = () => {
      const origin = backdrop.getBoundingClientRect();
      const bounds = groups.map(targets => targets.find(target => !target.closest('[hidden]'))?.getBoundingClientRect());

      spots.forEach((spot, index) => {
        const rect = bounds[index];
        if (!rect?.width || !rect.height) {
          spot.hidden = true;
          return;
        }
        spot.style.left = `${rect.left + rect.width * (highlights[index].anchorX ?? .5) - origin.left}px`;
        spot.style.top = `${rect.top + rect.height * highlights[index].anchorY - origin.top}px`;
        spot.hidden = false;
      });
    };

    const resize = new ResizeObserver(positionSpots);
    resize.observe(content);
    Array.from(content.children).forEach(section => resize.observe(section));
    groups.flat().forEach(target => resize.observe(target));

    const switchPanel = new MutationObserver(positionSpots);
    panels.forEach(panel => {
      resize.observe(panel);
      switchPanel.observe(panel, { attributes: true, attributeFilter: ['hidden'] });
      panel.addEventListener('animationend', positionSpots);
    });
    positionSpots();

    return () => {
      resize.disconnect();
      switchPanel.disconnect();
      panels.forEach(panel => panel.removeEventListener('animationend', positionSpots));
    };
  }, []);

  return (
    <div className="landing-backdrop" ref={backdropRef} aria-hidden="true">
      {highlights.map(({ selector, shape }) => (
        <span key={selector} className={`landing-backdrop__spot landing-backdrop__spot--${shape}${shape === 'faq' ? ' ui-ambient-spotlight' : ''}`} hidden />
      ))}
    </div>
  );
}
