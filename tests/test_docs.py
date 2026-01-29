"""Documentation tests - verify README stays in sync with implementation."""

import re
from pathlib import Path

import pytest

# Get the project root
PROJECT_ROOT = Path(__file__).parent.parent


def extract_endpoints_from_code() -> set[tuple[str, str]]:
    """Extract endpoint patterns from the handler code."""
    handlers_file = PROJECT_ROOT / "src" / "api_proxy" / "gmail" / "handlers.py"
    content = handlers_file.read_text()

    endpoints = set()

    # Match FastAPI route decorators
    # Pattern: @router.get("/{user_id}/messages") or @router.post("/{user_id}/messages/{message_id}/modify")
    route_pattern = re.compile(
        r'@router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )

    for match in route_pattern.finditer(content):
        method = match.group(1).upper()
        path = match.group(2)
        # Normalize path by adding /gmail/v1/users prefix
        full_path = f"/gmail/v1/users{path}"
        endpoints.add((method, full_path))

    return endpoints


def extract_endpoints_from_readme() -> set[tuple[str, str]]:
    """Extract documented endpoints from README."""
    readme_file = PROJECT_ROOT / "README.md"
    if not readme_file.exists():
        return set()

    content = readme_file.read_text()
    endpoints = set()

    # Match endpoint patterns in README
    # Pattern: `GET /gmail/v1/users/{userId}/messages` or similar
    endpoint_pattern = re.compile(
        r"`(GET|POST|PUT|DELETE|PATCH)\s+(/gmail/v1/users/[^`]+)`",
        re.IGNORECASE,
    )

    for match in endpoint_pattern.finditer(content):
        method = match.group(1).upper()
        path = match.group(2)
        # Normalize path parameters
        path = re.sub(r"\{userId\}", "{user_id}", path)
        path = re.sub(r"\{id\}", "{message_id}", path)
        endpoints.add((method, path))

    return endpoints


def get_blocked_operations_from_code() -> set[str]:
    """Extract blocked operation paths from code."""
    main_file = PROJECT_ROOT / "src" / "api_proxy" / "main.py"
    content = main_file.read_text()

    blocked = set()

    # Find BLOCKED_PATHS list
    match = re.search(r"BLOCKED_PATHS\s*=\s*\[(.*?)\]", content, re.DOTALL)
    if match:
        paths_str = match.group(1)
        # Extract individual paths
        path_pattern = re.compile(r'"([^"]+)"')
        for path_match in path_pattern.finditer(paths_str):
            blocked.add(path_match.group(1))

    return blocked


def get_blocked_operations_from_readme() -> set[str]:
    """Extract blocked operations documented in README."""
    readme_file = PROJECT_ROOT / "README.md"
    if not readme_file.exists():
        return set()

    content = readme_file.read_text()
    blocked = set()

    # Look for blocked operations section
    # Match patterns like `POST /gmail/v1/users/{userId}/messages/send`
    blocked_section = re.search(
        r"(?:Blocked|BLOCKED|blocked).*?(?=##|\Z)", content, re.DOTALL | re.IGNORECASE
    )

    if blocked_section:
        section_content = blocked_section.group(0)
        endpoint_pattern = re.compile(
            r"`(?:POST|PUT|DELETE)\s+(/gmail/v1/users/[^`]+)`", re.IGNORECASE
        )
        for match in endpoint_pattern.finditer(section_content):
            path = match.group(1)
            # Normalize
            path = re.sub(r"\{userId\}", "{user_id}", path)
            blocked.add(path)

    return blocked


class TestEndpointDocumentation:
    """Test that endpoints are documented."""

    def test_readme_exists(self):
        """README.md should exist."""
        readme = PROJECT_ROOT / "README.md"
        assert readme.exists(), "README.md not found"

    def test_all_endpoints_documented(self, subtests):
        """Verify every endpoint in the code is documented in README."""
        readme_file = PROJECT_ROOT / "README.md"
        if not readme_file.exists():
            pytest.skip("README.md not found")

        readme_content = readme_file.read_text()
        endpoints = extract_endpoints_from_code()

        for method, path in endpoints:
            with subtests.test(endpoint=f"{method} {path}"):
                # Normalize the path for matching - convert code param names to README style
                # Code uses: {user_id}, {message_id}, {label_id}
                # README uses: {userId}, {id}
                readme_path = path.replace("{user_id}", "{userId}")
                readme_path = readme_path.replace("{message_id}", "{id}")
                readme_path = readme_path.replace("{label_id}", "{id}")

                # Check if the endpoint is documented (method + path)
                search_pattern = f"`{method} {readme_path}`"
                assert search_pattern in readme_content, (
                    f"Endpoint {method} {path} not documented in README (expected: {search_pattern})"
                )


class TestBlockedOperationsDocumentation:
    """Test that blocked operations are documented."""

    def test_all_blocked_operations_documented(self, subtests):
        """Verify every blocked operation in code is documented in README."""
        readme_file = PROJECT_ROOT / "README.md"
        if not readme_file.exists():
            pytest.skip("README.md not found")

        readme_content = readme_file.read_text()
        blocked_paths = get_blocked_operations_from_code()

        for path in blocked_paths:
            with subtests.test(path=path):
                # Normalize path for README matching
                # Code uses: {user_id}, {draft_id}
                # README uses: {userId}, {id}
                readme_path = path.replace("{user_id}", "{userId}")
                readme_path = readme_path.replace("{draft_id}", "{id}")

                # Check if mentioned anywhere in README
                assert readme_path in readme_content, (
                    f"Blocked operation {path} not documented in README (expected: {readme_path})"
                )


class TestSecurityDocumentation:
    """Test that security model is documented."""

    def test_security_model_documented(self):
        """README should document the security model."""
        readme_file = PROJECT_ROOT / "README.md"
        if not readme_file.exists():
            pytest.skip("README.md not found")

        content = readme_file.read_text().lower()

        # Check for key security concepts
        assert "security" in content, "Security section missing"
        assert "allowlist" in content or "allow list" in content, (
            "Allowlist approach not documented"
        )
        assert "blocked" in content, "Blocked operations not documented"
        assert "api key" in content, "API key authentication not documented"


class TestConfirmationDocumentation:
    """Test that confirmation feature is documented."""

    def test_confirmation_modes_documented(self):
        """README should document confirmation modes."""
        readme_file = PROJECT_ROOT / "README.md"
        if not readme_file.exists():
            pytest.skip("README.md not found")

        content = readme_file.read_text()

        # Check for confirmation mode flags
        assert "--confirm-all" in content, "--confirm-all not documented"
        assert "--confirm-modify" in content, "--confirm-modify not documented"
        assert "--no-confirm" in content, "--no-confirm not documented"

    def test_default_confirmation_behavior_documented(self):
        """README should document default confirmation behavior."""
        readme_file = PROJECT_ROOT / "README.md"
        if not readme_file.exists():
            pytest.skip("README.md not found")

        content = readme_file.read_text().lower()

        # Check that default behavior is mentioned
        assert "default" in content, "Default behavior not documented"


class TestApiKeyDocumentation:
    """Test that API key management is documented."""

    def test_api_key_commands_documented(self):
        """README should document API key CLI commands."""
        readme_file = PROJECT_ROOT / "README.md"
        if not readme_file.exists():
            pytest.skip("README.md not found")

        content = readme_file.read_text()

        commands = ["create", "list", "disable", "enable", "revoke", "show"]
        for cmd in commands:
            assert f"api-proxy-keys {cmd}" in content or f"api-proxy-keys` `{cmd}" in content, (
                f"API key command '{cmd}' not documented"
            )

    def test_api_key_format_documented(self):
        """README should document API key format."""
        readme_file = PROJECT_ROOT / "README.md"
        if not readme_file.exists():
            pytest.skip("README.md not found")

        content = readme_file.read_text()

        assert "aproxy_" in content, "API key prefix not documented"
