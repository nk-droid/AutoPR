import ray
from typing import Any
from core.agents.triage.runner import TriageAgent
from core.agents.plan.runner import PlanAgent
from core.agents.code.runner import CodeAgent
from core.agents.qa.runner import QAAgent
from core.agents.pr.runner import PRAgent
from core.agents.review.runner import ReviewAgent
from core.agents.publish.runner import PublishAgent
from core.agents.merge.runner import MergeAgent
from core.contracts.run_context import (
    CodeWorkerInput,
    MergeWorkerInput,
    PlanWorkerInput,
    PRWorkerInput,
    PublishWorkerInput,
    QAJobPayload,
    QAWorkerInput,
    ReviewWorkerInput,
    TriageWorkerInput,
)
from observability.tracing import traced_remote, ray_worker_attrs
from infra.ray.jobs.qa import run_coverage_job, run_lint_job, run_security_job, run_tests_job

@ray.remote
class TriageWorker:
    def __init__(self):
        self.agent = TriageAgent()

    @traced_remote("ray.triage_worker", attributes=ray_worker_attrs)
    def run(self, payload: TriageWorkerInput, trace_context: dict[str, Any] | None = None):
        return self.agent.run(payload.issue)

@ray.remote
class PlanWorker:
    def __init__(self):
        self.agent = PlanAgent()

    @traced_remote("ray.plan_worker", attributes=ray_worker_attrs)
    def run(self, payload: PlanWorkerInput, trace_context: dict[str, Any] | None = None):
        return self.agent.run(payload.triage_result, payload.repo_map)

@ray.remote
class CodeWorker:
    def __init__(self):
        self.agent = CodeAgent()

    @traced_remote("ray.code_worker", attributes=ray_worker_attrs)
    def run(self, payload: CodeWorkerInput, trace_context: dict[str, Any] | None = None):
        return self.agent.run(
            payload.step,
            payload.repo_map,
            payload.file_contents,
            payload.dependency_files,
            payload.qa_feedback,
        )

@ray.remote
class LintWorker:
    @traced_remote("ray.lint_worker", attributes=ray_worker_attrs)
    def run(self, qa_payload: QAJobPayload, trace_context: dict[str, Any] | None = None):
        return run_lint_job(qa_payload)

@ray.remote
class TestWorker:
    @traced_remote("ray.test_worker", attributes=ray_worker_attrs)
    def run(self, qa_payload: QAJobPayload, trace_context: dict[str, Any] | None = None):
        return run_tests_job(qa_payload)

@ray.remote
class CoverageWorker:
    @traced_remote("ray.coverage_worker", attributes=ray_worker_attrs)
    def run(self, qa_payload: QAJobPayload, trace_context: dict[str, Any] | None = None):
        return run_coverage_job(qa_payload)

@ray.remote
class SecurityWorker:
    @traced_remote("ray.security_worker", attributes=ray_worker_attrs)
    def run(self, qa_payload: QAJobPayload, trace_context: dict[str, Any] | None = None):
        return run_security_job(qa_payload)

@ray.remote
class QAWorker:
    def __init__(self):
        self.agent = QAAgent()

    @traced_remote("ray.qa_worker", attributes=ray_worker_attrs)
    def run(self, payload: QAWorkerInput, trace_context: dict[str, Any] | None = None):
        return self.agent.run(payload.coding_output, payload.coding_step, payload.tool_results)

@ray.remote
class PublishWorker:
    def __init__(self):
        self.agent = PublishAgent()

    @traced_remote("ray.publish_worker", attributes=ray_worker_attrs)
    def run(self, payload: PublishWorkerInput, trace_context: dict[str, Any] | None = None):
        return self.agent.run(payload.context)

@ray.remote
class PRWorker:
    def __init__(self):
        self.agent = PRAgent()

    @traced_remote("ray.pr_worker", attributes=ray_worker_attrs)
    def run(self, payload: PRWorkerInput, trace_context: dict[str, Any] | None = None):
        return self.agent.run(payload.context)

@ray.remote
class ReviewWorker:
    def __init__(self):
        self.agent = ReviewAgent()

    @traced_remote("ray.review_worker", attributes=ray_worker_attrs)
    def run(self, payload: ReviewWorkerInput, trace_context: dict[str, Any] | None = None):
        return self.agent.run(payload.context)

@ray.remote
class MergeWorker:
    def __init__(self):
        self.agent = MergeAgent()

    @traced_remote("ray.merge_worker", attributes=ray_worker_attrs)
    def run(self, payload: MergeWorkerInput, trace_context: dict[str, Any] | None = None):
        return self.agent.run(payload.context)
