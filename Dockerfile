# Build stage - install the locked, hash-checked dependency set
FROM python:3.12-slim@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe AS builder

WORKDIR /app

# Install the pinned uv release used across CI
RUN pip install --no-cache-dir uv==0.13.1

# Copy dependency files
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Runtime dependencies come only from uv.lock, with hashes; the project itself
# is installed without resolving anything further.
RUN uv export --locked --no-dev --no-emit-project --format requirements-txt \
        --output-file /tmp/requirements.txt \
    && uv pip install --system --require-hashes -r /tmp/requirements.txt \
    && uv pip install --system --no-deps .

# Runtime stage - minimal image
FROM python:3.12-slim@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/pmcp /usr/local/bin/

# Create a non-root user with a fixed UID (the image smoke asserts UID 1000)
RUN useradd --uid 1000 --create-home --shell /bin/bash appuser
USER appuser

# Set environment defaults
ENV PMCP_LOG_LEVEL=info
ENV PYTHONUNBUFFERED=1

# Health check placeholder (MCP uses stdio, not HTTP)
# HEALTHCHECK --interval=30s --timeout=3s CMD echo "ok"

ENTRYPOINT ["pmcp"]
