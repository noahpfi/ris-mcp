# landing page + MCP share one hostname -> one image, no separate npm run build step
FROM node:22-slim AS web
WORKDIR /web
COPY website/package.json website/package-lock.json ./
RUN npm ci
COPY website/ ./
RUN npm run build


FROM python:3.12-slim

# no bytecode, rootfs read-only in compose
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RIS_STATIC_DIR=/app/website

WORKDIR /app

# hash-pinned; all deps ship manylinux wheels -> no build toolchain installed
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY src ./src
COPY --from=web /web/dist ./website

RUN groupadd --gid 10001 ris \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin ris
USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-m", "src.healthcheck"]

CMD ["python", "-m", "src.server"]
