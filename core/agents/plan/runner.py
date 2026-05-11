from core.agents.plan.graph import build_plan_graph
import core.agents.plan.nodes as nodes

class PlanAgent:
    def __init__(self):
        self.graph = build_plan_graph(nodes)

    def run(self, triage_result: dict):
        result = self.graph.invoke(
            {
                "triage_result": triage_result,
                "strategy": "",
                "steps": [],
                "assumptions": [],
                "open_questions": [],
                "status": "ok",
                "final_output": None,
            }
        )
        return result["status"], result["final_output"]
