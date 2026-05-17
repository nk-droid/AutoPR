from typing import Any
from core.orchestrator.models import RunType, TransitionEvent
from core.orchestrator.transitions import can_transition

class InvalidStateTransitionError(ValueError):
    pass

class StateMachine:
    def __init__(
        self,
        initial_state: str = "RECEIVED",
        run_type: RunType = RunType.ISSUE_TO_PR,
    ) -> None:
        self.state = initial_state
        self.run_type = run_type
        self.history: list[TransitionEvent] = []

    def set_run_type(self, run_type: RunType) -> None:
        self.run_type = run_type

    def transition(
        self,
        next_state: str,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> str:
        # Guard invalid jumps unless a caller explicitly disables validation.
        if validate and not can_transition(self.state, next_state, self.run_type):
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
        return {
            "state": self.state,
            "run_type": self.run_type.value,
            "history": [event.model_dump() for event in self.history],
        }
