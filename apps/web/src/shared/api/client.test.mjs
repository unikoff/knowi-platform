import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";
import { generateCourses } from "../../../tools/course-registry.mjs";
generateCourses();

for (const [base, expected] of [["/", "/api/user/me"], ["/platform/", "/platform/api/user/me"]]) {
 test(`apiRequest uses the configured base ${base}`, async () => {
  const vite = await createServer({
    configFile: false,
    // SSR-only contract test: avoid unrelated background client dependency scans.
    optimizeDeps: { noDiscovery: true, include: [] },
    base,
    server: { middlewareMode: true },
  });
  const originalFetch = globalThis.fetch;
  let requestedUrl;

  try {
    const { apiRequest } = await vite.ssrLoadModule("/src/shared/api/client.ts");
    globalThis.fetch = async (input) => {
      requestedUrl = input;
      return new Response(JSON.stringify({ user: null }), {
        headers: { "Content-Type": "application/json" },
      });
    };

    await apiRequest("/user/me");
    assert.equal(requestedUrl, expected);
  } finally {
    globalThis.fetch = originalFetch;
    await vite.close();
  }
 });
}

for (const [base, prefix] of [["/", ""], ["/platform/", "/platform"]]) {
 test(`learning requests carry the course selected by the route under ${base}`, async () => {
  const vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, base, server: { middlewareMode: true } });
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  let requestedUrl;
  try {
   globalThis.window = { location: { pathname: `${prefix}/backend_copy` } };
   const { apiRequest } = await vite.ssrLoadModule("/src/shared/api/client.ts");
   globalThis.fetch = async (input) => {
    requestedUrl = input;
    return new Response("{}", { headers: { "Content-Type": "application/json" } });
   };
   await apiRequest("/learning/catalog");
   assert.equal(requestedUrl, `${prefix}/api/learning/catalog?course_id=backend_copy`);
   await apiRequest("/learning/products");
   assert.equal(requestedUrl, `${prefix}/api/learning/products`);
  } finally {
   globalThis.fetch = originalFetch;
   if (originalWindow === undefined) delete globalThis.window;
   else globalThis.window = originalWindow;
   await vite.close();
  }
 });
}
