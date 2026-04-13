"""Tests for the Jira get_issue_dev_status server tool."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError
from mcp_atlassian.servers.jira import get_issue_dev_status


class TestGetIssueDevStatusTool:
    """Tests for the get_issue_dev_status tool function."""

    @pytest.fixture
    def mock_ctx(self):
        """Create a mock FastMCP context."""
        return MagicMock()

    @pytest.fixture
    def mock_jira_fetcher(self):
        """Create a mock JiraFetcher."""
        fetcher = MagicMock()
        fetcher.get_issue_dev_status = MagicMock()
        return fetcher

    @pytest.mark.asyncio
    async def test_get_issue_dev_status_success(self, mock_ctx, mock_jira_fetcher):
        """Test successful retrieval of development status."""
        dev_status = {
            "detail": [
                {
                    "repositories": [
                        {
                            "name": "my-repo",
                            "commits": [
                                {
                                    "id": "abc123",
                                    "message": "TEST-123 Fix bug",
                                }
                            ],
                            "branches": [
                                {"name": "feature/TEST-123"}
                            ],
                            "pullRequests": [
                                {
                                    "id": "1",
                                    "name": "TEST-123 Fix bug",
                                    "status": "OPEN",
                                }
                            ],
                        }
                    ]
                }
            ]
        }
        mock_jira_fetcher.get_issue_dev_status.return_value = dev_status

        with patch(
            "mcp_atlassian.servers.jira.get_jira_fetcher",
            return_value=mock_jira_fetcher,
        ):
            result = await get_issue_dev_status(mock_ctx, "TEST-123")

        parsed = json.loads(result)
        assert "detail" in parsed
        repos = parsed["detail"][0]["repositories"]
        assert len(repos) == 1
        assert repos[0]["name"] == "my-repo"
        assert len(repos[0]["commits"]) == 1
        assert len(repos[0]["branches"]) == 1
        assert len(repos[0]["pullRequests"]) == 1

    @pytest.mark.asyncio
    async def test_get_issue_dev_status_with_filters(self, mock_ctx, mock_jira_fetcher):
        """Test passing application_type and data_type filters."""
        mock_jira_fetcher.get_issue_dev_status.return_value = {"detail": []}

        with patch(
            "mcp_atlassian.servers.jira.get_jira_fetcher",
            return_value=mock_jira_fetcher,
        ):
            result = await get_issue_dev_status(
                mock_ctx, "TEST-123", application_type="stash", data_type="pullrequest"
            )

        mock_jira_fetcher.get_issue_dev_status.assert_called_once_with(
            issue_key="TEST-123",
            application_type="stash",
            data_type="pullrequest",
        )

    @pytest.mark.asyncio
    async def test_get_issue_dev_status_value_error(self, mock_ctx, mock_jira_fetcher):
        """Test handling of ValueError (issue not found)."""
        mock_jira_fetcher.get_issue_dev_status.side_effect = ValueError(
            "Issue 'BAD-999' not found"
        )

        with patch(
            "mcp_atlassian.servers.jira.get_jira_fetcher",
            return_value=mock_jira_fetcher,
        ):
            result = await get_issue_dev_status(mock_ctx, "BAD-999")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "not found" in parsed["error"]
        assert parsed["issue_key"] == "BAD-999"

    @pytest.mark.asyncio
    async def test_get_issue_dev_status_auth_error(self, mock_ctx, mock_jira_fetcher):
        """Test handling of authentication errors."""
        mock_jira_fetcher.get_issue_dev_status.side_effect = (
            MCPAtlassianAuthenticationError("Token expired")
        )

        with patch(
            "mcp_atlassian.servers.jira.get_jira_fetcher",
            return_value=mock_jira_fetcher,
        ):
            result = await get_issue_dev_status(mock_ctx, "TEST-123")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "Authentication" in parsed["error"]

    @pytest.mark.asyncio
    async def test_get_issue_dev_status_empty_result(self, mock_ctx, mock_jira_fetcher):
        """Test handling of empty development status."""
        mock_jira_fetcher.get_issue_dev_status.return_value = {"detail": []}

        with patch(
            "mcp_atlassian.servers.jira.get_jira_fetcher",
            return_value=mock_jira_fetcher,
        ):
            result = await get_issue_dev_status(mock_ctx, "TEST-123")

        parsed = json.loads(result)
        assert parsed == {"detail": []}
