"""
Domain exceptions for the orchestration service.
All HTTP mapping is done in the exception handlers registered on the app.
"""
from uuid import UUID


class OrchestrationError(Exception):
    """Base class for all orchestration errors."""


class NotFoundError(OrchestrationError):
    def __init__(self, resource: str, resource_id: UUID | str) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource} '{resource_id}' not found.")


class ConflictError(OrchestrationError):
    """Raised when an operation conflicts with current resource state."""


class InvalidTransitionError(OrchestrationError):
    """Raised when a workflow/task status transition is illegal."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(
            f"Transition from '{from_status}' to '{to_status}' is not allowed."
        )


class DependencyCycleError(OrchestrationError):
    """Raised when a task dependency graph contains a cycle."""


class AgentRuntimeError(OrchestrationError):
    """Raised when the Agent Runtime Service returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"Agent runtime error {status_code}: {detail}")


class WorkflowNotPausedError(OrchestrationError):
    """Raised when trying to resume a workflow that is not paused."""


class WorkflowAlreadyTerminalError(OrchestrationError):
    """Raised when trying to modify a completed / failed workflow."""
