FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps needed by asyncpg/web3's build chain
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY config ./config

# Runs as a non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 9100

ENTRYPOINT ["python", "-m", "arbitragex_engine.main"]
CMD ["--config", "config/config.yaml"]
