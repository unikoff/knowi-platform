import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.cwd();
const port = Number(process.env.CORP607_MIXED_WEB_PORT ?? "52707");
if (![52707, 54327].includes(port)) throw new Error("Unexpected synthetic Web listener");
const vite = await import(pathToFileURL(resolve(workspace, "node_modules/vite/dist/node/index.js")).href);
const config = { configFile: resolve(workspace, "config/vite.config.ts"), configLoader: "bundle" };
const listen = { host: "127.0.0.1", port, strictPort: true };
const dist = process.env.CORP607_MIXED_WEB_DIST;
const server = dist
  ? await vite.preview({ ...config, build: { outDir: dist }, preview: listen })
  : await vite.createServer({ ...config, server: { ...listen, hmr: false } });

try {
  if (!dist) await server.listen();
  await new Promise(resolveStop => {
    process.once("SIGINT", resolveStop);
    process.once("SIGTERM", resolveStop);
  });
} finally {
  await server.close();
}
