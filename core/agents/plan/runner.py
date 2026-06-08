from core.agents.plan.graph import build_plan_graph
import core.agents.plan.nodes as nodes
from core.contracts.enums import PlanStatus
from core.contracts.triage import TriageResult

class PlanAgent:
    def __init__(self):
        self.graph = build_plan_graph(nodes)

    def run(self, triage_result: TriageResult, repo_map: str = ""):
        result = self.graph.invoke(
            {
                "triage_result": triage_result,
                "repo_map": repo_map,
                "strategy": "",
                "steps": [],
                "assumptions": [],
                "open_questions": [],
                "status": PlanStatus.OK,
                "final_output": {},
            }
        )
        return result["status"], result["final_output"]
