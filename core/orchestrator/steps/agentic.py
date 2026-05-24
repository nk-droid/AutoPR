import os
import ray
from typing import Any, Dict

from core.contracts.plan import PlanStep as PlanStepModel
from core.contracts.code import CodeOutput as CodeOutputModel
from core.contracts.enums import CheckStatus, PipelineStage, RunState
from core.orchestrator.steps.base import PipelineStep, StepRuntime, is_success_status
from core.contracts.run_context import CodeWorkerInput, IssueToPRContext, PlanWorkerInput, PRToMergeContext, PRWorkerInput, QAJobPayload, QAWorkerInput, ReviewWorkerInput, ToolRunResult, TriageIssueInput, TriageWorkerInput
from core.contracts.triage import AmbiguityResult, Risk, TaskSpec, TriageResult
from core.orchestrator.models import MergeDecision, RunModel, StageResult, StageStatus
from core.orchestrator.transitions import can_merge_pr, can_open_pr

from infra.github.client import GitHubAPIError, GitHubClient
from infra.github.issues import get_issue_details
from infra.ray.actors import (
    TriageWorker,
    PlanWorker,
    CodeWorker,
    QAWorker,
    PRWorker,
    ReviewWorker,
    CoverageWorker,
    LintWorker,
    SecurityWorker,
    TestWorker
)

from observability.tracing import traced, pipeline_step_attrs, inject_trace_context

def _stage_results(context: Dict[str, Any]) -> Dict[str, StageResult]:
    # Keep prior stage outputs in context so later steps can make policy decisions.
    value = context.get("_stage_results")
    if isinstance(value, dict):
        return value
    value = {}
    context["_stage_results"] = value
    return value

class TriageStep(PipelineStep):
    stage = PipelineStage.TRIAGE
    success_state = RunState.TRIAGED.value

    @traced("pipeline.triage_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        issue_number = context.get("issue_number")
        repo = context.get("repository")
        issue = get_issue_details(
            issue_reference=issue_number,
            repo=repo,
            token=os.environ.get("GITHUB_TOKEN"),
        )
        title = issue.get("title", "")
        body = issue.get("body", "")
        if not title:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": "Triage blocked: issue title is missing."},
            )
        triage_input = TriageWorkerInput(issue=TriageIssueInput(title=title, body=body))
        return runtime.run_worker(self.stage, TriageWorker.remote(), triage_input)

class PlanStep(PipelineStep):
    stage = PipelineStage.PLAN
    success_state = RunState.PLANNED.value

    @traced("pipeline.plan_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        try:
            triage_result = TriageResult(
                task_spec=TaskSpec(**context.get("task_spec", {})),
                risk=Risk(**context.get("risk", {})),
                ambiguity=AmbiguityResult(**context.get("ambiguity", {})),
                questions=context.get("questions", []),
            )
        except Exception as exc:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": f"Plan blocked: invalid triage output ({exc})."},
            )
        return runtime.run_worker(self.stage, PlanWorker.remote(), PlanWorkerInput(triage_result=triage_result))

class CodeStep(PipelineStep):
    stage = PipelineStage.CODE
    success_state = RunState.CODING.value

    @traced("pipeline.code_step", attributes=pipeline_step_attrs)
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

        selected_step_raw = steps[step_index]
        try:
            selected_step = PlanStepModel(**selected_step_raw)
        except Exception as exc:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                outputs={"files_map": {}, "tests_map": {}},
                notes={"reason": f"Invalid plan step for coding ({exc})."},
            )
        repo_map = context.get("repo_map", "")
        file_contents = context.get("file_contents", {})
        if not isinstance(repo_map, str):
            repo_map = ""
        if not isinstance(file_contents, dict):
            file_contents = {}
        typed_file_contents: dict[str, str] = {}
        for path, content in file_contents.items():
            if not isinstance(path, str) or not isinstance(content, str):
                continue
            typed_file_contents[path] = content

        coding_result = runtime.run_worker(
            self.stage,
            CodeWorker.remote(),
            CodeWorkerInput(
                step=selected_step,
                repo_map=repo_map,
                file_contents=typed_file_contents,
            ),
        )
        code_output = coding_result.outputs if isinstance(coding_result.outputs, dict) else {}
        try:
            normalized_code_output = CodeOutputModel(**code_output).model_dump()
        except Exception:
            normalized_code_output = code_output
        return StageResult(
            stage=self.stage,
            status=coding_result.status,
            outputs={
                "coding_step_index": step_index,
                "coding_step": selected_step.model_dump(),
                "coding_output": normalized_code_output,
            },
        )

