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
        
        worker_result_ref = worker.run.remote(*args)
        stage_status, worker_result = ray.get(worker_result_ref)
        # stage_status = self._stage_status(worker_result)
        # stage_status = self._stage_status(
        #     worker_result if isinstance(worker_result, dict) else {}
        # )

        return StageResult(
            stage = stage,
            status = stage_status,
            outputs = worker_result
        )
    
def is_success_status(status: StageStatus) -> bool:
    return status in _SUCCESS_STATUSES

class PipelineStep(ABC):
    stage: PipelineStage
    success_state: str | None = None

    def before(self, context: Dict[str, Any], run: RunModel) -> List[Tuple[str, str]]:
        return []
    
    def execute(
        self,
        context: Dict[str, Any],
        run: RunModel,
        runtime: StepRuntime
    ) -> StageResult:
        raise NotImplementedError
    
    def after(self, result: StageResult, context: Dict[str, Any], run: RunModel) -> List[Tuple[str, str]]:
        if self.success_state and is_success_status(result.status):
            return [(self.success_state, self.stage.value)]
        return []