import "./components/styles/index.css";

export { CodeBlock } from "@platform/shared/ui/code-editor/CodeEditor";
export { VisualHeading } from "@platform/shared/ui/VisualHeading";
export { RichLesson, RichHero, Section, Lead, LinkedNotes, ContentList, Callout, TypeCards, TypeCard, PracticeCta, Checklist, KeyTakeaways, TheoryBridge } from "./components/layout";
export { CodeSequence } from "./components/code-exercises";
export { RecallCard, QuizCard } from "./components/questions";
export type { QuizOption } from "./components/questions";
export { PredictOutput, StepThrough, FillBlank, TerminalDemo, BranchExplorer } from "./components/code-demos";
export { MatchPairs, SliceExplorer, FlipCards } from "./components/explorations";
export { Diagram, DiagramFlow, DiagramNode, DiagramArrow, DiagramGroup, DiagramLane, DiagramBranches, DiagramBranch, DiagramNote, DiagramLegend, DiagramExchange, DiagramTrack } from "./components/diagrams";
export type { DiagramOrientation, DiagramNodeShape, DiagramArrowDirection, DiagramArrowStyle } from "./components/diagrams";
export { LessonScreenshots } from "./components/screenshots";
export type { LessonScreenshotItem } from "./components/screenshots";
export { InteractiveTable } from "./components/tables";
export type { InteractiveTableRow } from "./components/tables";
export { LessonMetadataProvider, LessonMetadataContext } from "./metadata-context";
export type { LessonMetadata } from "./metadata";
export { CourseLessonPlaceholder, CourseOverviewPlaceholder } from "./course-placeholder";
