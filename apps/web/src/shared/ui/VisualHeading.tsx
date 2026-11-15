import { ArrowLeftRight, Brain, Code2, Eye, GitBranch, Images, Layers, Lightbulb, Link2, ListChecks, ListOrdered, ListTree, Scissors, Sparkles, Table2, TextCursorInput } from "lucide-react";
import "./visual-heading.css";

const visuals = {
  quiz: { icon: Sparkles, title: "Вопрос" },
  recall: { icon: Brain, title: "Вспомнить" },
  match: { icon: Link2, title: "Пары" },
  flip: { icon: Layers, title: "Карточки" },
  fill: { icon: TextCursorInput, title: "Пропуск" },
  sequence: { icon: ListOrdered, title: "Порядок" },
  predict: { icon: Eye, title: "Прогноз" },
  step: { icon: ListTree, title: "По шагам" },
  branch: { icon: GitBranch, title: "Ветвление" },
  slice: { icon: Scissors, title: "Срезы" },
  code: { icon: Code2, title: "Код" },
  callout: { icon: Lightbulb, title: "Важно" },
  checklist: { icon: ListChecks, title: "Чек-лист" },
  screenshots: { icon: Images, title: "На экране" },
  table: { icon: Table2, title: "Таблица" },
  compare: { icon: ArrowLeftRight, title: "Сравнение" },
} as const;

/** One compact heading per standalone visual; task content stays below it. */
export function VisualHeading({ kind, id, className = "", as: Tag = "div" }: {
  kind: keyof typeof visuals;
  id?: string;
  className?: string;
  as?: "div" | "span";
}) {
  const { icon: Icon, title } = visuals[kind];
  return <Tag id={id} className={`visual-heading${className ? ` ${className}` : ""}`}>
    <Icon size={18} aria-hidden="true" />
    <span>{title}</span>
  </Tag>;
}
