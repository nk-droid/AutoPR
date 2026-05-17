from core.agents.review.graph import build_review_graph
import core.agents.review.nodes as nodes
from core.contracts.run_context import PRToMergeContext
from core.orchestrator.models import StageStatus

class ReviewAgent:
    def __init__(self):
        self.graph = build_review_graph(nodes)

    def run(self, context: PRToMergeContext):
        result = self.graph.invoke(
            {
                "context": context,
                "status": StageStatus.OK,
                "summary": "",
                "checks": [],
                "required_actions": [],
                "notes": {},
                "final_output": {},
            }
        )
        return result["status"], result["final_output"]
