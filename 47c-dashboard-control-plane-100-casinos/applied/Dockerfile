# syntax=docker/dockerfile:1.7
# Multi-stage build for Next.js 15 standalone output.
# Target production image size ~80MB.

ARG NODE_VERSION=22-alpine

# ---------- Stage 1: deps ----------
FROM node:${NODE_VERSION} AS deps
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

# Copy only manifests for optimal layer cache
COPY package.json pnpm-lock.yaml* .npmrc* ./

RUN --mount=type=cache,id=pnpm,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# ---------- Stage 2: builder ----------
FROM node:${NODE_VERSION} AS builder
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production

RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN pnpm build

# ---------- Stage 3: runner ----------
FROM node:${NODE_VERSION} AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3010
ENV HOSTNAME=0.0.0.0

# non-root user (node:node is built-in on node:alpine images)
RUN apk add --no-cache wget tini \
    && chown -R node:node /app

USER node

# Next standalone output
COPY --chown=node:node --from=builder /app/public ./public
COPY --chown=node:node --from=builder /app/.next/standalone ./
COPY --chown=node:node --from=builder /app/.next/static ./.next/static

EXPOSE 3010

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3010/v2/dashboard/api/health || exit 1

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "server.js"]
