// Only files already matched by Vite's local glob can be loaded.
export function previewTarget(origin: string, development: boolean, value: string | null): string {
  if (!development || origin !== "http://127.0.0.1:5173") {
    throw new Error("Предпросмотр доступен только на локальном dev-сервере 127.0.0.1:5173");
  }
  if (!value || value === "examples") return "examples";
  if (!/^chapter_[A-Za-z0-9_]+\/block_\d+\/[A-Za-z][A-Za-z0-9_]*$/.test(value)) {
    throw new Error("Укажем путь без расширения: chapter_01/block_01/Lesson01");
  }
  return value;
}
