import json
import uuid

from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from core.agents.code.graph import build_code_graph
from core.agents.plan.graph import build_plan_graph
from core.agents.plan.graph import is_output_parse_error
from core.agents.pr.graph import build_pr_graph
from core.agents.qa.graph import build_qa_graph
from core.agents.review.graph import build_review_graph
from core.agents.triage.graph import build_triage_graph
from core.contracts.code import CodeOutput
from core.contracts.enums import RiskLevel
from core.contracts.plan import PlanStep
from core.contracts.run_context import IssueToPRContext
from core.contracts.run_context import PRToMergeContext
from core.contracts.run_context import TriageIssueInput
from core.contracts.triage import AmbiguityResult
from core.contracts.triage import Risk
from core.contracts.triage import TaskSpec
from core.contracts.triage import TriageResult
from core.orchestrator.models import StageStatus


def test_plan_parse_error_detection() -> None:
    try:
        PlanStep.model_validate({"title": 1})
    except ValidationError as exc:
        validation_error = exc
    else:
        raise AssertionError("ValidationError expected")
    assert is_output_parse_error(OutputParserException("bad json")) is True
    assert is_output_parse_error(json.JSONDecodeError("bad", "{}", 0)) is True
    assert is_output_parse_error(validation_error) is True
    assert is_output_parse_error(RuntimeError("failed to parse json output")) is True
    assert is_output_parse_error(RuntimeError("network timeout")) is False


def test_triage_graph_executes_nodes_in_order() -> None:
    calls: list[str] = []
    task = TaskSpec(problem="p", acceptance_criteria=["a"], constraints=[], out_of_scope=[])
    risk = Risk(level=RiskLevel.LOW, reasons=["r"])
    ambiguity = AmbiguityResult(status=StageStatus.OK, questions=[])
    final = TriageResult(task_spec=task, risk=risk, ambiguity=ambiguity, questions=[])

    class Nodes:
        def extract_task(self, state):
            calls.append("extract_task")
            return {"task_spec": task}

        def assess_risk(self, state):
            calls.append("assess_risk")
            return {"risk": risk}

        def detect_ambiguity(self, state):
            calls.append("detect_ambiguity")
            return {"ambiguity": ambiguity, "status": StageStatus.OK}

        def finalize(self, state):
            calls.append("finalize")
            return {"final_output": final.model_dump(mode="json")}

    app = build_triage_graph(Nodes())
    result = app.invoke(
        {
            "issue": TriageIssueInput(title="Bug", body="desc"),
            "task_spec": task,
            "risk": risk,
            "ambiguity": ambiguity,
            "status": StageStatus.OK,
            "final_output": {},
        }
    )
    assert calls == ["extract_task", "assess_risk", "detect_ambiguity", "finalize"]
    assert result["final_output"]["task_spec"]["problem"] == "p"


def test_plan_graph_executes_nodes_in_order() -> None:
    calls: list[str] = []
    step = PlanStep(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        title="Plan A",
        objective="obj",
        rationale="why",
        files=["a.py"],
        tests=["tests/test_a.py"],
    )

    class Nodes:
        def draft_plan(self, state):
            calls.append("draft_plan")
            return {"strategy": "strat", "steps": [step], "status": StageStatus.OK}

        def map_dependencies(self, state):
            calls.append("map_dependencies")
            return {"steps": state["steps"]}

        def detect_ambiguity(self, state):
            calls.append("detect_ambiguity")
            return {"open_questions": ["q1"], "status": StageStatus.OK}

        def finalize(self, state):
            calls.append("finalize")
            return {
                "final_output": {
                    "strategy": state["strategy"],
                    "steps": [step.model_dump(mode="json")],
                }
            }

    app = build_plan_graph(Nodes())
    result = app.invoke(
        {
            "triage_result": TriageResult(
                task_spec=TaskSpec(
                    problem="p", acceptance_criteria=["a"], constraints=[], out_of_scope=[]
                ),
                risk=Risk(level=RiskLevel.LOW, reasons=["r"]),
                ambiguity=AmbiguityResult(status=StageStatus.OK, questions=[]),
                questions=[],
            ),
            "strategy": "",
            "steps": [],
            "assumptions": [],
            "open_questions": [],
            "status": StageStatus.OK,
            "final_output": {},
        }
    )
    assert calls == ["draft_plan", "map_dependencies", "detect_ambiguity", "finalize"]
    assert result["final_output"]["strategy"] == "strat"