class QAStep(PipelineStep):
    stage = PipelineStage.QA
    success_state = RunState.QA_RUNNING.value

    @traced("pipeline.qa_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        try:
            coding_output = CodeOutputModel(**context.get("coding_output", {}))
            coding_step = PlanStepModel(**context.get("coding_step", {}))
        except Exception as exc:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": f"QA blocked: invalid coding payload ({exc})."},
            )

        timeout_raw = context.get("qa_timeout_sec", 900)
        timeout_sec = timeout_raw if isinstance(timeout_raw, int) and timeout_raw > 0 else 900
        coverage_raw = context.get("coverage_threshold", 80.0)
        if isinstance(coverage_raw, float):
            coverage_threshold = coverage_raw
        elif isinstance(coverage_raw, int):
            coverage_threshold = float(coverage_raw)
        else:
            coverage_threshold = 80.0
        repo_path = context.get("repo_path") or context.get("local_repo_path")
        qa_payload = QAJobPayload(
            coding_output=coding_output,
            coding_step=coding_step,
            repo_path=repo_path,
            qa_timeout_sec=timeout_sec,
            coverage_threshold=coverage_threshold,
        )

        trace_context = inject_trace_context()
        lint_ref = LintWorker.remote().run.remote(qa_payload, trace_context=trace_context)
        test_ref = TestWorker.remote().run.remote(qa_payload, trace_context=trace_context)
        coverage_ref = CoverageWorker.remote().run.remote(qa_payload, trace_context=trace_context)
        security_ref = SecurityWorker.remote().run.remote(qa_payload, trace_context=trace_context)
        refs = [lint_ref, test_ref, coverage_ref, security_ref]
        # Run QA tools in parallel, then cancel stragglers after timeout.
        _, pending = ray.wait(
            refs,
            num_returns=len(refs),
            timeout=qa_payload.qa_timeout_sec + 30,
        )

        def _get_result_from_ref(name: str, pending: Any, ref: Any) -> ToolRunResult:
            if ref not in pending:
                return ray.get(ref)

            return ToolRunResult(
                name=name,
                status=CheckStatus.FAIL,
                payload={"reason": "timeout_or_cancelled"}
            )

        lint_result = _get_result_from_ref("lint", pending, lint_ref)
        tests_result = _get_result_from_ref("tests", pending, test_ref)
        coverage_result = _get_result_from_ref("coverage", pending, coverage_ref)
        security_result = _get_result_from_ref("security", pending, security_ref)

        tool_results = [lint_result, tests_result, coverage_result, security_result]
        for ref in pending:
            ray.cancel(ref, force=True)

        qa_worker_input = QAWorkerInput(
            coding_output=coding_output,
            coding_step=coding_step,
            tool_results=tool_results,
        )
        return runtime.run_worker(self.stage, QAWorker.remote(), qa_worker_input)

class PROpenStep(PipelineStep):
    stage = PipelineStage.PR_OPEN
    success_state = RunState.PR_OPENED.value

    @traced("pipeline.pr_open_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        results = _stage_results(context)
        qa_result = results.get(PipelineStage.QA.value) or results.get(PipelineStage.QA)
        # PR creation is hard-gated on QA policy output.
        decision = can_open_pr(qa_result)
        if not decision.allowed:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": decision.reason, "blocking_reasons": decision.blocking_reasons},
            )
        issue_number = context.get("issue_number")
        if not isinstance(issue_number, int):
            issue_number = run.issue_number
        if not isinstance(issue_number, int):
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": "PR open blocked: issue_number missing."},
            )
        payload = dict(context)
        payload.setdefault("repository", context.get("repository") or run.repository)
        payload.setdefault("issue_number", issue_number)
        payload.setdefault("execute_remote_actions", bool(context.get("execute_remote_actions", False)))
        head_branch_value = context.get("head_branch") or context.get("pr_head")
        if not isinstance(head_branch_value, str) or not head_branch_value:
            head_branch_value = f"autopr/issue-{issue_number}"
        payload.setdefault("head_branch", head_branch_value)
        base_branch_value = context.get("base_branch") or context.get("pr_base")
        if not isinstance(base_branch_value, str) or not base_branch_value:
            base_branch_value = "main"
        payload.setdefault("base_branch", base_branch_value)
        payload.setdefault("metadata", context.get("metadata") if isinstance(context.get("metadata"), dict) else dict(run.metadata))
        pr_context = IssueToPRContext(**payload)
        return runtime.run_worker(self.stage, PRWorker.remote(), PRWorkerInput(context=pr_context))

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
