import React, { useEffect, useRef, useState } from "react";
import { plainOptionText } from "./option-text";
import { Check, ChevronLeft, ChevronRight, RotateCcw, X } from "lucide-react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";
import { CodeBlock } from "@platform/shared/ui/code-editor/CodeEditor";
import { highlightPython } from "@platform/shared/lib/python-highlight";
import { RevealCard } from "./questions";

export function PredictOutput({ code, output, hint }: { code: string; output: string; hint?: string }) {
  return <RevealCard question="Что выведет программа?" code={code} description={hint} answerKind="вывод" selfAssessment={false} answer={<pre className="recall-output" aria-label="Вывод программы"><code>{output}</code></pre>} />;
}

// ---------- Интерактив: пошаговое выполнение (трассировщик) ----------

type TraceStep = { line: number; note: string; vars?: Record<string, string> };

export function StepThrough({ code, steps }: { code: string; steps: TraceStep[] }) {
  const dataKey = JSON.stringify([code, steps]);
  const [selection, setSelection] = useState({ key: dataKey, index: 0 });
  if (selection.key !== dataKey) setSelection({ key: dataKey, index: 0 });
  const lastIndex = Math.max(0, steps.length - 1);
  const currentIndex = selection.key === dataKey ? selection.index : 0;
  const step = steps[currentIndex];
  const lines = code.split("\n");
  const select = (index: number) => setSelection({ key: dataKey, index });
  const move = (delta: number) => setSelection(previous => {
    const index = Math.min(lastIndex, Math.max(0, previous.index + delta));
    return previous.index === index ? previous : { key: dataKey, index };
  });

  return (
    <section className="tracer" aria-label="Пошаговое выполнение">
      <div className="tracer-toolbar">
        <VisualHeading kind="step" />
        {step && <span className="tracer-progress">Шаг {currentIndex + 1} из {steps.length}</span>}
      </div>
      {steps.length > 0 && <div className="tracer-steps" role="group" aria-label="Выбрать шаг">
        {steps.map((_, i) => <button key={i} type="button" className={i === currentIndex ? "is-current" : ""} aria-label={`Шаг ${i + 1}`} aria-current={i === currentIndex ? "step" : undefined} onClick={() => select(i)}>{i + 1}</button>)}
      </div>}
      <div className="tracer-body">
        <div className="tracer-code" role="region" aria-label="Код программы" tabIndex={0}>
          <div className="tracer-code-content">
            {lines.map((line, idx) => (
              <div className={`tracer-line${idx === step?.line ? " is-current" : ""}`} aria-current={idx === step?.line ? "step" : undefined} key={idx}>
                <span className="tracer-ln" aria-hidden="true">{idx + 1}</span>
                <code dangerouslySetInnerHTML={{ __html: highlightPython(line) || "&nbsp;" }} />
              </div>
            ))}
          </div>
        </div>
        <div className="tracer-panel" role="status" aria-atomic="true">
          <span className="trace-label">{step ? `Шаг ${currentIndex + 1} · строка ${step.line + 1}` : "Нет шагов"}</span>
          <p className="tracer-note">{step?.note ?? "Для этого примера шаги пока не заданы."}</p>
          {step?.vars && Object.keys(step.vars).length > 0 && <div className="trace-values"><span className="trace-label">Значения</span><div className="tracer-vars">
            {Object.entries(step.vars).map(([name, value]) => <span className="tracer-var" key={name}><b>{name}</b><span className="tracer-equals">=</span><span className="tracer-value">{value}</span></span>)}
          </div></div>}
        </div>
      </div>
      {steps.length > 1 && <div className="tracer-nav">
        <button type="button" aria-disabled={currentIndex === 0} onClick={() => move(-1)}><ChevronLeft size={14} aria-hidden="true" /> Назад</button>
        <button type="button" aria-disabled={currentIndex === lastIndex} onClick={() => move(1)}>Дальше <ChevronRight size={14} aria-hidden="true" /></button>
        <button type="button" aria-disabled={currentIndex === 0} onClick={() => { if (currentIndex > 0) select(0); }}>В начало</button>
      </div>}
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
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const focusAfterReset = useRef<number | null>(null);
  const retry = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (picked === null && focusAfterReset.current !== null) {
      optionRefs.current[focusAfterReset.current]?.focus();
      focusAfterReset.current = null;
    } else if (isCorrect) retry.current?.focus();
  }, [picked, isCorrect]);

  return (
    <div className="fill-card">
      <VisualHeading kind="fill" />
      <div className="fill-prompt">{prompt}</div>
      <pre className="fill-code">
        <code>
          {before}
          <span className={`fill-gap ${picked !== null ? (isCorrect ? "is-correct" : "is-wrong") : ""}`}>{picked ?? "____"}</span>
          {after}
        </code>
      </pre>
      <div className="fill-options answer-options">
        {choices.map((option, index) => (
          <button
            key={index}
            ref={element => { optionRefs.current[index] = element; }}
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
      <p role="status" className={`fill-explanation ${isCorrect ? "is-correct" : "is-wrong"}`} hidden={picked === null}>
        {picked !== null && <>{isCorrect ? <Check size={14} aria-hidden="true" /> : <X size={14} aria-hidden="true" />} {isCorrect ? explanation : (wrongExplanation ?? "Пробуем другой вариант.")}</>}
      </p>
      {isCorrect && <button ref={retry} className="interactive-secondary fill-reset" type="button" onClick={() => {
        focusAfterReset.current = choices.findIndex(option => option === picked);
        setPicked(null);
      }}><RotateCcw size={15} aria-hidden="true" /> Попробовать ещё раз</button>}
    </div>
  );
}

// Совместимость существующих уроков: терминал использует общую карточку примера.
export function TerminalDemo({ title, lines }: { title?: string; lines: { cmd?: string; out?: string }[] }) {
  const code = lines.map(line => line.cmd !== undefined ? `$ ${line.cmd}` : (line.out ?? "")).join("\n");
  return <CodeBlock caption={title ?? "Терминал"} code={code} language="text" />;
}

// ---------- Интерактив: ветвление ----------
type BranchScenario = { label: string; activeLine: number; output: string };

export function BranchExplorer({ code, scenarios }: { code: string; scenarios: BranchScenario[] }) {
  const dataKey = JSON.stringify([code, scenarios]);
  const [selection, setSelection] = useState({ key: dataKey, index: 0 });
  if (selection.key !== dataKey) setSelection({ key: dataKey, index: 0 });
  const active = selection.key === dataKey ? selection.index : 0;
  const scenario = scenarios[active];
  const lines = code.split("\n");

  return (
    <section className="branch-card" aria-label="Исследование ветвления">
      <VisualHeading kind="branch" />
      {scenario && <div className="branch-selector"><span className="trace-label">Выберите сценарий</span>
        <div className="branch-scenarios answer-options" role="group" aria-label="Сценарии">
          {scenarios.map((item, index) => <button key={index} type="button" className={`branch-chip answer-option ${active === index ? "is-active" : ""}`} aria-pressed={active === index} onClick={() => setSelection({ key: dataKey, index })}>{plainOptionText(item.label)}</button>)}
        </div>
      </div>}
      <div className="branch-workspace">
        <div className="branch-code" role="region" aria-label="Код программы" tabIndex={0}>
          {lines.map((line, idx) => <div className={`branch-line ${idx === scenario?.activeLine ? "is-active" : ""}`} aria-current={idx === scenario?.activeLine ? "step" : undefined} key={idx}><span className="tracer-ln" aria-hidden="true">{idx + 1}</span><code dangerouslySetInnerHTML={{ __html: highlightPython(line) || "&nbsp;" }} /></div>)}
        </div>
        <div className="branch-result" role="status" aria-atomic="true">
          {scenario ? <><span className="trace-label">Выбранный сценарий</span><strong>{plainOptionText(scenario.label)}</strong><span className="trace-label">Результат</span><code>{scenario.output}</code></> : <p>Сценарии для этого примера пока не заданы.</p>}
        </div>
      </div>
    </section>
  );
}
