from typing import Any
import core.agents.merge.nodes as nodes
from core.agents.merge.graph import build_merge_graph
from core.orchestrator.models import StageStatus

class MergeAgent:
    def __init__(self):
        self.graph = build_merge_graph(nodes)

    def run(self, context: dict[str, Any]):
        result = self.graph.invoke(
            {
                "context": context,
                "status": StageStatus.OK,
                "repository": "",
                "pull_request_number": None,
                "merge_method": "squash",
                "commit_title": None,
                "merge_result": {},
                "notes": {},
                "final_output": {},
            }
        )
        return result["status"], result["final_output"]
