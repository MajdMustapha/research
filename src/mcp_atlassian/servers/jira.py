"""Jira FastMCP server instance and tool definitions.

NOTE: This file contains ONLY the new tool added by this PR.
The full file in the upstream repo (SharkyND/mcp-atlassian) should have
this tool appended at the end of the existing servers/jira.py file,
after the batch_create_versions tool.

The existing imports, jira_mcp instance, and all other tools remain unchanged.
"""

# ============================================================================
# NEW TOOL: get_issue_dev_status
# Add the following imports to the existing imports at the top of servers/jira.py:
#   (no new imports needed - all required imports already exist)
#
# Add the following tool function at the end of servers/jira.py:
# ============================================================================

import json
import logging
from typing import Annotated

from fastmcp import Context, FastMCP
from pydantic import Field
from requests.exceptions import HTTPError

from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError
from mcp_atlassian.servers.dependencies import get_jira_fetcher

logger = logging.getLogger(__name__)

jira_mcp = FastMCP(name="Jira MCP Service")


@jira_mcp.tool(tags={"jira", "read"})
async def get_issue_dev_status(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
    application_type: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Filter by source code management tool type. Supported values:\n"
                "- 'stash' for Bitbucket\n"
                "- 'GitHub' for GitHub\n"
                "- 'GitLab' for GitLab\n"
                "Leave empty to return data from all connected tools."
            ),
            default=None,
        ),
    ] = None,
    data_type: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Filter by type of development data. Supported values:\n"
                "- 'repository' for branches and commits\n"
                "- 'pullrequest' for pull requests\n"
                "Leave empty to return all development data."
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """Look up development information for a Jira issue.

    Retrieves commits, branches, and pull requests from connected source code
    management tools (Bitbucket, GitHub, GitLab) that are associated with the
    specified Jira issue. This uses the Jira Software development integration
    and requires Jira Software with connected development tools.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key (e.g., 'PROJ-123').
        application_type: Optional SCM type filter ('stash', 'GitHub', 'GitLab').
        data_type: Optional data type filter ('repository', 'pullrequest').

    Returns:
        JSON string containing development information including commits,
        branches, and pull requests associated with the issue.

    Raises:
        ValueError: If the Jira client is not configured or available.
    """
    try:
        jira = await get_jira_fetcher(ctx)
        result = jira.get_issue_dev_status(
            issue_key=issue_key,
            application_type=application_type,
            data_type=data_type,
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        error_message = ""
        log_level = logging.ERROR
        if isinstance(e, ValueError):
            log_level = logging.WARNING
            error_message = str(e)
        elif isinstance(e, MCPAtlassianAuthenticationError):
            error_message = f"Authentication/Permission Error: {str(e)}"
        elif isinstance(e, OSError | HTTPError):
            error_message = f"Network or API Error: {str(e)}"
        else:
            error_message = (
                f"An unexpected error occurred while fetching development "
                f"status for issue {issue_key}."
            )
            logger.exception(
                f"Unexpected error in get_issue_dev_status for '{issue_key}':"
            )

        error_result = {
            "success": False,
            "error": error_message,
            "issue_key": issue_key,
        }
        logger.log(
            log_level,
            f"get_issue_dev_status failed for '{issue_key}': {error_message}",
        )
        return json.dumps(error_result, indent=2, ensure_ascii=False)
