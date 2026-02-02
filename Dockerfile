FROM python:3.12-slim

WORKDIR /app

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy dependency files first (for layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev deps for production)
RUN uv sync --frozen --no-dev

# Copy source code
COPY src/ ./src/

# Create directory for credentials (mounted at runtime)
RUN mkdir -p /app/credentials

# Default port
EXPOSE 8000

# Default command - credentials paths can be overridden
CMD ["uv", "run", "api-proxy", \
     "--token-file", "/app/credentials/token.json", \
     "--api-keys-file", "/app/credentials/api_keys.json", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
