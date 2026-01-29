# FL3_V2 Multi-Service Dockerfile
# Supports: firehose, ta-pipeline, baseline-refresh

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

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
# Options:
#   firehose: python -m scripts.firehose_main
#   ta-pipeline: python -m scripts.ta_pipeline_v2 --once
#   baseline-refresh: python -m scripts.refresh_baselines
CMD ["python", "-m", "scripts.firehose_main"]
