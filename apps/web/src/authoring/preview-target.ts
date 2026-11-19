// Only files already matched by Vite's local glob can be loaded.
export function previewTarget(origin: string, development: boolean, value: string | null): string {
  const match = /^http:\/\/127\.0\.0\.1(?::([1-9]\d{0,4}))?$/.exec(origin);
  if (!development || !match || (match[1] !== undefined && Number(match[1]) > 65535)) {
    throw new Error("Предпросмотр доступен только на локальном dev-сервере HTTP 127.0.0.1");
  }
  if (!value || value === "examples") return "examples";
  if (!/^chapter_[A-Za-z0-9_]+\/block_\d+\/[A-Za-z][A-Za-z0-9_]*$/.test(value)) {
    throw new Error("Укажем путь без расширения: chapter_01/block_01/Lesson01");
  }
  return value;
}
