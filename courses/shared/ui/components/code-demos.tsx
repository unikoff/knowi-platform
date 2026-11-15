import React, { useId, useState } from "react";
import { plainOptionText } from "./option-text";
import { Check, ChevronDown, ChevronLeft, ChevronRight, X } from "lucide-react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";
import { CodeBlock } from "@platform/shared/ui/code-editor/CodeEditor";
import { highlightPython } from "@platform/shared/lib/python-highlight";

export function PredictOutput({ code, output, hint }: { code: string; output: string; hint?: string }) {
  const id = useId();
  const [shown, setShown] = useState(false);
  return (
    <section className="predict-card" aria-labelledby={`${id}-question`}>
      <VisualHeading kind="predict" id={`${id}-question`} />
      <CodeBlock code={code} />
      {hint && <p className="predict-hint">{hint}</p>}
      <button className="predict-btn" type="button" aria-expanded={shown} aria-controls={`${id}-output`} onClick={() => setShown(value => !value)}>
        {shown ? "Скрыть вывод" : "Показать вывод"}<ChevronDown size={15} aria-hidden="true" />
      </button>
      <div className={`predict-reveal${shown ? " is-open" : ""}`} id={`${id}-output`} aria-hidden={!shown}>
        <div className="predict-reveal-inner">
          <pre className="predict-output" aria-label="Вывод программы"><code>{output}</code></pre>
        </div>
      </div>
    </section>
  );
}

// ---------- Интерактив: пошаговое выполнение (трассировщик) ----------

type TraceStep = { line: number; note: string; vars?: Record<string, string> };

export function StepThrough({ code, steps }: { code: string; steps: TraceStep[] }) {
  const [index, setIndex] = useState(0);
  const [direction, setDirection] = useState(1);
  const lastIndex = Math.max(0, steps.length - 1);
  const currentIndex = Math.min(index, lastIndex);
  const step = steps[currentIndex];
  const lines = code.split("\n");
  const hasActiveLine = step && Number.isInteger(step.line) && step.line >= 0 && step.line < lines.length;

  function move(delta: number) {
    setDirection(delta);
    setIndex(Math.max(0, Math.min(lastIndex, currentIndex + delta)));
  }

  return (
    <section className="tracer" aria-label="Пошаговое выполнение">
      <div className="tracer-toolbar">
        <VisualHeading kind="step" />
        <div className="tracer-nav">
          <button type="button" disabled={currentIndex === 0} onClick={() => move(-1)}>
            <ChevronLeft size={14} aria-hidden="true" /> Назад
          </button>
          <span className="tracer-progress" aria-label={`Шаг ${steps.length ? currentIndex + 1 : 0} из ${steps.length}`}>
            {steps.length ? currentIndex + 1 : 0} / {steps.length}
          </span>
          <button type="button" disabled={currentIndex === lastIndex} onClick={() => move(1)}>
            Дальше <ChevronRight size={14} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className="tracer-body">
        <div className="tracer-code" role="region" aria-label="Код программы" tabIndex={0}>
          <div className={`tracer-code-content${hasActiveLine ? " has-active-line" : ""}`} style={{ "--trace-line": hasActiveLine ? step.line : 0 } as React.CSSProperties}>
            <span className="tracer-cursor" aria-hidden="true" />
            {lines.map((line, idx) => (
              <div className={`tracer-line${idx === step?.line ? " is-current" : ""}`} aria-current={idx === step?.line ? "step" : undefined} key={idx}>
                <span className="tracer-ln">{idx + 1}</span>
                <code dangerouslySetInnerHTML={{ __html: highlightPython(line) || "&nbsp;" }} />
              </div>
            ))}
          </div>
        </div>
        <div className="tracer-panel" role="status" aria-atomic="true" style={{ "--trace-direction": direction } as React.CSSProperties}>
          {step && <p className="tracer-note" key={currentIndex}>{step.note}</p>}
          {step?.vars && Object.keys(step.vars).length > 0 && (
            <div className="tracer-vars">
              {Object.entries(step.vars).map(([name, value]) => (
                <span className="tracer-var" key={name}>
                  <b>{name}</b><span className="tracer-equals">=</span><span className="tracer-value" key={value}>{value}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// ---------- Интерактив: заполни пропуск ----------

export function FillBlank({
  prompt,
  before,
  after,
  options,
  answer,
  explanation,
  wrongExplanation,
}: {
  prompt: string;
  before: string;
  after: string;
  options: string[];
  answer: string;
  explanation: string;
  wrongExplanation?: string;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const choices = options.map(plainOptionText);
  const isCorrect = picked === plainOptionText(answer);

  return (
    <div className="fill-card">
      <VisualHeading kind="fill" />
      <div className="fill-prompt">{prompt}</div>
      <pre className="fill-code">
        <code>
          {before}
          <span className={`fill-gap ${picked ? (isCorrect ? "is-correct" : "is-wrong") : ""}`}>{picked ?? "____"}</span>
          {after}
        </code>
      </pre>
      <div className="fill-options answer-options">
        {choices.map((option, index) => (
          <button
            key={index}
            type="button"
            className={`fill-option answer-option ${picked === option ? (isCorrect ? "is-correct" : "is-wrong") : ""}`}
            aria-pressed={picked === option}
            disabled={isCorrect}
            onClick={() => setPicked(option)}
          >
            <span>{option}</span>
          </button>
        ))}
      </div>
      {picked && (
        <p className={`fill-explanation ${isCorrect ? "is-correct" : "is-wrong"}`}>
          {isCorrect ? <Check size={14} /> : <X size={14} />} {isCorrect ? explanation : (wrongExplanation ?? "Пробуем другой вариант.")}
        </p>
      )}
    </div>
  );
}

// Совместимость существующих уроков: терминал использует общую карточку примера.
export function TerminalDemo({ title, lines }: { title?: string; lines: { cmd?: string; out?: string }[] }) {
  const code = lines.map(line => line.cmd !== undefined ? `$ ${line.cmd}` : (line.out ?? "")).join("\n");
  return <CodeBlock caption={title ?? "Терминал"} code={code} language="text" />;
}

// ---------- Интерактив: соедини пары ----------
type BranchScenario = { label: string; activeLine: number; output: string };

export function BranchExplorer({ code, scenarios }: { code: string; scenarios: BranchScenario[] }) {
  const [active, setActive] = useState(0);
  const scenario = scenarios[active];
  const lines = code.split("\n");

  return (
    <section className="branch-card">
      <VisualHeading kind="branch" />
      <div className="branch-scenarios answer-options">
        {scenarios.map((item, index) => (
          <button
            key={index}
            type="button"
            className={`branch-chip answer-option ${active === index ? "is-active" : ""}`}
            aria-pressed={active === index}
            onClick={() => setActive(index)}
          >
            {plainOptionText(item.label)}
          </button>
        ))}
      </div>
      <div className="branch-code">
        {lines.map((line, idx) => (
          <div className={`branch-line ${idx === scenario.activeLine ? "is-active" : ""}`} key={idx}>
            <code dangerouslySetInnerHTML={{ __html: highlightPython(line) || "&nbsp;" }} />
          </div>
        ))}
      </div>
      <div className="branch-output">
        <span>результат</span>
        <code>{scenario.output}</code>
      </div>
    </section>
  );
}

// ---------- Интерактив: карточки-перевёртыши ----------
