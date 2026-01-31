"""Google Calendar API client for making authenticated requests."""

import json
import logging
from pathlib import Path

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from api_proxy.config import get_config

logger = logging.getLogger(__name__)

# Calendar API scope - events only (not full calendar management)
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class CalendarClient:
    """Client for making authenticated requests to the Google Calendar API."""

    def __init__(self, token_file: Path | None = None):
        self._token_file = token_file
        self._credentials: Credentials | None = None
        self._http_client: httpx.AsyncClient | None = None

    @property
    def token_file(self) -> Path:
        """Get the token file path."""
        if self._token_file is not None:
            return self._token_file
        return get_config().token_file

    def _load_credentials(self) -> Credentials | None:
        """Load credentials from the token file."""
        token_path = self.token_file
        if not token_path.exists():
            logger.error(f"Token file not found: {token_path}")
            return None

        try:
            with open(token_path) as f:
                token_data = json.load(f)

            creds = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=token_data.get("client_id"),
                client_secret=token_data.get("client_secret"),
                scopes=token_data.get("scopes", SCOPES),
            )
            return creds
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.error(f"Failed to load credentials: {e}")
            return None

    def _save_credentials(self, creds: Credentials) -> None:
        """Save refreshed credentials back to the token file."""
        token_path = self.token_file
        try:
            # Load existing data to preserve any extra fields
            if token_path.exists():
                with open(token_path) as f:
                    token_data = json.load(f)
            else:
                token_data = {}

            # Update with new token
            token_data["token"] = creds.token
            if creds.refresh_token:
                token_data["refresh_token"] = creds.refresh_token
            if creds.expiry:
                token_data["expiry"] = creds.expiry.isoformat()

            with open(token_path, "w") as f:
                json.dump(token_data, f, indent=2)

        except OSError as e:
            logger.warning(f"Failed to save refreshed credentials: {e}")

    def _get_credentials(self) -> Credentials | None:
        """Get valid credentials, refreshing if necessary."""
        if self._credentials is None:
            self._credentials = self._load_credentials()

        if self._credentials is None:
            return None

        # Check if credentials need refresh
        if self._credentials.expired and self._credentials.refresh_token:
            try:
                self._credentials.refresh(Request())
                self._save_credentials(self._credentials)
                logger.info("Refreshed expired credentials")
            except Exception as e:
                logger.error(f"Failed to refresh credentials: {e}")
                return None

        return self._credentials

    async def get_http_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def _force_refresh_credentials(self) -> Credentials | None:
        """Force a token refresh, regardless of expiry status."""
        if self._credentials is None:
            self._credentials = self._load_credentials()

        if self._credentials is None or not self._credentials.refresh_token:
            return None

        try:
            self._credentials.refresh(Request())
            self._save_credentials(self._credentials)
            logger.info("Force-refreshed credentials after 401")
            return self._credentials
        except Exception as e:
            logger.error(f"Failed to force-refresh credentials: {e}")
            return None

    async def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        """
        Make an authenticated request to the Calendar API.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            path: API path (e.g., /calendars/primary/events)
            params: Query parameters
            json_body: JSON request body

        Returns:
            httpx.Response from the Calendar API

        Raises:
            RuntimeError: If credentials are not available
        """
        creds = self._get_credentials()
        if creds is None:
            raise RuntimeError("Backend authentication failed")

        config = get_config()
        url = f"{config.calendar_api_base_url}{path}"
        client = await self.get_http_client()

        logger.debug(f"Calendar API request: {method} {path}")

        response = await client.request(
            method=method,
            url=url,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            },
            params=params,
            json=json_body,
        )

        logger.debug(f"Calendar API response: {response.status_code}")

        # If we get a 401, try refreshing the token and retrying once
        if response.status_code == 401:
            logger.info("Got 401 from Calendar API, attempting token refresh")
            creds = self._force_refresh_credentials()
            if creds is not None:
                response = await client.request(
                    method=method,
                    url=url,
                    headers={
                        "Authorization": f"Bearer {creds.token}",
                        "Content-Type": "application/json",
                    },
                    params=params,
                    json=json_body,
                )
                logger.debug(f"Calendar API retry response: {response.status_code}")

        return response


# Global client instance
_client: CalendarClient | None = None


def get_calendar_client() -> CalendarClient:
    """Get the global Calendar client instance."""
    global _client
    if _client is None:
        _client = CalendarClient()
    return _client


async def close_calendar_client() -> None:
    """Close the global Calendar client."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
