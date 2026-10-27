# Web image — the Next.js app as a self-contained Node server, for the
# self-hosted and dedicated installs (Cloud's web app stays on Vercel).
#
# API_PROXY_TARGET is read when `next build` runs: Next bakes the /api rewrite
# into the build, so it is a build argument, not a runtime setting. The default
# is the backend's service name in deploy/selfhost/docker-compose.yml.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG API_PROXY_TARGET=http://api:8000
ENV API_PROXY_TARGET=$API_PROXY_TARGET \
    NEXT_STANDALONE=1 \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
