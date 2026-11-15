import type { ReactNode } from "react";

type SharedComponent = keyof typeof import("../ui");
export type CatalogVariant = { id: string; title: string; content: ReactNode };
export type CatalogExample = {
  id: string;
  title: string;
  api: SharedComponent;
  kind: "visual" | "interactive";
  variants: CatalogVariant[];
};
export type CatalogGroup = { id: string; title: string; examples: CatalogExample[] };

/** One public tool per entry. Examples demonstrate its supported configurations. */
export function defineCatalog(groups: CatalogGroup[]): CatalogGroup[] {
  const groupIds = new Set<string>();
  const componentIds = new Set<string>();
  const apis = new Set<SharedComponent>();
  for (const group of groups) {
    if (groupIds.has(group.id)) throw new Error(`Duplicate catalog group: ${group.id}`);
    groupIds.add(group.id);
    for (const component of group.examples) {
      if (componentIds.has(component.id) || apis.has(component.api)) throw new Error(`Duplicate catalog tool: ${component.api}`);
      componentIds.add(component.id);
      apis.add(component.api);
      if (["RichHero", "Section"].includes(component.api) && component.variants.length !== 1) throw new Error(`Only one standard example is allowed: ${component.api}`);
      if (component.variants.length < (component.kind === "interactive" ? 2 : 1)) throw new Error(`Missing demonstrations: ${component.api}`);
      const ids = component.variants.map(variant => variant.id);
      if (new Set(ids).size !== ids.length) throw new Error(`Duplicate demonstration: ${component.api}`);
    }
  }
  return groups;
}
