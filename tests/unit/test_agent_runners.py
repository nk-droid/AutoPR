import uuid
from typing import Any

from core.agents.code.runner import CodeAgent
from core.agents.merge.runner import MergeAgent
from core.agents.plan.runner import PlanAgent
from core.agents.pr.runner import PRAgent
from core.agents.publish.runner import PublishAgent
from core.agents.qa.runner import QAAgent
from core.agents.review.runner import ReviewAgent
from core.agents.triage.runner import TriageAgent
from core.contracts.code import CodeOutput
from core.contracts.enums import CheckStatus
from core.contracts.enums import RiskLevel
from core.contracts.plan import PlanStep
from core.contracts.run_context import IssueToPRContext
from core.contracts.run_context import PRToMergeContext
from core.contracts.run_context import TriageIssueInput
from core.contracts.run_context import ToolRunResult
from core.contracts.triage import AmbiguityResult
from core.contracts.triage import Risk
from core.contracts.triage import TaskSpec
from core.contracts.triage import TriageResult
from core.orchestrator.models import StageStatus

import core.agents.code.runner as code_runner
import core.agents.merge.runner as merge_runner
import core.agents.plan.runner as plan_runner
import core.agents.pr.runner as pr_runner
import core.agents.publish.runner as publish_runner
import core.agents.qa.runner as qa_runner
import core.agents.review.runner as review_runner
import core.agents.triage.runner as triage_runner

class _FakeGraph:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.last_payload: dict[str, Any] | None = None

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.last_payload = payload
        return dict(self.response)

def _triage_result() -> TriageResult:
    return TriageResult(
        task_spec=TaskSpec(
            problem="Fix endpoint",
            acceptance_criteria=["returns 200"],
            constraints=["no schema break"],
            out_of_scope=[],
        ),
        risk=Risk(level=RiskLevel.LOW, reasons=["small change"]),
        ambiguity=AmbiguityResult(status=StageStatus.OK, questions=[]),
        questions=[],
    )

def _plan_step() -> PlanStep:
    return PlanStep(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        title="Patch handler",
        objective="Update webhook parser",
        rationale="Handle edge case",
        files=["apps/api/routes/webhooks.py"],
        tests=["tests/integration/test_github_webhook_handler.py::test_case"],
    )

def _issue_context() -> IssueToPRContext:
    return IssueToPRContext(
        repository="acme/repo",
        issue_number=7,
        execute_remote_actions=False,
        head_branch="autopr/issue-7",
        base_branch="main",
        metadata={"source": "test"},
    )

def _merge_context() -> PRToMergeContext:
    return PRToMergeContext(
        repository="acme/repo",
        pull_request_number=19,
        review_approved=True,
        execute_remote_actions=False,
        metadata={"source": "test"},
    )

def test_triage_agent_runner_initial_payload(monkeypatch) -> None:
    fake = _FakeGraph({"status": StageStatus.NEEDS_REVIEW, "final_output": {"a": 1}})
    monkeypatch.setattr(triage_runner, "build_triage_graph", lambda _nodes: fake)
    agent = TriageAgent()
    issue = TriageIssueInput(title="Bug", body="Details")
    status, output = agent.run(issue)
    assert status == StageStatus.NEEDS_REVIEW
    assert output == {"a": 1}
    assert fake.last_payload is not None
    assert fake.last_payload["issue"] == issue
    assert fake.last_payload["status"] == StageStatus.OK
    assert fake.last_payload["final_output"] == {}

def test_plan_agent_runner_initial_payload(monkeypatch) -> None:
    fake = _FakeGraph({"status": StageStatus.BLOCKED, "final_output": {"plan": "x"}})
    monkeypatch.setattr(plan_runner, "build_plan_graph", lambda _nodes: fake)
    agent = PlanAgent()
    status, output = agent.run(_triage_result())
    assert status == StageStatus.BLOCKED
    assert output == {"plan": "x"}
    assert fake.last_payload is not None
    assert "triage_result" in fake.last_payload
    assert fake.last_payload["steps"] == []
    assert fake.last_payload["open_questions"] == []

