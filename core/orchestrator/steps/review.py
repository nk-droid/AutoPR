import os
from typing import Any

from core.contracts.enums import PipelineStage, RunState
from core.contracts.run_context import PRToMergeContext, ReviewWorkerInput
from core.orchestrator.models import MergeDecision, RunModel, StageResult, StageStatus
from core.orchestrator.steps.base import PipelineStep, StepRuntime, is_success_status
from core.orchestrator.transitions import can_merge_pr

from infra.github.client import GitHubAPIError, GitHubClient

from infra.ray.actors import ReviewWorker
from observability.tracing import pipeline_step_attrs, traced

class ReviewStep(PipelineStep):
    stage = PipelineStage.REVIEW

    def before(self, context: dict[str, Any], run: RunModel) -> list[tuple[str, str]]:
        if run.state in {RunState.RECEIVED.value, RunState.PR_OPENED.value}:
            return [(RunState.REVIEW_PENDING.value, "start merge workflow")]
        return []

    @traced("pipeline.review_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        repository = context.get("repository") or run.repository
        pull_request_number = context.get("pull_request_number")
        if pull_request_number is None:
            pull_request_number = run.pull_request_number

        if not repository or pull_request_number is None:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={
                    "reason": "Review blocked: repository or pull_request_number missing.",
                    "blocking_reasons": ["missing_review_inputs"],
                },
            )

        context["repository"] = repository
        context["pull_request_number"] = pull_request_number

        client = GitHubClient(token=context.get("github_token") or os.environ.get("GITHUB_TOKEN"))
        try:
            pull_request = client.get_pull_request(repository, pull_request_number)
        except Exception as exc:
            notes: dict[str, Any] = {
                "reason": "Review blocked: failed to load pull request details.",
                "blocking_reasons": ["pull_request_fetch_failed"],
                "error": str(exc),
                "repository": repository,
                "pull_request_number": pull_request_number,
            }
            if isinstance(exc, GitHubAPIError):
                notes["status_code"] = exc.status_code
                notes["api_error_payload"] = exc.response_payload
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes=notes,
            )
        finally:
            client.close()

        # Refresh PR metadata from GitHub so review logic uses canonical state.
        context["pull_request_url"] = pull_request.get("html_url") or context.get("pull_request_url")
        context["pull_request_state"] = pull_request.get("state")
        context["pull_request_draft"] = bool(pull_request.get("draft", False))
        mergeable = pull_request.get("mergeable")
        if mergeable is None or isinstance(mergeable, bool):
            context["pull_request_mergeable"] = mergeable
        context["pull_request_mergeable_state"] = pull_request.get("mergeable_state")
        payload = dict(context)
        payload.setdefault("repository", repository)
        payload.setdefault("pull_request_number", pull_request_number)
        payload.setdefault("review_approved", bool(context.get("review_approved", False)))
        payload.setdefault("execute_remote_actions", bool(context.get("execute_remote_actions", False)))
        payload.setdefault("metadata", context.get("metadata") if isinstance(context.get("metadata"), dict) else dict(run.metadata))
        review_context = PRToMergeContext(**payload)
        return runtime.run_worker(self.stage, ReviewWorker.remote(), ReviewWorkerInput(context=review_context))

    def after(
        self,
        result: StageResult,
        context: dict[str, Any],
        run: RunModel,
    ) -> list[tuple[str, str]]:
        if not is_success_status(result.status):
            return []

        policy_decision_value = context.get("policy_decision")
        policy_decision: MergeDecision | None = None
        if isinstance(policy_decision_value, MergeDecision):
            policy_decision = policy_decision_value
        elif isinstance(policy_decision_value, dict):
            try:
                policy_decision = MergeDecision(**policy_decision_value)
            except Exception:
                policy_decision = None
        decision = can_merge_pr(result, policy_decision)
        context["_merge_decision"] = decision.model_dump()

        if not decision.allowed:
            result.status = StageStatus.BLOCKED
            result.notes = {
                **result.notes,
                "reason": decision.reason,
                "blocking_reasons": decision.blocking_reasons,
            }
            return []

        return [(RunState.READY_TO_MERGE.value, decision.reason or "review checks passed")]
