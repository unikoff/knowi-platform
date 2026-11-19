import React, { useEffect, useId, useRef, useState } from "react";
import { plainOptionText } from "./option-text";
import { CodeBlock } from "@platform/shared/ui/code-editor/CodeEditor";
import { Check, ChevronDown, RotateCcw } from "lucide-react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";

export type RecallCardProps = {
  question: React.ReactNode;
  answer: React.ReactNode;
  code?: string;
  hint?: string;
};

export function RecallCard(props: RecallCardProps) {
  return <RevealCard {...props} />;
}

/** The legacy prediction adapter uses the same disclosure with its original labels. */
export function RevealCard({ question, answer, code, hint, description, answerKind = "ответ", selfAssessment = true }: RecallCardProps & {
  description?: string;
  answerKind?: "ответ" | "вывод";
  selfAssessment?: boolean;
}) {
  const id = useId();
  const [isRevealed, setIsRevealed] = useState(false);
  const [hintRevealed, setHintRevealed] = useState(false);
  const [reflection, setReflection] = useState<"remembered" | "repeat" | null>(null);

  return (
    <section className="recall-card" aria-labelledby={`${id}-question`}>
      <div className="recall-prompt">
        <VisualHeading kind="recall" />
        <h3 className="recall-question" id={`${id}-question`}>{question}</h3>
        {code !== undefined && <CodeBlock code={code} />}
        <p className="recall-instruction">{description ?? (code !== undefined ? "Предположите результат, затем сверьтесь с ответом." : "Попробуйте ответить своими словами, затем сверьтесь с ответом.")}</p>
      </div>
      <div className="recall-controls">
        {hint && <button className="recall-toggle" type="button" aria-expanded={hintRevealed} aria-controls={`${id}-hint`} onClick={() => setHintRevealed(value => !value)}>
          Подсказка <ChevronDown size={15} aria-hidden="true" />
        </button>}
        <button className="recall-toggle recall-answer-toggle" type="button" aria-expanded={isRevealed} aria-controls={`${id}-answer`} onClick={() => setIsRevealed(value => !value)}>
          {isRevealed ? `Скрыть ${answerKind}` : `Показать ${answerKind}`}
          <ChevronDown size={15} aria-hidden="true" />
        </button>
      </div>
      {hint && <p className="recall-hint" id={`${id}-hint`} hidden={!hintRevealed}>{hint}</p>}
      <div className="recall-reveal" id={`${id}-answer`} hidden={!isRevealed}>
        <div className="recall-answer">{answer}</div>
        {selfAssessment && <div className="recall-actions answer-options" role="group" aria-label="Самооценка">
          <button className="answer-option" type="button" aria-pressed={reflection === "remembered"} onClick={() => setReflection("remembered")}>
            <Check size={15} aria-hidden="true" /> Ответил самостоятельно
          </button>
          <button className="answer-option" type="button" aria-pressed={reflection === "repeat"} onClick={() => setReflection("repeat")}>
            <RotateCcw size={15} aria-hidden="true" /> Повторю позже
          </button>
        </div>}
        {selfAssessment && <p className="recall-status" role="status" hidden={!reflection}>
          {reflection === "remembered" && "Закройте ответ и попробуйте ещё раз."}
          {reflection === "repeat" && "Вернитесь к вопросу после следующего раздела."}
        </p>}
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
  const firstOptionRef = useRef<HTMLButtonElement>(null);
  const retryRef = useRef<HTMLButtonElement>(null);
  const focusAfterChange = useRef<"retry" | "option" | null>(null);
  useEffect(() => {
    if (focusAfterChange.current === "retry") retryRef.current?.focus();
    if (focusAfterChange.current === "option") firstOptionRef.current?.focus();
    focusAfterChange.current = null;
  }, [picked]);
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
                ref={index === 0 ? firstOptionRef : undefined}
                type="button"
                className={`quiz-option answer-option ${stateClass}${picked === index ? " is-picked" : ""}`}
                disabled={isDone}
                aria-pressed={picked === index}
                onClick={() => { focusAfterChange.current = "retry"; setPicked(index); }}
              >
                <span className="quiz-option-body">
                  <span className="quiz-option-title">{title}</span>
                  {code && <CodeBlock as="span" code={code} />}
                  {detail && <span className="quiz-option-description">{detail}</span>}
                  {stateClass && <span className="quiz-option-result">{stateClass === "is-correct" ? "Верный ответ" : "Неверный ответ"}</span>}
                </span>
              </button>
            );
          })}
        </div>
        <div className="choice-feedback" role="status">
          {isDone && <p className={`quiz-explanation ${isCorrect ? "is-correct" : "is-wrong"}`}>
            {isCorrect ? "Верно! " : "Не совсем. "}{explanation}
          </p>}
        </div>
        {isCorrect && answer && <div className="quiz-answer">{answer}</div>}
        {isDone && <button ref={retryRef} className="interactive-secondary quiz-retry" type="button" onClick={() => { focusAfterChange.current = "option"; setPicked(null); }}><RotateCcw size={15} aria-hidden="true" /> Попробовать ещё раз</button>}
      </div>
    </div>
  );
}
