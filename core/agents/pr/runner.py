from core.agents.pr.graph import build_pr_graph
import core.agents.pr.nodes as nodes

class PRAgent:
    def __init__(self):
        self.graph = build_pr_graph(nodes)

    def run(self, context: dict):
        result = self.graph.invoke(
            {
                "context": context,
                "status": "ok",
                "request": None,
                "pull_request_number": None,
                "pull_request_url": "",
                "summary": "",
                "notes": {},
                "final_output": None,
            }
        )
        return result["status"], result["final_output"]
