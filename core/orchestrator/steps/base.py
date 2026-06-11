import ray
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Any, Protocol

from core.contracts.enums import PipelineStage
from core.orchestrator.models import RunModel, StageResult, StageStatus

_SUCCESS_STATUSES = {StageStatus.OK, StageStatus.ACCEPTED}

class StepRuntime(Protocol):
    def transition_to(
        self,
        next_state: str,
        *,
        reason: str = "",
        metadata: Dict[str, Any] | None = None
    ) -> str:
        """
        Request a persisted workflow transition from a pipeline step.

        Args:
            next_state: Target state requested by the step.
            reason: Business reason captured in transition history.
            metadata: Extra structured context for audit and debugging.

        Returns:
            The state accepted by the runtime state machine.
        """

        state = self.state_machine.transition(next_state, reason=reason, metadata=metadata)
        self.run.state = state
        self.run.transition_history = list(self.state_machine.history)
        return state

    def run_worker(
        self,
        stage: PipelineStage,
        worker: Any,
        *args: Any
    ) -> StageResult:
        """
        Execute a stage worker through the runtime abstraction.

        Args:
            stage: Pipeline stage represented by the worker call.
            worker: Remote worker exposing a run method.
            *args: Positional payload passed to the worker.

        Returns:
            Stage result built from the worker status and outputs.
        """

        worker_result_ref = worker.run.remote(*args)
        stage_status, worker_result = ray.get(worker_result_ref)

        return StageResult(
            stage = stage,
            status = stage_status,
            outputs = worker_result
        )

def is_success_status(status: StageStatus) -> bool:
    """Return whether a stage status should advance the pipeline."""

    return status in _SUCCESS_STATUSES

class PipelineStep(ABC):
    """Base contract implemented by each ordered pipeline stage."""

    stage: PipelineStage
    success_state: str | None = None

    def before(self, context: Dict[str, Any], run: RunModel) -> List[Tuple[str, str]]:
        """
        Provide pre-execution transitions needed before a stage runs.

        Args:
            context: Mutable workflow context shared across stages.
            run: Current run model before the stage executes.

        Returns:
            Ordered transition requests as state and reason tuples.
        """

        # Hook for pre-step transitions or setup.
        return []

    def execute(
        self,
        context: Dict[str, Any],
        run: RunModel,
        runtime: StepRuntime
    ) -> StageResult:
        """
        Execute the stage's main business action.

        Args:
            context: Mutable workflow context shared across stages.
            run: Current run model at stage execution time.
            runtime: Coordinator facade for transitions and workers.

        Returns:
            Stage result describing status, outputs, and notes.
        """

        raise NotImplementedError

    def after(self, result: StageResult, context: Dict[str, Any], run: RunModel) -> List[Tuple[str, str]]:
        """
        Provide post-execution transitions derived from the stage result.

        Args:
            result: Stage result returned by execute.
            context: Mutable workflow context after stage outputs merge.
            run: Current run model after result persistence.

        Returns:
            Ordered transition requests as state and reason tuples.
        """

        # Default behavior advances to success_state when the step is green.
        if self.success_state and is_success_status(result.status):
            return [(self.success_state, self.stage.value)]
        return []
