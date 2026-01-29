# Build an API Proxy Server

## Overview

Build a proxy server that sits between AI agents and backend APIs. The proxy enforces capability restrictions at the API level—allowing only specific operations while blocking others that would be dangerous in agent hands.

The initial implementation focuses on Gmail, where it allows read operations and label modifications but **blocks all email sending capabilities**. This is necessary because Gmail's OAuth scopes don't provide fine-grained control: the `gmail.modify` scope (required for label changes) also grants send permission. The proxy provides the missing capability boundary.

The architecture is designed to support additional APIs in the future—both Google APIs (Calendar, Drive) and non-Google APIs.

## Architecture

```
AI Agent (untrusted)
    │
    │ HTTP requests with API key (no backend credentials)
    ▼
api-proxy (this server)
    │
    ├──► Invalid/missing API key → 401 Unauthorized
    │
    ├──► Blocked operations → 403 Forbidden (always)
    │
    ├──► Allowed operations → [Human confirmation if enabled]
    │                              │
    │                              ├── Approved → Forward to backend API
    │                              └── Rejected → 403 Forbidden
    │
    │ Backend APIs (with credentials)
    ▼
Backend Services (Gmail, Calendar, etc.)
```

The AI agent never receives backend API credentials. It only knows how to talk to this proxy using its API key. A human operator can optionally review and approve operations before they are forwarded to the backend.

## Agent Authentication

Agents must authenticate to the proxy using an API key. This provides:
- **Access control**: Only authorized agents can use the proxy
- **Audit trail**: All requests are associated with a specific API key
- **Revocation**: Compromised or retired agents can be disabled instantly

### API Key Format

API keys are opaque tokens with the prefix `aproxy_` followed by 32 random alphanumeric characters:
```
aproxy_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

### Authentication Header

Agents include the API key in the `Authorization` header:
```
Authorization: Bearer aproxy_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

### Key Storage

API keys are stored in a JSON file (`api_keys.json` by default) with metadata:
```json
{
  "keys": {
    "aproxy_a1b2c3d4...": {
      "name": "email-agent-prod",
      "created_at": "2025-01-15T10:30:00Z",
      "last_used_at": "2025-01-20T14:22:00Z",
      "enabled": true
    }
  }
}
```

The file path can be configured via `--api-keys-file` or the `API_KEYS_FILE` environment variable.

### Key Management Script

The proxy includes a CLI tool for managing API keys:

```bash
# Create a new API key
uv run api-proxy-keys create --name "email-agent-prod"
# Output: Created API key 'email-agent-prod': aproxy_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6

# List all API keys
uv run api-proxy-keys list
# Output:
# NAME              CREATED              LAST USED            ENABLED
# email-agent-prod  2025-01-15 10:30:00  2025-01-20 14:22:00  yes
# calendar-agent    2025-01-18 09:00:00  never                yes

# Disable an API key (keeps history, but rejects requests)
uv run api-proxy-keys disable --name "email-agent-prod"

# Re-enable a disabled API key
uv run api-proxy-keys enable --name "email-agent-prod"

# Revoke an API key (permanent deletion)
uv run api-proxy-keys revoke --name "email-agent-prod"

# Show details for a specific key
uv run api-proxy-keys show --name "email-agent-prod"
```

### Authentication Errors

| Scenario | Status Code | Response |
|----------|-------------|----------|
| Missing `Authorization` header | 401 | `{"error": "Missing Authorization header"}` |
| Invalid format (not `Bearer <key>`) | 401 | `{"error": "Invalid Authorization header format"}` |
| Unknown API key | 401 | `{"error": "Invalid API key"}` |
| Disabled API key | 403 | `{"error": "API key is disabled"}` |

## Gmail API Operations

The initial implementation proxies the Gmail API. Additional APIs can be added following the same pattern.

### Allowed Operations

The proxy should expose these endpoints, forwarding to the Gmail API:

#### Read Operations

| Proxy Endpoint | Gmail API | Purpose |
|----------------|-----------|---------|
| `GET /gmail/v1/users/{userId}/messages` | `users.messages.list` | List/search messages |
| `GET /gmail/v1/users/{userId}/messages/{id}` | `users.messages.get` | Get message content |
| `GET /gmail/v1/users/{userId}/labels` | `users.labels.list` | List available labels |
| `GET /gmail/v1/users/{userId}/labels/{id}` | `users.labels.get` | Get label details |

#### Modify Operations

