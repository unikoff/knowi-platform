import React, { useId, useRef, useState } from "react";
import { ExternalLink, ImageOff, Maximize2, Minimize2, X } from "lucide-react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";

export type LessonScreenshotItem = {
  src: string;
  alt: string;
  title: string;
  description: React.ReactNode;
  sourceUrl: string;
  sourceLabel?: string;
};

export function LessonScreenshots({ title, intro, images, footer }: {
  title: string;
  intro?: React.ReactNode;
  images: readonly LessonScreenshotItem[];
  footer?: React.ReactNode;
}) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const [active, setActive] = useState(0);
  const [zoomed, setZoomed] = useState(false);
  const [failed, setFailed] = useState<Set<string>>(() => new Set());
  const index = Math.min(active, images.length - 1);
  const image = images[index];
  if (!image) return null;

  function openImage() {
    setZoomed(false);
    dialog.current?.showModal();
  }

  return (
    <section className="lesson-screenshots" aria-labelledby={`${id}-title`}>
      <header className="lesson-screenshots-heading">
        <VisualHeading kind="screenshots" />
        <h3 id={`${id}-title`}>{title}</h3>
        {intro && <p>{intro}</p>}
      </header>
      <div className={`lesson-screenshots-body${images.length > 1 ? " has-steps" : ""}`}>
        {images.length > 1 && (
          <div className="lesson-screenshots-nav" role="group" aria-label="Шаги на скриншотах">
            {images.map((item, itemIndex) => (
              <button key={item.src} type="button" aria-pressed={index === itemIndex} onClick={() => setActive(itemIndex)}>
                <span className="lesson-screenshots-number">{String(itemIndex + 1).padStart(2, "0")}</span>
                <span>{item.title}</span>
              </button>
            ))}
          </div>
        )}
        <figure className="lesson-screenshot">
          {failed.has(image.src) ? (
            <div className="lesson-screenshot-unavailable" role="status">
              <ImageOff size={24} aria-hidden="true" />
              <p>Не удалось загрузить скриншот.</p>
              <a href={image.sourceUrl} target="_blank" rel="noreferrer">Открыть инструкцию</a>
            </div>
          ) : (
            <button className="lesson-screenshot-preview" type="button" onClick={openImage} aria-label={`Рассмотреть: ${image.title}`}>
              <img key={image.src} src={image.src} alt={image.alt} loading="lazy" decoding="async" onError={() => setFailed(previous => new Set(previous).add(image.src))} />
              <span className="lesson-screenshot-expand"><Maximize2 size={14} aria-hidden="true" /> Рассмотреть</span>
            </button>
          )}
          <figcaption>
            <div className="lesson-screenshot-caption" aria-live="polite">{image.description}</div>
            <a className="lesson-screenshot-source" href={image.sourceUrl} target="_blank" rel="noreferrer">
              {image.sourceLabel ?? "Источник: GitHub Docs"}<ExternalLink size={12} aria-hidden="true" />
            </a>
          </figcaption>
        </figure>
      </div>
      {footer && <div className="lesson-screenshots-footer">{footer}</div>}
      <dialog ref={dialog} className="lesson-screenshot-dialog" aria-labelledby={`${id}-dialog-title`} onClose={() => setZoomed(false)} onClick={event => { if (event.target === event.currentTarget) dialog.current?.close(); }}>
        <div className="lesson-screenshot-dialog-heading">
          <h3 id={`${id}-dialog-title`}>{image.title}</h3>
          <button type="button" onClick={() => dialog.current?.close()} aria-label="Закрыть изображение"><X size={20} aria-hidden="true" /></button>
        </div>
        <div className="lesson-screenshot-tools">
          <button type="button" onClick={() => setZoomed(value => !value)} aria-pressed={zoomed}>
            {zoomed ? <Minimize2 size={15} aria-hidden="true" /> : <Maximize2 size={15} aria-hidden="true" />}
            {zoomed ? "Вписать в окно" : "Размер 100%"}
          </button>
          <a href={image.src} target="_blank" rel="noreferrer">Открыть оригинал<ExternalLink size={13} aria-hidden="true" /></a>
        </div>
        <div className={`lesson-screenshot-full${zoomed ? " is-zoomed" : ""}`} tabIndex={zoomed ? 0 : undefined} aria-label="Изображение в полном размере">
          <img src={image.src} alt={image.alt} />
        </div>
      </dialog>
    </section>
  );
}
