import ray
from core.agents.triage.runner import TriageAgent
from core.agents.plan.runner import PlanAgent
from core.agents.code.runner import CodeAgent
from core.agents.qa.runner import QAAgent
from core.agents.pr.runner import PRAgent
from core.agents.review.runner import ReviewAgent
from core.contracts.run_context import (
    CodeWorkerInput,
    PlanWorkerInput,
    PRWorkerInput,
    QAJobPayload,
    QAWorkerInput,
    ReviewWorkerInput,
    TriageWorkerInput
)

from infra.ray.jobs.qa import run_coverage_job, run_lint_job, run_security_job, run_tests_job

@ray.remote
class TriageWorker:
    def __init__(self):
        self.agent = TriageAgent()

    def run(self, payload: TriageWorkerInput):
        return self.agent.run(payload.issue)

@ray.remote
class PlanWorker:
    def __init__(self):
        self.agent = PlanAgent()

    def run(self, payload: PlanWorkerInput):
        return self.agent.run(payload.triage_result)

@ray.remote
class CodeWorker:
    def __init__(self):
        self.agent = CodeAgent()

    def run(self, payload: CodeWorkerInput):
        return self.agent.run(payload.step, payload.repo_map, payload.file_contents)

@ray.remote
class LintWorker:
    def run(self, qa_payload: QAJobPayload):
        return run_lint_job(qa_payload)

@ray.remote
class TestWorker:
    def run(self, qa_payload: QAJobPayload):
        return run_tests_job(qa_payload)

@ray.remote
class CoverageWorker:
    def run(self, qa_payload: QAJobPayload):
        return run_coverage_job(qa_payload)

@ray.remote
class SecurityWorker:
    def run(self, qa_payload: QAJobPayload):
        return run_security_job(qa_payload)

@ray.remote
class QAWorker:
    def __init__(self):
        self.agent = QAAgent()

    def run(self, payload: QAWorkerInput):
        return self.agent.run(payload.coding_output, payload.coding_step, payload.tool_results)

@ray.remote
class PRWorker:
    def __init__(self):
        self.agent = PRAgent()

    def run(self, payload: PRWorkerInput):
        return self.agent.run(payload.context)

@ray.remote
class ReviewWorker:
    def __init__(self):
        self.agent = ReviewAgent()

    def run(self, payload: ReviewWorkerInput):
        return self.agent.run(payload.context)
