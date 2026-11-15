import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readCourseDocument } from "./course-document.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const coursesRoot = path.join(root, "courses");
const idPattern = /^[a-z0-9]+(?:_[a-z0-9]+)*$/;
const slugPattern = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/;
// These segments belong to the app/Vite, API or public assets, never a course.
const reservedSlugs = new Set(["shared", "courses", "api", "assets", "devtools", "src", "node_modules", "public", "config", "tools", "authoring", "preview-loading", "platform"]);
const windowsReserved = /^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$/i;
function inside(parent, child) {
  const rel = path.relative(parent, child);
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
}
function checkedPath(parent, relative, label) {
  if (typeof relative !== "string" || !relative || /[\\:\u0000-\u001f\u007f]/.test(relative) || path.isAbsolute(relative) || relative.split("/").some(x => !x || x === "." || x === ".." || /[. ]$/.test(x) || windowsReserved.test(x))) throw new Error(`Unsafe ${label}`);
  let current = parent;
  for (const part of relative.split("/")) {
    current = path.join(current, part);
    if (fs.lstatSync(current).isSymbolicLink()) throw new Error(`Symlink in ${label}`);
  }
  const real = fs.realpathSync(current);
  if (!inside(parent, real)) throw new Error(`Escaping ${label}`);
  return real;
}
function rejectSymlinks(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const name = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error("Symlink in frontend tree");
    if (entry.isDirectory()) rejectSymlinks(name);
  }
}
export function discoverCourses(base = coursesRoot) {
  base = fs.realpathSync(base);
  const registry = readCourseDocument(checkedPath(base, "registry.json", "registry"));
  if (registry.schema_version !== 1 || !Array.isArray(registry.courses)) throw new Error("Invalid course registry");
  const ids = new Set(), slugs = new Set(), result = [];
  for (const entry of registry.courses) {
    const id = entry.course_id;
    if (typeof id !== "string" || !idPattern.test(id) || id.length > 64 || id === "shared" || ids.has(id) || typeof entry.enabled !== "boolean") throw new Error("Invalid or duplicate course ID");
    ids.add(id);
    const manifestPath = checkedPath(base, entry.manifest, "manifest");
    if (entry.manifest !== `${id}/course.yaml`) throw new Error("Manifest must belong to its course folder");
    const manifest = readCourseDocument(manifestPath);
    if (manifest.course_id !== id || typeof manifest.slug !== "string" || !slugPattern.test(manifest.slug) || reservedSlugs.has(manifest.slug) || slugs.has(manifest.slug)) throw new Error("Invalid or duplicate course slug");
    slugs.add(manifest.slug);
    const courseDir = path.dirname(manifestPath);
    const frontendDir = checkedPath(courseDir, "frontend", "frontend directory");
    rejectSymlinks(frontendDir);
    const entries = {};
    for (const key of ["course", "personal_landing", "course_landing"]) {
      const declared = manifest.frontend?.[key];
      if (typeof declared !== "string" || !declared.startsWith("frontend/") || !/\.(?:ts|tsx|js|jsx)$/.test(declared)) throw new Error(`Invalid frontend entry ${key}`);
      const resolved = checkedPath(courseDir, declared, key);
      if (!inside(frontendDir, resolved) || !fs.statSync(resolved).isFile()) throw new Error(`Invalid frontend entry ${key}`);
      entries[key] = resolved;
    }
    if (entry.enabled) result.push({ id, slug: manifest.slug, frontendDir, entries });
  }
  return result;
}
export function generateCourses() {
  const courses = discoverCourses();
  const output = path.join(root, "apps/web/.cache/course-registry");
  fs.mkdirSync(output, { recursive: true });
  const ids = `// Generated from courses/registry.json by tools/course-registry.mjs.\nexport const registeredCourses: ReadonlyArray<{ id: string; slug: string }> = ${JSON.stringify(courses.map(({ id, slug }) => ({ id, slug })))};\nexport const courseIds = registeredCourses.map(course => course.id);\n`;
  const imports = courses.map(course => {
    const imports = Object.entries(course.entries).map(([key, file]) => {
      let rel = path.relative(output, file).split(path.sep).join("/");
      if (!rel.startsWith(".")) rel = "./" + rel;
      return `${JSON.stringify(key)}: () => import(${JSON.stringify(rel)})`;
    }).join(", ");
    return `  ${JSON.stringify(course.id)}: { ${imports} }`;
  }).join(",\n");
  const loaders = `// Generated from validated manifests. Each import remains lazy.\nexport const courseLoaders = {\n${imports}\n};\n`;
  const preview = courses.map(course => {
    let rel = path.relative(output, path.dirname(course.entries.course)).split(path.sep).join("/");
    if (!rel.startsWith(".")) rel = "./" + rel;
    return `  ${JSON.stringify(course.id)}: { prefix: ${JSON.stringify(rel + "/")}, modules: import.meta.glob<Record<string, import("react").ComponentType<{ module?: string }>>>(${JSON.stringify(rel + "/chapter_*/block_*/*.tsx")}) }`;
  }).join(",\n");
  const previews = `// Generated preview globs are limited to registered course frontend folders.\nexport const previewCourses = {\n${preview}\n};\n`;
  for (const [name, content] of [["course-ids.ts", ids], ["generated-loaders.ts", loaders], ["generated-preview.ts", previews]]) {
    const file = path.join(output, name);
    if (!fs.existsSync(file) || fs.readFileSync(file, "utf8") !== content) fs.writeFileSync(file, content);
  }
  return courses;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generateCourses();
