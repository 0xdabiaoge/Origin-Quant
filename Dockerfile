# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

# Stamped by the release workflow (v1.0.0, v1.0.1, ...). Defaults to a clearly
# non-release value so a local build can never be mistaken for a published one.
ARG OQ_VERSION=0.0.0-dev
ARG OQ_REVISION=unknown

LABEL org.opencontainers.image.title="Origin Quant" \
      org.opencontainers.image.description="Single-operator crypto trading engine" \
      org.opencontainers.image.version="${OQ_VERSION}" \
      org.opencontainers.image.revision="${OQ_REVISION}" \
      org.opencontainers.image.source="https://github.com/0xdabiaoge/Origin-Quant" \
      org.opencontainers.image.licenses="UNLICENSED"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build deps are dropped from the final image; pyarrow ships wheels, so this is
# mostly for safety on architectures without one.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && apt-get purge -y --auto-remove build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY config ./config

# Non-root, no shell, no home write access.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin oq \
 && mkdir -p /app/data /app/logs \
 && chown -R oq:oq /app/data /app/logs

USER 10001:10001

ENV OQ_CONFIG_DIR=/app/config \
    OQ_DB_PATH=/app/data/origin.db \
    OQ_DATA_DIR=/app/data \
    OQ_LOG_LEVEL=INFO \
    OQ_VERSION=${OQ_VERSION}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["oq"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000", "--scheduler"]
