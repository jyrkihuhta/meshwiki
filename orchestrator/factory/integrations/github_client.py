"""GitHub API client stub for the factory orchestrator."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class GitHubClient:
    """Stub GitHub client.

    Phase 4 only needs ``get_pr_diff`` for PM review.  A full implementation
    would use the GitHub REST API or PyGitHub; for now the method raises
    ``NotImplementedError`` so callers can see what needs to be wired up.
    """

    async def get_pr_diff(self, pr_number: int) -> str:
        """Fetch the unified diff for a pull request.

        Args:
            pr_number: GitHub pull request number.

        Returns:
            Unified diff as a string.
        """
        raise NotImplementedError(
            f"GitHubClient.get_pr_diff({pr_number}) is not yet implemented. "
            "Wire up the GitHub REST API or PyGitHub in a future phase."
        )
