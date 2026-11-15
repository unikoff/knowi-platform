import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..").replaceAll("\\", "/");
const requestRoot = encodeURI(projectRoot);
// Use raw HTTP paths so the client does not normalize away encoded traversal.
function status(port, pathname) {
  return new Promise((resolve, reject) => {
    const request = http.get({ host: "127.0.0.1", port, path: pathname, headers: { Accept: "application/octet-stream", Connection: "close" } }, response => {
      response.resume();
      response.once("end", () => resolve(response.statusCode));
    });
    request.once("error", reject);
  });
}

for (const base of ["/", "/platform/"]) {
  test(`private course/backend/archive/AI paths fail closed under ${base}`, async (context) => {
    const previousBase = process.env.VITE_BASE;
    process.env.VITE_BASE = base;
    let server;
    try {
      server = await createServer({
        configFile: "config/vite.config.ts",
        logLevel: "silent",
        plugins: [{ name: "one-shot-boundary-test", configResolved(config) { config.server.watch = null; } }],
        optimizeDeps: { noDiscovery: true, include: [] },
        server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
      });
      assert.equal(server.config.server.fs.strict, true);
      for (const rule of [".env", ".env.*", "**/personal.settings.yaml", "**/courses/*/backend/**", "**/courses/*/archive/**", "**/courses/shared/ai/**"]) {
        assert.ok(server.config.server.fs.deny.includes(rule));
      }
      assert.ok(!server.config.server.fs.allow.includes(path.join(projectRoot, "courses").replaceAll("\\", "/")));
      await server.listen();
      const port = server.httpServer.address().port;
      const prefix = `${base}@fs/${requestRoot}/courses/`;
      assert.equal(await status(port, `${prefix}backend/frontend/course/index.ts`), 200);
      const privatePaths = [
        "backend/backend/practice_catalog.py",
        "backend/archive/legacy-content/__init__.py",
        "shared/ai/README.md",
        "backend/frontend/course/%2e%2e/%2e%2e/backend/practice_catalog.py",
        "backend/frontend/course/%2e%2e/%2e%2e/archive/legacy-content/__init__.py",
        "shared/ui/%2e%2e/ai/README.md",
        "backend/frontend/course/..%2f..%2fbackend/practice_catalog.py",
        "backend/frontend/course/%252e%252e/%252e%252e/archive/legacy-content/__init__.py",
      ];
      const rejected = {};
      for (const privatePath of privatePaths) {
        const code = await status(port, prefix + privatePath);
        assert.ok([400, 403, 404].includes(code), privatePath);
        rejected[code] = (rejected[code] ?? 0) + 1;
      }
      context.diagnostic(`Private request outcomes: ${JSON.stringify(rejected)}`);
    } finally {
      await server?.close();
      if (previousBase === undefined) delete process.env.VITE_BASE;
      else process.env.VITE_BASE = previousBase;
    }
  });
}
