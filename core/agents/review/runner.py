from core.agents.review.graph import build_review_graph
import core.agents.review.nodes as nodes
from core.agents.review.nodes import MergeabilityUnknownError
from core.contracts.run_context import PRToMergeContext
from core.orchestrator.models import StageStatus

class ReviewAgent:
    def __init__(self):
        self.graph = build_review_graph(nodes)

    def _initial_state(self, context: PRToMergeContext, *, allow_unknown: bool) -> dict:
        return {
            "context": context,
            "status": StageStatus.OK,
            "summary": "",
            "checks": [],
            "required_actions": [],
            "notes": {},
            "final_output": {},
            "allow_unknown": allow_unknown,
        }

    def run(self, context: PRToMergeContext):
        try:
            result = self.graph.invoke(self._initial_state(context, allow_unknown=False))
        except MergeabilityUnknownError:
            result = self.graph.invoke(self._initial_state(context, allow_unknown=True))
        return result["status"], result["final_output"]
