from core.agents.review.graph import build_review_graph
import core.agents.review.nodes as nodes

class ReviewAgent:
    def __init__(self):
        self.graph = build_review_graph(nodes)

    def run(self, context: dict):
        result = self.graph.invoke(
            {
                "context": context,
                "status": "ok",
                "summary": "",
                "checks": [],
                "required_actions": [],
                "notes": {},
                "final_output": None,
            }
        )
        return result["status"], result["final_output"]
