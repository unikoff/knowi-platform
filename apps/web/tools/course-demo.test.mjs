import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";

for (const base of ["/", "/platform/"]) {
  test(`shared gallery HTML and entry module remain available under ${base}`, async () => {
    const previousBase = process.env.VITE_BASE;
    process.env.VITE_BASE = base;
    let server;
    try {
      server = await createServer({
        configFile: "config/vite.config.ts",
        plugins: [{ name: "one-shot-demo-test", configResolved(config) { config.server.watch = null; } }],
        optimizeDeps: { noDiscovery: true, include: [] },
        server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false, watch: null },
      });
      assert.equal(server.config.server.watch, null);
      await server.listen();
      const address = server.httpServer.address();
      const origin = `http://127.0.0.1:${address.port}`;
      for (const route of [`${base}backend/demo`, `${base}backend/demo/`, `${base}devtools/lesson-visual-demo.html`]) {
        const response = await fetch(origin + route);
        assert.equal(response.status, 200);
        const html = await response.text();
        const entry = [...html.matchAll(/src="([^"]+)"/g)].map(match => match[1]).find(src => src.endsWith("/courses/shared/demo/main.tsx"));
        assert.ok(entry, "Gallery HTML must use the shared demo entry");
        assert.ok(entry.startsWith(`${base}@fs/`), "Entry must respect the configured base");
        const module = await fetch(origin + entry);
        assert.equal(module.status, 200);
        assert.match(await module.text(), /createRoot/);
      }
    } finally {
      await server?.close();
      if (previousBase === undefined) delete process.env.VITE_BASE;
      else process.env.VITE_BASE = previousBase;
    }
  });
}
