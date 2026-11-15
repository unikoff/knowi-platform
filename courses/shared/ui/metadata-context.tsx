import { createContext, type ReactNode } from "react";
import { getGenericLessonMetadata, type LessonMetadata } from "./metadata";

export const LessonMetadataContext = createContext<LessonMetadata | null>(null);

export function LessonMetadataProvider({ source, module, title, children, resolveMetadata }: {
  source: string;
  module?: string;
  title?: string;
  children: ReactNode;
  resolveMetadata?: (source: string, module?: string, title?: string) => LessonMetadata | null;
}) {
  const resolve = resolveMetadata ?? getGenericLessonMetadata;
  return <LessonMetadataContext.Provider value={resolve(source, module, title)}>
    {children}
  </LessonMetadataContext.Provider>;
}
