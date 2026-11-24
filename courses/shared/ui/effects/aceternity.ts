import type { PointerEvent } from 'react';
import './aceternity.css';

export function trackCardSpotlight(event: PointerEvent<HTMLElement>) {
  if (event.pointerType === 'touch') return;

  const card = event.currentTarget;
  const { left, top } = card.getBoundingClientRect();
  card.style.setProperty('--card-spotlight-position', `${event.clientX - left}px ${event.clientY - top}px`);
}
