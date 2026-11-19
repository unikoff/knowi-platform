FROM node:24.18.0-bookworm-slim AS build

WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
RUN npm audit --audit-level=critical

COPY apps/web/ ./
COPY courses/ /app/courses/
RUN node tools/course-registry.mjs \
    && npm exec -- tsc --noEmit -p config/tsconfig.json \
    && ./node_modules/.bin/vite build --config config/vite.config.ts --configLoader native --outDir /tmp/frontend-dist

FROM nginx:1.30.7-alpine3.24-slim AS runtime

# The base tag still carries zlib 1.3.2; Alpine fixes CVE-2026-4738 in 1.4-r0.
RUN apk add --no-cache 'zlib>=1.4-r0'

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /tmp/frontend-dist/ /usr/share/nginx/html/

EXPOSE 8080
