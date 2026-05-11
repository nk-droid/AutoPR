import ray
from core.agents.triage.runner import TriageAgent
from core.agents.plan.runner import PlanAgent
from core.agents.code.runner import CodeAgent
from core.agents.qa.runner import QAAgent
from core.agents.pr.runner import PRAgent
from core.agents.review.runner import ReviewAgent

@ray.remote
class TriageWorker:
    def __init__(self):
        self.agent = TriageAgent()

    def run(self, issue: dict):
        filtered_issue = {
            "title": issue["title"],
            "body": issue["body"]
        }
        
        return self.agent.run(filtered_issue)
    
@ray.remote
class PlanWorker:
    def __init__(self):
        self.agent = PlanAgent()

    def run(self, triage_result: dict):
        return self.agent.run(triage_result)

@ray.remote
class CodeWorker:
    def __init__(self):
        self.agent = CodeAgent()

    def run(self, step, repo_map, file_contents):
        return self.agent.run(step, repo_map, file_contents)

@ray.remote
class QAWorker:
    def __init__(self):
        self.agent = QAAgent()

    def run(self, coding_output: dict, coding_step: dict):
        return self.agent.run(coding_output, coding_step)

@ray.remote
class PRWorker:
    def __init__(self):
        self.agent = PRAgent()

    def run(self, context: dict):
        return self.agent.run(context)

@ray.remote
class ReviewWorker:
    def __init__(self):
        self.agent = ReviewAgent()

    def run(self, context: dict):
        return self.agent.run(context)
