FROM python:3.12-slim

WORKDIR /app

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy dependency files first (for layer caching)
COPY pyproject.toml uv.lock README.md ./

# Install dependencies (no dev deps for production)
RUN uv sync --frozen --no-dev

# Copy source code
COPY src/ ./src/

# Create directories for credentials and logs (mounted at runtime)
RUN mkdir -p /app/credentials /app/logs

# Default port
EXPOSE 8000

# Default command - credentials paths can be overridden
# Uses --web-confirm for web-based approval UI (accessible at /approval/)
CMD ["uv", "run", "api-proxy", \
     "--token-file", "/app/credentials/token.json", \
     "--api-keys-file", "/app/credentials/api_keys.json", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--log-file", "/app/logs/api-proxy.log", \
     "--web-confirm"]
