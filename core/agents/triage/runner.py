import core.agents.triage.nodes as nodes
from core.agents.triage.graph import build_triage_graph

class TriageAgent:
    def __init__(self):
        self.graph = build_triage_graph(nodes)

    def run(self, issue: dict):
        result = self.graph.invoke({
            "issue": issue,
            "task_spec": None,
            "risk": None,
            "questions": [],
            "status": "ok",
            "final_output": None
        })

        return result["status"], result["final_output"]