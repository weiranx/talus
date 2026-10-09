# Talus web: Next.js on :3000. ENGINE_URL is needed at build time too, since rewrites are baked in.
FROM node:22-slim AS build
WORKDIR /app
ARG ENGINE_URL=http://engine:8000
ENV ENGINE_URL=$ENGINE_URL NEXT_TELEMETRY_DISABLED=1
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build && npm prune --omit=dev

FROM node:22-slim
WORKDIR /app
ARG ENGINE_URL=http://engine:8000
ENV NODE_ENV=production ENGINE_URL=$ENGINE_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app ./
EXPOSE 3000
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
