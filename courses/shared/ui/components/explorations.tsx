import React, { useEffect, useId, useRef, useState } from "react";
import { plainOptionText } from "./option-text";
import { ArrowRight, Check, Minus, Plus, RotateCcw, X } from "lucide-react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";

function shuffleOnce<T>(items: T[]): T[] {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

type MatchPairsProps = {
  prompt: string;
  pairs: { left: React.ReactNode; right: React.ReactNode }[];
  leftTitle?: string;
  rightTitle?: string;
  explanation?: string;
};

export function MatchPairs(props: MatchPairsProps) {
  // RichLesson can clone pair data when its parent measures the content.
  // Only a different exercise should reset the order and the learner's answers.
  const taskKey = JSON.stringify([plainOptionText(props.prompt), props.pairs.map(pair => [plainOptionText(pair.left), plainOptionText(pair.right)])]);
  return <MatchPairsTask key={taskKey} {...props} />;
}

function MatchPairsTask({
  prompt,
  pairs,
  leftTitle = "Слева",
  rightTitle = "Справа",
  explanation,
}: MatchPairsProps) {
  const id = useId();
  const [rightOrder] = useState(() => shuffleOnce(pairs.map((_pair, id) => id)));
  const [active, setActive] = useState<{ side: "left" | "right"; id: number } | null>(null);
  const [matched, setMatched] = useState<Set<number>>(() => new Set());
  const [miss, setMiss] = useState<{ side: "left" | "right"; id: number } | null>(null);
  const leftRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const resetRef = useRef<HTMLButtonElement>(null);
  const focusAfterMatch = useRef(false);
  const allDone = pairs.length > 0 && matched.size === pairs.length;

  useEffect(() => {
    if (!focusAfterMatch.current) return;
    if (allDone) resetRef.current?.focus();
    else leftRefs.current.find((button, index) => button && !matched.has(index))?.focus();
    focusAfterMatch.current = false;
  }, [matched, allDone]);

  function pick(side: "left" | "right", itemId: number) {
    if (matched.has(itemId)) return;
    if (active === null || active.side === side) {
      setActive(active?.id === itemId && active.side === side ? null : { side, id: itemId });
      setMiss(null);
    } else if (active.id === itemId) {
      focusAfterMatch.current = true;
      setMatched(current => new Set(current).add(itemId));
      setActive(null);
      setMiss(null);
    } else {
      setMiss(current => current?.side === side && current.id === itemId ? current : { side, id: itemId });
    }
  }

  function item(side: "left" | "right", itemId: number, label: React.ReactNode) {
    const isMatched = matched.has(itemId);
    const isActive = active?.side === side && active.id === itemId;
    const isWrong = miss?.side === side && miss.id === itemId;
    return (
      <button
        key={itemId}
        ref={side === "left" ? element => { leftRefs.current[itemId] = element; } : undefined}
        type="button"
        className={`match-item answer-option${isMatched ? " is-matched" : ""}${isActive ? " is-active" : ""}${isWrong ? " is-wrong" : ""}`}
        aria-pressed={isActive || isMatched}
        disabled={isMatched}
        onClick={() => pick(side, itemId)}
      >
        <span className="match-item-label">{plainOptionText(label)}</span>
        <span className="match-mark" aria-hidden="true">
          {isMatched ? <Check size={15} /> : isWrong ? <X size={15} /> : <span className="match-dot" />}
        </span>
      </button>
    );
  }

  return (
    <section className="match-card" aria-labelledby={`${id}-prompt`}>
      <div className="match-header">
        <VisualHeading kind="match" />
        <span className="match-progress" role="status" aria-label={`${matched.size} из ${pairs.length} пар соединено`}>
          {matched.size} / {pairs.length}
        </span>
      </div>
      <h3 className="match-prompt" id={`${id}-prompt`}>{prompt}</h3>
      <div className="match-grid">
        <div className="match-col answer-options" role="group" aria-labelledby={`${id}-left`}>
          <span className="match-col-title" id={`${id}-left`}>{leftTitle}</span>
          {pairs.map((pair, itemId) => item("left", itemId, pair.left))}
        </div>
        <div className="match-col answer-options" role="group" aria-labelledby={`${id}-right`}>
          <span className="match-col-title" id={`${id}-right`}>{rightTitle}</span>
          {rightOrder.map(itemId => item("right", itemId, pairs[itemId].right))}
        </div>
      </div>
      <div className="choice-feedback" role="status">
        {(miss || allDone) && <p className={`match-feedback${miss ? " is-wrong" : " is-correct"}`}>
          {miss ? "Не совпало. Попробуйте другую пару." : (explanation ?? "Все пары соединены верно!")}
        </p>}
      </div>
      {allDone && <button ref={resetRef} type="button" className="interactive-secondary match-reset" onClick={() => { focusAfterMatch.current = true; setMatched(new Set()); setActive(null); setMiss(null); }}><RotateCcw size={15} aria-hidden="true" /> Попробовать ещё раз</button>}
    </section>
  );
}

// ---------- Интерактив: исследователь срезов ----------

export function SliceExplorer({ text, initialStart = 0, initialEnd }: { text: string; initialStart?: number; initialEnd?: number }) {
  const chars = [...text];
  const [start, setStart] = useState(initialStart);
  const [end, setEnd] = useState(initialEnd ?? chars.length);
  const result = chars.slice(start, end).join("");

  const clampStart = (value: number) => Math.max(0, Math.min(value, chars.length));
  const clampEnd = (value: number) => Math.max(0, Math.min(value, chars.length));

  return (
    <section className="slice-card">
      <VisualHeading kind="slice" />
      <div className="slice-strip" aria-hidden="true">
        {chars.map((char, index) => (
          <div className={`slice-char ${index >= start && index < end ? "is-selected" : ""}`} key={index}>
            <span className="slice-symbol">{char === " " ? "␣" : char}</span>
            <span className="slice-index">{index}</span>
          </div>
        ))}
        <div className="slice-char slice-char--edge">
          <span className="slice-symbol"> </span>
          <span className="slice-index">{chars.length}</span>
        </div>
      </div>

      <div className="slice-controls">
        <div className="slice-stepper">
          <span>start</span>
          <button type="button" onClick={() => setStart(value => clampStart(value - 1))} disabled={start <= 0} aria-label="start минус">
            <Minus size={14} />
          </button>
          <strong>{start}</strong>
          <button type="button" onClick={() => setStart(value => clampStart(value + 1))} disabled={start >= chars.length} aria-label="start плюс">
            <Plus size={14} />
          </button>
        </div>
        <div className="slice-stepper">
          <span>end</span>
          <button type="button" onClick={() => setEnd(value => clampEnd(value - 1))} disabled={end <= 0} aria-label="end минус">
            <Minus size={14} />
          </button>
          <strong>{end}</strong>
          <button type="button" onClick={() => setEnd(value => clampEnd(value + 1))} disabled={end >= chars.length} aria-label="end плюс">
            <Plus size={14} />
          </button>
        </div>
      </div>

      <div className="slice-result" aria-live="polite" aria-atomic="true">
        <code>
          text[{start}:{end}]
        </code>
        <ArrowRight size={16} />
        <code className="slice-output">"{result}"</code>
      </div>
      <p className="slice-note">Правая граница не включается: символ с индексом end в срез не попадает.</p>
    </section>
  );
}

// ---------- Интерактив: исследователь ветвлений ----------
export function FlipCards({ cards }: { cards: { front: React.ReactNode; back: React.ReactNode }[] }) {
  const [flipped, setFlipped] = useState<Set<number>>(() => new Set());

  function toggle(index: number) {
    setFlipped((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  return (
    <section className="flip-section">
      <VisualHeading kind="flip" />
      <div className="flip-grid">
        {cards.map((card, index) => (
          <button
            key={index}
            type="button"
            className={`flip-card ${flipped.has(index) ? "is-flipped" : ""}`}
            onClick={() => toggle(index)}
            aria-pressed={flipped.has(index)}
            aria-label={`Карточка ${index + 1}: ${plainOptionText(flipped.has(index) ? card.back : card.front)}`}
          >
            <div className="flip-inner">
              <div
                key={flipped.has(index) ? "back" : "front"}
                className={`flip-face ${flipped.has(index) ? "flip-back" : "flip-front"}`}
              >
                {flipped.has(index) ? card.back : card.front}
              </div>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

// ---------- Интерактив: правда или ложь ----------
