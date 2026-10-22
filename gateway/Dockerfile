# Stage 1: build the React frontend
FROM node:22.12.0-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
RUN npm run build

# Stage 2: gateway (serves the built UI and proxies /api with zero-trust checks)
FROM node:22.12.0-alpine
ENV NODE_ENV=production
WORKDIR /app
COPY gateway/package*.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY gateway/src ./src
COPY --from=web /web/dist ./public
ENV STATIC_DIR=/app/public PORT=8443
USER node
EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8443/healthz || exit 1
CMD ["node", "src/server.js"]