| Proxy Endpoint | Gmail API | Purpose |
|----------------|-----------|---------|
| `POST /gmail/v1/users/{userId}/messages/{id}/modify` | `users.messages.modify` | Add/remove labels |
| `POST /gmail/v1/users/{userId}/messages/{id}/trash` | `users.messages.trash` | Move to trash |
| `POST /gmail/v1/users/{userId}/messages/{id}/untrash` | `users.messages.untrash` | Remove from trash |

### Blocked Operations (CRITICAL)

The proxy MUST reject these with `403 Forbidden`:

- `POST /gmail/v1/users/{userId}/messages/send` — Send a message
- `POST /gmail/v1/users/{userId}/drafts` — Create a draft
- `POST /gmail/v1/users/{userId}/drafts/send` — Send a draft
- `PUT /gmail/v1/users/{userId}/drafts/{id}` — Update a draft
- `DELETE /gmail/v1/users/{userId}/drafts/{id}` — Delete a draft
- `POST /gmail/v1/users/{userId}/messages/import` — Import a message
- `POST /gmail/v1/users/{userId}/messages/insert` — Insert a message
- Any other endpoint not explicitly allowed

Use an allowlist approach: reject anything not on the allowed list rather than trying to enumerate all blocked endpoints.

**Important**: Blocked operations are ALWAYS blocked, regardless of any command-line options or confirmation settings. The human-in-the-loop confirmation feature (described below) does not apply to blocked operations.

## Human-in-the-Loop Confirmation

The proxy supports an optional human-in-the-loop confirmation step before forwarding requests to the Gmail API. This allows a human operator to review and approve operations before they are executed.

### Confirmation Modes

The confirmation behavior is controlled via command-line options when starting the proxy:

| Option | Behavior |
|--------|----------|
| `--confirm-all` | Require confirmation before forwarding ANY request (read or modify). Useful for debugging or auditing all API traffic. |
| `--confirm-modify` | Require confirmation only for Modify operations (label changes, trash/untrash). **This is the default if no option is specified.** |
| `--no-confirm` | Do not require any confirmation. All allowed requests are forwarded immediately. |

These options are mutually exclusive. If multiple are specified, the proxy should exit with an error.

### Confirmation Flow

When confirmation is required for a request:

1. The proxy logs the pending request details to the console:
   - HTTP method and path
   - Query parameters (if any)
   - Request body summary (for modify operations: labels being added/removed)
   - **Never log full email content**

2. The proxy prompts the operator on stdin:
   ```
   [CONFIRM] POST /gmail/v1/users/me/messages/abc123/modify
     Add labels: STARRED
     Remove labels: UNREAD
   Allow this request? [y/N]:
   ```

3. The operator responds:
   - `y` or `Y` — Forward the request to Gmail API
   - `n`, `N`, or empty/Enter — Reject the request and return `403 Forbidden` to the caller with a message indicating the request was rejected by the operator

4. The proxy logs the decision (approved/rejected) and proceeds accordingly.

### Implementation Notes for Confirmation

- The confirmation prompt MUST be synchronous and blocking—only one request can be pending confirmation at a time
- While a request is pending confirmation, other incoming requests should queue (the server remains responsive but confirmation-required requests wait)
- Consider a configurable timeout for confirmation prompts (default: no timeout, wait indefinitely)
- The `--confirm-all` mode is intended for debugging and low-traffic scenarios; it is not practical for high-throughput use
- Blocked operations are NEVER subject to confirmation—they are rejected immediately without prompting

## Technical Requirements

### Python Version
Use Python 3.12 (latest stable version with full library compatibility).

### Package Management
Use `uv` for package management. Create a proper `pyproject.toml` with:
- Project metadata
- Dependencies
- Optional `[dev]` dependencies for testing/linting
- Scripts entry point

### Framework
Use FastAPI with:
- Proper request/response models using Pydantic
- Async handlers
- OpenAPI documentation auto-generation
- Health check endpoint at `GET /health`

### Dependencies
Core dependencies:
- `fastapi`
- `uvicorn`
- `httpx` (for async requests to Gmail API)
- `google-auth` (for OAuth credential handling)
- `google-auth-oauthlib`
- `google-auth-httplib2`
- `google-api-python-client`

Dev dependencies:
- `pytest`
- `pytest-asyncio`
- `pytest-httpx` (for mocking HTTP requests)
- `ruff`

### Linting
Use `ruff` for linting and formatting. Include a `ruff.toml` or configure in `pyproject.toml` with sensible defaults.

### Google OAuth (Backend Authentication)
The proxy should:
1. Load Google OAuth credentials from a `token.json` file (same format as the Google Python quickstart)
2. Automatically refresh expired tokens
3. NOT expose credentials to callers in any way

