from core.agents.qa.graph import build_qa_graph
import core.agents.qa.nodes as nodes

class QAAgent:
    def __init__(self):
        self.graph = build_qa_graph(nodes)

    def run(self, coding_output: dict, coding_step: dict):
        result = self.graph.invoke(
            {
                "coding_output": coding_output,
                "coding_step": coding_step,
                "status": "ok",
                "summary": "",
                "checks": [],
                "notes": {},
                "final_output": None,
            }
        )
        return result["status"], result["final_output"]
