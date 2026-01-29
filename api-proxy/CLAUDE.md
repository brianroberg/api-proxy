# CLAUDE.md - Instructions for AI Agents

This file provides context for AI agents working on the api-proxy codebase.

## Project Overview

api-proxy is a security gateway that sits between AI agents and backend APIs. It enforces capability restrictions that OAuth scopes cannot provide—specifically for Gmail, allowing read and label operations while blocking all email sending.

## Architecture

```
src/api_proxy/
├── main.py           # FastAPI app, CLI, middleware for blocking
├── auth.py           # API key authentication
├── keys.py           # API key management CLI
├── config.py         # Configuration management
├── models.py         # Shared Pydantic models
├── confirmation.py   # Human-in-the-loop confirmation
└── gmail/
    ├── client.py     # Gmail API wrapper using httpx
    ├── handlers.py   # FastAPI route handlers
    └── models.py     # Gmail-specific Pydantic models
```

## Key Design Decisions

### Allowlist Approach
Operations are blocked by default. Only explicitly allowed endpoints work. This is enforced in middleware (`main.py`) before routes are matched.

### Two-Layer Security
1. API key authentication (validates caller identity)
2. Operation restrictions (enforces allowlist)

### Error Differentiation
Errors clearly indicate origin:
- `proxy_error`: Problems in the proxy itself
- `backend_error`: Errors from Gmail API
- `auth_error`: Authentication failures
- `forbidden`: Blocked operations

### Confirmation Flow
- Uses `asyncio.Lock` for single-request blocking
- `asyncio.to_thread(input, ...)` for async stdin
- Configurable timeout (default 5 minutes)

## Running Commands

```bash
# Install dependencies
uv sync

# Run the server
uv run api-proxy

# Run tests
uv run pytest

# Run linting
uv run ruff check .

# Format code
uv run ruff format .

# Manage API keys
uv run api-proxy-keys create --name "test"
uv run api-proxy-keys list
```

## Testing

Tests use pytest with pytest-httpx for mocking Gmail API calls. Key test files:

- `test_auth.py` - API key authentication
- `test_keys.py` - Key management CLI
- `test_gmail_handlers.py` - Gmail endpoints
- `test_security.py` - Blocked operations
- `test_confirmation.py` - Confirmation feature
- `test_docs.py` - Documentation sync

Run a specific test:
```bash
uv run pytest tests/test_security.py -v
```

## Common Tasks

### Adding a New Allowed Endpoint

1. Add the route handler in `gmail/handlers.py`
2. Add the pattern to `is_allowed_path()` in `main.py`
3. Add tests in `test_gmail_handlers.py`
4. Document in README.md

### Adding a New Blocked Endpoint

1. Add the pattern to `BLOCKED_PATHS` in `main.py`
2. Add tests in `test_security.py`
3. Document in README.md under "Blocked Operations"

### Modifying Confirmation Behavior

- Mode logic: `confirmation.py` → `requires_confirmation()`
- Prompt formatting: `confirmation.py` → `_format_prompt()`
- CLI args: `main.py` → `parse_args()`

## Important Invariants

1. **Blocked operations NEVER prompt for confirmation** - they are rejected immediately
2. **API keys file is reloaded on every request** - changes take effect without restart
3. **Health endpoint requires no authentication** - for load balancer checks
4. **Errors never leak sensitive data** - no full API keys, no email content

## File Locations

- API keys: `api_keys.json` (or `--api-keys-file`)
- OAuth token: `token.json` (or `--token-file`)
- Both should have restricted permissions (600)

## Dependencies

Core:
- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `httpx` - Async HTTP client for Gmail API
- `google-auth` - OAuth token management
- `pydantic` - Data validation

Dev:
- `pytest` - Testing
- `pytest-asyncio` - Async test support
- `pytest-httpx` - HTTP mocking
- `ruff` - Linting and formatting
