"""Tests for the Jira Development mixin."""

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import HTTPError

from mcp_atlassian.jira.development import DevelopmentMixin


class TestDevelopmentMixin:
    """Tests for the DevelopmentMixin class."""

    @pytest.fixture
    def dev_mixin(self, jira_fetcher):
        """Create a DevelopmentMixin instance with mocked dependencies."""
        return jira_fetcher

    def test_get_issue_dev_status_basic(self, dev_mixin):
        """Test retrieving development status for an issue."""
        # Mock issue lookup to return an issue with an ID
        dev_mixin.jira.issue.return_value = {
            "id": "10001",
            "key": "TEST-123",
            "fields": {"summary": "Test Issue"},
        }

        # Mock the dev-status API response
        dev_status_response = {
            "detail": [
                {
                    "repositories": [
                        {
                            "name": "my-repo",
                            "commits": [
                                {
                                    "id": "abc123",
                                    "message": "TEST-123 Fix the bug",
                                    "author": {"name": "John Doe"},
                                }
                            ],
                            "branches": [
                                {
                                    "name": "feature/TEST-123",
                                    "url": "https://bitbucket.org/workspace/repo/branch/feature/TEST-123",
                                }
                            ],
                            "pullRequests": [
                                {
                                    "id": "1",
                                    "name": "TEST-123 Fix the bug",
                                    "status": "OPEN",
                                    "url": "https://bitbucket.org/workspace/repo/pull-requests/1",
                                }
                            ],
                        }
                    ]
                }
            ]
        }
        dev_mixin.jira.get.return_value = dev_status_response

        # Call the method
        result = dev_mixin.get_issue_dev_status("TEST-123")

        # Verify API calls
        dev_mixin.jira.issue.assert_called_once_with("TEST-123", fields="summary")
        dev_mixin.jira.get.assert_called_once_with(
            "rest/dev-status/latest/issue/detail",
            params={"issueId": "10001"},
        )

        # Verify result
        assert "detail" in result
        repos = result["detail"][0]["repositories"]
        assert len(repos) == 1
        assert repos[0]["name"] == "my-repo"
        assert len(repos[0]["commits"]) == 1
        assert len(repos[0]["branches"]) == 1
        assert len(repos[0]["pullRequests"]) == 1

    def test_get_issue_dev_status_with_application_type(self, dev_mixin):
        """Test filtering by application type."""
        dev_mixin.jira.issue.return_value = {"id": "10001"}
        dev_mixin.jira.get.return_value = {"detail": []}

        dev_mixin.get_issue_dev_status("TEST-123", application_type="stash")

        dev_mixin.jira.get.assert_called_once_with(
            "rest/dev-status/latest/issue/detail",
            params={"issueId": "10001", "applicationType": "stash"},
        )

    def test_get_issue_dev_status_with_data_type(self, dev_mixin):
        """Test filtering by data type."""
        dev_mixin.jira.issue.return_value = {"id": "10001"}
        dev_mixin.jira.get.return_value = {"detail": []}

        dev_mixin.get_issue_dev_status("TEST-123", data_type="pullrequest")

        dev_mixin.jira.get.assert_called_once_with(
            "rest/dev-status/latest/issue/detail",
            params={"issueId": "10001", "dataType": "pullrequest"},
        )

    def test_get_issue_dev_status_with_all_filters(self, dev_mixin):
        """Test filtering by both application type and data type."""
        dev_mixin.jira.issue.return_value = {"id": "10001"}
        dev_mixin.jira.get.return_value = {"detail": []}

        dev_mixin.get_issue_dev_status(
            "TEST-123", application_type="GitHub", data_type="repository"
        )

        dev_mixin.jira.get.assert_called_once_with(
            "rest/dev-status/latest/issue/detail",
            params={
                "issueId": "10001",
                "applicationType": "GitHub",
                "dataType": "repository",
            },
        )

    def test_get_issue_dev_status_issue_not_found(self, dev_mixin):
        """Test error when issue cannot be found."""
        dev_mixin.jira.issue.return_value = {}

        with pytest.raises(ValueError, match="Could not resolve issue ID"):
            dev_mixin.get_issue_dev_status("NONEXISTENT-999")

    def test_get_issue_dev_status_auth_error(self, dev_mixin):
        """Test authentication error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        http_error = HTTPError(response=mock_response)
        dev_mixin.jira.issue.side_effect = http_error

        with pytest.raises(Exception) as exc_info:
            dev_mixin.get_issue_dev_status("TEST-123")

        assert "Authentication failed" in str(exc_info.value) or isinstance(
            exc_info.value, HTTPError
        )

    def test_get_issue_dev_status_404_error(self, dev_mixin):
        """Test 404 error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_error = HTTPError(response=mock_response)
        dev_mixin.jira.issue.return_value = {"id": "10001"}
        dev_mixin.jira.get.side_effect = http_error

        with pytest.raises(ValueError, match="not found"):
            dev_mixin.get_issue_dev_status("TEST-123")

    def test_get_issue_dev_status_empty_response(self, dev_mixin):
        """Test handling of empty development status."""
        dev_mixin.jira.issue.return_value = {"id": "10001"}
        dev_mixin.jira.get.return_value = {"detail": []}

        result = dev_mixin.get_issue_dev_status("TEST-123")

        assert result == {"detail": []}
