/** Watch course files outside Vite's root so its glob HMR sees additions and deletions. */
export function courseFrontendWatch(frontendDirs) {
  return {
    name: "course-frontend-watch",
    configureServer(server) {
      server.watcher.add(frontendDirs);
    },
  };
}