def test_code_agent_runner_initial_payload(monkeypatch) -> None:
    fake = _FakeGraph({"status": StageStatus.OK, "final_output": {"files_map": {"a.py": "x"}}})
    monkeypatch.setattr(code_runner, "build_code_graph", lambda _nodes: fake)
    agent = CodeAgent()
    step = _plan_step()
    status, output = agent.run(step, "repo-map", {"a.py": "old"})
    assert status == StageStatus.OK
    assert output == {"files_map": {"a.py": "x"}}
    assert fake.last_payload is not None
    assert fake.last_payload["step"] == step
    assert fake.last_payload["repo_map"] == "repo-map"
    assert fake.last_payload["file_contents"] == {"a.py": "old"}
    assert fake.last_payload["status"] == StageStatus.OK

def test_qa_agent_runner_initial_payload(monkeypatch) -> None:
    fake = _FakeGraph({"status": StageStatus.NEEDS_REVIEW, "final_output": {"summary": "warn"}})
    monkeypatch.setattr(qa_runner, "build_qa_graph", lambda _nodes: fake)
    agent = QAAgent()
    output = CodeOutput(files_map={"app.py": "x"}, tests_map={"tests/test_app.py": "t"})
    step = _plan_step()
    tools = [ToolRunResult(name="lint", status=CheckStatus.PASS, payload={})]
    status, final_output = agent.run(output, step, tools)
    assert status == StageStatus.NEEDS_REVIEW
    assert final_output == {"summary": "warn"}
    assert fake.last_payload is not None
    assert fake.last_payload["coding_output"] == output
    assert fake.last_payload["coding_step"] == step
    assert fake.last_payload["tool_results"] == tools
    assert fake.last_payload["checks"] == []

def test_pr_agent_runner_initial_payload(monkeypatch) -> None:
    fake = _FakeGraph({"status": StageStatus.OK, "final_output": {"pull_request_number": 42}})
    monkeypatch.setattr(pr_runner, "build_pr_graph", lambda _nodes: fake)
    agent = PRAgent()
    status, output = agent.run(_issue_context())
    assert status == StageStatus.OK
    assert output == {"pull_request_number": 42}
    assert fake.last_payload is not None
    assert fake.last_payload["context"].issue_number == 7
    assert fake.last_payload["request"] is None
    assert fake.last_payload["pull_request_number"] is None

def test_review_agent_runner_initial_payload(monkeypatch) -> None:
    fake = _FakeGraph({"status": StageStatus.BLOCKED, "final_output": {"required_actions": ["x"]}})
    monkeypatch.setattr(review_runner, "build_review_graph", lambda _nodes: fake)
    agent = ReviewAgent()
    status, output = agent.run(_merge_context())
    assert status == StageStatus.BLOCKED
    assert output == {"required_actions": ["x"]}
    assert fake.last_payload is not None
    assert fake.last_payload["context"].pull_request_number == 19
    assert fake.last_payload["required_actions"] == []

def test_publish_agent_runner_initial_payload(monkeypatch) -> None:
    fake = _FakeGraph({"status": StageStatus.OK, "final_output": {"outputs": {"publish_output": "ok"}}})
    monkeypatch.setattr(publish_runner, "build_publish_graph", lambda _nodes: fake)
    agent = PublishAgent()
    status, output = agent.run({"repository": "acme/repo", "execute_remote_actions": True})
    assert status == StageStatus.OK
    assert output == {"outputs": {"publish_output": "ok"}}
    assert fake.last_payload is not None
    assert fake.last_payload["context"]["repository"] == "acme/repo"
    assert fake.last_payload["status"] == StageStatus.OK
    assert fake.last_payload["files_payload"] == {}
    assert fake.last_payload["final_output"] == {}

def test_merge_agent_runner_initial_payload(monkeypatch) -> None:
    fake = _FakeGraph({"status": StageStatus.NEEDS_REVIEW, "final_output": {"outputs": {"merge_output": {"status": "needs_review"}}}})
    monkeypatch.setattr(merge_runner, "build_merge_graph", lambda _nodes: fake)
    agent = MergeAgent()
    status, output = agent.run({"repository": "acme/repo", "pull_request_number": 17})
    assert status == StageStatus.NEEDS_REVIEW
    assert output == {"outputs": {"merge_output": {"status": "needs_review"}}}
    assert fake.last_payload is not None
    assert fake.last_payload["context"]["repository"] == "acme/repo"
    assert fake.last_payload["pull_request_number"] is None
    assert fake.last_payload["merge_method"] == "squash"
