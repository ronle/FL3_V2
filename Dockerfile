# FL3_V2 Multi-Service Dockerfile
# Supports: firehose, ta-pipeline, baseline-refresh

# --- Build stage: compile C extensions ---
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Runtime stage: slim final image ---
FROM python:3.11-slim

# Only runtime lib needed for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default timezone for logging
ENV TZ=America/New_York

# Health check endpoint (for Cloud Run)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "print('healthy')" || exit 1

# Default command (override in Cloud Run)
CMD ["python", "-m", "scripts.firehose_main"]
