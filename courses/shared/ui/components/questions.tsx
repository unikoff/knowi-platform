import React, { useId, useState } from "react";
import { plainOptionText } from "./option-text";
import { CodeBlock } from "@platform/shared/ui/code-editor/CodeEditor";
import { Check, ChevronDown, RotateCcw } from "lucide-react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";

export function RecallCard({ question, answer, hint }: { question: string; answer: React.ReactNode; hint?: string }) {
  const id = useId();
  const [isRevealed, setIsRevealed] = useState(false);
  const [reflection, setReflection] = useState<"remembered" | "repeat" | null>(null);

  return (
    <section className="recall-card" aria-labelledby={`${id}-question`}>
      <div className="recall-prompt">
        <VisualHeading kind="recall" />
        <h3 className="recall-question" id={`${id}-question`}>{question}</h3>
        {hint && <p className="recall-hint">{hint}</p>}
      </div>
      <button
        className="recall-toggle"
        type="button"
        aria-expanded={isRevealed}
        aria-controls={`${id}-answer`}
        onClick={() => setIsRevealed(value => !value)}
      >
        {isRevealed ? "Скрыть ответ" : "Показать ответ"}
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      <div
        className={`recall-reveal${isRevealed ? " is-open" : ""}`}
        id={`${id}-answer`}
        aria-hidden={!isRevealed}
        {...(!isRevealed ? { inert: "" } : {})}
      >
        <div className="recall-reveal-inner">
          <div className="recall-reveal-body">
            <div className="recall-answer">{answer}</div>
            <div className="recall-actions answer-options" role="group" aria-label="Самооценка">
              <button className={`answer-option${reflection === "remembered" ? " is-picked" : ""}`} type="button" aria-pressed={reflection === "remembered"} disabled={!isRevealed} onClick={() => setReflection("remembered")}>
                <Check size={15} aria-hidden="true" /> Вспомнил сам
              </button>
              <button className={`answer-option${reflection === "repeat" ? " is-picked" : ""}`} type="button" aria-pressed={reflection === "repeat"} disabled={!isRevealed} onClick={() => setReflection("repeat")}>
                <RotateCcw size={15} aria-hidden="true" /> Повторю позже
              </button>
            </div>
            {reflection && <p key={reflection} className={`recall-status${reflection === "remembered" ? " is-correct" : ""}`} role="status">
              {reflection === "remembered" ? "Закройте ответ и повторите своими словами." : "Вернитесь к вопросу после следующего раздела."}
            </p>}
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------- Универсальный выбор ответа ----------
export type QuizOption = string | { title: string; code?: string; description?: string };

export function QuizCard({
  question,
  description,
  options,
  correctIndex,
  explanation,
  orientation = "horizontal",
  answer,
}: {
  question: React.ReactNode;
  description?: React.ReactNode;
  options: QuizOption[];
  /** Индекс или несколько допустимых индексов; пользователь выбирает один ответ. */
  correctIndex: number | number[];
  explanation: string;
  orientation?: "horizontal" | "vertical";
  answer?: React.ReactNode;
}) {
  const id = useId();
  const [picked, setPicked] = useState<number | null>(null);
  const isDone = picked !== null;
  const accepted = typeof correctIndex === "number" ? [correctIndex] : correctIndex;
  const isCorrect = picked !== null && accepted.includes(picked);
  const detailed = options.some(option => typeof option !== "string" && Boolean(option.code || option.description));

  return (
    <div className="quiz-card" role="group" aria-labelledby={`${id}-question`}>
      <VisualHeading kind="quiz" />
      <div className="quiz-question">
        <strong id={`${id}-question`}>{question}</strong>
      </div>
      {description && <div className="quiz-description">{description}</div>}
      <div className="choice-response">
        <div className={`quiz-options answer-options${isDone ? " is-answered" : ""}`} data-orientation={orientation} data-detailed={detailed || undefined}>
          {options.map((option, index) => {
            const title = plainOptionText(typeof option === "string" ? option : option.title);
            const detail = typeof option === "string" ? undefined : option.description;
            const code = typeof option === "string" ? undefined : option.code;
            let stateClass = "";
            if (isDone && accepted.includes(index)) stateClass = "is-correct";
            else if (isDone && index === picked) stateClass = "is-wrong";
            return (
              <button
                key={index}
                type="button"
                className={`quiz-option answer-option ${stateClass}${picked === index ? " is-picked" : ""}`}
                disabled={isDone}
                aria-pressed={picked === index}
                onClick={() => setPicked(index)}
              >
                <span className="quiz-option-body">
                  <span className="quiz-option-title">{title}</span>
                  {code && <CodeBlock as="span" code={code} />}
                  {detail && <span className="quiz-option-description">{detail}</span>}
                </span>
              </button>
            );
          })}
        </div>
        <div className={`choice-feedback${isDone ? " is-visible" : ""}`} aria-hidden={!isDone}>
          <div className="choice-feedback-inner">
            <p className={`quiz-explanation ${isCorrect ? "is-correct" : "is-wrong"}`} role="status">
              {isDone && <>{isCorrect ? "Верно! " : "Не совсем. "}{explanation}</>}
            </p>
            {isCorrect && answer && <div className="quiz-answer">{answer}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
