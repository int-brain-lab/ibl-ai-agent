from __future__ import annotations


class IblAgentError(RuntimeError):
    """Base class for public IBL agent runtime errors."""


class ExecutionContractError(IblAgentError):
    """Raised when an execution input or generated artifact is invalid."""


class NotebookExecutionError(IblAgentError):
    """Raised when notebook generation or execution fails."""


class FeedbackError(IblAgentError):
    """Raised when feedback collection, packaging, or sending fails."""