def test_code_qa_pr_review_graphs_compile_and_invoke() -> None:
    class CodeNodes:
        def understand_task(self, state):
            return state

        def locate_files(self, state):
            return {"target_files": ["a.py"]}

        def generate_patch(self, state):
            return {
                "files": {"a.py": "print('x')"},
                "files_changed": ["a.py"],
                "status": StageStatus.OK,
            }

        def validate_patch(self, state):
            return state

        def finalize(self, state):
            return {
                "final_output": CodeOutput(
                    files_map={"a.py": "print('x')"}, tests_map={}
                ).model_dump(mode="json")
            }

    code_app = build_code_graph(CodeNodes())
    code_result = code_app.invoke(
        {
            "step": PlanStep(title="t", objective="o", files=["a.py"], tests=[]),
            "repo_map": "",
            "file_contents": {"a.py": "old"},
            "target_files": [],
            "files": {},
            "status": StageStatus.OK,
            "notes": {},
            "final_output": {},
        }
    )
    assert code_result["final_output"]["files_map"]["a.py"] == "print('x')"

    class QANodes:
        def evaluate_inputs(self, state):
            return {"status": StageStatus.OK}

        def run_checks(self, state):
            return {"status": StageStatus.OK, "summary": "ok", "checks": [], "notes": {}}

        def finalize(self, state):
            return {"final_output": {"summary": "ok"}}

    qa_app = build_qa_graph(QANodes())
    qa_result = qa_app.invoke(
        {
            "coding_output": CodeOutput(files_map={"a.py": "print('x')"}, tests_map={}),
            "coding_step": PlanStep(title="t", objective="o", files=[], tests=[]),
            "tool_results": [],
            "status": StageStatus.OK,
            "summary": "",
            "checks": [],
            "notes": {},
            "final_output": {},
        }
    )
    assert qa_result["final_output"]["summary"] == "ok"

    class PRNodes:
        def prepare_request(self, state):
            return {"status": StageStatus.OK}

        def open_pr(self, state):
            return {
                "status": StageStatus.OK,
                "pull_request_number": 12,
                "pull_request_url": "https://x",
            }

        def finalize(self, state):
            return {"final_output": {"pull_request_number": 12}}

    pr_app = build_pr_graph(PRNodes())
    pr_result = pr_app.invoke(
        {
            "context": IssueToPRContext(
                repository="acme/repo",
                issue_number=12,
                execute_remote_actions=False,
                head_branch="autopr/issue-12",
                base_branch="main",
                metadata={},
            ),
            "status": StageStatus.OK,
            "request": None,
            "pull_request_number": None,
            "pull_request_url": "",
            "summary": "",
            "notes": {},
            "final_output": {},
        }
    )
    assert pr_result["final_output"]["pull_request_number"] == 12

    class ReviewNodes:
        def evaluate_review(self, state):
            return {
                "status": StageStatus.OK,
                "summary": "ok",
                "checks": [],
                "required_actions": [],
                "notes": {},
            }

        def llm_merge_risk_review(self, state):
            return state

        def finalize(self, state):
            return {"final_output": {"summary": "ok"}}

    review_app = build_review_graph(ReviewNodes())
    review_result = review_app.invoke(
        {
            "context": PRToMergeContext(
                repository="acme/repo",
                pull_request_number=12,
                review_approved=True,
                execute_remote_actions=False,
                metadata={},
            ),
            "status": StageStatus.OK,
            "summary": "",
            "checks": [],
            "required_actions": [],
            "notes": {},
            "final_output": {},
        }
    )
    assert review_result["final_output"]["summary"] == "ok"