### Agent Authentication (Frontend Authentication)
The proxy requires all callers to authenticate with an API key:
1. Load API keys from `api_keys.json` (or path specified by `--api-keys-file`)
2. Validate the `Authorization: Bearer <key>` header on every request (except `/health`)
3. Update `last_used_at` timestamp on successful authentication
4. Return appropriate error codes (401 for invalid/missing, 403 for disabled)

## Project Structure

```
api-proxy/
├── src/
│   └── api_proxy/
│       ├── __init__.py
│       ├── main.py           # FastAPI app, CLI argument parsing, health check
│       ├── auth.py           # API key authentication middleware
│       ├── keys.py           # API key management (CLI tool)
│       ├── gmail/
│       │   ├── __init__.py
│       │   ├── client.py     # Gmail API wrapper
│       │   ├── handlers.py   # Gmail route handlers
│       │   └── models.py     # Gmail-specific Pydantic models
│       ├── models.py         # Shared Pydantic models
│       ├── config.py         # Configuration management
│       └── confirmation.py   # Human-in-the-loop confirmation logic
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Fixtures
│   ├── test_auth.py          # API key authentication tests
│   ├── test_keys.py          # Key management CLI tests
│   ├── test_gmail_handlers.py # Gmail handler tests
│   ├── test_gmail_client.py  # Gmail client tests
│   ├── test_security.py      # Security/blocking tests
│   ├── test_confirmation.py  # Confirmation feature tests
│   └── test_docs.py          # Documentation tests
├── pyproject.toml
├── README.md
├── CLAUDE.md                 # Instructions for AI agents working on this repo
└── .gitignore
```

## README Requirements

The README should be thorough and include:

1. **Overview** — What the proxy does and why it exists (API scope limitations, with Gmail as the primary example)

2. **Architecture diagram** — ASCII showing the trust boundary, API key authentication, and confirmation flow

3. **Quick Start** — Minimal steps to get running:
   - Prerequisites (Python, uv, Google OAuth credentials)
   - Installation
   - Creating an API key for your agent
   - Running the server (with default confirmation mode)
   - Making your first request (with API key in Authorization header)

4. **Agent Authentication** — Document the API key system:
   - Why API keys are required
   - API key format and header format
   - Using the key management CLI (`api-proxy-keys`)
   - Creating, listing, disabling, and revoking keys
   - Key storage file format and location
   - Authentication error responses

5. **Google OAuth Setup** — Step-by-step instructions for:
   - Creating a Google Cloud project
   - Enabling required APIs (Gmail, Calendar, etc.)
   - Creating OAuth credentials
   - Generating `token.json`

6. **Gmail API Reference** — Document every Gmail endpoint:
   - Method and path
   - Query parameters (for list operations)
   - Request body (for modify operations)
   - Response format
   - Example curl commands (including Authorization header)

7. **Security Model** — Explain:
   - The two-layer security model (API keys + operation restrictions)
   - What is allowed and why
   - What is blocked and why
   - The allowlist approach
   - How Google credentials are protected
   - How human-in-the-loop confirmation adds an additional safety layer

8. **Human-in-the-Loop Confirmation** — Document the confirmation feature:
   - Purpose and use cases
   - Command-line options (`--confirm-all`, `--confirm-modify`, `--no-confirm`)
   - Default behavior (confirmation required for modify operations)
   - What the confirmation prompt looks like
   - How to approve or reject requests
   - Note that blocked operations are always blocked regardless of confirmation settings

9. **Configuration** — Environment variables and config options:
   - `--api-keys-file` / `API_KEYS_FILE`
   - Token file location
   - Port and host settings

10. **Development** — How to:
    - Set up dev environment
    - Run tests
    - Run linting

11. **Adding New APIs** — Brief guide for extending to support additional APIs (reserved for future expansion)

12. **Deployment Considerations** — Notes on:
    - Running in production
    - Protecting the API keys file
    - Choosing the appropriate confirmation mode for your use case
    - Logging and monitoring

## Test Suite Requirements

### Testing Patterns
First, clone the datasette-enrichments repository to study its testing patterns:

```bash
git clone https://github.com/datasette/datasette-enrichments.git /tmp/datasette-enrichments
```

Review the test structure in `/tmp/datasette-enrichments/tests/` and follow similar patterns for:
- Fixture organization in `conftest.py`
- Test file organization
- Mocking external services
- Async test handling

### Test Categories

#### 1. Authentication Tests (`test_auth.py`)
Test the API key authentication middleware:

**Valid authentication:**
- Request with valid API key succeeds
- API key `last_used_at` is updated on successful request

