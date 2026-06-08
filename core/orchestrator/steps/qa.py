from typing import Any

import ray

from core.contracts.code import CodeOutput as CodeOutputModel
from core.contracts.enums import CheckStatus, PipelineStage, RunState
from core.contracts.plan import PlanStep as PlanStepModel
from core.contracts.run_context import QAJobPayload, QAWorkerInput, ToolRunResult
from core.orchestrator.models import RunModel, StageResult, StageStatus
from core.orchestrator.steps.base import PipelineStep, StepRuntime

from infra.ray.actors import CoverageWorker, LintWorker, QAWorker, SecurityWorker, TestWorker

from observability.tracing import inject_trace_context, pipeline_step_attrs, traced

class QAStep(PipelineStep):
    stage = PipelineStage.QA
    success_state = RunState.QA_RUNNING.value

    def before(self, context: dict[str, Any], run: RunModel) -> list[tuple[str, str]]:
        if run.state != RunState.QA_RUNNING.value:
            return [(RunState.QA_RUNNING.value, "qa started")]
        return []

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
