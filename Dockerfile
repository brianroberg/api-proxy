FROM python:3.12-slim

WORKDIR /app

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy dependency files first (for layer caching)
COPY pyproject.toml uv.lock README.md ./

# Install dependencies only (without installing the project itself)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code BEFORE installing the project
COPY src/ ./src/

# Install the project (source must be present for correct wheel build)
RUN uv sync --frozen --no-dev && \
    uv run python -c "from api_proxy.models import HealthResponse; assert 'version' in HealthResponse.model_fields, 'Build verification failed: old code installed'"

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
