"""Module for Jira development information operations."""

import logging
from typing import Any

from requests.exceptions import HTTPError

from ..exceptions import MCPAtlassianAuthenticationError
from .client import JiraClient

logger = logging.getLogger("mcp-jira")


class DevelopmentMixin(JiraClient):
    """Mixin for Jira development information operations.

    This mixin provides methods for retrieving development information
    (commits, branches, pull requests) associated with Jira issues via
    the Jira dev-status REST API.
    """

    def get_issue_dev_status(
        self,
        issue_key: str,
        application_type: str | None = None,
        data_type: str | None = None,
    ) -> dict[str, Any]:
        """Get development information for a Jira issue.

        Retrieves commits, branches, and pull requests associated with
        a Jira issue from connected source code management tools
        (Bitbucket, GitHub, GitLab, etc.).

        Uses the Jira Software dev-status REST API which aggregates
        development data from all connected development tools.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            application_type: Filter by SCM type. Supported values:
                - 'stash' for Bitbucket
                - 'GitHub' for GitHub
                - 'GitLab' for GitLab
                - None to return all connected tools
            data_type: Filter by data type. Supported values:
                - 'repository' for branches and commits
                - 'pullrequest' for pull requests
                - None to return all data types

        Returns:
            Dictionary containing development information with 'detail'
            key holding lists of repositories, branches, commits, and
            pull requests.

        Raises:
            MCPAtlassianAuthenticationError: If authentication fails
            ValueError: If the issue is not found
        """
        try:
            # First, get the issue to obtain its internal ID
            issue = self.jira.issue(issue_key, fields="summary")
            issue_id = (
                issue.get("id") if isinstance(issue, dict) else getattr(issue, "id", None)
            )

            if not issue_id:
                raise ValueError(f"Could not resolve issue ID for key '{issue_key}'")

            # Build the dev-status API URL parameters
            params: dict[str, str] = {"issueId": issue_id}
            if application_type:
                params["applicationType"] = application_type
            if data_type:
                params["dataType"] = data_type

            # Call the dev-status detail endpoint
            detail_response = self.jira.get(
                "rest/dev-status/latest/issue/detail",
                params=params,
            )

            return detail_response

        except HTTPError as http_err:
            if http_err.response is not None and http_err.response.status_code in [
                401,
                403,
            ]:
                error_msg = (
                    f"Authentication failed for Jira API ({http_err.response.status_code}). "
                    "Token may be expired or invalid. Please verify credentials."
                )
                logger.error(error_msg)
                raise MCPAtlassianAuthenticationError(error_msg) from http_err
            elif (
                http_err.response is not None
                and http_err.response.status_code == 404
            ):
                raise ValueError(
                    f"Issue '{issue_key}' not found or dev-status API not available."
                ) from http_err
            else:
                logger.error(f"HTTP error during API call: {http_err}", exc_info=False)
                raise
        except Exception as e:
            if isinstance(e, (MCPAtlassianAuthenticationError, ValueError)):
                raise
            error_msg = (
                f"Error getting development status for issue {issue_key}: {str(e)}"
            )
            logger.error(error_msg)
            msg = f"Error getting development status: {str(e)}"
            raise Exception(msg) from e
