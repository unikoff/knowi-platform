// Лёгкая подсветка Python без зависимостей.
// Возвращает безопасный HTML: исходник экранируется до токенизации.

const PY_KEYWORDS =
  "False|True|None|and|or|not|if|elif|else|for|while|def|return|class|in|is|break|continue|pass|import|from|as|try|except|finally|with|lambda|global|nonlocal|raise|yield|del|assert";

const PY_BUILTINS =
  "print|input|len|range|int|float|str|bool|list|dict|set|tuple|sum|min|max|abs|round|sorted|enumerate|zip|type|append|remove|pop|upper|lower|isdigit|isalpha|isalnum";

const TOKEN_RE = new RegExp(
  [
    "(#[^\\n]*)", // 1: комментарий
    "(&quot;&quot;&quot;[\\s\\S]*?&quot;&quot;&quot;|'''[\\s\\S]*?''')", // 2: тройные строки
    "(&quot;(?:\\\\.|(?!&quot;)[^\\\\\\n])*&quot;|'(?:\\\\.|[^'\\\\\\n])*')", // 3: строки
    "\\b(\\d+(?:\\.\\d+)?)\\b", // 4: числа
    "\\b(def|class)([ \\t]+)([\\p{ID_Start}_][\\p{ID_Continue}]*)", // 5–7: объявление
    `\\b(${PY_KEYWORDS})\\b`, // 8: ключевые слова
    `\\b(${PY_BUILTINS})\\b`, // 9: встроенные функции/методы
    "(?<![\\p{ID_Continue}])([\\p{ID_Start}_][\\p{ID_Continue}]*)(?=[ \\t]*\\()", // 10: вызов
  ].join("|"),
  "gu",
);

function escapeHtml(source: string): string {
  return source.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function highlightPython(source: string): string {
  const escaped = escapeHtml(source);

  return escaped.replace(TOKEN_RE, (match, comment, triple, str, num, declaration, space, name, keyword, builtin, call) => {
    if (comment) return `<span class="tok-com">${match}</span>`;
    if (triple || str) return `<span class="tok-str">${match}</span>`;
    if (num) return `<span class="tok-num">${match}</span>`;
    if (declaration) return `<span class="tok-kw">${declaration}</span>${space}<span class="${declaration === "class" ? "tok-class" : "tok-fn"}">${name}</span>`;
    if (keyword) return `<span class="tok-kw">${match}</span>`;
    if (builtin || call) return `<span class="tok-fn">${match}</span>`;
    return match;
  });
}
