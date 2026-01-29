# Build a Gmail API Proxy Server

## Overview

Build a proxy server that sits between an AI email agent and the Gmail API. The proxy enforces capability restrictions at the API level—specifically, it allows read operations and label modifications but **blocks all email sending capabilities**.

This is necessary because Gmail's OAuth scopes don't provide fine-grained control: the `gmail.modify` scope (required for label changes) also grants send permission. The proxy provides the missing capability boundary.

## Architecture

```
Email Agent (untrusted)
    │
    │ HTTP requests (no Gmail credentials)
    ▼
gmail-api-proxy (this server)
    │
    ├──► Blocked operations → 403 Forbidden (always)
    │
    ├──► Allowed operations → [Human confirmation if enabled]
    │                              │
    │                              ├── Approved → Forward to Gmail API
    │                              └── Rejected → 403 Forbidden
    │
    │ Gmail API (OAuth with gmail.modify scope)
    ▼
Gmail
```

The email agent never receives Gmail OAuth credentials. It only knows how to talk to this proxy. A human operator can optionally review and approve operations before they are forwarded to Gmail.

## Allowed Gmail API Operations

The proxy should expose these endpoints, forwarding to the Gmail API:

### Read Operations

| Proxy Endpoint | Gmail API | Purpose |
|----------------|-----------|---------|
| `GET /gmail/v1/users/{userId}/messages` | `users.messages.list` | List/search messages |
| `GET /gmail/v1/users/{userId}/messages/{id}` | `users.messages.get` | Get message content |
| `GET /gmail/v1/users/{userId}/labels` | `users.labels.list` | List available labels |
| `GET /gmail/v1/users/{userId}/labels/{id}` | `users.labels.get` | Get label details |

### Modify Operations

| Proxy Endpoint | Gmail API | Purpose |
|----------------|-----------|---------|
| `POST /gmail/v1/users/{userId}/messages/{id}/modify` | `users.messages.modify` | Add/remove labels |
| `POST /gmail/v1/users/{userId}/messages/{id}/trash` | `users.messages.trash` | Move to trash |
| `POST /gmail/v1/users/{userId}/messages/{id}/untrash` | `users.messages.untrash` | Remove from trash |

## Blocked Operations (CRITICAL)

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

### Authentication
The proxy should:
1. Load Gmail OAuth credentials from a `token.json` file (same format as the Google Python quickstart)
2. Automatically refresh expired tokens
3. NOT expose credentials to callers in any way

The proxy itself does not need to authenticate its callers initially (it's assumed to run on a trusted local network), but structure the code so authentication middleware could be added later.

## Project Structure

```
gmail-api-proxy/
├── src/
│   └── gmail_api_proxy/
│       ├── __init__.py
│       ├── main.py           # FastAPI app, CLI argument parsing, health check
│       ├── gmail_client.py   # Gmail API wrapper
│       ├── handlers.py       # Route handlers
│       ├── models.py         # Pydantic models
│       ├── config.py         # Configuration management
│       └── confirmation.py   # Human-in-the-loop confirmation logic
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Fixtures
│   ├── test_handlers.py      # Handler tests
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

1. **Overview** — What the proxy does and why it exists (the Gmail scope limitation problem)

2. **Architecture diagram** — ASCII showing the trust boundary and confirmation flow

3. **Quick Start** — Minimal steps to get running:
   - Prerequisites (Python, uv, Gmail OAuth credentials)
   - Installation
   - Running the server (with default confirmation mode)

4. **Gmail OAuth Setup** — Step-by-step instructions for:
   - Creating a Google Cloud project
   - Enabling the Gmail API
   - Creating OAuth credentials
   - Generating `token.json`

5. **API Reference** — Document every endpoint:
   - Method and path
   - Query parameters (for list operations)
   - Request body (for modify operations)
   - Response format
   - Example curl commands

6. **Security Model** — Explain:
   - What is allowed and why
   - What is blocked and why
   - The allowlist approach
   - How credentials are protected
   - How human-in-the-loop confirmation adds an additional safety layer

7. **Human-in-the-Loop Confirmation** — Document the confirmation feature:
   - Purpose and use cases
   - Command-line options (`--confirm-all`, `--confirm-modify`, `--no-confirm`)
   - Default behavior (confirmation required for modify operations)
   - What the confirmation prompt looks like
   - How to approve or reject requests
   - Note that blocked operations are always blocked regardless of confirmation settings

8. **Configuration** — Environment variables and config options

9. **Development** — How to:
   - Set up dev environment
   - Run tests
   - Run linting

10. **Deployment Considerations** — Notes on:
    - Running in production
    - Choosing the appropriate confirmation mode for your use case
    - Adding authentication
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

#### 1. Handler Tests (`test_handlers.py`)
Test each allowed endpoint:
- Returns correct status codes
- Properly forwards query parameters
- Properly forwards request bodies
- Returns Gmail API responses correctly
- Handles Gmail API errors gracefully

#### 2. Gmail Client Tests (`test_gmail_client.py`)
Test the Gmail API wrapper:
- Token loading
- Token refresh
- API call construction
- Error handling

#### 3. Security Tests (`test_security.py`)
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

#### 4. Confirmation Tests (`test_confirmation.py`)
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

#### 5. Documentation Tests (`test_docs.py`)
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
- Log all requests (method, path, status code)
- Log blocked attempts at WARNING level
- Log confirmation prompts and decisions (approved/rejected) at INFO level
- Log Gmail API errors at ERROR level
- Never log request/response bodies (may contain email content)

### Request Forwarding
For allowed endpoints, the proxy should:
1. Validate the request path against the allowlist
2. If confirmation is required for this request (based on mode and operation type):
   a. Display the confirmation prompt to the operator
   b. Wait for operator response
   c. If rejected, return 403 Forbidden immediately
3. Forward query parameters unchanged
4. Forward request body unchanged (for POST/PUT)
5. Add OAuth credentials to the outgoing request
6. Return the Gmail API response unchanged

### Path Handling
The proxy mirrors Gmail API paths exactly. This makes it a drop-in replacement—clients can use standard Gmail client libraries pointed at the proxy URL instead of `https://gmail.googleapis.com`.

## Deliverables Checklist

- [ ] `pyproject.toml` with all dependencies and metadata
- [ ] `src/gmail_api_proxy/` package with all modules including `confirmation.py`
- [ ] Command-line argument parsing for `--confirm-all`, `--confirm-modify`, `--no-confirm`
- [ ] Human-in-the-loop confirmation prompts working correctly
- [ ] Complete test suite in `tests/` including `test_confirmation.py`
- [ ] `README.md` with all sections described above (including confirmation documentation)
- [ ] `CLAUDE.md` with repo-specific instructions for AI agents
- [ ] `.gitignore` appropriate for Python projects
- [ ] `ruff.toml` or ruff config in `pyproject.toml`
- [ ] All tests passing
- [ ] All linting passing
- [ ] Example `token.json.example` showing expected format (with placeholder values)

## Verification

After implementation, verify:

1. **Functionality**: Run the server and test allowed operations work
2. **Security**: Verify blocked operations return 403
3. **Confirmation modes**:
   - Default mode (no args): Modify operations prompt for confirmation, read operations proceed immediately
   - `--confirm-all`: All operations prompt for confirmation
   - `--confirm-modify`: Same as default
   - `--no-confirm`: No operations prompt for confirmation
   - Blocked operations are always blocked, never prompt for confirmation
4. **Tests**: `uv run pytest` passes
5. **Linting**: `uv run ruff check .` passes
6. **Docs**: README accurately reflects implementation including confirmation feature
