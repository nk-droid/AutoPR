import logging
from typing import Any
from core.orchestrator.models import RunType, TransitionEvent
from core.orchestrator.transitions import can_transition

logger = logging.getLogger(__name__)


class InvalidStateTransitionError(ValueError):
    """Raised when a run tries to move outside its workflow graph."""

    pass


class StateMachine:
    """Tracks run state and records validated transition history."""

    def __init__(
        self,
        initial_state: str = "RECEIVED",
        run_type: RunType = RunType.ISSUE_TO_PR,
    ) -> None:
        self.state = initial_state
        self.run_type = run_type
        self.history: list[TransitionEvent] = []

    def set_run_type(self, run_type: RunType) -> None:
        """
        Switch the workflow graph used for future transitions.

        Args:
            run_type: Workflow graph selected for validation.
        """

        self.run_type = run_type

    def transition(
        self,
        next_state: str,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> str:
        """
        Record a state transition after optional workflow validation.

        Args:
            next_state: Target state requested by the caller.
            reason: Human-readable reason captured in history.
            metadata: Structured context stored with the transition.
            validate: Whether to enforce the workflow transition graph.

        Returns:
            The new current state after recording the event.
        """

        # Guard invalid jumps unless a caller explicitly disables validation.
        if validate and not can_transition(self.state, next_state, self.run_type):
            logger.warning(
                "invalid state transition rejected",
                extra={
                    "event": "invalid_transition",
                    "run_type": self.run_type.value,
                    "from_state": self.state,
                    "attempted_state": next_state,
                },
            )
            raise InvalidStateTransitionError(
                f"Invalid transition for {self.run_type.value}: {self.state} -> {next_state}"
            )
        # Every transition is captured as an immutable event for later replay.
        event = TransitionEvent(
            from_state=self.state,
            to_state=next_state,
            reason=reason,
            metadata=metadata or {},
        )
        self.history.append(event)
        self.state = next_state
        return self.state

    def snapshot(self) -> dict[str, Any]:
        """
        Serialize the current state machine state for persistence or debugging.

        Returns:
            Dictionary containing state, run type, and transition history.
        """

        return {
            "state": self.state,
            "run_type": self.run_type.value,
            "history": [event.model_dump() for event in self.history],
        }
