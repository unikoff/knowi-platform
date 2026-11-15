import { fileURLToPath } from "node:url";
import { dirname, relative, resolve } from "node:path";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { generateCourses } from "../tools/course-registry.mjs";

const devApiTarget = process.env.PYMENTOR_DEV_API_TARGET ?? "http://127.0.0.1:18001";
const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const projectRoot = fileURLToPath(new URL("../../..", import.meta.url));
const registeredCourses = generateCourses();
const frontendModules = resolve(frontendRoot, "node_modules");

function lessonVisualDemoRoutes(): Plugin {
  const configuredBase = process.env.VITE_BASE ?? "/";
  const basePath = configuredBase.startsWith("/") ? configuredBase.replace(/\/+$/, "") : "";
  const publicDemoPath = `${basePath}/backend/demo`;
  const builtHtml = "devtools/lesson-visual-demo.html";
  const demoRoot = resolve(projectRoot, "courses/shared/demo");
  const generatedHtml = resolve(frontendRoot, ".cache/course-registry/lesson-visual-demo.html");
  const generatedName = relative(frontendRoot, generatedHtml).replaceAll("\\", "/");
  const canonicalHtml = readFileSync(resolve(demoRoot, "index.html"), "utf8");
  const withEntry = (entry: string) => {
    const html = canonicalHtml.replace(/src=["'](?:\.\/main\.tsx|\/courses\/shared\/demo\/main\.tsx)["']/, `src=${JSON.stringify(entry)}`);
    if (html === canonicalHtml) throw new Error("Shared demo HTML must declare main.tsx");
    return html;
  };
  const scriptPath = relative(dirname(generatedHtml), resolve(demoRoot, "main.tsx")).replaceAll("\\", "/");
  // Build from ignored app state; the shared package owns all gallery content.
  mkdirSync(dirname(generatedHtml), { recursive: true });
  writeFileSync(generatedHtml, withEntry(scriptPath));

  const isDemo = (pathname: string) => pathname === publicDemoPath || pathname === `${publicDemoPath}/` || pathname === `${basePath}/${builtHtml}`;
  return {
    name: "lesson-visual-demo-route",
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        const pathname = request.url?.split("?")[0];
        if (!pathname || !isDemo(pathname)) return next();
        try {
          const entry = `/@fs/${resolve(demoRoot, "main.tsx").replaceAll("\\", "/")}`;
          const html = await server.transformIndexHtml(pathname, withEntry(entry));
          response.setHeader("Content-Type", "text/html; charset=utf-8");
          response.end(html);
        } catch (error) { next(error); }
      });
    },
    configurePreviewServer(server) {
      server.middlewares.use((request, _response, next) => {
        if (request.url) {
          const [pathname, query] = request.url.split("?", 2);
          if (isDemo(pathname)) request.url = `${basePath}/${builtHtml}${query ? `?${query}` : ""}`;
        }
        next();
      });
    },
    config() {
      return {
        build: {
          rolldownOptions: {
            input: { app: resolve(frontendRoot, "index.html"), lessonVisualDemo: generatedHtml },
          },
        },
      };
    },
    generateBundle: {
      order: "post",
      handler(_options, bundle) {
        const html = bundle[generatedName];
        if (!html || html.type !== "asset") throw new Error("Shared lesson demo HTML was not emitted");
        delete bundle[generatedName];
        this.emitFile({ type: "asset", fileName: builtHtml, source: html.source });
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), lessonVisualDemoRoutes()],
  // VITE_BASE is public build-time configuration (for example /platform/).
  // Never put backend credentials or other secrets in VITE_* variables.
  base: process.env.VITE_BASE ?? "/",
  cacheDir: process.env.VITE_CACHE_DIR ?? resolve(frontendRoot, ".cache/vite"),
  resolve: {
    alias: [
      { find: /^react$/, replacement: resolve(frontendModules, "react") },
      { find: "react-dom", replacement: resolve(frontendModules, "react-dom") },
      { find: /^@course-ui$/, replacement: resolve(projectRoot, "courses/shared/ui/index.ts") },
      { find: /^@course-ui\//, replacement: resolve(projectRoot, "courses/shared/ui") + "/" },
      { find: /^@platform\//, replacement: resolve(frontendRoot, "src") + "/" },
      { find: "video.js", replacement: resolve(frontendModules, "video.js") },
      { find: "lucide-react", replacement: resolve(frontendModules, "lucide-react") },
    ],
    dedupe: ["react", "react-dom"],
  },
  server: {
    strictPort: true,
    fs: {
      strict: true,
      allow: [
        frontendRoot,
        ...registeredCourses.map((course) => course.frontendDir),
        resolve(projectRoot, "courses/shared/ui"),
        resolve(projectRoot, "courses/shared/demo"),
        resolve(projectRoot, "courses/shared/landing"),
      ],
      deny: [
        ".env",
        ".env.*",
        "*.{crt,pem,key,p12,pfx,cer,der}",
        ".npmrc",
        ".yarnrc.yml",
        "**/.git/**",
        "**/.local",
        "**/.local/**",
        "**/courses/*/backend/**",
        "**/courses/*/archive/**",
        "**/courses/shared/ai/**",
        "**/personal.settings.yaml",
        "**/course.yaml",
      ],
    },
    proxy: {
      "/api": {
        target: devApiTarget,
        // Core verifies Origin against the external Host for unsafe requests.
        // Preserve the browser-facing host when proxying to the loopback API.
        changeOrigin: false,
      },
    },
  },
});
