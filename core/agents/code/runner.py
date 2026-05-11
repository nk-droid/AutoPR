from core.agents.code.graph import build_code_graph
import core.agents.code.nodes as nodes

class CodeAgent:
    def __init__(self):
        self.graph = build_code_graph(nodes)

    def run(self, step: dict, repo_map: str, file_contents: dict):
        result = self.graph.invoke({
            "step": step,
            "repo_map": repo_map,
            "file_contents": file_contents,
            "target_files": [],
            "files": {},
            "status": "ok",
            "notes": {},
            "final_output": None
        })

        return result["status"], result["final_output"]
