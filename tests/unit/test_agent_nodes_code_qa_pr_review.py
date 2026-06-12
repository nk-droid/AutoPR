from core.contracts.code import CodeOutput
from core.contracts.enums import CheckStatus
from core.contracts.plan import PlanStep
from core.contracts.run_context import IssueToPRContext
from core.contracts.run_context import PRToMergeContext
from core.contracts.run_context import ToolRunResult
from core.orchestrator.models import StageStatus

import core.agents.code.nodes as code_nodes
import core.agents.pr.nodes as pr_nodes
import core.agents.qa.nodes as qa_nodes
import core.agents.review.nodes as review_nodes

def test_code_nodes_generate_validate_finalize_success(monkeypatch) -> None:
    def fake_invoke_chain(*, output_model, **kwargs):
        del kwargs
        return code_nodes.GeneratedFilesPayload(
            files=[
                code_nodes.GeneratedFile(path="app/main.py", content="print('ok')"),
                code_nodes.GeneratedFile(path="tests/test_main.py", content="def test_ok():\n    assert True"),
                code_nodes.GeneratedFile(path="ignored.py", content="x"),
            ],
            summary="done",
        )

    monkeypatch.setattr(code_nodes, "invoke_chain", fake_invoke_chain)
    state = {
        "step": PlanStep(
            title="Update feature",
            objective="Update core flow",
            files=["app/main.py"],
            tests=["tests/test_main.py::test_ok"],
        ),
        "repo_map": "app/main.py\ntests/test_main.py",
        "file_contents": {"app/main.py": "old", "tests/test_main.py": "old"},
        "target_files": [],
        "files": {},
        "status": StageStatus.OK,
        "notes": {},
        "final_output": {},
    }
    state = code_nodes.understand_task(state)
    state = code_nodes.locate_files(state)
    state = code_nodes.generate_patch(state)
    assert state["status"] == StageStatus.OK
    assert state["notes"]["ignored_generated_files"] == ["ignored.py"]
    state = code_nodes.validate_patch(state)
    assert state["status"] == StageStatus.OK
    state = code_nodes.finalize(state)
    assert state["final_output"]["files_map"]["app/main.py"] == "print('ok')"
    assert "tests/test_main.py" in state["final_output"]["tests_map"]

def test_code_nodes_block_when_no_target_files() -> None:
    state = {
        "step": PlanStep(title="x", objective="y", files=[], tests=[]),
        "target_files": [],
        "status": StageStatus.OK,
        "notes": {},
    }
    updated = code_nodes.generate_patch(state)
    assert updated["status"] == StageStatus.BLOCKED
    assert updated["notes"]["blocking_reason"] == "no_target_files"

def test_qa_nodes_blocks_invalid_inputs() -> None:
    state = {"coding_output": {}, "coding_step": {}, "tool_results": []}
    updated = qa_nodes.evaluate_inputs(state)
    assert updated["status"] == StageStatus.BLOCKED
    assert updated["notes"]["blocking_reason"] == "invalid_coding_output"

def test_qa_nodes_run_checks_sets_blocked_when_tools_missing() -> None:
    state = {
        "coding_output": CodeOutput(files_map={"a.py": "x"}, tests_map={}),
        "coding_step": PlanStep(title="x", objective="y", files=["a.py"], tests=[]),
        "tool_results": [
            ToolRunResult(name="lint", status=CheckStatus.PASS, payload={}),
            ToolRunResult(name="tests", status=CheckStatus.PASS, payload={}),
        ],
        "status": StageStatus.OK,
        "summary": "",
        "checks": [],
        "notes": {},
    }
    updated = qa_nodes.run_checks(state)
    assert updated["status"] == StageStatus.BLOCKED
    assert set(updated["notes"]["missing_tools"]) == {"coverage", "security"}
    finalized = qa_nodes.finalize(updated)
    assert finalized["final_output"]["status"] == StageStatus.BLOCKED.value

def test_pr_nodes_prepare_and_skip_remote_open() -> None:
    context = IssueToPRContext(
        repository="acme/repo",
        issue_number=33,
        execute_remote_actions=False,
        head_branch="autopr/issue-33",
        base_branch="main",
        metadata={"source": "test"},
        task_spec={"problem": "Fix race condition"},
        coding_output={"files_map": {"app/main.py": "x"}, "tests_map": {"tests/test_main.py": "y"}},
    )
    state = {
        "context": context,
        "status": StageStatus.OK,
        "request": None,
        "pull_request_number": None,
        "pull_request_url": "",
        "summary": "",
        "notes": {},
        "final_output": {},
    }
    state = pr_nodes.prepare_request(state)
    assert state["status"] == StageStatus.OK
    assert state["request"].title.startswith("Fix #33:")
    assert "Files Changed" in state["request"].body
    state = pr_nodes.open_pr(state)
    assert state["status"] == StageStatus.NEEDS_REVIEW
    assert state["notes"]["remote_creation_skipped"] is True
    state = pr_nodes.finalize(state)
    assert "request" in state["final_output"]

def test_review_nodes_evaluate_and_finalize() -> None:
    context = PRToMergeContext(
        repository="acme/repo",
        pull_request_number=90,
        review_approved=True,
        execute_remote_actions=False,
        metadata={"source": "test"},
        qa_output={"status": "ok"},
        pull_request_state="open",
        pull_request_draft=False,
        pull_request_url="https://github.com/acme/repo/pull/90",
    )
    state = {
        "context": context,
        "status": StageStatus.OK,
        "summary": "",
        "checks": [],
        "required_actions": [],
        "notes": {},
        "final_output": {},
    }
    state = review_nodes.evaluate_review(state)
    assert state["status"] == StageStatus.OK
    assert state["required_actions"] == []
    state = review_nodes.finalize(state)
    assert "Review checks complete" in state["final_output"]["summary"]