**Invalid authentication:**
- Missing `Authorization` header returns 401
- Malformed header (not `Bearer <key>`) returns 401
- Unknown API key returns 401
- Disabled API key returns 403

**Edge cases:**
- Empty API key returns 401
- Whitespace-only API key returns 401
- API key with wrong prefix returns 401

#### 2. Key Management Tests (`test_keys.py`)
Test the API key management CLI:

**Create command:**
- Creates key with valid name
- Generated key has correct format (`aproxy_` + 32 chars)
- Stores key with correct metadata (name, created_at, enabled=true)
- Rejects duplicate names
- Rejects invalid names (empty, too long, special characters)

**List command:**
- Lists all keys with correct columns
- Shows "never" for keys that haven't been used
- Handles empty key file gracefully

**Disable/Enable commands:**
- Disable sets `enabled: false`
- Enable sets `enabled: true`
- Operations on non-existent key show error

**Revoke command:**
- Removes key from file entirely
- Revoke on non-existent key shows error

**Show command:**
- Displays all metadata for a key
- Masks the actual key value (shows only last 4 chars)

**File handling:**
- Creates key file if it doesn't exist
- Handles corrupted/invalid JSON gracefully
- Preserves existing keys when adding new ones

#### 3. Gmail Handler Tests (`test_gmail_handlers.py`)
Test each allowed Gmail endpoint:
- Returns correct status codes
- Properly forwards query parameters
- Properly forwards request bodies
- Returns Gmail API responses correctly
- Handles Gmail API errors gracefully

#### 4. Gmail Client Tests (`test_gmail_client.py`)
Test the Gmail API wrapper:
- Token loading
- Token refresh
- API call construction
- Error handling

#### 5. Security Tests (`test_security.py`)
**Critical** — Test that blocked operations are actually blocked:
- `POST /gmail/v1/users/me/messages/send` returns 403
- `POST /gmail/v1/users/me/drafts` returns 403
- `POST /gmail/v1/users/me/drafts/send` returns 403
- Unknown endpoints return 403 or 404
- Verify the allowlist is exhaustive

Test attempted bypasses:
- URL encoding tricks
- Case variations
- Extra path segments
- Query parameter injection

Test that blocked operations remain blocked regardless of confirmation mode:
- Blocked operations return 403 even with `--no-confirm`
- Blocked operations never trigger a confirmation prompt

Test that authentication is enforced on all endpoints:
- All proxy endpoints require authentication (not just Gmail)
- Health check endpoint does NOT require authentication
- Blocked operations return 401 before checking if operation is allowed (fail fast)

#### 6. Confirmation Tests (`test_confirmation.py`)
Test the human-in-the-loop confirmation feature:

**Command-line argument parsing:**
- `--confirm-all` sets the correct mode
- `--confirm-modify` sets the correct mode
- `--no-confirm` sets the correct mode
- Default (no option) uses `--confirm-modify` behavior
- Multiple conflicting options cause an error

**Confirmation behavior by mode:**
- With `--confirm-all`: Read operations require confirmation
- With `--confirm-all`: Modify operations require confirmation
- With `--confirm-modify`: Read operations proceed without confirmation
- With `--confirm-modify`: Modify operations require confirmation
- With `--no-confirm`: Read operations proceed without confirmation
- With `--no-confirm`: Modify operations proceed without confirmation

**Confirmation prompt handling:**
- Approved requests (`y` or `Y`) are forwarded to Gmail API
- Rejected requests (`n`, `N`, or empty) return 403 to caller
- Confirmation prompt includes method and path
- Confirmation prompt includes relevant request details (labels being modified)
- Confirmation prompt does NOT include full email content

**Mocking strategy for confirmation tests:**
- Mock stdin to simulate user input (`y`, `n`, empty)
- Mock stdout/stderr to verify prompt output
- Use fixtures to test different confirmation modes

#### 7. Documentation Tests (`test_docs.py`)
Use pytest's built-in subtests feature (available in pytest 9.0+) to verify documentation completeness.

Follow the pattern described at https://til.simonwillison.net/pytest/subtests:

```python
def test_all_endpoints_documented(subtests):
    """Verify every endpoint in the code is documented in README."""
    readme_content = Path("README.md").read_text()
    
    # Extract endpoints from the actual route definitions
    endpoints = extract_endpoints_from_code()
    
    for endpoint in endpoints:
        with subtests.test(endpoint=endpoint):
            assert endpoint in readme_content, f"Endpoint {endpoint} not documented in README"


def test_all_documented_endpoints_exist(subtests):
    """Verify every endpoint documented in README exists in code."""
    # Extract endpoints mentioned in README
    documented_endpoints = extract_endpoints_from_readme()
    
    # Extract actual endpoints from code
    actual_endpoints = extract_endpoints_from_code()
    
    for endpoint in documented_endpoints:
        with subtests.test(endpoint=endpoint):
            assert endpoint in actual_endpoints, f"Documented endpoint {endpoint} not found in code"
```

