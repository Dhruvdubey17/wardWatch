# The dashboard as a standalone Next.js server. Build context is frontend/.
#   docker build -t wardwatch-frontend frontend
# Next.js fixes rewrites at build time, so the FHIR service's address inside
# the Compose network is a build argument.

FROM node:22-slim AS build
ARG WARDWATCH_API_URL=http://fhir-service:8000
ENV WARDWATCH_API_URL=$WARDWATCH_API_URL NEXT_TELEMETRY_DISABLED=1
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM node:22-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
