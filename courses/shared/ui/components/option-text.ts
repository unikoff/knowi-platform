import React from "react";

/** Answer labels are plain text, including legacy inline-code JSX and backticks. */
export function plainOptionText(value: React.ReactNode): string {
  if (typeof value === "string") return value.replace(/`([^`]+)`/g, "$1");
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(plainOptionText).join("");
  if (React.isValidElement<{ children?: React.ReactNode }>(value)) {
    return plainOptionText(value.props.children);
  }
  return "";
}