This ensures:
- Every endpoint in the code is documented
- Every documented endpoint actually exists
- Documentation stays in sync with implementation

### Mocking Strategy
Use `pytest-httpx` to mock Gmail API responses. Never make real API calls in tests.

Create fixtures for common Gmail API responses:
- Message list response
- Single message response
- Label list response
- Modify response
- Error responses (404, 403, 500)

## Implementation Notes

### Error Handling
- Gmail API errors should be forwarded to the caller with appropriate status codes
- Proxy-level errors (blocked operations, auth failures) should have clear error messages
- Never leak sensitive information in error messages

### Logging
- Log all requests (method, path, status code, API key name)
- Log authentication failures at WARNING level (include key prefix if available, never full key)
- Log blocked attempts at WARNING level
- Log confirmation prompts and decisions (approved/rejected) at INFO level
- Log backend API errors at ERROR level
- Never log request/response bodies (may contain email content)
- Never log full API keys (only the key name or last 4 characters)

### Request Forwarding
For allowed endpoints, the proxy should:
1. Validate API key from `Authorization` header (return 401/403 if invalid)
2. Validate the request path against the allowlist (return 403 if blocked)
3. If confirmation is required for this request (based on mode and operation type):
   a. Display the confirmation prompt to the operator
   b. Wait for operator response
   c. If rejected, return 403 Forbidden immediately
4. Forward query parameters unchanged
5. Forward request body unchanged (for POST/PUT)
6. Add backend credentials to the outgoing request (Google OAuth for Gmail)
7. Return the backend API response unchanged

### Path Handling
The proxy mirrors Gmail API paths exactly. This makes it a drop-in replacement—clients can use standard Gmail client libraries pointed at the proxy URL instead of `https://gmail.googleapis.com`.

## Deliverables Checklist

- [ ] `pyproject.toml` with all dependencies, metadata, and script entry points
- [ ] `src/api_proxy/` package with all modules
- [ ] `src/api_proxy/auth.py` — API key authentication middleware
- [ ] `src/api_proxy/keys.py` — API key management CLI tool
- [ ] `src/api_proxy/gmail/` — Gmail-specific handlers and client
- [ ] Script entry points in `pyproject.toml`:
  - `api-proxy` — main server
  - `api-proxy-keys` — key management CLI
- [ ] Command-line argument parsing for `--confirm-all`, `--confirm-modify`, `--no-confirm`
- [ ] Command-line argument `--api-keys-file` for specifying key storage location
- [ ] Human-in-the-loop confirmation prompts working correctly
- [ ] Complete test suite in `tests/` including `test_auth.py` and `test_keys.py`
- [ ] `README.md` with all sections described above (including authentication documentation)
- [ ] `CLAUDE.md` with repo-specific instructions for AI agents
- [ ] `.gitignore` appropriate for Python projects
- [ ] `ruff.toml` or ruff config in `pyproject.toml`
- [ ] All tests passing
- [ ] All linting passing
- [ ] Example `token.json.example` showing expected format (with placeholder values)
- [ ] Example `api_keys.json.example` showing expected format (with placeholder values)

## Verification

After implementation, verify:

1. **Key management**:
   - `uv run api-proxy-keys create --name test-agent` creates a key
   - `uv run api-proxy-keys list` shows the key
   - `uv run api-proxy-keys disable --name test-agent` disables it
   - `uv run api-proxy-keys enable --name test-agent` re-enables it
2. **Authentication**:
   - Requests without API key return 401
   - Requests with invalid API key return 401
   - Requests with disabled API key return 403
   - Requests with valid API key proceed to handler
3. **Functionality**: Run the server and test allowed operations work (with valid API key)
4. **Security**: Verify blocked operations return 403 (even with valid API key)
5. **Confirmation modes**:
   - Default mode (no args): Modify operations prompt for confirmation, read operations proceed immediately
   - `--confirm-all`: All operations prompt for confirmation
   - `--confirm-modify`: Same as default
   - `--no-confirm`: No operations prompt for confirmation
   - Blocked operations are always blocked, never prompt for confirmation
6. **Tests**: `uv run pytest` passes
7. **Linting**: `uv run ruff check .` passes
8. **Docs**: README accurately reflects implementation including authentication and confirmation features
