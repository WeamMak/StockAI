FROM node:20.20.2-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS builder

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-audit --no-fund

COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

FROM nginx:1.29.8-alpine3.23@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de AS runtime

COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=builder --chown=101:101 /build/frontend/dist /usr/share/nginx/html

USER 101:101
EXPOSE 8080
VOLUME ["/tmp"]
STOPSIGNAL SIGQUIT

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/health/live"]

ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
