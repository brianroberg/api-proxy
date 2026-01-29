# API Proxy

A proxy server that enforces capability restrictions between AI agents and backend APIs. The proxy provides fine-grained access control that OAuth scopes cannot—allowing specific operations while blocking others that would be dangerous in agent hands.

## Overview

The initial implementation focuses on Gmail, where it allows read operations and label modifications but **blocks all email sending capabilities**. This is necessary because Gmail's OAuth scopes don't provide fine-grained control: the `gmail.modify` scope (required for label changes) also grants send permission. The proxy provides the missing capability boundary.

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

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- Google OAuth credentials (see [Google OAuth Setup](#google-oauth-setup))

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd api-proxy

# Install dependencies
uv sync
```

### Create an API Key

```bash
uv run api-proxy-keys create --name "my-agent"
# Output: Created API key 'my-agent': aproxy_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

Save this key securely—it won't be shown again.

### Start the Server

```bash
# Default mode: confirmation required for modify operations
uv run api-proxy

# Or disable confirmation for testing
uv run api-proxy --no-confirm
```

### Make Your First Request

```bash
curl -X GET "http://localhost:8000/gmail/v1/users/me/labels" \
  -H "Authorization: Bearer aproxy_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
```

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

### Key Management CLI

The proxy includes a CLI tool for managing API keys:

```bash
# Create a new API key
uv run api-proxy-keys create --name "email-agent-prod"
# Output: Created API key 'email-agent-prod': aproxy_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6

# List all API keys
uv run api-proxy-keys list
# Output:
# NAME                 CREATED              LAST USED            ENABLED
# email-agent-prod     2025-01-15 10:30:00  2025-01-20 14:22:00  yes

# Disable an API key (keeps history, but rejects requests)
uv run api-proxy-keys disable --name "email-agent-prod"

# Re-enable a disabled API key
uv run api-proxy-keys enable --name "email-agent-prod"

# Revoke an API key (permanent deletion)
uv run api-proxy-keys revoke --name "email-agent-prod"

# Show details for a specific key
uv run api-proxy-keys show --name "email-agent-prod"
```

### Key Storage

API keys are stored in `api_keys.json` (configurable via `--api-keys-file`):

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

### Authentication Errors

| Scenario | Status Code | Response |
|----------|-------------|----------|
| Missing `Authorization` header | 401 | `{"error": "auth_error", "message": "Missing Authorization header"}` |
| Invalid format (not `Bearer <key>`) | 401 | `{"error": "auth_error", "message": "Invalid Authorization header format"}` |
| Unknown API key | 401 | `{"error": "auth_error", "message": "Invalid API key"}` |
| Disabled API key | 403 | `{"error": "auth_error", "message": "API key is disabled"}` |

### All Status Codes

| Code | Error Type | Description |
|------|------------|-------------|
| 200 | - | Success |
| 400 | `proxy_error` | Invalid request parameter (userId, message_id, label_id format) |
| 401 | `auth_error` | Missing or invalid API key |
| 403 | `auth_error` | API key is disabled |
| 403 | `forbidden` | Blocked operation (send, drafts, etc.) |
| 403 | `forbidden` | Confirmation rejected by operator |
| 422 | `proxy_error` | Request validation failed (malformed JSON, missing fields) |
| 502 | `backend_error` | Backend unreachable or authentication failed |
| 4xx/5xx | `backend_error` | Error passed through from Gmail API |

### Error Response Format

All errors follow this structure:

```json
{
  "error": "error_type",
  "message": "Human-readable description"
}
```

Backend errors from Gmail include additional details:

```json
{
  "error": "backend_error",
  "message": "Backend API error",
  "details": { ... }
}
```

## Google OAuth Setup

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Gmail API:
   - Go to "APIs & Services" → "Library"
   - Search for "Gmail API"
   - Click "Enable"

### 2. Create OAuth Credentials

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. Select "Desktop application" as the application type
4. Download the credentials JSON file

### 3. Generate token.json

Use the included helper script to generate `token.json`:

```bash
# If using uv (recommended)
uv run python scripts/generate_token.py --credentials credentials.json

# Or with pip
pip install google-auth-oauthlib
python scripts/generate_token.py --credentials credentials.json
```

The script will:
1. Open a browser window for Google authentication
2. Request the `gmail.modify` scope
3. Save the token with restricted permissions (600)

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--credentials`, `-c` | `credentials.json` | Path to OAuth client credentials |
| `--output`, `-o` | `token.json` | Path for output token file |

**Example with custom paths:**

```bash
python scripts/generate_token.py \
  --credentials ~/Downloads/client_secret.json \
  --output ~/.config/api-proxy/token.json
```

## Gmail API Reference

### Read Operations

#### List Messages

`GET /gmail/v1/users/{userId}/messages`

List messages in the user's mailbox.

**Query Parameters:**
- `maxResults` (int): Maximum number of messages to return
- `pageToken` (string): Page token for pagination
- `q` (string): Gmail search query (e.g., `is:unread`)
- `labelIds` (list): Filter by label IDs
- `includeSpamTrash` (bool): Include spam and trash

**Example:**
```bash
curl -X GET "http://localhost:8000/gmail/v1/users/me/messages?maxResults=10&q=is:unread" \
  -H "Authorization: Bearer aproxy_..."
```

#### Get Message

`GET /gmail/v1/users/{userId}/messages/{id}`

Get a specific message by ID.

**Query Parameters:**
- `format` (string): `full`, `metadata`, `minimal`, or `raw`
- `metadataHeaders` (list): Headers to include when format is `metadata`

**Example:**
```bash
curl -X GET "http://localhost:8000/gmail/v1/users/me/messages/18d5a1b2c3d4e5f6?format=metadata" \
  -H "Authorization: Bearer aproxy_..."
```

#### List Labels

`GET /gmail/v1/users/{userId}/labels`

List all labels in the user's mailbox.

**Example:**
```bash
curl -X GET "http://localhost:8000/gmail/v1/users/me/labels" \
  -H "Authorization: Bearer aproxy_..."
```

#### Get Label

`GET /gmail/v1/users/{userId}/labels/{id}`

Get a specific label by ID.

**Example:**
```bash
curl -X GET "http://localhost:8000/gmail/v1/users/me/labels/INBOX" \
  -H "Authorization: Bearer aproxy_..."
```

### Modify Operations

#### Modify Message Labels

`POST /gmail/v1/users/{userId}/messages/{id}/modify`

Add or remove labels from a message.

**Request Body:**
```json
{
  "addLabelIds": ["STARRED", "IMPORTANT"],
  "removeLabelIds": ["UNREAD"]
}
```

**Example:**
```bash
curl -X POST "http://localhost:8000/gmail/v1/users/me/messages/18d5a1b2c3d4e5f6/modify" \
  -H "Authorization: Bearer aproxy_..." \
  -H "Content-Type: application/json" \
  -d '{"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]}'
```

#### Trash Message

`POST /gmail/v1/users/{userId}/messages/{id}/trash`

Move a message to trash.

**Example:**
```bash
curl -X POST "http://localhost:8000/gmail/v1/users/me/messages/18d5a1b2c3d4e5f6/trash" \
  -H "Authorization: Bearer aproxy_..."
```

#### Untrash Message

`POST /gmail/v1/users/{userId}/messages/{id}/untrash`

Remove a message from trash.

**Example:**
```bash
curl -X POST "http://localhost:8000/gmail/v1/users/me/messages/18d5a1b2c3d4e5f6/untrash" \
  -H "Authorization: Bearer aproxy_..."
```

## Security Model

### Two-Layer Security

1. **API Key Authentication**: Validates that the caller is an authorized agent
2. **Operation Restrictions**: Enforces allowlist of permitted operations

### Allowlist Approach

The proxy uses an **allowlist** approach: only explicitly allowed operations are permitted. Any endpoint not on the allowlist returns `403 Forbidden`.

### Allowed Operations

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/gmail/v1/users/{userId}/messages` | List messages |
| `GET` | `/gmail/v1/users/{userId}/messages/{id}` | Get message |
| `GET` | `/gmail/v1/users/{userId}/labels` | List labels |
| `GET` | `/gmail/v1/users/{userId}/labels/{id}` | Get label |
| `POST` | `/gmail/v1/users/{userId}/messages/{id}/modify` | Modify labels |
| `POST` | `/gmail/v1/users/{userId}/messages/{id}/trash` | Trash message |
| `POST` | `/gmail/v1/users/{userId}/messages/{id}/untrash` | Untrash message |

### Blocked Operations (Critical)

These operations are **ALWAYS** blocked, regardless of confirmation settings:

| Method | Endpoint | Reason |
|--------|----------|--------|
| `POST` | `/gmail/v1/users/{userId}/messages/send` | Send email |
| `POST` | `/gmail/v1/users/{userId}/drafts` | Create draft |
| `POST` | `/gmail/v1/users/{userId}/drafts/send` | Send draft |
| `PUT` | `/gmail/v1/users/{userId}/drafts/{id}` | Update draft |
| `DELETE` | `/gmail/v1/users/{userId}/drafts/{id}` | Delete draft |
| `POST` | `/gmail/v1/users/{userId}/messages/import` | Import message |
| `POST` | `/gmail/v1/users/{userId}/messages/insert` | Insert message |

### Credential Protection

- Google OAuth credentials are stored server-side only
- Agents never receive backend credentials
- API keys are stored separately from OAuth tokens
- Credentials are automatically refreshed when needed

## Human-in-the-Loop Confirmation

The proxy supports optional human confirmation before forwarding requests.

### Confirmation Modes

| Option | Behavior |
|--------|----------|
| `--confirm-all` | Require confirmation for ALL requests (read and modify) |
| `--confirm-modify` | Require confirmation only for modify operations (default) |
| `--no-confirm` | No confirmation required for any operations |

These options are mutually exclusive.

### Confirmation Prompt

When confirmation is required, the operator sees:

```
[CONFIRM] POST /gmail/v1/users/me/messages/abc123/modify
  Add labels: STARRED
  Remove labels: UNREAD
Allow this request? [y/N]:
```

- Enter `y` or `Y` to approve and forward the request
- Enter `n`, `N`, or just press Enter to reject (returns 403 to caller)

### Important Notes

- Blocked operations are **NEVER** subject to confirmation—they are always rejected
- Confirmation prompts are synchronous—only one pending at a time
- Default timeout is 5 minutes (configurable via `--confirmation-timeout`)

## Configuration

### Command-Line Options

```bash
uv run api-proxy [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8000` | Port to bind to |
| `--api-keys-file` | `api_keys.json` | Path to API keys file |
| `--token-file` | `token.json` | Path to Google OAuth token file |
| `--confirm-all` | - | Require confirmation for all requests |
| `--confirm-modify` | (default) | Require confirmation for modify operations |
| `--no-confirm` | - | Disable confirmation |
| `--confirmation-timeout` | `300` | Timeout for confirmation prompts (seconds) |
| `--reload` | - | Enable auto-reload for development |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `API_KEYS_FILE` | Path to API keys file (alternative to `--api-keys-file`) |

## Development

### Setup Development Environment

```bash
# Install with dev dependencies
uv sync --all-extras

# Or install dev dependencies explicitly
uv pip install -e ".[dev]"
```

### Run Tests

```bash
uv run pytest
```

### Run Linting

```bash
uv run ruff check .
uv run ruff format --check .
```

### Format Code

```bash
uv run ruff format .
```

## Adding New APIs

The architecture supports additional APIs. To add a new API:

1. Create a new module under `src/api_proxy/` (e.g., `calendar/`)
2. Implement handlers following the Gmail pattern
3. Define allowed and blocked operations
4. Add the router to `main.py`
5. Update tests and documentation

## Deployment Considerations

### Running in Production

1. **Use a process manager**: Run with systemd, supervisord, or similar
2. **Reverse proxy**: Put behind nginx/Caddy for TLS termination
3. **Choose confirmation mode carefully**: `--no-confirm` for automation, `--confirm-modify` for oversight

### Protecting Sensitive Files

- Store `api_keys.json` with restricted permissions (`chmod 600`)
- Store `token.json` with restricted permissions (`chmod 600`)
- Never commit these files to version control

### Logging and Monitoring

The proxy logs:
- All requests (method, path, status code, API key name)
- Authentication failures (WARNING level)
- Blocked operation attempts (WARNING level)
- Confirmation decisions (INFO level)

Sensitive data is **never** logged:
- Full API keys (only name or last 4 characters)
- Request/response bodies (may contain email content)

## Error Response Format

Errors clearly indicate their origin:

**Proxy errors:**
```json
{
  "error": "proxy_error",
  "message": "Backend authentication failed"
}
```

**Backend errors:**
```json
{
  "error": "backend_error",
  "message": "Not found",
  "details": { ... }
}
```

**Authentication errors:**
```json
{
  "error": "auth_error",
  "message": "Invalid API key"
}
```

**Forbidden operations:**
```json
{
  "error": "forbidden",
  "message": "This operation is not allowed"
}
```
