import os
from typing import Any, Dict

from core.contracts.enums import PipelineStage, RunState
from core.orchestrator.models import MergeDecision, RunModel, StageResult, StageStatus
from core.orchestrator.transitions import can_merge_pr, can_open_pr

from infra.github.client import GitHubAPIError, GitHubClient
from infra.github.issues import get_issue_details
from infra.ray.actors import TriageWorker, PlanWorker, CodeWorker, QAWorker, PRWorker, ReviewWorker

from core.orchestrator.steps.base import PipelineStep, StepRuntime, is_success_status

def _stage_results(context: Dict[str, Any]) -> Dict[str, StageResult]:
    value = context.get("_stage_results")
    if isinstance(value, dict):
        return value
    value = {}
    context["_stage_results"] = value
    return value

def _coerce_pr_number(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.isdigit():
            return int(candidate)
    return None

def _normalize_text(value: Any) -> str:
    return str(value or "").strip()

class TriageStep(PipelineStep):
    stage = PipelineStage.TRIAGE
    success_state = RunState.TRIAGED.value

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        issue_number = context.get("issue_number")
        repo = context.get("repository")
        issue = get_issue_details(
            issue_reference=issue_number,
            repo=repo,
            token=os.environ.get("GITHUB_TOKEN"),
        )
        return runtime.run_worker(self.stage, TriageWorker.remote(), issue)
    
class PlanStep(PipelineStep):
    stage = PipelineStage.PLAN
    success_state = RunState.PLANNED.value

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        triage_result = {
            "status": context.get("status"),
            "task_spec": context.get("task_spec"),
            "risk": context.get("risk"),
            "ambiguity": context.get("ambiguity"),
            "questions": context.get("questions", []),
        }
        return runtime.run_worker(self.stage, PlanWorker.remote(), triage_result)
    
class CodeStep(PipelineStep):
    stage = PipelineStage.CODE
    success_state = RunState.CODING.value

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        steps = context.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                outputs={"files_map": {}, "tests_map": {}},
                notes={"reason": "No plan steps available for coding"},
            )

        raw_step_index = context.get("coding_step_index", 0)
        try:
            step_index = int(raw_step_index)
        except (TypeError, ValueError):
            step_index = 0
        if step_index < 0 or step_index >= len(steps):
            step_index = 0

        selected_step = steps[step_index]
        repo_map = context.get("repo_map", "")
        file_contents = context.get("file_contents", {})
        if not isinstance(repo_map, str):
            repo_map = str(repo_map)
        if not isinstance(file_contents, dict):
            file_contents = {}

        coding_result = runtime.run_worker(
            self.stage,
            CodeWorker.remote(),
            selected_step,
            repo_map,
            file_contents,
        )
        code_output = coding_result.outputs if isinstance(coding_result.outputs, dict) else {}
        return StageResult(
            stage=self.stage,
            status=coding_result.status,
            outputs={
                "coding_step_index": step_index,
                "coding_step": selected_step,
                "coding_output": code_output,
            },
        )
    
class QAStep(PipelineStep):
    stage = PipelineStage.QA
    success_state = RunState.QA_RUNNING.value

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        coding_output = context.get("coding_output", {})
        coding_step = context.get("coding_step", {})
        if not isinstance(coding_output, dict):
            coding_output = {}
        if not isinstance(coding_step, dict):
            coding_step = {}
        return runtime.run_worker(self.stage, QAWorker.remote(), coding_output, coding_step)
    
class PROpenStep(PipelineStep):
    stage = PipelineStage.PR_OPEN
    success_state = RunState.PR_OPENED.value

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        results = _stage_results(context)
        qa_result = results.get(PipelineStage.QA.value) or results.get(PipelineStage.QA)
        decision = can_open_pr(qa_result)
        if not decision.allowed:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": decision.reason, "blocking_reasons": decision.blocking_reasons},
            )
        return runtime.run_worker(self.stage, PRWorker.remote(), context)
    
class ReviewStep(PipelineStep):
    stage = PipelineStage.REVIEW

    def before(self, context: dict[str, Any], run: RunModel) -> list[tuple[str, str]]:
        if run.state in {RunState.RECEIVED.value, RunState.PR_OPENED.value}:
            return [(RunState.REVIEW_PENDING.value, "start merge workflow")]
        return []

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        repository = _normalize_text(context.get("repository")) or _normalize_text(run.repository)
        pull_request_number = _coerce_pr_number(context.get("pull_request_number"))
        if pull_request_number is None:
            pull_request_number = _coerce_pr_number(run.pull_request_number)

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

        context["pull_request_url"] = _normalize_text(pull_request.get("html_url")) or _normalize_text(
            context.get("pull_request_url")
        )
        context["pull_request_state"] = _normalize_text(pull_request.get("state")).lower()
        context["pull_request_draft"] = bool(pull_request.get("draft", False))
        mergeable = pull_request.get("mergeable")
        if mergeable is None or isinstance(mergeable, bool):
            context["pull_request_mergeable"] = mergeable
        context["pull_request_mergeable_state"] = _normalize_text(
            pull_request.get("mergeable_state")
        ).lower()

        return runtime.run_worker(self.stage, ReviewWorker.remote(), context)

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
